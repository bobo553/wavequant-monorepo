"""短暂收盘跌破后的同级再次空翻多；缩减夹具，不代替实股完整回放。"""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from wavequant.domain.market_structure.trend_lifecycle import TrendLifecycle, _facts
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.trading_intervals import confirmed_trading_intervals, interval_days


def recovery_case(level=2):
    values = [
        (4.2, 4.4, 4.05, 4.2), (5.5, 5.95, 5.4, 5.51),
        (4.03, 4.1, 3.90, 4.00), (4.01, 4.1, 3.92, 4.01),
        (4.02, 4.1, 3.94, 4.02), (4.2, 4.65, 4.1, 4.54),
        (4.3, 4.5, 4.2, 4.3), (3.99, 4.09, 3.96, 4.07),
        (4.2, 4.5, 4.1, 4.3), (5.51, 5.78, 5.32, 5.32),
        (5.1, 5.4, 5.0, 5.2), (4.9, 5.0, 4.8, 4.9),
        (5.0, 5.4, 4.9, 5.2),
    ]
    bars = [Bar(datetime(2019, 1, 1) + timedelta(days=i), 'sh.600812', *row, 100)
            for i, row in enumerate(values)]

    def point(index, kind, price, known):
        return dict(index=index, ordinal=0, kind=kind, value=price,
                    available_at=known, state='confirmed')

    origin = point(0, 'L', 4.05, 0)
    initial = point(1, 'H', 5.95, 1)
    initial['observations'] = [dict(title='翻空为多', available_at=1, confirmed_low=origin)]
    points = [origin, initial, point(2, 'L', 3.90, 3), point(5, 'H', 4.65, 6),
              point(7, 'L', 3.96, 8), point(9, 'H', 5.78, 10), point(11, 'L', 4.8, 12)]
    return bars, dict(trend_level=level, strokes=[dict(id='path', points=points)])


def replay(bars, row, begin=0, end=None, engine=None):
    engine = engine or TrendLifecycle()
    events = []
    for now in range(begin, len(bars) if end is None else end):
        events.extend(engine.advance([row], bars, now, {}))
    return engine, events


def special_states(events):
    return [event for event in events if event['event'] == 'trend_lifecycle_state'
            and event.get('special_bull_recovery')]


@pytest.mark.parametrize('level', [0, 1, 2, 3])
def test_all_levels_recover_then_apply_existing_shallow_counter_rule(level):
    bars, row = recovery_case(level)
    _, events = replay(bars, row)
    states = special_states(events)
    assert [(event['bar_index'], event['state']) for event in states] == [(10, '空翻多'), (12, '空多交替')]
    assert states[0]['origin_index'] == 7
    assert states[0]['broken_high_index'] == 5
    assert states[0]['recovered_index'] == 5
    assert states[1]['counter_index'] == 11
    if level:
        intervals = confirmed_trading_intervals(events, len(bars), level)
        assert len(intervals) == 1
        assert (intervals[0].start_low_index, intervals[0].start_index) == (11, 12)
        assert interval_days(intervals, len(bars)) == (False,) * 12 + (True,)


@pytest.mark.parametrize('below_days', [0, 1, 2, 3])
def test_zero_to_three_consecutive_closes_are_allowed(below_days):
    bars, row = recovery_case()
    for index in range(2 + below_days, 5):
        bars[index] = replace(bars[index], close=4.08)
    _, events = replay(bars, row)
    assert len(special_states(events)) == 2


def test_fourth_close_rejects_recovery_even_if_later_price_recovers():
    bars, row = recovery_case()
    bars[5] = replace(bars[5], close=4.02, low=4.0)
    _, events = replay(bars, row)
    assert not special_states(events)
    assert any(event.get('reason') == 'four_consecutive_closes_below_original_low' for event in events)


def test_intraday_break_can_recover_on_the_same_day_but_equal_close_cannot():
    bars, row = recovery_case()
    for index in range(2, 5):
        bars[index] = replace(bars[index], close=4.08)
    _, events = replay(bars, row)
    assert len(special_states(events)) == 2
    recovery = next(event for event in events if event['event'] == 'trend_lifecycle_recovered')
    assert recovery['breach_index'] == recovery['recovered_index'] == 2
    assert recovery['consecutive_below_closes'] == 0
    bars, row = recovery_case()
    for index in range(5, len(bars)):
        bars[index] = replace(bars[index], close=4.05, low=min(bars[index].low, 4.05))
    _, events = replay(bars, row)
    assert not special_states(events)


