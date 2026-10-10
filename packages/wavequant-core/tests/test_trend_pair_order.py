"""六定义只要求两对高低同时严格同向，不规定最后一份证据的高低类型。"""

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import cast

import pytest

from wavequant.domain.market_structure.trend_judgement import judge_trend
from wavequant.domain.market_structure.trend_levels import trend_reversals


def _source(values: Sequence[int], *, level: int, mirror: bool = False) -> list[dict[str, object]]:
    """镜像同时交换高低类型和价格方向，仍保留同一个因果索引时钟。"""
    result: list[dict[str, object]] = []
    for index, price in enumerate(values):
        kind = 'L' if index % 2 == 0 else 'H'
        if mirror:
            kind, price = ('H' if kind == 'L' else 'L'), 100 - price
        result.append(dict(index=index, ordinal=0, kind=kind, value=price, time=str(index),
                           available_at=index + 1, state='confirmed', trend_level=level - 1))
    return result


def _evidence_values(point: Mapping[str, object]) -> list[object]:
    evidence = point['definition_evidence']
    assert isinstance(evidence, list)
    return [reference['value'] for reference in cast(list[dict[str, object]], evidence)]


@pytest.mark.parametrize('level', [1, 2, 3])
@pytest.mark.parametrize('mirror', [False, True])
def test_both_pair_orders_confirm_the_same_whole_wave_extreme(level: int, mirror: bool) -> None:
    # 前四点已建立下降方向。HL→HH 的最后证据为 H；HH→HL 的最后
    # 证据为 L，等价 L10 仅用来避免更早产生 HL，整波低点仍取最早坐标。
    first_low = _source([12, 24, 10, 20, 11, 22], level=level, mirror=mirror)
    first_high = _source([12, 24, 10, 20, 10, 22, 11], level=level, mirror=mirror)
    initial_kind, extreme_value = ('H', 90) if mirror else ('L', 10)
    direction = 'down' if mirror else 'up'
    expected_evidence = [90, 89, 80, 78] if mirror else [20, 22, 10, 11]

    for source in (first_low, first_high):
        before = deepcopy(source)
        assert trend_reversals(source[:-1], trend_level=level) == []
        points = trend_reversals(source, trend_level=level)
        assert [(point['index'], point['kind'], point['value']) for point in points] == [
            (2, initial_kind, extreme_value),
        ]
        point = points[0]
        assert point['wave_direction_after'] == direction
        assert point['available_at'] == len(source)
        field = 'confirmed_on_turn' if level == 1 else f'confirmed_on_level{level - 1}'
        assert point[field] == len(source) - 1
        assert _evidence_values(point) == expected_evidence
        assert 'flip' not in point
        assert point['trend_level'] == level and point['source_level'] == level - 1
        assert source == before


@pytest.mark.parametrize('level', [1, 2, 3])
@pytest.mark.parametrize('mirror', [False, True])
@pytest.mark.parametrize('suffix', [(10, 22), (11, 20), (10, 20), (9, 22)])
def test_equal_single_sided_or_mixed_evidence_cannot_reverse_the_wave(
    level: int, mirror: bool, suffix: tuple[int, int],
) -> None:
    source = _source([12, 24, 10, 20, *suffix], level=level, mirror=mirror)
    assert trend_reversals(source, trend_level=level) == []
    assert judge_trend(source, trend_level=level).points == ()


@pytest.mark.parametrize('level', [1, 2, 3])
@pytest.mark.parametrize('mirror', [False, True])
def test_delayed_last_pair_evidence_is_required_before_judgement(level: int, mirror: bool) -> None:
    source = _source([12, 24, 10, 20, 11, 22], level=level, mirror=mirror)
    source[-1]['available_at'] = 9
    assert judge_trend(source, trend_level=level, asof=8).points == ()
    point, = judge_trend(source, trend_level=level, asof=9).points
    assert point['index'] == 2 and point['available_at'] == 9
    assert judge_trend(source, trend_level=level, asof=9) == judge_trend(source, trend_level=level)
