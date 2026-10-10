"""高低点决定趋势线，各级递归不得借行情许可改变结构。"""

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta
from operator import setitem
from typing import cast
from unittest.mock import patch

import pytest

from wavequant.domain.market_structure.lecture_trend import reversal_trends
from wavequant.domain.market_structure.secondary_trend import secondary_trends
from wavequant.domain.market_structure.tertiary_trend import tertiary_trends
from wavequant.domain.market_structure.trend_judgement import TrendJudgement, judge_trend
from wavequant.domain.market_structure.trend_lines import trend_lines
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies import hierarchical_entry


START = datetime(2026, 1, 1)
# 图008的已标注点；最后的上升尾端不足以证明新的整波反向。
FIGURE_008 = (20, 30, 10, 20, 14, 26, 18, 35, 28, 45, 38, 42, 32, 36, 24, 29, 26)
NESTED_MACRO = (
    120, 240, 110, 230, 100, 220, 110, 230, 120, 240,
    110, 230, 105, 225, 115, 235, 125, 245, 115, 235,
    110, 230, 120, 240, 130, 250, 120, 240, 90, 210,
    100, 220, 110, 230, 100, 220, 80, 200, 90, 210, 100, 220,
)
# 最后一列是紧邻一级来源中两类证据首次齐全的位置；不额外等下一同类holding。
NESTED_SECONDARY = ((12, 'L', 500, 6), (27, 'H', 1200, 10), (36, 'L', 525, 14),
                    (51, 'H', 1225, 18), (60, 'L', 550, 22), (75, 'H', 1250, 26),
                    (84, 'L', 450, 30), (99, 'H', 1150, 34), (108, 'L', 400, 38))
StructureHistory = dict[int, dict[int, tuple[dict[str, object], ...]]]


def _day(index: int) -> str:
    return (START + timedelta(days=index)).date().isoformat()


def _points(values: Sequence[int], *, source_level: int = 0, mirror: bool = False,
            integer_clock: bool = False) -> list[dict[str, object]]:
    points: list[dict[str, object]] = []
    for index, value in enumerate(values):
        kind = 'L' if index % 2 == 0 else 'H'
        if mirror:
            kind = 'H' if kind == 'L' else 'L'
            value = 100 - value
        points.append(dict(index=index, ordinal=0, kind=kind, value=value, time=_day(index),
                           available_at=index + 1 if integer_clock else _day(index + 1),
                           state='confirmed', trend_level=source_level))
    return points


def _geometry(points: Sequence[Mapping[str, object]]) -> list[tuple[object, ...]]:
    return [(point['index'], point.get('ordinal', 0), point['kind'], point['value'], point['available_at'])
            for point in points]


def _nested_points(*, integer_clock: bool) -> list[dict[str, object]]:
    # 每段宏观走势包含一次反向微波，一级还原宏观极值，二三级继续用同一规则。
    values = [value for left, right in zip(NESTED_MACRO, NESTED_MACRO[1:])
              for value in (5 * left, 5 * left + 4 * (right - left), 5 * left + 3 * (right - left))]
    values.append(5 * NESTED_MACRO[-1])
    points = _points(values, integer_clock=integer_clock)
    # 全部点在最后一次观察时才可知，故任何攻击价格日都没有已知本级旧关键位。
    # 此样例隔离普通三级递归；即时突破另外用图008高低镜像验证。
    for point in points:
        point['available_at'] = len(points) if integer_clock else _day(len(points))
    return points


