"""直接来源冻结末升低的正式破位必须当天关闭上级资格。"""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from wavequant.domain.market_structure.source_bear_exit import (
    SOURCE_BEAR_EVENT, SOURCE_BEAR_EXIT, confirmed_source_bear, source_bearish_exits,
)
from wavequant.domain.market_structure.trend_lifecycle import TrendLifecycle, _facts
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.trading_intervals import (
    TradingEligibility, confirmed_trading_intervals, interval_days,
)


def sample(level, *, dated=False, known=10, price=4.21):
    # 4.30/4.21取自用户报价；其余行情、结构和知识日均合成。
    bars = [Bar(datetime(2024, 4, 6) + timedelta(days=index), 'TEST',
                4.6, 4.9, 4.4 if index < 10 else price, 4.6, 100) for index in range(13)]
    dates = {str(bar.timestamp.date()): index for index, bar in enumerate(bars)}

    def clock(index):
        return str(bars[index].timestamp.date()) if dated else index

    def point(index, kind, value, available):
        return dict(index=index, ordinal=0, kind=kind, value=value,
                    time=str(bars[index].timestamp.date()), available_at=clock(available), state='confirmed')

    key, origin, attack = point(4, 'L', 4.30, 5), point(6, 'H', 5.5, 7), point(10, 'L', price, known)
    attack['lifecycle_context'] = dict(direction='down', key=dict(key), origin=dict(origin),
                                       available_at=clock(known))
    attack['upgrade_evidence'] = dict(direction='down', role='endpoint', available_at=clock(known),
                                      origin=dict(origin), broken_key=dict(key), confirmed_by=dict(attack))
    lower = dict(trend_level=level - 1, strokes=[dict(id='lower', points=[key, origin, attack])])
    upper = dict(trend_level=level, strokes=[dict(id='upper', source_path='lower', points=[])])
    opened = dict(event='trend_lifecycle_state', state='空多交替', flip_confirmed=True,
        trend_level=level, source_path='upper', bar_index=3, flip_index=1,
        flip_known_index=1, counter_index=2, alternation_index=3)
    return bars, dates, lower, upper, opened


@pytest.mark.parametrize('level', [1, 2, 3])
@pytest.mark.parametrize('dated', [False, True])
@pytest.mark.parametrize('restore', [False, True])
def test_direct_source_formal_flip_closes_on_its_knowledge_day_and_replays(level, dated, restore):
    bars, dates, lower, upper, opened = sample(level, dated=dated)
    combined, child, owner = TrendLifecycle(), TrendLifecycle(), TrendLifecycle()
    gate = TradingEligibility(level)
    gate.observe(opened)
    snapshot = gate.intervals
    exits, separate = [], []
    for now in range(len(bars)):
        if restore and now == 10:
            combined = TrendLifecycle.from_checkpoint(combined.checkpoint())
            child = TrendLifecycle.from_checkpoint(child.checkpoint())
            owner = TrendLifecycle.from_checkpoint(owner.checkpoint())
        whole = combined.advance([lower, upper], bars, now, dates, _owned_structure=True)
        source = child.advance([lower], bars, now, dates, _owned_structure=True)
        target = owner.advance([upper], bars, now, dates, source_events=source, _owned_structure=True)
        today = [event for event in whole if event['event'] == SOURCE_BEAR_EVENT]
        exits.extend(today)
        separate.extend(event for event in target if event['event'] == SOURCE_BEAR_EVENT)
        for event in today:
            gate.observe(event)
        assert gate.active is (now < 10)
    assert exits == separate and len(exits) == 1
    event = exits[0]
    proof = event['source_confirmation']
    assert event['bar_index'] == 10
    assert proof['state'] == '多翻空' and confirmed_source_bear(proof, 10)
    assert proof['broken_key_index'] == 4 and proof['broken_key_price'] == 4.30
    assert proof['breakout_level'] == 4.21 and proof['counter_index'] is None
    assert snapshot[0].end_index is None
    closed = gate.intervals[0]
    assert (closed.end_index, closed.end_reason, closed.end_key_index, closed.end_key_price) == (
        10, SOURCE_BEAR_EXIT, 4, 4.30)
    assert confirmed_trading_intervals([opened, *exits], len(bars), level) == gate.intervals
    assert not interval_days(gate.intervals, len(bars))[10]
    assert interval_days(gate.intervals, len(bars), include_end=True)[10]
    assert gate.observe(dict(opened, event='trend_lifecycle_candidate', bar_index=11)) is None
    assert not gate.active
    assert gate.observe(dict(opened, bar_index=12, alternation_index=12, flip_index=10,
                             flip_known_index=10, counter_index=11)) is True
    assert gate.intervals[1].start_index == 12 and gate.intervals[0] == closed


