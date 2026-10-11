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
from wavequant.domain.market_structure.trend_judgement import judge_trend
from wavequant.domain.market_structure.trend_lines import high_low_points_from_strokes
from wavequant.domain.market_structure.trend_publication import annotate_published_legs, confirmed_trend_legs
from wavequant.domain.models.model import Bar


def _proof(point: Mapping[str, object], field: str) -> Mapping[str, object]:
    value = point[field]
    assert isinstance(value, Mapping)
    return cast(Mapping[str, object], value)


def _paths(level, field):
    paths = level[field]
    assert isinstance(paths, list)
    return cast(list[Mapping[str, object]], paths)


@pytest.fixture(scope='module')
def xiangyang_leg():
    raw = json.loads((Path(__file__).parent / 'fixtures/xiangyang_2025_trend_break.json').read_text(encoding='utf-8'))
    bars = [Bar(datetime.fromisoformat(day), raw['symbol'], *prices)
            for day, *prices in raw['rows'] if day <= '2019-04-30']
    first = reversal_trends(lecture_drawing(bars), bars)
    second = secondary_trends(first, bars)
    _assert_no_owned_secondary(first, second)
    stroke = next(s for s in _paths(first, 'qualified_strokes') if any(p['time'] == '2018-10-19' for p in s['points']))
    # 此段只取得一级整波身份；二级普通方向事实不能借一级许可证递归。
    source = next(s['points'] for s in _paths(first, 'source_strokes') if s['id'] == stroke['source_path'])
    # 与公共资格适配器相同，仅给未命名的原始折线点补种类标签。
    source = [dict(p, label=p.get('label', p['kind'])) for p in source]
    private = next(s['points'] for s in _paths(first, 'candidate_strokes') if s['source_path'] == stroke['source_path'])
    return bars, stroke, source, private


def _assert_no_owned_secondary(first, second):
    assert second['strokes'] == second['qualified_strokes'] == second['developing_strokes'] == []
    assert high_low_points_from_strokes(second['strokes']) == []
    projected = high_low_points_from_strokes(first['strokes'])
    assert [p for s in second['source_strokes'] for p in s['points']] == projected
    facts = [p for s in second['candidate_strokes'] for p in s['points']]
    assert [(p['time'], p['kind'], p['value'], p['available_at'], p['wave_direction_after'])
            for p in facts] == [
        ('2018-07-03', 'H', 6.85, '2018-09-07', 'down'),
        ('2018-10-19', 'L', 4.4, '2018-12-25', 'up'),
    ]
    assert [[(p['time'], p['value'], p['available_at']) for p in fact['definition_evidence']]
            for fact in facts] == [
        [('2018-08-15', 5.8, '2018-08-21'), ('2018-08-27', 5.39, '2018-09-04'),
         ('2018-08-20', 5.08, '2018-08-28'), ('2018-09-03', 5.07, '2018-09-07')],
        [('2018-09-25', 5.78, '2018-10-10'), ('2018-11-23', 5.85, '2018-12-03'),
         ('2018-10-19', 4.4, '2018-11-05'), ('2018-12-18', 4.82, '2018-12-25')],
    ]
    for fact in facts:
        assert fact['wave_identity_state'] == 'direction_only'
        assert fact['confirmation_rule'] == 'ordered_HH_then_HL_or_LL_then_LH_switch'
        assert 'wave_completed_at' not in fact and 'wave_completion_evidence' not in fact
        assert 'upgrade_evidence' not in fact and 'trend_confirmation' not in fact
    march_high = next((p for p in projected if p['time'] == '2019-03-21'), None)
    if march_high is not None:
        assert (march_high['source_geometry_available_at'], march_high['available_at']) == (
            '2019-03-22', '2019-04-01',
        )
    march_low = next((p for p in projected if p['time'] == '2019-03-29'), None)
    if march_low is not None:
        assert (march_low['source_geometry_available_at'], march_low['available_at']) == (
            '2019-04-09', '2019-04-09',
        )


