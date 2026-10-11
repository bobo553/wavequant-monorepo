"""已知方向的外包十字星只延伸有已确认对向锚点约束的单极值。"""

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import cast

import pytest

from wavequant.domain.market_structure.lecture_drawing import lecture_drawing
from wavequant.domain.market_structure.trend_lines import (
    high_low_points_from_strokes, polyline_high_low_points, prepare_trend_lines,
)
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.chart_entry_history import _staged_chart_frames, chart_entry_history


Point = Mapping[str, object]
Row = tuple[float, float, float, float]
FIXTURE = Path(__file__).parent / 'fixtures/xiangyang_1999_trend_segments.json'


def _mapping(value: object) -> Point:
    assert isinstance(value, Mapping) and all(isinstance(key, str) for key in value)
    return cast(Point, value)


def _records(value: object) -> list[Point]:
    assert isinstance(value, (list, tuple))
    return [_mapping(item) for item in value]


def _draw(bars: Sequence[Bar]) -> dict[str, object]:
    draw = cast(Callable[[Sequence[Bar]], dict[str, object]], lecture_drawing)
    return draw(bars)


def _bars(rows: Sequence[Row], *, rising: bool = False) -> list[Bar]:
    data: list[Bar] = []
    for index, (opened, high, low, closed) in enumerate(rows):
        if rising:
            opened, high, low, closed = 30-opened, 30-low, 30-high, 30-closed
        data.append(Bar(datetime(2026, 1, index+1), 'TEST', opened, high, low, closed, 1000))
    return data


def _points(drawing: Point) -> list[Point]:
    strokes = _records(drawing['strokes'])
    assert len(strokes) == 1
    return _records(strokes[0]['points'])


def _coordinate(point: Point) -> tuple[object, object, object]:
    return point['time'], point['kind'], point['value']


@pytest.mark.parametrize('rising', [False, True])
@pytest.mark.parametrize('counter', [15.0, 16.0])
def test_bounded_outside_doji_extends_only_the_unfinished_extreme(
    rising: bool, counter: float,
) -> None:
    data = _bars([(12, 14, 10, 13), (14, 16, 12, 15), (13, 14, 11, 12),
                  (12, counter, 10, 12)], rising=rising)
    before = _points(_draw(data[:3]))
    drawing = _draw(data)
    assert drawing['issues'] == []
    after = _points(drawing)
    assert after[:-1] == before[:-1]
    assert len(after) == len(before)
    assert _coordinate(after[-1]) == ('2026-01-04', 'H' if rising else 'L', 20 if rising else 10)
    assert after[-1]['state'] == 'developing'
    assert after[-1]['available_at'] == '2026-01-04'
    assert after[-1]['intrabar_order_resolved'] is False
    assert drawing['intrabar_incomplete'] is True
    assert drawing['teaching_paths'] == []
    assert all(point['index'] != 3 for point in after[:-1]), '十字星对向极值不能补成同日转折'


@pytest.mark.parametrize('rising', [False, True])
def test_bounded_outside_doji_preserves_confirmed_history_through_repeated_extensions(rising: bool) -> None:
    data = _bars([(12, 14, 10, 13), (14, 16, 12, 15), (13, 14, 11, 12),
                  (12, 15, 10, 12), (12, 15.5, 9, 12), (13, 16, 12, 15)], rising=rising)
    original = _points(_draw(data[:3]))
    for length in (4, 5):
        drawing = _draw(data[:length])
        assert drawing['issues'] == []
        points = _points(drawing)
        assert points[:-1] == original[:-1]
        assert points[-1]['index'] == length-1
        assert points[-1]['state'] == 'developing'
        assert points[-1]['intrabar_order_resolved'] is False
    final = _draw(data)
    assert final['issues'] == []
    points = _points(final)
    assert points[:-2] == original[:-1]
    assert _coordinate(points[-2]) == ('2026-01-05', 'H' if rising else 'L', 21 if rising else 9)
    assert points[-2]['state'] == 'confirmed'
    assert points[-2]['available_at'] == '2026-01-06'
    assert points[-2]['intrabar_order_resolved'] is False


@pytest.mark.parametrize('rising', [False, True])
def test_outside_doji_still_breaks_when_the_opposite_extreme_crosses_its_confirmed_anchor(rising: bool) -> None:
    data = _bars([(12, 14, 10, 13), (14, 16, 12, 15), (13, 14, 11, 12),
                  (12, 16.1, 10, 12)], rising=rising)
    drawing = _draw(data)
    assert [issue['index'] for issue in _records(drawing['issues'])] == [3]
    assert _points(drawing) == _points(_draw(data[:3]))
    assert drawing['teaching_paths'] == []


