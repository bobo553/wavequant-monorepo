"""A later local certificate must not discard an already qualified wave peak."""

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
import json
from pathlib import Path
from typing import cast

import pytest

from wavequant.domain.market_structure.lecture_drawing import lecture_drawing
from wavequant.domain.market_structure.lecture_trend import reversal_trends
from wavequant.domain.market_structure.secondary_trend import secondary_trends
from wavequant.domain.market_structure.trend_lines import polyline_high_low_points
from wavequant.domain.market_structure.trend_levels import completed_source_points
from wavequant.domain.market_structure.trend_confirmation import qualify_uptrend
from wavequant.domain.market_structure.trend_publication import annotate_published_legs, confirmed_trend_legs, publish_uptrends
from wavequant.domain.models.model import Bar


def mapping(value: object) -> Mapping[str, object]:
    """断言证据对象边界，保留原字段和引用，仅供测试读取。"""
    assert isinstance(value, Mapping) and all(isinstance(key, str) for key in value)
    return cast(Mapping[str, object], value)


def mappings(value: object) -> Sequence[Mapping[str, object]]:
    assert isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))
    return tuple(mapping(item) for item in value)


def price(value: object) -> float:
    assert isinstance(value, (int, float)) and not isinstance(value, bool)
    return float(value)


def sample(level, dates, first_known=2):
    prices = [(10, 8, 9), (7, 5, 6), (9, 6, 8), (11, 8, 10.5), (10.8, 9, 10.2),
              (9.5, 8, 9), (10.5, 8.5, 10), (12, 9, 11.5), (11.8, 9.5, 11.3),
              (11.5, 8.2, 11.1), (10.5, 8.4, 9), (10.8, 8.6, 10.2), (10.6, 8.8, 10)]
    bars = [Bar(datetime(2024, 1, 1) + timedelta(days=i), 'TEST', (high + low) / 2, high, low, close, 100)
            for i, (high, low, close) in enumerate(prices)]
    source = [dict(index=i, kind=kind, value=value, ordinal=0,
                   time=bars[i].timestamp.date().isoformat(),
                   available_at=bars[known].timestamp.date().isoformat() if dates else known,
                   state='confirmed', label=kind)
              for i, kind, value, known in [(0, 'H', 10, 0), (1, 'L', 5, first_known), (3, 'H', 11, 4),
                                           (5, 'L', 8, 9), (7, 'H', 12, 9), (11, 'H', 10.8, 12)]]
    field = 'source_turn_position' if level == 1 else f'source_level{level - 1}_position'
    candidates = [dict(p, **{field: i}, confirmation_rule='structure') for i, p in enumerate(source)]
    return bars, source, candidates


@pytest.mark.parametrize('level', (1, 2, 3))
@pytest.mark.parametrize('dates', (False, True))
def test_existing_wave_keeps_peak_before_a_later_local_trigger(level, dates):
    bars, source, candidates = sample(level, dates)
    public = publish_uptrends(candidates, source, bars, source_level=level - 1)
    result = annotate_published_legs(public, source, bars, source_level=level - 1)
    peak = next(p for p in result if p['index'] == 7)
    local = next(p for p in result if p['index'] == 5)
    expected_known = '2024-01-04' if dates else 3
    assert mapping(local['trend_confirmation'])['available_at'] == ('2024-01-10' if dates else 9)
    assert local['incoming_trend_state'] == 'unconfirmed'
    assert mapping(peak['incoming_trend_confirmation'])['available_at'] == expected_known
    assert mapping(mapping(peak['active_trend_confirmation'])['origin'])['index'] == 1
    waves = confirmed_trend_legs(result)
    assert [(leg['direction'], [p['index'] for p in mappings(leg['points'])]) for leg in waves] == [('up', [1, 7])]


@pytest.mark.parametrize('level', (1, 2, 3))
@pytest.mark.parametrize('dates', (False, True))
def test_first_late_confirmation_still_cannot_backfill_an_earlier_peak(level, dates):
    bars, source, candidates = sample(level, dates, first_known=9)
    result = publish_uptrends(candidates, source, bars, source_level=level - 1)
    assert not any(p['kind'] == 'H' and p['index'] in (3, 7) for p in result)


