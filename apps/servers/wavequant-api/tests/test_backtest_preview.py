from datetime import datetime, timedelta
from dataclasses import asdict, dataclass, is_dataclass
import json
import math

import pytest

from wavequant.domain.models.model import Bar, Signal
from wavequant.domain.strategies.chart_drawing_history import ChartDrawingFrame
from wavequant.domain.strategies.market_calculations import MarketDay
from wavequant_api.application.backtest_preview import BacktestPreviewPublisher


def test_preview_copies_completed_prefix_and_throttles_but_publishes_last_day():
    bars = tuple(Bar(datetime(2026, 1, 1) + timedelta(days=i), 'TEST', 10, 12, 9, 11, 100)
                 for i in range(4))
    published = []
    clock = [0.0]
    publisher = BacktestPreviewPublisher(lambda revision, value: published.append((revision, value)),
                                         clock=lambda: clock[0])
    points = [dict(time='2026-01-01', value=10)]
    strokes = [dict(id='test', points=points)]
    result = dict(signals=[], orders=[], trades=[], audit=[], equity=[], backtest={})

    def day(index):
        result['equity'] = [dict(timestamp=bar.timestamp.isoformat(), equity=100) for bar in bars[:index + 1]]
        return MarketDay(ChartDrawingFrame(index, 0, points, dict(strokes=strokes)),
                         (dict(strokes=strokes),) * 3, (), (False, False, False))

    publisher.observe(bars, day(0))
    assert not published, 'market-only progress must not be published as a completed account day'
    publisher.observe(bars, day(0), result)
    points.append(dict(time='2026-01-02', value=12))
    assert published[0][1]['bars'][-1]['time'] == '2026-01-01'
    assert len(published[0][1]['market_bars']) == 4
    assert published[0][1]['market_asof'] == '2026-01-04'
    assert published[0][1]['market_bars'][:1] == published[0][1]['bars']
    assert published[0][1]['asof'] == '2026-01-01'
    assert len(published[0][1]['theory']['lecture_drawing']['strokes'][0]['points']) == 1
    publisher.observe(bars, day(1), result)
    assert len(published) == 1
    clock[0] = 3.0
    publisher.observe(bars, day(2), result)
    assert published[-1][0] == 3
    publisher.observe(bars, day(3), result)
    assert published[-1][0] == 4
    assert published[-1][1]['processed'] == 4
    assert published[-1][1]['total'] == 4
    assert published[-1][1]['market_bars'] is published[0][1]['market_bars']
    assert published[-1][1]['bars'] == published[-1][1]['market_bars']


def test_preview_requires_account_alignment_and_preserves_real_bs():
    bars = [Bar(datetime(2026, 1, 1), 'TEST', 10, 12, 9, 11, 100)]
    day = MarketDay(ChartDrawingFrame(0, 0, [], {'strokes': []}),
        ({'strokes': []},) * 3, (), (True, True, True))
    published = []
    publisher = BacktestPreviewPublisher(lambda _, value: published.append(value))
    signal = Signal(bars[0].timestamp, 'TEST', 0, 'LONG', 11, 9, 'test', bars[0].timestamp, 0, 2, 'bull', 15)
    result = dict(signals=[signal], orders=[], trades=[], audit=[], equity=[], backtest={})
    with pytest.raises(ValueError, match='finish the same market day'):
        publisher.observe(bars, day, result)
    result['equity'] = [dict(timestamp=bars[0].timestamp.isoformat(), equity=100)]
    result['orders'] = [dict(timestamp=bars[0].timestamp.isoformat(), side='BUY', status='filled', price=11)]
    publisher.observe(bars, day, result)
    assert {row['kind'] for row in published[0]['markers']} == {'fill', 'signal'}
    assert published[0]['signals'][0]['side'] == 'LONG'
    result['orders'].clear()
    assert published[0]['orders'], 'later mutation cannot erase an already published fill'


