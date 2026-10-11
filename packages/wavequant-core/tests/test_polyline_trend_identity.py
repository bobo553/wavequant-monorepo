"""折线四点定义直接成立一级趋势；趋势段递归仍须组合波或严格升级身份。"""

from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import date, timedelta
from typing import cast

import pytest

from wavequant.domain.market_structure.trend_judgement import judge_trend, mutable_points
from wavequant.domain.market_structure.trend_levels import completed_source_points


START = date(2026, 1, 1)
PRICES = (2, 80, 5, 100, 10, 90, 30, 85, 20, 95, 25)
POLICY = 'whole_wave_v117'
BASIS = 'polyline_four_point_definition'


def _moment(index: int, integer_clock: bool) -> int | str:
    return index if integer_clock else (START + timedelta(days=index)).isoformat()


def _source(values: Sequence[int], *, level: int, mirror: bool,
            integer_clock: bool) -> list[dict[str, object]]:
    # 裸 confirmed 声明的是直接来源已反转结束。grade0 是折线反转，grade1/2
    # 则是已存在的下级趋势段，不能仅因复用了同一价格公式而绕过趋势升格门禁。
    return [dict(index=index, ordinal=0, kind=('H' if mirror else 'L') if index % 2 == 0 else
                 ('L' if mirror else 'H'), value=200 - value if mirror else value,
                 time=(START + timedelta(days=index)).isoformat(),
                 available_at=_moment(index + 1, integer_clock), state='confirmed', trend_level=level - 1)
            for index, value in enumerate(values)]


def _mapping(value: object) -> Mapping[str, object]:
    assert isinstance(value, Mapping)
    return value


def _closed_polyline_point(*, mirror: bool, integer_clock: bool) -> dict[str, object]:
    result = judge_trend(_source(PRICES, level=1, mirror=mirror, integer_clock=integer_clock), trend_level=1)
    points = mutable_points(result.points)
    assert [point['index'] for point in points] == [3, 4]
    return points[0]


@pytest.mark.parametrize('mirror', (False, True))
@pytest.mark.parametrize('integer_clock', (False, True))
def test_polyline_pairs_publish_the_whole_extreme_without_requiring_an_upper_composite_wave(
    mirror: bool, integer_clock: bool,
) -> None:
    source = _source(PRICES, level=1, mirror=mirror, integer_clock=integer_clock)
    original = deepcopy(source)
    result = judge_trend(source, trend_level=1)
    assert [(point['index'], point['available_at'], point['wave_completed_at']) for point in result.points] == [
        (3, _moment(9, integer_clock), _moment(9, integer_clock)),
        (4, _moment(11, integer_clock), _moment(11, integer_clock)),
    ]
    assert [point['value'] for point in result.points] == ([100, 190] if mirror else [100, 10])
    for point in result.points:
        assert point['source_level'] == 0 and point['trend_level'] == 1
        assert point['wave_identity_state'] == 'owned' and point['wave_completion_policy'] == POLICY
        assert 'upgrade_evidence' not in point
        identity = _mapping(point['lower_wave_identity_evidence'])
        assert identity['basis'] == BASIS and 'interior' not in identity
        assert _mapping(identity['origin'])['index'] == point['index']
        assert identity['available_at'] == point['wave_completed_at']
    # H100 后的全区间最低仍是相邻 L10；后来严格下降 H90→85、L30→20
    # 已足够确认一级负反转。不能把“一级折线定义”误当成“二级单段升级”。
    first = result.points[0]
    assert _mapping(_mapping(first['lower_wave_identity_evidence'])['endpoint'])['index'] == 4
    # 共同证据固定按两高、两低分组；价格镜像后种类互换，来源坐标保持原值。
    assert [ref['index'] for ref in cast(Sequence[Mapping[str, object]], first['definition_evidence'])] == (
        [6, 8, 5, 7] if mirror else [5, 7, 6, 8])
    assert [ref['index'] for ref in cast(Sequence[Mapping[str, object]], result.points[1]['definition_evidence'])] == (
        [8, 10, 7, 9] if mirror else [7, 9, 8, 10])
    assert source == original


@pytest.mark.parametrize('level', (2, 3))
@pytest.mark.parametrize('mirror', (False, True))
@pytest.mark.parametrize('integer_clock', (False, True))
def test_the_same_completed_trend_source_keeps_single_child_facts_out_of_upper_geometry(
    level: int, mirror: bool, integer_clock: bool,
) -> None:
    source = _source(PRICES, level=level, mirror=mirror, integer_clock=integer_clock)
    result = judge_trend(source, trend_level=level)
    assert result.points == ()
    assert [(point['index'], point['available_at']) for point in result.candidates] == [
        (3, _moment(9, integer_clock)), (4, _moment(11, integer_clock)),
    ]
    assert all(point['wave_identity_state'] == 'direction_only' for point in result.candidates)
    assert all('wave_completed_at' not in point for point in result.candidates)
    assert completed_source_points(result.candidates, source_level=level) == []


