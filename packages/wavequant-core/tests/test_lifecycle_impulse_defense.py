"""翻多后统一守末跌高之后的最低点，不用较近局部低点替换。"""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
import json
from pathlib import Path

import pytest

from wavequant.domain.market_structure.trend_lifecycle import (
    LifecycleState, TrendLifecycle, _Cycle, _Fact, _Pivot,
)
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.trading_intervals import confirmed_trading_intervals, interval_days


def gap_case(level=2):
    rows = [(5.2, 5.4, 5.1, 5.3, 100), (3.1, 3.2, 3, 3.1, 100), (4.8, 5, 4.7, 4.9, 100),
            (4, 4.09, 3.96, 4.07, 100), (5.51, 5.78, 5.32, 5.32, 200),
            (4.12, 4.15, 4, 4.09, 100), (4.10, 4.21, 4.06, 4.21, 150),
            (4.19, 4.25, 4.19, 4.22, 3364310), (4.25, 4.64, 4.22, 4.64, 13786411)]
    bars = [Bar(datetime(2024, 1, 1) + timedelta(days=i), 'TEST', *row) for i, row in enumerate(rows)]

    def point(index, kind, price, known):
        return dict(index=index, ordinal=0, kind=kind, value=price, available_at=known,
                    time=bars[index].timestamp.date().isoformat(), state='confirmed')

    key, background, prior, origin, high = (point(0, 'H', 5.4, 0), point(1, 'L', 3, 1),
        point(2, 'H', 5, 2), point(3, 'L', 3.96, 3), point(4, 'H', 5.78, 4))
    high['observations'] = [dict(title='翻空为多', available_at=4, key=dict(key), confirmed_low=dict(background))]
    row = dict(trend_level=level, strokes=[dict(id='path', points=[key, background, prior, origin, high])])
    polyline = [dict(high), point(5, 'L', 4, 6), point(6, 'H', 4.21, 6)]
    return bars, row, polyline


def replay(bars, row, polyline, *, restore_at=None):
    engine, events = TrendLifecycle(), []
    dates = {bar.timestamp.date().isoformat(): i for i, bar in enumerate(bars)}
    for now in range(len(bars)):
        if restore_at == now:
            engine = TrendLifecycle.from_checkpoint(engine.checkpoint())
        visible = [point for point in polyline if point['available_at'] <= now]
        events.extend(engine.advance([row], bars, now, dates, polyline=visible))
    return engine, events


@pytest.mark.parametrize('level', [0, 1, 2, 3])
def test_body_gap_uses_frozen_flip_origin_for_both_defense_and_measurement(level):
    bars, row, polyline = gap_case(level)
    _, events = replay(bars, row, polyline)
    confirmation = next(event for event in events if event.get('state') == '空多交替')
    assert confirmation['bar_index'] == 8 and confirmation['origin_index'] == 1
    assert confirmation['confirmation_evidence']['a_origin_index'] == 1
    assert confirmation['confirmation_evidence']['a_origin_price'] == 3
    assert confirmation['confirmation_evidence']['a_duration'] == 3
    assert confirmation['confirmation_evidence']['key_index'] == 0
    if level:
        windows = confirmed_trading_intervals(events, len(bars), level)
        assert interval_days(windows, len(bars)) == (False,) * 8 + (True,)


@pytest.mark.parametrize('level', [0, 1, 2, 3])
@pytest.mark.parametrize('failure', ['broken_origin', 'missing_origin', 'future_origin'])
def test_broken_missing_or_future_frozen_origin_cannot_confirm(level, failure):
    bars, row, polyline = gap_case(level)
    if failure == 'broken_origin':
        bars[5] = replace(bars[5], low=2.99)
        polyline[1]['value'] = 2.99
    elif failure == 'missing_origin':
        row['strokes'][0]['points'].pop(1)
    else:
        row['strokes'][0]['points'][1]['available_at'] = 9
        row['strokes'][0]['points'][-1]['observations'][0]['confirmed_low']['available_at'] = 9
    _, events = replay(bars, row, polyline, restore_at=7)
    assert not any(event.get('state') in ('空多交替', '转多确认') for event in events)
    if level:
        assert confirmed_trading_intervals(events, len(bars), level) == ()


@pytest.mark.parametrize('up', [False, True])
@pytest.mark.parametrize('move', [-1, 0, 1])
def test_same_cycle_impulse_only_extends_strictly_in_its_direction(up, move):
    observer = TrendLifecycle()
    sign, kind = (1, 'H') if up else (-1, 'L')
    origin = _Pivot(0, 0, 'L' if up else 'H', 5 if up else 15, 0)
    high = _Pivot(2, 0, kind, 10, 2)
    title = '翻空为多' if up else '翻多为空'
    fact = _Fact(title, high, 2, origin)
    observer._cycles[2, 'path'] = _Cycle(LifecycleState.BULL_FLIP if up else LifecycleState.BEAR_FLIP,
        up, origin, 2, high, evidence=frozenset((fact,)))
    later = _Pivot(4, 0, kind, 10 + sign * move, 4)
    events = []
    observer._consume((2, 'path'), _Fact(title, later, 4, origin), 4, events)
    assert observer._cycles[2, 'path'].impulse == (later if move > 0 else high)
    assert bool(events) == (move > 0)
    counter = _Pivot(5, 0, origin.kind, 7 if up else 13, 5)
    alternation = _Fact('空多交替' if up else '多空交替', counter, 5, origin, later)
    observer._consume((2, 'path'), alternation, 5, events)
    assert observer._cycles[2, 'path'].alternated == (5 if move > 0 else None)


