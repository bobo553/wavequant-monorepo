"""等值十字星保留因果来源，只有同路径正式尾点能承接后续母线。"""

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import cast

import pytest

from wavequant.domain.market_structure.lecture_drawing import (
    _prior_doji_touched_endpoint, lecture_drawing,
)
from wavequant.domain.market_structure.price_action import Direction
from wavequant.domain.market_structure.trend_lines import (
    high_low_points_from_strokes, polyline_high_low_points, prepare_trend_lines,
)
from wavequant.domain.market_structure.trend_pipeline import calculate_trend_hierarchy
from wavequant.domain.models.model import Bar


Point = Mapping[str, object]
Row = tuple[float, float, float, float]
FIXTURES = Path(__file__).parent / 'fixtures'
BASE: tuple[Row, ...] = ((12, 14, 10, 13), (14, 16, 12, 15),
                         (13, 14, 11, 12), (13.5, 14, 11, 12))


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
    bars: list[Bar] = []
    for index, (opened, high, low, closed) in enumerate(rows):
        if rising:
            opened, high, low, closed = 30-opened, 30-low, 30-high, 30-closed
        bars.append(Bar(datetime(2026, 1, index+1), 'TEST', opened, high, low, closed, 1000))
    return bars


def _points(drawing: Point) -> list[Point]:
    strokes = _records(drawing['strokes'])
    assert len(strokes) == 1
    return _records(strokes[0]['points'])


def _owned(bundle: Point) -> list[Point]:
    return [point for stroke in _records(bundle['strokes']) for point in _records(stroke['points'])]


def _coordinate(point: Point) -> tuple[object, object, object]:
    return point['time'], point['kind'], point['value']


def _prefix(bars: Sequence[Bar], asof: str) -> list[Bar]:
    return [bar for bar in bars if bar.timestamp.date().isoformat() <= asof]


@pytest.mark.parametrize('rising', [False, True])
@pytest.mark.parametrize('repeats', [1, 3])
@pytest.mark.parametrize('bullish_mother', [False, True])
@pytest.mark.parametrize('equal_mother_extreme', [False, True])
def test_repeated_equal_doji_preserves_old_point_and_connects_only_the_coloured_mother(
    rising: bool, repeats: int, bullish_mother: bool, equal_mother_extreme: bool,
) -> None:
    equal_rows: list[Row] = [(12, 15+0.1*index, 11, 12) for index in range(repeats)]
    mother_extreme = 11.0 if equal_mother_extreme else 10.5
    mother: Row = ((12, 15.5, mother_extreme, 15) if bullish_mother
                  else (15, 15.5, mother_extreme, 12))
    bars = _bars([*BASE, *equal_rows, mother], rising=rising)
    before = _draw(bars[:len(BASE)])
    previous = _points(before)
    for length in range(len(BASE)+1, len(bars)):
        unchanged = _draw(bars[:length])
        assert unchanged['issues'] == []
        assert _points(unchanged) == previous
        assert prepare_trend_lines(polyline_high_low_points(unchanged), trend_level=1, symbol='TEST') == (
            prepare_trend_lines(polyline_high_low_points(before), trend_level=1, symbol='TEST'))
    result = _draw(bars)
    assert result['issues'] == []
    points = _points(result)
    assert points[:len(previous)-1] == previous[:-1]
    assert all(point['index'] not in range(len(BASE), len(BASE)+repeats) for point in points)
    mother_index = len(bars)-1
    mother_day = bars[-1].timestamp.date().isoformat()
    mother_points = [point for point in points if point['index'] == mother_index]
    assert len(mother_points) == (1 if equal_mother_extreme and bullish_mother else 2)
    assert all(point['available_at'] == mother_day and point['intrabar_order_resolved'] is False
               for point in mother_points)
    assert mother_points[-1]['state'] == 'developing'
    if equal_mother_extreme or not bullish_mother:
        # 此次真正反向才确认旧尾；价格日保留，可知日必须等到有色母线。
        old_endpoint = [point for point in points if point['index'] == previous[-1]['index']]
        assert len(old_endpoint) == 1
        assert old_endpoint[0] == dict(previous[-1], state='confirmed', available_at=mother_day)


