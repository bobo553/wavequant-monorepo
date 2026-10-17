"""无前驱路径首端不能遮蔽后续已成立的反向背景。"""

from copy import deepcopy
from datetime import date, datetime, timedelta
from dataclasses import replace
import json
from pathlib import Path

import pytest

from wavequant.domain.market_structure.trend_lifecycle import (
    LifecycleState, TrendLifecycle, _Cycle, _Fact, _Pivot, _facts,
)
from wavequant.domain.market_structure.bull_alternation_confirmation import confirm_bull_alternation
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.market_calculations import market_calculations
from wavequant.domain.strategies.recovery_entry_history import merge_recovery_entry_history
from wavequant.domain.market_structure.trend_lines import _annotate_geometry


@pytest.mark.parametrize('level', [1, 2, 3])
@pytest.mark.parametrize('mirror', [False, True])
@pytest.mark.parametrize('attack', [6.2, 6.4940278055852385, 6.803100567839622])
def test_unanchored_old_extreme_does_not_hide_later_frozen_key(level, mirror, attack):
    # 新华传媒已发布二级节点的缩减价格序列；日期缩减不证明真实确认日。
    prices = [3.282126081582199, 8.230407911001233, 4.113138070493338,
              7.400171220318062, 3.836007122368955, 6.4940278055852385,
              3.3510821788171428, attack]
    dates = {(date(2020, 1, 1) + timedelta(days=i)).isoformat(): i for i in range(10)}
    days = list(dates)
    source = [dict(index=i, ordinal=0, time=days[i], available_at=days[i],
                   kind=('H' if i % 2 == 0 else 'L') if mirror else ('L' if i % 2 == 0 else 'H'),
                   value=20 - price if mirror else price, state='confirmed')
              for i, price in enumerate(prices)]
    title = '翻多为空' if mirror else '翻空为多'
    for count in range(1, len(source) + 1):
        points = deepcopy(source[:count])
        _annotate_geometry(points, 'TEST', dates, trend_level=level)
        facts = [fact for fact in _facts(points, dates, count - 1) if fact.title == title]
        if count == len(source) and attack > prices[5]:
            assert len(facts) == 1
            assert (facts[0].broken.index, facts[0].origin.index, facts[0].point.index) == (5, 6, 7)
            assert facts[0].known == 7
        else:
            assert facts == []
        assert [{key: point[key] for key in source[0]} for point in points] == source[:count]


@pytest.mark.parametrize('level', [1, 2, 3])
def test_boundary_bootstrap_keeps_late_context_clock(level):
    prices = [3, 9, 5, 8, 4, 10]
    points = [dict(index=i, ordinal=0, time=str(i), available_at=i,
                   kind='L' if i % 2 == 0 else 'H', value=price, state='confirmed')
              for i, price in enumerate(prices)]
    points[0]['available_at'] = 8
    dates = {i: i for i in range(9)}
    _annotate_geometry(points, 'TEST', dates, trend_level=level)
    assert not any(fact.title == '翻空为多' for fact in _facts(points, dates, 7))
    flip = next(fact for fact in _facts(points, dates, 8) if fact.title == '翻空为多')
    assert flip.known == 8
    assert (flip.broken.index, flip.origin.index, flip.point.index) == (3, 4, 5)
    assert points[-1]['available_at'] == 5


@pytest.mark.parametrize('level', [1, 2, 3])
@pytest.mark.parametrize('up', [False, True])
@pytest.mark.parametrize('break_origin', [False, True])
def test_counter_break_retains_only_defended_flip_and_checkpoint(level, up, break_origin):
    origin = _Pivot(1, 0, 'L' if up else 'H', 3 if up else 17, 1)
    high = _Pivot(2, 0, 'H' if up else 'L', 10, 2)
    counter = _Pivot(3, 0, origin.kind, 6 if up else 14, 3)
    title = '翻空为多' if up else '翻多为空'
    flip = _Fact(title, high, 2, origin)
    alternation = _Fact('空多交替' if up else '多空交替', counter, 3, origin, high)
    cycle = _Cycle(LifecycleState.BULL_ALTERNATION if up else LifecycleState.BEAR_ALTERNATION,
                   up, origin, 2, high, counter, 3, evidence=frozenset((flip, alternation)),
                   confirmation_source=(counter,))
    bars = [Bar(datetime(2024, 1, 1) + timedelta(days=i), 'TEST', 7, 15, 5, 8, 100)
            for i in range(5)]
    if break_origin:
        bars[-1] = replace(bars[-1], low=2 if up else 5, high=15 if up else 18)
    engine = TrendLifecycle()
    engine._cycles[level, 'path'] = cycle
    engine._last_index = 4
    events = []
    engine._price((level, 'path'), bars, 4, events)
    assert events[0]['reason'] == 'defense_broken'
    if break_origin:
        assert (level, 'path') not in engine._cycles
        assert len(events) == 1
    else:
        pending = engine._cycles[level, 'path']
        assert pending.origin == origin and pending.impulse == high
        assert pending.evidence == frozenset((flip,))
        assert pending.counter is None and pending.alternated is None
        assert pending.confirmation_source == ()
        assert events[-1]['confirmation_rule'] == 'countermove_extended_with_frozen_origin_defended'
        assert TrendLifecycle.from_checkpoint(engine.checkpoint()).checkpoint() == engine.checkpoint()