@pytest.mark.parametrize('up', [False, True])
def test_alternation_can_confirm_a_later_strict_extension_without_repeating_flip_label(up):
    observer = TrendLifecycle()
    origin = _Pivot(0, 0, 'L' if up else 'H', 5 if up else 15, 0)
    impulse = _Pivot(2, 0, 'H' if up else 'L', 10, 2)
    observer._consume((2, 'path'), _Fact('翻空为多' if up else '翻多为空', impulse, 2, origin), 2, [])
    extended = replace(impulse, index=4, known=4, price=11 if up else 9)
    counter = _Pivot(5, 0, origin.kind, 7 if up else 13, 5)
    observer._consume((2, 'path'), _Fact('空多交替' if up else '多空交替', counter, 5, origin, extended), 5, [])
    assert observer._cycles[2, 'path'].alternated == 5
    assert observer._cycles[2, 'path'].impulse == extended


def test_successful_gap_retains_frozen_origin_across_checkpoint_and_rejects_revision():
    bars, row, polyline = gap_case()
    engine, events = replay(bars, row, polyline)
    restored = TrendLifecycle.from_checkpoint(engine.checkpoint())
    assert restored.checkpoint() == engine.checkpoint()
    bars.append(replace(bars[-1], timestamp=bars[-1].timestamp + timedelta(days=1),
                        open=4.4, high=4.7, low=4.3, close=4.5))
    revised = deepcopy(row)
    revised['strokes'][0]['points'][1]['value'] = 3.01
    dates = {bar.timestamp.date().isoformat(): i for i, bar in enumerate(bars)}
    after = restored.advance([revised], bars, 9, dates, polyline=polyline)
    assert any(event.get('reason') == 'source_evidence_revised' for event in after)
    assert confirmed_trading_intervals(events + after, len(bars), 2)[0].end_index == 9


@pytest.mark.parametrize('restore_at', [None, 1484, 1504, 1677])
def test_guofang_history_uses_the_same_origin_for_flip_and_alternation(restore_at):
    fixture = json.loads((Path(__file__).parent / 'fixtures' / 'guofang_2024_impulse_defense.json').read_text('utf-8'))
    bars = [Bar(datetime.fromisoformat(day), fixture['symbol'], *values) for day, *values in fixture['bars']]
    dates = {bar.timestamp.date().isoformat(): i for i, bar in enumerate(bars)}
    row = dict(trend_level=2, strokes=[dict(id='path', points=fixture['points'])])
    polyline = [dict(point, available_at=dates[point['available_at']]) for point in fixture['polyline']]
    _, events = replay(bars, row, polyline, restore_at=restore_at)
    assert round(bars[1484].close, 2) == 4.73
    assert round(bars[1706].close, 2) == 6.24
    assert any(event.get('state') == '空翻多' for event in events)
    confirmations = [event for event in events if event.get('state') == '空多交替'
                     and event.get('confirmation_evidence')]
    assert confirmations
    for event in confirmations:
        proof = event['confirmation_evidence']
        assert proof['a_origin_index'] == event['origin_index']
        assert proof['a_origin_price'] == event['origin_price']
        assert proof['key_index'] < event['origin_index'] < event['flip_index']
        assert min(bar.low for bar in bars[event['origin_index'] + 1:event['bar_index'] + 1]) >= event['origin_price']
    _, uninterrupted = replay(bars, row, polyline)
    assert events == uninterrupted


@pytest.mark.parametrize('old_version', (
    'six_state_anchor_close_rejection_geometry_v16',
    'six_state_shared_frozen_wave_key_v21',
    'six_state_polyline_volume_breakout_v33',
))
def test_previous_lifecycle_checkpoint_is_not_reused(old_version):
    checkpoint = TrendLifecycle().checkpoint()
    with pytest.raises(ValueError, match='incompatible lifecycle checkpoint'):
        TrendLifecycle.from_checkpoint((old_version, *checkpoint[1:]))


@pytest.mark.parametrize('level', [1, 2, 3])
@pytest.mark.parametrize('restore_at', [None, 1484, 1504])
def test_guofang_named_flip_and_deep_low_open_only_on_confirmation(level, restore_at):
    # 使用既有冻结行情与结构输入核对资格适配链；完整三级几何另需因果回放。
    fixture = json.loads((Path(__file__).parent / 'fixtures' / 'guofang_2024_impulse_defense.json').read_text('utf-8'))
    bars = [Bar(datetime.fromisoformat(day), fixture['symbol'], *values)
            for day, *values in fixture['bars'][:1505]]
    dates = {bar.timestamp.date().isoformat(): i for i, bar in enumerate(bars)}
    row = dict(trend_level=level, strokes=[dict(id='path', points=fixture['points'])])
    # 仅投影本回放前缀已知的折线；不能将更晚的确认日期回填。
    polyline = [dict(point, available_at=dates[point['available_at']]) for point in fixture['polyline']
                if point['available_at'] in dates]
    _, events = replay(bars, row, polyline, restore_at=restore_at)
    windows = confirmed_trading_intervals(events, len(bars), level)
    matching = [event for event in events if event.get('state') == '空多交替'
                and event.get('flip_index') == 1354 and event.get('counter_index') == 1484]
    assert matching
    confirmation = matching[0]
    assert confirmation['bar_index'] == 1504
    assert confirmation['origin_index'] == 755
    assert confirmation['confirmation_evidence']['key_index'] == 636
    assert confirmation['confirmation_rule'] == 'body_gap_volume_bullish_breakout_after_defended_low'
    days = interval_days(windows, len(bars))
    assert not any(days[1484:1504])
    assert days[1504]