@pytest.mark.parametrize('rising', [False, True])
@pytest.mark.parametrize('invalid', [
    'other_epoch', 'different_extreme', 'seed_anchor', 'closed_endpoint',
    'opposite_crossing', 'wrong_direction', 'future_endpoint',
])
def test_an_old_endpoint_cannot_borrow_an_unrelated_or_unconfirmed_child(
    rising: bool, invalid: str,
) -> None:
    data = _bars([*BASE, (12, 15, 11, 12)], rising=rising)
    points = _points(_draw(data[:4]))
    direction = Direction.UP if rising else Direction.DOWN
    assert _prior_doji_touched_endpoint(points, data[4], direction, 0, 4)
    epoch = 0
    child = data[4]
    if invalid == 'other_epoch':
        epoch = 3
    elif invalid == 'different_extreme':
        child = _bars([*BASE, (12, 15, 10.9, 12)], rising=rising)[4]
    elif invalid == 'seed_anchor':
        points[-2] = dict(points[-2], state='seed')
    elif invalid == 'closed_endpoint':
        points[-1] = dict(points[-1], state='confirmed')
    elif invalid == 'opposite_crossing':
        child = _bars([*BASE, (12, 16.1, 11, 12)], rising=rising)[4]
    elif invalid == 'wrong_direction':
        direction = Direction.DOWN if rising else Direction.UP
    else:
        assert invalid == 'future_endpoint'
        points[-1] = dict(points[-1], index=5)
    assert not _prior_doji_touched_endpoint(points, child, direction, epoch, 4)


@pytest.mark.parametrize('rising', [False, True])
def test_equal_doji_still_breaks_without_direction_or_when_its_opposite_crosses(rising: bool) -> None:
    unknown = _bars([(12, 14, 10, 12), (12, 15, 9, 12)], rising=rising)
    assert [issue['index'] for issue in _records(_draw(unknown)['issues'])] == [1]
    crossing = _bars([*BASE, (12, 16.1, 11, 12)], rising=rising)
    drawing = _draw(crossing)
    assert [issue['index'] for issue in _records(drawing['issues'])] == [4]
    assert _points(drawing) == _points(_draw(crossing[:4]))


