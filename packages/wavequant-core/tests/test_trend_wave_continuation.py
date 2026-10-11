"""Formal publication and its live extension belong to one causal wave."""

from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
from typing import cast

import pytest

from wavequant.domain.market_structure.lecture_drawing import lecture_drawing
from wavequant.domain.market_structure.lecture_trend import reversal_trends
from wavequant.domain.market_structure.secondary_trend import secondary_trends
from wavequant.domain.market_structure.tertiary_trend import tertiary_trends
from wavequant.domain.market_structure.hierarchical_development import hierarchical_developing_path
from wavequant.domain.market_structure.trend_confirmation import qualify_uptrend
from wavequant.domain.market_structure.trend_judgement import judge_trend
from wavequant.domain.market_structure.trend_levels import completed_source_points
from wavequant.domain.market_structure.trend_publication import trend_wave_identity
from wavequant.domain.models.model import Bar


def _mapping(value: object) -> Mapping[str, object]:
    assert isinstance(value, Mapping) and all(isinstance(key, str) for key in value)
    return cast(Mapping[str, object], value)


def _mutable(value: object) -> dict[str, object]:
    assert isinstance(value, dict) and all(isinstance(key, str) for key in value)
    return cast(dict[str, object], value)


def _records(value: object) -> list[Mapping[str, object]]:
    assert isinstance(value, (list, tuple))
    return [_mapping(item) for item in value]


def _fixture(name: str) -> Mapping[str, object]:
    return _mapping(json.loads((Path(__file__).parent / 'fixtures' / name).read_text(encoding='utf-8')))


FIXTURE = _fixture('xiangyang_2024_wave_continuation.json')


def identity(proof: Mapping[str, object], level: int = 3, path: str | None = None) -> str | None:
    source_path = path or cast(str, _mapping(FIXTURE['formal_stroke'])['source_path'])
    return trend_wave_identity(proof, trend_level=level, source_path=source_path)


def test_real_formal_and_developing_paths_have_one_first_known_wave_identity() -> None:
    formal, tail = _mapping(FIXTURE['formal_stroke']), _mapping(FIXTURE['developing_stroke'])
    leg = _records(formal['confirmed_legs'])[0]
    assert [p['value'] for p in _records(leg['points'])] == [3.45, 18.9]
    assert [p['value'] for p in _records(tail['points'])] == [3.45, 19.63]
    first, live = _mapping(leg['confirmation']), _mapping(tail['confirmation'])
    assert first['available_at'] == live['available_at'] == '2025-03-11'
    assert identity(first) == identity(live) == leg['wave_id'] == tail['wave_id']
    wave_id = leg['wave_id']
    assert isinstance(wave_id, str) and wave_id.startswith('trend-wave-v1:')
    assert formal['wave_display_policy'] == tail['wave_display_policy'] == 'one_connection_per_confirmed_wave_v109'


@pytest.mark.parametrize('change', ('label', 'direct_render_fields', 'ordinal_zero', 'equivalent_numeric_value'))
def test_labels_and_direct_route_serialization_do_not_start_another_wave(change: str) -> None:
    proof = _mutable(deepcopy(_records(_mapping(FIXTURE['formal_stroke'])['confirmed_legs'])[0]['confirmation']))
    before = deepcopy(proof)
    if change == 'label':
        _mutable(proof['origin'])['label'] = 'another display label'
    elif change == 'direct_render_fields':
        proof.pop('flip_high', None)
        proof.pop('alternation_low', None)
    elif change == 'ordinal_zero':
        _mutable(proof['origin']).pop('ordinal', None)
    else:
        _mutable(proof['confirmed_by'])['value'] = 11.440
    assert identity(proof) == identity(before)


@pytest.mark.parametrize('change', ('level', 'path', 'direction', 'date', 'origin', 'ordinal', 'key', 'trigger', 'whole_origin'))
def test_independent_causal_facts_cannot_merge_only_because_the_price_origin_matches(change: str) -> None:
    proof = _mutable(deepcopy(_records(_mapping(FIXTURE['formal_stroke'])['confirmed_legs'])[0]['confirmation']))
    expected = identity(proof)
    if change == 'level':
        assert identity(proof, level=2) != expected
        return
    if change == 'path':
        assert identity(proof, path='other-source-path') != expected
        return
    if change == 'direction': proof['direction'] = 'down'
    elif change == 'date': proof['available_at'] = '2025-03-12'
    elif change == 'origin':
        origin = _mutable(proof['origin'])
        origin['index'] = cast(int, origin['index']) + 1
    elif change == 'ordinal': _mutable(proof['origin'])['ordinal'] = 1
    elif change == 'key':
        key = _mutable(proof['broken_key'])
        key['value'] = cast(float, key['value']) + .01
    elif change == 'trigger':
        trigger = _mutable(proof['confirmed_by'])
        trigger['index'] = cast(int, trigger['index']) + 1
    else:
        whole_origin = _mapping(proof['origin'])
        proof['wave_origin'] = dict(whole_origin, index=cast(int, whole_origin['index']) - 1)
    assert identity(proof) != expected