@pytest.mark.parametrize('mirror', (False, True))
@pytest.mark.parametrize('integer_clock', (False, True))
def test_real_first_level_completion_is_consumable_but_does_not_license_the_same_leg_at_level_two(
    mirror: bool, integer_clock: bool,
) -> None:
    first = judge_trend(_source(PRICES, level=1, mirror=mirror, integer_clock=integer_clock), trend_level=1)
    projected = completed_source_points(first.points, source_level=1)
    assert [(point['index'], point['available_at']) for point in projected] == [
        (3, _moment(9, integer_clock)), (4, _moment(11, integer_clock)),
    ]
    second_direct = judge_trend(first.points, trend_level=2)
    second_adapter = judge_trend(projected, trend_level=2)
    assert second_direct == second_adapter
    assert second_direct.points == () and second_direct.candidates == ()
    assert judge_trend(second_direct.points, trend_level=3).points == ()


@pytest.mark.parametrize('level', (1, 2, 3))
@pytest.mark.parametrize('mirror', (False, True))
@pytest.mark.parametrize('integer_clock', (False, True))
def test_ordinary_identity_is_known_only_when_both_strict_pairs_are_available(
    level: int, mirror: bool, integer_clock: bool,
) -> None:
    source = _source(PRICES, level=level, mirror=mirror, integer_clock=integer_clock)
    for size in range(1, len(source) + 1):
        actual = judge_trend(source, trend_level=level, asof=_moment(size, integer_clock))
        assert actual == judge_trend(source[:size], trend_level=level)
        expected = ([3] if size >= 9 else []) + ([4] if size >= 11 else [])
        assert {(point['index'], point['available_at']) for point in actual.candidates} == {
            (index, _moment(9 if index == 3 else 11, integer_clock)) for index in expected
        }
        assert [point['index'] for point in actual.points] == (expected if level == 1 else [])


@pytest.mark.parametrize('level', (1, 2, 3))
@pytest.mark.parametrize('mirror', (False, True))
@pytest.mark.parametrize(('high', 'low'), ((85, 30), (90, 20), (85, 35), (95, 20)))
def test_equal_or_mixed_pairs_do_not_create_a_polyline_reversal_or_an_upper_license(
    level: int, mirror: bool, high: int, low: int,
) -> None:
    # 原末两高 90→85、末两低30→20；分别改成等高、等低或高低异向。
    values = (*PRICES[:7], high, low)
    result = judge_trend(_source(values, level=level, mirror=mirror, integer_clock=True), trend_level=level)
    assert result.points == () and result.candidates == ()


@pytest.mark.parametrize('mirror', (False, True))
@pytest.mark.parametrize('integer_clock', (False, True))
@pytest.mark.parametrize('mutation', (
    'missing_basis', 'wrong_basis', 'missing_identity', 'missing_definition', 'source_level', 'trend_level',
    'future_origin', 'future_endpoint', 'future_definition', 'equal_highs', 'equal_lows', 'mixed_pairs',
))
def test_polyline_identity_cannot_be_forged_into_a_recursive_or_noncausal_completion(
    mirror: bool, integer_clock: bool, mutation: str,
) -> None:
    point = _closed_polyline_point(mirror=mirror, integer_clock=integer_clock)
    completion = cast(dict[str, object], point['wave_completion_evidence'])
    identity = cast(dict[str, object], completion['lower_wave_identity_evidence'])
    definition = cast(list[dict[str, object]], completion['definition_evidence'])
    source_level = 1
    if mutation == 'missing_basis':
        identity.pop('basis')
    elif mutation == 'wrong_basis':
        identity['basis'] = 'unlicensed_direct_child'
    elif mutation == 'missing_identity':
        completion.pop('lower_wave_identity_evidence')
    elif mutation == 'missing_definition':
        completion.pop('definition_evidence')
    elif mutation == 'source_level':
        point['source_level'] = 1
    elif mutation == 'trend_level':
        point.update(trend_level=2, source_level=1)
        source_level = 2
    elif mutation in ('future_origin', 'future_endpoint'):
        cast(dict[str, object], identity[mutation.removeprefix('future_')])['available_at'] = _moment(10, integer_clock)
    elif mutation == 'future_definition':
        definition[-1]['available_at'] = _moment(10, integer_clock)
    elif mutation in ('equal_highs', 'equal_lows'):
        kind = 'H' if mutation == 'equal_highs' else 'L'
        refs = [ref for ref in definition if ref['kind'] == kind]
        refs[1]['value'] = refs[0]['value']
    else:
        refs = [ref for ref in definition if ref['kind'] == ('H' if mirror else 'L')]
        refs[1]['value'] = cast(int, refs[0]['value']) + (-10 if mirror else 10)
    with pytest.raises(ValueError):
        completed_source_points([point], source_level=source_level)
