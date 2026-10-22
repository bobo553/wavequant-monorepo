"""历史行只连接生产根，并在一次读取中保留全部可变对象别名。"""

from typing import Never, cast

import pytest

from wavequant.domain.market_structure.owned_evidence import CertifiedSource, owned_evidence_scope
from wavequant.domain.market_structure.owned_evidence_producer import producer_record_scope
from wavequant.domain.market_structure.owned_evidence_records import OwnedEvidenceRecords
from wavequant.domain.market_structure.owned_record_prepared_history import OwnedRecordPreparedHistory
from wavequant.domain.market_structure.trend_lines import _ClosedPreparedPaths, _prepared_payload
from wavequant.domain.market_structure.trend_point_version import PointVersion, record_point_version


def _mapping(value: object) -> dict[str, object]:
    return cast(dict[str, object], value)


def _rows(value: object) -> list[dict[str, object]]:
    return cast(list[dict[str, object]], value)


def _prepared(points: object) -> dict[str, object]:
    stroke: dict[str, object] = {'id': 'path', 'points': points, 'source_paths': ['path']}
    shared = [stroke]
    return dict(source_strokes=shared, candidate_strokes=shared, structure_strokes=shared,
                strokes=shared, developing_strokes=[], qualified_strokes=[],
                _path_market_end_indices={'path': None}, trend_level=1)


def test_history_preserves_array_stroke_point_aliases_and_independent_reads() -> None:
    proof = {'values': [1, 2]}
    point: dict[str, object] = {'index': 1, 'proof': proof}
    with OwnedEvidenceRecords(cache_bytes=0) as store, producer_record_scope(store) as scope:
        points = PointVersion([point, point]).seal()
        record_point_version(points, scope)
        history = OwnedRecordPreparedHistory(store)
        prepared = _prepared(points)
        payload = history.capture(prepared)
        assert payload is not None
        first = history.restore(payload)
        source = _rows(first['source_strokes'])
        assert first['source_strokes'] is first['candidate_strokes'] is first['structure_strokes']
        assert source[0] is _rows(first['strokes'])[0]
        restored = _rows(source[0]['points'])
        assert restored[0] is restored[1]
        assert type(restored) is list
        _mapping(restored[0]['proof'])['values'] = ['mutated']
        second = history.restore(payload)
        assert _rows(_rows(second['source_strokes'])[0]['points'])[0] == point
        assert second['source_strokes'] is not first['source_strokes']


def test_distinct_arrays_with_same_sequence_root_stay_distinct_but_share_their_points() -> None:
    point: dict[str, object] = {'proof': [1]}
    with OwnedEvidenceRecords() as store, producer_record_scope(store) as scope:
        first = PointVersion([point]).seal()
        record_point_version(first, scope)
        second = PointVersion(previous=first, start=1).seal()
        record_point_version(second, scope)
        assert first is not second and first.evidence_sequence is second.evidence_sequence
        prepared = _prepared(first)
        prepared['candidate_strokes'] = [{'id': 'other', 'points': second}]
        history = OwnedRecordPreparedHistory(store)
        payload = history.capture(prepared)
        assert payload is not None
        restored = history.restore(payload)
        left = _rows(_rows(restored['source_strokes'])[0]['points'])
        right = _rows(_rows(restored['candidate_strokes'])[0]['points'])
        assert left is not right
        assert left[0] is right[0]


def test_explicit_point_carrier_preserves_aliases_outside_point_arrays() -> None:
    point: dict[str, object] = {'index': 1, 'proof': {'values': [2]}}
    with OwnedEvidenceRecords() as store, producer_record_scope(store) as scope:
        points = PointVersion([point]).seal()
        record_point_version(points, scope)
        separate = dict(point)
        scope.record_point(separate)
        prepared = _prepared(points)
        prepared['developing_strokes'] = [{'id': 'tail', 'points': points,
                                          'confirmed_anchor': point, 'separate_anchor': separate}]
        history = OwnedRecordPreparedHistory(store)
        assert history.capture(prepared) is None
        assert history.last_rejection == 'prepared_point_without_record'
        payload = history.capture(prepared, point_lookup=scope.lookup)
        assert payload is not None
        restored = history.restore(payload)
        tail = _rows(restored['developing_strokes'])[0]
        anchor = _rows(tail['points'])[0]
        assert tail['confirmed_anchor'] is anchor
        assert tail['separate_anchor'] is not anchor
        assert _mapping(tail['separate_anchor'])['proof'] is anchor['proof']