def test_touching_same_level_high_is_not_a_new_flip():
    bars, row = recovery_case()
    row['strokes'][0]['points'][-2]['value'] = 4.65
    _, events = replay(bars, row)
    assert not special_states(events)


def test_new_origin_breach_before_or_after_breakout_confirmation_rejects_alternation():
    for index in (8, 11):
        bars, row = recovery_case()
        bars[index] = replace(bars[index], low=3.95)
        _, events = replay(bars, row)
        assert not any(event['state'] == '空多交替' for event in special_states(events))


@pytest.mark.parametrize('checkpoint_at', [4, 6, 11, 12])
def test_checkpoint_preserves_short_break_and_special_flip_without_duplicate_events(checkpoint_at):
    bars, row = recovery_case()
    book, before = replay(bars, row, end=checkpoint_at)
    checkpoint = book.checkpoint()
    for restored in (book.copy(), TrendLifecycle.from_checkpoint(checkpoint)):
        _, after = replay(bars, row, begin=checkpoint_at, engine=restored)
        _, whole = replay(bars, row)
        assert before + after == whole
    assert book.checkpoint() == checkpoint


def test_source_revision_removes_pending_recovery_and_special_structure():
    bars, row = recovery_case()
    book, _ = replay(bars, row, end=6)
    revised = deepcopy(row)
    revised['strokes'][0]['points'][1]['observations'] = []
    _, events = replay(bars, revised, begin=6, engine=book)
    assert not special_states(events)
    book, _ = replay(bars, row, end=11)
    revised = deepcopy(row)
    revised['strokes'][0]['points'][3]['value'] = 4.7
    _, events = replay(bars, revised, begin=11, engine=book)
    assert any(event.get('reason') == 'source_evidence_revised' for event in events)
    assert not special_states(events)


def test_special_confirmation_does_not_read_a_future_counter():
    bars, row = recovery_case()
    _, before = replay(bars, row, end=12)
    assert not any(event['state'] == '空多交替' for event in special_states(before))
    _, prefix = replay(bars[:12], row)
    assert before == prefix


def upgrade_recovery_case(level=2):
    """复现生产证据形状：突破点observations为空，升级证据引用直接下级坐标。"""
    days = ['2018-05-07', '2018-06-22', '2018-07-26', '2018-10-17',
            '2018-10-18', '2018-10-19', '2018-10-24', '2018-11-22',
            '2019-01-04', '2019-03-04', '2019-04-11', '2019-04-30',
            '2019-08-06', '2019-10-09']
    prices = [(5.12, 5.29, 5.01, 5.16), (4.1, 4.2, 3.95, 4.1),
              (5.6, 5.95, 5.48, 5.51), (4, 4.1, 3.91, 3.94),
              (3.94, 4, 3.90, 3.93), (3.96, 4.07, 3.90, 4.06),
              (4.1, 4.2, 4, 4.1), (4.41, 4.65, 4.36, 4.54),
              (3.99, 4.09, 3.96, 4.07), (4.5, 4.8, 4.4, 4.7),
              (5.51, 5.78, 5.32, 5.32), (5, 5.1, 4.8, 4.9),
              (4.12, 4.15, 4, 4.09), (8, 8.5, 7.5, 8.2)]
    bars = [Bar(datetime.fromisoformat(day), 'sh.600812', *values, 100)
            for day, values in zip(days, prices)]

    def point(index, kind, value, known):
        return dict(index=index, ordinal=0, kind=kind, value=value, available_at=known,
                    time=days[index], state='reversal', observations=[])

    key = point(0, 'H', 5.29, 0)
    origin = point(1, 'L', 3.95, 1)
    first = point(2, 'H', 5.95, 2)
    old_low = point(5, 'L', 3.90, 6)
    new_key = point(7, 'H', 4.65, 8)
    new_origin = point(8, 'L', 3.96, 9)
    impulse = point(10, 'H', 5.78, 11)
    counter = point(12, 'L', 4, 13)
    for high, low, broken in ((first, origin, key), (impulse, new_origin, new_key)):
        high['upgrade_evidence'] = dict(direction='up', role='endpoint',
            available_at=high['available_at'], confirmed_by=dict(high),
            broken_key=dict(broken), origin=dict(low))
    # 与实际升级证据一样，下级确认低点早于本级；解析后仍须用本级时钟。
    impulse['upgrade_evidence']['origin']['available_at'] = 8
    return bars, dict(trend_level=level, strokes=[dict(id='path',
        points=[key, origin, first, old_low, new_key, new_origin, impulse, counter])])


