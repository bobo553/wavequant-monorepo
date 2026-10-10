"""真实上市首日前缀中的反转、严格升级与整段展示使用相同高低点规则。"""

from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import cast

import pytest

from wavequant.domain.market_structure.lecture_drawing import lecture_drawing
from wavequant.domain.market_structure.lecture_trend import reversal_trends
from wavequant.domain.market_structure.trend_judgement import judge_trend
from wavequant.domain.market_structure.trend_lines import (
    high_low_points_from_strokes, polyline_high_low_points, prepare_trend_lines,
)
from wavequant.domain.models.model import Bar


Point = Mapping[str, object]
FIXTURE = Path(__file__).parent / 'fixtures/xiangyang_1997_trend_segments.json'


@pytest.fixture(scope='module')
def xiangyang_1997() -> tuple[Bar, ...]:
    raw = json.loads(FIXTURE.read_text(encoding='utf-8'))
    assert raw['symbol'] == 'sz.000678'
    assert raw['source'] == 'tdx_local_day' and raw['price_basis'] == 'raw_unadjusted'
    assert raw['volume_unit'] == 'shares'
    bars = tuple(Bar(datetime.fromisoformat(day), raw['symbol'], *prices) for day, *prices in raw['rows'])
    assert len(bars) == 196
    assert (bars[0].timestamp.date().isoformat(), bars[-1].timestamp.date().isoformat()) == (
        '1997-01-06', '1997-10-31',
    )
    return bars


@pytest.fixture(scope='module')
def akshare_1997(xiangyang_1997: tuple[Bar, ...]) -> tuple[Bar, ...]:
    captured = json.loads(FIXTURE.read_text(encoding='utf-8'))['akshare_snapshot']
    assert (captured['source'], captured['symbol'], captured['asof'], captured['timeframe']) == (
        'akshare', 'sz.000678', '1997-10-31', '1d',
    )
    assert captured['price_basis'] == 'raw_unadjusted' and captured['row_count'] == 196
    # 真实服务器快照仅有59根收盘分差，以差分附录复原，避免重复另一份上市首日数据。
    closes = dict(captured['close_overrides'])
    assert len(closes) == 59
    bars = tuple(replace(bar, close=closes.get(bar.timestamp.date().isoformat(), bar.close))
                 for bar in xiangyang_1997)
    normalized = [[bar.timestamp.date().isoformat(), bar.open, bar.high, bar.low, bar.close, float(bar.volume)]
                  for bar in bars]
    assert hashlib.sha256(json.dumps(normalized, separators=(',', ':')).encode()).hexdigest() == captured['rows_sha256']
    return bars


def _prefix(bars: Sequence[Bar], asof: str) -> list[Bar]:
    return [bar for bar in bars if bar.timestamp.date().isoformat() <= asof]


def _source(bars: Sequence[Bar], asof: str) -> list[dict[str, object]]:
    return polyline_high_low_points(lecture_drawing(_prefix(bars, asof)))


def _records(value: object) -> list[Point]:
    assert isinstance(value, (list, tuple))
    assert all(isinstance(item, Mapping) for item in value)
    return cast(list[Point], list(value))


def _points(bundle: Point, field: str = 'strokes') -> list[Point]:
    return [point for stroke in _records(bundle[field]) for point in _records(stroke['points'])]


def _coordinate(point: Point) -> tuple[object, ...]:
    return point['time'], point['kind'], point['value']


def _segment(bundle: Point, origin: tuple[object, ...], endpoint: tuple[object, ...]) -> list[Point]:
    # 连续覆盖保留六定义判定的中间反转；不能把不同路径拼接或补造跳连直线。
    for field in ('strokes', 'developing_strokes'):
        for stroke in _records(bundle[field]):
            points = _records(stroke['points'])
            for first, point in enumerate(points):
                if _coordinate(point) != origin:
                    continue
                for last in range(first + 1, len(points)):
                    if _coordinate(points[last]) != endpoint:
                        continue
                    segment = points[first:last + 1]
                    for left, right in zip(segment, segment[1:]):
                        assert left['kind'] != right['kind']
                        difference = cast(float, right['value']) - cast(float, left['value'])
                        assert difference > 0 if right['kind'] == 'H' else difference < 0
                    return segment
    pytest.fail(f'missing structural or developing segment: {origin} -> {endpoint}')


