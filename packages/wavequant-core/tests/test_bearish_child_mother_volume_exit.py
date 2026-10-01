"""A volume bearish outside mother reduces first and protects its own low and close."""

from dataclasses import replace
from datetime import datetime, timedelta
import json
from pathlib import Path

import pytest

from wavequant.application.analytics.backtest import run_backtest
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar, Signal
from wavequant.domain.strategies.staged_exit import StagedExitState, observe_volume_down_exit


def xinhua_january() -> list[Bar]:
    data = json.loads((Path(__file__).parent / "fixtures/xinhuawenxuan_2026_trend.json").read_text(encoding="utf-8"))
    return [Bar(datetime.fromisoformat(day), data["symbol"], *prices)
            for day, *prices in data["bars"] if "2024-01-19" <= day <= "2024-01-30"]


def test_xinhua_equal_high_mother_compares_with_previous_bearish_not_child_volume():
    bars = xinhua_january()
    state = StagedExitState()
    mother, child = bars[6], bars[5]
    assert mother.high == child.high and mother.low < child.low
    assert bars[1].volume < mother.volume < child.volume
    reduction = observe_volume_down_exit(bars, 6, state)
    assert reduction is not None and reduction["reason"] == "volume_bearish_child_mother_reduce_70"
    assert reduction["exit_target_fraction"] == .7
    assert reduction["child_date"] == "2024-01-26"
    assert reduction["mother_date"] == "2024-01-29"
    assert reduction["mother_volume"] == 3_106_650
    assert reduction["bearish_reference_date"] == "2024-01-22"
    assert reduction["bearish_reference_volume"] == 2_868_478
    clear = observe_volume_down_exit(bars, 7, state)
    assert clear is not None and clear["reason"] == "volume_bearish_child_mother_break_clear"
    assert clear["exit_fraction"] == 1
    assert clear["observed_low"] < clear["mother_low"]
    assert clear["observed_close"] < clear["mother_close"]


@pytest.mark.parametrize("change", [
    {"volume": 2_868_478}, {"volume": 2_868_477},
    {"close": 16.1589337319377}, {"close": 16.20},
    {"high": 16.32}, {"low": 15.90}, {"low": 15.894230424340806},
])
def test_equal_volume_non_bearish_or_non_outside_mother_does_not_reduce(change):
    bars = xinhua_january()
    bars[6] = replace(bars[6], **change)
    decision = observe_volume_down_exit(bars, 6, StagedExitState())
    assert decision is None or decision["reason"] != "volume_bearish_child_mother_reduce_70"


@pytest.mark.parametrize("change", [
    {"high": 16.40}, {"high": 16.40, "low": 15.894230424340806},
])
def test_mother_accepts_strict_outside_or_one_equal_low_boundary(change):
    bars = xinhua_january()
    bars[6] = replace(bars[6], **change)
    decision = observe_volume_down_exit(bars, 6, StagedExitState())
    assert decision is not None and decision["reason"] == "volume_bearish_child_mother_reduce_70"


def test_bearish_child_is_the_previous_bearish_volume_reference():
    bars = xinhua_january()
    bars[5] = replace(bars[5], open=16.20)
    assert observe_volume_down_exit(bars, 6, StagedExitState()) is None
    bars[6] = replace(bars[6], volume=bars[5].volume + 1)
    decision = observe_volume_down_exit(bars, 6, StagedExitState())
    assert decision is not None and decision["reason"] == "volume_bearish_child_mother_reduce_70"
    assert decision["bearish_reference_date"] == "2024-01-26"


def test_doji_child_is_allowed_but_skipped_as_a_volume_reference():
    bars = xinhua_january()
    bars[5] = replace(bars[5], open=bars[5].close)
    decision = observe_volume_down_exit(bars, 6, StagedExitState())
    assert decision is not None and decision["reason"] == "volume_bearish_child_mother_reduce_70"
    assert decision["bearish_reference_date"] == "2024-01-22"


@pytest.mark.parametrize("change", [
    {"low": 15.84610255023228, "close": 15.90},
    {"low": 15.88, "close": 16.00},
    {"close": 16.038614046666385, "high": 16.10}, {"close": 16.05, "high": 16.10},
])
def test_next_session_clear_requires_both_strict_mother_price_breaks(change):
    bars = xinhua_january()
    state = StagedExitState()
    observe_volume_down_exit(bars, 6, state)
    bars[7] = replace(bars[7], **change)
    decision = observe_volume_down_exit(bars, 7, state)
    assert decision is None or decision["reason"] != "volume_bearish_child_mother_break_clear"


