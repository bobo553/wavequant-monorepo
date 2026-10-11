"""纯点判定的知识前缀、严格升级和递归快照，不依赖 K 线许可证。"""

from collections.abc import Mapping, MutableMapping, Sequence
from copy import deepcopy
from dataclasses import FrozenInstanceError
from datetime import date, timedelta
import json
from typing import cast

import pytest

from wavequant.domain.market_structure.trend_judgement import TrendJudgement, judge_trend, mutable_points
from wavequant.domain.market_structure.trend_level_upgrades import promote_trend_key_breaks
from wavequant.domain.market_structure.trend_levels import completed_source_points, trend_reversals


FIGURE_008 = (20, 30, 10, 20, 14, 26, 18, 35, 28, 45, 38, 42, 32, 36, 24, 29, 26)


def _source(values: tuple[int, ...], *, level: int = 1, mirror: bool = False,
            clock: str = 'index') -> list[dict[str, object]]:
    points: list[dict[str, object]] = []
    for index, price in enumerate(values):
        kind = 'L' if index % 2 == 0 else 'H'
        if mirror:
            kind, price = ('H' if kind == 'L' else 'L'), 100 - price
        time = str(index)
        known: int | str = index + 1
        if clock == 'numeric':
            known = str(index + 1)
        elif clock == 'date':
            time = (date(2024, 1, 1) + timedelta(days=index)).isoformat()
            known = (date(2024, 1, 1) + timedelta(days=index + 1)).isoformat()
        points.append(dict(index=index, ordinal=0, kind=kind, value=price, time=time, available_at=known,
                           state='confirmed', trend_level=level - 1, label=f'source-{index}'))
    return points


def _geometry(result: TrendJudgement) -> list[tuple[int, str, float, int | str]]:
    return [(cast(int, point['index']), cast(str, point['kind']), cast(float, point['value']),
             cast(int | str, point['available_at'])) for point in result.points]


@pytest.mark.parametrize('level', [1, 2, 3])
@pytest.mark.parametrize('mirror', [False, True])
def test_all_levels_keep_strict_mirrored_upgrade_without_a_future_holding_turn(level: int, mirror: bool) -> None:
    source = _source((*FIGURE_008, 50), level=level, mirror=mirror)
    before = deepcopy(source)
    result = judge_trend(source, trend_level=level)
    # L4> L2 后 H5>H3 已补齐多头证据；H11<H9 后 L12<L10 补齐空头证据。
    # 各自在6、13首次可知，不等待后一个同类 holding 点；严格本级突破仍在18。
    expected = [(2, 'L', 10, 6), (9, 'H', 45, 13), (14, 'L', 24, 18), (17, 'H', 50, 18)]
    if mirror:
        expected = [(index, 'H' if kind == 'L' else 'L', 100 - price, known)
                    for index, kind, price, known in expected]
    assert _geometry(result) == expected
    endpoint = result.points[-1]
    proof = endpoint['upgrade_evidence']
    assert isinstance(proof, Mapping) and proof['role'] == 'endpoint'
    direction = 'down' if mirror else 'up'
    assert proof['direction'] == endpoint['wave_direction_before'] == endpoint['wave_direction_after'] == direction
    key, attack = proof['broken_key'], proof['confirmed_by']
    assert isinstance(key, Mapping) and isinstance(attack, Mapping)
    assert (key['index'], key['value'], key['available_at']) == (9, 55 if mirror else 45, 13)
    assert (attack['index'], attack['value'], attack['available_at']) == (17, 50, 18)
    assert not any(point['index'] == 17 for point in judge_trend(source, trend_level=level, asof=17).candidates)
    assert (result.trend_level, result.source_level) == (level, level - 1)
    # reversal 仍表示公开几何拐点；严格同向尾端尚未结束整波，不能凭该状态递归。
    assert all(point['state'] == 'reversal' for point in result.points)
    assert [point['wave_completed_at'] for point in result.points[:-1]] == [6, 13, 18]
    assert 'wave_completed_at' not in endpoint and 'wave_completion_evidence' not in endpoint
    assert [point['label'] for point in result.points] == (['H1', 'L1', 'H2', 'L2'] if mirror else ['L1', 'H1', 'L2', 'H2'])
    assert result.candidates[0]['label'] == 'source-2'
    assert result.candidates[0]['state'] == 'confirmed'
    assert source == before


