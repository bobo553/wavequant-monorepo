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
    assert [(point['time'], point['kind'], point['value'], point['available_at']) for point in segment] == [
        ('1997-03-10', 'L', 15.90, '1997-03-11'), ('1997-03-13', 'H', 17.45, '1997-03-19'),
        ('1997-03-19', 'L', 16.18, '1997-03-25'), ('1997-03-27', 'H', 19.00, '1997-03-28'),
        ('1997-04-11', 'L', 17.48, '1997-04-17'), ('1997-04-28', 'H', 24.27, '1997-04-29'),
    ]
    # 4/28高点尚为折线发展端，4/29来源确认前不能加入正式一级点。
    before = judge_trend(_source(xiangyang_1997, '1997-04-29'), asof='1997-04-28')
    assert not any(point['time'] == '1997-04-28' for point in before.points)
    source = _source(xiangyang_1997, '1997-04-29')
    facts = judge_trend(source).candidates
    early = _ordinary_up(facts, '1997-03-19', '1997-03-21')
    assert early['wave_identity_state'] == 'direction_only'
    assert 'wave_completed_at' not in early and 'wave_completion_evidence' not in early
    assert [(point['time'], point['kind'], point['value'], point['available_at'])
            for point in _records(early['definition_evidence'])] == [
        ('1997-03-17', 'H', 16.90, '1997-03-18'), ('1997-03-19', 'H', 17.10, '1997-03-20'),
        ('1997-03-19', 'L', 16.18, '1997-03-19'), ('1997-03-20', 'L', 16.25, '1997-03-21'),
    ]
    assert not any(point['time'] == '1997-03-19' for point in judge_trend(source, asof='1997-03-24').points)
    owned = _ordinary_up(facts, '1997-03-19', '1997-03-25')
    assert (owned['wave_identity_state'], owned['wave_completed_at']) == ('owned', '1997-03-25')
    assert [(point['time'], point['kind'], point['value'], point['available_at'])
            for point in _records(owned['definition_evidence'])] == [
        ('1997-03-19', 'H', 17.10, '1997-03-20'), ('1997-03-24', 'H', 17.59, '1997-03-25'),
        ('1997-03-19', 'L', 16.18, '1997-03-19'), ('1997-03-20', 'L', 16.25, '1997-03-21'),
    ]


def test_april_attack_cannot_reuse_a_stale_key_or_borrow_its_own_day_as_prior_completion(
    xiangyang_1997: tuple[Bar, ...],
) -> None:
    source = _source(xiangyang_1997, '1997-04-29')
    snapshot = judge_trend(source)
    high = next(point for point in snapshot.points if _coordinate(point) == ('1997-03-27', 'H', 19.00))
    assert (high['available_at'], high['wave_completed_at']) == ('1997-03-28', '1997-04-04')
    closure = high['wave_completion_evidence']
    assert isinstance(closure, Mapping)
    assert [(point['time'], point['kind'], point['value'], point['available_at'])
            for point in _records(closure['definition_evidence'])] == [
        ('1997-03-27', 'H', 19.00, '1997-03-28'), ('1997-04-02', 'H', 18.78, '1997-04-02'),
        ('1997-03-28', 'L', 17.97, '1997-03-31'), ('1997-04-03', 'L', 17.81, '1997-04-04'),
    ]
    attack = next(point for point in source if _coordinate(point) == ('1997-04-04', 'H', 19.20))
    assert (attack['time'], attack['available_at']) == ('1997-04-04', '1997-04-07')
    # 同日才闭合的最近关键点不能成为此前压力；也不能退用已过时的3/13高点制造升级。
    assert high['wave_completed_at'] == attack['time']
    assert not any(point['time'] == '1997-04-04' for point in snapshot.candidates)
    assert not any(point['time'] == '1997-04-11' for point in judge_trend(source, asof='1997-04-16').points)
    ordinary = _ordinary_up(snapshot.candidates, '1997-04-11', '1997-04-17')
    assert [(point['time'], point['kind'], point['value'], point['available_at'])
            for point in _records(ordinary['definition_evidence'])] == [
        ('1997-04-14', 'H', 18.14, '1997-04-15'), ('1997-04-16', 'H', 19.20, '1997-04-17'),
        ('1997-04-11', 'L', 17.48, '1997-04-14'), ('1997-04-15', 'L', 17.66, '1997-04-16'),
    ]
    strict = next(point for point in snapshot.candidates if point['time'] == '1997-04-11'
                  and point['available_at'] == '1997-04-17' and isinstance(point.get('upgrade_evidence'), Mapping))
    proof = cast(Point, strict['upgrade_evidence'])
    key, confirmed = cast(Point, proof['broken_key']), cast(Point, proof['confirmed_by'])
    assert (_coordinate(key), key['available_at']) == (('1997-03-27', 'H', 19.00), '1997-04-04')
    assert (_coordinate(confirmed), confirmed['available_at']) == (('1997-04-16', 'H', 19.20), '1997-04-17')
    assert cast(str, key['available_at']) < cast(str, confirmed['time'])
    assert cast(float, confirmed['value']) > cast(float, key['value'])


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


