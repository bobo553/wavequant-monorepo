"""完整原始行情的包含归并、三级递归与首次知识／整波完成回归。"""

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import cast

import pytest

from wavequant.domain.market_structure.lecture_drawing import lecture_drawing
from wavequant.domain.market_structure.trend_judgement import judge_trend
from wavequant.domain.market_structure.trend_lines import (
    high_low_points_from_strokes, polyline_high_low_points, prepare_trend_lines,
)
from wavequant.domain.models.model import Bar


Point = Mapping[str, object]
Geometry = tuple[dict[str, object], ...]
Facts = tuple[tuple[Point, ...], ...]
Clock = tuple[int, str, str, float, str, str]
FIXTURE = Path(__file__).parent / 'fixtures/xiangyang_directional_doji_original.json'
FIELDS = ('index', 'ordinal', 'time', 'kind', 'value', 'available_at', 'wave_completed_at',
          'source_path', 'trend_level', 'wave_identity_state')
DOJIS = ('2018-05-23', '2020-11-16', '2021-02-09')
CLOCKS: tuple[Clock, ...] = (
    (1, '2018-04-27', 'H', 7.06, '2018-05-02', '2018-05-17'),
    (1, '2018-05-31', 'L', 5.54, '2018-05-31', '2018-06-04'),
    (1, '2018-05-31', 'H', 5.99, '2018-06-06', '2018-06-06'),
    (1, '2020-11-02', 'L', 4.67, '2020-11-03', '2020-11-10'),
    (1, '2020-11-20', 'H', 5.76, '2020-11-23', '2020-11-27'),
    (1, '2021-02-09', 'L', 4.39, '2021-02-19', '2021-02-19'),
    (1, '2021-02-22', 'H', 4.84, '2021-02-25', '2021-02-25'),
    (2, '2018-04-27', 'H', 7.06, '2018-06-04', '2018-06-04'),
    (2, '2020-11-02', 'L', 4.67, '2020-11-10', '2020-11-27'),
    (2, '2020-11-20', 'H', 5.76, '2021-01-05', '2021-01-05'),
    (2, '2021-01-08', 'L', 4.35, '2021-01-15', '2021-04-29'),
)


def _mapping(value: object) -> Point:
    assert isinstance(value, Mapping) and all(isinstance(key, str) for key in value)
    return cast(Point, value)


def _records(value: object) -> list[Point]:
    assert isinstance(value, (list, tuple))
    return [_mapping(item) for item in value]


def _draw(bars: Sequence[Bar]) -> dict[str, object]:
    return cast(Callable[[Sequence[Bar]], dict[str, object]], lecture_drawing)(bars)


def _day(bar: Bar) -> str:
    return bar.timestamp.date().isoformat()


def _prefix(bars: Sequence[Bar], asof: str) -> tuple[Bar, ...]:
    return tuple(bar for bar in bars if _day(bar) <= asof)


def _points(bundle: Point) -> list[Point]:
    return [point for stroke in _records(bundle['strokes']) for point in _records(stroke['points'])]


def _matching(points: Sequence[Point], day: str, kind: str, value: float) -> list[Point]:
    return [point for point in points if (point['time'], point['kind'], point['value']) == (day, kind, value)]


def _geometry(bars: Sequence[Bar]) -> Geometry:
    source = polyline_high_low_points(_draw(bars))
    levels = []
    for level in (1, 2, 3):
        prepared = prepare_trend_lines(source, trend_level=level, symbol='sz.000678')
        assert prepared['qualified_strokes'] == []
        assert prepared['source_level'] == level-1
        actual_source = [point for stroke in _records(prepared['source_strokes'])
                         for point in _records(stroke['points'])]
        assert actual_source == source
        levels.append(prepared)
        if level < 3:
            # 真实公共适配器核验整波结束；绝不将 qualified_strokes 当来源。
            source = high_low_points_from_strokes(prepared['strokes'])
            assert all(point['trend_level'] == level and 'wave_completed_at' in point for point in source)
            assert all(point['available_at'] == max(cast(str, point['source_geometry_available_at']),
                                                   cast(str, point['wave_completed_at'])) for point in source)
    return tuple(levels)


