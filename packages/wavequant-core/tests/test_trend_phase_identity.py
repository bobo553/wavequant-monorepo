"""严格旧键开启新整波后，方向定义的旧点不能替代该整波的真实极值。"""

from collections.abc import Mapping
from copy import deepcopy
from datetime import date, timedelta
from typing import cast

import pytest

from wavequant.domain.market_structure.trend_judgement import judge_trend, mutable_points
from wavequant.domain.market_structure.trend_levels import completed_source_points


START = date(2026, 1, 1)
PRICES = (10, 20, 12, 22, 15, 21, 14, 25, 13, 24, 12)


def _moment(index: int, integer_clock: bool) -> int | str:
    return index if integer_clock else (START + timedelta(days=index)).isoformat()


def _source(*, mirror: bool, integer_clock: bool) -> list[dict[str, object]]:
    return [dict(index=index, ordinal=0, kind=('H' if mirror else 'L') if index % 2 == 0 else
                 ('L' if mirror else 'H'), value=100 - value if mirror else value,
                 time=(START + timedelta(days=index)).isoformat(), available_at=_moment(index + 1, integer_clock),
                 state='confirmed', trend_level=0)
            for index, value in enumerate(PRICES)]


def _mapping(value: object) -> Mapping[str, object]:
    assert isinstance(value, Mapping)
    return value


def _terminal_point(*, mirror: bool, integer_clock: bool) -> dict[str, object]:
    result = judge_trend(_source(mirror=mirror, integer_clock=integer_clock), trend_level=1)
    return next(point for point in mutable_points(result.points) if point['index'] == 9)


@pytest.mark.parametrize('mirror', (False, True))
@pytest.mark.parametrize('integer_clock', (False, True))
def test_a_strict_new_wave_closes_at_its_own_extreme_while_retaining_the_old_direction_reference(
    mirror: bool, integer_clock: bool,
) -> None:
    source = _source(mirror=mirror, integer_clock=integer_clock)
    original = deepcopy(source)
    early = judge_trend(source, trend_level=1, asof=_moment(10, integer_clock))
    closed = judge_trend(source, trend_level=1, asof=_moment(11, integer_clock))
    assert [point['index'] for point in closed.points] == [3, 8, 9]
    early_endpoint = next(point for point in early.points if point['index'] == 9)
    endpoint = next(point for point in closed.points if point['index'] == 9)
    assert early_endpoint['available_at'] == endpoint['available_at'] == _moment(10, integer_clock)
    assert 'wave_completed_at' not in early_endpoint
    assert endpoint['wave_completed_at'] == _moment(11, integer_clock)
    assert endpoint['value'] == (76 if mirror else 24)
    # H7=25 的攻击价格日恰是旧 H3=22 首知日，不能取得旧键升级许可；
    # H9=24 后来才严格突破同一已知旧键，并从 L8=13 开启自己的上升整波。
    # 因此 H7 仍须保留为 LH 定义的真实比较点，但它不属于 L8 之后的新整波。
    early_proof = _mapping(early_endpoint['upgrade_evidence'])
    assert _mapping(early_proof['broken_key'])['index'] == 3
    assert _mapping(early_proof['broken_key'])['available_at'] == _moment(7, integer_clock)
    assert _mapping(early_proof['origin'])['index'] == 8
    assert _mapping(early_proof['confirmed_by'])['index'] == 9
    assert not any(point['index'] == 7 and 'upgrade_evidence' in point for point in closed.candidates)
    completion = _mapping(endpoint['wave_completion_evidence'])
    definition = cast(tuple[Mapping[str, object], ...], completion['definition_evidence'])
    assert {reference['index'] for reference in definition} == {7, 8, 9, 10}
    phase = _mapping(_mapping(completion['lower_wave_identity_evidence'])['wave_origin'])
    assert (phase['index'], phase['kind'], phase['value'], phase['available_at']) == (
        8, 'H' if mirror else 'L', 87 if mirror else 13, _moment(9, integer_clock))
    projected = completed_source_points(closed.points, source_level=1)
    effective = next(point for point in projected if point['index'] == 9)
    assert effective['available_at'] == _moment(11, integer_clock)
    assert effective['source_geometry_available_at'] == _moment(10, integer_clock)
    assert not any(point['index'] == 9 for point in completed_source_points(early.points, source_level=1))
    assert source == original


@pytest.mark.parametrize('mirror', (False, True))
@pytest.mark.parametrize('integer_clock', (False, True))
def test_phase_completion_is_identical_for_cold_prefix_and_direct_or_projected_recursion(
    mirror: bool, integer_clock: bool,
) -> None:
    source = _source(mirror=mirror, integer_clock=integer_clock)
    for size in range(1, len(source) + 1):
        actual = judge_trend(source, trend_level=1, asof=_moment(size, integer_clock))
        assert actual == judge_trend(source[:size], trend_level=1)
    closed = judge_trend(source, trend_level=1)
    projected = completed_source_points(closed.points, source_level=1)
    assert judge_trend(closed.points, trend_level=2) == judge_trend(projected, trend_level=2)
    # 一级开启后立即反转并不授予二级同段身份，真实闭合知识只是二级的来源门槛。
    assert judge_trend(closed.points, trend_level=2).points == ()


@pytest.mark.parametrize('mirror', (False, True))
@pytest.mark.parametrize('integer_clock', (False, True))
@pytest.mark.parametrize('mutation', (
    'missing_phase', 'same_kind', 'not_left', 'future_phase', 'wrong_leg', 'invalid_price',
    'old_phase_boundary', 'false_current_extreme',
))
def test_phase_boundaries_cannot_hide_a_false_current_extreme_or_borrow_later_knowledge(
    mirror: bool, integer_clock: bool, mutation: str,
) -> None:
    point = _terminal_point(mirror=mirror, integer_clock=integer_clock)
    completion = cast(dict[str, object], point['wave_completion_evidence'])
    identity = cast(dict[str, object], completion['lower_wave_identity_evidence'])
    phase = cast(dict[str, object], identity['wave_origin'])
    if mutation == 'missing_phase':
        identity.pop('wave_origin')
    elif mutation == 'same_kind':
        phase['kind'] = point['kind']
    elif mutation == 'not_left':
        phase['index'] = point['index']
    elif mutation == 'future_phase':
        phase['available_at'] = _moment(12, integer_clock)
    elif mutation == 'wrong_leg':
        phase['value'] = cast(int, point['value']) + (-1 if mirror else 1)
    elif mutation == 'invalid_price':
        phase['value'] = 'invalid'
    elif mutation == 'old_phase_boundary':
        # 把起点拉回旧高 H7 之前，H7 就重新属于本波；此时较小 H9 必须拒绝。
        phase.update(_source(mirror=mirror, integer_clock=integer_clock)[6])
    else:
        definition = cast(list[dict[str, object]], completion['definition_evidence'])
        current = next(reference for reference in definition if reference['index'] == 9)
        current['value'] = cast(int, point['value']) + (-1 if mirror else 1)
    with pytest.raises(ValueError):
        completed_source_points([point], source_level=1)
