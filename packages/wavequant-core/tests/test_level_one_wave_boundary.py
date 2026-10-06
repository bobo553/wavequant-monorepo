"""A wave reversal needs a confirmed retracement after its counter-impulse."""

from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path

import pytest

from wavequant.domain.market_structure.lecture_drawing import lecture_drawing
from wavequant.domain.market_structure.lecture_trend import _wave_reversals, reversal_trends
from wavequant.domain.models.model import Bar


def turns(values, mirrored=False):
    return [dict(index=index, ordinal=0, time=str(index), available_at=str(index + 1),
                 kind=('L' if index % 2 == 0 else 'H') if mirrored else
                      ('H' if index % 2 == 0 else 'L'),
                 value=30 - value if mirrored else value)
            for index, value in enumerate(values)]


@pytest.mark.parametrize('mirrored', [False, True])
def test_minor_counter_swing_keeps_entire_wave_open_until_new_extreme(mirrored):
    source = turns([12, 10, 11.5, 9, 11, 8, 9, 8.5, 9.5, 7.5, 10, 8.5, 10.5, 9], mirrored)
    assert _wave_reversals(source[:9]) == []
    result = _wave_reversals(source)
    assert [(point['index'], point['kind'], point['value']) for point in result] == [
        (9, 'H' if mirrored else 'L', 22.5 if mirrored else 7.5),
    ]
    assert result[0]['confirmed_on_turn'] == 11
    assert result[0]['available_at'] == '12'


@pytest.mark.parametrize('mirrored', [False, True])
def test_equal_counter_high_waits_for_strict_impulse_and_later_retracement(mirrored):
    source = turns([12, 10, 11.5, 9, 11, 8, 9, 8.5, 9, 8.7, 9.5, 9], mirrored)
    assert _wave_reversals(source[:11]) == []
    result = _wave_reversals(source)
    assert len(result) == 1
    assert (result[0]['index'], result[0]['confirmed_on_turn']) == (5, 11)


@pytest.mark.parametrize('mirrored', [False, True])
def test_equal_extreme_keeps_earliest_entire_wave_anchor(mirrored):
    source = turns([12, 10, 11.5, 9, 11, 8, 9, 8, 9.5, 8.5, 10, 9], mirrored)
    result = _wave_reversals(source)
    assert [(point['index'], point['confirmed_on_turn']) for point in result] == [(5, 9)]


@pytest.mark.parametrize('mirrored', [False, True])
def test_confirmed_wave_and_ordered_proof_are_prefix_stable(mirrored):
    source = turns([12, 10, 11.5, 9, 11, 8, 9, 8.5, 9.5, 7.5, 10, 8.5, 10.5,
                    9, 10, 8, 9.5, 7.8, 9, 8.2, 9.7, 8.5], mirrored)
    before = deepcopy(source)
    full = _wave_reversals(source)
    assert full
    for point in full:
        proof = point['confirmed_by']
        highs = sorted((p for p in proof if p['kind'] == 'H'), key=lambda p: p['index'])
        lows = sorted((p for p in proof if p['kind'] == 'L'), key=lambda p: p['index'])
        assert (highs[-1]['index'] < lows[-1]['index']) == (point['wave_direction_after'] == 'up')
    for end in range(len(source) + 1):
        assert _wave_reversals(source[:end]) == [point for point in full if point['confirmed_on_turn'] < end]
    assert source == before


@pytest.fixture(scope='module')
def xiangyang_bars():
    raw = json.loads((Path(__file__).parent / 'fixtures/xiangyang_2025_level_one_low.json').read_text(encoding='utf-8'))
    return [Bar(datetime.fromisoformat(day), raw['symbol'], *values) for day, *values in raw['bars']]


def test_real_xiangyang_december_high_connects_to_january_13_low(xiangyang_bars):
    base = lecture_drawing(xiangyang_bars)
    before = deepcopy(base)
    result = reversal_trends(base, xiangyang_bars)
    points = next(stroke['points'] for stroke in result['strokes']
                  if any(point['time'] == '2024-12-30' for point in stroke['points']))
    position = next(index for index, point in enumerate(points) if point['time'] == '2024-12-30')
    assert [(point['time'], point['kind'], point['value']) for point in points[position:position + 2]] == [
        ('2024-12-30', 'H', 7.29), ('2025-01-13', 'L', 5.51),
    ]
    assert not any(point['time'] == '2025-01-07' for point in points)
    assert points[position + 1]['available_at'] == '2025-01-21'
    assert base == before
    raw = json.loads((Path(__file__).parent / 'fixtures/xiangyang_2025_level_one_low.json').read_text(encoding='utf-8'))
    fields = ('index', 'ordinal', 'time', 'kind', 'value', 'state', 'available_at', 'projection_count', 'projection_rank')
    snapshot = [dict(id=stroke['id'], kind=stroke['kind'],
                     points=[{key:point[key] for key in fields} for point in stroke['points']])
                for stroke in result['strokes']]
    assert snapshot == raw['level_one_strokes']


@pytest.mark.parametrize('asof', ['2025-01-10', '2025-01-13', '2025-01-14', '2025-01-20', '2025-01-21'])
def test_real_january_low_is_published_only_after_ordered_retracement_confirms(xiangyang_bars, asof):
    bars = [bar for bar in xiangyang_bars if str(bar.timestamp.date()) <= asof]
    result = reversal_trends(lecture_drawing(bars), bars)
    lows = [point for stroke in result['strokes'] for point in stroke['points']
            if point['kind'] == 'L' and point['time'].startswith('2025-01')]
    assert [(point['time'], point['value']) for point in lows] == (
        [('2025-01-13', 5.51)] if asof >= '2025-01-21' else []
    )


def test_real_confirmed_level_one_points_are_stable_at_every_history_prefix(xiangyang_bars):
    full = reversal_trends(lecture_drawing(xiangyang_bars), xiangyang_bars)
    fields = ('time', 'kind', 'value', 'available_at', 'confirmation_rule')
    for end in range(1, len(xiangyang_bars) + 1):
        bars = xiangyang_bars[:end]
        asof = str(bars[-1].timestamp.date())
        prefix = reversal_trends(lecture_drawing(bars), bars)
        actual = [tuple(point[field] for field in fields)
                  for stroke in prefix['strokes'] for point in stroke['points']]
        expected = [tuple(point[field] for field in fields)
                    for stroke in full['strokes'] for point in stroke['points'] if point['available_at'] <= asof]
        assert actual == expected, asof