def test_preview_publishes_same_own_level_observation_policy_as_completed_chart():
    from copy import deepcopy

    from wavequant.domain.market_structure.trend_display import publish_trend_display

    bars = tuple(Bar(datetime(2026, 1, 1) + timedelta(days=i), 'TEST', 10, 12, 9, 11, 100)
                 for i in range(4))
    points = [dict(index=i, ordinal=0, time=bars[i].timestamp.date().isoformat(),
                   available_at=bars[i].timestamp.date().isoformat(), kind=kind, value=price)
              for i, (kind, price) in enumerate([('H', 12), ('L', 9), ('H', 11), ('L', 10)])]
    row = dict(scope='structural_trend_points_with_independent_strategy_permissions',
               strokes=[dict(id='path', source_path='path', kind='secondary', points=points)],
               developing_strokes=[])
    before = deepcopy(row)
    day = MarketDay(ChartDrawingFrame(3, 0, [], dict(strokes=[])),
                    ({'strokes': []}, row, {'strokes': []}), (), (False, False, False))
    result = dict(signals=[], orders=[], trades=[], audit=[],
                  equity=[dict(timestamp=bar.timestamp.isoformat(), equity=100) for bar in bars])
    published = []
    publisher = BacktestPreviewPublisher(lambda _, value: published.append(value))
    publisher.observe(bars, day, result)
    preview = published[-1]['theory']['secondary_trends']
    completed = publish_trend_display(row, result['audit'], level=2, asof_index=3)
    assert preview['strokes'] == completed['strokes']
    assert preview['strokes'] == before['strokes'], 'a missing flip must not cut the secondary path'
    assert [point['index'] for point in preview['strokes'][0]['points']] == [0, 1, 2, 3]
    assert not preview['strokes'][0].get('prevent_display_bridge')
    assert preview['observation_strokes'] == completed['observation_strokes']
    assert preview['observation_strokes'][0]['state'] == '多头疑虑'
    assert [point['index'] for point in preview['observation_strokes'][0]['points']] == [1, 2]
    assert row == before

    result['audit'] = [dict(event='trend_lifecycle_state', timestamp=bars[2].timestamp.isoformat(),
        bar_index=2, available_index=2, trend_level=2, source_path='path', direction='up',
        source_event='翻空为多', state='空翻多', origin_index=1, flip_index=2,
        flip_known_index=2, flip_confirmed=True)]
    publisher.observe(bars, day, result)
    updated = published[-1]['theory']['secondary_trends']
    assert updated['observation_strokes'] == []
    assert updated['strokes'] == preview['strokes'], 'flip confirmation changes state, not connectivity'
    assert [point['index'] for point in updated['strokes'][0]['points']] == [0, 1, 2, 3]
    assert preview['observation_strokes'], 'a later confirmation must not rewrite an older snapshot'


def test_preview_preserves_secondary_continuity_without_authorizing_tertiary_promotion():
    from copy import deepcopy

    bars = tuple(Bar(datetime(2026, 1, 1) + timedelta(days=i), 'TEST', 10, 12, 9, 11, 100)
                 for i in range(4))
    points = [dict(index=i, ordinal=0, time=bars[i].timestamp.date().isoformat(),
                   available_at=bars[i].timestamp.date().isoformat(), kind=kind, value=price)
              for i, (kind, price) in enumerate([('H', 12), ('L', 9), ('H', 11), ('L', 10)])]
    scope = 'structural_trend_points_with_independent_strategy_permissions'
    secondary = dict(scope=scope, strokes=[dict(id='secondary-path', source_path='first-path',
                                              points=points)], developing_strokes=[])
    tertiary = dict(scope=scope, strokes=[dict(id='tertiary-path', source_path='secondary-path',
                                             points=deepcopy(points[1:3]))], developing_strokes=[])
    points[2]['lifecycle_context'] = dict(direction='up', key=dict(points[0]))
    day = MarketDay(ChartDrawingFrame(3, 0, [], dict(strokes=[])),
                    ({'strokes': []}, secondary, tertiary), (), (False, False, False))
    own_flip = dict(event='trend_lifecycle_state', timestamp=bars[2].timestamp.isoformat(),
                    bar_index=2, available_index=2, trend_level=3, source_path='tertiary-path',
                    direction='up', source_event='翻空为多', state='空翻多', origin_index=1,
                    flip_index=2, flip_known_index=2, flip_confirmed=True)
    result = dict(signals=[], orders=[], trades=[], audit=[own_flip],
                  equity=[dict(timestamp=bar.timestamp.isoformat(), equity=100) for bar in bars])
    published = []
    publisher = BacktestPreviewPublisher(lambda _, value: published.append(value))

    publisher.observe(bars, day, result)
    before = published[-1]['theory']
    assert before['secondary_trends']['strokes'] == secondary['strokes']
    assert [point['index'] for point in before['tertiary_trends']['strokes'][0]['points']] == [1, 2]
    assert before['tertiary_trends']['observation_strokes'][0]['reason'] == 'source_level_bull_flip_unconfirmed'

    result['audit'].append(dict(own_flip, trend_level=2, source_path='secondary-path',
                               special_bull_recovery=True, source_event='短暂跌破后同级再次空翻多'))
    publisher.observe(bars, day, result)
    after = published[-1]['theory']
    assert after['secondary_trends']['strokes'] == before['secondary_trends']['strokes']
    assert after['secondary_trends']['observation_strokes'] == []
    assert [point['index'] for point in after['tertiary_trends']['strokes'][0]['points']] == [1, 2]
    assert [point['index'] for point in before['tertiary_trends']['strokes'][0]['points']] == [1, 2]


