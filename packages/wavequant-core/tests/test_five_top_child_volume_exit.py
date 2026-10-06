"""Five-top holdings clear on a volume-confirmed break of the prior child."""

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
from wavequant.domain.strategies.wave_exhaustion_exit import (
    observe_five_top_child_volume_clear, observe_wave_exhaustion,
)
from wavequant.interfaces.charts.visualization import ChartRepository


@pytest.fixture(scope="module")
def sample():
    raw = json.loads((Path(__file__).parent / "fixtures/xianfeng_2025_split_n.json").read_text(encoding="utf-8"))
    bars = [Bar(datetime.fromisoformat(day), raw["symbol"], *values) for day, *values in raw["bars"]]
    dates = {str(bar.timestamp.date()): i for i, bar in enumerate(bars)}
    profile = whole_wave_profile({"scenarios": {"base": {"execution": {}}}})
    result = generate_system_signals(bars, SystemStrategy(**profile["strategy"]))
    events = [e for e in result.audit if e["event"].startswith("wave_projection_")
              and e.get("attack") == dates["2025-09-29"]]
    return bars, events, dates


def test_real_five_top_child_break_clears_remaining_at_same_close(sample):
    bars, events, dates = sample
    index = dates["2025-11-18"]
    for reduced in (False, True):
        decision = observe_wave_exhaustion(bars, index, events, StrategyConfig(), reduced=reduced)
        assert decision is not None
        assert decision["reason"] == "wave_five_top_child_volume_clear"
        assert decision["exit_fraction"] == 1.0
        assert decision["wave_reached_stage"] == "five_top"
        assert (decision["wave_reached_date"], decision["wave_reached_price"]) == ("2025-10-28", 4.93)
        assert (decision["mother_date"], decision["child_date"], decision["child_low"]) == (
            "2025-11-13", "2025-11-14", 4.91)
        assert (decision["previous_low"], decision["previous_close"]) == (4.99, 5.19)
        assert (decision["bearish_reference_date"], decision["bearish_reference_volume"]) == (
            "2025-11-11", 63_172_500)
        assert decision["observed_volume"] == 63_288_181 < decision["previous_volume"]

    # Hold the parent N from the preceding session to isolate this exit's fill.
    entry = index - 1
    signal = Signal(bars[entry].timestamp, bars[entry].symbol, entry, "LONG", bars[entry].close,
                    3.29, "fixture", bars[dates["2025-09-29"]].timestamp, 0, None, "fixture", 10.0)
    config = StrategyConfig(entry_at_close=True, wave_exhaustion_exit=True, exit_on_target=False,
                            max_hold_bars=200, max_participation=1, slippage_bps_per_side=0)
    full = run_portfolio({bars[0].symbol: bars}, [signal], config,
                         wave_events={bars[0].symbol: events})
    buy, clear = [o for o in full.orders if o["status"] == "filled"]
    assert (clear["timestamp"][:10], clear["reason"], clear["price"]) == (
        "2025-11-18", "wave_five_top_child_volume_clear", 4.91)
    assert clear["quantity"] == buy["quantity"]
    assert clear["remaining_quantity"] == 0
    assert clear["execution_model"] == "same_day_close"
    prefix_bars = bars[:index + 1]
    profile = whole_wave_profile({"scenarios": {"base": {"execution": {}}}})
    prefix_result = generate_system_signals(prefix_bars, SystemStrategy(**profile["strategy"]))
    prefix_events = [e for e in prefix_result.audit if e["event"].startswith("wave_projection_")
                     and e.get("attack") == dates["2025-09-29"]]
    assert prefix_events == [e for e in events if e["bar_index"] <= index]
    prefix = run_portfolio({bars[0].symbol: prefix_bars}, [signal], config,
                           wave_events={bars[0].symbol: prefix_events})
    assert prefix.orders == full.orders

    # The independent body-child risk now clears this earlier holding before
    # the later volume-confirmed child break can sell it again.
    earlier = index - 3
    earlier_signal = replace(signal, timestamp=bars[earlier].timestamp, bar_index=earlier,
                             reference_price=bars[earlier].close)
    earlier_result = run_portfolio({bars[0].symbol: prefix_bars}, [earlier_signal], config,
                                   wave_events={bars[0].symbol: prefix_events})
    buy, clear = [o for o in earlier_result.orders if o["status"] == "filled"]
    assert (clear["timestamp"][:10], clear["reason"]) == (
        "2025-11-14", "wave_five_top_body_upper_shadow_clear")
    assert (clear["mother_body_low"], clear["mother_body_high"]) == (4.84, 5.0)
    assert (clear["child_body_low"], clear["child_body_high"]) == (4.91, 4.96)
    assert clear["wave_upper_shadow_fraction"] == pytest.approx(7 / 12)
    assert clear["quantity"] == buy["quantity"]
    assert clear["remaining_quantity"] == 0
    assert not any(o["side"] == "SELL" and o["timestamp"][:10] == "2025-11-18" for o in earlier_result.orders)
    unrelated = replace(signal, trigger_timestamp=bars[earlier].timestamp)
    foreign = run_portfolio({bars[0].symbol: prefix_bars}, [unrelated], config,
                            wave_events={bars[0].symbol: prefix_events})
    assert [o["side"] for o in foreign.orders if o["status"] == "filled"] == ["BUY"]