@pytest.mark.parametrize('rising', [False, True])
def test_outside_doji_does_not_treat_a_seed_as_a_confirmed_opposite_anchor(rising: bool) -> None:
    data = _bars([(14, 16, 12, 13), (13, 14, 11, 12), (12, 15, 10, 12)], rising=rising)
    assert _points(_draw(data[:2]))[-2]['state'] == 'seed'
    drawing = _draw(data)
    assert [issue['index'] for issue in _records(drawing['issues'])] == [2]
    assert _points(drawing) == _points(_draw(data[:2]))


@pytest.mark.parametrize('rising', [False, True])
def test_outside_doji_requires_a_strict_new_directional_extreme(rising: bool) -> None:
    data = _bars([(12, 14, 10, 13), (14, 16, 12, 15), (13, 14, 11, 12),
                  (13.5, 14, 11, 12), (12, 15, 11, 12)], rising=rising)
    before = _draw(data[:4])
    assert _points(before)[-1]['index'] == 2
    drawing = _draw(data)
    assert [issue['index'] for issue in _records(drawing['issues'])] == [4]
    assert _points(drawing) == _points(before)


@pytest.fixture(scope='module')
def xiangyang_1999() -> tuple[Bar, ...]:
    captured = _mapping(json.loads(FIXTURE.read_text(encoding='utf-8')))
    bars: list[Bar] = []
    rows = captured['rows']
    assert isinstance(rows, list)
    for row in rows:
        assert isinstance(row, list) and len(row) == 6 and isinstance(row[0], str)
        values = row[1:]
        assert all(type(value) in (int, float) for value in values)
        prices = cast(list[float], values)
        bars.append(Bar(datetime.fromisoformat(row[0]), 'sz.000678',
                        prices[0], prices[1], prices[2], prices[3], prices[4]))
    return tuple(bars)


def _prefix(bars: Sequence[Bar], asof: str) -> list[Bar]:
    return [bar for bar in bars if bar.timestamp.date().isoformat() <= asof]


def _geometry(bars: Sequence[Bar], asof: str) -> dict[str, object]:
    return prepare_trend_lines(polyline_high_low_points(_draw(_prefix(bars, asof))),
                               trend_level=1, symbol='sz.000678')


def test_real_bounded_outside_doji_keeps_the_reported_trend_in_one_path(xiangyang_1999: tuple[Bar, ...]) -> None:
    prefix = _prefix(xiangyang_1999, '1999-04-30')
    drawing = _draw(prefix)
    assert drawing['issues'] == []
    source = polyline_high_low_points(drawing)
    assert {point['source_path'] for point in source} == {'lecture-0'}
    bundle = prepare_trend_lines(source, trend_level=1, symbol='sz.000678')
    assert bundle['_path_market_end_indices'] == {'lecture-0': None}
    strokes = _records(bundle['strokes'])
    assert len(strokes) == 1
    points = _records(strokes[0]['points'])
    pairs = list(zip(points, points[1:]))
    matches = [(high, low) for high, low in pairs
               if _coordinate(high) == ('1999-04-05', 'H', 10.18)
               and _coordinate(low) == ('1999-04-22', 'L', 8.80)]
    assert len(matches) == 1, [_coordinate(point) for point in points]
    high, low = matches[0]
    assert high['available_at'] == high['wave_completed_at'] == '1999-04-09'
    assert low['available_at'] == '1999-04-23'
    assert low['wave_completed_at'] == '1999-04-30'
    recursive = high_low_points_from_strokes(strokes)
    recursive_low = [point for point in recursive if _coordinate(point) == _coordinate(low)]
    assert len(recursive_low) == 1 and recursive_low[0]['available_at'] == '1999-04-30'
    assert recursive_low[0]['source_geometry_available_at'] == '1999-04-23'


@pytest.mark.parametrize('asof', ['1999-04-23', '1999-04-26', '1999-04-29'])
def test_real_low_cannot_enter_the_next_level_before_its_own_wave_ends(
    xiangyang_1999: tuple[Bar, ...], asof: str,
) -> None:
    bundle = _geometry(xiangyang_1999, asof)
    strokes = _records(bundle['strokes'])
    assert len(strokes) == 1 and strokes[0]['source_path'] == 'lecture-0'
    points = _records(strokes[0]['points'])
    low = [point for point in points if _coordinate(point) == ('1999-04-22', 'L', 8.80)]
    assert len(low) == 1
    assert low[0]['available_at'] == '1999-04-23'
    assert 'wave_completed_at' not in low[0]
    assert ('1999-04-22', 'L', 8.80) not in [
        _coordinate(point) for point in high_low_points_from_strokes(strokes)
    ]