@pytest.mark.parametrize('level', [1, 2, 3])
@pytest.mark.parametrize('mirror', [False, True])
@pytest.mark.parametrize('cutoff,pivot,trigger', [(6, 2, 5), (13, 9, 12)])
def test_asof_confirms_only_when_both_kinds_are_known_without_waiting_for_a_holding_turn(
    level: int, mirror: bool, cutoff: int, pivot: int, trigger: int,
) -> None:
    source = _source(FIGURE_008, level=level, mirror=mirror)
    before = judge_trend(source, trend_level=level, asof=cutoff - 1)
    after = judge_trend(source, trend_level=level, asof=cutoff)
    # 第一例最后补齐的是H，第二例是L；方向取决于两类极值，知识日取齐全时点。
    # 完整输入中的未来 holding 点不能提前参与，冷前缀与asof投影必须完全一致。
    assert not any(point['index'] == pivot for point in before.candidates)
    fact = next(point for point in after.candidates if point['index'] == pivot)
    assert fact['available_at'] == cutoff
    field = 'confirmed_on_turn' if level == 1 else f'confirmed_on_level{level - 1}'
    assert fact[field] == trigger
    proof = cast(Sequence[Mapping[str, object]], fact['definition_evidence'])
    assert max(cast(int, point['available_at']) for point in proof) == cutoff
    assert len(proof) == 4 and {point['kind'] for point in proof} == {'H', 'L'}
    assert _geometry(after) == _geometry(judge_trend(source[:cutoff], trend_level=level))
    assert _geometry(after) == _geometry(judge_trend(source, trend_level=level, asof=cutoff + 1))


@pytest.mark.parametrize('clock,cutoff,known', [('numeric', '6', '6'), ('date', '2024-01-07', '2024-01-07')])
def test_numeric_and_date_cutoffs_match_the_source_clock(clock: str, cutoff: str, known: str) -> None:
    source = _source(FIGURE_008, clock=clock)
    assert _geometry(judge_trend(source, asof=cutoff)) == [(2, 'L', 10, known)]
    assert judge_trend(source, asof='14' if clock == 'numeric' else '2024-01-15').points[-1]['value'] == 45


@pytest.mark.parametrize('level', [1, 2, 3])
def test_filtered_or_unfinished_sources_keep_original_positions(level: int) -> None:
    source = _source((*FIGURE_008, 50), level=level)
    source[0]['available_at'] = 100
    source[4]['state'] = 'developing'
    result = judge_trend(source, trend_level=level, asof=18)
    endpoint = result.points[-1]
    field = 'source_turn_position' if level == 1 else f'source_level{level - 1}_position'
    trigger = 'confirmed_on_turn' if level == 1 else f'confirmed_on_level{level - 1}'
    assert endpoint['index'] == endpoint[field] == endpoint[trigger] == 17
    assert endpoint['available_at'] == 18


def test_later_higher_point_keeps_own_known_date_and_retains_replaced_event() -> None:
    source = _source((*FIGURE_008, 50, 30, 55, 35, 53, 29, 48))
    source[17]['available_at'] = 19
    earlier, later = judge_trend(source, asof=22), judge_trend(source, asof=23)
    # H19=55在20已严格突破旧已结束H9=45，之后H21=53仍高于旧关键位，
    # 但不能把整波最高55替换为局部53，也不能在回调L18/L20另起上级波。
    whole_high = max(source[17:22:2], key=lambda point: cast(int, point['value']))
    assert (whole_high['index'], whole_high['value']) == (19, 55)
    assert _geometry(earlier)[-1] == _geometry(later)[-1] == (19, 'H', 55, 20)
    assert not any(point['index'] in (18, 20) for point in later.points)
    assert 'wave_completed_at' not in earlier.points[-1]
    # 较低H21=53与较低L22=29在23才齐全；结束知识独立于几何20，不能回填22。
    assert later.points[-1]['wave_completed_at'] == 23
    completion = later.points[-1]['wave_completion_evidence']
    assert isinstance(completion, Mapping)
    assert (completion['index'], completion['value'], completion['available_at']) == (19, 55, 23)
    assert completion['wave_direction_before'] == 'up' and completion['wave_direction_after'] == 'down'
    evidence = completion['definition_evidence']
    assert isinstance(evidence, tuple)
    assert [(point['index'], point['value']) for point in cast(Sequence[Mapping[str, object]], evidence)] == (
        [(19, 55), (21, 53), (20, 35), (22, 29)])
    # H21虽仍在旧H9之上，却未延伸本级H19=55；它只作为真实下级反向
    # 证据保留在结束证明中，不再重复取得一个上级严格端点身份。
    assert [(point['index'], point['value'], point['available_at']) for point in later.candidates
            if point['index'] in (19, 21)] == [(19, 55, 20), (19, 55, 23)]
    assert (source[21]['kind'], source[21]['value'], source[21]['available_at']) == ('H', 53, 22)
    assert any(point['index'] == 21 and point['value'] == 53 for point in cast(Sequence[Mapping[str, object]], evidence))
    assert judge_trend(source[:22], asof=22) == earlier
    assert 'wave_completed_at' not in earlier.points[-1]
    assert all(left['kind'] != right['kind'] for left, right in zip(later.points, later.points[1:]))


