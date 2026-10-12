"""十字母线归并仅改变基础折线未结束尾端，不推断另一极值顺序。"""

from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from datetime import datetime
from typing import cast

import pytest

from wavequant.domain.market_structure.lecture_drawing import lecture_drawing
from wavequant.domain.market_structure.polyline import observe_polyline
from wavequant.domain.market_structure.price_action import Direction
from wavequant.domain.models.model import Bar


Point = Mapping[str, object]
Row = tuple[float, float, float, float]
BASE: tuple[Row, ...] = ((12, 14, 10, 13), (14, 16, 12, 15), (13, 14, 11, 12))


def _bars(rows: Sequence[Row], *, rising: bool) -> list[Bar]:
    result = []
    for index, (opened, high, low, closed) in enumerate(rows):
        if rising:
            opened, high, low, closed = 30-opened, 30-low, 30-high, 30-closed
        result.append(Bar(datetime(2026, 1, index+1), 'TEST', opened, high, low, closed, 1000))
    return result


def _records(value: object) -> list[Point]:
    assert isinstance(value, (list, tuple))
    result = []
    for item in value:
        assert isinstance(item, Mapping) and all(isinstance(key, str) for key in item)
        result.append(cast(Point, item))
    return result


def _draw(bars: Sequence[Bar]) -> dict[str, object]:
    return cast(Callable[[Sequence[Bar]], dict[str, object]], lecture_drawing)(bars)


def _points(drawing: Point) -> list[Point]:
    paths = _records(drawing['strokes'])
    assert len(paths) == 1
    assert paths[0]['id'] == 'lecture-0'
    return _records(paths[0]['points'])


@pytest.mark.parametrize('rising', [False, True])
def test_opposite_crossing_of_a_doji_does_not_cut_the_single_extreme_lecture_path(rising: bool) -> None:
    bars = _bars([*BASE, (12, 17, 10, 12)], rising=rising)
    previous = _points(_draw(bars[:3]))

    drawing = _draw(bars)

    assert drawing['issues'] == []
    points = _points(drawing)
    assert points[:-1] == previous[:-1]
    assert len(points) == len(previous)
    assert (points[-1]['index'], points[-1]['kind'], points[-1]['value']) == (
        3, 'H' if rising else 'L', 20 if rising else 10)
    assert points[-1]['state'] == 'developing'
    assert points[-1]['available_at'] == '2026-01-04'
    assert points[-1]['intrabar_order_resolved'] is False
    assert points[-1]['drawing_source'] == 'lecture_known_direction_single_extreme_for_outside_doji'
    assert drawing['intrabar_incomplete'] is True
    assert drawing['incomplete'] is False
    assert drawing['teaching_paths'] == []
    assert all(point['index'] != 3 for point in points[:-1])


@pytest.mark.parametrize('rising', [False, True])
def test_equal_incoming_extreme_keeps_its_old_coordinate_and_clock_even_after_opposite_crossing(rising: bool) -> None:
    bars = _bars([*BASE, (12, 17, 11, 12)], rising=rising)
    previous = _draw(bars[:3])

    drawing = _draw(bars)

    assert drawing['issues'] == []
    assert _points(drawing) == _points(previous)
    assert drawing['intrabar_incomplete'] is True
    assert drawing['teaching_paths'] == []