@pytest.fixture(scope='module', params=['akshare', 'tdx'])
def xiangyang_2010(request: pytest.FixtureRequest) -> tuple[str, tuple[Bar, ...]]:
    provider = request.param
    assert isinstance(provider, str) and provider in ('akshare', 'tdx')
    captured = _mapping(json.loads((FIXTURES / f'xiangyang_2010_equal_doji_{provider}.json').read_text(encoding='utf-8')))
    assert captured['symbol'] == 'sz.000678' and captured['price_basis'] == 'raw_unadjusted'
    rows = captured['rows']
    assert isinstance(rows, list) and len(rows) == captured['row_count'] == 2995
    rows_hash = hashlib.sha256(json.dumps(rows, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    assert rows_hash == captured['rows_sha256'] == captured['data_version']
    bars: list[Bar] = []
    for row in rows:
        assert isinstance(row, list) and len(row) == 6 and isinstance(row[0], str)
        values = row[1:]
        assert all(type(value) in (int, float) for value in values)
        prices = cast(list[float], values)
        bars.append(Bar(datetime.fromisoformat(row[0]), 'sz.000678',
                        prices[0], prices[1], prices[2], prices[3], prices[4]))
    assert bars[0].timestamp.date().isoformat() == captured['first'] == '1997-01-06'
    assert bars[-1].timestamp.date().isoformat() == captured['last'] == '2010-04-30'
    return provider, tuple(bars)


def test_real_equal_high_day_preserves_raw_points_and_the_entire_owned_clock(
    xiangyang_2010: tuple[str, tuple[Bar, ...]],
) -> None:
    provider, bars = xiangyang_2010
    by_day = {bar.timestamp.date().isoformat(): bar for bar in bars}
    first, last, doji = by_day['2010-03-03'], by_day['2010-03-25'], by_day['2010-03-09']
    assert (first.open, first.high, first.low, first.close, first.volume) == (6.99, 7.15, 6.91, 7.12, 9718865)
    assert (last.open, last.high, last.low, last.close) == (7.49, 7.98, 7.29, 7.69)
    assert last.volume == (29932556 if provider == 'akshare' else 29932555)
    assert (doji.open, doji.high, doji.low, doji.close) == (6.88, 6.94, 6.79, 6.88)
    assert doji.high == by_day['2010-03-08'].high
    before, after = (_draw(_prefix(bars, asof)) for asof in ('2010-03-08', '2010-03-09'))
    assert before['issues'] == after['issues'] == []
    assert _points(after) == _points(before)
    assert prepare_trend_lines(polyline_high_low_points(after), trend_level=1, symbol='sz.000678') == (
        prepare_trend_lines(polyline_high_low_points(before), trend_level=1, symbol='sz.000678'))


@pytest.mark.parametrize('asof', [
    '2010-03-08', '2010-03-09', '2010-03-11', '2010-03-18',
    '2010-03-25', '2010-03-26', '2010-03-29', '2010-04-01',
])
def test_real_prefixes_do_not_confirm_or_complete_the_reported_high_early(
    xiangyang_2010: tuple[str, tuple[Bar, ...]], asof: str,
) -> None:
    _, bars = xiangyang_2010
    drawing = _draw(_prefix(bars, asof))
    assert drawing['issues'] == []
    raw = _points(drawing)
    assert all(cast(str, point['time']) <= asof and cast(str, point['available_at']) <= asof for point in raw)
    source = polyline_high_low_points(drawing)
    assert {point['source_path'] for point in source} == {'lecture-0'}
    bundle = prepare_trend_lines(source, trend_level=1, symbol='sz.000678')
    assert bundle['_path_market_end_indices'] == {'lecture-0': None}
    points = _owned(bundle)
    assert all(cast(str, point['available_at']) <= asof
               and cast(str, point.get('wave_completed_at', asof)) <= asof for point in points)
    high = [point for point in points if _coordinate(point) == ('2010-03-25', 'H', 7.98)]
    low = [point for point in points if _coordinate(point) == ('2010-03-15', 'L', 6.61)]
    assert bool(high) == bool(low) == (asof >= '2010-03-26')
    if high:
        assert high[0]['available_at'] == '2010-03-26'
        assert high[0].get('wave_completed_at') == ('2010-03-30' if asof >= '2010-03-30' else None)
        assert low[0]['available_at'] == low[0]['wave_completed_at'] == '2010-03-26'
    recursive_high = [point for point in high_low_points_from_strokes(bundle['strokes'])
                      if _coordinate(point) == ('2010-03-25', 'H', 7.98)]
    assert bool(recursive_high) == (asof >= '2010-03-30')
    if recursive_high:
        assert recursive_high[0]['available_at'] == '2010-03-30'
        assert recursive_high[0]['source_geometry_available_at'] == '2010-03-26'


def test_real_full_pipeline_keeps_the_causal_intermediate_turns_in_one_source(
    xiangyang_2010: tuple[str, tuple[Bar, ...]],
) -> None:
    _, bars = xiangyang_2010
    hierarchy = calculate_trend_hierarchy(bars)
    drawing = hierarchy['lecture_drawing']
    assert drawing['issues'] == []
    expected = [('2010-03-11', 'H', 7.35, '2010-03-12', '2010-03-18'),
                ('2010-03-15', 'L', 6.61, '2010-03-26', '2010-03-26'),
                ('2010-03-25', 'H', 7.98, '2010-03-26', '2010-03-30'),
                ('2010-03-29', 'L', 7.40, '2010-04-02', '2010-04-02')]
    points = [point for point in _owned(hierarchy['reversal_trends'])
              if '2010-03-01' <= cast(str, point['time']) <= '2010-03-31']
    assert [(*_coordinate(point), point['available_at'], point['wave_completed_at']) for point in points] == expected
    assert all(point['source_path'] == 'lecture-0' for point in points)
    # 逐日 confirmed 历史不可被后续等值触及或新母线移走/倒填可知日。
    confirmed: dict[tuple[object, object, object], Point] = {}
    for bar in bars:
        asof = bar.timestamp.date().isoformat()
        if not '2010-03-01' <= asof <= '2010-04-30':
            continue
        current = {_coordinate(point): point for point in _points(_draw(_prefix(bars, asof)))
                   if point['state'] == 'confirmed'}
        for coordinate, previous in confirmed.items():
            assert coordinate in current
            assert current[coordinate]['available_at'] == previous['available_at']
        confirmed = current
