"""Child reduction keeps its volume gate; a later volume low break can clear directly."""

from dataclasses import replace
from datetime import datetime

import pytest

from wavequant.application.analytics.backtest import run_backtest
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar, Signal
from wavequant.domain.strategies.staged_exit import (
    StagedExitState, observe_bearish_child_pattern_exit, observe_volume_down_exit,
)


def xianfeng_august_2020() -> list[Bar]:
    rows = [
        ("2020-08-06", 4.75, 4.83, 4.48, 4.65, 19_299_500),
        ("2020-08-07", 4.63, 5.00, 4.50, 4.98, 27_648_300),
        ("2020-08-10", 5.01, 5.42, 5.01, 5.35, 30_633_500),
        ("2020-08-11", 5.37, 5.63, 5.21, 5.48, 34_501_400),
        ("2020-08-12", 5.48, 5.51, 5.21, 5.34, 15_799_059),
        ("2020-08-13", 5.34, 5.37, 4.96, 5.08, 19_530_900),
    ]
    return [Bar(datetime.fromisoformat(day), "sz.300163", opening, high, low, close, volume)
            for day, opening, high, low, close, volume in rows]


def test_lower_volume_child_does_not_reduce_but_volume_break_clears_directly():
    bars = xianfeng_august_2020()
    state = StagedExitState()
    assert bars[3].low == bars[4].low
    assert bars[4].volume < bars[0].volume
    assert observe_volume_down_exit(bars, 4, state) is None
    assert observe_bearish_child_pattern_exit(bars, 4, state) is None
    assert state.volume_reduction_target == 0
    clear = observe_bearish_child_pattern_exit(bars, 5, state)
    assert clear is not None and clear["reason"] == "bearish_mother_child_break_clear"
    assert clear["exit_fraction"] == 1
    assert clear["child_low"] == 5.21
    assert clear["child_volume"] == 15_799_059
    assert clear["observed_volume"] == 19_530_900


@pytest.mark.parametrize("volume", [19_299_500, 19_299_499, 19_299_501])
def test_child_reduction_still_compares_strictly_with_the_previous_bearish_reference(volume):
    bars = xianfeng_august_2020()
    bars[4] = replace(bars[4], low=5.22, volume=volume)
    state = StagedExitState()
    decision = observe_volume_down_exit(bars, 4, state)
    assert observe_bearish_child_pattern_exit(bars, 4, state) is None
    if volume > bars[0].volume:
        assert decision is not None and decision["reason"] == "volume_bearish_child_reduce_70"
        assert decision["exit_target_fraction"] == .7
        assert decision["bearish_reference_volume"] == 19_299_500
    else:
        assert decision is None
        assert state.volume_reduction_target == 0


@pytest.mark.parametrize("change", [
    {"low": 5.21, "close": 5.22}, {"volume": 15_799_059}, {"volume": 15_799_058},
    {"low": 5.20, "close": 5.34}, {"low": 5.20, "close": 5.35},
])
def test_equal_low_or_non_expanding_volume_does_not_clear(change):
    bars = xianfeng_august_2020()
    bars[5] = replace(bars[5], **change)
    state = StagedExitState()
    assert observe_bearish_child_pattern_exit(bars, 4, state) is None
    assert observe_bearish_child_pattern_exit(bars, 5, state) is None


@pytest.mark.parametrize("close", [5.34, 5.40])
def test_volume_low_break_requires_close_below_child_close(close):
    bars = xianfeng_august_2020()
    bars[5] = replace(bars[5], close=close, high=5.42)
    state = StagedExitState()
    observe_bearish_child_pattern_exit(bars, 4, state)
    clear = observe_bearish_child_pattern_exit(bars, 5, state)
    assert clear is None


@pytest.mark.parametrize("change", [
    {"high": 5.64}, {"low": 5.20}, {"close": 5.48}, {"open": 5.21, "close": 5.34},
])
def test_non_containment_and_non_bearish_children_do_not_arm_a_clear(change):
    bars = xianfeng_august_2020()
    bars[4] = replace(bars[4], **change)
    state = StagedExitState()
    assert observe_bearish_child_pattern_exit(bars, 4, state) is None
    assert observe_bearish_child_pattern_exit(bars, 5, state) is None


