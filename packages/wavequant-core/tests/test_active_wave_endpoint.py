"""A later local certificate must not discard an already qualified wave peak."""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
import json
from pathlib import Path

import pytest

from wavequant.domain.market_structure.lecture_drawing import lecture_drawing
from wavequant.domain.market_structure.lecture_trend import reversal_trends
from wavequant.domain.market_structure.secondary_trend import secondary_trends
from wavequant.domain.market_structure.trend_confirmation import qualify_uptrend
from wavequant.domain.market_structure.trend_publication import annotate_published_legs, confirmed_trend_legs, publish_uptrends
from wavequant.domain.models.model import Bar


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
    assert local['trend_confirmation']['available_at'] == ('2024-01-10' if dates else 9)
    assert local['incoming_trend_state'] == 'unconfirmed'
    assert peak['incoming_trend_confirmation']['available_at'] == expected_known
    assert peak['active_trend_confirmation']['origin']['index'] == 1
    waves = confirmed_trend_legs(result)
    assert [(leg['direction'], [p['index'] for p in leg['points']]) for leg in waves] == [('up', [1, 7])]


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
    assert [(leg['direction'], [p['index'] for p in leg['points']]) for leg in waves] == [('up', [1, expected])]
    assert waves[0]['confirmation']['available_at'] == ('2024-01-04' if dates else 3)
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
    assert [(leg['direction'], [p['index'] for p in leg['points']]) for leg in waves] == expected
    if high <= 12:
        assert all(point['index'] not in (9, 11) for point in public)
    else:
        assert waves[1]['confirmation']['available_at'] == ('2024-01-10' if dates else 9)
        assert waves[2]['confirmation']['available_at'] == ('2024-01-12' if dates else 11)
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
    assert [(leg['direction'], [p['index'] for p in leg['points']]) for leg in waves] == [('down', [1, expected])]
    assert waves[0]['confirmation']['available_at'] == ('2024-01-04' if dates else 3)
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
def test_real_secondary_wave_ends_at_august_2022_peak(cutoff):
    raw = json.loads((Path(__file__).parent / 'fixtures/xiangyang_2025_trend_break.json').read_text(encoding='utf-8'))
    bars = [Bar(datetime.fromisoformat(day), raw['symbol'], *prices) for day, *prices in raw['rows'] if day <= cutoff]
    second = secondary_trends(reversal_trends(lecture_drawing(bars), bars), bars)
    stroke = next(s for s in second['strokes'] if any(p['time'] == '2022-04-27' for p in s['points']))
    wave = next(leg for leg in stroke['confirmed_legs'] if leg['direction'] == 'up'
                and leg['points'][0]['time'] == '2022-04-27')
    assert [(p['time'], p['value']) for p in wave['points']] == [('2022-04-27', 4.06), ('2022-08-02', 10.66)]
    assert wave['confirmation']['available_at'] == '2022-06-20'
    low = next(p for p in stroke['points'] if p['time'] == '2022-07-18')
    assert low['incoming_trend_state'] == 'unconfirmed'
    following = next(leg for leg in stroke['confirmed_legs'] if leg['direction'] == 'down'
                     and leg['points'][0]['time'] == '2022-08-02')
    assert following['confirmation']['available_at'] == '2023-04-26'
    assert following['confirmation']['broken_key']['value'] == 4.81


@pytest.mark.parametrize('cutoff', ('2022-08-01', '2022-08-02', '2022-08-22', '2022-08-23'))
def test_real_peak_keeps_its_source_publication_date_in_replay(cutoff):
    raw = json.loads((Path(__file__).parent / 'fixtures/xiangyang_2025_trend_break.json').read_text(encoding='utf-8'))
    bars = [Bar(datetime.fromisoformat(day), raw['symbol'], *prices) for day, *prices in raw['rows'] if day <= cutoff]
    second = secondary_trends(reversal_trends(lecture_drawing(bars), bars), bars)
    peaks = [p for s in second['strokes'] for p in s['points'] if p['time'] == '2022-08-02']
    if cutoff < '2022-08-23':
        assert not peaks
    else:
        assert len(peaks) == 1
        peak = peaks[0]
        assert peak['value'] == 10.66 and peak['available_at'] == '2022-08-23'
        assert peak['incoming_trend_confirmation']['available_at'] == '2022-06-20'
        assert peak['active_trend_confirmation']['origin']['time'] == '2022-04-27'
