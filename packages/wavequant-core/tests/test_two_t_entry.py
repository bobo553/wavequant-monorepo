"""Causal entry confirmation at the original N's two-T target."""

from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.two_t_entry import TWO_T_ENTRY_OBSERVATION, two_t_entry_history


def target_sequence():
    start = datetime(2026, 6, 4)
    prices = [
        (4.2, 4.5, 4.15, 4.4), (4.8, 5.2, 4.38, 5.1),
        (6.33, 6.56, 6.19, 6.44), (6.58, 7.30, 6.54, 6.94),
        (6.87, 6.89, 6.32, 6.47), (6.6, 7.6, 6.5, 7.5),
        (7.0, 7.4, 6.9, 7.1),
    ]
    bars = [Bar(start + timedelta(days=i), "sz.300163", *row, 100) for i, row in enumerate(prices)]
    setup = dict(event="wave_continuation_ready", bar_index=2, origin_index=0,
                 attack_index=1, squeeze_index=2, two_t=7.3, defense=4.38)
    return bars, setup


@pytest.mark.parametrize("close", [6.94, 7.30])
def test_touch_or_equal_close_blocks_without_needing_half_range_shadow(close):
    bars, setup = target_sequence()
    bars[3] = replace(bars[3], close=close)
    risk = two_t_entry_history(bars, [setup])[3]
    assert risk["reason"] == TWO_T_ENTRY_OBSERVATION
    assert risk["wave_reached_price"] == 7.3
    assert risk["two_t_close_above"] is False
    assert risk["target_entry_trigger"] == "first_two_t_touch"


def test_clean_strong_close_above_target_allows_and_clean_next_session_retires():
    bars, setup = target_sequence()
    bars[3] = replace(bars[3], high=7.6, close=7.5)
    bars[4] = replace(bars[4], open=7.5, high=7.7, low=7.45, close=7.6)
    assert two_t_entry_history(bars, [setup]) == {}


@pytest.mark.parametrize("prices, pattern", [
    ((7.4, 7.6, 7.3, 7.5), None),
    ((7.4, 8.1, 7.3, 7.5), "long_upper_shadow"),
    ((7.6, 7.7, 7.3, 7.5), "higher_open_bearish_body"),
    ((6.4, 7.6, 6.3, 7.5), "direct_lower_open"),
])
def test_close_above_alone_cannot_bypass_weak_body_or_bearish_resistance(prices, pattern):
    bars, setup = target_sequence()
    bars[3] = replace(bars[3], open=prices[0], high=prices[1], low=prices[2], close=prices[3])
    risk = two_t_entry_history(bars, [setup])[3]
    assert risk["two_t_close_above"] is True
    if pattern is not None:
        assert pattern in risk["target_resistance_patterns"]
    else:
        assert risk["two_t_strong_body"] is False


def test_next_session_resistance_blocks_only_that_session_then_needs_strong_recovery():
    bars, setup = target_sequence()
    bars[3] = replace(bars[3], high=7.6, close=7.5)
    bars[4] = replace(bars[4], open=7.4, high=7.5, low=7.2, close=7.35)
    bars[5] = replace(bars[5], open=7.4, high=7.55, low=7.3, close=7.5)
    bars[6] = replace(bars[6], open=7.55, high=8.1, low=7.5, close=8.0)
    risks = two_t_entry_history(bars, [setup])
    assert set(risks) == {4, 5}
    assert risks[4]["target_entry_trigger"] == "next_two_t_session"
    assert risks[4]["target_resistance_patterns"] == ["direct_lower_open"]
    assert risks[5]["target_entry_trigger"] == "unconfirmed_two_t_retest"
    assert two_t_entry_history(bars[:4], [setup]) == {}


def test_failed_first_touch_blocks_next_session_and_retires_after_clean_strong_recovery():
    bars, setup = target_sequence()
    risks = two_t_entry_history(bars, [setup])
    assert set(risks) == {3, 4}
    # Recovery at index 5 cannot remove the already observed failures.
    assert two_t_entry_history(bars[:4], [setup]) == {3: risks[3]}
    assert two_t_entry_history(bars[:5], [setup]) == risks


def test_old_failed_target_does_not_block_new_wave_below_but_still_guards_retests():
    bars, setup = target_sequence()
    bars[5] = replace(bars[5], high=7.2, close=7.1)
    bars[6] = replace(bars[6], open=7.1, high=7.5, low=7.0, close=7.2)
    risks = two_t_entry_history(bars, [setup])
    assert set(risks) == {3, 4, 6}
    assert risks[6]["wave_reached_date"] == str(bars[3].timestamp.date())
    assert risks[6]["target_entry_trigger"] == "unconfirmed_two_t_retest"


def test_partial_current_bar_cannot_borrow_later_high_or_recovery():
    bars, setup = target_sequence()
    partial = bars[:4]
    partial[-1] = replace(partial[-1], high=7.2)
    assert two_t_entry_history(partial, [setup]) == {}
    assert 3 in two_t_entry_history(bars[:4], [setup])
    partial[-1] = replace(bars[3], high=7.6, close=7.5)
    assert two_t_entry_history(partial, [setup]) == {}


def test_late_known_target_cannot_backdate_touch_or_borrow_same_day_high():
    bars, setup = target_sequence()
    setup["bar_index"] = 4
    assert two_t_entry_history(bars[:4], [setup]) == {}
    assert two_t_entry_history(bars[:5], [setup]) == {}
    bars[5] = replace(bars[5], close=7.1)
    risks = two_t_entry_history(bars[:6], [setup])
    assert set(risks) == {5}
    assert risks[5]["target_known_date"] == str(bars[4].timestamp.date())
    setup["bar_index"] = 3
    assert two_t_entry_history(bars[:4], [setup]) == {}


@pytest.mark.parametrize("invalid_day", [2, 4])
def test_original_defense_failure_retires_pending_or_failed_target(invalid_day):
    bars, setup = target_sequence()
    bars[invalid_day] = replace(bars[invalid_day], low=4.37)
    risks = two_t_entry_history(bars, [setup])
    assert set(risks) == (set() if invalid_day == 2 else {3})


def test_equal_defense_holds_and_newer_farther_target_cannot_erase_original():
    bars, setup = target_sequence()
    bars[3] = replace(bars[3], low=4.38)
    newer = dict(setup, origin_index=1, attack_index=2, squeeze_index=3, bar_index=3, two_t=10.74)
    risks = two_t_entry_history(bars, [newer, setup, setup])
    assert risks[3]["wave_n_date"] == str(bars[1].timestamp.date())
    assert risks[3]["wave_reached_price"] == 7.3


@pytest.mark.parametrize("field,value", [
    ("bar_index", None), ("bar_index", True), ("bar_index", 99), ("origin_index", -1),
    ("attack_index", 3), ("two_t", float("nan")), ("two_t", True),
    ("defense", 7.3), ("defense", 0), ("squeeze_index", 9),
])
def test_unknown_future_or_invalid_setup_is_not_an_entry_barrier(field, value):
    bars, setup = target_sequence()
    setup[field] = value
    assert two_t_entry_history(bars, [setup]) == {}
