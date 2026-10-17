"""跨级只消费结束的下级整波；同向关键位突破延长整波，不复制局部回调。"""

from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from datetime import date, timedelta
from typing import cast

import pytest

from wavequant.domain.market_structure import trend_levels
from wavequant.domain.market_structure.trend_judgement import TrendJudgement, judge_trend, mutable_points
from wavequant.domain.market_structure.trend_lines import high_low_points_from_strokes, prepare_trend_lines


START = date(2026, 1, 1)
POLICY = 'whole_wave_v117'
FIGURE = (20, 30, 10, 20, 14, 26, 18, 35, 28, 45, 38, 42, 32, 36, 24, 29, 26)
LATE_CLOSE = (*FIGURE, 50, 30, 48, 32, 46, 28)
ProjectSource = Callable[..., list[dict[str, object]]]


def _moment(index: int, integer_clock: bool) -> int | str:
    return index if integer_clock else (START + timedelta(days=index)).isoformat()


def _source(values: Sequence[int], *, source_level: int, mirror: bool, integer_clock: bool) -> list[dict[str, object]]:
    # 裸 confirmed 来源维持已有兼容契约，表示下级确已反转结束的点。
    # 需要“提前严格端点”时，使用另一次真实 judge 输出，绝不伪造完成证据。
    return [dict(index=index, ordinal=0, kind=('H' if mirror else 'L') if index % 2 == 0 else
                 ('L' if mirror else 'H'), value=100 - value if mirror else value,
                 time=(START + timedelta(days=index)).isoformat(), available_at=_moment(index + 1, integer_clock),
                 state='confirmed', trend_level=source_level, label=f'p{index}')
            for index, value in enumerate(values)]


def _point(judgement: TrendJudgement, index: int) -> Mapping[str, object]:
    return next(point for point in judgement.points if point['index'] == index)


def _mapping(value: object) -> Mapping[str, object]:
    assert isinstance(value, Mapping)
    return value


def _records(value: object) -> list[Mapping[str, object]]:
    assert isinstance(value, (list, tuple))
    assert all(isinstance(item, Mapping) for item in value)
    return cast(list[Mapping[str, object]], list(value))


def _geometry(points: Sequence[Mapping[str, object]]) -> list[tuple[object, object, object, object]]:
    return [(point['index'], point['kind'], point['value'], point['available_at']) for point in points]


def _project(points: Sequence[Mapping[str, object]], *, source_level: int) -> list[dict[str, object]]:
    project = getattr(trend_levels, 'completed_source_points', None)
    assert callable(project), 'whole-wave completion needs the shared source projection'
    return cast(ProjectSource, project)(points, source_level=source_level)


def _lower_snapshots(*, lower_level: int, mirror: bool, integer_clock: bool,
                     attack: int = 50) -> tuple[TrendJudgement, TrendJudgement]:
    values = (*FIGURE, attack, 30, 48 if attack == 50 else 42, 32, 46 if attack == 50 else 40, 28)
    source = _source(values, source_level=lower_level - 1, mirror=mirror, integer_clock=integer_clock)
    early = judge_trend(source, trend_level=lower_level, asof=_moment(18, integer_clock))
    closed = judge_trend(source, trend_level=lower_level, asof=_moment(23, integer_clock))
    return early, closed


