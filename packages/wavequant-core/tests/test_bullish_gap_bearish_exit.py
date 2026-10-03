"""A volume backed long bullish candle can reverse through its body the next day."""

import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest

from wavequant.application.analytics.backtest import run_portfolio
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar, Signal
from wavequant.domain.strategies.staged_exit import StagedExitState, observe_volume_down_exit


def guofang_april_bars() -> list[Bar]:
    fixture = Path(__file__).parent / "fixtures/guofang_2022_squeeze.json"
    raw = json.loads(fixture.read_text(encoding="utf-8"))
    return [Bar(datetime.fromisoformat(day), raw["symbol"], *prices)
            for day, *prices in raw["bars"] if "2022-04-18" <= day <= "2022-04-25"]


def test_guofang_april_25_uses_bullish_volume_before_exceptional_session():
    bars = guofang_april_bars()
    assert [bar.timestamp.date().isoformat() for bar in bars] == [
        "2022-04-18", "2022-04-19", "2022-04-20", "2022-04-21",
        "2022-04-22", "2022-04-25",
    ]
    assert bars[4].volume == 76_131_957 < bars[3].volume == 88_851_705
    assert bars[3].volume >= 2 * bars[2].volume == 73_101_784
    assert bars[4].volume > bars[2].volume
    assert bars[5].open < (bars[4].open + bars[4].close) / 2
    assert bars[5].close < bars[5].open
    assert bars[5].volume == 47_988_400 > bars[0].volume == 40_462_266
    decision = observe_volume_down_exit(bars, 5, StagedExitState())
    assert decision is not None
    assert decision["reason"] == "volume_bullish_gap_bearish_clear"
    assert decision["bullish_volume_basis"] == "bullish_before_exceptional_volume"
    assert decision["bullish_volume_benchmark_date"] == "2022-04-20"
    assert decision["bullish_volume_benchmark"] == 36_550_892
    assert decision["bearish_reference_date"] == "2022-04-18"
    assert decision["bearish_reference_volume"] == 40_462_266

    signal = Signal(bars[2].timestamp, bars[2].symbol, 2, "LONG", bars[2].close, 1,
                    "fixture", bars[2].timestamp, 0, None, "fixture", 20)
    config = StrategyConfig(initial_capital=100_000, max_position_weight=.8, max_participation=1,
                            slippage_bps_per_side=0, entry_at_close=True, exit_on_target=False,
                            volume_down_exit=True, max_hold_bars=100)
    result = run_portfolio({bars[0].symbol: bars}, [signal], config)
    fills = [order for order in result.orders if order["status"] == "filled"]
    assert [(order["timestamp"][:10], order["reason"]) for order in fills] == [
        ("2022-04-20", "fixture"),
        ("2022-04-25", "volume_bullish_gap_bearish_clear"),
    ]
    assert fills[-1]["remaining_quantity"] == 0
    assert fills[-1]["bearish_reference_date"] == "2022-04-18"
    prefix = run_portfolio({bars[0].symbol: bars[:5]}, [signal], config)
    assert prefix.orders == [order for order in result.orders if order["timestamp"] < bars[5].timestamp.isoformat()]


def test_guofang_april_25_clears_when_prior_bull_volume_exceeds_previous_session():
    bars = guofang_april_bars()
    bars[4] = replace(bars[4], volume=90_000_000)
    decision = observe_volume_down_exit(bars, 5, StagedExitState())
    assert decision is not None
    assert decision["reason"] == "volume_bullish_gap_bearish_clear"
    assert decision["exit_fraction"] == 1.0
    assert decision["execution_model"] == "same_day_close"
    assert decision["bullish_date"] == "2022-04-22"
    assert decision["bullish_volume"] == 90_000_000
    assert decision["bullish_volume_basis"] == "previous_session"
    assert decision["bullish_volume_benchmark_date"] == "2022-04-21"
    assert decision["bearish_reference_date"] == "2022-04-18"
    assert decision["bearish_reference_volume"] == 40_462_266
    assert decision["observed_open"] < decision["bullish_body_midpoint"]

    signal = Signal(bars[2].timestamp, bars[2].symbol, 2, "LONG", bars[2].close, 1,
                    "fixture", bars[2].timestamp, 0, None, "fixture", 20)
    config = StrategyConfig(initial_capital=100_000, max_position_weight=.8, max_participation=1,
                            slippage_bps_per_side=0, entry_at_close=True, exit_on_target=False,
                            volume_down_exit=True, max_hold_bars=100)
    result = run_portfolio({bars[0].symbol: bars}, [signal], config)
    fills = [order for order in result.orders if order["status"] == "filled"]
    assert [(order["timestamp"][:10], order["reason"]) for order in fills] == [
        ("2022-04-20", "fixture"),
        ("2022-04-25", "volume_bullish_gap_bearish_clear"),
    ]
    assert fills[-1]["remaining_quantity"] == 0
    assert fills[-1]["bearish_reference_date"] == "2022-04-18"


@pytest.mark.parametrize("change", [
    "equal_benchmark_volume", "prior_volume_not_exceptional", "large_prior_bearish", "no_earlier_bullish",
    "short_bull", "equal_body_midpoint", "bullish_second_day",
    "equal_bearish_volume", "no_prior_bearish",
])
def test_gap_reversal_requires_all_conditions(change: str):
    bars = guofang_april_bars()
    if change == "equal_benchmark_volume":
        bars[4] = replace(bars[4], volume=bars[2].volume)
    elif change == "prior_volume_not_exceptional":
        bars[3] = replace(bars[3], volume=70_000_000)
        bars[4] = replace(bars[4], volume=60_000_000)
    elif change == "large_prior_bearish":
        bars[3] = replace(bars[3], close=6.0)
    elif change == "no_earlier_bullish":
        bars[2] = replace(bars[2], open=bars[2].close)
    elif change == "short_bull":
        bars[4] = replace(bars[4], open=6.95)
    elif change == "equal_body_midpoint":
        bars[5] = replace(bars[5], open=(bars[4].open + bars[4].close) / 2)
    elif change == "bullish_second_day":
        bars[5] = replace(bars[5], close=bars[5].open)
    elif change == "equal_bearish_volume":
        bars[5] = replace(bars[5], volume=bars[0].volume)
    else:
        bars[0] = replace(bars[0], open=bars[0].close)
    decision = observe_volume_down_exit(bars, 5, StagedExitState())
    assert decision is None or decision["reason"] != "volume_bullish_gap_bearish_clear"
