"""Body containment uses open/close, independently of full-candle containment."""

import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest

from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies import wave_exhaustion_exit as exits


REASON = "wave_five_top_body_upper_shadow_clear"


@pytest.fixture
def body_pair():
    bars = [
        Bar(datetime(2025, 4, 23), "sh.601086", 9, 10, 8, 10, 1_000_000),
        Bar(datetime(2025, 4, 24), "sh.601086", 15, 16, 9, 10, 1_000_000),
        Bar(datetime(2025, 4, 25), "sh.601086", 12, 16, 11, 12.5, 1),
    ]
    events = [dict(event="wave_projection_target_reached", attack=0, origin_index=0,
                   bar_index=1, reached_stage="five_top", reached_target=15)]
    return bars, events


def observe(bars, events):
    return exits.observe_five_top_body_upper_shadow_clear(bars, 2, events)


def test_global_full_exit_does_not_require_a_lower_open_or_bearish_child(body_pair):
    bars, events = body_pair
    config = StrategyConfig(wave_exhaustion_exit=True, exit_on_target=False)
    result = exits.observe_wave_exhaustion(bars, 2, events, config, entry_index=1)
    assert result is not None
    assert result["reason"] == REASON
    assert result["exit_fraction"] == 1.0
    assert bars[2].open > bars[1].close and bars[2].close > bars[2].open


@pytest.mark.parametrize("mother_bullish", [False, True])
@pytest.mark.parametrize("child_close", [11.5, 12, 12.5])
@pytest.mark.parametrize("stage", ["five_top", "ten_full"])
def test_body_contains_bearish_bullish_and_doji_children(body_pair, mother_bullish, child_close, stage):
    bars, events = body_pair
    if mother_bullish:
        bars[1] = replace(bars[1], open=10, close=15)
    bars[2] = replace(bars[2], close=child_close)
    decision = observe(bars, [dict(events[0], reached_stage=stage)])
    assert decision is not None
    assert decision["reason"] == REASON and decision["exit_fraction"] == 1.0
    assert decision["mother_body_low"] == 10 and decision["mother_body_high"] == 15
    assert decision["child_body_low"] == min(12, child_close)
    assert decision["child_body_high"] == max(12, child_close)


@pytest.mark.parametrize("opening,closing", [(10, 12), (12, 15), (10, 10), (15, 15)])
def test_one_body_boundary_may_be_equal_and_doji_is_included(body_pair, opening, closing):
    bars, events = body_pair
    bars[2] = replace(bars[2], open=opening, close=closing, high=21, low=9)
    assert observe(bars, events) is not None


@pytest.mark.parametrize("opening,closing", [(9.99, 12), (12, 15.01), (10, 15), (15, 10), (9, 16)])
def test_not_contained_or_identical_bodies_do_not_form_a_body_child(body_pair, opening, closing):
    bars, events = body_pair
    bars[2] = replace(bars[2], open=opening, close=closing, high=25, low=8)
    assert observe(bars, events) is None


def test_a_doji_mother_does_not_supply_a_larger_body(body_pair):
    bars, events = body_pair
    bars[1] = replace(bars[1], open=12, close=12)
    bars[2] = replace(bars[2], close=12)
    assert observe(bars, events) is None


@pytest.mark.parametrize("volume", [0, 1, 2_000_000])
def test_half_shadow_decimal_boundary_has_no_volume_gate(body_pair, volume):
    bars, events = body_pair
    bars[2] = replace(bars[2], open=11.3, high=12.3, low=10.3, close=11, volume=volume)
    decision = observe(bars, events)
    assert decision is not None and decision["wave_upper_shadow_fraction"] == 0.5
    bars[2] = replace(bars[2], high=12.299)
    assert observe(bars, events) is None


@pytest.mark.parametrize("change", ["one_p", "two_t", "same_day", "future", "invalidated", "past_invalidated", "future_invalidated"])
def test_body_clear_uses_only_prior_live_five_top(body_pair, change):
    bars, events = body_pair
    reached = events[0]
    if change in ("one_p", "two_t"):
        events = [dict(reached, reached_stage=change)]
    elif change in ("same_day", "future"):
        events = [dict(reached, bar_index=2 if change == "same_day" else 3)]
    else:
        invalidation = {"invalidated": 2, "past_invalidated": 1, "future_invalidated": 3}[change]
        events = [dict(reached, event="wave_projection_invalidated", bar_index=invalidation), reached]
    assert (observe(bars, events) is not None) == (change == "future_invalidated")


def test_real_guofang_body_is_contained_even_when_high_is_above_mother_high():
    source = json.loads((Path(__file__).parent / "fixtures/guofang_2025_five_top_low_open.json").read_text(encoding="utf-8"))
    selected = [row for row in source["bars"] if row["timestamp"][:10] in ("2025-04-23", "2025-04-24", "2025-04-25")]
    bars = [Bar(**dict(row, timestamp=datetime.fromisoformat(row["timestamp"]))) for row in selected]
    events = [dict(event="wave_projection_target_reached", attack=0, bar_index=1,
                   reached_stage="five_top", reached_target=19)]
    decision = observe(bars, events)
    assert decision is not None
    assert bars[2].high > bars[1].high
    assert decision["mother_date"] == "2025-04-24"
    assert decision["child_date"] == "2025-04-25"
    assert decision["mother_body_low"] == bars[1].open
    assert decision["mother_body_high"] == bars[1].close
    assert decision["child_body_low"] == decision["child_body_high"] == bars[2].open
    assert decision["wave_upper_shadow_fraction"] == 1.0
