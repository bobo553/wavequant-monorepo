"""A lower-level pullback cannot invent a four-point higher-level reversal."""

import copy
from datetime import datetime
import json
from pathlib import Path

import pytest

from tests.test_hierarchical_confirmation import setup
from wavequant.domain.market_structure.hierarchical_development import hierarchical_developing_path
from wavequant.domain.market_structure.lecture_drawing import lecture_drawing
from wavequant.domain.market_structure.lecture_trend import reversal_trends
from wavequant.domain.market_structure.secondary_trend import secondary_trends
from wavequant.domain.market_structure.tertiary_trend import tertiary_trends
from wavequant.domain.models.model import Bar


def countertrend_fixture(level, rising=True):
    bars, points = setup(rising)
    bar = bars[1]
    bars[1] = Bar(bar.timestamp, bar.symbol, bar.open, 17 if not rising else bar.high,
                  3 if rising else bar.low, bar.close, bar.volume)
    points[1]['value'] = 3 if rising else 17
    for index, kind, value in [(2, 'H', 9), (3, 'L', 6)]:
        bar = bars[index]
        bars[index] = Bar(bar.timestamp, bar.symbol, bar.open, 14 if not rising and index == 3 else bar.high,
                          6 if rising and index == 3 else bar.low, bar.close, bar.volume)
        points.append(dict(index=index, ordinal=0, time=bar.timestamp.date().isoformat(),
                           kind=kind if rising else ('L' if kind == 'H' else 'H'),
                           value=value if rising else 20 - value, label=kind,
                           available_at=bar.timestamp.date().isoformat()))
    values = [(9, 10, 8, 9), (8, 9, 7, 8), (9, 10, 8, 9),
              (9, 10, 8, 9), (8, 9, 6.5, 8), (8, 9, 7, 8)]
    if not rising:
        values = [tuple(20 - value for value in (op, lo, hi, cl)) for op, hi, lo, cl in values]
    bars.extend(Bar(datetime(2025, 1, index + 6), 'TEST', *row, 100)
                for index, row in enumerate(values))
    for index, kind, value, known_day in [(4, 'H', 11, 6), (6, 'L', 7, 8),
                                         (8, 'H', 10, 10), (9, 'L', 6.5, 11)]:
        points.append(dict(index=index, ordinal=0, time=bars[index].timestamp.date().isoformat(),
                           kind=kind if rising else ('L' if kind == 'H' else 'H'),
                           value=value if rising else 20 - value, label=kind,
                           available_at=f'2025-01-{known_day:02d}'))
    anchor = dict(points[0], **{f'source_level{level-1}_position': 0, f'confirmed_on_level{level-1}': 1},
                  wave_direction_after='down' if rising else 'up')
    return bars, dict(id='source', points=points), [anchor]


def developing(bars, source, formal, level, end_index=None):
    return hierarchical_developing_path(source, formal, trend_level=level, source_level=level-1,
                                       kind='secondary' if level == 2 else 'tertiary',
                                       bars=bars, end_index=end_index)


@pytest.mark.parametrize('level', [2, 3])
@pytest.mark.parametrize('rising', [True, False])
def test_local_four_point_decline_is_not_a_turn_until_the_frozen_source_key_breaks(level, rising):
    bars, source, formal = countertrend_fixture(level, rising)
    before = copy.deepcopy(source)
    pending = developing(bars[:10], source, formal, level)
    assert len(pending['points']) == 3
    assert pending['points'][-1] == pending['confirmed_endpoint']
    assert 'countertrend_confirmation' not in pending
    # Local lower highs/lows still hold the source trend's frozen 6/14 key.
    assert developing(bars, source, formal, level)['points'][-1]['value'] == (11 if rising else 9)
    bars.append(Bar(datetime(2025, 1, 12), 'TEST', 8 if rising else 12, 9 if rising else 14.5,
                    5.5 if rising else 11, 8 if rising else 12, 100))
    confirmed = developing(bars, source, formal, level)
    assert len(confirmed['points']) == 6
    assert confirmed['countertrend_confirmation']['available_at'] == '2025-01-12'
    assert confirmed['countertrend_confirmation']['direction'] == ('down' if rising else 'up')
    assert all(point['available_at'] >= '2025-01-12' for point in confirmed['points'][3:])
    assert all(point['edge_state'] == 'developing' for point in confirmed['points'][3:])
    assert confirmed['confirmation'] == pending['confirmation']
    extended_bars = bars + [Bar(datetime(2025, 1, 13), 'TEST', 8, 9, 7, 8, 100)]
    if not rising:
        extended_bars[-1] = Bar(datetime(2025, 1, 13), 'TEST', 12, 13, 11, 12, 100)
    extended = developing(extended_bars, source, formal, level)
    assert extended['countertrend_confirmation'] == confirmed['countertrend_confirmation']
    assert source == before