def test_complete_lower_cycle_independently_authorizes_the_march_downward_leg(xiangyang_leg):
    bars, stroke, source, private = xiangyang_leg
    high = next(p for p in private if p['time'] == '2019-03-21')
    low = next(p for p in private if p['time'] == '2019-03-29')
    prior = next(p for p in reversed(stroke['points']) if p['kind'] == 'L' and p['time'] < high['time'])
    assert high['value'] == 9.84 and high['broken_key']['value'] == 6.01
    assert (high['broken_key']['time'], high['broken_key']['available_at']) == ('2019-01-14', '2019-01-22')
    assert high['confirmation_rule'] == 'level0_confirmed_high_breaks_known_level1_last_fall_high'
    assert low['value'] == 8.5 and low['available_at'] == low['wave_completed_at'] == '2019-04-09'
    assert low['upgrade_evidence']['direction'] == 'up' and low['upgrade_evidence']['role'] == 'origin'
    assert (prior['time'], prior['value']) == ('2019-01-22', 5.4)
    position = high['source_turn_position']
    assert (source[position]['index'], source[position]['ordinal'], source[position]['value']) == (
        high['index'], high['ordinal'], high['value'],
    )
    lower = next(p for p in reversed(source[:position]) if p['kind'] == 'L')
    assert (lower['time'], lower['value']) == ('2019-03-19', 8.89)
    assert (lower['ordinal'], lower['available_at']) == (1, '2019-03-20')
    dates = {bar.timestamp.date().isoformat(): index for index, bar in enumerate(bars)}
    # 真正一级旧低5.4未被跌破；下降需下级破位、交替高和新的收盘攻击。
    # 不能把折线L8.89伪装为一级旧低，也不能提前借4月独立上涨证书。
    assert qualify_downtrend(source, source[position], position, prior, bars, dates['2019-03-26']) is None
    expected = qualify_downtrend(source, source[position], position, prior, bars, dates['2019-03-27'])
    assert expected is not None
    assert qualify_downtrend(source, source[position], position, None, bars, dates['2019-03-27']) == expected
    wrong = dict(source[position], ordinal=source[position]['ordinal'] + 1)
    assert qualify_downtrend(source, wrong, position, prior, bars, len(bars) - 1) is None
    endpoint = next(p for p in stroke['points'] if p['time'] == '2019-03-29')
    assert endpoint['incoming_trend_state'] == 'confirmed'
    proof = _proof(endpoint, 'incoming_trend_confirmation')
    assert proof == expected and proof['direction'] == 'down'
    assert proof['confirmation_rule'] == 'source_key_break_alternation_then_market_turn'
    assert proof['available_at'] == '2019-03-27'
    assert (_proof(proof, 'broken_key')['time'], _proof(proof, 'broken_key')['value']) == ('2019-03-19', 8.89)
    assert (_proof(proof, 'broken_key')['ordinal'], _proof(proof, 'broken_key')['available_at']) == (1, '2019-03-20')
    assert (_proof(proof, 'flip_low')['time'], _proof(proof, 'flip_low')['value'],
            _proof(proof, 'flip_low')['available_at']) == ('2019-03-22', 8.8, '2019-03-25')
    assert (_proof(proof, 'alternation_high')['time'], _proof(proof, 'alternation_high')['value'],
            _proof(proof, 'alternation_high')['available_at']) == ('2019-03-25', 9.35, '2019-03-26')
    assert (_proof(proof, 'confirmed_by')['time'], _proof(proof, 'confirmed_by')['value']) == ('2019-03-27', 8.79)
    assert endpoint['available_at'] == '2019-04-09'
    assert not any(p['time'] == '2019-03-29' for p in judge_trend(source, trend_level=1, asof='2019-04-08').points)
    owned = next(p for p in judge_trend(source, trend_level=1, asof='2019-04-09').points if p['time'] == '2019-03-29')
    assert owned['available_at'] == owned['wave_completed_at'] == '2019-04-09'


