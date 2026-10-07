"""Bottom launch ownership, lifetime and prefix causality are independent of trading."""

from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from wavequant.domain.market_structure.bottom_n_targets import (
    DeclineStart, PositiveNCompletion, bottom_n_target_history,
)
from wavequant.domain.models.model import Bar


def bars(lows):
    return [Bar(datetime(2023, 1, 1) + timedelta(days=i), "TEST", low + 1, low + 2, low, low + 1.5, 100)
            for i, low in enumerate(lows)]


def test_first_floor_n_owns_all_following_local_and_cross_cycle_candidates():
    history = bars([10, 9, 8, 5, 6, 7, 8, 7, 9])
    declines = [DeclineStart(0, 2)]
    completions = [PositiveNCompletion(3, 5, 5), PositiveNCompletion(4, 6, 6), PositiveNCompletion(3, 8, 8)]
    result = bottom_n_target_history(history, declines, completions)
    assert result.qualifications[5].eligible
    assert result.qualifications[5].bottom_index == 3
    for index in (6, 8):
        assert not result.qualifications[index].eligible
        assert result.qualifications[index].source_attack == 5
    assert result.source_at == (None,) * 5 + (5,) * 4


def test_origin_above_real_floor_cannot_consume_the_first_launch():
    result = bottom_n_target_history(bars([10, 5, 6, 7, 8, 9]), [DeclineStart(0, 1)],
                                     [PositiveNCompletion(2, 4, 4), PositiveNCompletion(1, 5, 5)])
    assert result.qualifications[4].reason == "origin_is_not_decline_floor"
    assert result.qualifications[5].eligible


def test_equal_low_preserves_earliest_bottom_and_strict_break_allows_a_new_floor():
    result = bottom_n_target_history(bars([10, 5, 6, 7, 5, 4, 5, 6]), [DeclineStart(0, 1)],
                                     [PositiveNCompletion(1, 3, 3), PositiveNCompletion(5, 7, 7)])
    assert result.source_at[4] == 3
    assert result.source_at[5] is None
    assert [(e.attack, e.bar_index, e.reason) for e in result.retirements] == [(3, 5, "launch_origin_broken")]
    assert result.qualifications[7].eligible


def test_new_confirmed_decline_retires_old_source_only_when_known():
    history = bars([10, 5, 6, 7, 10, 8, 6, 7, 8])
    declines = [DeclineStart(0, 1), DeclineStart(4, 7)]
    completions = [PositiveNCompletion(1, 3, 3), PositiveNCompletion(6, 8, 8)]
    result = bottom_n_target_history(history, declines, completions)
    assert result.source_at[6] == 3
    assert result.source_at[7] is None
    assert result.qualifications[8].eligible
    assert result.retirements[0].bar_index == 7


def test_late_high_inside_launch_and_regressing_paths_cannot_reopen_the_box():
    history = bars([10, 5, 6, 7, 6, 8])
    result = bottom_n_target_history(history, [DeclineStart(0, 1), DeclineStart(2, 4), DeclineStart(0, 5)],
                                     [PositiveNCompletion(1, 3, 3), PositiveNCompletion(4, 5, 5)])
    assert not result.retirements
    assert result.qualifications[5].source_attack == 3


def test_future_high_or_n_cannot_change_any_prefix_or_borrow_a_new_bottom():
    history = bars([10, 5, 6, 7, 10, 8, 6, 7, 8])
    declines = [DeclineStart(0, 1), DeclineStart(4, 7)]
    completions = [PositiveNCompletion(1, 3, 3), PositiveNCompletion(6, 8, 8)]
    full = bottom_n_target_history(history, declines, completions)
    for end in range(len(history)):
        prefix = bottom_n_target_history(history[:end + 1], [s for s in declines if s.known_at <= end],
                                         [n for n in completions if n.known_at <= end])
        assert prefix.source_at == full.source_at[:end + 1]
        assert prefix.qualifications == {k: v for k, v in full.qualifications.items() if k <= end}
        assert prefix.retirements == tuple(e for e in full.retirements if e.bar_index <= end)
    changed = [*history[:-1], replace(history[-1], low=1, open=2, high=3, close=2)]
    assert bottom_n_target_history(changed, declines, completions).source_at[:-1] == full.source_at[:-1]


def test_missing_decline_and_revisited_equal_bottom_do_not_invent_a_source():
    history = bars([10, 5, 6, 5, 7])
    result = bottom_n_target_history(history, [], [PositiveNCompletion(1, 2, 2)])
    assert not result.qualifications[2].eligible
    result = bottom_n_target_history(history, [DeclineStart(0, 1)], [PositiveNCompletion(3, 4, 4)])
    assert not result.qualifications[4].eligible


@pytest.mark.parametrize("origins", [(0, 3, 4), (4, 3, 0), (3, 4, 0)])
def test_same_attack_selects_the_known_floor_before_local_or_cross_cycle_n(origins):
    history = bars([8, 10, 7, 5, 6, 7])
    completions = [PositiveNCompletion(origin, 5, 5) for origin in origins]
    result = bottom_n_target_history(history, [DeclineStart(1, 2)], completions)
    assert result.qualifications[5].eligible
    assert result.completions[5] == PositiveNCompletion(3, 5, 5)
    assert result.source_at[-1] == 5 and not result.retirements


def test_later_known_same_attack_candidate_does_not_rewrite_an_earlier_decision():
    history = bars([10, 5, 6, 7, 8, 9])
    first = PositiveNCompletion(2, 4, 4)
    late = PositiveNCompletion(1, 4, 5)
    full = bottom_n_target_history(history, [DeclineStart(0, 1)], [first, late])
    prefix = bottom_n_target_history(history[:5], [DeclineStart(0, 1)], [first])
    assert full.qualifications == prefix.qualifications
    assert full.completions == prefix.completions == {4: first}
    assert full.source_at == (None,) * 6


@pytest.mark.parametrize("declines, completions", [
    ([DeclineStart(1, 0)], []), ([], [PositiveNCompletion(1, 0, 1)]),
    ([], [PositiveNCompletion(0, 1, 3)]),
])
def test_unavailable_or_noncausal_inputs_fail(declines, completions):
    with pytest.raises(ValueError):
        bottom_n_target_history(bars([10, 5, 6]), declines, completions)


def test_post_b_exit_cannot_measure_an_ineligible_n_even_with_stale_prices():
    from wavequant.interfaces.research_tools.stock_backtest import _post_b_wave_exit_events

    history = bars([5, 6, 7, 8, 9, 10])
    local = dict(event="n_completed", direction="up", origin=1, bar_index=2, known_at=2,
                 defense=6, one_p=9, two_t=10, target_eligible=False)
    events = _post_b_wave_exit_events(history, [local], dict(wave_b_low_index=0, bar_index=1))
    assert not any(e["event"] == "wave_n_target_reached" for e in events)


def test_post_b_exit_observes_source_retirement_before_future_target_hits():
    from wavequant.interfaces.research_tools.stock_backtest import _post_b_wave_exit_events

    history = bars([5, 6, 7, 8, 9, 10])
    root = dict(event="n_completed", direction="up", origin=1, bar_index=2, known_at=2,
                defense=6, one_p=11, two_t=12, target_eligible=True)
    retirement = dict(event="n_target_source_retired", attack=2, bar_index=3)
    events = _post_b_wave_exit_events(history, [root, retirement], dict(wave_b_low_index=0, bar_index=1))
    assert not any(e["event"] == "wave_n_target_reached" for e in events)
    assert next(e for e in events if e["event"] == "wave_projection_invalidated")["bar_index"] == 3