@pytest.mark.parametrize('level', (1, 2, 3))
@pytest.mark.parametrize('dates', (False, True))
@pytest.mark.parametrize('high,expected', ((10.8, 7), (12, 7), (13, 11)))
def test_lower_or_equal_peak_preserves_first_extreme_and_higher_peak_extends(level, dates, high, expected):
    bars, source, candidates = sample(level, dates)
    bars[11] = replace(bars[11], high=high)
    source[-1]['value'] = candidates[-1]['value'] = high
    before = deepcopy((source, candidates))
    result = publish_uptrends(candidates, source, bars, source_level=level - 1)
    waves = confirmed_trend_legs(result)
    assert [(leg['direction'], [p['index'] for p in mappings(leg['points'])]) for leg in waves] == [('up', [1, expected])]
    assert mapping(waves[0]['confirmation'])['available_at'] == ('2024-01-04' if dates else 3)
    assert (source, candidates) == before


def reverse_sample(level, dates, high):
    values = [(10, 8, 9), (7, 5, 6), (9, 6, 8), (11, 8, 10.5), (10.8, 9, 10.2),
              (9.5, 8, 9), (10.5, 8.5, 10), (12, 9, 11.5), (11.8, 9.5, 11.3),
              (11, 7, 7.5), (10, 8, 9), (high, 8, 9.5), (max(10, high - .1), 8, 9.2)]
    bars = [Bar(datetime(2024, 1, 1) + timedelta(days=i), 'TEST', (h + low) / 2, h, low, close, 100)
            for i, (h, low, close) in enumerate(values)]
    source = [dict(index=i, kind=kind, value=value, ordinal=0,
                   time=bars[i].timestamp.date().isoformat(),
                   available_at=bars[known].timestamp.date().isoformat() if dates else known,
                   state='confirmed', label=kind)
              for i, kind, value, known in [(0, 'H', 10, 0), (1, 'L', 5, 2), (3, 'H', 11, 4),
                                           (5, 'L', 8, 6), (7, 'H', 12, 8), (9, 'L', 7, 10), (11, 'H', high, 12)]]
    field = 'source_turn_position' if level == 1 else f'source_level{level - 1}_position'
    candidates = [dict(p, **{field: i}, confirmation_rule='structure') for i, p in enumerate(source)]
    return bars, source, candidates


@pytest.mark.parametrize('level', (1, 2, 3))
@pytest.mark.parametrize('dates', (False, True))
@pytest.mark.parametrize('high', (10.8, 12, 13))
def test_real_reverse_ends_old_wave_even_if_its_floor_holds_and_the_low_stays_private(level, dates, high):
    bars, source, candidates = reverse_sample(level, dates, high)
    before = deepcopy((source, candidates))
    public = publish_uptrends(candidates, source, bars, source_level=level - 1)
    waves = confirmed_trend_legs(public)
    assert min(bar.low for bar in bars[1:]) == 5
    expected = [('up', [1, 7]), ('down', [7, 9]), ('up', [9, 11])] if high > 12 else [('up', [1, 7])]
    assert [(leg['direction'], [p['index'] for p in mappings(leg['points'])]) for leg in waves] == expected
    if high <= 12:
        assert all(point['index'] not in (9, 11) for point in public)
    else:
        assert mapping(waves[1]['confirmation'])['available_at'] == ('2024-01-10' if dates else 9)
        assert mapping(waves[2]['confirmation'])['available_at'] == ('2024-01-12' if dates else 11)
    assert (source, candidates) == before


def mirror(bars, source, points):
    bars = [replace(bar, open=20 - bar.open, high=20 - bar.low, low=20 - bar.high, close=20 - bar.close)
            for bar in bars]
    for entries in (source, points):
        for point in entries:
            point['kind'] = 'L' if point['kind'] == 'H' else 'H'
            point['value'] = 20 - point['value']
    return bars


@pytest.mark.parametrize('level', (1, 2, 3))
@pytest.mark.parametrize('dates', (False, True))
@pytest.mark.parametrize('high,expected', ((10.8, 7), (12, 7), (13, 11)))
def test_downward_mirror_keeps_low_or_extends_lower_with_original_confirmation(level, dates, high, expected):
    bars, source, points = sample(level, dates)
    bars[11] = replace(bars[11], high=high)
    source[-1]['value'] = points[-1]['value'] = high
    bars = mirror(bars, source, points)
    before = deepcopy((source, points))
    result = annotate_published_legs(points, source, bars, source_level=level - 1)
    waves = confirmed_trend_legs(result)
    assert [(leg['direction'], [p['index'] for p in mappings(leg['points'])]) for leg in waves] == [('down', [1, expected])]
    assert mapping(waves[0]['confirmation'])['available_at'] == ('2024-01-04' if dates else 3)
    assert (source, points) == before