def test_old_rows_and_truncated_versions_keep_their_original_visible_dates() -> None:
    with OwnedEvidenceRecords() as store, producer_record_scope(store) as scope:
        first = PointVersion([{'known': 1}]).seal()
        record_point_version(first, scope)
        later = PointVersion([{'known': 2}], previous=first, start=1).seal()
        record_point_version(later, scope)
        truncated = PointVersion(previous=later, start=1).seal()
        record_point_version(truncated, scope)
        history = OwnedRecordPreparedHistory(store)
        payloads = [history.capture(_prepared(version)) for version in (first, later, truncated)]
        assert all(payload is not None for payload in payloads)
        results = [history.restore(cast(bytes, payload)) for payload in payloads]
        assert [_rows(_rows(row['strokes'])[0]['points']) for row in results] == [
            [{'known': 1}], [{'known': 1}, {'known': 2}], [{'known': 1}],
        ]


def test_recorded_history_does_not_iterate_points_or_depend_on_old_pool_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    with owned_evidence_scope(memory_bytes=0) as evidence:
        store = evidence.record_store()
        with producer_record_scope(store) as scope:
            values: list[dict[str, object]] = [{'index': index, 'proof': [index]} for index in range(200)]
            points = CertifiedSource(values, [str(index).encode() for index in range(200)], evidence, None)
            record_point_version(points, scope)
        assert evidence.admission_remaining == 0

        def forbidden(self: PointVersion) -> Never:
            raise AssertionError('history must not enumerate recorded points')

        monkeypatch.setattr(PointVersion, '__iter__', forbidden)
        history = OwnedRecordPreparedHistory(store)
        payload = history.capture(_prepared(points))
        assert payload is not None
        store.clear_cache()
        restored = history.restore(payload)
        assert _rows(_rows(restored['strokes'])[0]['points']) == values


def test_capture_record_count_depends_on_shell_shape_not_historical_point_count() -> None:
    with OwnedEvidenceRecords(cache_bytes=0) as store, producer_record_scope(store) as scope:
        small = PointVersion([{'index': 0}]).seal()
        record_point_version(small, scope)
        large = PointVersion([{'index': index} for index in range(1, 200)], previous=small, start=1).seal()
        record_point_version(large, scope)
        history = OwnedRecordPreparedHistory(store)
        before = store.record_count
        assert history.capture(_prepared(small)) is not None
        small_records = store.record_count - before
        store.clear_cache()
        before = store.record_count
        assert history.capture(_prepared(large)) is not None
        assert store.record_count - before == small_records


def test_plain_missing_and_foreign_point_versions_are_rejected_before_any_write() -> None:
    with OwnedEvidenceRecords() as store, OwnedEvidenceRecords() as foreign:
        with producer_record_scope(foreign) as scope:
            foreign_points = PointVersion([{'proof': [1]}]).seal()
            record_point_version(foreign_points, scope)
        history = OwnedRecordPreparedHistory(store)
        before = store.record_count
        for points in ([{'proof': [1]}], PointVersion([{'proof': [1]}]).seal(), foreign_points):
            assert history.capture(_prepared(points)) is None
            assert store.record_count == before