@pytest.mark.parametrize('level', (1, 2, 3))
@pytest.mark.parametrize('mirror', (False, True))
@pytest.mark.parametrize('integer_clock', (False, True))
def test_hh_hl_continuation_does_not_turn_each_source_pullback_into_an_upper_reversal(
    level: int, mirror: bool, integer_clock: bool,
) -> None:
    values = (*FIGURE, 50, 30, 60, 32, 70)
    source = _source(values, source_level=level - 1, mirror=mirror, integer_clock=integer_clock)
    before = deepcopy(source)
    full = judge_trend(source, trend_level=level)
    assert [point['index'] for point in full.points] == [2, 9, 14, 21]
    assert source == before
    # H17→H19→H21 都是同一上升整波的延伸，L18/L20 只是下级回调。
    # 严格破位的每份事实仍留在 candidates，不能靠删审计事件达到压缩。
    endpoints = [point for point in full.candidates
                 if _mapping(point.get('upgrade_evidence', {})).get('role') == 'endpoint']
    assert {17, 19, 21}.issubset({cast(int, point['index']) for point in endpoints})
    assert not any(point['index'] in (18, 20) for point in full.points)
    assert _point(full, 14)['value'] == (76 if mirror else 24)
    terminal = _point(full, 21)
    assert terminal['value'] == (30 if mirror else 70)
    assert terminal['available_at'] == _moment(22, integer_clock)
    assert terminal['wave_completion_policy'] == POLICY and 'wave_completed_at' not in terminal
    for end, endpoint in ((18, 17), (20, 19), (22, 21)):
        prefix = judge_trend(source, trend_level=level, asof=_moment(end, integer_clock))
        cold = judge_trend(source[:end], trend_level=level)
        assert _geometry(prefix.points) == _geometry(cold.points)
        assert [point['index'] for point in prefix.points] == [2, 9, 14, endpoint]


@pytest.mark.parametrize('level', (1, 2, 3))
@pytest.mark.parametrize('mirror', (False, True))
def test_ordinary_direction_needs_both_high_and_low_pairs_and_keeps_the_whole_extreme(
    level: int, mirror: bool,
) -> None:
    source = _source(FIGURE[:13], source_level=level - 1, mirror=mirror, integer_clock=True)
    before = judge_trend(source, trend_level=level, asof=12)
    assert [point['index'] for point in before.points] == [2]
    # H11 已成为较低高点，但最后低点 L12 尚未知；不能把单个反转当整波翻向。
    after = judge_trend(source, trend_level=level, asof=13)
    assert [point['index'] for point in after.points] == [2, 9]
    high = _point(after, 9)
    assert high['available_at'] == high['wave_completed_at'] == 13
    evidence = _mapping(high['wave_completion_evidence'])
    assert evidence['wave_direction_before'] != evidence['wave_direction_after']
    assert len(cast(Sequence[object], evidence['definition_evidence'])) == 4


@pytest.mark.parametrize('level', (1, 2, 3))
@pytest.mark.parametrize('mirror', (False, True))
@pytest.mark.parametrize('integer_clock', (False, True))
def test_a_strict_endpoint_keeps_early_geometry_but_only_a_real_later_flip_closes_it(
    level: int, mirror: bool, integer_clock: bool,
) -> None:
    early, closed = _lower_snapshots(lower_level=level, mirror=mirror, integer_clock=integer_clock)
    early_endpoint = _point(early, 17)
    final_endpoint = _point(closed, 17)
    assert early_endpoint['available_at'] == final_endpoint['available_at'] == _moment(18, integer_clock)
    assert early_endpoint['wave_direction_before'] == early_endpoint['wave_direction_after']
    assert early_endpoint['wave_completion_policy'] == POLICY
    assert 'wave_completed_at' not in early_endpoint and 'wave_completion_evidence' not in early_endpoint
    assert final_endpoint['wave_completed_at'] == _moment(23, integer_clock)
    closing = _mapping(final_endpoint['wave_completion_evidence'])
    assert closing['index'] == 17 and closing['available_at'] == _moment(23, integer_clock)
    assert closing['wave_direction_before'] != closing['wave_direction_after']
    assert 'wave_completed_at' not in early_endpoint  # 后快照不能改写较早的只读快照。