def test_warning_survives_a_later_small_volume_break_before_a_volume_break():
    bars = xianfeng_august_2020()
    bars[5] = replace(bars[5], volume=15_799_059)
    bars.append(Bar(datetime(2020, 8, 14), bars[0].symbol, 5.20, 5.23, 4.95, 5.08, 15_799_060))
    state = StagedExitState()
    observe_bearish_child_pattern_exit(bars, 4, state)
    assert observe_bearish_child_pattern_exit(bars, 5, state) is None
    clear = observe_bearish_child_pattern_exit(bars, 6, state)
    assert clear is not None and clear["child_date"] == "2020-08-12"


@pytest.mark.parametrize("reference_volume,break_volume", [(10_000_000, 12_000_000), (20_000_000, 16_000_000)])
def test_either_child_or_previous_bearish_volume_is_sufficient(reference_volume, break_volume):
    bars = xianfeng_august_2020()
    bars[5] = replace(bars[5], open=5.36, high=5.50, low=5.20, close=5.35, volume=reference_volume)
    bars.append(Bar(datetime(2020, 8, 14), bars[0].symbol, 5.15, 5.30, 5.10, 5.25, break_volume))
    state = StagedExitState()
    observe_bearish_child_pattern_exit(bars, 4, state)
    assert observe_bearish_child_pattern_exit(bars, 5, state) is None
    clear = observe_bearish_child_pattern_exit(bars, 6, state)
    assert clear is not None and clear["exit_fraction"] == 1
    assert clear["child_volume"] == 15_799_059
    assert clear["bearish_reference_date"] == "2020-08-13"
    assert clear["bearish_reference_volume"] == reference_volume
    assert clear["observed_close"] > bars[6].open


def test_no_prior_bearish_reference_prevents_reduction_but_not_direct_volume_clear():
    bars = xianfeng_august_2020()[3:]
    state = StagedExitState()
    assert observe_volume_down_exit(bars, 1, state) is None
    assert observe_bearish_child_pattern_exit(bars, 1, state) is None
    assert state.volume_reduction_target == 0
    clear = observe_bearish_child_pattern_exit(bars, 2, state)
    assert clear is not None and clear["exit_fraction"] == 1


@pytest.mark.parametrize("change", [{"volume": 15_799_059}, {"low": 5.21, "close": 5.22}])
def test_position_remains_open_without_a_strict_volume_and_low_break(change):
    bars = xianfeng_august_2020()
    bars[5] = replace(bars[5], **change)
    entry = Signal(bars[2].timestamp, bars[2].symbol, 2, "LONG", bars[2].close, 4.5,
                   "fixture", bars[2].timestamp, 0, None, "fixture", 10.0)
    config = StrategyConfig(entry_at_close=True, volume_down_exit=True, staged_exit_enabled=False,
                            exit_on_target=False, max_participation=1, risk_fraction=.2,
                            max_position_weight=.8, slippage_bps_per_side=0)
    result = run_backtest(bars, [entry], config)
    assert not any(order["reason"] == "bearish_mother_child_break_clear" for order in result.orders)
    assert result.open_positions


@pytest.mark.parametrize("initial_capital", [100_000, 1_000])
def test_xianfeng_position_skips_child_reduction_and_clears_at_break_day_close(initial_capital):
    bars = xianfeng_august_2020()
    entry = Signal(bars[2].timestamp, bars[2].symbol, 2, "LONG", bars[2].close, 4.5,
                   "fixture", bars[2].timestamp, 0, None, "fixture", 10.0)
    config = StrategyConfig(entry_at_close=True, volume_down_exit=True, staged_exit_enabled=False,
                            exit_on_target=False, max_participation=1, risk_fraction=.2,
                            max_position_weight=.8, slippage_bps_per_side=0, initial_capital=initial_capital)
    result = run_backtest(bars, [entry], config)
    fills = [order for order in result.orders if order["status"] == "filled"]
    assert [(o["side"], o["timestamp"][:10], o["reason"]) for o in fills] == [
        ("BUY", "2020-08-10", "fixture"),
        ("SELL", "2020-08-13", "bearish_mother_child_break_clear"),
    ]
    assert fills[-1]["quantity"] == fills[0]["quantity"]
    assert fills[-1]["remaining_quantity"] == 0 and fills[-1]["price"] == 5.08
    assert fills[-1]["observed_volume"] > fills[-1]["child_volume"]
    for end in (5, 6):
        prefix = run_backtest(bars[:end], [entry], config)
        assert prefix.orders == [order for order in result.orders
                                 if order["timestamp"] <= bars[end - 1].timestamp.isoformat()]
    assert run_backtest(bars, [entry], replace(config, volume_down_exit=False)).trades == []
