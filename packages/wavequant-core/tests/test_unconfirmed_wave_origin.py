"""Later confirmation must not replace a held whole-wave floor with its local B."""

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
import json
from pathlib import Path
from typing import cast

import pytest

from wavequant.domain.market_structure.lecture_drawing import lecture_drawing
from wavequant.domain.market_structure.lecture_trend import reversal_trends
from wavequant.domain.market_structure.trend_publication import publish_uptrends
from wavequant.domain.models.model import Bar


def _example(*, equal_floor: bool = False, breached: bool = False):
    local_low = 2.0 if equal_floor else 3.0
    rows = [(9, 10, 8, 9), (4, 5, 2, 3), (4, 5, 1.5 if breached else 3, 4),
            (5, 6, 4, 5), (4, 5, local_low, 4), (5, 7, 4, 6.5), (6, 7.2, 5, 7)]
    bars = [Bar(datetime(2026, 1, 1) + timedelta(days=index), 'TEST', *row, 1000)
            for index, row in enumerate(rows)]
    source = [dict(index=index, ordinal=0, kind=kind, value=value,
                   time=bars[index].timestamp.date().isoformat(),
                   available_at=bars[known].timestamp.date().isoformat(), state='confirmed')
              for index, kind, value, known in ((0, 'H', 10.0, 0), (1, 'L', 2.0, 2),
                                               (2, 'H', 5.0, 3), (3, 'H', 6.0, 4),
                                               (4, 'L', local_low, 4), (5, 'H', 7.0, 6))]
    candidates = [dict(source[position], source_turn_position=position,
                       confirmation_rule='ordered_HH_then_HL_or_LL_then_LH_switch',
                       wave_direction_after='up' if source[position]['kind'] == 'L' else 'down')
                  for position in (0, 1, 3, 4, 5)]
    candidates[-2]['available_at'] = bars[6].timestamp.date().isoformat()
    return bars, source, candidates


@pytest.mark.parametrize('equal_floor', [False, True])
def test_confirmed_upturn_preserves_the_earliest_held_wave_floor(equal_floor: bool) -> None:
    bars, source, candidates = _example(equal_floor=equal_floor)
    before = deepcopy((source, candidates))
    published = publish_uptrends(candidates, source, bars, source_level=0)
    low = next(point for point in published if point['kind'] == 'L')
    assert (low['index'], low['value']) == (1, 2.0)
    assert low['available_at'] == '2026-01-07'
    certificate = cast(Mapping[str, object], low['trend_confirmation'])
    assert certificate['confirmation_rule'] == 'strict_same_level_market_key_break'
    assert cast(Mapping[str, object], certificate['origin'])['index'] == 4
    assert cast(Mapping[str, object], certificate['wave_origin'])['index'] == 1
    assert cast(Mapping[str, object], certificate['broken_key'])['index'] == 3
    assert cast(Mapping[str, object], certificate['confirmed_by'])['index'] == 5
    assert (source, candidates) == before


def test_a_breached_old_floor_is_not_carried_into_a_later_confirmation() -> None:
    bars, source, candidates = _example(breached=True)
    published = publish_uptrends(candidates, source, bars, source_level=0)
    low = next(point for point in published if point['kind'] == 'L')
    assert (low['index'], low['value']) == (4, 3.0)
    assert 'wave_origin' not in cast(Mapping[str, object], low['trend_confirmation'])


def test_a_held_floor_does_not_grant_permission_without_a_legal_confirmation() -> None:
    bars, source, candidates = _example()
    before = deepcopy((source, candidates))
    published = publish_uptrends(candidates[:3], source[:4], bars[:5], source_level=0)
    assert [point['kind'] for point in published] == ['H']
    assert (source, candidates) == before


def test_without_market_bars_the_local_certificate_does_not_invent_a_held_floor() -> None:
    _, source, candidates = _example()
    published = publish_uptrends(candidates, source, (), source_level=0)
    low = next(point for point in published if point['kind'] == 'L')
    assert (low['index'], low['value']) == (4, 3.0)
    assert 'wave_origin' not in cast(Mapping[str, object], low['trend_confirmation'])


def test_a_new_published_descent_high_bounds_the_wave_floor_window() -> None:
    bars, source, candidates = _example()
    published = publish_uptrends(candidates[1:], source, bars, source_level=0)
    assert [(point['index'], point['kind']) for point in published] == [(3, 'H'), (4, 'L'), (5, 'H')]
    assert 'wave_origin' not in cast(Mapping[str, object], published[1]['trend_confirmation'])


