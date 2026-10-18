"""闭合来源各证明轮次共享预算，历史回读和活动路径不能相互污染。"""

from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
from typing import cast

import pytest

from wavequant.domain.market_structure.lecture_drawing import lecture_drawing
from wavequant.domain.market_structure.trend_cascade import _TrendCascadeCache, prepare_trend_cascade
from wavequant.domain.market_structure.trend_lines import (
    _ClosedPreparedPaths, _PreparedPathReference, _TrendJudgementCache,
    polyline_high_low_points, prepare_trend_lines,
)
from wavequant.domain.models.model import Bar


def _two_paths() -> list[dict[str, object]]:
    fixture = json.loads((Path(__file__).parent / 'fixtures/huaci_2024_key_break.json').read_text(encoding='utf-8'))
    bars = [Bar(datetime.fromisoformat(row[0]), fixture['symbol'], *row[1:])
            for row in fixture['bars'] if row[0] <= '2024-04-30']
    points = polyline_high_low_points(lecture_drawing(bars))
    dates = {bar.timestamp.date().isoformat(): index for index, bar in enumerate(bars)}
    offset = len(bars) + 10
    source = []
    for path, shift in (('closed-a', 0), ('active-b', offset)):
        for point in points:
            item = dict(index=cast(int, point['index']) + shift, ordinal=point['ordinal'],
                time=str(cast(int, point['index']) + shift),
                available_at=dates[cast(str, point['available_at'])] + shift,
                kind=point['kind'], value=point['value'], trend_level=0, state='reversal', source_path=path)
            if not shift:
                item['source_path_end_index'] = offset
            source.append(item)
    return source


@pytest.mark.parametrize('closed_budget', (1, 8 * 1024 * 1024))
def test_closed_promoted_paths_do_not_evict_the_active_round_state(closed_budget: int) -> None:
    source = _two_paths()
    before = deepcopy(source)
    expected = prepare_trend_cascade(source, symbol='TWO-PATH')
    workers = [_TrendJudgementCache(tail_reuse=True, state_memory_bytes=32 * 1024 * 1024) for _ in range(3)]
    closed = [_ClosedPreparedPaths(memory_bytes=closed_budget) for _ in range(3)]
    with _TrendCascadeCache(workers) as rounds:
        assert prepare_trend_cascade(source, symbol='TWO-PATH', caches=workers,
            closed_paths=closed, _round_caches=rounds) == expected
        extra = rounds.workers_for(1)
        assert extra is not None
        initial = [{key: (state, state.cold_runs) for key, state in worker._states.items()}
                   for worker in extra]
        assert initial[0] and initial[1]
        assert all('active-b' in path for states in initial for _, path in states)
        assert prepare_trend_cascade(deepcopy(source), symbol='TWO-PATH', caches=workers,
            closed_paths=closed, _round_caches=rounds) == expected
        assert [{key: (state, state.cold_runs) for key, state in worker._states.items()}
                for worker in extra] == initial
        if closed_budget == 1:
            assert all(not cache._paths and not cache._objects for cache in closed)
        else:
            first = closed[0]._paths[1, 'closed-a', 'TWO-PATH', 0]
            promoted = closed[0]._paths[1, 'closed-a', 'TWO-PATH', 1]
            assert first.signature != promoted.signature
        # 已闭合旧版本有真实修订时独立冷算，不能用它淘汰另一条活动来源。
        revised = deepcopy(source)
        revised[0]['audit'] = {'correction': ['closed-only']}
        assert prepare_trend_cascade(revised, symbol='TWO-PATH', caches=workers,
            closed_paths=closed, _round_caches=rounds) == prepare_trend_cascade(revised, symbol='TWO-PATH')
        assert extra[0]._states[1, 'active-b'] is initial[0][1, 'active-b'][0]
    assert source == before


def _closed_source(slot: int) -> list[dict[str, object]]:
    prices = (20, 30, 10, 20, 14, 26, 18, 35, 28, 45, 36, 42, 30, 36, 24, 31, 26, 50)
    return [dict(index=index, ordinal=0, time=str(index), available_at=index + 1,
        kind='L' if index % 2 == 0 else 'H', value=value, trend_level=0,
        state='reversal', source_path='closed', source_path_end_index=100,
        audit={'round': [slot]}) for index, value in enumerate(prices)]


def test_round_slots_preserve_independent_history_and_extra_rounds_cannot_replace_them() -> None:
    closed = _ClosedPreparedPaths()
    rows = []
    for slot in range(3):
        source = _closed_source(slot)
        expected = prepare_trend_lines(source)
        prepared = prepare_trend_lines(source, _closed_paths=closed, _closed_slot=slot)
        assert prepared == expected
        encoded = closed.encode(prepared)
        assert isinstance(encoded['source_strokes'][0], _PreparedPathReference)
        rows.append((encoded, deepcopy(expected)))
    original_paths = dict(closed._paths)
    original_bytes = tuple(closed._objects)
    original_used = closed._memory_used
    for slot in (None, 3, 1000):
        source = _closed_source(99)
        assert prepare_trend_lines(source, _closed_paths=closed, _closed_slot=slot) == prepare_trend_lines(source)
    assert closed._paths == original_paths
    assert tuple(closed._objects) == original_bytes
    assert closed._memory_used == original_used
    # 公共默认仍是第零槽，后来版本不得覆盖已有引用。
    assert closed.get(1, 'closed', _closed_source(0), 100) is original_paths[1, 'closed', 'STRUCTURE', 0]
    for encoded, expected in rows:
        decoded = closed.decode(encoded)
        assert decoded == expected
        decoded['source_strokes'][0]['points'][0]['audit']['round'].append('caller-mutation')
        assert closed.decode(encoded) == expected


def test_round_slots_share_one_budget_and_rejected_versions_keep_the_cold_result() -> None:
    measure = _ClosedPreparedPaths()
    prepare_trend_lines(_closed_source(0), _closed_paths=measure)
    closed = _ClosedPreparedPaths(memory_bytes=measure._memory_used + 1)
    for slot in range(3):
        source = _closed_source(slot)
        expected = prepare_trend_lines(source)
        prepared = prepare_trend_lines(source, _closed_paths=closed, _closed_slot=slot)
        assert prepared == expected
        encoded = closed.encode(prepared)
        assert isinstance(encoded['source_strokes'][0], _PreparedPathReference) is (slot == 0)
        assert closed.decode(encoded) == expected
    assert set(closed._paths) == {(1, 'closed', 'STRUCTURE', 0)}
    assert closed._memory_used == measure._memory_used <= closed._memory_bytes
