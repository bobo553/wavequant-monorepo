"""真实上市起点的完整前缀保护一级普通反转及有边界十字星连续性。

原始 OHLCV 来自本地通达信日线，不以报告首尾两根 K 线构造趋势。保留
上市以来的状态，使 1999 年的旧关键位和整波极值确实由之前历史推导。
"""

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import cast

import pytest

from wavequant.domain.market_structure.lecture_drawing import lecture_drawing
from wavequant.domain.market_structure.trend_judgement import judge_trend
from wavequant.domain.market_structure.trend_levels import completed_source_points, trend_reversals
from wavequant.domain.market_structure.trend_lines import (
    high_low_points_from_strokes, polyline_high_low_points, prepare_trend_lines,
)
from wavequant.domain.models.model import Bar


Point = Mapping[str, object]
FIXTURE = Path(__file__).parent / 'fixtures/xiangyang_1999_trend_segments.json'


def _mapping(value: object) -> Point:
    assert isinstance(value, Mapping) and all(isinstance(key, str) for key in value)
    return cast(Point, value)


def _records(value: object) -> list[Point]:
    assert isinstance(value, (list, tuple))
    return [_mapping(item) for item in value]


def _coordinate(point: Point) -> tuple[object, object, object]:
    return point['time'], point['kind'], point['value']


def _price(point: Point) -> float:
    value = point['value']
    assert type(value) in (int, float)
    return float(cast(float, value))


def _known(point: Point) -> str:
    value = point['available_at']
    assert isinstance(value, str)
    return value


@pytest.fixture(scope='module')
def xiangyang_1999() -> tuple[Bar, ...]:
    captured = _mapping(json.loads(FIXTURE.read_text(encoding='utf-8')))
    assert (captured['source'], captured['symbol'], captured['price_basis'], captured['volume_unit']) == (
        'tdx_local_day', 'sz.000678', 'raw_unadjusted', 'shares',
    )
    rows = captured['rows']
    assert isinstance(rows, list) and len(rows) == captured['row_count'] == 598
    assert hashlib.sha256(json.dumps(rows, separators=(',', ':')).encode()).hexdigest() == captured['rows_sha256']
    bars: list[Bar] = []
    for row in rows:
        assert isinstance(row, list) and len(row) == 6 and isinstance(row[0], str)
        prices = row[1:]
        assert all(type(value) in (int, float) for value in prices)
        values = cast(list[float], prices)
        bars.append(Bar(datetime.fromisoformat(row[0]), 'sz.000678',
                        values[0], values[1], values[2], values[3], values[4]))
    assert (bars[0].timestamp.date().isoformat(), bars[-1].timestamp.date().isoformat()) == (
        '1997-01-06', '1999-06-30',
    )
    return tuple(bars)


def _prefix(bars: Sequence[Bar], asof: str) -> list[Bar]:
    return [bar for bar in bars if bar.timestamp.date().isoformat() <= asof]


def _drawing(bars: Sequence[Bar]) -> dict[str, object]:
    # 既有绘图入口尚无类型注解，此处只收窄实际使用的签名，不替换其算法。
    draw = cast(Callable[[Sequence[Bar]], dict[str, object]], lecture_drawing)
    return draw(bars)


def _source(bars: Sequence[Bar], asof: str, path: str = 'lecture-0') -> list[dict[str, object]]:
    drawing = _drawing(_prefix(bars, asof))
    return [point for point in polyline_high_low_points(drawing) if point['source_path'] == path]


def _geometry(bars: Sequence[Bar], asof: str) -> dict[str, object]:
    return prepare_trend_lines(polyline_high_low_points(_drawing(_prefix(bars, asof))),
                               trend_level=1, symbol='sz.000678')


def _path_points(bundle: Point, path: str, field: str = 'strokes') -> list[Point]:
    return [point for stroke in _records(bundle[field]) if stroke['source_path'] == path
            for point in _records(stroke['points'])]


def _find(points: Sequence[Point], coordinate: tuple[str, str, float]) -> Point:
    matches = [point for point in points if _coordinate(point) == coordinate]
    assert len(matches) == 1, (coordinate, [_coordinate(point) for point in points])
    return matches[0]


