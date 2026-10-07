"""Strict market breaks confirm direction, while the extreme remains live."""

import copy
from datetime import datetime, timedelta
import json
from pathlib import Path

import pytest

from wavequant.domain.market_structure.hierarchical_confirmation import market_trend_confirmation
from wavequant.domain.market_structure.hierarchical_development import hierarchical_developing_path
from wavequant.domain.market_structure.lecture_drawing import lecture_drawing
from wavequant.domain.market_structure.lecture_trend import reversal_trends
from wavequant.domain.market_structure.secondary_trend import secondary_trends
from wavequant.domain.market_structure.tertiary_trend import tertiary_trends
from wavequant.domain.models.model import Bar


def setup(rising=True):
    values = [(9, 10, 8, 9), (7, 8, 6, 7), (8, 9, 7, 8), (9, 10, 8, 9), (9, 11, 8, 9)]
    if not rising:
        values = [tuple(20 - value for value in (op, lo, hi, cl)) for op, hi, lo, cl in values]
    bars = [Bar(datetime(2025, 1, 1) + timedelta(days=index), 'TEST', *row, 100)
            for index, row in enumerate(values)]
    points = [dict(index=index, ordinal=0, time=bars[index].timestamp.date().isoformat(), kind=kind, value=value,
                   available_at=bars[index].timestamp.date().isoformat(), label=kind)
              for index, kind, value in [(0, 'H' if rising else 'L', 10),
                                         (1, 'L' if rising else 'H', 6 if rising else 14)]]
    return bars, points


@pytest.mark.parametrize('rising', [True, False])
def test_strict_market_break_confirms_without_close_and_equal_price_does_not(rising):
    bars, points = setup(rising)
    before = copy.deepcopy(points)
    assert market_trend_confirmation(points, points[0], 0, bars, 3) is None
    result = market_trend_confirmation(points, points[0], 0, bars, 4)
    assert result['confirmation']['available_at'] == '2025-01-05'
    assert result['confirmation']['direction'] == ('up' if rising else 'down')
    assert result['confirmation']['confirmed_by']['value'] == (11 if rising else 9)
    assert bars[4].close == (9 if rising else 11)
    assert points == before


@pytest.mark.parametrize('rising', [True, False])
def test_unconfirmed_more_extreme_origin_and_same_bar_double_break_cannot_confirm(rising):
    bars, points = setup(rising)
    bar = bars[-1]
    bars[-1] = Bar(bar.timestamp, bar.symbol, bar.open, 15 if not rising else bar.high,
                   5 if rising else bar.low, bar.close, bar.volume)
    assert market_trend_confirmation(points, points[0], 0, bars, 4) is None


@pytest.mark.parametrize('field', ['key', 'origin'])
def test_unknown_key_or_origin_never_backdates_confirmation(field):
    bars, points = setup()
    points[0 if field == 'key' else 1]['available_at'] = '2025-01-06'
    assert market_trend_confirmation(points, points[0], 0, bars, 4) is None
    bars.append(Bar(datetime(2025, 1, 6), 'TEST', 10, 12, 9, 11, 100))
    assert market_trend_confirmation(points, points[0], 0, bars, 5)['confirmation']['available_at'] == '2025-01-06'


def test_path_boundary_and_prefix_proof_with_extending_endpoint():
    bars, points = setup()
    first = market_trend_confirmation(points, points[0], 0, bars, 4)
    bars.extend([Bar(datetime(2025, 1, 6), 'TEST', 11, 13, 9, 12, 100),
                 Bar(datetime(2025, 1, 7), 'TEST', 11, 13, 9, 12, 100)])
    extended = market_trend_confirmation(points, points[0], 0, bars, 6)
    assert extended['confirmation'] == first['confirmation']
    assert extended['endpoint']['time'] == '2025-01-06'
    assert extended['endpoint']['value'] == 13
    assert market_trend_confirmation(points, points[0], 0, bars, 3) is None