@pytest.fixture(scope='module')
def real_bars() -> tuple[Bar, ...]:
    captured = _mapping(json.loads(FIXTURE.read_text(encoding='utf-8')))
    assert captured['symbol'] == 'sz.000678'
    assert captured['source'] == 'akshare' and captured['upstream'] == 'sina'
    assert captured['provider_version'] == '1.18.94' and captured['price_basis'] == 'raw_unadjusted'
    assert captured['data_version'] == '957cf040534df31716943e4202f159ced9b655ee7442c6ff81c5186e9695f4c2'
    assert captured['source_sha256'] == '9f5f3a01c0748623a8f73a02a54f177cc0165961aa505cabc8f98f79d0f5384d'
    assert captured['source_identity_sha256'] == '6f1fe1bdec0dc4d4babf7a6ea395abaa27e4bbd378620763a6947f95a471dcc5'
    rows = captured['rows']
    assert isinstance(rows, list) and len(rows) == captured['row_count'] == 5507
    row_hash = hashlib.sha256(json.dumps(rows, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    assert row_hash == captured['rows_sha256'] == '6fb14c7c45f5e623bbc28b03ffe0c4782faebc0cfc86eeab9ae9b67debdce956'
    bars = []
    for row in rows:
        assert isinstance(row, list) and len(row) == 7 and isinstance(row[0], str)
        assert all(type(value) in (int, float) for value in row[1:])
        values = cast(list[float], row[1:])
        bars.append(Bar(datetime.fromisoformat(row[0]), 'sz.000678', values[0], values[1], values[2],
                        values[3], values[4], adjustment_factor=values[5]))
    assert _day(bars[0]) == captured['first'] == '1997-01-06'
    assert _day(bars[-1]) == captured['last'] == '2021-04-30'
    assert all(_day(left) < _day(right) for left, right in zip(bars, bars[1:]))
    return tuple(bars)


@pytest.fixture(scope='module')
def full_geometry(real_bars: tuple[Bar, ...]) -> Geometry:
    return _geometry(real_bars)


@pytest.fixture(scope='module')
def clock_snapshots(real_bars: tuple[Bar, ...]) -> Mapping[str, Facts]:
    days = [_day(bar) for bar in real_bars]
    moments = {moment for *_, known, completed in CLOCKS for moment in (known, completed)}
    checkpoints = moments | {days[days.index(moment)-1] for moment in moments}
    result = {}
    for asof in sorted(checkpoints):
        geometry = _geometry(_prefix(real_bars, asof))
        # 只保存轻量时钟事实，避免在门禁中持有全部每日嵌套证明。
        result[asof] = tuple(tuple({field: point[field] for field in FIELDS if field in point}
                                  for point in _points(level)) for level in geometry)
    return result


@pytest.mark.parametrize('day,values', [
    ('2018-04-27', (6.38, 7.06, 6.31, 6.79, 16951419)),
    ('2018-05-31', (5.61, 5.99, 5.54, 5.75, 3137600)),
    ('2020-11-02', (4.76, 4.83, 4.67, 4.74, 2698200)),
    ('2021-02-22', (4.68, 4.84, 4.65, 4.74, 4836514)),
])
def test_original_complete_prefix_retains_each_reported_ohlcv(
    real_bars: tuple[Bar, ...], day: str, values: tuple[float, float, float, float, float],
) -> None:
    bar = next(bar for bar in real_bars if _day(bar) == day)
    assert (bar.open, bar.high, bar.low, bar.close, bar.volume) == values
    assert bar.adjustment_factor == 1


@pytest.mark.parametrize('first,last', [('2018-04-27', '2018-05-31'), ('2020-11-02', '2021-02-22')])
def test_reported_intervals_share_one_complete_original_polyline_path(
    real_bars: tuple[Bar, ...], first: str, last: str,
) -> None:
    drawing = _draw(real_bars)
    assert drawing['issues'] == []
    paths = _records(drawing['strokes'])
    assert [path['id'] for path in paths] == ['lecture-0']
    points = _records(paths[0]['points'])
    assert any(point['time'] == first for point in points)
    assert any(point['time'] == last for point in points)
    assert {point['source_path'] for point in polyline_high_low_points(drawing)} == {'lecture-0'}


@pytest.mark.parametrize('day', DOJIS)
def test_doji_and_adjacent_daily_steps_equal_independent_original_prefixes(
    real_bars: tuple[Bar, ...], day: str,
) -> None:
    index = next(index for index, bar in enumerate(real_bars) if _day(bar) == day)
    targets = {index-1, index, index+1}
    steps: dict[int, tuple[int, list[Point]]] = {}

    def collect(position: int, epoch: int, points: Sequence[Point]) -> None:
        if position in targets:
            steps[position] = epoch, deepcopy(list(points))

    draw = cast(Callable[..., dict[str, object]], lecture_drawing)
    draw(real_bars, on_step=collect)

    for position in sorted(targets):
        prefix = _draw(real_bars[:position+1])
        assert prefix['issues'] == []
        paths = _records(prefix['strokes'])
        assert len(paths) == 1 and paths[0]['id'] == 'lecture-0'
        epoch, points = steps[position]
        assert epoch == 0
        assert points == _records(paths[0]['points'])
        assert all(cast(str, point['available_at']) <= _day(real_bars[position]) for point in points)
    previous, current = steps[index-1][1], steps[index][1]
    assert current[:-1] == previous[:-1]
    assert len(current) == len(previous)
    assert current[-1]['state'] == 'developing'
    assert current[-1]['kind'] == previous[-1]['kind'] == 'L'
    assert cast(float, current[-1]['value']) <= cast(float, previous[-1]['value'])
    if current[-1]['value'] == previous[-1]['value']:
        assert current[-1] == previous[-1]
    else:
        assert current[-1]['index'] == index and current[-1]['intrabar_order_resolved'] is False


def test_full_original_prefix_recurses_complete_formal_points_at_each_level(full_geometry: Geometry) -> None:
    assert [len(_points(level)) for level in full_geometry] == [748, 115, 10]
    assert [level['source_level'] for level in full_geometry] == [0, 1, 2]
    for level, prepared in enumerate(full_geometry, start=1):
        assert prepared['qualified_strokes'] == []
        assert all(point['wave_identity_state'] == 'owned' and point['trend_level'] == level
                   for point in _points(prepared))
        if level < 3:
            direct = high_low_points_from_strokes(prepared['strokes'])
            next_source = [point for path in _records(full_geometry[level]['source_strokes'])
                           for point in _records(path['points'])]
            assert next_source == direct
    assert [len(high_low_points_from_strokes(level['strokes'])) for level in full_geometry] == [747, 115, 10]


@pytest.mark.parametrize('level,day,kind,value,known,completed', CLOCKS)
def test_target_point_is_not_known_or_completed_before_its_own_clock(
    real_bars: tuple[Bar, ...], clock_snapshots: Mapping[str, Facts],
    level: int, day: str, kind: str, value: float, known: str, completed: str,
) -> None:
    days = [_day(bar) for bar in real_bars]
    before_known = days[days.index(known)-1]
    assert _matching(clock_snapshots[before_known][level-1], day, kind, value) == []
    first = _matching(clock_snapshots[known][level-1], day, kind, value)
    assert len(first) == 1 and first[0]['available_at'] == known
    before_completed = days[days.index(completed)-1]
    prior = _matching(clock_snapshots[before_completed][level-1], day, kind, value)
    assert not prior or 'wave_completed_at' not in prior[0]
    closed = _matching(clock_snapshots[completed][level-1], day, kind, value)
    assert len(closed) == 1
    assert closed[0]['available_at'] == known and closed[0]['wave_completed_at'] == completed


@pytest.mark.parametrize('day', DOJIS)
def test_full_source_asof_and_independent_prefix_judge_match_before_and_on_each_doji(
    real_bars: tuple[Bar, ...], full_geometry: Geometry, day: str,
) -> None:
    days = [_day(bar) for bar in real_bars]
    for asof in (days[days.index(day)-1], day):
        prefix = _geometry(_prefix(real_bars, asof))
        for level in (1, 2, 3):
            own_source = [point for path in _records(prefix[level-1]['source_strokes'])
                          for point in _records(path['points'])]
            full_source = [point for path in _records(full_geometry[level-1]['source_strokes'])
                           for point in _records(path['points'])]
            assert judge_trend(own_source, trend_level=level) == judge_trend(
                full_source, trend_level=level, asof=asof)


def test_short_2018_initialization_does_not_replace_the_original_higher_level_background(
    real_bars: tuple[Bar, ...], full_geometry: Geometry,
) -> None:
    short = _geometry(tuple(bar for bar in real_bars if '2018-01-15' <= _day(bar) <= '2018-06-15'))
    full_second = _matching(_points(full_geometry[1]), '2018-04-27', 'H', 7.06)
    assert len(full_second) == 1
    assert full_second[0]['available_at'] == full_second[0]['wave_completed_at'] == '2018-06-04'
    assert _matching(_points(short[1]), '2018-04-27', 'H', 7.06) == []