def test_real_prefixes_keep_earlier_confirmed_polyline_points_and_never_read_future_prices(
    xiangyang_1999: tuple[Bar, ...],
) -> None:
    dates = [bar.timestamp.date().isoformat() for bar in xiangyang_1999
             if '1999-04-05' <= bar.timestamp.date().isoformat() <= '1999-04-30']
    confirmed: dict[tuple[object, object, object], Point] = {}
    for asof in dates:
        drawing = _draw(_prefix(xiangyang_1999, asof))
        assert drawing['issues'] == [], asof
        points = _points(drawing)
        current = {_coordinate(point): point for point in points if point['state'] == 'confirmed'}
        for coordinate, previous in confirmed.items():
            assert coordinate in current, (asof, coordinate)
            assert current[coordinate]['available_at'] == previous['available_at'], (asof, coordinate)
        assert all(cast(str, point['time']) <= asof and cast(str, point['available_at']) <= asof
                   for point in points)
        confirmed = current


def _bounded_history(*, rising: bool) -> list[Bar]:
    return _bars([(12, 14, 10, 13), (14, 16, 12, 15), (13, 14, 11, 12),
                  (12, 15, 10, 12), (12, 15.5, 9, 12), (13, 16, 12, 15),
                  (12, 14, 11, 13), (14, 17, 13, 16)], rising=rising)


@pytest.mark.parametrize('rising', [False, True])
def test_staged_history_uses_each_prefix_own_continuous_single_extreme_source(rising: bool) -> None:
    data = _bounded_history(rising=rising)
    with _staged_chart_frames(data, resume_start=0, closed=[], previous_epoch=None,
                              previous_raw=[]) as (frames, stages):
        rows = zip(frames.iter_rows(), *(stage.iter_rows() for stage in stages))
        count = 0
        for frame, first, second, third in rows:
            count += 1
            prefix = data[:frame.index+1]
            drawing = _draw(prefix)
            expected_paths = [dict(id=path['id'], points=path['points'])
                              for path in _records(drawing['strokes'])]
            assert frame.epoch == 0
            assert frame.drawing['strokes'] == expected_paths
            source = polyline_high_low_points(drawing)
            staged_source = [point for path in _records(first['source_strokes'])
                             for point in _records(path['points'])]
            assert staged_source == source
            assert all(point['source_path'] == 'lecture-0' for point in staged_source)
            expected_first = prepare_trend_lines(source, trend_level=1, symbol='TEST')
            expected_second = prepare_trend_lines(high_low_points_from_strokes(expected_first['strokes']),
                                                  trend_level=2, symbol='TEST')
            expected_third = prepare_trend_lines(high_low_points_from_strokes(expected_second['strokes']),
                                                 trend_level=3, symbol='TEST')
            assert (first, second, third) == (expected_first, expected_second, expected_third)
            asof = prefix[-1].timestamp.date().isoformat()
            assert all(cast(str, point['time']) <= asof and cast(str, point['available_at']) <= asof
                       for point in frame.raw)
            if frame.index in (3, 4):
                same_day = [point for point in frame.raw if point['index'] == frame.index]
                assert len(same_day) == 1
                assert same_day[0]['kind'] == ('H' if rising else 'L')
                assert same_day[0]['state'] == 'developing'
                assert same_day[0]['intrabar_order_resolved'] is False
            if frame.index == 5:
                prior_endpoint = [point for point in frame.raw if point['index'] == 4]
                assert len(prior_endpoint) == 1
                assert prior_endpoint[0]['state'] == 'confirmed'
                assert prior_endpoint[0]['available_at'] == '2026-01-06'
        assert count == len(data)


@pytest.mark.parametrize('rising', [False, True])
def test_chart_history_cache_replays_bounded_doji_partial_and_appended_bars_like_fresh(rising: bool) -> None:
    data = _bounded_history(rising=rising)
    cache: dict[str, object] = {}
    for length in (3, 4):
        prefix = data[:length]
        assert chart_entry_history(prefix, prefix_cache=cache) == chart_entry_history(prefix)
    # 同一日的未完十字星先有较浅极值；后来完整行情只能影响自己的快照。
    partial = replace(data[3], high=19.8) if rising else replace(data[3], low=10.2)
    partial_prefix = [*data[:3], partial]
    assert chart_entry_history(partial_prefix, prefix_cache=cache) == chart_entry_history(partial_prefix)
    for length in (4, 5, 6, 7, 8, 8):
        prefix = data[:length]
        assert chart_entry_history(prefix, prefix_cache=cache) == chart_entry_history(prefix)
    key = cache['key']
    assert isinstance(key, tuple)
    version = 'polyline_owned_completed_source_geometry_independent_permissions_v118'
    assert version in key
    # 已改变折线来源语义的旧检查点必须被拒绝，不能借其较早断路历史。
    stale_cache: dict[str, object] = dict(cache, key=tuple(
        version.removesuffix('v118')+'v117' if item == version else item for item in key
    ), checkpoint=object())
    assert chart_entry_history(data, prefix_cache=stale_cache) == chart_entry_history(data)
    assert stale_cache['key'] == cache['key']
