"""Bearish mother/child volume warnings protect the child's low after reduction."""

from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path

import pytest

from wavequant.application.analytics.backtest import run_backtest
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar, Signal
from wavequant.domain.strategies.staged_exit import StagedExitState, observe_volume_down_exit


def xianfeng_april() -> list[Bar]:
    path = Path(__file__).parent / "fixtures/xianfeng_2026_resistance.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return [Bar(datetime.fromisoformat(day), data["symbol"], *prices)
            for day, *prices in data["bars"] if "2026-04-10" <= day <= "2026-04-21"]


def test_xianfeng_equal_low_bearish_pair_compares_volume_before_the_mother():
    bars = xianfeng_april()
    state = StagedExitState()
    assert bars[5].close < bars[5].open and bars[6].close < bars[6].open
    assert bars[5].low == bars[6].low == 4.89
    assert bars[6].volume < bars[5].volume
    warning = observe_volume_down_exit(bars, 6, state)
    assert warning is not None and warning["reason"] == "volume_bearish_mother_child_reduce_70"
    assert warning["exit_target_fraction"] == .7
    assert warning["mother_date"] == "2026-04-17"
    assert warning["child_date"] == "2026-04-20"
    assert warning["bearish_reference_date"] == "2026-04-15"
    assert warning["bearish_reference_volume"] == 15_569_200
    assert warning["child_volume"] == 20_778_797
    clear = observe_volume_down_exit(bars, 7, state)
    assert bars[7].close > bars[6].close
    assert clear is not None and clear["reason"] == "volume_bearish_mother_child_low_clear"
    assert clear["exit_fraction"] == 1
    assert clear["observed_low"] == 4.88
    assert clear["child_low"] == 4.89


@pytest.mark.parametrize("change", [
    {"volume": 15_569_200},
    {"volume": 15_569_199},
    {"close": 4.96},
    {"close": 4.97},
    {"high": 5.19},
    {"low": 4.88},
    {"high": 5.18, "low": 4.89},
])
def test_bearish_pair_rejects_equal_volume_non_bearish_child_and_non_containment(change):
    bars = xianfeng_april()
    bars[6] = replace(bars[6], **change)
    decision = observe_volume_down_exit(bars, 6, StagedExitState())
    assert decision is None or decision["reason"] != "volume_bearish_mother_child_reduce_70"


@pytest.mark.parametrize("change", [{"low": 4.90}, {"high": 5.18, "low": 4.90}])
def test_bearish_pair_accepts_strict_inside_or_one_equal_boundary(change):
    bars = xianfeng_april()
    bars[6] = replace(bars[6], **change)
    decision = observe_volume_down_exit(bars, 6, StagedExitState())
    assert decision is not None and decision["reason"] == "volume_bearish_mother_child_reduce_70"


def test_child_low_warning_survives_a_safe_next_session_and_equal_touch():
    bars = xianfeng_april()
    bars[7] = replace(bars[7], low=4.89)
    bars.append(Bar(datetime(2026, 4, 22), bars[0].symbol, 5.07, 5.10, 4.88, 5.09, 1_000))
    state = StagedExitState()
    assert observe_volume_down_exit(bars, 6, state) is not None
    assert observe_volume_down_exit(bars, 7, state) is None
    clear = observe_volume_down_exit(bars, 8, state)
    assert clear is not None and clear["reason"] == "volume_bearish_mother_child_low_clear"
    assert clear["child_date"] == "2026-04-20"
    assert clear["observed_close"] > clear["child_close"]


def test_prior_reduction_does_not_disable_the_bearish_child_low_warning():
    bars = xianfeng_april()
    state = StagedExitState(volume_reduction_target=.7, volume_trigger_index=3)
    observe_volume_down_exit(bars, 6, state)
    clear = observe_volume_down_exit(bars, 7, state)
    assert clear is not None and clear["reason"] == "volume_bearish_mother_child_low_clear"