def test_unknown_metadata_does_not_run_hooks_or_trigger_full_graph_capture() -> None:
    class Audit:
        def __reduce_ex__(self, protocol: int) -> Never:
            raise AssertionError('history must not call serialization hooks')

        def __sizeof__(self) -> Never:
            raise AssertionError('history must not size unknown values')

    with OwnedEvidenceRecords() as store, producer_record_scope(store) as scope:
        points = PointVersion([{'index': 1}]).seal()
        record_point_version(points, scope)
        history = OwnedRecordPreparedHistory(store)
        before = store.record_count
        row = _prepared(points)
        row['audit'] = {'nested': Audit()}
        assert history.capture(row) is None
        assert history.last_rejection == 'prepared_point_without_record'
        assert store.record_count == before
        row['audit'] = Audit()
        assert history.capture(row) is None
        assert store.record_count == before


def test_payloads_are_request_scoped_and_close_invalidates_the_adapter() -> None:
    with OwnedEvidenceRecords() as store, OwnedEvidenceRecords() as foreign, producer_record_scope(store) as scope:
        points = PointVersion([{'index': 1}]).seal()
        record_point_version(points, scope)
        history = OwnedRecordPreparedHistory(store)
        payload = history.capture(_prepared(points))
        assert payload is not None and history.is_payload(payload)
        with pytest.raises(ValueError, match='this request|its request'):
            OwnedRecordPreparedHistory(foreign).restore(payload)
        with pytest.raises(ValueError, match='invalid'):
            history.restore(payload + b'extra')
        history.close()
        with pytest.raises(ValueError, match='closed'):
            history.restore(payload)
        sequence = points.evidence_sequence
        assert sequence is not None
        assert store.materialize(sequence.reference) == [{'index': 1}]


def test_shell_limit_is_an_explicit_rejection_without_a_full_pickle_fallback() -> None:
    with OwnedEvidenceRecords() as store, producer_record_scope(store) as scope:
        points = PointVersion([{'index': 1}]).seal()
        record_point_version(points, scope)
        before = store.record_count
        history = OwnedRecordPreparedHistory(store, memory_bytes=0)
        assert history.capture(_prepared(points)) is None
        assert history.last_rejection == 'prepared_shell_memory_limit'
        assert store.record_count == before


def test_experimental_closed_paths_keep_whole_row_aliases_through_load_and_decode() -> None:
    with owned_evidence_scope(memory_bytes=0) as evidence:
        evidence.record_history_enabled = True
        store = evidence.record_store()
        with producer_record_scope(store) as scope:
            points = PointVersion([{'index': 1}]).seal()
            record_point_version(points, scope)
        with _ClosedPreparedPaths(_owned_history=True) as paths:
            assert paths._point_history is None
            encoded = paths.encode(_prepared(points))
            payload = _prepared_payload(encoded)
            assert payload is not None and OwnedRecordPreparedHistory.is_payload(payload)
            loaded = paths.load_encoded(payload)
            restored = paths.decode(loaded)
            assert restored is loaded
            assert restored['source_strokes'] is restored['candidate_strokes'] is restored['structure_strokes']
            assert paths._graph_history is None


def test_experimental_closed_paths_reject_missing_roots_without_legacy_fallback() -> None:
    with owned_evidence_scope(memory_bytes=0) as evidence:
        evidence.record_history_enabled = True
        with _ClosedPreparedPaths(_owned_history=True) as paths:
            with pytest.raises(ValueError, match='prepared_points_without_version'):
                paths.encode(_prepared([{'index': 1}]))
            assert paths._graph_history is None
            assert paths._point_history is None


def test_closed_paths_keep_explicit_carriers_until_capture_and_clear_them_afterward() -> None:
    with owned_evidence_scope(memory_bytes=0) as evidence:
        evidence.record_history_enabled = True
        store = evidence.record_store()
        with producer_record_scope(store) as scope:
            point: dict[str, object] = {'index': 1, 'proof': [2]}
            points = PointVersion([point]).seal()
            record_point_version(points, scope)
            carrier = scope.lookup(point)
            assert carrier is not None
        with _ClosedPreparedPaths(_owned_history=True) as paths:
            prepared = _prepared(points)
            prepared['developing_strokes'] = [{'id': 'tail', 'points': points, 'confirmed_anchor': point}]
            paths.begin_row()
            paths.trust_point_record(carrier)
            payload = _prepared_payload(paths.encode(prepared))
            assert payload is not None
            assert paths._record_points == {}
            restored = paths.decode(paths.load_encoded(payload))
            tail = _rows(restored['developing_strokes'])[0]
            assert tail['confirmed_anchor'] is _rows(tail['points'])[0]
            paths.trust_point_record(carrier)
            with pytest.raises(ValueError, match='prepared_points_without_version'):
                paths.encode(_prepared([point]))
            assert paths._record_points == {}