def test_xiangyang_first_break_and_may_endpoint_use_real_full_daily_history():
    raw = json.loads((Path(__file__).parent / 'fixtures/xiangyang_2025_trend_break.json').read_text(encoding='utf-8'))
    bars = [Bar(datetime.fromisoformat(date), raw['symbol'], op, hi, lo, cl, volume)
            for date, op, hi, lo, cl, volume in raw['rows']]
    dates = {bar.timestamp.date().isoformat(): bar for bar in bars}
    assert (dates['2022-08-02'].open, dates['2022-08-02'].high, dates['2022-08-02'].low,
            dates['2022-08-02'].close, dates['2022-08-02'].volume) == (10.18, 10.66, 9.8, 9.91, 107655028)
    assert (dates['2025-05-15'].open, dates['2025-05-15'].high, dates['2025-05-15'].low,
            dates['2025-05-15'].close, dates['2025-05-15'].volume) == (18, 19.63, 16.34, 18.04, 163780356)
    results = {}
    for end in ['2025-03-10', '2025-03-11', '2025-05-15']:
        prefix = [bar for bar in bars if bar.timestamp.date().isoformat() <= end]
        first = reversal_trends(lecture_drawing(prefix), prefix)
        second = secondary_trends(first, prefix)
        results[end] = tertiary_trends(second, prefix)
    before = results['2025-03-10']['developing_strokes'][-1]
    first = results['2025-03-11']['developing_strokes'][-1]
    last = results['2025-05-15']['developing_strokes'][-1]
    assert before['confirmation']['direction'] == 'down'
    assert before['confirmed_endpoint']['value'] == 3.45
    assert first['state'] == last['state'] == 'confirmed'
    assert first['confirmation']['direction'] == 'up'
    assert first['confirmation'] == last['confirmation']
    assert last['confirmation']['available_at'] == '2025-03-11'
    assert last['confirmation']['broken_key']['value'] == 10.66
    assert [(point['time'], point['value']) for point in last['points']] == [
        ('2022-08-02', 10.66), ('2024-07-25', 3.45), ('2025-05-15', 19.63)]
    assert last['endpoint_state'] == last['points'][-1]['state'] == 'developing'
    assert [stroke['points'] for stroke in results['2025-03-11']['strokes']] == [
        stroke['points'] for stroke in results['2025-05-15']['strokes']]
    assert all(point['time'] != '2025-05-15' for stroke in results['2025-05-15']['strokes'] for point in stroke['points'])


@pytest.mark.parametrize('level,kind', [(2, 'secondary'), (3, 'tertiary')])
def test_later_formal_origin_keeps_first_market_confirmation_and_live_endpoint(level, kind):
    bars, points = setup()
    anchor = dict(points[0], **{f'source_level{level-1}_position': 0, f'confirmed_on_level{level-1}': 1},
                  wave_direction_after='down')
    base = dict(points[1], **{f'source_level{level-1}_position': 1, f'confirmed_on_level{level-1}': 2},
                available_at='2025-01-05', wave_direction_after='up')
    points.append(dict(points[0], index=2, time='2025-01-03', available_at='2025-01-03', value=9))
    source = dict(id='source', points=points)
    result = hierarchical_developing_path(source, [anchor, base], trend_level=level, source_level=level-1,
                                          kind=kind, bars=bars)
    assert result['state'] == 'confirmed'
    assert result['confirmation']['available_at'] == '2025-01-05'
    assert result['points'][0]['value'] == 6
    assert result['points'][-1]['value'] == 11
    assert result['points'][-1]['state'] == 'developing'


@pytest.mark.parametrize('level,kind', [(2, 'secondary'), (3, 'tertiary')])
def test_source_evolution_after_the_confirmed_extreme_stays_visible_and_unconfirmed(level, kind):
    bars, points = setup()
    bars.extend([Bar(datetime(2025, 1, 6), 'TEST', 9, 10, 8, 9, 100),
                 Bar(datetime(2025, 1, 7), 'TEST', 8, 9, 7, 8, 100),
                 Bar(datetime(2025, 1, 8), 'TEST', 9, 10, 8, 9, 100),
                 Bar(datetime(2025, 1, 9), 'TEST', 9, 10, 8, 9, 100)])
    points.extend([dict(index=6, ordinal=0, time='2025-01-07', kind='L', value=7,
                        available_at='2025-01-08', label='L2'),
                   dict(index=7, ordinal=0, time='2025-01-08', kind='H', value=10,
                        available_at='2025-01-09', label='H2')])
    anchor = dict(points[0], **{f'source_level{level-1}_position': 0, f'confirmed_on_level{level-1}': 1},
                  wave_direction_after='down')
    result = hierarchical_developing_path(dict(id='source', points=points), [anchor], trend_level=level,
                                          source_level=level-1, kind=kind, bars=bars)
    assert [(point['kind'], point['value']) for point in result['points']] == [
        ('H', 10), ('L', 6), ('H', 11), ('L', 7), ('H', 10)]
    assert result['confirmed_endpoint']['time'] == '2025-01-05'
    assert all(point['edge_state'] == 'developing' for point in result['points'][3:])
    assert result['confirmation']['available_at'] == '2025-01-05'
