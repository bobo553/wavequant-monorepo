"""买点筛选级别和真实空多交替可知区间。"""

from dataclasses import replace
from datetime import datetime, timedelta
from typing import cast

import pytest

from wavequant.domain.strategies.buy_intervals import confirmed_buy_interval_days, require_entry_days
from wavequant.domain.strategies.chart_entry_history import _confirmed_bear_flip_events
from wavequant.domain.strategies.hierarchical_entry import EntryContext
from wavequant.domain.strategies.integrated_strategy import SystemStrategy
from wavequant.domain.strategies.whole_wave_entry import select_wave_entry
from wavequant.domain.models.model import Bar


def context(level: int) -> EntryContext:
    return EntryContext(level, 0, 0, 0, 9., 1, 1, 10., 0, 5., 2, 2, 7.)


def test_selected_level_is_default_two_and_all_levels_are_supported() -> None:
    assert SystemStrategy().buy_interval_level == 2
    for level in (1, 2, 3):
        SystemStrategy(buy_interval_level=level).validate()


@pytest.mark.parametrize('level', (True, False, 0, 4, 2., '2', None))
def test_invalid_level_cannot_authorize_a_buy_window(level: object) -> None:
    with pytest.raises(ValueError):
        confirmed_buy_interval_days({}, 2, cast(int, level))


def test_interval_opens_only_when_known_and_closes_when_context_is_invalidated() -> None:
    second, third = context(2), context(3)
    history = {0: (), 1: (second,), 2: (second,), 3: (second, third), 4: (third,), 5: ()}
    assert confirmed_buy_interval_days(history, 6, 2) == (False, False, True, True, False, False)
    assert confirmed_buy_interval_days(history, 6, 3) == (False, False, False, True, True, False)
    assert confirmed_buy_interval_days(history, 6, 1) == (False,) * 6
    future = replace(second, alternation_index=5)
    assert not any(confirmed_buy_interval_days({2: (future,), 3: (future,)}, 4, 2))


@pytest.mark.parametrize('level', (1, 2, 3))
def test_wave_selection_uses_selected_level_including_first_level(level: int) -> None:
    bars = tuple(Bar(datetime(2026, 1, 1) + timedelta(days=index), 'LEVEL', 8., 10.,
                     5. if index == 0 else 7., 8., 100.) for index in range(6))
    contexts = tuple(context(value) for value in (1, 2, 3))
    proof, reason = select_wave_entry(contexts, contexts, bars=bars, attack=4, low_index=3,
        asof=5, deep_ratio=None, buy_interval_level=level)
    assert reason == ''
    assert proof is not None and proof['trend_level'] == level
    absent = tuple(value for value in contexts if value.trend_level != level)
    proof, _ = select_wave_entry(absent, contexts, bars=bars, attack=4, low_index=3,
        asof=5, deep_ratio=None, buy_interval_level=level)
    assert proof is None


def test_entry_window_axis_cannot_be_shorter_than_market_history() -> None:
    with pytest.raises(ValueError):
        require_entry_days((False, True), 3)


def test_confirmed_bear_flip_closes_retained_context_until_new_alternation() -> None:
    original = context(2)
    renewed = replace(original, flip_index=4, alternation_index=6)
    history = {index: (original,) for index in range(8)}
    history[6] = (original, renewed)
    history[7] = (renewed,)
    events = [dict(event='hierarchy_bear_flip', trend_level=2, bar_index=4, high_index=1)]
    assert confirmed_buy_interval_days(history, 8, 2, events=events) == (
        False, False, True, True, False, False, True, True)


def test_bear_flip_uses_known_day_and_only_closes_selected_level() -> None:
    history = {index: (context(1), context(2), context(3)) for index in range(7)}
    events = [dict(event='hierarchy_bear_flip', trend_level=2, bar_index=5, high_index=2)]
    assert confirmed_buy_interval_days(history, 7, 2, events=events) == (
        False, False, True, True, True, False, False)
    assert confirmed_buy_interval_days(history, 7, 1, events=events) == (
        False, False, True, True, True, True, True)
    assert confirmed_buy_interval_days(history, 4, 2, events=events) == (False, False, True, True)


def test_bear_flip_on_alternation_day_does_not_open_buy_window() -> None:
    history = {index: (context(2),) for index in range(4)}
    events = [dict(event='hierarchy_bear_flip', trend_level=2, bar_index=2)]
    assert not any(confirmed_buy_interval_days(history, 4, 2, events=events))


def test_bear_flip_reads_low_point_observation_context_clock() -> None:
    dates = {'2026-01-01': 0, '2026-01-02': 1, '2026-01-05': 2}
    point = dict(index=0, ordinal=1, kind='L', state='confirmed', available_at='2026-01-02',
        observations=[dict(title='翻多为空', available_at='2026-01-05')])
    assert not _confirmed_bear_flip_events({'level-2': (point,)}, trend_level=2, dates=dates, asof_index=1)
    flips = _confirmed_bear_flip_events({'level-2': (point,)}, trend_level=2, dates=dates, asof_index=2)
    assert flips == [dict(bar_index=2, event='hierarchy_bear_flip', trend_level=2, source_path='level-2',
                          pivot_index=0, pivot_ordinal=1, available_at='2026-01-05')]


@pytest.mark.parametrize('state,display_only', [('developing', False), ('confirmed', True)])
def test_display_tail_cannot_close_a_buy_window(state: str, display_only: bool) -> None:
    point = dict(index=0, ordinal=0, kind='L', state=state, display_only=display_only,
        available_at='2026-01-01', observations=[dict(title='翻多为空', available_at='2026-01-01')])
    assert not _confirmed_bear_flip_events({'level-2': (point,)}, trend_level=2,
                                           dates={'2026-01-01': 0}, asof_index=0)