@pytest.mark.parametrize('dated', [False, True])
def test_late_source_confirmation_is_not_backdated_to_the_attack(dated):
    bars, dates, lower, upper, _ = sample(2, dated=dated, known=11)
    engine, events = TrendLifecycle(), []
    for now in range(len(bars)):
        events.extend(engine.advance([lower, upper], bars, now, dates, _owned_structure=True))
    exits = [event for event in events if event['event'] == SOURCE_BEAR_EVENT]
    assert len(exits) == 1 and exits[0]['bar_index'] == 11
    assert exits[0]['source_confirmation']['flip_index'] == 10


@pytest.mark.parametrize('invalid', ['touch', 'missing_context', 'local_key', 'future_context',
                                    'unknown_key', 'wrong_direction', 'wrong_attack'])
def test_bear_upgrade_requires_the_same_frozen_context_and_a_strict_old_key(invalid):
    _, dates, lower, _, _ = sample(2, price=4.30 if invalid == 'touch' else 4.21)
    points = lower['strokes'][0]['points']
    attack = points[-1]
    if invalid == 'missing_context':
        attack.pop('lifecycle_context')
    elif invalid == 'local_key':
        attack['upgrade_evidence']['broken_key']['value'] = 4.4
    elif invalid == 'future_context':
        attack['lifecycle_context']['available_at'] = 11
    elif invalid == 'unknown_key':
        points[0]['available_at'] = 10
        attack['upgrade_evidence']['broken_key']['available_at'] = 10
        attack['lifecycle_context']['key']['available_at'] = 10
    elif invalid == 'wrong_direction':
        attack['upgrade_evidence']['direction'] = 'up'
    elif invalid == 'wrong_attack':
        attack['upgrade_evidence']['confirmed_by']['value'] = 4.2
    assert not any(fact.title == '翻多为空' for fact in _facts(points, dates, 10))


def test_direct_source_break_cannot_close_an_unrelated_or_ended_parent():
    bars, dates, lower, upper, opened = sample(2)
    engine, flips = TrendLifecycle(), []
    for now in range(11):
        flips.extend(engine.advance([lower], bars, now, dates))
    flip = next(event for event in flips if event.get('source_event') == '翻多为空')
    for changed in (dict(flip, trend_level=0), dict(flip, source_path='other'),
                    dict(flip, event='trend_lifecycle_candidate'), dict(flip, available_index=11),
                    dict(flip, broken_key_price=4.21)):
        assert source_bearish_exits([upper], [changed], 10) == []
    ended = dict(upper, _path_market_end_indices={'lower': 10})
    assert source_bearish_exits([ended], [flip], 10) == []
    event = source_bearish_exits([upper], [flip], 10)[0]
    gate = TradingEligibility(2)
    gate.observe(opened)
    forged = deepcopy(event)
    forged['direct_source_path'] = 'other'
    assert gate.observe(forged) is None and gate.active


@pytest.mark.parametrize('level', [1, 2, 3])
@pytest.mark.parametrize('restore', [False, True])
def test_formal_bear_break_supersedes_pending_special_bull_recovery(level, restore):
    from wavequant.domain.market_structure.trend_lifecycle import LifecycleState, _Cycle, _Pivot

    bars, dates, lower, upper, opened = sample(level)
    engine, gate = TrendLifecycle(), TradingEligibility(level)
    gate.observe(opened)
    for now in range(10):
        engine.advance([lower, upper], bars, now, dates)
    engine._cycles[level - 1, 'lower'] = _Cycle(LifecycleState.BULL_FLIP, True,
        _Pivot(0, 0, 'L', 3.0, 0), 8, _Pivot(8, 0, 'H', 5.0, 8), recovery=True)
    if restore:
        engine = TrendLifecycle.from_checkpoint(engine.checkpoint())
    events = engine.advance([lower, upper], bars, 10, dates)
    exit_event = next(event for event in events if event['event'] == SOURCE_BEAR_EVENT)
    assert exit_event['source_confirmation']['state'] == '多翻空'
    assert gate.observe(exit_event) is False
    assert gate.intervals[0].end_index == 10