def test_real_five_top_generates_november_exit_signal_and_close_fill(sample):
    bars, _, dates = sample
    profile = whole_wave_profile({"scenarios": {"base": {"execution": {}}}})
    result = generate_system_signals(bars, SystemStrategy(**profile["strategy"]))
    index = dates["2025-11-18"]
    exit_signal = next(s for s in result.signals if s.bar_index == index and s.side == "EXIT")
    assert exit_signal.reason == "wave_five_top_child_volume_clear"
    assert not any("wave_five_top_child_volume_clear" in s.reason for s in result.signals
                   if s.bar_index < index)
    exit_event = next(e for e in result.audit if e["event"] == "exit_signal" and e["bar_index"] == index)
    assert (exit_event["wave_n_date"], exit_event["wave_reached_date"], exit_event["child_date"]) == (
        "2025-09-29", "2025-10-28", "2025-11-14")
    chart = ChartRepository.render_theory(
        object.__new__(ChartRepository), bars[:index + 1], SystemStrategy(**profile["strategy"]), result, "2025-11-18",
        geometry={"tertiary_trends": {}},
    )
    chart_exit = next(e for e in chart["events"] if e["event"] == "exit_signal" and e["bar_index"] == index)
    assert (chart_exit["time"], chart_exit["reason"]) == (
        "2025-11-18", "wave_five_top_child_volume_clear")
    prefix = generate_system_signals(bars[:index + 1], SystemStrategy(**profile["strategy"]))
    assert prefix.signals == [s for s in result.signals if s.bar_index <= index]

    entry_index = dates["2025-11-17"]
    entry = Signal(bars[entry_index].timestamp, bars[entry_index].symbol, entry_index,
                   "LONG", bars[entry_index].close, 3.29, "fixture",
                   bars[entry_index].timestamp, 0, None, "fixture", 10.0)
    config = StrategyConfig(entry_at_close=True, wave_exhaustion_exit=True, exit_on_target=False,
                            max_hold_bars=200, max_participation=1, slippage_bps_per_side=0)
    portfolio = run_portfolio({bars[0].symbol: bars[:index + 1]},
                              [entry, *[s for s in result.signals if s.bar_index <= index]], config)
    buy, clear = [order for order in portfolio.orders if order["status"] == "filled"]
    assert (clear["timestamp"][:10], clear["reason"], clear["price"]) == (
        "2025-11-18", "wave_five_top_child_volume_clear", 4.91)
    assert clear["quantity"] == buy["quantity"]
    assert clear["remaining_quantity"] == 0
    assert clear["execution_model"] == "same_day_close"
    with_events = run_portfolio(
        {bars[0].symbol: bars[:index + 1]},
        [entry, *[s for s in result.signals if s.bar_index <= index]], config,
        wave_events={bars[0].symbol: [e for e in result.audit
                                     if e["event"].startswith("wave_projection_") and e["bar_index"] <= index]},
    )
    explained_clear = next(order for order in with_events.orders
                           if order["side"] == "SELL" and order["status"] == "filled")
    assert (explained_clear["wave_n_date"], explained_clear["wave_reached_date"],
            explained_clear["child_date"], explained_clear["bearish_reference_volume"]) == (
        "2025-09-29", "2025-10-28", "2025-11-14", 63_172_500)


def test_global_exit_requires_a_prior_live_five_top(sample):
    bars, _, dates = sample
    index = dates["2025-11-18"]
    reached = dict(event="wave_projection_target_reached", attack=dates["2025-09-29"],
                   origin_index=dates["2025-09-23"], bar_index=index - 1,
                   reached_stage="five_top", reached_target=4.93)
    assert observe_five_top_child_volume_clear(bars, index, [reached]) is not None
    assert observe_five_top_child_volume_clear(bars, index, [dict(reached, bar_index=index)]) is None
    assert observe_five_top_child_volume_clear(bars, index,
        [reached, dict(reached, event="wave_projection_invalidated", bar_index=index)]) is None
    assert observe_five_top_child_volume_clear(bars, index,
        [dict(reached, reached_stage="two_t")]) is None