@pytest.mark.parametrize('level', [1, 2, 3])
@pytest.mark.parametrize('mirror', [False, True])
def test_ordinary_reverse_fact_survives_an_earlier_same_coordinate_strict_endpoint(level: int, mirror: bool) -> None:
    source = _source((*FIGURE_008, 50, 30, 48, 28, 46), level=level, mirror=mirror)
    # H17严格突破先在18成立；更低H19与更低L20在21补齐反向普通定义。
    # 两份事实虽然坐标相同，方向和知识日不同，不能互相覆盖或倒写。
    before = judge_trend(source, trend_level=level, asof=20)
    frozen_before = mutable_points(before.points)
    after = judge_trend(source, trend_level=level, asof=21)
    earlier = [point for point in before.candidates if point['index'] == 17]
    facts = [point for point in after.candidates if point['index'] == 17]
    initial, reverse = ('down', 'up') if mirror else ('up', 'down')
    assert len(earlier) == 1 and earlier[0]['available_at'] == 18
    assert [(point['available_at'], point['wave_direction_after']) for point in facts] == [(18, initial), (21, reverse)]
    assert facts[0]['wave_direction_before'] == initial
    assert facts[1]['wave_direction_before'] == initial
    assert facts[0].get('upgrade_evidence') is not None
    assert facts[1].get('upgrade_evidence') is None
    assert facts[1]['confirmation_rule'] == 'ordered_HH_then_HL_or_LL_then_LH_switch'
    assert facts[1]['definition_evidence'] != facts[0].get('definition_evidence')
    geometry = [point for point in after.points if point['index'] == 17]
    assert len(geometry) == 1 and geometry[0]['available_at'] == 18
    assert geometry[0]['wave_direction_after'] == initial
    # 几何和最早strict事实不变；仅在真实反向21出现后加入整波结束知识。
    # 比较所有非结束字段，避免用只比较坐标掩盖许可证/来源被后来事实覆盖。
    completion_fields = {'wave_completed_at', 'wave_completion_evidence', 'wave_completion_policy'}
    assert [{key: value for key, value in point.items() if key not in completion_fields} for point in before.points] == (
        [{key: value for key, value in point.items() if key not in completion_fields} for point in after.points])
    assert 'wave_completed_at' not in before.points[-1]
    assert geometry[0]['wave_completed_at'] == 21
    completion = geometry[0]['wave_completion_evidence']
    assert isinstance(completion, Mapping)
    assert (completion['available_at'], completion['wave_direction_before'], completion['wave_direction_after']) == (
        21, initial, reverse)
    assert completion['definition_evidence'] == facts[1]['definition_evidence']
    assert mutable_points(before.points) == frozen_before
    assert before == judge_trend(source[:20], trend_level=level, asof=20)
    assert after == judge_trend(source[:21], trend_level=level, asof=21)
    if level < 3:
        early_source = completed_source_points(before.points, source_level=level)
        late_source = completed_source_points(after.points, source_level=level)
        assert not any(point['index'] == 17 for point in early_source)
        supplied = next(point for point in late_source if point['index'] == 17)
        assert (supplied['available_at'], supplied['source_geometry_available_at'], supplied['wave_completed_at']) == (21, 18, 21)


