"""A bearish outside reversal after a rising run clears the remaining position."""

import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest

from wavequant.application.analytics.backtest import run_portfolio
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar, Signal
from wavequant.domain.strategies.staged_exit import StagedExitState, observe_volume_down_exit


def xinhua_bars() -> list[Bar]:
    fixture = Path(__file__).parent / "fixtures/xinhuawenxuan_2026_trend.json"
    raw = json.loads(fixture.read_text(encoding="utf-8"))
    return [
        Bar(datetime.fromisoformat(day), raw["symbol"], *prices)
        for day, *prices in raw["bars"]
        if "2026-07-28" <= day <= "2026-08-05"
    ]


def test_xinhua_august_four_outside_reversal_clears_at_close():
    bars = xinhua_bars()
    assert [bar.timestamp.date().isoformat() for bar in bars] == [
        "2026-07-28", "2026-07-29", "2026-07-30", "2026-07-31",
        "2026-08-03", "2026-08-04", "2026-08-05",
    ]
    decision = observe_volume_down_exit(bars, 5, StagedExitState())
    assert decision is not None
    assert decision["reason"] == "volume_bearish_outside_clear"
    assert decision["bearish_reference_date"] == "2026-07-28"
    assert decision["bearish_reference_volume"] == 1_739_200
    assert decision["observed_volume"] == 2_556_580
    assert decision["exit_fraction"] == 1.0

    signal = Signal(
        bars[1].timestamp, bars[1].symbol, 1, "LONG", bars[1].close, 15,
        "fixture", bars[1].timestamp, 0, None, "fixture", 30,
    )
    config = StrategyConfig(
        initial_capital=100_000, risk_fraction=.2, max_position_weight=.8,
        max_participation=1, slippage_bps_per_side=0, entry_at_close=True,
        exit_on_target=False, volume_down_exit=True, max_hold_bars=100,
    )
    result = run_portfolio({bars[0].symbol: bars}, [signal], config)
    fills = [order for order in result.orders if order["status"] == "filled"]
    assert [(order["timestamp"][:10], order["reason"]) for order in fills] == [
        ("2026-07-29", "fixture"),
        ("2026-08-04", "volume_bearish_outside_clear"),
    ]
    assert fills[-1]["remaining_quantity"] == 0
    assert fills[-1]["price"] == bars[5].close
    assert fills[-1]["bearish_reference_date"] == "2026-07-28"
    assert fills[-1]["bearish_reference_volume"] == 1_739_200
    prefix = run_portfolio({bars[0].symbol: bars[:6]}, [signal], config)
    assert prefix.orders == [order for order in result.orders if order["timestamp"] <= bars[5].timestamp.isoformat()]


def test_xinhua_reversal_compares_volume_to_pre_run_bearish_day_not_previous_day():
    bars = xinhua_bars()
    bars[4] = replace(bars[4], volume=3_000_000)
    decision = observe_volume_down_exit(bars, 5, StagedExitState())
    assert decision is not None and decision["reason"] == "volume_bearish_outside_clear"
    assert decision["observed_volume"] < decision["previous_volume"]


@pytest.mark.parametrize("change", ["equal_reference_volume", "no_outside_high", "no_close_break", "broken_run"])
def test_xinhua_reversal_needs_volume_and_full_price_break(change):
    bars = xinhua_bars()
    if change == "equal_reference_volume":
        bars[5] = replace(bars[5], volume=bars[0].volume)
    elif change == "no_outside_high":
        bars[5] = replace(bars[5], high=bars[4].high - .01)
    elif change == "no_close_break":
        bars[5] = replace(bars[5], close=bars[4].low)
    else:
        bars[3] = replace(bars[3], open=bars[3].close)
    decision = observe_volume_down_exit(bars, 5, StagedExitState())
    assert decision is None or decision["reason"] != "volume_bearish_outside_clear"