def test_recorded_history_requires_both_private_history_and_experiment_flag() -> None:
    with owned_evidence_scope() as evidence:
        evidence.record_history_enabled = False
        with _ClosedPreparedPaths(_owned_history=True) as legacy:
            assert legacy._record_history is None
        evidence.record_history_enabled = True
        with _ClosedPreparedPaths(_owned_history=False) as public:
            assert public._record_history is None


def test_closed_path_descriptors_restore_versions_without_merging_distinct_arrays() -> None:
    point: dict[str, object] = {'index': 1, 'proof': {'items': [2]}}
    with OwnedEvidenceRecords() as store, producer_record_scope(store) as scope:
        first = PointVersion([point]).seal()
        record_point_version(first, scope)
        second = PointVersion(previous=first, start=1).seal()
        record_point_version(second, scope)
        source: dict[str, object] = {'id': 'source', 'points': first}
        candidate: dict[str, object] = {'id': 'candidate', 'points': second}
        tail: dict[str, object] = {'id': 'tail', 'points': first, 'confirmed_anchor': point}
        history = OwnedRecordPreparedHistory(store)
        saved = history.capture_path(source, candidate, candidate, tail, point_lookup=scope.lookup)
        assert saved is not None
        restored, reading = history.restore_path(saved)
        assert reading.read_domain is not None
        assert history.attach_path_versions(restored, reading) > 0
        left = _rows(restored['source_strokes'])[0]['points']
        right = _rows(restored['candidate_strokes'])[0]['points']
        assert type(left) is PointVersion and type(right) is PointVersion
        assert left is not right
        assert cast(PointVersion, left)[0] is cast(PointVersion, right)[0]
        assert cast(PointVersion, left).record_store is store
        assert cast(PointVersion, left).evidence_sequence != first.evidence_sequence
        restored_tail = _rows(restored['developing_strokes'])[0]
        assert restored_tail['points'] is left
        assert restored_tail['confirmed_anchor'] is cast(PointVersion, left)[0]
        carriers = history.path_point_records(restored, reading)
        assert len(carriers) == 1 and carriers[0].value is restored_tail['confirmed_anchor']
        assert _rows(restored['candidate_strokes'])[0] is _rows(restored['strokes'])[0]


def test_closed_path_put_waits_for_final_seal_and_borrow_reuses_recorded_versions() -> None:
    with owned_evidence_scope(memory_bytes=0) as evidence:
        evidence.record_history_enabled = True
        store = evidence.record_store()
        source_values: list[dict[str, object]] = [{'index': 1, 'proof': [2]}]
        source_points = PointVersion(source_values)
        points = PointVersion([{'index': 2, 'proof': [3]}])
        source: dict[str, object] = {'id': 'source', 'points': source_points}
        candidate: dict[str, object] = {'id': 'candidate', 'points': points}
        tail: dict[str, object] = {'id': 'tail', 'points': points, 'confirmed_anchor': points[0]}
        with _ClosedPreparedPaths(_owned_history=True) as paths:
            paths.put(1, 'path', source_values, 4, source, candidate, candidate, tail)
            assert paths.get(1, 'path', source_values, 4) is None
            assert paths._record_pending_paths
            with producer_record_scope(store) as scope:
                source_points.seal()
                points.seal()
                record_point_version(source_points, scope)
                record_point_version(points, scope)
                carrier = scope.lookup(points[0])
                assert carrier is not None
                paths.trust_point_record(carrier)
                paths.finish_recorded_paths()
            assert paths._record_pending_paths == {}
            assert paths._record_pending_bytes == 0
            saved = paths.get(1, 'path', source_values, 4)
            assert saved is not None
            paths.begin_row()
            restored = paths.restore(saved, borrow=True)
            again = paths.restore(saved, borrow=True)
            fresh = paths.restore(saved, borrow=False)
            assert restored[0] is again[0]
            assert restored[0] is not fresh[0]
            assert type(restored[0]['points']) is PointVersion
            assert restored[1] is restored[2]
            assert paths._decoded.memory_used <= paths._decoded._limit
            assert paths._memory_used <= paths._memory_bytes
            assert restored[3] is not None
            anchor = restored[3]['confirmed_anchor']
            assert paths._lookup_point_record(anchor) is not None


