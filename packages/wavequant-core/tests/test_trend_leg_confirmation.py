"""Two qualified vertices do not automatically qualify the leg between them."""

from copy import deepcopy
from datetime import datetime, timedelta
import json
from pathlib import Path

import pytest

from wavequant.domain.market_structure.lecture_drawing import lecture_drawing
from wavequant.domain.market_structure.hierarchical_development import hierarchical_developing_path
from wavequant.domain.market_structure.lecture_trend import reversal_trends
from wavequant.domain.market_structure.secondary_trend import secondary_trends
from wavequant.domain.market_structure.trend_confirmation import qualify_downtrend, qualify_uptrend
from wavequant.domain.market_structure.trend_publication import annotate_published_legs, confirmed_trend_legs
from wavequant.domain.models.model import Bar


@pytest.fixture(scope='module')
def xiangyang_leg():
    raw = json.loads((Path(__file__).parent / 'fixtures/xiangyang_2025_trend_break.json').read_text(encoding='utf-8'))
    bars = [Bar(datetime.fromisoformat(day), raw['symbol'], *prices)
            for day, *prices in raw['rows'] if day <= '2019-04-30']
    first = reversal_trends(lecture_drawing(bars), bars)
    second = secondary_trends(first, bars)
    stroke = next(s for s in second['strokes'] if any(p['time'] == '2019-03-21' for p in s['points']))
    source = next(s['points'] for s in first['structure_strokes'] if s['id'] == stroke['source_path'])
    return bars, stroke, source


def test_march_high_and_later_low_have_upward_proofs_but_neither_downward_route(xiangyang_leg):
    bars, stroke, source = xiangyang_leg
    high = next(p for p in stroke['points'] if p['time'] == '2019-03-21')
    low = next(p for p in stroke['points'] if p['time'] == '2019-03-29')
    prior = next(p for p in reversed(stroke['points'][:stroke['points'].index(high)]) if p['kind'] == 'L')
    assert high['value'] == 9.84 and high['broken_key']['value'] == 6.85
    assert high['confirmation_rule'] == 'level1_confirmed_high_breaks_known_level2_last_fall_high'
    assert low['value'] == 8.5 and low['trend_confirmation']['direction'] == 'up'
    assert low['trend_confirmation']['available_at'] == '2019-04-10'
    assert (prior['time'], prior['value']) == ('2018-10-19', 4.4)
    position = high['source_level1_position']
    lower = next(p for p in reversed(source[:position]) if p['kind'] == 'L')
    assert (lower['time'], lower['value']) == ('2019-01-22', 5.4)
    assert qualify_downtrend(source, source[position], position, prior, bars, len(bars) - 1) is None
    assert qualify_downtrend(source, source[position], position, None, bars, len(bars) - 1) is None


def test_a_future_upturn_does_not_publish_the_preceding_unconfirmed_downward_leg(xiangyang_leg):
    _, stroke, _ = xiangyang_leg
    low = next(p for p in stroke['points'] if p['time'] == '2019-03-29')
    assert stroke.get('leg_confirmation_policy') == 'two_routes_each_direction_v108'
    assert low.get('incoming_trend_state') == 'unconfirmed'
    assert low.get('incoming_trend_confirmation') is None
    assert low['trend_confirmation']['direction'] == 'up'
    following_high = next(p for p in stroke['points'] if p['time'] == '2019-04-18')
    assert following_high['incoming_trend_state'] == 'confirmed'
    assert following_high['incoming_trend_confirmation']['direction'] == 'up'
    wave = next(leg for leg in stroke['confirmed_legs'] if leg['points'][0]['time'] == '2018-10-19')
    assert [(p['time'], p['value']) for p in wave['points']] == [('2018-10-19', 4.4), ('2019-04-18', 11.3)]
    assert wave['confirmation']['available_at'] == '2019-02-12'
    assert following_high['active_trend_confirmation'] == wave['confirmation']


def bearish_sample(level, dates=False):
    values = [(22, 20, 21), (25, 23, 24), (24, 21, 22), (22, 19, 19.5), (21, 19.2, 19.8),
              (22, 20.5, 21), (21.5, 19.5, 20), (21, 18, 18.5), (20.5, 18.2, 18.7)]
    bars = [Bar(datetime(2024, 1, 1) + timedelta(days=i), 'TEST', (high + low) / 2, high, low, close, 100)
            for i, (high, low, close) in enumerate(values)]
    positions = [(0, 'L', 20, 0), (1, 'H', 25, 2), (3, 'L', 19, 4), (5, 'H', 22, 6)]
    source = [dict(index=i, ordinal=0, kind=kind, value=value, time=bars[i].timestamp.date().isoformat(),
                   available_at=bars[known].timestamp.date().isoformat() if dates else known,
                   state='confirmed', label=kind) for i, kind, value, known in positions]
    field = 'source_turn_position' if level == 1 else f'source_level{level - 1}_position'
    points = [dict(p, **{field: i}) for i, p in enumerate(source[:3])]
    return bars, source, points


