"""增量版本保持历史隔离，只消费变化后缀；性能断言使用访问边界。"""

from collections.abc import Iterator
from copy import deepcopy
from typing import cast
from unittest.mock import patch

import pytest

from wavequant.domain.market_structure.owned_evidence import CertifiedSource, owned_evidence_scope
from wavequant.domain.market_structure.trend_lines import (
    _TrendCandidateCache, _TrendPermissionCache, _prepare_qualification_candidates, _prepared_payload,
)
from wavequant.domain.market_structure.trend_point_version import PointVersion
from wavequant.domain.market_structure.trend_publication import _OriginLookup
from wavequant.domain.strategies.hierarchy_stream import HierarchyHistoryStream


def point(index: int, **fields: object) -> dict[str, object]:
    return dict(dict(index=index, ordinal=0, kind='H' if index % 2 else 'L',
                     value=10. + index, time=str(index), available_at=index,
                     confirmation_rule='structural'), **fields)


def forbid_version_iteration(self: PointVersion) -> Iterator[dict[str, object]]:
    raise AssertionError('an incremental consumer traversed the complete immutable version')


def test_append_revision_and_truncation_preserve_published_versions() -> None:
    original = PointVersion(point(index) for index in range(96)).seal()
    with patch.object(PointVersion, '__iter__', forbid_version_iteration):
        appended = PointVersion([point(96)], previous=original, start=96).seal()
        revised = PointVersion([point(94, value=3.)], previous=appended, start=94).seal()
        shortened = PointVersion(previous=revised, start=40).seal()
    assert appended[0] is original[0]
    assert revised[93] is original[93]
    assert len(original) == 96 and original[94]['value'] == 104.
    assert len(appended) == 97 and len(revised) == 95 and len(shortened) == 40
    assert revised.changed_since(appended) == 94
    assert revised.changed_since(original) == 94
    with pytest.raises(TypeError, match='immutable'):
        appended[0] = point(0, value=9.)


def test_public_serialization_materializes_independent_lists() -> None:
    version = PointVersion([point(0, proof={'values': [1, 2]})]).seal()
    with owned_evidence_scope() as owner:
        payload = owner.dumps({'points': version})
        restored = cast(dict[str, object], owner.loads(payload))
        assert type(restored['points']) is list
        assert restored['points'] == list(version)
        copied = cast(list[dict[str, object]], restored['points'])
        cast(list[int], cast(dict[str, object], copied[0]['proof'])['values']).append(3)
        assert owner.loads(payload) == {'points': list(version)}
        assert _prepared_payload({'points': version}) is not None
    assert type(deepcopy(version)) is list


def test_certified_tail_shares_points_and_rejects_foreign_request() -> None:
    with owned_evidence_scope() as owner:
        old = CertifiedSource([point(i) for i in range(64)], [str(i).encode() for i in range(64)], owner, None)
        with patch.object(PointVersion, '__iter__', forbid_version_iteration):
            new = CertifiedSource.from_tail([point(64)], [b'64'], owner, old, 64)
        assert new[0] is old[0] and new.keys[0] is old.keys[0]
        assert new.parent is old.token and new.changed_from == 64
        with owned_evidence_scope() as foreign:
            with pytest.raises(ValueError, match='one evidence owner'):
                CertifiedSource.from_tail([], [], foreign, new, len(new))


def test_candidate_append_only_normalizes_new_coordinate_buckets() -> None:
    cache = _TrendCandidateCache()
    old = PointVersion(point(i) for i in range(96)).seal()
    prepared = cache.prepare(old)
    new = PointVersion([point(96)], previous=old, start=96).seal()
    with patch.object(PointVersion, '__iter__', forbid_version_iteration):
        actual = cache.prepare(new)
    assert actual[0] is prepared[0]
    assert list(actual) == _prepare_qualification_candidates(list(new), owned=False)
    assert cache.prepare(new) is actual


@pytest.mark.parametrize('start,tail', [
    (5, [point(5, available_at=20), point(5, available_at=8), point(6)]),
    (3, [point(3, value=2.), point(4)]),
    (2, []),
    (0, [point(8), point(2)]),
])
def test_candidate_revisions_match_cold_normalization(start: int, tail: list[dict[str, object]]) -> None:
    cache = _TrendCandidateCache()
    old = PointVersion(point(i) for i in range(6)).seal()
    before = deepcopy(cache.prepare(old))
    new = PointVersion(tail, previous=old, start=start).seal()
    actual = cache.prepare(new)
    assert list(actual) == _prepare_qualification_candidates(list(new), owned=False)
    assert list(old) == [point(i) for i in range(6)]
    assert before == _prepare_qualification_candidates(list(old), owned=False)


def test_permission_source_projection_preserves_identity_and_retires_old_path() -> None:
    cache = _TrendPermissionCache(())
    old = PointVersion(point(i) for i in range(96)).seal()
    prepared = cache.source_projection(1, 'a', old)
    assert cache.source_projection(1, 'a', old) is prepared
    new = PointVersion([point(96)], previous=old, start=96).seal()
    with patch.object(PointVersion, '__iter__', forbid_version_iteration):
        changed = cache.source_projection(1, 'a', new)
    assert changed[0] is prepared[0]
    assert changed[-1]['label'] == 'L'
    other = cache.source_projection(1, 'b', new)
    assert other is not changed and list(other) == list(changed)