def test_real_short_history_recurses_through_completed_formal_points_with_separate_geometry_and_closure_dates(
    xiangyang_1997: tuple[Bar, ...],
) -> None:
    source = _source(xiangyang_1997, '1997-10-31')
    for level in (1, 2, 3):
        naked = judge_trend(source, trend_level=level)
        prepared = prepare_trend_lines(source, trend_level=level, symbol='sz.000678')
        assert _points(prepared, 'source_strokes') == source
        assert [(_coordinate(point), point['available_at'], point['trend_level'], point['source_level'])
                for point in _points(prepared)] == [
            (_coordinate(point), point['available_at'], level, level - 1) for point in naked.points
        ]
        # 几何首次可知日不倒填为闭合日；上一级只能读取真正完成的直接下级波段。
        completed = [point for point in naked.points if 'wave_completed_at' in point]
        assert all(point['wave_completion_policy'] == 'whole_wave_v116' for point in naked.points)
        next_source = high_low_points_from_strokes(prepared['strokes'])
        assert [(_coordinate(point), point['available_at'], point['source_geometry_available_at'],
                 point['trend_level'], point['source_level'])
                for point in next_source] == [
            (_coordinate(point), point['wave_completed_at'], point['available_at'], level, level - 1)
            for point in completed
        ]
        assert (len(naked.points), len(next_source)) == {1: (23, 22), 2: (3, 2), 3: (0, 0)}[level]
        assert all(cast(str, point['available_at']) <= '1997-10-31' for point in next_source)
        source = next_source


def test_april_unfinished_primary_high_keeps_its_geometry_date_without_entering_level_two_source(
    xiangyang_1997: tuple[Bar, ...],
) -> None:
    source = _source(xiangyang_1997, '1997-04-29')
    first = prepare_trend_lines(source, trend_level=1, symbol='sz.000678')
    high = next(point for point in _points(first) if _coordinate(point) == ('1997-04-28', 'H', 24.27))
    assert high['available_at'] == '1997-04-29'
    assert high['wave_completion_policy'] == 'whole_wave_v116'
    assert 'wave_completed_at' not in high and 'wave_completion_evidence' not in high
    # 正式几何中的严格攻击端点仍可画线；没有反向闭合证据时不能成为二级攻击来源。
    completed = high_low_points_from_strokes(first['strokes'])
    assert not any(_coordinate(point) == _coordinate(high) for point in completed)
    second = prepare_trend_lines(completed, trend_level=2, symbol='sz.000678')
    assert _points(second, 'source_strokes') == completed
    assert not any(_coordinate(point) == _coordinate(high) for point in _points(second, 'candidate_strokes'))


def test_april_high_enters_the_next_level_only_when_its_own_ordinary_reverse_proof_is_complete(
    xiangyang_1997: tuple[Bar, ...],
) -> None:
    coordinate = ('1997-04-28', 'H', 24.27)
    for asof in ('1997-04-29', '1997-05-14', '1997-05-15'):
        first = prepare_trend_lines(_source(xiangyang_1997, asof), trend_level=1, symbol='sz.000678')
        high = next(point for point in _points(first) if _coordinate(point) == coordinate)
        assert high['available_at'] == '1997-04-29'
        completed = high_low_points_from_strokes(first['strokes'])
        projected = [point for point in completed if _coordinate(point) == coordinate]
        if asof < '1997-05-15':
            assert 'wave_completed_at' not in high and projected == []
            continue
        assert high['wave_completed_at'] == '1997-05-15'
        proof = high['wave_completion_evidence']
        assert isinstance(proof, Mapping)
        assert (proof['confirmation_rule'], proof['available_at'], proof['wave_direction_before'],
                proof['wave_direction_after']) == (
            'ordered_HH_then_HL_or_LL_then_LH_switch', '1997-05-15', 'up', 'down',
        )
        assert [(point['time'], point['kind'], point['value'], point['available_at'])
                for point in _records(proof['definition_evidence'])] == [
            ('1997-05-06', 'H', 23.79, '1997-05-07'), ('1997-05-12', 'H', 23.70, '1997-05-13'),
            ('1997-05-08', 'L', 21.50, '1997-05-09'), ('1997-05-14', 'L', 20.00, '1997-05-15'),
        ]
        assert len(projected) == 1
        assert (projected[0]['available_at'], projected[0]['source_geometry_available_at']) == (
            '1997-05-15', '1997-04-29',
        )
        second = prepare_trend_lines(completed, trend_level=2, symbol='sz.000678')
        assert _points(second, 'source_strokes') == completed


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