def test_broken_formal_counter_cannot_reconfirm_just_because_original_low_is_defended():
    bars = [Bar(datetime(2024, 1, 1) + timedelta(days=i), 'TEST', *row)
            for i, row in enumerate([(8, 9, 7, 8, 100), (4, 5, 3, 4, 100),
                (9, 10, 8, 9, 100), (8, 9, 7, 8, 100), (7, 8, 6, 7, 100)])]
    broken, origin, high, counter = (_Pivot(0, 0, 'H', 9, 0), _Pivot(1, 0, 'L', 3, 1),
                                   _Pivot(2, 0, 'H', 10, 2), _Pivot(3, 0, 'L', 7, 3))
    proof = confirm_bull_alternation(bars, 4, origin=origin, high=high, broken=broken,
        points=(broken, origin, high, counter), polyline=(), level=2, path='path')
    assert proof is None


def test_xinhua_full_causal_pipeline_confirms_february_low_without_rewriting_open_window():
    fixture = json.loads((Path(__file__).parent / 'fixtures/xinhua_2024_alternation.json').read_text('utf8'))
    bars = tuple(Bar(datetime.fromisoformat(day), fixture['symbol'], opening, high, low, close, volume,
                     adjustment_factor=factor)
                 for day, opening, high, low, close, volume, factor in fixture['bars'])
    dates = {bar.timestamp.date().isoformat(): index for index, bar in enumerate(bars)}
    events = []
    permissions = []

    def collect(day):
        events.extend(event for event in day.lifecycle_events if event['trend_level'] == 2)
        permissions.append(day.permits_buy(2))

    with market_calculations(bars, on_day=collect) as work:
        work.calculate_trend_line(3)
        intervals = work.trading_intervals(2)
    confirmations = [event for event in events if event.get('state') == '空多交替'
                     and event['event'] == 'trend_lifecycle_state'
                     and event.get('counter_index') == dates['2024-02-08']]
    assert len(confirmations) == 1
    confirmation = confirmations[0]
    assert confirmation['bar_index'] == dates['2024-03-20']
    assert confirmation['flip_index'] == dates['2023-05-16']
    assert confirmation['flip_known_index'] == dates['2023-05-30']
    assert confirmation['origin_index'] == dates['2022-10-28']
    assert confirmation['confirmation_evidence']['key_index'] == dates['2021-05-17']
    assert confirmation['confirmation_rule'] == 'positive_n_and_squeeze_after_qualified_b'
    assert confirmation['counter_price'] == pytest.approx(3.532767357186747)
    assert confirmation['confirmation_evidence']['attack'] == dates['2024-03-13']
    assert all(permissions[dates['2024-03-20']:])
    contexts = merge_recovery_entry_history(bars, {}, events)
    assert not contexts[dates['2024-03-19']]
    assert contexts[dates['2024-03-20']][0].alternation_low_index == dates['2024-02-08']
    assert contexts[dates['2024-03-20']][0].confirmation_attack == dates['2024-03-13']
    assert not any(event.get('counter_index') == dates['2024-02-08']
                   and event.get('state') == '空多交替' and event['bar_index'] < dates['2024-03-20']
                   for event in events)
    # 旧资格按既有退出契约保持开放；新低点确认不能倒改区间起始日期。
    assert [(window.start_index, window.end_index) for window in intervals] == [
        (dates['2023-08-22'], dates['2023-09-14']), (dates['2023-11-21'], None)]


@pytest.mark.parametrize('invalid', [None, 'future', 'unconfirmed'])
def test_new_counter_updates_context_in_existing_interval_only_when_confirmed(invalid):
    bars = [Bar(datetime(2024, 1, 1) + timedelta(days=i), 'TEST', 7, 10, 6, 8, 100)
            for i in range(8)]
    bars[5] = replace(bars[5], low=5)
    first = dict(event='trend_lifecycle_state', state='空多交替', trend_level=2, source_path='path',
                 bar_index=4, flip_confirmed=True, flip_index=2, flip_known_index=2,
                 counter_index=3, counter_price=6, alternation_index=4,
                 origin_index=1, origin_price=3, cycle_started_index=2, breakout_level=10,
                 confirmation_evidence=dict(key_index=0, key_price=9, attack=4))
    later = dict(first, bar_index=6, counter_index=5, counter_price=5, alternation_index=6,
                 confirmation_evidence=dict(key_index=0, key_price=9, attack=6))
    if invalid == 'future':
        later['flip_known_index'] = 7
    elif invalid == 'unconfirmed':
        later['flip_confirmed'] = False
    history = merge_recovery_entry_history(bars, {}, [first, later])
    assert history[4][0].alternation_low_index == 3
    assert history[5] == ()
    if invalid is None:
        assert history[6][0].alternation_low_index == 5
        assert history[6][0].confirmation_attack == 6
        assert history[4][0].alternation_low_index == 3
    else:
        assert history[6] == ()