def test_permission_projection_detects_late_knowledge_without_geometry_changes() -> None:
    cache = _TrendPermissionCache(())
    first = cache.hierarchy_projection(1, 'a', [point(0), point(1)])
    same = cache.hierarchy_projection(1, 'a', [point(0), point(1, proof={'ignored': 1})])
    assert same is first
    late = cache.hierarchy_projection(1, 'a', [point(0), point(1, available_at=4)])
    assert late.changed_since(first) == 1
    assert late[0] is first[0] and first[1]['available_at'] == 1
    assert late[1]['available_at'] == 4


def test_shared_history_updates_tail_and_releases_future_points_at_their_knowledge_day() -> None:
    stream = HierarchyHistoryStream((), shared=True)
    old = PointVersion([point(0), point(1, available_at=3)]).seal()
    first = stream._project_shared(old, 1, 1)
    assert first is not None and len(first) == 1
    second = stream._project_shared(old, 1, 2)
    assert second is not None and len(second) == 1
    third = stream._project_shared(old, 1, 3)
    assert third is not None and third[1]['available_at'] == 3
    assert first[0] is third[0]
    new = PointVersion([point(2, available_at=4)], previous=old, start=2).seal()
    with patch.object(PointVersion, '__iter__', forbid_version_iteration):
        fourth = stream._project_shared(new, 1, 4)
    assert fourth is not None and fourth[0] is third[0]
    assert len(third) == 2 and len(fourth) == 3
    assert stream._project_shared(new, 1, 5) is fourth
    stream.close()


def test_origin_index_updates_suffix_and_keeps_first_duplicate_reference() -> None:
    old = PointVersion(point(i) for i in range(96)).seal()
    lookup = _OriginLookup(old)
    duplicate = point(0, available_at=100)
    new = PointVersion([duplicate], previous=old, start=len(old)).seal()
    with patch.object(PointVersion, '__iter__', forbid_version_iteration):
        lookup.update(new)
    assert lookup.find(point(0)) is old[0]
    assert lookup.find(point(0), known_point=duplicate) is duplicate
    revised = PointVersion([point(95, value=2.)], previous=new, start=95).seal()
    lookup.update(revised)
    cold = _OriginLookup(revised)
    assert lookup.find(point(0), known_point=duplicate) is None
    for value in revised:
        assert lookup.find(value) is cold.find(value)


@pytest.mark.parametrize('start,stop', [(0, None), (31, 65), (32, 96), (95, None), (-4, None), (70, 12)])
def test_block_range_keeps_python_slice_semantics(start: int, stop: int | None) -> None:
    from wavequant.domain.market_structure.trend_frozen_state import FrozenPrefixList

    values = list(range(103))
    sequence = FrozenPrefixList(values)
    sequence.freeze(97)
    assert list(sequence.iter_range(start, stop)) == values[start:stop]
    assert sequence[start:stop] == values[start:stop]


def test_publication_lookahead_does_not_materialize_the_remaining_history() -> None:
    from wavequant.domain.market_structure.trend_publication import _candidate_tail

    candidates = PointVersion(point(i) for i in range(4096)).seal()
    with patch.object(PointVersion, '__getitem__', side_effect=AssertionError('lookahead copied or indexed its suffix')):
        tail = _candidate_tail(candidates, 2048)
        assert next(tail)['index'] == 2048
        tail.close()


def test_unchanged_permission_projection_does_not_random_access_the_frozen_tree() -> None:
    cache = _TrendPermissionCache(())
    points = [point(i) for i in range(4096)]
    previous = cache.hierarchy_projection(1, 'a', points)
    with patch.object(PointVersion, '__getitem__', side_effect=AssertionError('projection revisited the tree root')):
        assert cache.hierarchy_projection(1, 'a', points) is previous


def test_owned_clock_projection_skips_old_points_and_releases_future_knowledge() -> None:
    from wavequant.domain.market_structure.trend_lifecycle import _StructureClocks
    from wavequant.domain.market_structure import trend_lifecycle

    dates = {'2026-01-01': 0, '2026-01-05': 4}
    old = dict(index=0, available_at='2026-01-01', proof={'known_at': 0})
    future = dict(index=1, available_at=2, proof={'wave_completed_at': '2026-01-05'})
    points = PointVersion([old, future]).seal()
    clocks = _StructureClocks()
    assert clocks.advance(points, dates, 2) == (True, 4)
    with patch.object(trend_lifecycle, '_clock', side_effect=AssertionError('old proof was scanned')):
        assert clocks.advance(points, dates, 3) == (True, 4)
    assert clocks.advance(points, dates, 4) == (True, None)
    with patch.object(trend_lifecycle, '_clock', side_effect=AssertionError('old proof was scanned')):
        assert clocks.advance(points, dates, 5) == (True, None)
    revised = PointVersion([dict(future, proof={'wave_completed_at': 7})], previous=points, start=1).seal()
    assert clocks.advance(revised, dates, 5) == (True, 7)
    assert clocks.advance(PointVersion(previous=revised, start=1).seal(), dates, 6) == (True, None)