def daily_source_sample(level, *, dated=False):
    bars, dates, lower, upper, opened = sample(level, dated=dated)
    key, high = deepcopy(lower['strokes'][0]['points'][:2])
    bars[4] = replace(bars[4], low=4.30)
    bars[6] = replace(bars[6], high=5.5)
    # 后来的低点高于冻结末升低，且没有任何攻击低点或六态观察。
    # 4.35 已跌破右侧低点，但不能因此替换整波起点 4.30 而提前关窗。
    right_low = dict(index=8, ordinal=0, kind='L', value=4.4, state='confirmed',
                     time=str(bars[8].timestamp.date()),
                     available_at=str(bars[9].timestamp.date()) if dated else 9)
    bars[9] = replace(bars[9], low=4.35)
    upper['source_strokes'] = [dict(id='lower', points=[key, high, right_low])]
    return bars, dates, lower, upper, opened


@pytest.mark.parametrize('level', [1, 2, 3])
@pytest.mark.parametrize('dated', [False, True])
@pytest.mark.parametrize('resume', ['continuous', 'copy', 'checkpoint', 'combine'])
def test_daily_source_market_break_does_not_wait_for_a_confirmed_attack_endpoint(level, dated, resume):
    from wavequant.domain.market_structure.source_bear_exit import SOURCE_MARKET_BREAK_EVENT

    bars, dates, _, upper, opened = daily_source_sample(level, dated=dated)
    engine, gate = TrendLifecycle(), TradingEligibility(level)
    gate.observe(opened)
    events = []
    for now in range(len(bars)):
        if now == 10:
            if resume == 'copy':
                engine = engine.copy()
            elif resume == 'checkpoint':
                engine = TrendLifecycle.from_checkpoint(engine.checkpoint())
            elif resume == 'combine':
                engine = TrendLifecycle.combine([engine])
        today = engine.advance([upper], bars, now, dates, _owned_structure=True)
        events.extend(today)
        for event in today:
            gate.observe(event)
        assert gate.active is (now < 10)
    exits = [event for event in events if event['event'] == SOURCE_BEAR_EVENT]
    assert len(exits) == 1 and exits[0]['bar_index'] == 10
    proof = exits[0]['source_confirmation']
    assert proof['event'] == SOURCE_MARKET_BREAK_EVENT
    assert confirmed_source_bear(proof, 10)
    assert 'state' not in proof and 'flip_index' not in proof
    assert proof['broken_key_index'] == 4 and proof['broken_key_price'] == 4.30
    assert proof['market_low'] == 4.21 and proof['origin_index'] == 6
    assert gate.intervals[0].end_index == 10
    assert gate.intervals[0].end_key_index == 4
    assert gate.intervals[0].end_key_price == 4.30
    assert confirmed_trading_intervals([opened, *events], len(bars), level) == gate.intervals
    assert not interval_days(gate.intervals, len(bars))[10]
    assert interval_days(gate.intervals, len(bars), include_end=True)[10]


@pytest.mark.parametrize('invalid', ['touch', 'future_key', 'future_origin', 'developing',
    'display_only', 'open_wave', 'direction_only', 'invalidated_high', 'same_day_new_high',
    'wrong_path', 'ended_path', 'unordered'])