def test_real_history_keeps_the_reported_prices_and_actual_breakpoint_bars(xiangyang_1999: tuple[Bar, ...]) -> None:
    assert xiangyang_1999[508].timestamp.date().isoformat() == '1999-02-04'
    assert xiangyang_1999[508].high == 10.37
    assert xiangyang_1999[550].timestamp.date().isoformat() == '1999-04-22'
    assert xiangyang_1999[550].low == 8.80
    assert xiangyang_1999[543].timestamp.date().isoformat() == '1999-04-13'
    assert xiangyang_1999[543].open == xiangyang_1999[543].close == 9.50
    assert (xiangyang_1999[544].open, xiangyang_1999[544].close) == (9.51, 9.53)


@pytest.mark.parametrize(('coordinate', 'known', 'proof'), [
    (('1999-02-04', 'H', 10.37), '1999-03-03', (
        ('1999-02-08', 'H', 9.90, '1999-02-09'), ('1999-03-01', 'H', 9.78, '1999-03-02'),
        ('1999-03-01', 'L', 9.50, '1999-03-01'), ('1999-03-03', 'L', 9.38, '1999-03-03'),
    )),
    (('1999-03-15', 'H', 10.34), '1999-03-18', (
        ('1999-03-15', 'H', 10.34, '1999-03-15'), ('1999-03-17', 'H', 10.13, '1999-03-18'),
        ('1999-03-12', 'L', 10.00, '1999-03-15'), ('1999-03-16', 'L', 9.82, '1999-03-17'),
    )),
    (('1999-04-05', 'H', 10.18), '1999-04-09', (
        ('1999-04-05', 'H', 10.18, '1999-04-05'), ('1999-04-09', 'H', 9.95, '1999-04-09'),
        ('1999-04-02', 'L', 9.96, '1999-04-05'), ('1999-04-08', 'L', 9.78, '1999-04-09'),
    )),
])
def test_real_confirmed_polyline_pairs_close_the_first_level_wave(
    xiangyang_1999: tuple[Bar, ...], coordinate: tuple[str, str, float], known: str,
    proof: tuple[tuple[str, str, float, str], ...],
) -> None:
    source = _source(xiangyang_1999, known)
    ordinary = [point for point in trend_reversals(source, trend_level=1) if _coordinate(point) == coordinate]
    assert len(ordinary) == 1 and ordinary[0]['available_at'] == known
    evidence = _records(ordinary[0]['definition_evidence'])
    assert tuple((point['time'], point['kind'], point['value'], point['available_at']) for point in evidence) == proof
    assert _price(evidence[1]) < _price(evidence[0]) and _price(evidence[3]) < _price(evidence[2])
    result = judge_trend(source, trend_level=1)
    peak = _find(result.points, coordinate)
    assert peak['wave_identity_state'] == 'owned'
    assert peak['wave_completed_at'] == known
    completion = _mapping(peak['wave_completion_evidence'])
    assert completion['available_at'] == known
    assert _records(completion['definition_evidence']) == _records(ordinary[0]['definition_evidence'])
    assert (completion['wave_direction_before'], completion['wave_direction_after']) == ('up', 'down')
    # 输入本身已有确认的折线峰谷，不能把“没有更低整波极值”附加成 LL/LH 的门槛。
    assert _known(_find(source, coordinate)) <= known


def test_real_first_level_covers_intermediate_reversals_before_the_path_break(xiangyang_1999: tuple[Bar, ...]) -> None:
    bundle = _geometry(xiangyang_1999, '1999-04-23')
    points = _path_points(bundle, 'lecture-0')
    interval = [point for point in points if '1999-02-04' <= cast(str, point['time']) <= '1999-04-05']
    assert [_coordinate(point) for point in interval] == [
        ('1999-02-04', 'H', 10.37), ('1999-02-05', 'L', 9.37), ('1999-03-15', 'H', 10.34),
        ('1999-03-26', 'L', 9.51), ('1999-04-05', 'H', 10.18),
    ]
    assert [point['wave_completed_at'] for point in interval] == [
        '1999-03-03', '1999-03-05', '1999-03-18', '1999-04-01', '1999-04-09',
    ]
    assert bundle['qualified_strokes'] == []


