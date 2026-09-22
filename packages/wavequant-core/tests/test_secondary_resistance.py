from datetime import datetime, timedelta
from dataclasses import replace
import json
from pathlib import Path

from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.secondary_resistance import secondary_resistance_history
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals
from wavequant.domain.strategies.strategy_profiles import whole_wave_profile


def test_resisted_secondary_break_needs_later_confirmation_not_same_or_next_bar():
    rows = [(9, 10, 8, 9), (9, 9.5, 8.8, 9.4), (9.6, 11, 9.5, 10.1), (10.2, 11.1, 10, 10.3), (10.4, 11.5, 10.3, 11.4)]
    bars = [Bar(datetime(2026, 1, 1) + timedelta(days=i), "TEST", *row, 1000) for i, row in enumerate(rows)]
    history = {i: {2: [dict(index=0, kind="H", value=10)]} for i in range(len(bars))}
    result = secondary_resistance_history(bars, history)
    assert list(result) == [2, 3]
    assert secondary_resistance_history(bars[:4], history) == result
    assert 4 in secondary_resistance_history(bars[:4] + [replace(bars[4], close=11.1)], history)
    assert secondary_resistance_history(bars, {}) == {}


def test_lexin_july2_secondary_pressure_blocks_local_squeeze():
    raw = json.loads((Path(__file__).parent / "fixtures/lexin_2026_squeeze.json").read_text(encoding="utf-8"))
    bars = [Bar(datetime.fromisoformat(day), raw["symbol"], *values) for day, *values in raw["bars"]]
    config = SystemStrategy(
        **(whole_wave_profile({"scenarios": {"base": {"execution": {}}}})["strategy"] | {"volume_filter": False})
    )
    result = generate_system_signals(bars, config)
    assert not any(s.side == "LONG" and str(s.timestamp.date()) == "2026-07-02" for s in result.signals)
    rejection = next(
        e
        for e in result.audit
        if e["event"] == "entry_rejected"
        and e["timestamp"].startswith("2026-07-02")
        and e["reason"] == "secondary_breakout_resistance_unresolved"
    )
    assert rejection["secondary_high_date"] == "2026-04-30"
    cutoff = rejection["bar_index"]
    prefix = generate_system_signals(bars[: cutoff + 1], config)
    assert prefix.signals == [s for s in result.signals if s.bar_index <= cutoff]
    assert rejection in prefix.audit
    assert any(s.side == "LONG" and str(s.timestamp.date()) == "2026-08-04" for s in result.signals)


def test_source_key_is_causal_and_new_resistance_high_does_not_cancel_pending_gate():
    rows = [
        (9, 10, 8, 9),
        (9, 9.5, 8.8, 9.4),
        (9.6, 11, 9.5, 10.1),
        (10.2, 11.1, 10, 10.3),
        (10.4, 11.2, 10.3, 10.5),
        (10.6, 11.6, 10.5, 11.5),
    ]
    bars = [Bar(datetime(2026, 1, 1) + timedelta(days=i), "TEST", *row, 1000) for i, row in enumerate(rows)]
    history = {i: {2: [dict(index=0, kind="H", value=8)]} for i in range(len(bars))}
    events = [
        dict(bar_index=1, event="hierarchy_resistance_key", key=dict(index=0, value=10)),
        dict(bar_index=3, event="hierarchy_resistance_key", key=dict(index=2, value=11)),
    ]
    result = secondary_resistance_history(bars, history, key_events=events, include_resolved=True)
    assert 1 not in result
    assert not result[4].get("secondary_resistance_resolved")
    assert result[4]["secondary_high"] == 10
    assert result[5]["secondary_resistance_resolved"]
    assert result[5]["secondary_resistance_high"] == 11.2
    assert result[5]["secondary_confirmation_close"] == 11.5
    assert secondary_resistance_history(bars[:5], history, key_events=events, include_resolved=True) == {
        i: e for i, e in result.items() if i < 5
    }
    # A future key event cannot retroactively make the earlier breakout known.
    assert secondary_resistance_history(bars[:3], history, key_events=[dict(events[0], bar_index=3)]) == {}
    stronger = {i: {2: [dict(index=0, kind="H", value=12)]} for i in range(len(bars))}
    assert secondary_resistance_history(bars, stronger, key_events=events) == {}