def test_closed_template_budget_rejects_retention_but_keeps_current_record_roots() -> None:
    with owned_evidence_scope(memory_bytes=0) as evidence:
        evidence.record_history_enabled = True
        store = evidence.record_store()
        values: list[dict[str, object]] = [{'index': 1}]
        with producer_record_scope(store) as scope:
            points = PointVersion(values).seal()
            record_point_version(points, scope)
        source: dict[str, object] = {'id': 'source', 'points': points}
        candidate: dict[str, object] = {'id': 'candidate', 'points': points}
        with _ClosedPreparedPaths(_owned_history=True, memory_bytes=64 * 1024) as paths:
            for index in range(12):
                paths.put(1, str(index), values, 4, source, candidate, candidate, None)
            paths.finish_recorded_paths()
            assert 0 < len(paths._record_saved) < 12
            assert paths._memory_used <= paths._memory_bytes
            assert points.evidence_sequence is not None
            encoded = _prepared_payload(paths.encode(_prepared(points)))
            assert encoded is not None and OwnedRecordPreparedHistory.is_payload(encoded)
            assert paths._graph_history is None


@pytest.mark.parametrize('separate_template', (False, True))
def test_independent_closed_path_reads_stay_distinct_when_saved_together(separate_template: bool) -> None:
    shared = {'items': [1, 2]}
    point: dict[str, object] = {'index': 1, 'proof': shared, 'again': shared}
    equal_point: dict[str, object] = {'index': 1, 'proof': {'items': [1, 2]}}
    with OwnedEvidenceRecords(cache_bytes=0) as store, producer_record_scope(store) as scope:
        first = PointVersion([point, point, equal_point]).seal()
        record_point_version(first, scope)
        second = PointVersion(previous=first, start=len(first)).seal()
        record_point_version(second, scope)
        source: dict[str, object] = {'id': 'source', 'points': first}
        candidate: dict[str, object] = {'id': 'candidate', 'points': second}
        tail: dict[str, object] = {'id': 'tail', 'points': first, 'confirmed_anchor': point}
        history = OwnedRecordPreparedHistory(store)
        saved = history.capture_path(source, candidate, candidate, tail, point_lookup=scope.lookup)
        assert saved is not None
        other_saved = (history.capture_path(source, candidate, candidate, tail, point_lookup=scope.lookup)
                       if separate_template else saved)
        assert other_saved is not None
        left, left_read = history.restore_path(saved)
        right, right_read = history.restore_path(other_saved)
        assert left_read.read_domain != right_read.read_domain
        history.attach_path_versions(left, left_read)
        history.attach_path_versions(right, right_read)
        records = (*history.path_point_records(left, left_read), *history.path_point_records(right, right_read))
        by_identity = {id(record.value): record for record in records}
        combined = {field: [*_rows(left[field]), *_rows(right[field])] for field in (
            'source_strokes', 'candidate_strokes', 'structure_strokes', 'strokes', 'developing_strokes')}
        payload = history.capture(combined, point_lookup=lambda value: by_identity.get(id(value)))
        assert payload is not None
        final = history.restore(payload)
        a = _rows(_rows(final['source_strokes'])[0]['points'])
        b = _rows(_rows(final['source_strokes'])[1]['points'])
        assert a is not b and a[0] is not b[0] and a[0]['proof'] is not b[0]['proof']
        assert a[0] is a[1] and b[0] is b[1]
        assert a[0]['proof'] is a[0]['again'] and b[0]['proof'] is b[0]['again']
        assert a[0]['proof'] == a[2]['proof'] and a[0]['proof'] is not a[2]['proof']
        for index, points in enumerate((a, b)):
            other = _rows(_rows(final['candidate_strokes'])[index]['points'])
            assert other is not points and other[0] is points[0]
            tail_row = _rows(final['developing_strokes'])[index]
            assert tail_row['points'] is points and tail_row['confirmed_anchor'] is points[0]
        _mapping(a[0]['proof'])['items'] = ['changed']
        assert _mapping(b[0]['proof'])['items'] == [1, 2]