@pytest.mark.parametrize('level', (1, 2, 3))
@pytest.mark.parametrize('dates', (False, True))
@pytest.mark.parametrize('direction', ('up', 'down'))
@pytest.mark.parametrize('unavailable', ('outside_history', 'before_price'))
def test_existing_proof_cannot_bypass_missing_or_premature_publication_cutoff(level, dates, direction, unavailable):
    bars, source, points = sample(level, dates)
    points = points[:5]
    if direction == 'up':
        first = qualify_uptrend(source, source[1], 1, source[0], bars, len(bars) - 1)
        later = qualify_uptrend(source, source[3], 3, source[2], bars, len(bars) - 1)
        assert first is not None and later is not None
        points[1]['trend_confirmation'] = first
        points[2]['incoming_trend_confirmation'] = first
        points[3]['trend_confirmation'] = later
        points[4]['incoming_trend_confirmation'] = later
    else:
        bars = mirror(bars, source, points)
    known = 999 if unavailable == 'outside_history' else 6
    points[-1]['available_at'] = ('2099-01-01' if known == 999 else '2024-01-07') if dates else known
    result = annotate_published_legs(points, source, bars, source_level=level - 1)
    assert result[-1]['incoming_trend_state'] == 'unconfirmed'
    assert result[-1].get('incoming_trend_confirmation') is None


