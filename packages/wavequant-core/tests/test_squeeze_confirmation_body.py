"""A higher close on a bearish candle is still resistance, not its failure."""

from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path

import pytest

from wavequant.domain.market_state.market_regime import (
    MarketRegime,
    RegimePolicy,
    WaveBoundary,
    observe_market_regime,
)
from wavequant.domain.market_structure.n_shape import BoxAnchorMode, NSetup, PivotRef
from wavequant.domain.market_structure.price_action import Direction, ShadowPolicy
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals


@pytest.mark.parametrize("opening,confirmed", [(12.9, False), (12.8, False), (12.3, True), (12.4, True)])
def test_local_confirmation_needs_directional_body(opening, confirmed):
    rows = [
        (8.5, 9, 8, 8.5),
        (10, 12, 9.5, 11),
        (10.7, 11, 10, 10.5),
        (10.8, 12.6, 10.4, 12.2),
        (12.4, 13.5, 11.8, 12.4),
        (opening, 13.2, 11.8, 12.8),
    ]
    bars = [Bar(datetime(2022, 1, i + 1), "TEST", *row, 1000) for i, row in enumerate(rows)]
    setup = NSetup(
        "TEST",
        "1d",
        Direction.UP,
        PivotRef(0, 0),
        PivotRef(1, 1),
        PivotRef(2, 2),
        "test",
        BoxAnchorMode.ATTACK_VIRTUAL_EXTREME,
    )
    policy = RegimePolicy(ShadowPolicy(0.5), WaveBoundary.ORIGIN, local_resistance_failure=True)
    result = observe_market_regime(bars, setup, timeframe="1d", policy=policy)
    assert (result.latest.regime == MarketRegime.BULL) is confirmed
    assert result.latest.rolling_defense_held
    assert bars[-1].close > bars[-2].close
    broken = bars[:-1] + [replace(bars[-1], low=10.3)]
    assert observe_market_regime(broken, setup, timeframe="1d", policy=policy).latest.regime is None


def test_later_upper_shadow_does_not_extend_original_n_resistance_window():
    rows = [
        (8.5, 9, 8, 8.5),
        (10, 12, 9.5, 11),
        (10.7, 11, 10, 10.5),
        (10.8, 12.6, 10.4, 12.2),
        (12.4, 13.5, 11.8, 12.4),
        (12.4, 14, 12, 12.8),
    ]
    bars = [Bar(datetime(2022, 1, i + 1), "TEST", *row, 1000) for i, row in enumerate(rows)]
    setup = NSetup(
        "TEST",
        "1d",
        Direction.UP,
        PivotRef(0, 0),
        PivotRef(1, 1),
        PivotRef(2, 2),
        "test",
        BoxAnchorMode.ATTACK_VIRTUAL_EXTREME,
    )
    policy = RegimePolicy(ShadowPolicy(0.5), WaveBoundary.ORIGIN, local_resistance_failure=True)
    result = observe_market_regime(bars, setup, timeframe="1d", policy=policy)
    assert result.latest.resistance.long_shadow
    assert result.latest.first_resistance_index == 4
    assert result.latest.regime == MarketRegime.BULL


@pytest.mark.parametrize(
    "change,confirmed",
    [
        ({}, True),
        ({"volume": 1000}, True),
        ({"volume": 999}, True),
        ({"close": 13.5}, False),
        ({"high": 16}, True),
        ({"low": 10}, True),
        ({"low": 9.9}, False),
        ({"open": 14}, False),
    ],
)
def test_later_candle_facts_do_not_extend_n_resistance_window(change: dict[str, float], confirmed: bool) -> None:
    rows = [
        (8.5, 9, 8, 8.5),
        (10, 12, 9.5, 11),
        (10.7, 11, 10, 10.5),
        (10.8, 12.6, 10, 12.2),
        (12.4, 13.5, 11.8, 12.4),
        (12, 14, 11.5, 14),
    ]
    bars = [Bar(datetime(2022, 1, index + 1), "TEST", *row, 1000) for index, row in enumerate(rows)]
    bars[-1] = replace(bars[-1], **{"volume": 2000, **change})
    setup = NSetup(
        "TEST", "1d", Direction.UP, PivotRef(0, 0), PivotRef(1, 1), PivotRef(2, 2),
        "test", BoxAnchorMode.ATTACK_VIRTUAL_EXTREME,
    )
    policy = RegimePolicy(ShadowPolicy(0.5), WaveBoundary.ORIGIN, local_resistance_failure=True)
    result = observe_market_regime(bars, setup, timeframe="1d", policy=policy)

    latest = result.latest
    assert latest is not None
    assert (latest.regime == MarketRegime.BULL) is confirmed
    if confirmed:
        assert latest.resistance is not None
        assert latest.resistance.direct_opposing_open
        assert latest.resistance.detected
        assert latest.first_resistance_index == 4
        prefix = observe_market_regime(bars, setup, timeframe="1d", policy=policy, asof_index=4)
        assert prefix.frames == result.frames[:-1]