@pytest.mark.parametrize('lower_level', (1, 2))
@pytest.mark.parametrize('mirror', (False, True))
@pytest.mark.parametrize('integer_clock', (False, True))
def test_recursive_source_projects_its_own_closure_23_without_backdating_geometry_18(
    lower_level: int, mirror: bool, integer_clock: bool,
) -> None:
    early, closed = _lower_snapshots(lower_level=lower_level, mirror=mirror, integer_clock=integer_clock)
    before = _project(early.points, source_level=lower_level)
    after = _project(closed.points, source_level=lower_level)
    assert not any(point['index'] == 17 for point in before)
    supplied = next(point for point in after if point['index'] == 17)
    assert supplied['available_at'] == _moment(23, integer_clock)
    assert supplied['source_geometry_available_at'] == _moment(18, integer_clock)
    assert _point(closed, 17)['available_at'] == _moment(18, integer_clock)
    # 两条公开路径读取相同完成来源：直接递归 judge 与图表 stroke 适配。
    adapted = high_low_points_from_strokes([dict(id='real-lower', points=closed.points, trend_level=lower_level)])
    adapted_endpoint = next(point for point in adapted if point['index'] == 17)
    assert adapted_endpoint['available_at'] == _moment(23, integer_clock)
    assert adapted_endpoint['source_geometry_available_at'] == _moment(18, integer_clock)
    direct = judge_trend(closed.points, trend_level=lower_level + 1, asof=_moment(23, integer_clock))
    prepared = prepare_trend_lines(adapted, trend_level=lower_level + 1)
    line = [point for stroke in _records(prepared['strokes']) for point in _records(stroke['points'])]
    assert _geometry(line) == _geometry(direct.points)


@pytest.mark.parametrize('level', (2, 3))
@pytest.mark.parametrize('mirror', (False, True))
@pytest.mark.parametrize('integer_clock', (False, True))
def test_one_completed_lower_leg_can_strictly_break_an_already_known_upper_key(
    level: int, mirror: bool, integer_clock: bool,
) -> None:
    early, closed = _lower_snapshots(lower_level=level - 1, mirror=mirror, integer_clock=integer_clock)
    source = _source((*FIGURE, 50), source_level=level - 1, mirror=mirror, integer_clock=integer_clock)
    source[-1] = dict(_point(early, 17))
    ongoing = judge_trend(source, trend_level=level, asof=_moment(23, integer_clock))
    assert not any(point['index'] == 17 for point in ongoing.candidates)
    source[-1] = dict(_point(closed, 17))
    before = judge_trend(source, trend_level=level, asof=_moment(22, integer_clock))
    after = judge_trend(source, trend_level=level, asof=_moment(23, integer_clock))
    assert not any(point['index'] == 17 for point in before.candidates)
    endpoint = _point(after, 17)
    assert endpoint['available_at'] == _moment(23, integer_clock)
    evidence = _mapping(endpoint['upgrade_evidence'])
    assert evidence['role'] == 'endpoint'
    assert _mapping(evidence['broken_key'])['index'] == 9
    assert _mapping(evidence['confirmed_by'])['available_at'] == _moment(23, integer_clock)
    assert endpoint[f'source_level{level - 1}_available_at'] == _moment(23, integer_clock)
    assert 'wave_completed_at' not in endpoint  # 本级新端点不能马上再自动升一级。


@pytest.mark.parametrize('level', (2, 3))
@pytest.mark.parametrize('integer_clock', (False, True))
@pytest.mark.parametrize('invalid', ('developing', 'display', 'open'))
def test_only_formal_strict_endpoint_can_attack_before_lower_wave_completion(
    level: int, integer_clock: bool, invalid: str,
) -> None:
    early, closed = _lower_snapshots(lower_level=level - 1, mirror=False, integer_clock=integer_clock)
    source = _source((*FIGURE, 50), source_level=level - 1, mirror=False, integer_clock=integer_clock)
    point = dict(_point(early if invalid == 'open' else closed, 17))
    if invalid == 'developing':
        point['state'] = 'developing'
    elif invalid == 'display':
        point['display_only'] = True
    source[-1] = point
    result = judge_trend(source, trend_level=level, asof=_moment(23, integer_clock))
    if invalid == 'open':
        endpoint = _point(result, 17)
        proof = _mapping(endpoint['upgrade_evidence'])
        assert endpoint['available_at'] == _moment(18, integer_clock)
        assert _mapping(proof['broken_key'])['index'] == 9
        assert _mapping(proof['origin'])['index'] == 14
        assert 'wave_completed_at' not in endpoint
        assert 'wave_completed_at' not in point
    else:
        assert not any(candidate['index'] == 17 for candidate in result.candidates)