def _plain(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return tuple(_plain(item) for item in value)
    return value


def _structure(points: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    fields = ('index', 'ordinal', 'kind', 'value', 'time', 'available_at', 'state', 'label', 'trend_level', 'source_level',
              'wave_direction_before', 'wave_direction_after', 'direction_definition', 'confirmation_rule',
              'broken_key', 'definition_evidence', 'confirmed_by', 'upgrade_evidence', 'source_confirmation',
              'source_turn_position', 'confirmed_on_turn', 'source_reversal_available_at',
              'source_level1_position', 'confirmed_on_level1', 'source_level1_available_at',
              'source_level2_position', 'confirmed_on_level2', 'source_level2_available_at')
    return [{field: _plain(point[field]) for field in fields if field in point} for point in points]


def _stroke_points(bundle: Mapping[str, object], field: str = 'strokes') -> list[Mapping[str, object]]:
    strokes = bundle[field]
    assert isinstance(strokes, (list, tuple))
    points: list[Mapping[str, object]] = []
    for stroke in strokes:
        assert isinstance(stroke, Mapping)
        raw = stroke['points']
        assert isinstance(raw, (list, tuple))
        assert all(isinstance(point, Mapping) for point in raw)
        points.extend(cast(Sequence[Mapping[str, object]], raw))
    return points


def _bars(points: Sequence[Mapping[str, object]], *, market: bool) -> list[Bar]:
    count = max(cast(int, point['index']) for point in points) + 2
    bars = [Bar(START + timedelta(days=index), 'FIG008', 50, 100, 1, 50, 1000)
            for index in range(count)]
    if market:
        for point in points:
            index = cast(int, point['index'])
            value = cast(int, point['value'])
            low, high = (value, value + 1) if point['kind'] == 'L' else (value - 1, value)
            middle = (low + high) / 2
            bars[index] = replace(bars[index], open=middle, high=high, low=low, close=middle)
        bars[-1] = replace(bars[-2], timestamp=bars[-1].timestamp)
    return bars


def _history_bars() -> list[Bar]:
    values = (40, *FIGURE_008, 33, 30, 42, 31)
    bars: list[Bar] = []
    for index, value in enumerate(values):
        high, low = (value, value - 1) if index % 2 == 0 else (value + 1, value)
        middle = (high + low) / 2
        bars.append(Bar(START + timedelta(days=index), 'FIG008', middle, high, low, middle, 1000))
    return bars


@pytest.mark.parametrize('level', (1, 2, 3))
@pytest.mark.parametrize('integer_clock', (False, True))
def test_figure008_uses_identical_point_rules_and_own_knowledge_dates(level: int, integer_clock: bool) -> None:
    source = _points((*FIGURE_008, 33, 30, 42), source_level=level - 1, integer_clock=integer_clock)
    before = deepcopy(source)
    result = judge_trend(source, trend_level=level)
    # H5、L12、H17各自补齐本轮另一类严格升降证据，来源确认后立即定义整波。
    moments: tuple[object, ...] = (6, 13, 18) if integer_clock else (_day(6), _day(13), _day(18))
    assert _geometry(result.points) == [(2, 0, 'L', 10, moments[0]), (9, 0, 'H', 45, moments[1]),
                                       (14, 0, 'L', 24, moments[2])]
    assert [point['wave_direction_after'] for point in result.points] == ['up', 'down', 'up']
    assert [point['trend_level'] for point in result.points] == [level] * 3
    assert [point['source_level'] for point in result.points] == [level - 1] * 3
    field = 'confirmed_on_turn' if level == 1 else f'confirmed_on_level{level - 1}'
    assert [point[field] for point in result.points] == [5, 12, 17]
    assert source == before


@pytest.mark.parametrize('integer_clock', (False, True))
def test_asof_reduces_known_source_prefix_instead_of_filtering_the_final_line(integer_clock: bool) -> None:
    source = _points((*FIGURE_008, 33, 30, 42), integer_clock=integer_clock)
    golden = {5: [], 6: [(2, 'L', 10)], 12: [(2, 'L', 10)],
              13: [(2, 'L', 10), (9, 'H', 45)], 17: [(2, 'L', 10), (9, 'H', 45)],
              18: [(2, 'L', 10), (9, 'H', 45), (14, 'L', 24)]}
    for index, expected in golden.items():
        asof = index if integer_clock else _day(index)
        known = ([point for point in source if cast(int, point['available_at']) <= index] if integer_clock else
                 [point for point in source if cast(str, point['available_at']) <= _day(index)])
        observed = judge_trend(source, asof=asof)
        assert [(point['index'], point['kind'], point['value']) for point in observed.points] == expected
        assert observed == judge_trend(known, asof=asof)


@pytest.mark.parametrize('level', (1, 2, 3))
@pytest.mark.parametrize('mirror', (False, True))
def test_strict_key_break_immediately_adds_origin_and_endpoint_without_future_holding(level: int, mirror: bool) -> None:
    source = _points((*FIGURE_008, 50), source_level=level - 1, mirror=mirror)
    before = judge_trend(source, trend_level=level, asof=_day(17))
    result = judge_trend(source, trend_level=level, asof=_day(18))
    expected = [(2, 0, 'L', 10, _day(6)), (9, 0, 'H', 45, _day(13)),
                (14, 0, 'L', 24, _day(18)), (17, 0, 'H', 50, _day(18))]
    if mirror:
        expected = [(index, ordinal, 'H' if kind == 'L' else 'L', 100 - value, known)
                    for index, ordinal, kind, value, known in expected]
    assert _geometry(before.points) == expected[:2]
    assert _geometry(result.points) == expected
    endpoint = result.points[-1]
    proof = endpoint['upgrade_evidence']
    assert isinstance(proof, Mapping)
    assert proof['direction'] == endpoint['wave_direction_after'] == ('down' if mirror else 'up')
    assert proof['role'] == 'endpoint'
    assert proof['available_at'] == _day(18)
    key = proof['broken_key']
    assert isinstance(key, Mapping)
    assert (key['index'], key['value'], key['available_at']) == (9, 55 if mirror else 45, _day(13))
    assert len(source) == 18


@pytest.mark.parametrize('level', (1, 2, 3))
def test_generic_driver_uses_judgement_geometry_with_and_without_market_bars(level: int) -> None:
    source = _points((*FIGURE_008, 50), source_level=level - 1)
    expected = judge_trend(source, trend_level=level)
    results = [trend_lines(source, bars, trend_level=level)
               for bars in ((), _bars(source, market=False), _bars(source, market=True))]
    assert all(_structure(_stroke_points(result)) == _structure(expected.points) for result in results)


def test_primary_wrapper_only_adapts_the_original_high_low_drawing() -> None:
    source = _points((*FIGURE_008, 50))
    for point in source:
        index = cast(int, point['index']) + 1
        point.update(index=index, time=_day(index), available_at=_day(index + 1))
    seed = dict(index=0, ordinal=0, kind='H', value=40, time=_day(0), available_at=_day(0), state='seed')
    tail_index = len(source) + 1
    tail = dict(index=tail_index, ordinal=0, kind='L', value=30, time=_day(tail_index),
                available_at=_day(tail_index), state='developing')
    drawing: dict[str, object] = dict(strokes=[dict(id='figure008', points=[seed, *source, tail])])
    before = deepcopy(drawing)
    expected = judge_trend(source)
    for market in (False, True):
        observed = reversal_trends(drawing, _bars(source, market=market))
        assert _structure(_stroke_points(observed)) == _structure(expected.points)
    assert drawing == before


@pytest.mark.parametrize('level', (2, 3))
def test_formal_wrappers_keep_the_entire_judged_line_when_only_bars_change(level: int) -> None:
    source = _points((*FIGURE_008, 50), source_level=level - 1)
    bundle: dict[str, object] = dict(strokes=[dict(id='figure008', trend_level=level - 1,
                                                  kind='reversal' if level == 2 else 'secondary', points=source)])
    before = deepcopy(bundle)
    expected = judge_trend(source, trend_level=level)
    derive = secondary_trends if level == 2 else tertiary_trends
    placeholder = derive(bundle, _bars(source, market=False))
    market = derive(bundle, _bars(source, market=True))
    assert _structure(_stroke_points(placeholder)) == _structure(expected.points)
    assert _structure(_stroke_points(market)) == _structure(expected.points)
    assert bundle == before


@pytest.mark.parametrize('level', (2, 3))
def test_separate_source_paths_never_supply_each_others_reversal_or_strict_break(level: int) -> None:
    source = _points((*FIGURE_008, 50), source_level=level - 1)
    # 在L12之前断路：左侧只有较低H11，不能借右侧L12补齐下降定义H9。
    # 右侧随后可凭自己的两高两低形成L14，但没有本路径旧H9，不能升级H17。
    predecessor: dict[str, object] = dict(strokes=[dict(id='left', points=source[:12]),
                                                   dict(id='right', points=source[12:])])
    expected = judge_trend(source[:12], trend_level=level, asof=_day(18))
    right = judge_trend(source[12:], trend_level=level, asof=_day(18))
    assert _geometry(expected.points) == [(2, 0, 'L', 10, _day(6))]
    assert _geometry(right.points) == [(14, 0, 'L', 24, _day(18))]
    assert not any(point.get('upgrade_evidence') is not None for point in right.candidates)
    derive = secondary_trends if level == 2 else tertiary_trends
    observed = derive(predecessor, _bars(source, market=True))
    assert _structure(_stroke_points(observed)) == _structure((*expected.points, *right.points))
    assert not any(point['index'] in (9, 17) for point in _stroke_points(observed))


def test_same_kind_merge_keeps_the_selected_extremes_own_later_knowledge_date() -> None:
    source = _points((*FIGURE_008, 50, 30, 55, 35, 53, 29, 48), integer_clock=True)
    source[17]['available_at'] = 19
    early = judge_trend(source, asof=22)
    before = _structure(early.points)
    later = judge_trend(source, asof=23)
    assert _geometry(early.points)[-1] == (21, 0, 'H', 53, 22)
    assert _geometry(later.points)[-1] == (19, 0, 'H', 55, 23)
    assert [(point['index'], point['value'], point['available_at']) for point in later.candidates
            if point['index'] in (19, 21)] == [(19, 55, 23), (21, 53, 22)]
    assert all(left['kind'] != right['kind'] for left, right in zip(later.points, later.points[1:]))
    assert _structure(early.points) == before
    known = [point for point in source if cast(int, point['available_at']) <= 22]
    assert early == judge_trend(known, asof=22)


@pytest.mark.parametrize('integer_clock', (False, True))
def test_three_nonempty_levels_consume_the_complete_previous_judgement_without_relabeling(integer_clock: bool) -> None:
    source = _nested_points(integer_clock=integer_clock)
    before = deepcopy(source)
    known: int | str = len(source) if integer_clock else _day(len(source))
    first = judge_trend(source)
    second = judge_trend(first.points, trend_level=2)
    third = judge_trend(second.points, trend_level=3)
    primary = [(3 * index, 0, 'H' if index % 2 else 'L', 5 * value, known)
               for index, value in enumerate(NESTED_MACRO[1:-1], start=1)]
    assert len(source) == 124
    assert _geometry(first.points) == primary
    assert [point['confirmed_on_turn'] for point in first.points] == [3 * index + 2 for index in range(1, 41)]
    assert _geometry(second.points) == [(index, 0, kind, value, known)
                                       for index, kind, value, _ in NESTED_SECONDARY]
    assert [point['confirmed_on_level1'] for point in second.points] == [position for *_, position in NESTED_SECONDARY]
    assert _geometry(third.points) == [(75, 0, 'H', 1250, known)]
    assert third.points[0]['confirmed_on_level2'] == 7
    assert third.points[0]['wave_direction_after'] == 'down'
    assert source == before
    early: int | str = 123 if integer_clock else _day(123)
    assert judge_trend(source, asof=early).points == ()
    assert judge_trend(source, asof=known) == first


def test_chart_wrappers_recurse_over_every_geometric_point_without_strategy_filtering() -> None:
    source = _nested_points(integer_clock=False)
    bars = _bars(source, market=True)
    first_judgement = judge_trend(source)
    second_judgement = judge_trend(first_judgement.points, trend_level=2)
    third_judgement = judge_trend(second_judgement.points, trend_level=3)
    first = trend_lines(source, bars)
    second = secondary_trends(first, bars)
    third = tertiary_trends(second, bars)
    assert _structure(_stroke_points(first)) == _structure(first_judgement.points)
    assert _structure(_stroke_points(second)) == _structure(second_judgement.points)
    assert _structure(_stroke_points(third)) == _structure(third_judgement.points)
    assert (len(_stroke_points(first)), len(_stroke_points(second)), len(_stroke_points(third))) == (40, 9, 1)
    assert _structure(_stroke_points(second, 'source_strokes')) == _structure(_stroke_points(first))
    assert _structure(_stroke_points(third, 'source_strokes')) == _structure(_stroke_points(second))


def test_real_history_recurses_over_geometry_while_returning_only_strategy_qualified_points() -> None:
    bars = _history_bars()
    structures: StructureHistory = {}
    sources: dict[tuple[int, int], list[tuple[object, ...]]] = {}
    source_contracts: dict[tuple[int, int], list[dict[str, object]]] = {}
    judgements: dict[tuple[int, int], list[dict[str, object]]] = {}

    def observe(points: Sequence[Mapping[str, object]], *, trend_level: int = 1,
                asof: int | str | None = None) -> TrendJudgement:
        assert type(asof) is int
        sources[(asof, trend_level)] = _geometry(points)
        source_contracts[(asof, trend_level)] = _structure(points)
        if trend_level > 1:
            assert all('trend_confirmation' not in point and 'incoming_trend_confirmation' not in point
                       for point in points)
        result = judge_trend(points, trend_level=trend_level, asof=asof)
        judgements[(asof, trend_level)] = _structure(result.points)
        return result

    # 只记录真实判断入口，不替换计算结果或折线来源。
    with patch('wavequant.domain.strategies.hierarchical_entry.judge_trend', side_effect=observe):
        qualified, epochs = hierarchical_entry.hierarchical_history(bars, structure_sink=structures)
    # 真实逐日折线的来源发生位置比图008点数组偏移1；确认仍取两类证据齐全首日。
    golden = ((3, 0, 'L', 10, 7), (10, 0, 'H', 45, 14), (15, 0, 'L', 24, 19))
    assert epochs == {index: 0 for index in range(len(bars))}
    for now in range(len(bars)):
        assert _geometry(structures[now][1]) == [point for point in golden if point[-1] <= now]
        assert sources[(now, 2)] == _geometry(structures[now][1])
        assert sources[(now, 3)] == _geometry(structures[now][2])
        assert source_contracts[(now, 2)] == judgements[(now, 1)]
        assert source_contracts[(now, 3)] == judgements[(now, 2)]
        assert structures[now][2] == structures[now][3] == ()
        assert _geometry(qualified[now][1]) == ([(10, 0, 'H', 45, 14)] if now >= 14 else [])
        assert qualified[now][2] == qualified[now][3] == ()
    assert sources[(21, 2)] == list(golden)
    assert len(qualified[21][1]) == 1


def test_real_history_structure_sink_keeps_cold_warm_and_partial_prefix_caches_identical() -> None:
    bars = _history_bars()
    complete: StructureHistory = {}
    full = hierarchical_entry.hierarchical_history(bars, structure_sink=complete)
    cache: dict[str, object] = {}
    for end in (9, 16, 21, 22):
        prefix = bars[:end]
        fresh_structure: StructureHistory = {}
        expected = hierarchical_entry.hierarchical_history(prefix, structure_sink=fresh_structure)
        cached_structure: StructureHistory = {-1: {}}
        observed = hierarchical_entry.hierarchical_history(prefix, prefix_cache=cache, structure_sink=cached_structure)
        assert observed == expected
        assert cached_structure == fresh_structure == {now: complete[now] for now in range(end)}
        warm_structure: StructureHistory = {}
        assert hierarchical_entry.hierarchical_history(prefix, prefix_cache=cache, structure_sink=warm_structure) == expected
        assert warm_structure == fresh_structure

    checkpoint = deepcopy(cache['checkpoint'])
    partial = [*bars[:-1], replace(bars[-2], timestamp=bars[-1].timestamp)]
    fresh_partial: StructureHistory = {}
    cached_partial: StructureHistory = {}
    expected_partial = hierarchical_entry.hierarchical_history(partial, structure_sink=fresh_partial)
    observed_partial = hierarchical_entry.hierarchical_history(partial, prefix_cache=cache, structure_sink=cached_partial)
    assert observed_partial == expected_partial
    assert cached_partial == fresh_partial
    assert cache['checkpoint'] == checkpoint
    restored: StructureHistory = {}
    assert hierarchical_entry.hierarchical_history(bars, prefix_cache=cache, structure_sink=restored) == full
    assert restored == complete

    following = [*bars, Bar(START + timedelta(days=len(bars)), 'FIG008', 42.5, 43, 42, 42.5, 1000)]
    fresh_next: StructureHistory = {}
    cached_next: StructureHistory = {}
    assert hierarchical_entry.hierarchical_history(following, prefix_cache=cache, structure_sink=cached_next) == (
        hierarchical_entry.hierarchical_history(following, structure_sink=fresh_next))
    assert cached_next == fresh_next
    assert {now: cached_next[now] for now in range(len(bars))} == complete


def test_requesting_geometry_or_loading_an_old_cache_rebuilds_the_complete_structure_history() -> None:
    bars = _history_bars()
    structures: StructureHistory = {}
    expected = hierarchical_entry.hierarchical_history(bars, structure_sink=structures)
    strategy_only_cache: dict[str, object] = {}
    hierarchical_entry.hierarchical_history(bars, prefix_cache=strategy_only_cache)
    newly_requested: StructureHistory = {}
    assert hierarchical_entry.hierarchical_history(bars, prefix_cache=strategy_only_cache,
                                                   structure_sink=newly_requested) == expected
    assert newly_requested == structures
    old_cache: dict[str, object] = dict(key=('daily_recursive_ordered_trend_definitions_v112', tuple(bars[:-1]), False, False),
                                        checkpoint=('obsolete',))
    rebuilt: StructureHistory = {}
    assert hierarchical_entry.hierarchical_history(bars, prefix_cache=old_cache, structure_sink=rebuilt) == expected
    assert rebuilt == structures


def test_modifying_public_history_views_cannot_change_a_saved_prefix_checkpoint() -> None:
    bars = _history_bars()
    expected_structure: StructureHistory = {}
    expected = hierarchical_entry.hierarchical_history(bars, structure_sink=expected_structure)
    cache: dict[str, object] = {}
    public_structure: StructureHistory = {}
    public_history, _ = hierarchical_entry.hierarchical_history(bars, prefix_cache=cache, structure_sink=public_structure)
    checkpoint = deepcopy(cache['checkpoint'])
    public_structure[15][1][0]['value'] = 999
    public_history[15][1][0]['available_at'] = 99
    assert cache['checkpoint'] == checkpoint
    warm_structure: StructureHistory = {}
    assert hierarchical_entry.hierarchical_history(bars, prefix_cache=cache, structure_sink=warm_structure) == expected
    assert warm_structure == expected_structure
    assert cache['checkpoint'] == checkpoint


@pytest.mark.parametrize('level', (2, 3))
def test_lower_level_licenses_never_authorize_the_upper_judgement(level: int) -> None:
    source = _points((*FIGURE_008, 50), source_level=level - 1)
    contaminated = deepcopy(source)
    lower_proof = dict(direction='up', trend_level=level - 1, available_at=_day(1),
                       confirmation_rule='lower_level_n_target', one_p_target=1,
                       broken_key=dict(index=0, kind='H', value=1, available_at=_day(0)))
    for point in contaminated:
        point.update(trend_confirmation=lower_proof, incoming_trend_confirmation=lower_proof,
                     active_trend_confirmation=lower_proof, n_target_confirmation=lower_proof,
                     wave_id='lower-level-only')
    before = deepcopy(contaminated)
    expected = judge_trend(source, trend_level=level)
    observed = judge_trend(contaminated, trend_level=level)
    assert _structure(observed.points) == _structure(expected.points)
    assert _structure(observed.candidates) == _structure(expected.candidates)
    for selected in (*observed.points, *observed.candidates):
        assert not any(field in selected for field in ('trend_confirmation', 'incoming_trend_confirmation',
                                                      'active_trend_confirmation', 'n_target_confirmation', 'wave_id'))
    assert contaminated == before


def test_judgement_is_an_immutable_snapshot_and_remains_a_direct_recursive_input() -> None:
    source = _points((*FIGURE_008, 33, 30, 42))
    result = judge_trend(source)
    frozen = _structure(result.points)
    assert isinstance(result.points, tuple) and isinstance(result.candidates, tuple)
    with pytest.raises(FrozenInstanceError):
        setattr(result, 'points', ())
    with pytest.raises(TypeError):
        setitem(cast(dict[str, object], result.points[0]), 'value', 999)
    evidence = result.points[0]['definition_evidence']
    assert isinstance(evidence, tuple) and isinstance(evidence[0], Mapping)
    with pytest.raises(TypeError):
        setitem(cast(dict[str, object], evidence[0]), 'value', 999)
    source[2]['value'] = 999
    assert _structure(result.points) == frozen
    assert judge_trend(result.points, trend_level=2).points == ()
