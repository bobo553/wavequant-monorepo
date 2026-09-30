"""The Guofang April 2023 bearish inside child warns before its low/close break."""

from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path

import pytest

from wavequant.application.analytics.backtest import run_backtest
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar, Signal
from wavequant.domain.strategies.staged_exit import StagedExitState, observe_volume_down_exit


def guofang_april_2023() -> list[Bar]:
    rows = [
        ("2023-03-28", 4.34, 4.36, 4.25, 4.33, 3_876_000),
        ("2023-03-29", 4.37, 4.38, 4.26, 4.29, 3_526_100),
        ("2023-03-30", 4.29, 4.47, 4.24, 4.43, 12_760_313),
        ("2023-03-31", 4.40, 4.57, 4.33, 4.50, 17_204_587),
        ("2023-04-03", 4.50, 4.50, 4.36, 4.40, 15_040_800),
        ("2023-04-04", 4.39, 4.40, 4.26, 4.30, 11_525_718),
    ]
    return [Bar(datetime.fromisoformat(day), "sh.601086", opening, high, low, close, volume)
            for day, opening, high, low, close, volume in rows]


def test_guofang_april_child_reduces_then_next_session_double_break_clears():
    bars = guofang_april_2023()
    state = StagedExitState()
    assert bars[4].volume < bars[3].volume
    assert observe_volume_down_exit(bars, 3, state) is None
    reduce = observe_volume_down_exit(bars, 4, state)
    assert reduce is not None
    assert reduce["reason"] == "volume_bearish_child_reduce_70"
    assert reduce["exit_target_fraction"] == .7
    assert reduce["mother_date"] == "2023-03-31"
    assert reduce["bearish_reference_date"] == "2023-03-29"
    assert reduce["child_bearish_volume_multiple"] == pytest.approx(15_040_800 / 3_526_100)
    clear = observe_volume_down_exit(bars, 5, state)
    assert clear is not None and clear["reason"] == "volume_bearish_child_break_clear"
    assert clear["child_low"] == 4.36 and clear["child_close"] == 4.40
    assert clear["observed_low"] == 4.26 and clear["observed_close"] == 4.30


def test_guofang_april_portfolio_reduces_70_percent_then_closes_remaining():
    bars = guofang_april_2023()
    entry = Signal(bars[2].timestamp, bars[2].symbol, 2, "LONG", bars[2].close, 4.10,
                   "fixture", bars[2].timestamp, 0, None, "fixture", 6.0)
    config = StrategyConfig(entry_at_close=True, volume_down_exit=True, staged_exit_enabled=False,
                            exit_on_target=False, max_participation=1, risk_fraction=.2,
                            max_position_weight=.8, slippage_bps_per_side=0)
    result = run_backtest(bars, [entry], config)
    buy, reduce, clear = [order for order in result.orders if order["status"] == "filled"]
    assert [(o["side"], o["timestamp"][:10]) for o in (buy, reduce, clear)] == [
        ("BUY", "2023-03-30"), ("SELL", "2023-04-03"), ("SELL", "2023-04-04")]
    assert reduce["reason"] == "volume_bearish_child_reduce_70"
    assert reduce["quantity"] == (buy["quantity"] * 70 // 10000) * 100
    assert reduce["price"] == 4.40
    assert clear["reason"] == "volume_bearish_child_break_clear"
    assert clear["quantity"] == reduce["remaining_quantity"]
    assert clear["price"] == 4.30
    assert clear["position_closed"] is True
    assert result.open_positions == []


@pytest.mark.parametrize("change", [
    {"volume": 3_526_100},
    {"close": 4.50},
    {"high": 4.58},
])
def test_child_requires_more_than_prior_bearish_volume_bearish_body_and_inside_range(change):
    bars = guofang_april_2023()
    bars[4] = replace(bars[4], **change)
    decision = observe_volume_down_exit(bars, 4, StagedExitState())
    assert decision is None or decision["reason"] != "volume_bearish_child_reduce_70"


def test_mother_must_close_above_prior_bearish_resistance():
    bars = guofang_april_2023()
    bars[1] = replace(bars[1], high=4.51)
    decision = observe_volume_down_exit(bars, 4, StagedExitState())
    assert decision is None or decision["reason"] != "volume_bearish_child_reduce_70"


@pytest.mark.parametrize("change", [
    {"low": 4.36, "close": 4.37},
    {"low": 4.26, "close": 4.40},
])
def test_next_session_requires_strict_low_and_close_break(change):
    bars = guofang_april_2023()
    state = StagedExitState()
    assert observe_volume_down_exit(bars, 4, state) is not None
    bars[5] = replace(bars[5], **change)
    decision = observe_volume_down_exit(bars, 5, state)
    assert decision is None


def test_xianfeng_february_child_exceeds_prior_bearish_volume_then_breaks_next_day():
    fixture = Path(__file__).parent / "fixtures/xianfeng_2026_resistance.json"
    raw = json.loads(fixture.read_text(encoding="utf-8"))
    rows = [row for row in raw["bars"] if "2026-01-27" <= row[0] <= "2026-02-04"]
    bars = [Bar(datetime.fromisoformat(day), raw["symbol"], *prices) for day, *prices in rows]
    dates = {bar.timestamp.date().isoformat(): index for index, bar in enumerate(bars)}
    state = StagedExitState()

    child = observe_volume_down_exit(bars, dates["2026-02-03"], state)
    assert child is not None and child["reason"] == "volume_bearish_child_reduce_70"
    assert child["bearish_reference_date"] == "2026-01-27"
    assert child["child_volume"] == 86_844_200
    assert child["child_bearish_volume_multiple"] == pytest.approx(86_844_200 / 55_429_400)
    clear = observe_volume_down_exit(bars, dates["2026-02-04"], state)
    assert clear is not None and clear["reason"] == "volume_bearish_child_break_clear"
    assert clear["observed_low"] < child["child_low"]
    assert clear["observed_close"] < child["child_close"]

    entry_index = dates["2026-01-29"]
    entry = Signal(bars[entry_index].timestamp, bars[entry_index].symbol, entry_index,
                   "LONG", bars[entry_index].close, 4.07, "fixture",
                   bars[entry_index].timestamp, 0, None, "fixture", 6.03)
    config = StrategyConfig(entry_at_close=True, volume_down_exit=True, staged_exit_enabled=False,
                            exit_on_target=False, max_hold_bars=100, max_participation=1,
                            slippage_bps_per_side=0)
    result = run_backtest(bars, [entry], config)
    orders = [order for order in result.orders if order["status"] == "filled"]
    assert [(order["side"], order["timestamp"][:10], order["reason"]) for order in orders] == [
        ("BUY", "2026-01-29", "fixture"),
        ("SELL", "2026-02-03", "volume_bearish_child_reduce_70"),
        ("SELL", "2026-02-04", "volume_bearish_child_break_clear"),
    ]
    assert orders[2]["remaining_quantity"] == 0