@pytest.mark.parametrize('level', (2, 3))
@pytest.mark.parametrize('mirror', (False, True))
@pytest.mark.parametrize('integer_clock', (False, True))
@pytest.mark.parametrize('blocked', ('equal', 'future_key'))
def test_completed_lower_endpoints_cannot_upgrade_an_equal_or_not_yet_known_upper_key(
    level: int, mirror: bool, integer_clock: bool, blocked: str,
) -> None:
    attack = 45 if blocked == 'equal' else 50
    _, closed = _lower_snapshots(lower_level=level - 1, mirror=mirror, integer_clock=integer_clock, attack=attack)
    source = _source((*FIGURE, attack), source_level=level - 1, mirror=mirror, integer_clock=integer_clock)
    source[-1] = dict(_point(closed, 17))
    if blocked == 'future_key':
        # 真实 L12 的迟知使 upper H9 的结束日推迟到18，晚于攻击价格索引17；
        # 不能因为 lower H17 到23才结束，就把晚知 upper key 倒带回攻击价格日。
        source[12]['available_at'] = _moment(18, integer_clock)
    result = judge_trend(source, trend_level=level, asof=_moment(23, integer_clock))
    assert not any(point['index'] == 17 and _mapping(point.get('upgrade_evidence', {})).get('role') == 'endpoint'
                   for point in result.candidates)


@pytest.mark.parametrize('level', (2, 3))
@pytest.mark.parametrize('mirror', (False, True))
@pytest.mark.parametrize('integer_clock', (False, True))
def test_a_later_observed_closed_source_keeps_three_independent_knowledge_dates(
    level: int, mirror: bool, integer_clock: bool,
) -> None:
    _, closed = _lower_snapshots(lower_level=level - 1, mirror=mirror, integer_clock=integer_clock)
    actual = _point(closed, 17)
    proof = actual['wave_completion_evidence']
    # 模拟历史观察器在27重新观察到同坐标事实：仅改变接口观察知识，真实
    # 几何18、整波结束23及其证明均来自公共judge，不能把观察27倒填为结束。
    observed = dict(actual, available_at=_moment(27, integer_clock),
                    source_geometry_available_at=_moment(18, integer_clock))
    before = dict(observed)
    projected = _project([observed], source_level=level - 1)
    assert len(projected) == 1
    supplied = projected[0]
    assert supplied['available_at'] == _moment(27, integer_clock)
    assert supplied['wave_completed_at'] == _moment(23, integer_clock)
    assert supplied['source_geometry_available_at'] == _moment(18, integer_clock)
    assert supplied['wave_completion_evidence'] == proof
    assert _project(projected, source_level=level - 1) == projected
    adapted = high_low_points_from_strokes([dict(id='later-observation', points=[observed])])
    assert adapted[0]['available_at'] == _moment(27, integer_clock)
    assert adapted[0]['wave_completed_at'] == _moment(23, integer_clock)
    assert adapted[0]['source_geometry_available_at'] == _moment(18, integer_clock)
    source = _source((*FIGURE, 50), source_level=level - 1, mirror=mirror, integer_clock=integer_clock)
    source[-1] = observed
    early = judge_trend(source, trend_level=level, asof=_moment(26, integer_clock))
    later = judge_trend(source, trend_level=level, asof=_moment(27, integer_clock))
    assert not any(point['index'] == 17 for point in early.candidates)
    assert _point(later, 17)['available_at'] == _moment(27, integer_clock)
    assert observed == before and actual['available_at'] == _moment(18, integer_clock)


