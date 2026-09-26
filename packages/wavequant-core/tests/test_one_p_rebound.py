from datetime import datetime
import json
from pathlib import Path
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.integrated_strategy import generate_system_signals
from .test_completed_wave_recovery import config


def test_guilin_one_p_rebound():
    raw = json.loads((Path(__file__).parent / "fixtures/guilin_2020_rebound.json").read_text())
    bars = [Bar(datetime.fromisoformat(d), raw["symbol"], *v) for d, *v in raw["bars"]]
    now = next(i for i, b in enumerate(bars) if str(b.timestamp.date()) == "2020-07-06")
    result = generate_system_signals(bars, config())
    proof = next(e for e in result.audit if e["event"] == "long_signal" and e["bar_index"] == now)
    assert proof["wave_entry_n_date"] == "2020-05-28"
    assert proof["wave_a_class"] == "ordinary"
    assert proof["wave_a_high_date"] == "2020-06-08"
    assert proof["wave_b_low_date"] == "2020-06-29"
    assert proof["wave_gap_trigger"] == "rebound_close_breakout"
    assert "wave_c_1618_target" not in proof
    prefix = generate_system_signals(bars[: now + 1], config())
    assert prefix.signals == [s for s in result.signals if s.bar_index <= now]


def test_positive_outside_close_is_explicit_and_never_intrabar_order():
    from dataclasses import replace
    from wavequant.domain.market_structure.n_shape import NSetup, PivotRef, MilestoneBasis, BoxAnchorMode, observe_n
    from wavequant.domain.market_structure.price_action import Direction
    import pytest

    bars = [
        Bar(datetime(2020, 1, i + 1), "TEST", *v, 100)
        for i, v in enumerate([(10.2, 10.5, 10, 10.2), (11.5, 12, 11, 11.8), (11.7, 12.5, 10.8, 12.3)])
    ]
    setup = NSetup(
        "TEST",
        "1d",
        Direction.UP,
        PivotRef(0, 0),
        PivotRef(1, 2),
        PivotRef(2, 2),
        "lecture_causal",
        BoxAnchorMode.ATTACK_VIRTUAL_EXTREME,
        allow_outside_close=True,
    )
    result = observe_n(bars, setup, timeframe="1d", milestone_basis=MilestoneBasis.EXTREME)
    assert result.completion.bar_index == 2
    assert result.completion.defense == 10.8
    assert observe_n(bars[:2], setup, timeframe="1d", milestone_basis=MilestoneBasis.EXTREME).completion is None
    for wrong in [replace(setup, allow_outside_close=False), replace(setup, source="other")]:
        with pytest.raises(ValueError):
            observe_n(bars, wrong, timeframe="1d", milestone_basis=MilestoneBasis.EXTREME)
    with pytest.raises(ValueError):
        observe_n(
            bars[:2] + [replace(bars[2], close=12)], setup, timeframe="1d", milestone_basis=MilestoneBasis.EXTREME
        )


def test_ordinary_rebound_requires_target_defense_and_known_close_break():
    from dataclasses import replace
    from wavequant.domain.market_structure.wave_projection import WaveProjectionSetup
    from wavequant.domain.market_structure.polyline import PointKind, LinePoint, ReversalPoint
    from wavequant.domain.strategies.wave_continuation import wave_gap_entry

    rows = [
        (10, 11, 10, 10.5),
        (11, 13, 11, 12),
        (13, 14, 12, 13.5),
        (14, 17, 13, 16),
        (18, 20, 18, 19),
        (17, 18, 15, 16),
        (16, 17, 15.5, 16),
        (16, 18.5, 16, 18.1),
    ]
    bars = [Bar(datetime(2020, 1, i + 1), "TEST", *v, 100) for i, v in enumerate(rows)]
    setup = WaveProjectionSetup(0, 2, 3, 10, 14, 22, 12)
    pivot = ReversalPoint(LinePoint(5, 0, PointKind.HIGH, 18), 6, "test")
    proof = wave_gap_entry(bars, setup, 7, pivots=[pivot])
    assert proof["wave_a_class"] == "ordinary"
    assert proof["wave_a_origin_date"] == "2020-01-01"
    assert proof["wave_equal_target"] == 25
    assert proof["wave_entry_two_t_date"] == ""
    assert "wave_c_1618_target" not in proof
    assert wave_gap_entry(bars, setup, 7, pivots=[replace(pivot, confirmed_index=7)]) is None
    for change in [{"close": 18}, {"low": 11.9}, {"open": 18.1}, {"close": 16.5}]:
        altered = bars[:-1] + [replace(bars[-1], **change)]
        assert wave_gap_entry(altered, setup, 7, pivots=[pivot]) is None
    below = replace(setup, box_anchor=16, two_t=28)
    assert wave_gap_entry(bars, below, 7, pivots=[pivot]) is None


def test_ordinary_rebound_minutes_honor_optional_volume_filter():
    from dataclasses import replace
    from wavequant.application.analytics.intraday_entry import resolve_consolidation_entries
    from wavequant.infrastructure.market_data.minute import MinuteBar
    from wavequant.infrastructure.market_data.akshare_history import MinuteCoverageError

    raw = json.loads((Path(__file__).parent / "fixtures/guilin_2020_rebound.json").read_text())
    bars = [Bar(datetime.fromisoformat(d), raw["symbol"], *v) for d, *v in raw["bars"] if d <= "2020-07-06"]
    now = len(bars) - 1
    b = bars[-1]
    minute = [
        MinuteBar(b.timestamp.replace(hour=9, minute=35), b.open, 4.90, b.low, 4.88, bars[-2].volume),
        MinuteBar(b.timestamp.replace(hour=9, minute=40), 4.88, 4.91, 4.87, 4.90, 1),
        MinuteBar(b.timestamp.replace(hour=9, minute=45), 4.90, 4.95, 4.89, 4.94, 1000),
    ]

    def load(bar):
        if bar.timestamp == b.timestamp:
            return minute
        raise MinuteCoverageError(str(bar.timestamp.date()), None, None, "fixture")

    for volume_filter, when, price in [(True, "09:40:00", 4.90), (False, "09:35:00", 4.88)]:
        strategy = replace(config(), volume_filter=volume_filter)
        generated = generate_system_signals(bars, strategy)
        resolved, executions, _ = resolve_consolidation_entries(bars, generated, strategy, load, daily_fallback=True)
        entry = executions[b.symbol, now]
        assert entry["timing"]["decision_timestamp"].endswith(when)
        assert entry["price"] == price
        proof = next(e for e in resolved.audit if e["event"] == "long_signal" and e["bar_index"] == now)
        assert proof["squeeze_confirmation"] == "one_p_wave_rebound"
        assert proof["wave_body_fraction"] < 0.03
