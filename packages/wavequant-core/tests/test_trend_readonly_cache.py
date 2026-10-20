"""只读借用不越过版本、请求和公共可变导出的边界。"""

from copy import deepcopy
from typing import cast
from unittest.mock import patch

import pytest

from wavequant.domain.market_structure.trend_judgement import TrendJudgement, _frozen_point, mutable_points
from wavequant.domain.market_structure.trend_readonly_cache import _ReadonlySnapshotCache
from wavequant.domain.strategies import hierarchy_stream as stream_module
from wavequant.domain.strategies.hierarchy_stream import HierarchyHistoryStream
from tests.test_lecture_strategy import history


@pytest.mark.parametrize('limit', [0, 4096, 12000, 100000])
def test_readonly_cache_is_bounded_and_revisions_never_hit_old_tokens(limit: int) -> None:
    cache = _ReadonlySnapshotCache[object](memory_bytes=limit, max_entries=2)
    for index in range(12):
        cache.put(index, ('version', index), {'proof': [index, 'x' * 1000]})
        assert cache.memory_used <= limit
        assert len(cache._entries) <= 2
        assert cache.get(index, ('version', index + 1)) is None
    cache.put(11, ('version', 12), {'proof': 'x' * 200000})
    assert cache.get(11, ('version', 11)) is None
    assert cache.get(11, ('version', 12)) is None
    cache.clear()
    assert cache.memory_used == 0


def test_unknown_values_are_not_cached_or_inspected_with_custom_hooks() -> None:
    class Unknown:
        def __sizeof__(self) -> int:
            raise AssertionError('unknown value must not be inspected')

    cache = _ReadonlySnapshotCache[object](memory_bytes=10000, max_entries=2)
    cache.put(1, b'old', {'proof': 1})
    cache.put(1, b'new', Unknown())
    assert cache.get(1, b'new') is None
    assert cache.get(1, b'old') is None
    assert cache.memory_used == 0


def test_frozen_templates_reuse_only_matching_versions_and_keep_public_exports_owned() -> None:
    stream = HierarchyHistoryStream(history(18, 5))
    point = _frozen_point({'index': 1, 'proof': {'known': 2}})
    try:
        with patch.object(stream_module, 'mutable_points', wraps=mutable_points) as decode:
            first = stream._readonly_points((point,), level=1, kind='candidates')
            again = stream._readonly_points((point,), level=1, kind='candidates')
            assert again is not first and again[0] is first[0]
            assert decode.call_count == 1
            exported = mutable_points((point,))
            cast(dict[str, object], exported[0]['proof'])['known'] = 99
            assert first == [{'index': 1, 'proof': {'known': 2}}]
            revised = _frozen_point({'index': 1, 'proof': {'known': 3}})
            assert stream._readonly_points((revised,), level=1, kind='candidates') != first
            assert decode.call_count == 2
            other_level = stream._readonly_points((point,), level=2, kind='candidates')
            assert other_level == first and other_level is not first
        raw = [{'index': 1, 'proof': {'known': 2}}]
        one = stream._readonly_points(raw, level=1, kind='geometry')
        two = stream._readonly_points(raw, level=1, kind='geometry')
        assert one == two and one is not two
        raw[0]['proof'] = {'known': 5}
        assert stream._readonly_points(raw, level=1, kind='geometry') != one
    finally:
        stream.close()
    assert stream._decoded.memory_used == 0


@pytest.mark.parametrize('enabled', [False, True])
def test_unchanged_geometry_still_qualifies_all_levels_on_every_day(enabled: bool) -> None:
    stream = HierarchyHistoryStream(history(18, 5), n_enabled=enabled)
    qualify = stream_module.qualify_trend_structure
    try:
        with patch.object(stream_module, 'qualify_trend_structure', wraps=qualify) as calls:
            for index in range(5):
                stream.advance(index, 0, [])
            assert calls.call_count == 15
            assert [len(call.args[2]) for call in calls.call_args_list] == [day for day in range(1, 6) for _ in range(3)]
        saved = deepcopy(stream.history)
    finally:
        stream.close()
    assert stream.history == saved


def test_stable_admission_keeps_old_prefix_when_a_scan_exceeds_capacity() -> None:
    cache = _ReadonlySnapshotCache[object](memory_bytes=12000, max_entries=2, evict=False)
    cache.put('first', None, {'proof': 1})
    cache.put('second', None, {'proof': 2})
    for index in range(20):
        cache.put(('tail', index), None, {'proof': index})
    assert cache.get('first', None) == ({'proof': 1},)
    assert cache.get('second', None) == ({'proof': 2},)
    assert len(cache._entries) == 2


def test_revised_tail_only_decodes_new_points_and_preserves_old_knowledge() -> None:
    stream = HierarchyHistoryStream(history(18, 5))
    head = _frozen_point({'index': 0, 'proof': {'known': 0}})
    old_tail = _frozen_point({'index': 1, 'proof': {'known': 1}})
    new_tail = _frozen_point({'index': 1, 'proof': {'known': 2}})
    try:
        old = stream._readonly_points((head, old_tail), level=1, kind='geometry')
        with patch.object(stream_module, 'mutable_points', wraps=mutable_points) as decode:
            new = stream._readonly_points((head, new_tail), level=1, kind='geometry')
            assert decode.call_count == 1
            assert decode.call_args.args[0] == [new_tail]
        assert new[0] is old[0]
        assert old[1]['proof'] == {'known': 1}
        assert new[1]['proof'] == {'known': 2}
    finally:
        stream.close()


def test_recursive_structure_reuse_requires_complete_frozen_knowledge() -> None:
    point = _frozen_point({'index': 0, 'available_at': 1, 'wave_completed_at': 3})
    judgement = TrendJudgement(1, 0, (point,), ())
    assert not stream_module._available_structure(judgement, 2)
    assert stream_module._available_structure(judgement, 3)

    for raw in (
        {'index': 0, 'available_at': 4},
        {'index': 4, 'available_at': 1},
        {'index': 0, 'available_at': True},
        {'index': 0, 'available_at': 1, 'wave_completed_at': '2020-01-01'},
        {'index': 0, 'available_at': 1, 'proof': object()},
    ):
        assert not stream_module._available_structure(TrendJudgement(1, 0, (_frozen_point(raw),), ()), 3)
    unknown_candidate = _frozen_point({'proof': object()})
    assert not stream_module._available_structure(TrendJudgement(1, 0, (point,), (unknown_candidate,)), 3)
    assert not stream_module._available_structure(
        TrendJudgement(1, 0, ({'index': 0, 'available_at': 1},), ()), 3)
