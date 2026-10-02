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
from wavequant.domain.strategies.wave_exhaustion_exit import observe_wave_exhaustion


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
    kwargs = dict(signals=[signal], config=config, wave_events={bars[0].symbol: events})
    full = run_portfolio({bars[0].symbol: bars}, **kwargs)
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

    # The same rule clears the residual after a filled target-shadow reduction.
    earlier = index - 3
    earlier_signal = replace(signal, timestamp=bars[earlier].timestamp, bar_index=earlier,
                             reference_price=bars[earlier].close)
    reduced = run_portfolio({bars[0].symbol: prefix_bars}, [earlier_signal], config,
                            wave_events={bars[0].symbol: prefix_events})
    buy, reduction, clear = [o for o in reduced.orders if o["status"] == "filled"]
    assert (reduction["timestamp"][:10], reduction["reason"]) == (
        "2025-11-14", "wave_target_upper_shadow_reduce")
    assert clear["reason"] == "wave_five_top_child_volume_clear"
    assert clear["quantity"] == reduction["remaining_quantity"] < buy["quantity"]
    assert clear["remaining_quantity"] == 0
    unrelated = replace(signal, trigger_timestamp=bars[earlier].timestamp)
    foreign = run_portfolio({bars[0].symbol: prefix_bars}, [unrelated], config,
                            wave_events={bars[0].symbol: prefix_events})
    assert [o["side"] for o in foreign.orders if o["status"] == "filled"] == ["BUY"]


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
    assert decision["reason"] == "wave_five_top_child_volume_clear"