def test_real_early_strict_peak_requires_its_own_later_reversal_before_recursion(xiangyang_1999: tuple[Bar, ...]) -> None:
    coordinate = ('1999-02-04', 'H', 10.37)
    earlier = judge_trend(_source(xiangyang_1999, '1999-03-02'), trend_level=1)
    peak = _find(earlier.points, coordinate)
    assert peak['available_at'] == '1999-02-05'
    assert 'wave_completed_at' not in peak
    assert peak['confirmation_rule'] == 'level0_confirmed_high_breaks_known_level1_last_fall_high'
    assert coordinate not in [_coordinate(point) for point in completed_source_points(earlier.points, source_level=1)]
    later = judge_trend(_source(xiangyang_1999, '1999-03-03'), trend_level=1)
    closed = _find(later.points, coordinate)
    assert closed['available_at'] == '1999-02-05' and closed['wave_completed_at'] == '1999-03-03'
    projected = _find(completed_source_points(later.points, source_level=1), coordinate)
    assert projected['available_at'] == '1999-03-03'
    assert projected['source_geometry_available_at'] == '1999-02-05'


def test_real_bounded_doji_keeps_the_high_and_low_in_one_trend_path(xiangyang_1999: tuple[Bar, ...]) -> None:
    prefix = _prefix(xiangyang_1999, '1999-04-30')
    drawing = _mapping(_drawing(prefix))
    issues = _records(drawing['issues'])
    assert issues == [] and drawing['intrabar_incomplete'] is True
    source = polyline_high_low_points(drawing)
    assert source and {point['source_path'] for point in source} == {'lecture-0'}
    assert all('source_path_end_index' not in point for point in source)
    doji_low = _find(source, ('1999-04-13', 'L', 9.43))
    assert doji_low['available_at'] == '1999-04-14'
    assert doji_low['intrabar_order_resolved'] is False
    assert ('1999-04-13', 'H', 9.74) not in [_coordinate(point) for point in source]
    bundle = prepare_trend_lines(source, trend_level=1, symbol='sz.000678')
    assert bundle['_path_market_end_indices'] == {'lecture-0': None}
    points = _path_points(bundle, 'lecture-0')
    peak = _find(points, ('1999-04-05', 'H', 10.18))
    low = _find(points, ('1999-04-22', 'L', 8.80))
    assert points.index(low) == points.index(peak) + 1
    assert (peak['available_at'], low['available_at'], low['wave_completed_at']) == (
        '1999-04-09', '1999-04-23', '1999-04-30',
    )


@pytest.mark.parametrize('asof', ['1999-04-23', '1999-04-29'])
def test_real_continuous_path_low_is_known_but_cannot_enter_second_level_before_completion(
    xiangyang_1999: tuple[Bar, ...], asof: str,
) -> None:
    coordinate = ('1999-04-22', 'L', 8.80)
    low = _find(_source(xiangyang_1999, asof), coordinate)
    assert low['available_at'] == '1999-04-23'
    bundle = _geometry(xiangyang_1999, asof)
    assert coordinate not in [_coordinate(point) for point in high_low_points_from_strokes(bundle['strokes'])]


def test_real_continuous_path_low_enters_the_direct_source_on_its_actual_reversal_day(xiangyang_1999: tuple[Bar, ...]) -> None:
    coordinate = ('1999-04-22', 'L', 8.80)
    source = _source(xiangyang_1999, '1999-04-30')
    ordinary = [point for point in trend_reversals(source, trend_level=1) if _coordinate(point) == coordinate]
    assert len(ordinary) == 1 and ordinary[0]['available_at'] == '1999-04-30'
    evidence = _records(ordinary[0]['definition_evidence'])
    assert _price(evidence[1]) > _price(evidence[0]) and _price(evidence[3]) > _price(evidence[2])
    low = _find(judge_trend(source, trend_level=1).points, coordinate)
    assert low['wave_completed_at'] == '1999-04-30'
    actual_proof = _records(_mapping(low['wave_completion_evidence'])['definition_evidence'])
    assert actual_proof == _records(ordinary[0]['definition_evidence'])
    bundle = _geometry(xiangyang_1999, '1999-04-30')
    projected = _find(high_low_points_from_strokes(bundle['strokes']), coordinate)
    assert projected['available_at'] == '1999-04-30'
