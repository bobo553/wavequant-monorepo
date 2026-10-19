"""直接下级的完整空头确认结束上级资格；疑虑及交替候选不能代替。"""

import pytest

from tests.test_trading_intervals import state
from tests.test_trend_lifecycle import scenario
from wavequant.domain.market_structure.source_bear_exit import SOURCE_BEAR_EVENT, SOURCE_BEAR_EXIT, source_bearish_exits
from wavequant.domain.market_structure.trend_lifecycle import TrendLifecycle, lifecycle_interval_days
from wavequant.domain.strategies.eligible_polyline_n import EligiblePolylineN
from wavequant.domain.strategies.recovery_entry_history import merge_recovery_entry_history
from wavequant.domain.strategies.trading_intervals import TradingEligibility, confirmed_trading_intervals, interval_days


def bearish(now=5, level=1, path='lower'):
    return dict(event='trend_lifecycle_state', bar_index=now, available_index=now,
        trend_level=level, source_path=path, direction='down', state='转空确认',
        flip_confirmed=True, origin_index=0, origin_price=21, cycle_started_index=2,
        flip_index=2, flip_known_index=2, breakout_level=17, counter_index=3, counter_price=19,
        alternation_index=4, confirmation_close=16, previous_close=17.5,
        confirmation_rule='strict_close_cross_after_confirmed_alternation')


def parent(level):
    return dict(trend_level=level, strokes=[dict(id='upper', source_path='lower', points=[])],
                _path_market_end_indices={'lower': None})


def opening(level):
    return dict(state(4, '空多交替', level=level, path='upper'),
        direction='up', origin_index=0, origin_price=9, cycle_started_index=2,
        breakout_level=13, counter_price=11, confirmation_rule='positive_n_and_squeeze_after_qualified_b',
        confirmation_evidence=dict(key_index=0, key_price=12, attack=3))


@pytest.mark.parametrize('level', [1, 2, 3])
def test_direct_source_bear_confirmation_closes_today_without_waiting_for_parent_bear(level):
    opened = opening(level)
    exits = source_bearish_exits([parent(level)], [bearish(level=level - 1)], 5)
    assert len(exits) == 1 and exits[0]['trend_level'] == level
    gate = TradingEligibility(level)
    assert gate.observe(opened) is True
    snapshot = gate.intervals
    assert gate.observe(exits[0]) is False
    assert gate.observe(exits[0]) is None
    assert snapshot[0].end_index is None
    closed = gate.intervals[0]
    assert (closed.end_index, closed.end_reason, closed.end_key_index) == (5, SOURCE_BEAR_EXIT, 2)
    assert closed.end_key_price == 17
    assert confirmed_trading_intervals([opened, *exits], 7, level) == gate.intervals
    assert interval_days(gate.intervals, 7) == (False, False, False, False, True, False, False)
    assert interval_days(gate.intervals, 7, include_end=True)[5]
    assert lifecycle_interval_days([opened, *exits], 7, level, up=True) == interval_days(gate.intervals, 7)
    # 结束后出现新的本级确认才另开窗口，不修改先前区间。
    assert gate.observe(state(7, '空多交替', level=level, path='upper')) is True
    assert [(window.start_index, window.end_index) for window in gate.intervals] == [(4, 5), (7, None)]


@pytest.mark.parametrize('change', [
    {'state': '多翻空'}, {'state': '多空交替'}, {'event': 'trend_lifecycle_candidate'},
    {'flip_confirmed': False}, {'direction': 'up'}, {'flip_known_index': 6},
    {'alternation_index': None}, {'alternation_index': 5}, {'counter_index': 2},
    {'confirmation_close': 17}, {'previous_close': 16.9}, {'bar_index': 4},
    {'available_index': 6}, {'source_path': 'unrelated'}, {'trend_level': 0},
])
def test_missing_confirmation_wrong_path_and_nonadjacent_level_do_not_close(change):
    event = dict(bearish(), **change)
    assert source_bearish_exits([parent(2)], [event], 5) == []


def test_closed_source_or_missing_lineage_does_not_close_other_windows():
    row = parent(2)
    row['_path_market_end_indices']['lower'] = 5
    assert source_bearish_exits([row], [bearish()], 5) == []
    row = parent(2)
    row['strokes'][0].pop('source_path')
    assert source_bearish_exits([row], [bearish()], 5) == []
    gate = TradingEligibility(2)
    gate.observe(opening(2))
    gate.observe(state(4, '空多交替', path='unrelated'))
    event = source_bearish_exits([parent(2)], [bearish()], 5)[0]
    gate.observe(event)
    assert [window.source_path for window in gate.active_intervals] == ['unrelated']
    invalid = dict(event, direct_source_path='wrong')
    gate = TradingEligibility(2)
    gate.observe(opening(2))
    assert gate.observe(invalid) is None and gate.active