@pytest.mark.parametrize('level', (1, 2, 3))
@pytest.mark.parametrize('dates', (False, True))
def test_all_levels_publish_a_real_same_level_downward_break(level, dates):
    bars, source, points = bearish_sample(level, dates)
    before = deepcopy((points, source))
    result = annotate_published_legs(points, source, bars, source_level=level - 1)
    assert result[-1]['incoming_trend_state'] == 'confirmed'
    proof = result[-1]['incoming_trend_confirmation']
    assert proof['direction'] == 'down' and proof['confirmation_rule'] == 'strict_same_level_market_key_break'
    assert proof['available_at'] == ('2024-01-04' if dates else 3)
    assert confirmed_trend_legs(result)[0]['direction'] == 'down'
    assert (points, source) == before


@pytest.mark.parametrize('level', (1, 2, 3))
@pytest.mark.parametrize('dates', (False, True))
def test_all_levels_wait_for_the_complete_lower_bearish_cycle(level, dates):
    bars, source, points = bearish_sample(level, dates)
    # No public own-level low: the lower-level L20 break must be followed
    # by a known H22 alternation and a fresh close below L19.
    points = points[1:]
    points[-1]['available_at'] = '2024-01-07' if dates else 6
    result = annotate_published_legs(points, source, bars, source_level=level - 1)
    assert result[-1]['incoming_trend_state'] == 'unconfirmed'
    early = dict(points[-1], available_at='2024-01-09' if dates else 8)
    result = annotate_published_legs([points[0], early], source, bars, source_level=level - 1)
    assert result[-1]['incoming_trend_state'] == 'unconfirmed'  # The L19 precedes the lower L18 turn bar.
    final = dict(points[-1], index=7, value=18, time='2024-01-08', available_at='2024-01-09' if dates else 8)
    source.append({key: value for key, value in final.items() if not key.startswith('source_')})
    points[-1] = final
    result = annotate_published_legs(points, source, bars, source_level=level - 1)
    proof = result[-1]['incoming_trend_confirmation']
    assert proof['direction'] == 'down' and proof['confirmation_rule'] == 'source_key_break_alternation_then_market_turn'
    assert proof['confirmed_by']['kind'] == 'K' and proof['confirmed_by']['value'] == 18.5
    assert proof['alternation_high']['value'] == 22


@pytest.mark.parametrize('failure', ('no_bars', 'wrong_position', 'wrong_ordinal', 'unconfirmed_source', 'unknown_endpoint'))
def test_missing_or_mismatched_evidence_cannot_publish_a_downward_leg(failure):
    bars, source, points = bearish_sample(2, dates=True)
    if failure == 'no_bars':
        bars = []
    elif failure == 'wrong_position':
        points[1]['source_level1_position'] = 0
    elif failure == 'wrong_ordinal':
        points[1]['ordinal'] = 1
    elif failure == 'unconfirmed_source':
        source[1]['state'] = 'developing'
    else:
        points[2]['available_at'] = '2099-01-01'
    result = annotate_published_legs(points, source, bars, source_level=1)
    assert result[-1]['incoming_trend_state'] == 'unconfirmed'
    assert confirmed_trend_legs(result) == []


@pytest.mark.parametrize('cutoff,peak', [('2019-03-29', '2019-03-21'), ('2019-04-10', '2019-03-21'), ('2019-04-25', '2019-04-18')])
def test_unqualified_pullback_does_not_end_the_bull_wave_in_replay(cutoff, peak):
    raw = json.loads((Path(__file__).parent / 'fixtures/xiangyang_2025_trend_break.json').read_text(encoding='utf-8'))
    bars = [Bar(datetime.fromisoformat(day), raw['symbol'], *prices) for day, *prices in raw['rows'] if day <= cutoff]
    first = reversal_trends(lecture_drawing(bars), bars)
    second = secondary_trends(first, bars)
    stroke = next(s for s in second['strokes'] if any(p['time'] == '2019-03-21' for p in s['points']))
    wave = next(leg for leg in stroke['confirmed_legs'] if leg['points'][0]['time'] == '2018-10-19')
    assert wave['points'][-1]['time'] == peak
    assert wave['confirmation']['available_at'] == '2019-02-12'
    tail = next(s for s in second['developing_strokes'] if s['source_path'] == stroke['source_path'])
    assert tail['confirmation']['direction'] == 'up'
    assert tail['confirmation']['origin']['time'] == '2018-10-19'


