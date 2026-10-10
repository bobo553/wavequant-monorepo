"""Two qualified vertices do not automatically qualify the leg between them."""

from copy import deepcopy
from collections.abc import Mapping
from datetime import datetime, timedelta
import json
from pathlib import Path
from typing import cast

import pytest

from wavequant.domain.market_structure.lecture_drawing import lecture_drawing
from wavequant.domain.market_structure.hierarchical_development import hierarchical_developing_path
from wavequant.domain.market_structure.lecture_trend import reversal_trends
from wavequant.domain.market_structure.secondary_trend import secondary_trends
from wavequant.domain.market_structure.trend_confirmation import qualify_downtrend, qualify_uptrend
from wavequant.domain.market_structure.trend_publication import annotate_published_legs, confirmed_trend_legs
from wavequant.domain.models.model import Bar


def _proof(point: Mapping[str, object], field: str) -> Mapping[str, object]:
    value = point[field]
    assert isinstance(value, Mapping)
    return cast(Mapping[str, object], value)


@pytest.fixture(scope='module')
def xiangyang_leg():
    raw = json.loads((Path(__file__).parent / 'fixtures/xiangyang_2025_trend_break.json').read_text(encoding='utf-8'))
    bars = [Bar(datetime.fromisoformat(day), raw['symbol'], *prices)
            for day, *prices in raw['rows'] if day <= '2019-04-30']
    first = reversal_trends(lecture_drawing(bars), bars)
    second = secondary_trends(first, bars)
    stroke = next(s for s in second['strokes'] if any(p['time'] == '2018-10-19' for p in s['points']))
    # 二级只能读取正式一级来源；私有本级升级候选仍需独立取得逐方向证书。
    source = next(s['points'] for s in first['strokes'] if s['id'] == stroke['source_path'])
    private = next(s['points'] for s in second['candidate_strokes'] if s['source_path'] == stroke['source_path'])
    return bars, stroke, source, private


def test_known_level_key_break_independently_authorizes_the_march_downward_leg(xiangyang_leg):
    bars, stroke, source, private = xiangyang_leg
    high = next(p for p in private if p['time'] == '2019-03-21')
    low = next(p for p in private if p['time'] == '2019-03-29')
    prior = next(p for p in reversed(stroke['points']) if p['kind'] == 'L' and p['time'] < high['time'])
    assert high['value'] == 9.84 and high['broken_key']['value'] == 9.3
    assert high['broken_key']['time'] == '2019-03-18'
    assert high['confirmation_rule'] == 'level1_confirmed_high_breaks_known_level2_last_fall_high'
    assert low['value'] == 8.5 and low['available_at'] == '2019-04-01'
    assert low['upgrade_evidence']['direction'] == 'down'
    assert (prior['time'], prior['value']) == ('2019-03-15', 8.23)
    position = high['source_level1_position']
    lower = next(p for p in reversed(source[:position]) if p['kind'] == 'L')
    assert (lower['time'], lower['value']) == ('2019-03-19', 8.89)
    # 不能拿更早的已显示L8.23或缺失key授权下降。最近本级已定义的L8.89
    # 虽尚未进入上升发布链，却已知于3/22；3/26 L8.65严格破位独立授予下降。
    # 许可来自本级真实key，而非后来的4月上涨或尚未确认的下级回调周期。
    assert qualify_downtrend(source, source[position], position, prior, bars, len(bars) - 1) is None
    assert qualify_downtrend(source, source[position], position, None, bars, len(bars) - 1) is None
    endpoint = next(p for p in stroke['points'] if p['time'] == '2019-03-29')
    assert endpoint['incoming_trend_state'] == 'confirmed'
    proof = _proof(endpoint, 'incoming_trend_confirmation')
    assert proof['direction'] == 'down'
    assert proof['confirmation_rule'] == 'strict_same_level_market_key_break'
    assert proof['available_at'] == '2019-03-26'
    assert (_proof(proof, 'broken_key')['time'], _proof(proof, 'broken_key')['value']) == ('2019-03-19', 8.89)
    assert _proof(proof, 'broken_key')['available_at'] == '2019-03-22'
    assert (_proof(proof, 'confirmed_by')['time'], _proof(proof, 'confirmed_by')['value']) == ('2019-03-26', 8.65)
    assert endpoint['available_at'] == '2019-04-01'


