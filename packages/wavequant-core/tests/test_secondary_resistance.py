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