@pytest.mark.parametrize('integer_clock', (False, True))
@pytest.mark.parametrize('invalid', (
    'unsupported_policy', 'orphan_policy', 'missing_evidence', 'open_with_evidence',
    'early_completion', 'endpoint_coordinate', 'endpoint_value', 'ordinal', 'same_direction',
    'missing_source_kind', 'equal_pair', 'future_known', 'future_price',
))
def test_generated_ordinary_completion_rejects_invalid_proofs_without_weakening_legacy_sources(
    integer_clock: bool, invalid: str,
) -> None:
    _, closed = _lower_snapshots(lower_level=1, mirror=False, integer_clock=integer_clock)
    point = mutable_points(_project(closed.points, source_level=1))[-1]
    assert point['index'] == 17
    evidence = cast(dict[str, object], point['wave_completion_evidence'])
    if invalid == 'unsupported_policy':
        point['wave_completion_policy'] = 'unknown'
    elif invalid == 'orphan_policy':
        point.pop('wave_completion_policy')
    elif invalid == 'missing_evidence':
        point.pop('wave_completion_evidence')
    elif invalid == 'open_with_evidence':
        point.pop('wave_completed_at')
    elif invalid == 'early_completion':
        point['wave_completed_at'] = _moment(17, integer_clock)
        evidence['available_at'] = _moment(17, integer_clock)
    elif invalid == 'endpoint_coordinate':
        evidence['index'] = 16
    elif invalid == 'endpoint_value':
        evidence['value'] = 49
    elif invalid == 'ordinal':
        evidence['ordinal'] = 1
    elif invalid == 'same_direction':
        evidence['wave_direction_after'] = evidence['wave_direction_before']
    else:
        refs = cast(list[dict[str, object]], evidence['definition_evidence'])
        if invalid == 'missing_source_kind':
            refs[2]['kind'] = 'H'
        elif invalid == 'equal_pair':
            refs[1]['value'] = refs[0]['value']
        elif invalid == 'future_known':
            refs[1]['available_at'] = _moment(24, integer_clock)
        else:
            # 价格坐标也必须当时已发生，不能只验证其“自称”知识日<=结束23。
            # 两高仍按顺序下降，改变的是后一个高点的未来价格位置和日期。
            refs[1]['index'] = 100
            refs[1]['time'] = (START + timedelta(days=100)).isoformat()
    unchanged = deepcopy(point)
    with pytest.raises(ValueError, match='completion|completed|wave'):
        _project([point], source_level=1)
    assert point == unchanged


@pytest.mark.parametrize('integer_clock', (False, True))
@pytest.mark.parametrize('mirror', (False, True))
@pytest.mark.parametrize('level', (1, 2, 3))
def test_an_early_true_strict_closure_remains_the_old_key_when_ordinary_closure_arrives_later(
    integer_clock: bool, mirror: bool, level: int,
) -> None:
    source = _source((*FIGURE[:16], 24, 50, 30, 45, 20), source_level=level - 1,
                     mirror=mirror, integer_clock=integer_clock)
    source[18]['available_at'] = _moment(20, integer_clock)
    unchanged = deepcopy(source)
    # L16=L14使普通上涨不足；H17在18严格破旧H9，真实整波L14先结束。
    # L18=30迟至20可知才补齐普通HH+HL。它不能把已结束key18拖晚到20，
    # 因为反向L20的价格索引恰为20：晚普通证明同日才知，不是旧key。
    early = judge_trend(source, trend_level=level, asof=_moment(18, integer_clock))
    before_attack = judge_trend(source, trend_level=level, asof=_moment(20, integer_clock))
    final = judge_trend(source, trend_level=level, asof=_moment(21, integer_clock))
    first_key = _point(early, 14)
    assert first_key['wave_completed_at'] == _moment(18, integer_clock)
    assert _mapping(first_key['upgrade_evidence'])['role'] == 'origin'
    key_facts = [point for point in final.candidates if point['index'] == 14]
    assert [point['available_at'] for point in key_facts] == [_moment(18, integer_clock), _moment(20, integer_clock)]
    assert key_facts[0].get('upgrade_evidence') is not None
    assert key_facts[1].get('upgrade_evidence') is None
    assert _point(final, 14)['wave_completed_at'] == _moment(18, integer_clock)
    assert not any(point['index'] == 20 for point in before_attack.candidates)
    endpoint = _point(final, 20)
    proof = _mapping(endpoint['upgrade_evidence'])
    key = _mapping(proof['broken_key'])
    assert proof['direction'] == ('up' if mirror else 'down') and proof['role'] == 'endpoint'
    assert (key['index'], key['value'], key['available_at']) == (14, 76 if mirror else 24, _moment(18, integer_clock))
    assert endpoint['value'] == (80 if mirror else 20)
    assert endpoint['available_at'] == _moment(21, integer_clock)
    assert 'wave_completed_at' not in endpoint
    assert _point(final, 17)['wave_completed_at'] == _moment(21, integer_clock)
    assert before_attack == judge_trend(source[:20], trend_level=level, asof=_moment(20, integer_clock))
    assert early.points[-1].get('wave_completed_at') is None
    assert source == unchanged