def test_each_direction_keeps_its_independent_march_and_april_certificate(xiangyang_leg):
    _, stroke, _, _ = xiangyang_leg
    assert stroke.get('leg_confirmation_policy') == 'two_routes_each_direction_v108'
    march_low = next(p for p in stroke['points'] if p['time'] == '2019-03-29')
    assert march_low['incoming_trend_confirmation']['direction'] == 'down'
    assert march_low['incoming_trend_confirmation']['available_at'] == '2019-03-26'
    assert march_low['trend_confirmation']['direction'] == 'up'
    assert march_low['trend_confirmation']['available_at'] == '2019-04-02'
    assert march_low['available_at'] == '2019-04-01'
    following_high = next(p for p in stroke['points'] if p['time'] == '2019-04-08')
    assert following_high['incoming_trend_state'] == 'confirmed'
    assert following_high['incoming_trend_confirmation']['direction'] == 'up'
    wave = next(leg for leg in stroke['confirmed_legs'] if leg['points'][0]['time'] == '2018-10-19')
    assert [(p['time'], p['value']) for p in wave['points']] == [('2018-10-19', 4.4), ('2019-03-21', 9.84)]
    # 逐高升级让真实 H6.01 在01-14突破早已知 H5.85；冻结该首次证书，
    # 不能沿用旧归并模式较晚的02-12日期。3/26真实key下降证书结束这段
    # 上涨；4月独立上涨不能重写1月首次确认日或把原整波继续延伸到4月高点。
    assert wave['confirmation']['available_at'] == '2019-01-14'
    assert (wave['confirmation']['broken_key']['time'], wave['confirmation']['broken_key']['value']) == (
        '2018-11-23', 5.85,
    )
    assert (wave['confirmation']['confirmed_by']['time'], wave['confirmation']['confirmed_by']['value']) == (
        '2019-01-14', 6.01,
    )
    next_wave = next(leg for leg in stroke['confirmed_legs'] if leg['direction'] == 'up'
                     and leg['points'][0]['time'] == '2019-03-29')
    assert [(p['time'], p['value']) for p in next_wave['points']] == [('2019-03-29', 8.5), ('2019-04-11', 11.2)]
    assert next_wave['confirmation']['available_at'] == '2019-04-02'
    assert (next_wave['confirmation']['broken_key']['time'], next_wave['confirmation']['broken_key']['value']) == (
        '2019-03-21', 9.84,
    )
    assert (next_wave['confirmation']['confirmed_by']['time'], next_wave['confirmation']['confirmed_by']['value']) == (
        '2019-04-02', 10.01,
    )
    assert following_high['active_trend_confirmation'] == next_wave['confirmation']
    # 04-16则确实严格跌破正式L10.3，独立下降证书会结束此前上涨；升级通道
    # 的保留不等于所有局部回调都可授权，必须核对真实旧关键位及首次攻击价。
    down = next(leg for leg in stroke['confirmed_legs'] if leg['points'][-1]['time'] == '2019-04-16')
    assert down['direction'] == 'down'
    assert down['confirmation']['confirmation_rule'] == 'strict_same_level_market_key_break'
    assert down['confirmation']['available_at'] == '2019-04-16'
    assert (down['confirmation']['broken_key']['time'], down['confirmation']['broken_key']['value']) == (
        '2019-04-09', 10.3,
    )
    assert down['confirmation']['confirmed_by']['value'] == 10.08


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
    proof = _proof(result[-1], 'incoming_trend_confirmation')
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
    proof = _proof(result[-1], 'incoming_trend_confirmation')
    assert proof['direction'] == 'down' and proof['confirmation_rule'] == 'source_key_break_alternation_then_market_turn'
    assert _proof(proof, 'confirmed_by')['kind'] == 'K' and _proof(proof, 'confirmed_by')['value'] == 18.5
    assert _proof(proof, 'alternation_high')['value'] == 22


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