def test_source_cycles_keep_their_flip_and_counter_evidence_in_wave_identity() -> None:
    data = _fixture('xiangyang_2019_trend_legs.json')
    proof = _mutable(deepcopy(_records(_mapping(data['secondary_stroke'])['confirmed_legs'])[0]['confirmation']))
    assert proof['confirmation_rule'] == 'source_key_break_alternation_then_market_turn'
    expected = identity(proof, level=2)
    assert expected is not None
    changed = deepcopy(proof); _mutable(changed['alternation_low'])['available_at'] = '2019-01-28'
    assert identity(changed, level=2) != expected
    changed = deepcopy(proof); changed['retracement_origin'] = dict(_mapping(proof['retracement_origin']), value=4.5)
    assert identity(changed, level=2) != expected
    changed = deepcopy(proof); changed.pop('alternation_low')
    assert identity(changed, level=2) is None


@pytest.mark.parametrize('cutoff,endpoint_date,endpoint_price', [
    ('2025-03-21', '2025-03-20', 18.9),
    ('2025-04-02', '2025-03-20', 18.9),
    ('2025-05-15', '2025-05-15', 19.63),
])
def test_the_real_whole_active_wave_keeps_its_identity_after_an_independent_completed_source_key_break(
    cutoff: str, endpoint_date: str, endpoint_price: float,
) -> None:
    raw = _fixture('xiangyang_2025_trend_break.json')
    rows = raw['rows']
    assert isinstance(rows, list)
    symbol = raw['symbol']
    assert isinstance(symbol, str)
    bars: list[Bar] = []
    for row in rows:
        assert isinstance(row, list) and len(row) == 6 and isinstance(row[0], str)
        if row[0] <= cutoff:
            prices = row[1:]
            assert all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in prices)
            open_price, high, low, close, volume = [float(value) for value in cast(Sequence[int | float], prices)]
            bars.append(Bar(datetime.fromisoformat(row[0]), symbol, open_price, high, low, close, volume))
    draw = cast(Callable[[Sequence[Bar]], dict[str, object]], lecture_drawing)
    first = reversal_trends(draw(bars), bars)
    second = secondary_trends(first, bars)
    third = tertiary_trends(second, bars)
    source_stroke = _records(third['source_strokes'])[-1]
    path = source_stroke['id']
    assert isinstance(path, str)
    # 公开结构发展线只表达几何；策略开发通道读取同路径资格和已经结束的
    # 真实二级来源。仅补显示label，绝不改available/closure或借下级许可证。
    source_points = [dict(point, label=point.get('label', point['kind']))
                     for point in _records(source_stroke['points'])]
    source_view = dict(source_stroke, points=source_points)
    qualified = next(stroke for stroke in _records(third['qualified_strokes']) if stroke['source_path'] == path)
    candidates = next(stroke for stroke in _records(third['candidate_strokes']) if stroke['source_path'] == path)
    develop = cast(Callable[..., dict[str, object] | None], hierarchical_developing_path)
    tail = develop(source_view, _records(qualified['points']), trend_level=3, source_level=2,
                   kind='tertiary', bars=bars, structural=_records(candidates['points']))
    assert tail is not None
    origin = next(point for point in _records(tail['points']) if point.get('development_role') == 'confirmed_direction_origin')
    assert (origin['time'], origin['value']) == ('2024-07-25', 3.45)
    endpoint = _mapping(tail['confirmed_endpoint'])
    assert (endpoint['time'], endpoint['value']) == (endpoint_date, endpoint_price)
    proof = _mapping(tail['confirmation'])
    assert proof['confirmation_rule'] == 'strict_same_level_market_key_break'
    assert (proof['direction'], proof['available_at']) == ('up', '2025-03-07')
    key, trigger = _mapping(proof['broken_key']), _mapping(proof['confirmed_by'])
    # 旧8.03只是局部方向事实。III真正拥有的旧整波高点为9.77；II的
    # 12月H8.17没有破该key，不能把它的单段上涨许可冒作III许可。
    assert (key['time'], key['value'], key['available_at']) == ('2023-07-04', 9.77, '2024-03-29')
    assert (trigger['time'], trigger['value']) == ('2025-03-07', 10.11)
    assert cast(str, key['available_at']) < cast(str, trigger['time']) <= cast(str, proof['available_at']) <= cutoff
    assert cast(float, trigger['value']) > cast(float, key['value'])
    # 连续来源保留2022年的10.66压力高点，策略视图不会把此前未获上涨
    # 许可的较低9.77峰顶另行发布；完整几何仍拥有它及其独立闭合证据。
    owned_key = next(point for stroke in _records(third['strokes']) if stroke['source_path'] == path
                     for point in _records(stroke['points'])
                     if all(point[field] == key[field] for field in ('index', 'ordinal', 'kind', 'value', 'available_at')))
    assert not any(all(point[field] == key[field] for field in ('index', 'ordinal', 'kind', 'value'))
                   for point in _records(qualified['points']))
    assert (owned_key['wave_identity_state'], owned_key['wave_completed_at']) == ('owned', '2024-03-29')
    assert owned_key['available_at'] == key['available_at'] == '2024-03-29'
    recursive_key = completed_source_points([owned_key], source_level=3)
    assert len(recursive_key) == 1
    assert (recursive_key[0]['available_at'], recursive_key[0]['source_geometry_available_at']) == (
        owned_key['wave_completed_at'], owned_key['available_at'])
    key_completion = _mapping(owned_key['wave_completion_evidence'])
    assert (key_completion['available_at'], key_completion['wave_direction_before'],
            key_completion['wave_direction_after']) == ('2024-03-29', 'up', 'down')
    # 新一级折线身份规则使实际II来源在3/29补齐LL/LH。III已有真实内部
    # 下级反转的组合波，故当日即闭合；6/26是另一份较晚strict证明，不能
    # 覆盖这份最早结束知识，更不能把2023年的几何日期当作闭合日期。
    key_refs = _records(key_completion['definition_evidence'])
    assert [(point['kind'], point['time'], point['value'], point['available_at']) for point in key_refs] == [
        ('H', '2023-07-04', 9.77, '2023-08-02'),
        ('H', '2024-01-18', 7.5, '2024-03-01'),
        ('L', '2023-10-23', 5.81, '2023-12-14'),
        ('L', '2024-02-08', 3.78, '2024-03-29'),
    ]
    assert cast(float, key_refs[1]['value']) < cast(float, key_refs[0]['value'])
    assert cast(float, key_refs[3]['value']) < cast(float, key_refs[2]['value'])
    for reference in key_refs:
        actual_source = next(point for point in source_points
                             if all(point[field] == reference[field]
                                    for field in ('index', 'ordinal', 'kind', 'value', 'time', 'available_at')))
        assert actual_source['wave_identity_state'] == 'owned'
        assert actual_source['wave_completed_at'] == reference['available_at']
    key_identity = _mapping(key_completion['lower_wave_identity_evidence'])
    assert [(_mapping(key_identity[field])['time'], _mapping(key_identity[field])['value'])
            for field in ('origin', 'interior', 'endpoint')] == [
        ('2023-07-04', 9.77), ('2023-10-23', 5.81), ('2024-02-08', 3.78),
    ]
    assert key_identity['available_at'] == key_completion['available_at']
    before_key = judge_trend(source_points, trend_level=3, asof='2024-03-28')
    assert not any(point['time'] == '2023-07-04' for point in before_key.points)
    first_key = judge_trend(source_points, trend_level=3, asof='2024-03-29')
    assert next(point for point in first_key.points if point['time'] == '2023-07-04')['wave_completed_at'] == '2024-03-29'
    assert cast(str, _records(tail['points'])[-1]['time']) <= cutoff
    wave_id = identity(proof, path=path)
    assert wave_id is not None and wave_id == tail['wave_id']
    # 策略发展波从已结束II低点取得本级market许可。该低点另有独立的
    # III严格下降升级身份，开放端点不能仅因行情许可变成已结束III来源。
    whole_origin = next(point for point in source_points if point['time'] == '2024-07-25')
    assert (whole_origin['value'], whole_origin['available_at'], whole_origin['wave_completed_at']) == (
        3.45, '2024-08-30', '2024-08-30')
    assert whole_origin['source_geometry_available_at'] == '2024-07-30'
    origin_completion = _mapping(whole_origin['wave_completion_evidence'])
    assert (origin_completion['available_at'], origin_completion['wave_direction_before'],
            origin_completion['wave_direction_after']) == ('2024-08-30', 'down', 'up')
    # II低点自身的复合上涨也由真实已完成I点确认，不借用2025年的market
    # 资格。8/30已补齐严格HH/HL和实际内部反转，故无需等后来10/21证明。
    origin_refs = _records(origin_completion['definition_evidence'])
    assert [(point['kind'], point['time'], point['value'], point['available_at']) for point in origin_refs] == [
        ('H', '2024-08-16', 3.85, '2024-08-20'),
        ('H', '2024-08-22', 5.01, '2024-08-30'),
        ('L', '2024-08-13', 3.65, '2024-08-19'),
        ('L', '2024-08-20', 3.66, '2024-08-22'),
    ]
    assert cast(float, origin_refs[1]['value']) > cast(float, origin_refs[0]['value'])
    assert cast(float, origin_refs[3]['value']) > cast(float, origin_refs[2]['value'])
    for reference in origin_refs:
        actual_lower = next(point for stroke in _records(second['source_strokes'])
                            for point in _records(stroke['points'])
                            if all(point[field] == reference[field]
                                   for field in ('index', 'ordinal', 'kind', 'value', 'time', 'available_at')))
        assert actual_lower['wave_identity_state'] == 'owned'
        assert actual_lower['wave_completed_at'] == reference['available_at']
    origin_identity = _mapping(origin_completion['lower_wave_identity_evidence'])
    assert [(_mapping(origin_identity[field])['time'], _mapping(origin_identity[field])['value'])
            for field in ('origin', 'interior', 'endpoint')] == [
        ('2024-07-25', 3.45), ('2024-08-01', 3.96), ('2024-08-22', 5.01),
    ]
    assert origin_identity['available_at'] == origin_completion['available_at']
    geometry_origin = next(point for stroke in _records(third['strokes'])
                           for point in _records(stroke['points']) if point['time'] == '2024-07-25')
    qualified_origin = next(point for point in _records(qualified['points']) if point['time'] == '2024-07-25')
    for open_origin in (geometry_origin, qualified_origin):
        assert (open_origin['value'], open_origin['available_at'], open_origin['wave_identity_state']) == (
            3.45, '2024-08-30', 'owned')
        assert (open_origin['wave_direction_before'], open_origin['wave_direction_after']) == ('down', 'down')
        assert 'wave_completed_at' not in open_origin and 'wave_completion_evidence' not in open_origin
        down_upgrade = _mapping(open_origin['upgrade_evidence'])
        assert (down_upgrade['direction'], down_upgrade['role'], down_upgrade['available_at']) == (
            'down', 'endpoint', '2024-08-30')
        assert (_mapping(down_upgrade['origin'])['time'], _mapping(down_upgrade['origin'])['value']) == (
            '2023-07-04', 9.77)
        down_key = _mapping(down_upgrade['broken_key'])
        down_attack = _mapping(down_upgrade['confirmed_by'])
        assert (down_key['time'], down_key['value'], down_key['available_at']) == (
            '2022-10-10', 5.29, '2023-05-16')
        assert (down_attack['time'], down_attack['value'], down_attack['available_at']) == (
            '2024-07-25', 3.45, '2024-08-30')
        assert cast(str, down_key['available_at']) < cast(str, down_attack['time']) < cast(str, down_attack['available_at'])
        assert cast(float, down_attack['value']) < cast(float, down_key['value'])
        assert completed_source_points([open_origin], source_level=3) == []
    down_owned_key = next(point for stroke in _records(third['strokes'])
                          for point in _records(stroke['points']) if point['time'] == '2022-10-10')
    assert (down_owned_key['wave_identity_state'], down_owned_key['wave_completed_at']) == ('owned', '2023-05-16')
    before_origin = judge_trend(source_points, trend_level=3, asof='2024-08-29')
    assert not any(point['time'] == '2024-07-25' for point in before_origin.points)
    first_origin = judge_trend(source_points, trend_level=3, asof='2024-08-30')
    first_down_endpoint = next(point for point in first_origin.points if point['time'] == '2024-07-25')
    assert first_down_endpoint['available_at'] == '2024-08-30' and 'wave_completed_at' not in first_down_endpoint
    # 上涨策略许可仍独立等到3/7才成立；它既不重置已拥有的下降极值，
    # 也不能把这个开放端点升级成可供下一级消费的完成反转。
    outgoing_permission = _mapping(qualified_origin['trend_confirmation'])
    assert (outgoing_permission['direction'], outgoing_permission['available_at']) == ('up', '2025-03-07')
    assert identity(outgoing_permission, path=path) == wave_id
    dates = {bar.timestamp.date().isoformat(): index for index, bar in enumerate(bars)}
    whole_position = source_points.index(whole_origin)
    assert qualify_uptrend(source_points, whole_origin, whole_position, key, bars, dates['2025-03-06']) is None
    whole_proof = qualify_uptrend(source_points, whole_origin, whole_position, key, bars, dates['2025-03-07'])
    assert whole_proof is not None
    assert identity(whole_proof, path=path) == wave_id

    # Jan低点在Feb21仅补齐方向pair，尚未拥有上级整波身份。真正复合波
    # 等到已结束I H3/21在4/1可消费才成立，独立许可不得倒填到Feb28。
    local_facts = [point for stroke in _records(second['candidate_strokes'])
                   for point in _records(stroke['points']) if point['time'] == '2025-01-13']
    early_local = next(point for point in local_facts if point['available_at'] == '2025-02-21')
    assert early_local['wave_identity_state'] == 'direction_only'
    assert 'wave_completed_at' not in early_local
    assert [point['time'] for point in _records(early_local['definition_evidence'])] == [
        '2025-01-10', '2025-02-10', '2025-01-13', '2025-02-14']
    local_key = next(point for point in source_points if point['time'] == '2024-12-11')
    assert (local_key['value'], local_key['available_at'], local_key['wave_completed_at']) == (8.17, '2025-01-10', '2025-01-10')
    if cutoff < '2025-04-01':
        assert not any(point['time'] == '2025-01-13' for point in source_points)
        assert not any(point.get('wave_identity_state') == 'owned' for point in local_facts)
    else:
        local_origin = next(point for point in source_points if point['time'] == '2025-01-13')
        assert (local_origin['value'], local_origin['available_at'], local_origin['wave_completed_at']) == (
            5.51, '2025-04-01', '2025-04-01')
        completion = _mapping(local_origin['wave_completion_evidence'])
        identity_proof = _mapping(completion['lower_wave_identity_evidence'])
        assert [(_mapping(identity_proof[field])['time'], _mapping(identity_proof[field])['value'])
                for field in ('origin', 'interior', 'endpoint')] == [
            ('2025-01-13', 5.51), ('2025-02-10', 6.97), ('2025-03-21', 18.9)]
        assert _mapping(identity_proof['endpoint'])['available_at'] == completion['available_at'] == '2025-04-01'
        local_position = source_points.index(local_origin)
        for before_completion in ('2025-02-27', '2025-02-28', '2025-03-31'):
            assert qualify_uptrend(source_points, local_origin, local_position, local_key, bars,
                                   dates[before_completion]) is None
        local_proof = qualify_uptrend(source_points, local_origin, local_position, local_key, bars, dates['2025-04-01'])
        assert local_proof is not None
        assert (local_proof['direction'], local_proof['available_at']) == ('up', '2025-04-01')
        assert (_mapping(local_proof['confirmed_by'])['time'], _mapping(local_proof['confirmed_by'])['value']) == (
            '2025-04-01', 12.22)
        assert identity(local_proof, path=path) != wave_id
    # 尚未结束的二级H3/21不可提前变成三级下降起点；局部L4/29也不能重置
    # 三级整波来源。真实高级整波仍从3.45延伸，未来闭合不得倒填这些截止。
    assert not any(point['time'] in ('2025-03-21', '2025-04-29') for point in source_points)
    if cutoff >= '2025-04-02':
        open_high = next(point for stroke in _records(second['strokes'])
                         for point in _records(stroke['points']) if point['time'] == '2025-03-21')
        assert open_high['value'] == 18.9 and 'wave_completed_at' not in open_high
    assert not any(point.get('development_role') for stroke in _records(third['developing_strokes'])
                   for point in _records(stroke['points']))