def test_pair_requires_a_bearish_reference_before_the_combination():
    bars = xianfeng_april()
    for index in range(5):
        bars[index] = replace(bars[index], open=bars[index].low, close=bars[index].high)
    decision = observe_volume_down_exit(bars, 6, StagedExitState())
    assert decision is None or decision["reason"] != "volume_bearish_mother_child_reduce_70"


@pytest.mark.parametrize("reference_volume", [0, 20_778_797])
def test_pair_requires_positive_and_strictly_lower_reference_volume(reference_volume):
    bars = xianfeng_april()
    bars[3] = replace(bars[3], volume=reference_volume)
    decision = observe_volume_down_exit(bars, 6, StagedExitState())
    assert decision is None or decision["reason"] != "volume_bearish_mother_child_reduce_70"


def test_doji_mother_is_not_a_bearish_pair():
    bars = xianfeng_april()
    bars[5] = replace(bars[5], close=bars[5].open)
    decision = observe_volume_down_exit(bars, 6, StagedExitState())
    assert decision is None or decision["reason"] != "volume_bearish_mother_child_reduce_70"


def test_bullish_mother_warning_does_not_hide_an_overlapping_bearish_pair():
    rows = [(5.0, 5.02, 4.8, 4.9, 100), (4.9, 5.6, 4.9, 5.5, 500),
            (5.4, 5.5, 5.1, 5.2, 200), (5.23, 5.28, 5.1, 5.15, 600),
            (5.13, 5.25, 5.09, 5.23, 1)]
    bars = [Bar(datetime(2026, 2, day), "sz.300163", *prices)
            for day, prices in enumerate(rows, 1)]
    state = StagedExitState()
    first = observe_volume_down_exit(bars, 2, state)
    assert first is not None and first["reason"] == "volume_bearish_child_reduce_70"
    observe_volume_down_exit(bars, 3, state)
    assert state.bearish_mother_child_warning_index == 3
    clear = observe_volume_down_exit(bars, 4, state)
    assert clear is not None and clear["reason"] == "volume_bearish_mother_child_low_clear"


@pytest.mark.parametrize("initial_capital", [100_000, 1_000])
def test_xianfeng_portfolio_reduces_then_clears_on_bullish_low_break_without_future_data(initial_capital):
    bars = xianfeng_april()
    entry = Signal(bars[4].timestamp, bars[4].symbol, 4, "LONG", bars[4].close, 4.52,
                   "fixture", bars[4].timestamp, 0, None, "fixture", 7.30)
    config = StrategyConfig(entry_at_close=True, volume_down_exit=True, staged_exit_enabled=False,
                            exit_on_target=False, max_participation=1, risk_fraction=.2,
                            max_position_weight=.8, slippage_bps_per_side=0, initial_capital=initial_capital)
    result = run_backtest(bars, [entry], config)
    fills = [order for order in result.orders if order["status"] == "filled"]
    expected = [
        ("BUY", "2026-04-16", "fixture"),
        ("SELL", "2026-04-20", "volume_bearish_mother_child_reduce_70"),
        ("SELL", "2026-04-21", "volume_bearish_mother_child_low_clear"),
    ]
    if initial_capital == 1_000:
        expected.pop(1)
        assert any(order["reason"] == "reduction_below_one_lot" for order in result.orders)
    else:
        assert fills[1]["quantity"] == (fills[0]["quantity"] * 70 // 10000) * 100
        assert fills[1]["price"] == 4.90
    assert [(o["side"], o["timestamp"][:10], o["reason"]) for o in fills] == expected
    assert fills[-1]["price"] == 5.06 and fills[-1]["remaining_quantity"] == 0
    for end in (7, 8):
        prefix = run_backtest(bars[:end], [entry], config)
        assert prefix.orders == [order for order in result.orders
                                 if order["timestamp"] <= bars[end - 1].timestamp.isoformat()]
    assert run_backtest(bars, [entry], replace(config, volume_down_exit=False)).trades == []
