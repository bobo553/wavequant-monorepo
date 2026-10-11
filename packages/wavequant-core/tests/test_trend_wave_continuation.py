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
    assert (key['time'], key['value'], key['available_at']) == ('2023-07-04', 9.77, '2024-06-26')
    assert (trigger['time'], trigger['value']) == ('2025-03-07', 10.11)
    assert cast(str, key['available_at']) < cast(str, trigger['time']) <= cast(str, proof['available_at']) <= cutoff
    assert cast(float, trigger['value']) > cast(float, key['value'])
    assert any(all(point[field] == key[field] for field in ('index', 'ordinal', 'kind', 'value', 'available_at'))
               for point in _records(qualified['points']))
    assert cast(str, _records(tail['points'])[-1]['time']) <= cutoff
    wave_id = identity(proof, path=path)
    assert wave_id is not None and wave_id == tail['wave_id']
    # 策略发展波从已结束II低点取得本级market许可；证书不授予III整波
    # 几何身份。III仍只拥有9.77高点，7月低点不会由N/行情证书反向插入。
    whole_origin = next(point for point in source_points if point['time'] == '2024-07-25')
    assert (whole_origin['value'], whole_origin['available_at'], whole_origin['wave_completed_at']) == (
        3.45, '2024-10-21', '2024-10-21')
    assert not any(point['time'] == '2024-07-25' for point in _records(qualified['points']))
    assert not any(point['time'] == '2024-07-25' for stroke in _records(third['strokes'])
                   for point in _records(stroke['points']))
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