@pytest.mark.parametrize('level', [2, 3])
@pytest.mark.parametrize('boundary', [
    'touch', 'held', 'unknown_key', 'unknown_origin', 'developing', 'developing_origin', 'path_cutoff',
])
def test_source_trend_key_touch_unknown_or_out_of_path_evidence_cannot_start_dashed_edges(level, boundary):
    bars, source, formal = countertrend_fixture(level)
    bars.append(Bar(datetime(2025, 1, 12), 'TEST', 8, 9, 5.5, 8, 100))
    if boundary in ('touch', 'held'):
        bar = bars[-1]
        bars[-1] = Bar(bar.timestamp, bar.symbol, bar.open, bar.high,
                       6 if boundary == 'touch' else 6.25, bar.close, bar.volume)
    elif boundary == 'unknown_key':
        source['points'][3]['available_at'] = '2025-01-13'
    elif boundary == 'unknown_origin':
        source['points'][4]['available_at'] = '2025-01-13'
    elif boundary == 'developing':
        source['points'][3]['state'] = 'developing'
    elif boundary == 'developing_origin':
        source['points'][4]['state'] = 'developing'
    result = developing(bars, source, formal, level, 10 if boundary == 'path_cutoff' else None)
    assert len(result['points']) == 3
    assert 'countertrend_confirmation' not in result


@pytest.mark.parametrize('level', [2, 3])
@pytest.mark.parametrize('rising', [True, False])
def test_same_level_market_key_break_confirms_without_waiting_for_four_source_turns(level, rising):
    bars, source, formal = countertrend_fixture(level, rising)
    source['points'] = source['points'][:5]
    bar = bars[-1]
    bars[-1] = Bar(bar.timestamp, bar.symbol, bar.open, 18 if not rising else bar.high,
                   2 if rising else bar.low, bar.close, bar.volume)
    base = dict(source['points'][1], **{f'source_level{level-1}_position': 1,
                                      f'confirmed_on_level{level-1}': 2},
                wave_direction_after='up' if rising else 'down')
    result = developing(bars, source, formal + [base], level)
    assert result['state'] == 'confirmed'
    assert result['confirmation']['direction'] == ('down' if rising else 'up')
    assert result['confirmation']['available_at'] == '2025-01-11'
    assert result['confirmed_endpoint']['value'] == (2 if rising else 18)


def test_xiangyang_may2025_to_april2026_has_no_tertiary_countertrend_dashes():
    directory = Path(__file__).parent / 'fixtures'
    raw = json.loads((directory / 'xiangyang_2025_trend_break.json').read_text(encoding='utf-8'))
    extension = json.loads((directory / 'xiangyang_2026_countertrend.json').read_text(encoding='utf-8'))
    rows = raw['rows'] + extension['rows']
    bars = [Bar(datetime.fromisoformat(date), raw['symbol'], op, hi, lo, cl, volume)
            for date, op, hi, lo, cl, volume in rows]
    last = next(bar for bar in bars if bar.timestamp.date().isoformat() == '2026-04-08')
    assert (last.open, last.high, last.low, last.close, last.volume) == (13.55, 14.44, 13.3, 13.67, 57682054)
    proof = None
    for end in [row[0] for row in rows if row[0] >= '2025-05-15']:
        prefix = [bar for bar in bars if bar.timestamp.date().isoformat() <= end]
        first = reversal_trends(lecture_drawing(prefix), prefix)
        second = secondary_trends(first, prefix)
        third = tertiary_trends(second, prefix)
        tail = third['developing_strokes'][-1]
        assert tail['state'] == 'confirmed'
        assert tail['confirmation']['available_at'] == '2025-03-11'
        assert tail['confirmed_endpoint']['time'] == '2025-05-15'
        assert tail['confirmed_endpoint']['value'] == 19.63
        assert tail['points'][-1] == tail['confirmed_endpoint']
        assert not any(point.get('edge_state') == 'developing' for point in tail['points'])
        assert 'countertrend_confirmation' not in tail
        if proof is not None:
            assert tail['confirmation'] == proof
        proof = tail['confirmation']