def test_original_descriptor_cannot_rebind_an_independent_read_to_producer_identity() -> None:
    with OwnedEvidenceRecords(cache_bytes=0) as store, producer_record_scope(store) as scope:
        point: dict[str, object] = {'proof': [1]}
        points = PointVersion([point]).seal()
        record_point_version(points, scope)
        shell: dict[str, object] = {'id': 'path', 'points': points, 'confirmed_anchor': point}
        history = OwnedRecordPreparedHistory(store)
        saved = history.capture_path(shell, shell, shell, shell, point_lookup=scope.lookup)
        assert saved is not None
        unscoped = history.restore(saved.payload)
        with pytest.raises(ValueError, match='independent read domain'):
            history.attach_path_versions(unscoped, saved)
        with pytest.raises(ValueError, match='independent read domain'):
            history.path_point_records(unscoped, saved)


def test_borrowed_and_independent_closed_path_reads_keep_their_domains_when_encoded() -> None:
    with owned_evidence_scope(memory_bytes=0) as evidence:
        evidence.record_history_enabled = True
        store = evidence.record_store()
        values: list[dict[str, object]] = [{'index': 1, 'proof': {'items': [2]}}]
        with producer_record_scope(store) as scope:
            points = PointVersion(values).seal()
            record_point_version(points, scope)
            carrier = scope.lookup(values[0])
            assert carrier is not None
        source: dict[str, object] = {'id': 'source', 'points': points}
        candidate: dict[str, object] = {'id': 'candidate', 'points': points}
        tail: dict[str, object] = {'id': 'tail', 'points': points, 'confirmed_anchor': values[0]}
        with _ClosedPreparedPaths(_owned_history=True) as paths:
            paths.put(1, 'path', values, 4, source, candidate, candidate, tail)
            paths.trust_point_record(carrier)
            paths.finish_recorded_paths()
            saved = paths.get(1, 'path', values, 4)
            assert saved is not None
            paths.begin_row()
            borrowed = paths.restore(saved, borrow=True)
            same = paths.restore(saved, borrow=True)
            fresh = paths.restore(saved)
            row: dict[str, object] = dict(
                source_strokes=[borrowed[0], same[0], fresh[0]],
                candidate_strokes=[borrowed[1], same[1], fresh[1]],
                structure_strokes=[borrowed[1], same[1], fresh[1]],
                strokes=[borrowed[2], same[2], fresh[2]],
                developing_strokes=[borrowed[3], same[3], fresh[3]],
            )
            payload = _prepared_payload(paths.encode(row))
            assert payload is not None
            final = paths.decode(paths.load_encoded(payload))
            sources = _rows(final['source_strokes'])
            assert sources[0] is sources[1] and sources[0] is not sources[2]
            a, b = _rows(sources[0]['points']), _rows(sources[2]['points'])
            assert a[0] is not b[0] and a[0]['proof'] is not b[0]['proof']
            tails = _rows(final['developing_strokes'])
            assert tails[0]['confirmed_anchor'] is a[0]
            assert tails[1]['confirmed_anchor'] is a[0]
            assert tails[2]['confirmed_anchor'] is b[0]
            assert paths._decoded.memory_used <= paths._decoded._limit
