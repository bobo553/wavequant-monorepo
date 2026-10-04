"""V3 resistance failure rebreaks the original N attack's high and close."""

from dataclasses import replace
from datetime import datetime, timedelta
from typing import TypedDict

import pytest

from wavequant.domain.market_state.market_regime import (
    MarketRegime, RegimePolicy, WaveBoundary, observe_market_regime,
)
from wavequant.domain.market_structure.n_shape import BoxAnchorMode, NSetup, PivotRef
from wavequant.domain.market_structure.price_action import Direction, ShadowPolicy
from wavequant.domain.models.model import Bar


class CandleChange(TypedDict, total=False):
    open: float
    high: float
    low: float
    close: float


def sample() -> tuple[list[Bar], NSetup]:
    rows = [
        (8.5, 9, 8, 8.5),
        (10, 12, 9.5, 11),
        (10.7, 11, 10, 10.5),
        (10.4, 12.6, 10, 12.2),
        (12.3, 13.5, 12.2, 12.4),
        (12, 13.0, 11.5, 12.1),
        (12.3, 13.2, 12.2, 13.0),
    ]
    bars = [Bar(datetime(2024, 3, 1) + timedelta(days=i), "TEST", *row, 1000) for i, row in enumerate(rows)]
    setup = NSetup(
        "TEST", "1d", Direction.UP, PivotRef(0, 0), PivotRef(1, 1), PivotRef(2, 2),
        "test_confirmed_pivots", BoxAnchorMode.ATTACK_VIRTUAL_EXTREME,
    )
    return bars, setup


def policy() -> RegimePolicy:
    return RegimePolicy(ShadowPolicy(0.5), WaveBoundary.ORIGIN, local_resistance_failure=True)


def test_v3_attack_high_and_close_break_resolve_window_resistance_below_later_record() -> None:
    bars, setup = sample()
    result = observe_market_regime(bars, setup, timeframe="1d", policy=policy())
    latest = result.latest
    assert latest is not None and latest.regime == MarketRegime.BULL
    assert latest.first_resistance_index == 3
    assert latest.first_defense_breach_index is None
    assert latest.continuation_level == 13.5 > bars[-1].high > bars[3].high
    assert bars[-1].close > bars[3].close
    assert result.frames[-2].rolling_defense_held is False
    prefix = observe_market_regime(bars[:6], setup, timeframe="1d", policy=policy())
    assert prefix.frames == result.frames[:-1]
    assert prefix.latest is not None and prefix.latest.regime is None


@pytest.mark.parametrize("change", [
    {"high": 12.6, "close": 12.5, "open": 12.1, "low": 12.05},
    {"high": 12.59, "close": 12.5, "open": 12.1, "low": 12.05},
    {"high": 12.7, "close": 12.2, "open": 10.7, "low": 10.2},
    {"high": 12.7, "close": 12.19, "open": 10.6, "low": 10.15},
    {"open": 13.0},
    {"open": 13.1},
])
def test_attack_bar_break_requires_both_strict_original_anchors_and_bullish_body(change: CandleChange) -> None:
    bars, setup = sample()
    bars[-1] = replace(bars[-1], **change)
    latest = observe_market_regime(bars, setup, timeframe="1d", policy=policy()).latest
    assert latest is not None and latest.regime is None


@pytest.mark.parametrize("change", [
    {"open": 12.7, "high": 13.04, "low": 12.7},
    {"open": 12.4, "high": 13.1, "low": 12.0},
    {"open": 12.3, "high": 13.4, "low": 12.3},
])
def test_attack_bar_break_requires_strong_body_and_short_upper_shadow(change: CandleChange) -> None:
    bars, setup = sample()
    bars[-1] = replace(bars[-1], **change)
    assert bars[-1].high > bars[3].high and bars[-1].close > bars[3].close
    latest = observe_market_regime(bars, setup, timeframe="1d", policy=policy()).latest
    assert latest is not None and latest.regime is None


def test_strict_record_close_keeps_its_existing_confirmation_without_new_shape_gate() -> None:
    bars, setup = sample()
    bars[-1] = replace(bars[-1], open=13.59, high=14.4, low=13.5, close=13.6)
    latest = observe_market_regime(bars, setup, timeframe="1d", policy=policy()).latest
    assert latest is not None and latest.regime == MarketRegime.BULL


def test_attack_bar_break_cannot_revive_original_defense_or_absent_window_resistance() -> None:
    bars, setup = sample()
    broken = [*bars[:5], replace(bars[5], low=9.9), bars[6]]
    latest = observe_market_regime(broken, setup, timeframe="1d", policy=policy()).latest
    assert latest is not None and latest.first_defense_breach_index == 5
    assert latest.regime is None
    absent = [*bars[:3], replace(bars[3], open=10.8), replace(bars[4], close=13.4), *bars[5:]]
    latest = observe_market_regime(absent, setup, timeframe="1d", policy=policy()).latest
    assert latest is not None and latest.first_resistance_index is None
    assert latest.regime is None


def test_attack_bar_break_still_requires_progress_above_previous_close() -> None:
    bars, setup = sample()
    bars[-2] = replace(bars[-2], close=13.0)
    latest = observe_market_regime(bars, setup, timeframe="1d", policy=policy()).latest
    assert latest is not None and latest.regime is None


def test_attack_bar_break_cannot_confirm_at_the_original_attack_close() -> None:
    bars, setup = sample()
    bars = [
        *bars[:3], replace(bars[3], close=12.6), replace(bars[4], high=12.6),
        replace(bars[5], open=12.0, high=12.7, low=11.95, close=12.6),
    ]
    latest = observe_market_regime(bars, setup, timeframe="1d", policy=policy()).latest
    assert latest is not None and latest.regime is None


def test_attack_bar_break_does_not_confirm_the_next_session() -> None:
    bars, setup = sample()
    bars = [*bars[:4], replace(bars[4], open=12, high=12.7, low=11.95, close=12.6)]
    result = observe_market_regime(bars, setup, timeframe="1d", policy=policy())
    assert len(result.frames) == 2
    assert result.latest is not None and result.latest.regime is None


def test_attack_bar_break_does_not_change_default_research_or_inverse_n() -> None:
    bars, setup = sample()
    default = RegimePolicy(ShadowPolicy(0.5), WaveBoundary.ORIGIN)
    latest = observe_market_regime(bars, setup, timeframe="1d", policy=default).latest
    assert latest is not None and latest.regime is None
    inverse = [replace(bar, open=40-bar.open, high=40-bar.low, low=40-bar.high, close=40-bar.close) for bar in bars]
    latest = observe_market_regime(
        inverse, replace(setup, direction=Direction.DOWN), timeframe="1d", policy=policy(),
    ).latest
    assert latest is not None and latest.regime is None