@pytest.mark.parametrize('rising', [False, True])
def test_repeated_crossing_doji_extensions_wait_for_a_later_real_opposite_leg(rising: bool) -> None:
    bars = _bars([*BASE, (12, 17, 10, 12), (12, 18, 9, 12),
                  (12, 19, 9, 12), (13, 20, 12, 18)], rising=rising)
    original = _points(_draw(bars[:3]))
    prior_tail: Point | None = None
    for length in (4, 5, 6):
        drawing = _draw(bars[:length])
        assert drawing['issues'] == []
        points = _points(drawing)
        assert points[:-1] == original[:-1]
        assert len(points) == len(original)
        assert points[-1]['state'] == 'developing'
        if length == 6:
            assert points[-1] == prior_tail
        prior_tail = points[-1]

    drawing = _draw(bars)

    assert drawing['issues'] == []
    points = _points(drawing)
    assert prior_tail is not None
    assert points[:-2] == original[:-1]
    assert points[-2] == dict(prior_tail, state='confirmed', available_at='2026-01-07')
    assert points[-2]['index'] == 4
    assert points[-2]['intrabar_order_resolved'] is False
    assert (points[-1]['index'], points[-1]['kind']) == (6, 'L' if rising else 'H')
    assert points[-1]['state'] == 'developing'
    assert all(point['index'] != 5 for point in points)


@pytest.mark.parametrize('rising', [False, True])
@pytest.mark.parametrize('bullish_mother', [False, True])
@pytest.mark.parametrize('equal_mother_extreme', [False, True])
def test_a_later_coloured_mother_joins_the_known_equal_child_endpoint_after_crossing_dojis(
    rising: bool, bullish_mother: bool, equal_mother_extreme: bool,
) -> None:
    mother_low = 11.0 if equal_mother_extreme else 10.0
    mother: Row = ((12, 19, mother_low, 18) if bullish_mother else (18, 19, mother_low, 12))
    bars = _bars([*BASE, (12, 17, 11, 12), (12, 18, 11, 12), mother], rising=rising)
    previous = _points(_draw(bars[:3]))
    assert _points(_draw(bars[:5])) == previous

    drawing = _draw(bars)

    assert drawing['issues'] == []
    points = _points(drawing)
    assert points[:len(previous)-1] == previous[:-1]
    assert all(point['index'] not in (3, 4) for point in points)
    mother_points = [point for point in points if point['index'] == 5]
    assert len(mother_points) == (1 if equal_mother_extreme and bullish_mother else 2)
    assert all(point['available_at'] == '2026-01-06' and point['intrabar_order_resolved'] is False
               for point in mother_points)
    assert mother_points[-1]['state'] == 'developing'
    if equal_mother_extreme or not bullish_mother:
        old_endpoint = [point for point in points if point['index'] == 2]
        assert old_endpoint == [dict(previous[-1], state='confirmed', available_at='2026-01-06')]
    assert _records(drawing['teaching_paths'])[0]['source'] == 'known_child_endpoint_to_coloured_mother'


@pytest.mark.parametrize('rising', [False, True])
def test_step_snapshots_of_crossing_dojis_equal_independent_causal_prefixes(rising: bool) -> None:
    bars = _bars([*BASE, (12, 17, 10, 12), (12, 18, 9, 12),
                  (12, 19, 9, 12), (13, 20, 12, 18)], rising=rising)
    snapshots: dict[int, list[Point]] = {}

    def collect(index: int, epoch: int, points: Sequence[Point]) -> None:
        assert epoch == 0
        snapshots[index] = deepcopy(list(points))

    draw = cast(Callable[..., dict[str, object]], lecture_drawing)
    draw(bars, on_step=collect)

    for index in range(2, len(bars)):
        assert snapshots[index] == _points(_draw(bars[:index+1]))
        asof = bars[index].timestamp.date().isoformat()
        assert all(cast(str, point['available_at']) <= asof for point in snapshots[index])


@pytest.mark.parametrize('rising', [False, True])
def test_lecture_single_extreme_reduction_does_not_supply_observed_intrabar_evidence(rising: bool) -> None:
    bars = _bars([*BASE, (12, 17, 10, 12)], rising=rising)
    assert _draw(bars)['issues'] == []

    strict = observe_polyline(bars, symbol='TEST', timeframe='1d', start_index=2,
                              initial_direction=Direction.UP if rising else Direction.DOWN)

    assert strict.blocked_at == 3
    assert strict.reversals == ()
