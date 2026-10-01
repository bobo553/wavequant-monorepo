"""A bearish child warns even when its volume is lower than earlier bearish volume."""

from dataclasses import replace
from datetime import datetime

import pytest

from wavequant.application.analytics.backtest import run_backtest
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar, Signal
from wavequant.domain.strategies.staged_exit import StagedExitState, observe_bearish_child_pattern_exit


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


def test_equal_low_bearish_child_reduces_without_a_volume_gate_and_clears_on_double_break():
    bars = xianfeng_august_2020()
    state = StagedExitState()
    assert bars[3].low == bars[4].low
    assert bars[4].volume < bars[0].volume

    reduce = observe_bearish_child_pattern_exit(bars, 4, state)
    assert reduce is not None and reduce["reason"] == "bearish_mother_child_reduce_70"
    assert reduce["exit_target_fraction"] == .7
    assert reduce["mother_date"] == "2020-08-11"
    assert reduce["child_date"] == "2020-08-12"

    clear = observe_bearish_child_pattern_exit(bars, 5, state)
    assert clear is not None and clear["reason"] == "bearish_mother_child_break_clear"
    assert clear["child_low"] == 5.21 and clear["child_close"] == 5.34
    assert clear["observed_low"] == 4.96 and clear["observed_close"] == 5.08


@pytest.mark.parametrize("change", [
    {"low": 5.21, "close": 5.22},
    {"low": 5.20, "close": 5.34},
    {"low": 5.20, "close": 5.35},
])
def test_next_session_needs_strict_low_and_close_break(change):
    bars = xianfeng_august_2020()
    bars[5] = replace(bars[5], **change)
    state = StagedExitState()
    assert observe_bearish_child_pattern_exit(bars, 4, state) is not None
    decision = observe_bearish_child_pattern_exit(bars, 5, state)
    assert decision is None or decision["reason"] != "bearish_mother_child_break_clear"


@pytest.mark.parametrize("change", [
    {"high": 5.64},
    {"low": 5.20},
    {"close": 5.48},
    {"open": 5.21, "close": 5.34},
])
def test_non_containment_and_non_bearish_children_do_not_warn(change):
    bars = xianfeng_august_2020()
    bars[4] = replace(bars[4], **change)
    assert observe_bearish_child_pattern_exit(bars, 4, StagedExitState()) is None


def test_double_break_after_the_next_session_does_not_clear():
    bars = xianfeng_august_2020()
    bars[5] = replace(bars[5], low=5.21, close=5.36)
    bars.append(Bar(datetime(2020, 8, 14), bars[0].symbol, 5.20, 5.23, 4.96, 5.08, 1_000))
    state = StagedExitState()
    assert observe_bearish_child_pattern_exit(bars, 4, state) is not None
    assert observe_bearish_child_pattern_exit(bars, 5, state) is None
    assert observe_bearish_child_pattern_exit(bars, 6, state) is None


def test_bearish_mother_needs_no_earlier_bearish_reference():
    rows = [
        ("2020-08-10", 6.0, 6.1, 5.0, 5.5, 1_000),
        ("2020-08-11", 5.6, 5.8, 5.1, 5.3, 500),
        ("2020-08-12", 5.3, 5.4, 5.0, 5.2, 300),
    ]
    bars = [Bar(datetime.fromisoformat(day), "sz.300163", *prices) for day, *prices in rows]
    state = StagedExitState()
    reduction = observe_bearish_child_pattern_exit(bars, 1, state)
    assert reduction is not None and reduction["reason"] == "bearish_mother_child_reduce_70"
    clear = observe_bearish_child_pattern_exit(bars, 2, state)
    assert clear is not None and clear["reason"] == "bearish_mother_child_break_clear"


@pytest.mark.parametrize("initial_capital", [100_000, 1_000])
def test_xianfeng_position_reduces_and_clears_at_daily_close(initial_capital):
    bars = xianfeng_august_2020()
    entry = Signal(bars[2].timestamp, bars[2].symbol, 2, "LONG", bars[2].close, 4.5,
                   "fixture", bars[2].timestamp, 0, None, "fixture", 10.0)
    config = StrategyConfig(entry_at_close=True, volume_down_exit=True, staged_exit_enabled=False,
                            exit_on_target=False, max_participation=1, risk_fraction=.2,
                            max_position_weight=.8, slippage_bps_per_side=0,
                            initial_capital=initial_capital)
    result = run_backtest(bars, [entry], config)
    fills = [order for order in result.orders if order["status"] == "filled"]
    expected = [
        ("BUY", "2020-08-10", "fixture"),
        ("SELL", "2020-08-12", "bearish_mother_child_reduce_70"),
        ("SELL", "2020-08-13", "bearish_mother_child_break_clear"),
    ]
    if initial_capital == 1_000:
        expected.pop(1)
        assert any(order["reason"] == "reduction_below_one_lot" for order in result.orders)
    else:
        assert fills[1]["quantity"] == (fills[0]["quantity"] * 70 // 10000) * 100
    assert [(order["side"], order["timestamp"][:10], order["reason"]) for order in fills] == expected
    assert fills[-1]["remaining_quantity"] == 0
    assert result.open_positions == []