def test_later_resistance_cannot_revive_broken_defense_or_create_n_resistance() -> None:
    rows = [
        (8.5, 9, 8, 8.5),
        (10, 12, 9.5, 11),
        (10.7, 11, 10, 10.5),
        (10.8, 12.6, 10, 12.2),
        (12.4, 13.5, 9.9, 12.4),
        (12, 14, 11.5, 14),
    ]
    bars = [Bar(datetime(2022, 1, index + 1), "TEST", *row, 1000) for index, row in enumerate(rows)]
    bars[-1] = replace(bars[-1], volume=2000)
    setup = NSetup(
        "TEST", "1d", Direction.UP, PivotRef(0, 0), PivotRef(1, 1), PivotRef(2, 2),
        "test", BoxAnchorMode.ATTACK_VIRTUAL_EXTREME,
    )
    policy = RegimePolicy(ShadowPolicy(0.5), WaveBoundary.ORIGIN, local_resistance_failure=True)
    latest = observe_market_regime(bars, setup, timeframe="1d", policy=policy).latest
    assert latest is not None and latest.regime is None

    bars[-2] = replace(bars[-2], open=12.3, high=12.5, low=12.2)
    result = observe_market_regime(bars, setup, timeframe="1d", policy=policy)
    latest = result.latest
    assert latest is not None
    assert latest.resistance is not None and latest.resistance.direct_opposing_open
    assert latest.first_resistance_index is None
    assert latest.regime is None


def test_n_attack_day_resistance_is_retained_in_confirmation_window() -> None:
    rows = [
        (8.5, 9, 8, 8.5),
        (10, 12, 9.5, 11),
        (10.7, 11, 10, 10.5),
        (10.4, 12.6, 10, 12.2),
        (12.3, 12.5, 12.2, 12.4),
        (12, 14, 11.5, 14),
    ]
    bars = [Bar(datetime(2022, 1, index + 1), "TEST", *row, 1000) for index, row in enumerate(rows)]
    setup = NSetup(
        "TEST", "1d", Direction.UP, PivotRef(0, 0), PivotRef(1, 1), PivotRef(2, 2),
        "test", BoxAnchorMode.ATTACK_VIRTUAL_EXTREME,
    )
    policy = RegimePolicy(ShadowPolicy(0.5), WaveBoundary.ORIGIN, local_resistance_failure=True)
    result = observe_market_regime(bars, setup, timeframe="1d", policy=policy)
    assert result.frames[0].resistance is not None
    assert result.frames[0].resistance.direct_opposing_open
    assert result.frames[0].first_resistance_index == 3
    assert result.frames[1].regime is None
    latest = result.latest
    assert latest is not None and latest.first_resistance_index == 3
    assert latest.regime == MarketRegime.BULL


def test_record_close_confirms_prior_window_resistance_with_later_upper_shadow():
    rows = [
        (3.75, 3.80, 3.72, 3.75),
        (3.78, 3.83, 3.76, 3.80),
        (3.77, 3.80, 3.73, 3.75),
        (3.82, 3.93, 3.78, 3.91),
        (3.88, 3.96, 3.88, 3.93),
        (3.99, 4.29, 3.95, 4.08),
    ]
    days = ("2022-11-23", "2022-11-25", "2022-11-28", "2022-11-29", "2022-11-30", "2022-12-01")
    bars = [Bar(datetime.fromisoformat(day), "TEST", *row, 1000) for day, row in zip(days, rows)]
    setup = NSetup(
        "TEST", "1d", Direction.UP, PivotRef(0, 0), PivotRef(1, 1), PivotRef(2, 2),
        "test", BoxAnchorMode.ATTACK_VIRTUAL_EXTREME,
    )
    policy = RegimePolicy(ShadowPolicy(0.5), WaveBoundary.ORIGIN, local_resistance_failure=True)

    result = observe_market_regime(bars, setup, timeframe="1d", policy=policy)
    assert result.frames[-2].resistance.direct_opposing_open
    assert result.latest.resistance.long_shadow
    assert result.latest.regime == MarketRegime.BULL

    no_gap = bars[:-1] + [replace(bars[-1], open=bars[-2].high)]
    assert observe_market_regime(no_gap, setup, timeframe="1d", policy=policy).latest.regime == MarketRegime.BULL
    bearish = bars[:-1] + [replace(bars[-1], open=4.10)]
    assert observe_market_regime(bearish, setup, timeframe="1d", policy=policy).latest.regime is None
    broken_defense = bars[:-1] + [replace(bars[-1], low=3.70)]
    assert observe_market_regime(broken_defense, setup, timeframe="1d", policy=policy).latest.regime is None