@pytest.mark.parametrize('level', [1, 2, 3])
@pytest.mark.parametrize('mirror', [False, True])
def test_later_strict_origin_fact_survives_an_earlier_same_coordinate_ordinary_origin(level: int, mirror: bool) -> None:
    source = _source((*FIGURE_008, 44, 30, 50), level=level, mirror=mirror)
    ordinary = trend_reversals(source, trend_level=level)
    events: list[dict[str, object]] = []
    earliest = promote_trend_key_breaks(source, ordinary, trend_level=level, event_sink=events)
    assert earliest == promote_trend_key_breaks(source, ordinary, trend_level=level)
    # 普通定义在H17=44可知的18成立，但44未超过本级旧H9=45。
    # H19=50在20才独立严格破位；几何继续保留早ordinary，账本追加晚strict。
    assert next(point for point in earliest if point['index'] == 14)['available_at'] == 18
    # event_sink同时保留真正owned普通事实和独立严格事实，不能只按坐标
    # 取第一项，把早ordinary18误当成晚strict20。
    assert [point['available_at'] for point in events if point['index'] == 14] == [18, 20]
    upgrade = next(point for point in events if point['index'] == 14
                   and isinstance(point.get('upgrade_evidence'), Mapping)
                   and cast(Mapping[str, object], point['upgrade_evidence'])['role'] == 'origin')
    assert upgrade['available_at'] == 20
    proof = upgrade['upgrade_evidence']
    assert isinstance(proof, Mapping) and proof['role'] == 'origin'
    assert proof['direction'] == ('down' if mirror else 'up')

    assert not any(point['index'] == 14 for point in judge_trend(source, trend_level=level, asof=17).candidates)
    before = judge_trend(source, trend_level=level, asof=18)
    after = judge_trend(source, trend_level=level, asof=20)
    assert len([point for point in before.candidates if point['index'] == 14]) == 1
    facts = [point for point in after.candidates if point['index'] == 14]
    assert [point['available_at'] for point in facts] == [18, 20]
    assert facts[0].get('upgrade_evidence') is None
    assert facts[1]['upgrade_evidence'] is not None
    geometry = next(point for point in after.points if point['index'] == 14)
    assert geometry['available_at'] == 18 and geometry.get('upgrade_evidence') is None
    assert geometry['wave_completed_at'] == 18
    assert not any(point['index'] == 18 for point in after.points)
    later_upgrade = facts[1]['upgrade_evidence']
    assert isinstance(later_upgrade, Mapping)
    strict_origin = later_upgrade['origin']
    assert isinstance(strict_origin, Mapping)
    assert strict_origin['index'] == 14


@pytest.mark.parametrize('high', (45, 50))
def test_equal_extreme_keeps_earliest_coordinate_and_breaks_only_a_closed_strictly_lower_key(high: int) -> None:
    result = judge_trend(_source((*FIGURE_008, high, 30, high, 35, high - 2, 29, high - 3)))
    highs = [point for point in result.points if point['value'] == high and cast(int, point['index']) >= 17]
    assert len(highs) == 1 and highs[0]['index'] == 17
    assert highs[0]['wave_completed_at'] == 23
    upgrades = [point for point in result.candidates if point['index'] == 19 and point.get('upgrade_evidence') is not None]
    assert upgrades == []
    if high == 45:
        # 两个H都只是相等触及已结束H9=45：不得严格升级。
        assert upgrades == []
    else:
        # 首次H17=50严格破旧已结束H9=45，但后来H19=50未延伸整波。
        # 同价不能重复授予端点身份；首份独立strict证据和真实闭合仍需保留。
        initial = [point for point in result.candidates if point['index'] == 17
                   and isinstance(point.get('upgrade_evidence'), Mapping)]
        assert len(initial) == 1
        proof = initial[0]['upgrade_evidence']
        assert isinstance(proof, Mapping)
        key, origin = proof['broken_key'], proof['origin']
        assert isinstance(key, Mapping) and isinstance(origin, Mapping)
        assert (key['index'], key['value'], key['available_at']) == (9, 45, 13)
        assert origin['index'] == 14 and proof['role'] == 'endpoint'
        assert initial[0]['available_at'] == 18
    completion = highs[0]['wave_completion_evidence']
    assert isinstance(completion, Mapping)
    refs = completion['definition_evidence']
    assert isinstance(refs, tuple)
    assert [(point['index'], point['value']) for point in cast(Sequence[Mapping[str, object]], refs)
            if point['kind'] == 'H'] == [(19, high), (21, high - 2)]
    assert not any(point['index'] == 18 for point in result.points)