@pytest.mark.parametrize('cutoff,peak,origin,known', [
    ('2019-03-29', '2019-03-21', '2018-10-19', '2019-01-14'),
    ('2019-04-10', '2019-04-08', '2019-03-29', '2019-04-02'),
    ('2019-04-15', '2019-04-11', '2019-03-29', '2019-04-02'),
])
def test_replay_waits_for_the_down_endpoint_then_opens_the_independent_april_up_wave(cutoff, peak, origin, known):
    raw = json.loads((Path(__file__).parent / 'fixtures/xiangyang_2025_trend_break.json').read_text(encoding='utf-8'))
    bars = [Bar(datetime.fromisoformat(day), raw['symbol'], *prices) for day, *prices in raw['rows'] if day <= cutoff]
    first = reversal_trends(lecture_drawing(bars), bars)
    second = secondary_trends(first, bars)
    stroke = next(s for s in second['strokes'] if any(p['time'] == '2018-10-19' for p in s['points']))
    wave = next(leg for leg in stroke['confirmed_legs'] if leg['points'][0]['time'] == '2018-10-19')
    assert wave['points'][-1]['time'] == '2019-03-21'
    assert wave['confirmation']['available_at'] == '2019-01-14'
    if cutoff == '2019-03-29':
        # 价格点虽已发生，但该L需4/1才成为真实可知来源，不能提前公开端点。
        assert not any(leg['direction'] == 'down' for leg in stroke['confirmed_legs']
                       if leg['points'][0]['time'] >= '2019-03-21')
    else:
        down = next(leg for leg in stroke['confirmed_legs'] if leg['direction'] == 'down'
                    and leg['points'][0]['time'] == '2019-03-21')
        assert down['points'][-1]['time'] == '2019-03-29'
        assert down['confirmation']['available_at'] == '2019-03-26'
        current = next(leg for leg in stroke['confirmed_legs'] if leg['direction'] == 'up'
                       and leg['points'][0]['time'] == origin)
        assert current['points'][-1]['time'] == peak
        assert current['confirmation']['available_at'] == known
    tail = next(s for s in second['developing_strokes'] if s['source_path'] == stroke['source_path'])
    assert tail['confirmation']['direction'] == 'up'
    assert tail['confirmation']['available_at'] == known
    effective_origin = tail['confirmation'].get('wave_origin', tail['confirmation']['origin'])
    assert effective_origin['time'] == origin
    assert tail['points'][0]['time'] == origin and tail['points'][-1]['time'] == peak
    if cutoff == '2019-03-29':
        assert tail['confirmation']['origin']['time'] == '2018-12-18'
    else:
        assert tail['confirmation']['origin']['time'] == '2019-03-29'
        assert tail['confirmation']['confirmed_by']['value'] == 10.01


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
    down = _proof(result[4], 'incoming_trend_confirmation')
    assert down['direction'] == 'down'
    assert down['available_at'] == 9
    assert _proof(down, 'broken_key')['value'] == 8
    legs = confirmed_trend_legs(result)
    assert [(leg['direction'], [p['index'] for p in cast(list[Mapping[str, object]], leg['points'])])
            for leg in legs] == [('up', [1, 7]), ('down', [7, 9])]


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
    assert _proof(points[-1], 'incoming_trend_confirmation')['direction'] == 'down'
    result = hierarchical_developing_path(dict(id='source', points=source), points, trend_level=level,
                                         source_level=level - 1, kind='secondary' if level == 2 else 'tertiary', bars=bars)
    assert result is not None and result['confirmation']['direction'] == 'up'
    assert result['confirmation']['available_at'] == '2024-01-14'
    assert result['points'][0]['index'] == 7 and result['points'][-1]['value'] == 24