def test_guofang_july16_lower_open_is_resistance_not_squeeze_confirmation():
    raw = json.loads((Path(__file__).parent / "fixtures/guofang_2026_consolidation.json").read_text(encoding="utf-8"))
    bars = [
        Bar(datetime.fromisoformat(day), raw["symbol"], *values) for day, *values in raw["bars"] if day <= "2026-07-24"
    ]
    config = SystemStrategy(
        pivot_mode="lecture_causal",
        entry_policy="hierarchical_two_buy_points",
        buy_point_definition="whole_flip_wave_v3",
        volume_filter=False,
        strict_n_attack_quality=False,
        preflight_reward_risk=False,
    )
    full = generate_system_signals(bars, config)
    attack = next(
        e
        for e in full.audit
        if e["event"] == "n_completed" and e["direction"] == "up" and e["timestamp"].startswith("2026-07-10")
    )
    assert not any(
        e["event"] == "regime_confirmation"
        and e["attack"] == attack["bar_index"]
        and e["timestamp"].startswith("2026-07-16")
        for e in full.audit
    )
    assert not any(
        s.side == "LONG" and "2026-07-16" <= s.timestamp.date().isoformat() <= "2026-07-17" for s in full.signals
    )
    cutoff = next(i for i, b in enumerate(bars) if b.timestamp.date().isoformat() == "2026-07-16")
    prefix = generate_system_signals(bars[: cutoff + 1], config)
    assert prefix.signals == [s for s in full.signals if s.bar_index <= cutoff]


def test_guofang_n_survives_as_history_without_false_squeeze():
    raw = json.loads((Path(__file__).parent / "fixtures/guofang_2022_squeeze.json").read_text(encoding="utf-8"))
    bars = [Bar(datetime.fromisoformat(day), raw["symbol"], *values) for day, *values in raw["bars"]]
    config = SystemStrategy(
        pivot_mode="lecture_causal",
        entry_policy="hierarchical_two_buy_points",
        buy_point_definition="whole_flip_wave_v3",
        volume_filter=False,
        strict_n_attack_quality=False,
        preflight_reward_risk=False,
    )
    full = generate_system_signals(bars, config)
    attacks = {e["timestamp"][:10]: e for e in full.audit if e["event"] == "n_completed" and e["direction"] == "up"}
    for day in ("2022-05-16", "2022-05-18"):
        attack = attacks[day]["bar_index"]
        assert not any(
            e["event"] == "regime_confirmation"
            and e["attack"] == attack
            and e["regime"] in (MarketRegime.BULL.value, MarketRegime.STRONG_BULL.value)
            for e in full.audit
        )
        assert not any(s.side == "LONG" and s.trigger_timestamp == bars[attack].timestamp for s in full.signals)
    assert not any(s.side == "LONG" and s.timestamp.date().isoformat() == "2022-05-23" for s in full.signals)
    for day in ("2022-05-16", "2022-05-17", "2022-05-18", "2022-05-23", "2022-05-24"):
        cutoff = next(i for i, b in enumerate(bars) if b.timestamp.date().isoformat() == day)
        prefix = generate_system_signals(bars[: cutoff + 1], config)
        assert prefix.signals == [s for s in full.signals if s.bar_index <= cutoff]
        assert [e for e in prefix.audit if e["event"] == "regime_confirmation"] == [
            e for e in full.audit if e["event"] == "regime_confirmation" and e["bar_index"] <= cutoff
        ]