@pytest.mark.parametrize('cutoff', ('2023-07-04', '2024-03-01', '2025-05-15'))
def test_real_secondary_wave_preserves_its_same_path_peak_and_independent_descent(cutoff):
    raw = json.loads((Path(__file__).parent / 'fixtures/xiangyang_2025_trend_break.json').read_text(encoding='utf-8'))
    bars = [Bar(datetime.fromisoformat(day), raw['symbol'], *prices) for day, *prices in raw['rows'] if day <= cutoff]
    drawing = lecture_drawing(bars)
    first = reversal_trends(drawing, bars)
    second = secondary_trends(first, bars)
    geometry = next(s for s in mappings(second['strokes'])
                    if any(p['time'] == '2022-04-27' for p in mappings(s['points'])))
    stroke = next(s for s in mappings(second['qualified_strokes']) if s['id'] == geometry['id'])

    # 二级必须从当时可知的一级高低点判断，不能用后来行情许可筛选一级来源。
    # 两高、两低同向分别确认上涨和下降；各证据的来源可知日仍独立保留。
    candidates = mappings(next(s['points'] for s in mappings(second['candidate_strokes']) if s['id'] == geometry['id']))
    low_definition = next(p for p in candidates if p['time'] == '2022-04-27'
                          and p['confirmation_rule'] == 'ordered_HH_then_HL_or_LL_then_LH_switch')
    high_definition = next(p for p in candidates if p['time'] == '2022-08-02'
                           and p['confirmation_rule'] == 'ordered_HH_then_HL_or_LL_then_LH_switch')
    assert (low_definition['available_at'], low_definition['wave_completed_at']) == ('2022-07-04', '2022-07-04')
    assert [(p['time'], p['kind'], p['value'], p['available_at'])
            for p in mappings(low_definition['definition_evidence'])] == [
        ('2022-06-02', 'H', 5.31, '2022-06-09'), ('2022-06-24', 'H', 6.67, '2022-07-04'),
        ('2022-04-27', 'L', 4.06, '2022-05-13'), ('2022-06-14', 'L', 4.42, '2022-06-16'),
    ]
    assert (high_definition['available_at'], high_definition['wave_completed_at']) == ('2022-09-13', '2022-09-13')
    assert [(p['time'], p['kind'], p['value'], p['available_at'])
            for p in mappings(high_definition['definition_evidence'])] == [
        ('2022-08-02', 'H', 10.66, '2022-08-09'), ('2022-08-18', 'H', 8.16, '2022-08-24'),
        ('2022-08-12', 'L', 6.77, '2022-08-23'), ('2022-09-01', 'L', 6.08, '2022-09-13'),
    ]
    source_stroke = next(s for s in mappings(first['strokes']) if s['id'] == geometry['source_path'])
    source_points = mappings(next(s['points'] for s in mappings(second['source_strokes'])
                                  if s['id'] == geometry['source_path']))
    for definition in (low_definition, high_definition):
        assert definition['wave_identity_state'] == 'owned'
        for reference in mappings(definition['definition_evidence']):
            actual = next(p for p in source_points if (p['index'], p.get('ordinal', 0)) ==
                          (reference['index'], reference.get('ordinal', 0)))
            assert tuple(actual[field] for field in ('kind', 'value', 'available_at')) == tuple(
                reference[field] for field in ('kind', 'value', 'available_at'))
            assert actual['wave_identity_state'] == 'owned'
            assert actual['wave_completed_at'] == reference['available_at']

    wave = next(leg for leg in mappings(stroke['confirmed_legs']) if leg['direction'] == 'up'
                and mappings(leg['points'])[0]['time'] == '2022-04-27')
    assert [(p['time'], p['value']) for p in mappings(wave['points'])] == [('2022-04-27', 4.06), ('2022-08-02', 10.66)]
    proof = mapping(wave['confirmation'])
    assert proof['confirmation_rule'] == 'strict_same_level_market_key_break'
    assert proof['available_at'] == '2022-06-20'
    origin, key, trigger = (mapping(proof[field]) for field in ('origin', 'broken_key', 'confirmed_by'))
    wave_origin = mapping(proof.get('wave_origin', origin))
    assert (origin['time'], origin['value'], origin['available_at']) == (
        '2022-04-27', 4.06, '2022-05-13')
    assert (key['time'], key['value'], key['available_at']) == (
        '2022-03-24', 5.45, '2022-05-13')
    assert (trigger['time'], trigger['value']) == ('2022-06-20', 5.79)
    assert price(trigger['value']) > price(key['value'])
    assert (wave_origin['time'], wave_origin['value'], wave_origin['available_at']) == (
        '2022-04-27', 4.06, '2022-05-13')
    assert mappings(wave['points'])[0]['available_at'] == '2022-05-13'
    assert mappings(wave['points'])[-1]['available_at'] == '2022-08-09'
    # 连续路径保留较高的旧二级压力。6/15只触及5.45，一级5.31峰不能
    # 越级替代旧key，未完成的一级高点也不能用既有行情许可证提前发布。
    assert not any(p['time'] == '2022-06-02' for p in candidates)
    assert not any(p['time'] == '2022-06-14' for p in mappings(geometry['points']))
    dates = {bar.timestamp.date().isoformat(): index for index, bar in enumerate(bars)}
    source_origin = next(p for p in source_points if p['time'] == '2022-04-27')
    origin_position = source_points.index(source_origin)
    assert qualify_uptrend(source_points, source_origin, origin_position, key, bars, dates['2022-06-15']) is None
    assert qualify_uptrend(source_points, source_origin, origin_position, key, bars, dates['2022-06-20']) == proof

    # 下级局部反转不改写二级整波起点；真正独立的下降许可却必须
    # 终止该上涨。下降的旧低点已于9/27可知，9/29低价严格跌破后才取得证书。
    low = next(p for p in mappings(stroke['points']) if p['time'] == '2022-10-10')
    assert low['incoming_trend_state'] == 'confirmed'
    following = next(leg for leg in mappings(stroke['confirmed_legs']) if leg['direction'] == 'down'
                     and mappings(leg['points'])[0]['time'] == '2022-08-02')
    assert [(p['time'], p['value']) for p in mappings(following['points'])] == [('2022-08-02', 10.66), ('2022-10-10', 5.29)]
    down = mapping(following['confirmation'])
    assert down['direction'] == 'down' and down['confirmation_rule'] == 'strict_same_level_market_key_break'
    assert down['available_at'] == '2022-09-29'
    origin, key, trigger = (mapping(down[field]) for field in ('origin', 'broken_key', 'confirmed_by'))
    assert (origin['time'], origin['value'], origin['available_at']) == (
        '2022-09-20', 7.65, '2022-09-27')
    assert (mapping(down['wave_origin'])['time'], mapping(down['wave_origin'])['value']) == ('2022-08-02', 10.66)
    assert (key['time'], key['value'], key['available_at']) == (
        '2022-09-01', 6.08, '2022-09-27')
    assert (trigger['time'], trigger['value']) == ('2022-09-29', 5.79)
    assert price(trigger['value']) < price(key['value'])
    assert low['incoming_trend_confirmation'] == down
    assert low['active_trend_confirmation'] == down

    # 7/29外包十字星在已确认对向边界内只延伸上行，8/2高点属于原路径。
    # 零级8/3反转、一级8/9闭合、二级9/13闭合分别保留，不能同日越级。
    assert not any(issue['time'] == '2022-07-29' for issue in drawing['issues'])
    assert not any('source_path_end_index' in p for p in mappings(source_stroke['points']))
    august_source = next(p for p in polyline_high_low_points(drawing)
                         if p['time'] == '2022-08-02' and p['kind'] == 'H')
    assert (august_source['value'], august_source['available_at'], august_source['source_path']) == (
        10.66, '2022-08-03', source_stroke['source_path'])
    first_peak = next(p for p in mappings(source_stroke['points']) if p['time'] == '2022-08-02')
    assert (first_peak['available_at'], first_peak['wave_completed_at']) == ('2022-08-03', '2022-08-09')
    second_peak = next(p for p in mappings(geometry['points']) if p['time'] == '2022-08-02')
    assert (second_peak['available_at'], second_peak['wave_completed_at']) == ('2022-08-09', '2022-09-13')