def test_preview_and_completed_projection_keep_third_level_tail_connected_during_advance():
    from copy import deepcopy

    from wavequant.domain.market_structure.trend_display import publish_trend_display

    bars = tuple(Bar(datetime(2026, 1, 1) + timedelta(days=i), 'TEST', 10, 12, 9, 11, 100)
                 for i in range(5))
    points = [dict(index=i, ordinal=0, time=bars[i].timestamp.date().isoformat(),
                   available_at=bars[i].timestamp.date().isoformat(), kind=kind, value=price)
              for i, (kind, price) in enumerate([('H', 12), ('L', 9), ('H', 11), ('L', 8), ('H', 13)])]
    scope = 'structural_trend_points_with_independent_strategy_permissions'
    published = []
    publisher = BacktestPreviewPublisher(lambda _, value: published.append(value))
    for index in (3, 4):
        source_points = deepcopy(points[:index + 1])
        source_points[2]['lifecycle_context'] = dict(direction='up', key=dict(points[0]))
        second = dict(scope=scope, strokes=[dict(id='secondary-path', source_path='first-path',
                                                points=source_points)])
        third = dict(scope=scope, strokes=[dict(id='tertiary-path', source_path='secondary-path',
                                               points=deepcopy(points[:index]))],
                     developing_strokes=[dict(id='third-tail', source_path='secondary-path', display_only=True,
                                               points=deepcopy(points[index - 1:index + 1]))])
        result = dict(signals=[], orders=[], trades=[], audit=[],
                      equity=[dict(timestamp=bar.timestamp.isoformat(), equity=100) for bar in bars[:index + 1]])
        day = MarketDay(ChartDrawingFrame(index, 0, [], dict(strokes=[])),
                        ({'strokes': []}, second, third), (), (False, False, False))
        publisher.observe(bars, day, result)
        preview = published[-1]['theory']['tertiary_trends']
        completed = publish_trend_display(third, [], level=3, asof_index=index, source_row=second)
        assert preview['strokes'] == completed['strokes']
        assert preview['developing_strokes'] == completed['developing_strokes']
        assert len(preview['strokes']) == 1
        assert preview['strokes'][0]['id'] == 'tertiary-path'
        assert preview['strokes'][0]['points'][-1] == preview['developing_strokes'][0]['points'][0]
    first_tail = published[0]['theory']['tertiary_trends']['developing_strokes'][0]
    last_tail = published[-1]['theory']['tertiary_trends']['developing_strokes'][0]
    assert [point['index'] for point in first_tail['points']] == [2, 3]
    assert [point['index'] for point in last_tail['points']] == [3, 4]


def test_preview_value_matches_original_json_types_and_owns_nested_account_values():
    from wavequant_api.application.backtest_preview import _preview_value

    @dataclass
    class Evidence:
        timestamp: datetime
        values: list[object]

    evidence = Evidence(datetime(2026, 1, 2, 15, 30), [True, 1, 1.0, -0.0, None, ('nested', 2)])
    value = {'evidence': evidence, 'non_string_keys': {None: 'empty', 2: False}}

    def original_default(item):
        if isinstance(item, datetime):
            return item.isoformat()
        if is_dataclass(item):
            return asdict(item)
        raise TypeError(type(item).__name__)

    expected = json.loads(json.dumps(value, default=original_default, allow_nan=False))
    actual = _preview_value(value)
    assert actual == expected
    assert json.dumps(actual) == json.dumps(expected)
    assert [type(item) for item in actual['evidence']['values']] == [bool, int, float, float, type(None), list]
    assert math.copysign(1, actual['evidence']['values'][3]) == -1
    evidence.values.append('later')
    assert actual == expected
    with pytest.raises(ValueError):
        _preview_value({'equity': float('nan')})
    with pytest.raises(TypeError):
        _preview_value({'unsupported': object()})


def test_throttled_day_does_not_copy_or_encode_account_before_publication():
    bars = tuple(Bar(datetime(2026, 1, 1) + timedelta(days=index), 'TEST', 10, 12, 9, 11, 100)
                 for index in range(3))
    published = []
    publisher = BacktestPreviewPublisher(lambda revision, value: published.append(value), clock=lambda: 0.0)
    result = dict(signals=[], orders=[], trades=[], audit=[], backtest={},
                  equity=[dict(timestamp=bars[0].timestamp.isoformat(), equity=100)])
    day = MarketDay(ChartDrawingFrame(0, 0, [], {'strokes': []}),
                    ({'strokes': []},) * 3, (), (False, False, False))
    publisher.observe(bars, day, result)
    skipped = MarketDay(ChartDrawingFrame(1, 0, [], {'strokes': []}), day.levels, (), (False, False, False))
    publisher.observe(bars, skipped, {'unsupported': object()})
    assert len(published) == 1
    assert published[0]['processed'] == 1