@pytest.mark.parametrize("change", ["equal_child_low", "equal_previous_low", "equal_previous_close",
                                    "equal_bearish_volume", "zero_bearish_volume", "no_bearish",
                                    "not_child", "identical_pair", "doji_mother", "bullish"])
def test_clear_requires_every_strict_condition(sample, change):
    original, events, dates = sample
    bars = list(original)
    i = dates["2025-11-18"]
    if change == "equal_child_low":
        bars[i] = replace(bars[i], low=bars[i - 2].low)
    elif change == "equal_previous_low":
        bars[i] = replace(bars[i], low=bars[i - 1].low, close=5.0)
    elif change == "equal_previous_close":
        bars[i] = replace(bars[i], open=5.25, high=5.3, close=bars[i - 1].close)
    elif change in ("equal_bearish_volume", "zero_bearish_volume"):
        reference = dates["2025-11-11"]
        if change == "equal_bearish_volume":
            bars[i] = replace(bars[i], volume=bars[reference].volume)
        else:
            bars[reference] = replace(bars[reference], volume=0)
    elif change == "no_bearish":
        bars[:i] = [replace(b, open=b.close) for b in bars[:i]]
    elif change == "not_child":
        bars[i - 2] = replace(bars[i - 2], high=5.04)
    elif change == "identical_pair":
        bars[i - 2] = replace(bars[i - 2], low=bars[i - 3].low)
    elif change == "doji_mother":
        bars[i - 3] = replace(bars[i - 3], open=bars[i - 3].close)
    else:
        bars[i] = replace(bars[i], open=4.90)
    # Disable other large-body exits to observe only this conjunction.
    decision = observe_wave_exhaustion(bars, i, events, StrategyConfig(wave_engulf_min_body=0.5),
                                       reduced=True, entry_index=i - 1)
    assert decision is None


@pytest.mark.parametrize("stage", ["one_p", "two_t", "five_top", "ten_full"])
def test_milestone_is_owned_known_and_at_least_five_top(sample, stage):
    bars, _, dates = sample
    i = dates["2025-11-18"]
    event = dict(event="wave_projection_target_reached", attack=dates["2025-09-29"],
                 bar_index=i - 1, reached_stage=stage, reached_target=4.93)
    config = StrategyConfig(wave_engulf_min_body=0.5)
    decision = observe_wave_exhaustion(bars, i, [event], config, reduced=True)
    assert (decision is not None) == (stage in ("five_top", "ten_full"))
    same_day = observe_wave_exhaustion(bars, i, [dict(event, bar_index=i)], config, reduced=True)
    assert same_day is None or same_day["reason"] != "wave_five_top_child_volume_clear"
    assert observe_wave_exhaustion(bars, i, [dict(event, bar_index=i + 1)], config, reduced=True) is None
    invalidated = dict(event="wave_projection_invalidated", attack=event["attack"], bar_index=i)
    assert observe_wave_exhaustion(bars, i, [event, invalidated], config, reduced=True) is None


def test_child_close_and_color_are_not_extra_clear_conditions(sample):
    original, events, dates = sample
    bars = list(original)
    i = dates["2025-11-18"]
    bars[i - 2] = replace(bars[i - 2], open=5.0, close=4.92, volume=40_000_000)
    bars[i] = replace(bars[i], close=4.95)
    decision = observe_wave_exhaustion(bars, i, events, StrategyConfig(), reduced=True)
    assert decision is not None
    assert decision["reason"] == "wave_five_top_child_volume_clear"
    assert decision["observed_close"] > decision["child_close"]
    assert decision["bearish_reference_date"] == "2025-11-14"
    assert decision["bearish_reference_volume"] == 40_000_000


def test_equal_low_child_with_strict_high_containment_is_allowed(sample):
    original, events, dates = sample
    bars = list(original)
    i = dates["2025-11-18"]
    bars[i - 2] = replace(bars[i - 2], low=bars[i - 3].low, high=5.02)
    bars[i] = replace(bars[i], low=4.65)
    decision = observe_wave_exhaustion(bars, i, events, StrategyConfig(), reduced=True)
    assert decision is not None
    assert decision["reason"] == "wave_five_top_child_volume_clear"