def test_each_direction_keeps_its_independent_march_and_april_certificate(xiangyang_leg):
    _, stroke, _, _ = xiangyang_leg
    assert stroke['view'] == 'strategy_permissions' and stroke['trend_level'] == 1
    march_low = next(p for p in stroke['points'] if p['time'] == '2019-03-29')
    assert march_low['incoming_trend_confirmation']['direction'] == 'down'
    assert march_low['incoming_trend_confirmation']['available_at'] == '2019-03-27'
    assert march_low['trend_confirmation']['direction'] == 'up'
    assert march_low['trend_confirmation']['available_at'] == '2019-04-02'
    assert march_low['available_at'] == march_low['wave_completed_at'] == '2019-04-09'
    following_high = next(p for p in stroke['points'] if p['time'] == '2019-04-18')
    assert following_high['incoming_trend_state'] == 'confirmed'
    assert following_high['incoming_trend_confirmation']['direction'] == 'up'
    wave = next(leg for leg in stroke['confirmed_legs'] if leg['points'][0]['time'] == '2018-10-19')
    assert [(p['time'], p['value']) for p in wave['points']] == [('2018-10-19', 4.4), ('2019-03-21', 9.84)]
    # 首次一级上升证书冻结于11/8；后续下降与4月上涨各自保存证明，
    # 下级局部高低或非法二级候选不能改写这段整波的级别与起点。
    assert wave['confirmation']['available_at'] == '2018-11-08'
    assert (wave['confirmation']['broken_key']['time'], wave['confirmation']['broken_key']['value']) == (
        '2018-10-17', 4.8,
    )
    assert (wave['confirmation']['confirmed_by']['time'], wave['confirmation']['confirmed_by']['value']) == (
        '2018-11-08', 5.0,
    )
    next_wave = next(leg for leg in stroke['confirmed_legs'] if leg['direction'] == 'up'
                     and leg['points'][0]['time'] == '2019-03-29')
    assert [(p['time'], p['value']) for p in next_wave['points']] == [('2019-03-29', 8.5), ('2019-04-18', 11.3)]
    assert next_wave['confirmation']['available_at'] == '2019-04-02'
    assert (next_wave['confirmation']['broken_key']['time'], next_wave['confirmation']['broken_key']['value']) == (
        '2019-03-21', 9.84,
    )
    assert (next_wave['confirmation']['confirmed_by']['time'], next_wave['confirmation']['confirmed_by']['value']) == (
        '2019-04-02', 10.01,
    )
    assert following_high['active_trend_confirmation'] == next_wave['confirmation']
    # 4/16仅跌破折线L10.3，没有已取得一级身份的同级旧低。
    # 它不能关闭本级上涨，亦不能借局部回调强造二级趋势。
    _, _, source, _ = xiangyang_leg
    local_low = next(p for p in source if p['time'] == '2019-04-09' and p['kind'] == 'L')
    local_attack = next(p for p in source if p['time'] == '2019-04-16' and p['kind'] == 'L')
    assert (local_low['value'], local_low['available_at']) == (10.3, '2019-04-10')
    assert (local_attack['value'], local_attack['available_at']) == (10.08, '2019-04-17')
    assert not any(p['time'] in ('2019-04-09', '2019-04-16') for p in stroke['points'])
    assert [(leg['direction'], leg['points'][-1]['time']) for leg in stroke['confirmed_legs']
            if leg['points'][0]['time'] >= '2019-03-21'] == [('down', '2019-03-29'), ('up', '2019-04-18')]


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
    ('2019-03-29', '2019-03-21', '2018-10-19', '2018-11-08'),
    ('2019-04-10', '2019-04-08', '2019-03-29', '2019-04-02'),
    ('2019-04-15', '2019-04-11', '2019-03-29', '2019-04-02'),
])
def test_replay_waits_for_the_down_endpoint_then_opens_the_independent_april_up_wave(
    cutoff, peak, origin, known, xiangyang_leg,
):
    raw = json.loads((Path(__file__).parent / 'fixtures/xiangyang_2025_trend_break.json').read_text(encoding='utf-8'))
    bars = [Bar(datetime.fromisoformat(day), raw['symbol'], *prices) for day, *prices in raw['rows'] if day <= cutoff]
    first = reversal_trends(lecture_drawing(bars), bars)
    second = secondary_trends(first, bars)
    _assert_no_owned_secondary(first, second)
    stroke = next(s for s in _paths(first, 'qualified_strokes') if any(p['time'] == '2018-10-19' for p in s['points']))
    geometry = next(s for s in _paths(first, 'strokes') if s['id'] == stroke['id'])
    wave = next(leg for leg in stroke['confirmed_legs'] if leg['points'][0]['time'] == '2018-10-19')
    assert wave['points'][-1]['time'] == '2019-03-21'
    assert wave['confirmation']['available_at'] == '2018-11-08'
    _, complete, _, _ = xiangyang_leg
    complete_wave = next(leg for leg in complete['confirmed_legs'] if leg['points'][0]['time'] == '2018-10-19')
    assert wave['confirmation'] == complete_wave['confirmation']
    if cutoff == '2019-03-29':
        # 3/29价格已发生，但折线L尚未知，本级H也尚未真实闭合。
        # 后来的4/1折线确认、4/9整波身份都不能倒填这个前缀。
        assert not any(leg['direction'] == 'down' for leg in stroke['confirmed_legs']
                       if leg['points'][0]['time'] >= '2019-03-21')
        assert not any(p['time'] == '2019-03-29' for p in geometry['points'])
        assert 'wave_completed_at' not in geometry['points'][-1]
        assert not any(p['time'] == '2019-03-21' for s in _paths(second, 'source_strokes') for p in s['points'])
    else:
        down = next(leg for leg in stroke['confirmed_legs'] if leg['direction'] == 'down'
                    and leg['points'][0]['time'] == '2019-03-21')
        assert down['points'][-1]['time'] == '2019-03-29'
        assert down['confirmation']['available_at'] == '2019-03-27'
        assert down['confirmation'] == next(leg for leg in complete['confirmed_legs']
            if leg['direction'] == 'down' and leg['points'][0]['time'] == '2019-03-21')['confirmation']
        assert down['points'][-1]['available_at'] == '2019-04-09'
        current = next(leg for leg in stroke['confirmed_legs'] if leg['direction'] == 'up'
                       and leg['points'][0]['time'] == origin)
        assert current['points'][-1]['time'] == peak
        assert current['confirmation']['available_at'] == known
        complete_up = next(leg for leg in complete['confirmed_legs'] if leg['direction'] == 'up'
                           and leg['points'][0]['time'] == '2019-03-29')
        assert current['confirmation'] == complete_up['confirmation']
        assert current['confirmation']['origin']['time'] == origin
        assert current['confirmation']['confirmed_by']['value'] == 10.01
    tail = next(s for s in _paths(first, 'developing_strokes') if s['source_path'] == stroke['source_path'])
    tail_expectation = {
        '2019-03-29': [('2019-03-21', 9.84, '2019-03-22'), ('2019-03-22', 8.8, '2019-03-25')],
        '2019-04-10': [('2019-04-08', 11.07, '2019-04-09'), ('2019-04-09', 10.3, '2019-04-10')],
        '2019-04-15': [('2019-04-11', 11.2, '2019-04-12'), ('2019-04-12', 10.7, '2019-04-15')],
    }
    assert [(p['time'], p['value'], p['available_at']) for p in tail['points']] == tail_expectation[cutoff]
    assert tail['display_only'] is True and tail['wave_direction'] == 'down'
    assert tail['points'][-1]['display_only'] is True and tail['confirmed_legs'] == []
    assert 'confirmation' not in tail and 'wave_completed_at' not in tail['points'][-1]
    assert all(p['available_at'] <= cutoff for s in _paths(second, 'source_strokes') for p in s['points'])


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