@pytest.mark.parametrize('cutoff', (
    '2022-06-23', '2022-06-24', '2022-06-27', '2022-06-28', '2022-07-01', '2022-07-04',
))
def test_real_same_path_peak_waits_for_its_source_day_despite_an_earlier_wave_certificate(cutoff):
    raw = json.loads((Path(__file__).parent / 'fixtures/xiangyang_2025_trend_break.json').read_text(encoding='utf-8'))
    bars = [Bar(datetime.fromisoformat(day), raw['symbol'], *prices) for day, *prices in raw['rows'] if day <= cutoff]
    first = reversal_trends(lecture_drawing(bars), bars)
    second = secondary_trends(first, bars)
    source_peaks = [p for s in mappings(first['strokes']) for p in mappings(s['points']) if p['time'] == '2022-06-24']
    peaks = [p for s in mappings(second['strokes']) for p in mappings(s['points']) if p['time'] == '2022-06-24']
    qualified_peaks = [p for s in mappings(second['qualified_strokes'])
                       for p in mappings(s['points']) if p['time'] == '2022-06-24']
    completed_peaks = [p for s in mappings(second['source_strokes'])
                       for p in mappings(s['points']) if p['time'] == '2022-06-24']
    qualified_origin = next(p for s in mappings(second['qualified_strokes'])
                            for p in mappings(s['points']) if p['time'] == '2022-04-27')
    proof = mapping(qualified_origin['trend_confirmation'])
    assert proof['available_at'] == '2022-06-20'
    assert (mapping(proof['broken_key'])['time'], mapping(proof['broken_key'])['value']) == ('2022-03-24', 5.45)
    waves = [leg for s in mappings(second['qualified_strokes']) for leg in mappings(s['confirmed_legs'])
             if leg['direction'] == 'up' and mappings(leg['points'])[0]['time'] == '2022-04-27']
    # 较早行情许可不替代整波闭合：一级峰6/27仅可绘图，7/4才是二级来源。
    if cutoff < '2022-06-27':
        assert not source_peaks
    else:
        assert len(source_peaks) == 1
        assert (source_peaks[0]['value'], source_peaks[0]['available_at']) == (6.67, '2022-06-27')
    if cutoff < '2022-07-04':
        assert not completed_peaks and not peaks and not qualified_peaks and not waves
        if source_peaks:
            assert 'wave_completed_at' not in source_peaks[0]
            assert completed_source_points(source_peaks, source_level=1) == []
    else:
        assert len(completed_peaks) == len(peaks) == len(qualified_peaks) == len(waves) == 1
        assert source_peaks[0]['wave_completed_at'] == '2022-07-04'
        assert completed_peaks[0]['source_geometry_available_at'] == '2022-06-27'
        assert completed_peaks[0]['available_at'] == peaks[0]['available_at'] == qualified_peaks[0]['available_at'] == (
            '2022-07-04')
        assert completed_peaks[0]['value'] == peaks[0]['value'] == qualified_peaks[0]['value'] == 6.67
        peak = qualified_peaks[0]
        assert mapping(peak['incoming_trend_confirmation'])['available_at'] == '2022-06-20'
        assert mapping(mapping(peak['active_trend_confirmation'])['origin'])['time'] == '2022-04-27'
        assert [(p['time'], p['value']) for p in mappings(waves[0]['points'])] == [('2022-04-27', 4.06), ('2022-06-24', 6.67)]
        assert completed_source_points(peaks, source_level=2) == []