@pytest.mark.parametrize('level', [1, 2, 3])
def test_upgrade_only_deep_counter_no_longer_opens_without_an_existing_confirmation_rule(level):
    bars, row = upgrade_recovery_case(level)
    _, events = replay(bars, row)
    states = special_states(events)
    assert [(event['bar_index'], event['state']) for event in states] == [(11, '空翻多')]
    intervals = confirmed_trading_intervals(events, len(bars), level)
    assert intervals == ()


def test_upgrade_requires_real_same_level_origin_key_and_confirming_endpoint():
    _, row = upgrade_recovery_case()
    points = row['strokes'][0]['points']
    for missing in ('origin', 'broken_key', 'confirmed_by', 'available_at'):
        changed = deepcopy(points)
        changed[2]['upgrade_evidence'].pop(missing)
        assert not any(fact.point.index == 2 for fact in _facts(changed, {}, 2))
    changed = deepcopy(points)
    changed[2]['upgrade_evidence']['broken_key']['value'] = 5.1
    assert not any(fact.point.index == 2 for fact in _facts(changed, {}, 2))
    assert not any(fact.point.index == 2 for fact in _facts(points[1:], {}, 2))


def test_upgrade_uses_own_level_clock_and_does_not_duplicate_display_observation():
    _, row = upgrade_recovery_case()
    points = deepcopy(row['strokes'][0]['points'])
    points[-2]['available_at'] = 9
    points[-2]['index'] = 9
    points[-2]['upgrade_evidence']['available_at'] = 9
    points[-2]['upgrade_evidence']['confirmed_by'].update(index=9, available_at=9)
    points[-3]['available_at'] = 10
    assert not any(fact.point.index == 9 for fact in _facts(points, {}, 9))
    fact = next(fact for fact in _facts(points, {}, 10) if fact.point.index == 9)
    assert fact.known == 10 and fact.origin.known == 10
    _, row = upgrade_recovery_case()
    points = row['strokes'][0]['points']
    points[2]['observations'] = [dict(title='翻空为多', available_at=2, confirmed_low=dict(points[1]))]
    assert len([fact for fact in _facts(points, {}, 2) if fact.point.index == 2]) == 1


@pytest.mark.parametrize('checkpoint_at', [3, 6, 12])
def test_upgrade_recovery_checkpoint_keeps_the_original_seed_and_counter_clock(checkpoint_at):
    bars, row = upgrade_recovery_case()
    book, before = replay(bars, row, end=checkpoint_at)
    restored = TrendLifecycle.from_checkpoint(book.checkpoint())
    _, after = replay(bars, row, begin=checkpoint_at, engine=restored)
    _, whole = replay(bars, row)
    assert before + after == whole
    assert not any(event['state'] == '空多交替' for event in special_states(whole))


@pytest.mark.parametrize('level', [1, 2, 3])
def test_august_counter_survives_zero_close_break_and_same_leg_high_extension(level):
    bars, final = upgrade_recovery_case(level)
    for index in (3, 4):
        bars[index] = replace(bars[index], open=4, high=4.1, low=3.96, close=4)
    early = deepcopy(final)
    high = early['strokes'][0]['points'][-2]
    high.update(index=9, time='2019-03-04', available_at=9, value=4.7)
    high['upgrade_evidence']['available_at'] = 9
    high['upgrade_evidence']['confirmed_by'].update(index=9, available_at=9, value=4.7)
    book, before = replay(bars, early, end=11)
    restored = TrendLifecycle.from_checkpoint(book.checkpoint())
    _, after = replay(bars, final, begin=11, engine=restored)
    events = before + after
    recovery = next(event for event in events if event['event'] == 'trend_lifecycle_recovered')
    assert recovery['consecutive_below_closes'] == 0
    assert bars[recovery['recovered_index']].timestamp.date().isoformat() == '2018-10-19'
    assert not any(event.get('reason') == 'source_evidence_revised' for event in after)
    extension = next(event for event in after if event.get('confirmation_rule') ==
                     'same_origin_and_broken_key_confirmed_high_extension')
    assert extension['origin_index'] == 8 and extension['flip_index'] == 10
    assert extension['cycle_started_index'] == 9
    windows = confirmed_trading_intervals(events, len(bars), level)
    assert windows == ()  # 深回撤守低本身不再替代既有确认规则。


def test_touching_original_low_does_not_start_recovery():
    from wavequant.domain.market_structure.trend_lifecycle import _BullRecovery, advance_bull_recovery

    _, row = upgrade_recovery_case()
    seed = next(fact for fact in _facts(row['strokes'][0]['points'], {}, 2) if fact.point.index == 2)
    state = _BullRecovery(seed)
    assert advance_bull_recovery(state, 3.95, 4.06, 3) == state
