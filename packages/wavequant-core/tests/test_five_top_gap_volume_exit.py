"""A known five-top plus a bearish gap and prior-bearish volume clears holdings."""

from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path

import pytest

from wavequant.application.analytics.backtest import run_portfolio
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar, Signal
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals
from wavequant.domain.strategies.strategy_profiles import whole_wave_profile
from wavequant.domain.strategies.wave_exhaustion_exit import observe_five_top_gap_volume_clear


@pytest.fixture(scope="module")
def sample():
    raw = json.loads((Path(__file__).parent / "fixtures/guofang_2026_consolidation.json").read_text(encoding="utf-8"))
    bars = [Bar(datetime.fromisoformat(day), raw["symbol"], *values) for day, *values in raw["bars"]]
    dates = {str(bar.timestamp.date()): i for i, bar in enumerate(bars)}
    profile = whole_wave_profile({"scenarios": {"base": {"execution": {}}}})
    return bars, dates, SystemStrategy(**profile["strategy"])


def test_guofang_five_top_gap_clears_at_september_15_close(sample):
    bars, dates, strategy = sample
    index = dates["2026-09-15"]
    result = generate_system_signals(bars[:index + 1], strategy)
    reason = "wave_five_top_gap_volume_clear"
    exit_signal = next(signal for signal in result.signals if signal.bar_index == index and signal.side == "EXIT")
    assert reason in exit_signal.reason.split("|")
    evidence = next(event for event in result.audit if event["event"] == "exit_signal" and event["bar_index"] == index)
    assert evidence["wave_reached_date"] == "2026-09-14"
    assert (evidence["previous_open"], evidence["previous_close"]) == (bars[index - 1].open, bars[index - 1].close)
    assert (evidence["bearish_reference_date"], evidence["bearish_reference_volume"]) == (
        "2026-08-26", 7_902_800)
    assert evidence["observed_volume"] == 52_774_200

    entry = index - 1
    signal = Signal(bars[entry].timestamp, bars[entry].symbol, entry, "LONG", bars[entry].close,
                    1.0, "fixture", bars[entry].timestamp, 0, None, "fixture", 100.0)
    config = StrategyConfig(entry_at_close=True, exit_on_target=False, max_hold_bars=200,
                            max_participation=1, slippage_bps_per_side=0)
    portfolio = run_portfolio({bars[0].symbol: bars[:index + 1]}, [signal, exit_signal], config,
                              wave_events={bars[0].symbol: [event for event in result.audit
                                                            if event["event"].startswith("wave_projection_")]})
    buy, clear = [order for order in portfolio.orders if order["status"] == "filled"]
    assert (clear["timestamp"][:10], clear["reason"], clear["execution_model"]) == (
        "2026-09-15", reason, "same_day_close")
    assert clear["quantity"] == buy["quantity"]
    assert clear["remaining_quantity"] == 0


@pytest.mark.parametrize("change", ["equal_open", "equal_close", "equal_volume", "no_bearish",
                                     "same_day_five_top", "invalidated", "only_two_t"])
def test_five_top_gap_clear_requires_every_condition(sample, change):
    original, dates, _ = sample
    index = dates["2026-09-15"]
    bars = list(original[:index + 1])
    reached = dict(event="wave_projection_target_reached", attack=dates["2024-10-14"],
                   bar_index=index - 1, reached_stage="five_top", reached_target=23.6)
    events = [reached]
    if change == "equal_open":
        bars[index] = replace(bars[index], open=bars[index - 1].close, high=bars[index - 1].close)
    elif change == "equal_close":
        bars[index] = replace(bars[index], close=bars[index - 1].open)
    elif change == "equal_volume":
        bars[index] = replace(bars[index], volume=7_902_800)
    elif change == "no_bearish":
        bars[:index] = [replace(bar, open=bar.close) for bar in bars[:index]]
    elif change == "same_day_five_top":
        events = [dict(reached, bar_index=index)]
    elif change == "invalidated":
        events.append(dict(reached, event="wave_projection_invalidated", bar_index=index))
    else:
        events = [dict(reached, reached_stage="two_t")]
    assert observe_five_top_gap_volume_clear(bars, index, events) is None


def test_five_top_gap_volume_clear_accepts_bullish_body_when_price_conditions_hold(sample):
    original, dates, _ = sample
    index = dates["2026-09-15"]
    bars = list(original[:index + 1])
    bars[index] = replace(bars[index], open=22.0, low=21.9, close=22.5)
    reached = dict(event="wave_projection_target_reached", attack=dates["2024-10-14"],
                   bar_index=index - 1, reached_stage="five_top", reached_target=23.6)
    assert observe_five_top_gap_volume_clear(bars, index, [reached]) is not None