def test_next_session_clear_does_not_require_expanding_volume_or_a_bearish_body():
    bars = xinhua_january()
    state = StagedExitState()
    observe_volume_down_exit(bars, 6, state)
    bars[7] = replace(bars[7], open=15.30, volume=1)
    decision = observe_volume_down_exit(bars, 7, state)
    assert decision is not None and decision["reason"] == "volume_bearish_child_mother_break_clear"


def test_missing_or_zero_previous_bearish_volume_does_not_arm_the_mother():
    bars = xinhua_january()
    for index in (0, 1):
        bars[index] = replace(bars[index], open=bars[index].low, close=bars[index].high)
    state = StagedExitState()
    decision = observe_volume_down_exit(bars, 6, state)
    assert decision is None or decision["reason"] != "volume_bearish_child_mother_reduce_70"
    decision = observe_volume_down_exit(bars, 7, state)
    assert decision is None or decision["reason"] != "volume_bearish_child_mother_break_clear"
    bars = xinhua_january()
    bars[1] = replace(bars[1], volume=0)
    decision = observe_volume_down_exit(bars, 6, StagedExitState())
    assert decision is None or decision["reason"] != "volume_bearish_child_mother_reduce_70"


def test_existing_seventy_percent_reduction_still_arms_mother_clear():
    bars = xinhua_january()
    state = StagedExitState(volume_reduction_target=.7)
    assert observe_volume_down_exit(bars, 6, state) is None
    assert observe_volume_down_exit(bars, 7, state)["reason"] == "volume_bearish_child_mother_break_clear"


def test_clear_is_only_for_the_next_trading_session():
    bars = xinhua_january()
    bars[7] = replace(bars[7], low=bars[6].low, close=16.00)
    bars.append(replace(bars[7], timestamp=bars[7].timestamp + timedelta(days=1), low=15.3, close=15.4))
    state = StagedExitState()
    observe_volume_down_exit(bars, 6, state)
    observe_volume_down_exit(bars, 7, state)
    decision = observe_volume_down_exit(bars, 8, state)
    assert decision is None or decision["reason"] != "volume_bearish_child_mother_break_clear"


@pytest.mark.parametrize("initial_capital", [100_000, 3_000])
def test_xinhua_mother_reduction_and_next_session_clear_are_causal_close_fills(initial_capital):
    bars = xinhua_january()
    entry = Signal(bars[4].timestamp, bars[4].symbol, 4, "LONG", bars[4].close, 14.0,
                   "fixture", bars[4].timestamp, 0, None, "fixture", 30.0)
    config = StrategyConfig(entry_at_close=True, volume_down_exit=True, staged_exit_enabled=False,
                            exit_on_target=False, max_hold_bars=100, max_participation=1,
                            risk_fraction=.2, max_position_weight=.8, slippage_bps_per_side=0,
                            initial_capital=initial_capital)
    result = run_backtest(bars, [entry], config)
    fills = [o for o in result.orders if o["status"] == "filled"]
    expected = [("BUY", "2024-01-25", "fixture"),
                ("SELL", "2024-01-29", "volume_bearish_child_mother_reduce_70"),
                ("SELL", "2024-01-30", "volume_bearish_child_mother_break_clear")]
    if initial_capital == 3_000:
        expected.pop(1)
        assert any(o["reason"] == "reduction_below_one_lot" for o in result.orders)
    else:
        assert fills[1]["quantity"] == (fills[0]["quantity"] * 70 // 10000) * 100
        assert fills[1]["price"] == bars[6].close
    assert [(o["side"], o["timestamp"][:10], o["reason"]) for o in fills] == expected
    assert fills[-1]["remaining_quantity"] == 0 and fills[-1]["price"] == bars[7].close
    assert fills[-1]["mother_date"] == "2024-01-29"
    for end in (7, 8):
        prefix = run_backtest(bars[:end], [entry], config)
        assert prefix.orders == [o for o in result.orders if o["timestamp"] <= bars[end - 1].timestamp.isoformat()]
    assert run_backtest(bars, [entry], replace(config, volume_down_exit=False)).trades == []