@pytest.mark.parametrize('integer_clock', (False, True))
@pytest.mark.parametrize('mirror', (False, True))
def test_ordinary_completion_rejects_an_endpoint_inside_its_own_proven_whole_extreme(
    integer_clock: bool, mirror: bool,
) -> None:
    _, closed = _lower_snapshots(lower_level=1, mirror=mirror, integer_clock=integer_clock)
    point = mutable_points(_project(closed.points, source_level=1))[-1]
    assert point['index'] == 17
    evidence = point['wave_completion_evidence']
    assert isinstance(evidence, dict)
    refs = evidence['definition_evidence']
    assert isinstance(refs, list) and all(isinstance(ref, Mapping) for ref in refs)
    # 来自真实结束23的证明包含H19=48（镜像L19=52）；即便把端点与其
    # 自身坐标证明同步改价，H40/L60也不可能是该整波最高/最低，必须拒绝。
    assert any(ref['index'] == 19 and ref['value'] == (52 if mirror else 48)
               for ref in cast(Sequence[Mapping[str, object]], refs))
    point['value'] = evidence['value'] = 60 if mirror else 40
    unchanged = deepcopy(point)
    with pytest.raises(ValueError, match='completion|completed|wave'):
        _project([point], source_level=1)
    assert point == unchanged


@pytest.mark.parametrize('integer_clock', (False, True))
@pytest.mark.parametrize('mirror', (False, True))
@pytest.mark.parametrize('invalid', ('future_attack', 'late_key', 'equal_break', 'endpoint_role', 'wrong_origin'))
def test_generated_strict_completion_requires_a_causal_opposite_whole_wave_origin(
    integer_clock: bool, mirror: bool, invalid: str,
) -> None:
    values = (*FIGURE[:16], 24, 50)
    raw = _source(values, source_level=0, mirror=mirror, integer_clock=integer_clock)
    result = judge_trend(raw)
    # L16与L14相等，普通HH+HL不能结束下跌。L14只凭严格突破H9的真实证据
    # 结束，因此本样本独立覆盖strict分支，不手造“已结束”的证明。
    point = mutable_points(_project(result.points, source_level=1))[-1]
    assert point['index'] == 14
    evidence = cast(dict[str, object], point['wave_completion_evidence'])
    assert 'definition_evidence' not in evidence
    upgrade = cast(dict[str, object], evidence['upgrade_evidence'])
    attack = cast(dict[str, object], upgrade['confirmed_by'])
    key = cast(dict[str, object], upgrade['broken_key'])
    if invalid == 'future_attack':
        attack['index'] = 100
        attack['time'] = (START + timedelta(days=100)).isoformat()
    elif invalid == 'late_key':
        key['available_at'] = _moment(17, integer_clock)
    elif invalid == 'equal_break':
        attack['value'] = key['value']
    elif invalid == 'endpoint_role':
        upgrade['role'] = 'endpoint'
    else:
        cast(dict[str, object], upgrade['origin'])['index'] = 16
    unchanged = deepcopy(point)
    with pytest.raises(ValueError, match='completion|completed|wave'):
        _project([point], source_level=1)
    assert point == unchanged