@pytest.mark.parametrize('source_level', [0, 1, 2])
@pytest.mark.parametrize('indexed_clock', [False, True])
def test_whole_wave_origin_is_shared_by_all_levels_and_availability_clocks(source_level, indexed_clock):
    bars, source, candidates = _example()
    for point in candidates:
        position = point.pop('source_turn_position')
        point['source_turn_position' if source_level == 0 else f'source_level{source_level}_position'] = position
    if indexed_clock:
        for point in source + candidates:
            point['available_at'] = (datetime.fromisoformat(point['available_at']) - bars[0].timestamp).days
    published = publish_uptrends(candidates, source, bars, source_level=source_level)
    low = next(point for point in published if point['kind'] == 'L')
    assert (low['index'], low['value'], low['available_at']) == (1, 2.0, 6 if indexed_clock else '2026-01-07')
    proof = low['trend_confirmation']
    assert proof['origin']['index'] == 4
    assert proof['wave_origin']['index'] == 1


def test_a_new_actual_lower_floor_replaces_the_old_wave_origin():
    bars, source, candidates = _example()
    bars[4] = replace(bars[4], low=1.0)
    source[4]['value'] = candidates[-2]['value'] = 1.0
    low = next(point for point in publish_uptrends(candidates, source, bars, source_level=0) if point['kind'] == 'L')
    assert (low['index'], low['value']) == (4, 1.0)
    assert 'wave_origin' not in low['trend_confirmation']


def test_later_price_breach_does_not_rewrite_a_completed_wave_certificate():
    bars, source, candidates = _example()
    bars[6] = replace(bars[6], low=1.0)
    low = next(point for point in publish_uptrends(candidates, source, bars, source_level=0) if point['kind'] == 'L')
    assert (low['index'], low['value']) == (1, 2.0)
    assert low['trend_confirmation']['confirmed_by']['index'] == 5


def test_real_july_origin_and_confirmation_are_stable_at_every_daily_prefix():
    raw = json.loads((Path(__file__).parent / 'fixtures/xiangyang_2025_trend_break.json').read_text(encoding='utf-8'))
    history = [Bar(datetime.fromisoformat(day), raw['symbol'], *prices)
               for day, *prices in raw['rows'] if day <= '2018-08-10']
    full = reversal_trends(lecture_drawing(history), history)
    fields = ('time', 'kind', 'value', 'available_at', 'trend_confirmation')
    for end, bar in enumerate(history, 1):
        asof = str(bar.timestamp.date())
        if asof < '2018-07-11':
            continue
        bars = history[:end]
        prefix = reversal_trends(lecture_drawing(bars), bars)
        actual = [tuple(point.get(field) for field in fields) for stroke in prefix['strokes'] for point in stroke['points']]
        expected = [tuple(point.get(field) for field in fields) for stroke in full['strokes'] for point in stroke['points']
                    if point['available_at'] <= asof]
        assert actual == expected, asof
        july_lows = [point for stroke in prefix['strokes'] for point in stroke['points']
                     if point['kind'] == 'L' and point['time'].startswith('2018-07')]
        assert [(point['time'], point['value']) for point in july_lows] == (
            [('2018-07-11', 4.84)] if asof >= '2018-08-01' else []), asof


def test_real_xiangyang_july_rally_uses_july11_not_the_later_july16_floor() -> None:
    raw = json.loads((Path(__file__).parent / 'fixtures/xiangyang_2025_trend_break.json').read_text(encoding='utf-8'))
    bars = [Bar(datetime.fromisoformat(day), raw['symbol'], *prices)
            for day, *prices in raw['rows'] if day <= '2018-08-10']
    drawing = lecture_drawing(bars)
    before = deepcopy(drawing)
    first = reversal_trends(drawing, bars)
    points = [point for stroke in first['strokes'] for point in stroke['points']]
    july_lows = [point for point in points if point['kind'] == 'L' and point['time'].startswith('2018-07')]
    assert [(point['time'], point['value'], point['available_at']) for point in july_lows] == [
        ('2018-07-11', 4.84, '2018-08-01'),
    ]
    certificate = july_lows[0]['trend_confirmation']
    assert (certificate['origin']['time'], certificate['origin']['value']) == ('2018-07-16', 5.0)
    assert (certificate['wave_origin']['time'], certificate['wave_origin']['value']) == ('2018-07-11', 4.84)
    assert certificate['broken_key']['time'] == '2018-07-13'
    assert certificate['broken_key']['available_at'] == '2018-07-23'
    assert certificate['available_at'] == certificate['confirmed_by']['time'] == '2018-07-26'
    assert [(point['time'], point['value']) for point in points if point['kind'] == 'H'
            and point['time'].startswith('2018-07')] == [('2018-07-03', 6.85), ('2018-07-30', 6.66)]
    assert drawing == before