def test_daily_source_market_break_rejects_noncausal_or_unfinished_source(invalid):
    from wavequant.domain.market_structure.trend_levels import _WAVE_COMPLETION_POLICY

    bars, dates, _, upper, _ = daily_source_sample(2)
    key, high, right_low = upper['source_strokes'][0]['points']
    if invalid == 'touch':
        bars[10] = replace(bars[10], low=4.30)
    elif invalid == 'future_key':
        key['available_at'] = 10
    elif invalid == 'future_origin':
        high['available_at'] = 11
    elif invalid == 'developing':
        high['state'] = 'developing'
    elif invalid == 'display_only':
        high['display_only'] = True
    elif invalid in ('open_wave', 'direction_only'):
        high['wave_identity_state'] = 'owned' if invalid == 'open_wave' else 'direction_only'
        high['wave_completion_policy'] = _WAVE_COMPLETION_POLICY
    elif invalid == 'invalidated_high':
        bars[8] = replace(bars[8], high=5.6)
    elif invalid == 'same_day_new_high':
        bars[10] = replace(bars[10], high=5.6)
    elif invalid == 'wrong_path':
        upper['source_strokes'][0]['id'] = 'another'
    elif invalid == 'ended_path':
        upper['_path_market_end_indices'] = {'lower': 10}
    elif invalid == 'unordered':
        upper['source_strokes'][0]['points'] = [high, key, right_low]
    engine = TrendLifecycle()
    for now in range(11):
        events = engine.advance([upper], bars, now, dates, _owned_structure=True)
    assert not any(event['event'] == SOURCE_BEAR_EVENT for event in events)


def test_daily_source_market_break_is_identical_for_combined_and_separate_levels():
    bars, dates, lower, upper, _ = daily_source_sample(2)
    # 下级只有尚未破位的已知结构，不注入已确认攻击端点。
    lower['strokes'][0]['points'] = upper['source_strokes'][0]['points']
    combined, child, parent = TrendLifecycle(), TrendLifecycle(), TrendLifecycle()
    whole_exits, split_exits = [], []
    for now in range(len(bars)):
        whole = combined.advance([lower, upper], bars, now, dates, _owned_structure=True)
        source = child.advance([lower], bars, now, dates, _owned_structure=True)
        target = parent.advance([upper], bars, now, dates, source_events=source, _owned_structure=True)
        whole_exits.extend(event for event in whole if event['event'] == SOURCE_BEAR_EVENT)
        split_exits.extend(event for event in target if event['event'] == SOURCE_BEAR_EVENT)
    assert whole_exits == split_exits and len(whole_exits) == 1


def test_public_mutable_source_cannot_reuse_an_old_key_candidate():
    bars, dates, _, upper, _ = daily_source_sample(2)
    engine = TrendLifecycle()
    for now in range(10):
        engine.advance([upper], bars, now, dates)
    upper['source_strokes'][0]['points'][0]['value'] = 4.0
    assert not any(event['event'] == SOURCE_BEAR_EVENT
                   for event in engine.advance([upper], bars, 10, dates))


@pytest.mark.parametrize('up', [False, True])
def test_shared_market_break_is_strict_and_respects_its_prefix(up):
    from wavequant.domain.market_structure.trend_confirmation import first_strict_market_break

    bars = [Bar(datetime(2024, 1, 1) + timedelta(days=i), 'TEST', 5, high, low, 5, 100)
            for i, (high, low) in enumerate([(6, 4), (7, 3), (7.1, 2.9), (8, 2)])]
    threshold = 7 if up else 3
    assert first_strict_market_break(bars, 0, 2, threshold, up=up) is None
    assert first_strict_market_break(bars, 0, 3, threshold, up=up) == 2
    assert first_strict_market_break(bars, 3, 3, threshold, up=up) is None



@pytest.mark.parametrize('level', [1, 2, 3])
def test_daily_source_market_index_and_plain_prices_produce_the_same_exit(level):
    from wavequant.domain.market_structure.trend_market_index import _MarketRangeIndex

    prices, dates, _, upper, _ = daily_source_sample(level)
    bars = tuple(prices)
    market_index = _MarketRangeIndex(bars)
    indexed, plain = TrendLifecycle(), TrendLifecycle()
    for now in range(len(bars)):
        actual = indexed.advance([upper], bars, now, dates,
                                 _owned_structure=True, _market_index=market_index)
        expected = plain.advance([upper], bars, now, dates, _owned_structure=True)
        assert actual == expected


def test_restoring_after_market_break_does_not_emit_it_again():
    bars, dates, _, upper, _ = daily_source_sample(2)
    engine = TrendLifecycle()
    for now in range(11):
        engine.advance([upper], bars, now, dates, _owned_structure=True)
    engine = TrendLifecycle.from_checkpoint(engine.checkpoint())
    for now in range(11, len(bars)):
        assert not any(event['event'] == SOURCE_BEAR_EVENT for event in
            engine.advance([upper], bars, now, dates, _owned_structure=True))
