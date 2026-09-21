from dataclasses import replace
from datetime import datetime, timedelta
import json
from pathlib import Path

from wavequant.domain.market_structure.alternation_duration import short_shallow_pullback, filter_duration_landmarks
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals
from wavequant.domain.strategies.strategy_profiles import whole_wave_profile


def test_close_depth_and_strict_half_duration_boundaries():
    assert short_shallow_pullback(4, 16, 8.01, 10, 4)
    assert not short_shallow_pullback(4, 16, 8, 10, 5)
    assert not short_shallow_pullback(4, 16, 8.01, 10, 6)
    assert not short_shallow_pullback(4, 16, 8.01, 10, 5)


def test_formal_short_pullback_waits_for_close_not_wick_breakout():
    bars = [Bar(datetime(2026, 1, 1) + timedelta(days=i), "TEST", 12, 17, 10, 12, 100) for i in range(9)]
    item = dict(
        index=6,
        value=10,
        available_at="2026-01-07",
        confirmed_bear_low=dict(index=0, value=4),
        confirmed_flip_high=dict(index=5, value=16),
    )
    assert filter_duration_landmarks([item], bars) == []
    bars[-1] = replace(bars[-1], close=17)
    result = filter_duration_landmarks([item], bars)
    assert result[0]["available_at"] == "2026-01-09"
    assert filter_duration_landmarks([item], bars[:-1]) == []


def test_lexin_april23_short_correction_is_not_may10_alternation():
    raw = json.loads((Path(__file__).parent / "fixtures/lexin_2026_squeeze.json").read_text(encoding="utf-8"))
    bars = [
        Bar(datetime.fromisoformat(day), raw["symbol"], *values) for day, *values in raw["bars"] if day <= "2018-06-15"
    ]
    config = SystemStrategy(
        **(whole_wave_profile({"scenarios": {"base": {"execution": {}}}})["strategy"] | {"volume_filter": False})
    )
    result = generate_system_signals(bars, config)
    low = next(i for i, b in enumerate(bars) if str(b.timestamp.date()) == "2018-04-23")
    assert not any(e["event"] == "squeeze_alternation_confirmed" and e["b_low_index"] == low for e in result.audit)


def test_short_squeeze_waits_until_breakout_and_never_backdates():
    from wavequant.domain.market_structure.squeeze_alternation import squeeze_alternations

    bars = [Bar(datetime(2026, 1, 1) + timedelta(days=i), "TEST", 12, 15, 11, 13, 100) for i in range(14)]
    bars[0] = replace(bars[0], low=4)
    bars[6] = replace(bars[6], high=16, close=15)
    bars[7] = replace(bars[7], low=10, close=12)
    bars[-1] = replace(bars[-1], high=18, close=17)
    audit = [
        dict(event="n_completed", direction="up", bar_index=10, origin=7, pullback=9, known_at=10),
        dict(event="regime_confirmation", regime="轧空", bar_index=12, attack=10),
    ]
    anchors = {
        10: [
            dict(
                origin=dict(index=0, value=4),
                high=dict(index=6, value=16),
                key=dict(index=0, value=8),
                source_path="test",
                known_index=7,
                trend_level=2,
            )
        ]
    }
    assert squeeze_alternations(bars[:-1], audit, anchors) == []
    result = squeeze_alternations(bars, audit, anchors)
    assert [e["event"] for e in result] == ["squeeze_alternation_confirmed", "squeeze_alternation_breakout"]
    assert all(e["bar_index"] == 13 for e in result)
    bars[12] = replace(bars[12], low=9)
    assert squeeze_alternations(bars, audit, anchors) == []