def test_lower_license_is_removed_but_unknown_audit_is_copied_without_aliases() -> None:
    source = _source((*FIGURE_008, 50))
    lower: dict[str, object] = dict(direction='down', available_at=5, notes=['lower'])
    for point in source:
        point.update(confirmed_by=deepcopy(lower), incoming_trend_confirmation=deepcopy(lower),
                     incoming_trend_state='confirmed', active_trend_confirmation=deepcopy(lower),
                     trend_confirmation=deepcopy(lower), n_target_confirmation=deepcopy(lower),
                     incoming_leg_confirmation=deepcopy(lower), wave_id='lower-wave', audit=dict(tags=['kept']))
    before = deepcopy(source)
    result = judge_trend(source)
    for candidate in result.candidates:
        assert not any(field in candidate for field in ('incoming_trend_confirmation', 'incoming_trend_state',
            'active_trend_confirmation', 'trend_confirmation', 'n_target_confirmation',
            'incoming_leg_confirmation', 'wave_id'))
        audit = candidate['source_confirmation']
        assert isinstance(audit, Mapping) and audit['notes'] == ('lower',)
        assert isinstance(candidate['audit'], Mapping)
    assert source == before
    copied = mutable_points(result.points)
    cast(list[str], cast(dict[str, object], copied[0]['source_confirmation'])['notes']).append('changed')
    assert cast(Mapping[str, object], result.points[0]['source_confirmation'])['notes'] == ('lower',)


def test_deep_frozen_result_recurses_directly_and_copy_is_json_compatible() -> None:
    first = judge_trend(_source((*FIGURE_008, 50)))
    second = judge_trend(first.points, trend_level=2)
    assert judge_trend(second.points, trend_level=3).trend_level == 3
    with pytest.raises(FrozenInstanceError):
        setattr(first, 'points', ())
    with pytest.raises(TypeError):
        cast(MutableMapping[str, object], first.points[0])['value'] = 1000
    with pytest.raises(TypeError):
        cast(MutableMapping[str, object], first.points[-1]['upgrade_evidence'])['direction'] = 'down'
    copied = mutable_points(first.points)
    assert json.loads(json.dumps(copied))[0]['value'] == 10
    copied[0]['value'] = 1000
    assert first.points[0]['value'] == 10


def test_unknown_audit_set_with_tuple_keys_survives_the_copy_boundary() -> None:
    source = _source((*FIGURE_008, 50))
    source[2]['audit_keys'] = {(1, 2), (3, 4)}
    result = judge_trend(source)
    assert result.points[0]['audit_keys'] == frozenset({(1, 2), (3, 4)})
    assert mutable_points(result.points)[0]['audit_keys'] == source[2]['audit_keys']


@pytest.mark.parametrize('level', [False, True, 0, 4, 1.0, '1', None])
def test_invalid_level_is_rejected(level: object) -> None:
    with pytest.raises(ValueError, match='trend_level'):
        judge_trend([], trend_level=cast(int, level))


@pytest.mark.parametrize('cutoff', [False, True, -1, '', 2.0, object()])
def test_invalid_asof_is_rejected(cutoff: object) -> None:
    with pytest.raises(ValueError, match='asof'):
        judge_trend([], asof=cast(int | str, cutoff))


def test_mixed_clock_grade_jump_and_invalid_sequence_are_rejected() -> None:
    with pytest.raises(ValueError, match='clocks'):
        judge_trend(_source(FIGURE_008), asof='7')
    with pytest.raises(ValueError, match='immediately lower'):
        judge_trend(_source(FIGURE_008), trend_level=3)
    with pytest.raises(ValueError, match='sequence'):
        judge_trend(cast(Sequence[Mapping[str, object]], 'not points'))