@pytest.mark.parametrize('level', (1, 2, 3))
def test_a_later_real_key_break_ends_the_continued_wave_without_backfilling_the_small_pullback(level):
    values = [(10, 8, 9), (7, 5, 6), (9, 6, 8), (11, 8, 10.5), (10.8, 9, 10.2),
              (9.5, 8, 9), (10.5, 8.5, 10), (12, 9, 11.5), (11.8, 9.5, 11.3),
              (11, 7, 7.5), (10, 8, 9)]
    bars = [Bar(datetime(2024, 1, 1) + timedelta(days=i), 'TEST', (high + low) / 2, high, low, close, 100)
            for i, (high, low, close) in enumerate(values)]
    source = [dict(index=i, ordinal=0, kind=kind, value=value, available_at=known,
                   time=bars[i].timestamp.date().isoformat(), state='confirmed', label=kind)
              for i, kind, value, known in [(0, 'H', 10, 0), (1, 'L', 5, 2), (3, 'H', 11, 4),
                                           (5, 'L', 8, 6), (7, 'H', 12, 8), (9, 'L', 7, 10)]]
    field = 'source_turn_position' if level == 1 else f'source_level{level - 1}_position'
    points = [dict(source[i], **{field: i}) for i in (1, 2, 3, 4, 5)]
    first = qualify_uptrend(source, source[1], 1, source[0], bars, 10)
    later = qualify_uptrend(source, source[3], 3, source[2], bars, 10)
    assert first is not None and later is not None
    points[0]['trend_confirmation'] = first
    points[1]['incoming_trend_confirmation'] = first
    points[2]['available_at'] = 7
    points[2]['trend_confirmation'] = later
    points[3]['incoming_trend_confirmation'] = later
    result = annotate_published_legs(points, source, bars, source_level=level - 1)
    assert result[2]['incoming_trend_state'] == 'unconfirmed'
    assert result[3]['active_trend_confirmation'] == first
    assert result[4]['incoming_trend_confirmation']['direction'] == 'down'
    assert result[4]['incoming_trend_confirmation']['available_at'] == 9
    assert result[4]['incoming_trend_confirmation']['broken_key']['value'] == 8
    legs = confirmed_trend_legs(result)
    assert [(leg['direction'], [p['index'] for p in leg['points']]) for leg in legs] == [('up', [1, 7]), ('down', [7, 9])]


@pytest.mark.parametrize('level', (2, 3))
def test_a_complete_new_bullish_cycle_can_turn_the_previous_bear_wave_before_the_next_formal_high(level):
    bars, source, _ = bearish_sample(level, dates=True)
    values = [(23, 19, 22.5), (22.8, 21, 22), (22, 20.5, 21), (22.5, 21, 22),
              (24, 21, 23.5), (23.8, 22, 23)]
    for i, (high, low, close) in enumerate(values, start=9):
        bars.append(Bar(datetime(2024, 1, 1) + timedelta(days=i), 'TEST', (high + low) / 2, high, low, close, 100))
    for i, kind, value, known in [(7, 'L', 18, 8), (9, 'H', 23, 10), (11, 'L', 20.5, 12), (13, 'H', 24, 14)]:
        source.append(dict(index=i, ordinal=0, kind=kind, value=value, time=bars[i].timestamp.date().isoformat(),
                           available_at=bars[known].timestamp.date().isoformat(), state='confirmed', label=kind))
    field = f'source_level{level - 1}_position'
    points = [dict(source[i], **{field: i}) for i in (1, 4)]
    outgoing = qualify_uptrend(source, source[4], 4, points[0], bars, 14)
    assert outgoing is not None and outgoing['confirmation_rule'] == 'source_key_break_alternation_then_market_turn'
    points[0]['wave_direction_after'] = 'down'
    points[1].update(available_at='2024-01-15', trend_confirmation=outgoing, wave_direction_after='up')
    points = annotate_published_legs(points, source, bars, source_level=level - 1)
    assert points[-1]['incoming_trend_confirmation']['direction'] == 'down'
    result = hierarchical_developing_path(dict(id='source', points=source), points, trend_level=level,
                                         source_level=level - 1, kind='secondary' if level == 2 else 'tertiary', bars=bars)
    assert result is not None and result['confirmation']['direction'] == 'up'
    assert result['confirmation']['available_at'] == '2024-01-14'
    assert result['points'][0]['index'] == 7 and result['points'][-1]['value'] == 24