def _ordinary_up(points: Sequence[Point], day: str, known: str) -> Point:
    for point in points:
        if (point['time'] == day and point['kind'] == 'L' and point['available_at'] == known
                and point.get('wave_direction_before') == 'down' and point.get('wave_direction_after') == 'up'
                and isinstance(point.get('definition_evidence'), (list, tuple))):
            return point
    pytest.fail(f'missing ordinary HH/HL upward reversal at {day}, known {known}')


def test_real_1997_prices_match_both_reported_trend_segments(xiangyang_1997: tuple[Bar, ...]) -> None:
    by_day = {bar.timestamp.date().isoformat(): bar for bar in xiangyang_1997}
    assert by_day['1997-03-10'].low == 15.90
    assert by_day['1997-04-28'].high == 24.27
    assert by_day['1997-08-19'].low == 10.45
    assert by_day['1997-08-25'].high == 12.20
    assert by_day['1997-09-04'].high == 12.30
    # 本样本只证明几何；不同行情源的收盘分差不能成为趋势线的交易许可。
    assert by_day['1997-04-28'].close == 22.55 and by_day['1997-08-25'].close == 11.88


def test_march_origin_keeps_later_ordinary_reversal_separate_from_its_earlier_downward_endpoint(
    xiangyang_1997: tuple[Bar, ...],
) -> None:
    source = _source(xiangyang_1997, '1997-03-17')
    before = judge_trend(source, asof='1997-03-14')
    known = judge_trend(source, asof='1997-03-17')
    at_origin = [point for point in known.candidates if _coordinate(point) == ('1997-03-10', 'L', 15.90)]
    assert any(point['available_at'] == '1997-03-11'
               and point.get('wave_direction_after') == 'down' for point in at_origin)
    ordinary = next(point for point in at_origin
                    if point.get('confirmation_rule') == 'ordered_HH_then_HL_or_LL_then_LH_switch')
    assert ordinary['available_at'] == '1997-03-17'
    assert (ordinary['wave_direction_before'], ordinary['wave_direction_after']) == ('down', 'up')
    proof = _records(ordinary['definition_evidence'])
    assert [(point['time'], point['kind'], point['value'], point['available_at']) for point in proof] == [
        ('1997-03-10', 'H', 16.35, '1997-03-11'), ('1997-03-13', 'H', 17.45, '1997-03-14'),
        ('1997-03-11', 'L', 15.90, '1997-03-11'), ('1997-03-14', 'L', 16.38, '1997-03-17'),
    ]
    assert not any(point['time'] == '1997-03-10' and point.get('wave_direction_after') == 'up'
                   and point.get('confirmation_rule') == ordinary['confirmation_rule'] for point in before.candidates)


def test_april_snapshot_displays_the_whole_march_rise_without_erasing_formal_subsegments(
    xiangyang_1997: tuple[Bar, ...],
) -> None:
    prefix = _prefix(xiangyang_1997, '1997-04-29')
    first = reversal_trends(lecture_drawing(prefix), prefix)
    segment = _segment(first, ('1997-03-10', 'L', 15.90), ('1997-04-28', 'H', 24.27))
    assert segment[-1]['available_at'] == '1997-04-29'
    assert [(point['time'], point['kind'], point['value'], point['available_at']) for point in segment
            if point['time'] in ('1997-04-04', '1997-04-11')] == [
        ('1997-04-04', 'H', 19.20, '1997-04-07'), ('1997-04-11', 'L', 17.48, '1997-04-14'),
    ]
    # 4/28高点尚为折线发展端，4/29来源确认前不能加入正式一级点。
    before = judge_trend(_source(xiangyang_1997, '1997-04-29'), asof='1997-04-28')
    assert not any(point['time'] == '1997-04-28' for point in before.points)