@pytest.mark.parametrize('level', [1, 2, 3])
@pytest.mark.parametrize('restore_at', [None, 5])
def test_separate_and_combined_lifecycle_replay_publish_identical_qualification_exits(level, restore_at):
    bars, lower = scenario(level - 1, up=False)
    lower['strokes'][0]['id'] = 'lower'
    upper = parent(level)
    dates = {bar.timestamp.date().isoformat(): i for i, bar in enumerate(bars)}
    combined, child, owner = TrendLifecycle(), TrendLifecycle(), TrendLifecycle()
    combined_exits, separate_exits = [], []
    for now in range(len(bars)):
        if now == restore_at:
            combined = TrendLifecycle.from_checkpoint(combined.checkpoint())
            child = TrendLifecycle.from_checkpoint(child.checkpoint())
            owner = TrendLifecycle.from_checkpoint(owner.checkpoint())
        whole = combined.advance([lower, upper], bars, now, dates)
        source = child.advance([lower], bars, now, dates)
        target = owner.advance([upper], bars, now, dates, source_events=source)
        combined_exits.extend(event for event in whole if event['event'] == SOURCE_BEAR_EVENT)
        separate_exits.extend(event for event in target if event['event'] == SOURCE_BEAR_EVENT)
    assert combined_exits == separate_exits
    assert len(combined_exits) == 1 and combined_exits[0]['bar_index'] == 5


def test_ending_permission_removes_n_sources_and_entry_context_on_the_same_day():
    bars, _ = scenario(1, up=False)
    opened = opening(2)
    close = source_bearish_exits([parent(2)], [bearish()], 5)[0]
    events = [opened, close]
    observer = EligiblePolylineN(2)
    for now in range(len(bars)):
        observer.advance(now, (), [event for event in events if event['bar_index'] == now])
        assert observer.eligibility.active is (now == 4)
    history = merge_recovery_entry_history(bars, {}, events)
    assert len(history[4]) == 1 and history[5] == ()


def test_guofang_requested_boundary_is_a_confirmation_date_not_a_retroactive_endpoint():
    # 用户报告日期的缩减事件夹具；不是实际行情全量六态计算的验证。
    days = ['2019-11-18', '2020-03-20', '2020-04-28', '2020-06-01',
            '2020-08-14', '2020-08-27', '2020-09-03', '2020-09-04', '2020-12-22']
    opened = dict(opening(2), bar_index=3, alternation_index=3, flip_index=1,
                  flip_known_index=1, counter_index=2)
    proof = dict(bearish(7), origin_index=4, cycle_started_index=5, flip_index=5,
                 flip_known_index=5, counter_index=6, alternation_index=6,
                 breakout_level=5.8, previous_close=5.85, confirmation_close=5.73)
    exits = source_bearish_exits([parent(2)], [proof], 7)
    assert len(exits) == 1
    assert confirmed_trading_intervals([opened, *exits], 7, 2)[0].end_index is None
    windows = confirmed_trading_intervals([opened, *exits], len(days), 2)
    assert days[windows[0].start_low_index] == '2020-04-28'
    assert days[windows[0].end_index] == '2020-09-04'
    assert not interval_days(windows, len(days))[7]


def test_live_market_day_closes_permission_before_publishing_strategy_inputs():
    from tests.test_market_calculations import _calculation_case, _case

    with _calculation_case(up=False) as (calculations, _calls, bars):
        calculations.calculate_polyline()
        _, points = _case(up=False)
        lower = dict(trend_level=1, strokes=[dict(id='lower', source_path='polyline', points=points)])
        upper = parent(2)
        for now in range(len(bars)):
            source = calculations._advance_lifecycle(1, lower, now)
            if now == 4:
                calculations._eligibility[2].observe(opening(2))
            target = calculations._advance_lifecycle(2, upper, now, source_events=source)
            assert calculations._eligibility[2].active is (now == 4)
            if now == 5:
                exit_event = next(event for event in target if event['event'] == SOURCE_BEAR_EVENT)
                assert exit_event['buy_eligible'] is False
        calculations._seal_lifecycle(2)
        windows = calculations.trading_intervals(2)
        assert windows[0].end_index == 5
        audit = [opening(2), *calculations.history_events(2)]
        assert confirmed_trading_intervals(audit, len(bars), 2) == windows