@pytest.mark.parametrize('level', (1, 2, 3))
def test_august_higher_high_and_higher_low_reverse_every_level_without_an_extra_holding_point(
    xiangyang_1997: tuple[Bar, ...], level: int,
) -> None:
    # 相同高低点作为紧邻下级输入，级别只改变来源元数据，不能改变价格公式。
    source = [dict(point, trend_level=level - 1) for point in _source(xiangyang_1997, '1997-08-28')]
    before = judge_trend(source, trend_level=level, asof='1997-08-25')
    known = judge_trend(source, trend_level=level, asof='1997-08-26')
    assert not any(point['time'] == '1997-08-25' for point in before.candidates)
    origin = _ordinary_up(known.candidates, '1997-08-19', '1997-08-26')
    assert _coordinate(origin) == ('1997-08-19', 'L', 10.45)
    assert (origin['trend_level'], origin['source_level']) == (level, level - 1)
    key = origin['broken_key']
    assert isinstance(key, Mapping)
    assert (_coordinate(key), key['available_at']) == (('1997-08-15', 'H', 11.70), '1997-08-18')
    proof = _records(origin['definition_evidence'])
    assert [(point['time'], point['kind'], point['value'], point['available_at']) for point in proof] == [
        ('1997-08-19', 'H', 11.05, '1997-08-20'), ('1997-08-25', 'H', 12.20, '1997-08-26'),
        ('1997-08-19', 'L', 10.45, '1997-08-20'), ('1997-08-20', 'L', 10.50, '1997-08-20'),
    ]
    assert (origin['wave_direction_before'], origin['wave_direction_after']) == ('down', 'up')
    assert not any(point['time'] == '1997-08-27' for point in known.candidates)
    visible = [point for point in source if cast(str, point['available_at']) <= '1997-08-26']
    assert judge_trend(visible, trend_level=level) == known
    prepared = prepare_trend_lines(visible, trend_level=level, symbol='sz.000678')
    segment = _segment(prepared, ('1997-08-19', 'L', 10.45), ('1997-08-25', 'H', 12.20))
    assert segment[-1]['available_at'] == '1997-08-26'


def test_september_higher_high_extends_the_real_august_origin_with_its_own_source_knowledge(
    xiangyang_1997: tuple[Bar, ...],
) -> None:
    prefix = _prefix(xiangyang_1997, '1997-09-05')
    first = reversal_trends(lecture_drawing(prefix), prefix)
    segment = _segment(first, ('1997-08-19', 'L', 10.45), ('1997-09-04', 'H', 12.30))
    assert segment[-1]['available_at'] == '1997-09-05'
    assert segment[0]['wave_direction_after'] == 'up'
    candidate = _ordinary_up(_points(first, 'candidate_strokes'), '1997-08-19', '1997-08-26')
    assert candidate['available_at'] == '1997-08-26'
    source = _source(xiangyang_1997, '1997-09-05')
    before = judge_trend(source, asof='1997-09-04')
    assert not any(point['time'] == '1997-09-04' for point in before.points)


def test_real_short_history_recurses_through_all_formal_points_with_identical_price_and_date_rules(
    xiangyang_1997: tuple[Bar, ...],
) -> None:
    source = _source(xiangyang_1997, '1997-10-31')
    for level in (1, 2, 3):
        naked = judge_trend(source, trend_level=level)
        prepared = prepare_trend_lines(source, trend_level=level, symbol='sz.000678')
        assert _points(prepared, 'source_strokes') == source
        next_source = high_low_points_from_strokes(prepared['strokes'])
        assert [(_coordinate(point), point['available_at'], point['trend_level'], point['source_level'])
                for point in next_source] == [
            (_coordinate(point), point['available_at'], level, level - 1) for point in naked.points
        ]
        assert next_source and all(cast(str, point['available_at']) <= '1997-10-31' for point in next_source)
        source = next_source


def test_captured_akshare_and_tdx_prefixes_keep_the_same_target_geometry_despite_real_close_differences(
    xiangyang_1997: tuple[Bar, ...], akshare_1997: tuple[Bar, ...],
) -> None:
    assert all((left.open, left.high, left.low, left.volume) == (right.open, right.high, right.low, right.volume)
               for left, right in zip(xiangyang_1997, akshare_1997))
    assert sum(left.close != right.close for left, right in zip(xiangyang_1997, akshare_1997)) == 59
    for asof in ('1997-03-17', '1997-04-29', '1997-08-26', '1997-09-05', '1997-10-31'):
        sources = [_source(bars, asof) for bars in (xiangyang_1997, akshare_1997)]
        for level in (1, 2, 3):
            snapshots = [prepare_trend_lines(source, trend_level=level, symbol='sz.000678') for source in sources]
            geometry: list[list[tuple[object, ...]]] = []
            for snapshot in snapshots:
                points = _points(snapshot) + _points(snapshot, 'developing_strokes')
                geometry.append([(_coordinate(point), point['available_at']) for point in points
                                 if ('1997-03-10' <= cast(str, point['time']) <= '1997-04-28'
                                     or '1997-08-19' <= cast(str, point['time']) <= '1997-09-04')])
            assert geometry[0] == geometry[1], (asof, level)
            sources = [high_low_points_from_strokes(snapshot['strokes']) for snapshot in snapshots]
