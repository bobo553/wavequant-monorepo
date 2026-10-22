"""点版本的惰性并行记录、变化尾部写入和历史别名边界。"""

from copy import deepcopy
import gc
from unittest.mock import patch
from weakref import ref

import pytest

from wavequant.domain.market_structure.owned_evidence import CertifiedSource, OwnedEvidence
from wavequant.domain.market_structure.owned_evidence_producer import producer_record_scope
from wavequant.domain.market_structure.owned_evidence_records import OwnedEvidenceRecords
from wavequant.domain.market_structure.trend_point_records import PointRecordView
from wavequant.domain.market_structure.trend_point_version import PointVersion, record_point_version


def _points(count: int):
    proof = {'origin': {'index': 0}, 'steps': [1, 2, 3]}
    return [dict(index=index, ordinal=0, kind='H' if index % 2 == 0 else 'L', proof=proof)
            for index in range(count)]


def test_append_records_only_new_points_and_preserves_shared_proofs_across_blocks():
    with OwnedEvidenceRecords(cache_bytes=0) as store:
        first = PointVersion(_points(65)).seal()
        with producer_record_scope(store) as scope:
            old_root = record_point_version(first, scope)
        assert old_root is not None
        original = store.materialize(old_root.reference)
        new_point = dict(index=65, ordinal=0, kind='L', proof=first[64]['proof'])
        extended = PointVersion([new_point], previous=first, start=len(first)).seal()
        assert len(extended.point_records) == len(first)
        assert extended.evidence_sequence is None
        visited = []
        with producer_record_scope(store, inputs=(first.point_records[64],)) as scope:
            capture = scope.ensure_record

            def record(point):
                visited.append(point)
                return capture(point)

            with patch.object(scope, 'ensure_record', side_effect=record):
                root = record_point_version(extended, scope)
        assert visited == [new_point]
        assert root is not None and extended.record_store is store
        actual = store.materialize(root.reference)
        assert actual == list(extended)
        assert actual[0]['proof'] is actual[32]['proof'] is actual[64]['proof'] is actual[65]['proof']
        assert store.materialize(old_root.reference) == original
        assert all(type(point) is dict for point in extended)
        assert set(extended[-1]) == {'index', 'ordinal', 'kind', 'proof'}


@pytest.mark.parametrize('operation', ('replace', 'insert', 'remove_tail', 'clear', 'slice', 'empty_slice'))
def test_mutation_before_final_seal_rewrites_the_exact_uncertified_suffix(operation):
    with OwnedEvidenceRecords(cache_bytes=0) as store:
        first = PointVersion(_points(40)).seal()
        with producer_record_scope(store) as scope:
            old = record_point_version(first, scope)
        changed = PointVersion(previous=first, start=len(first))
        replacement = {'index': 900, 'ordinal': 0, 'kind': 'H'}
        if operation == 'replace':
            changed[7] = replacement
            start = 7
        elif operation == 'insert':
            changed.insert(7, replacement)
            start = 7
        elif operation == 'remove_tail':
            changed.pop()
            start = len(changed)
        elif operation == 'clear':
            changed.clear()
            start = 0
        elif operation == 'empty_slice':
            changed[7:7] = [replacement]
            start = 7
        else:
            changed[7:10] = [replacement]
            start = 7
        assert changed.point_records == ()
        changed.seal()
        visited = []
        with producer_record_scope(store) as scope:
            capture = scope.ensure_record

            def record(point):
                visited.append(point)
                return capture(point)

            with patch.object(scope, 'ensure_record', side_effect=record):
                root = record_point_version(changed, scope)
        assert visited == list(changed.iter_range(start))
        assert root is not None and store.materialize(root.reference) == list(changed)
        assert old is not None and store.materialize(old.reference) == list(first)


def test_lazy_view_reads_only_selected_roots_and_never_materializes_proof_children():
    with OwnedEvidenceRecords(cache_bytes=0) as store:
        version = PointVersion(_points(97)).seal()
        with producer_record_scope(store) as scope:
            record_point_version(version, scope)
        with patch.object(store, 'materialize', side_effect=AssertionError('view materialized evidence')):
            view = version.point_records
            assert isinstance(view, PointRecordView)
            assert len(view) == 97
            record = view[65]
            assert record.value is version[65] and record.matches(version[65])
            assert [point.value for point in view.iter_range(31, 34)] == list(version.iter_range(31, 34))
            assert view[-1].value is version[-1]
            assert [point.value for point in view[94:]] == list(version.iter_range(94))
            assert view[20:10] == () and tuple(view.iter_range(20, 10)) == ()
        with pytest.raises(IndexError):
            view[97]
        with pytest.raises(IndexError):
            view[-98]


def test_versions_do_not_hold_an_ancestor_chain_or_per_point_record_table():
    with OwnedEvidenceRecords(cache_bytes=0) as store:
        first = PointVersion(_points(32)).seal()
        with producer_record_scope(store) as scope:
            record_point_version(first, scope)
        first_ref = ref(first)
        small_metadata = first.record_metadata_bytes
        second = PointVersion(_points(65), previous=first, start=len(first)).seal()
        with producer_record_scope(store) as scope:
            record_point_version(second, scope)
        del first
        gc.collect()
        assert first_ref() is None
        assert second.record_metadata_bytes <= small_metadata + 128
        assert second.record_metadata_bytes < 2048
        assert len(second.point_records) == 97
        # 载体只由此次读取者保留；读完不改变版本的常量元数据占用。
        before = second.record_metadata_bytes
        tuple(second.point_records)
        assert second.record_metadata_bytes == before


def test_truncation_changes_only_visible_root_without_recapturing_points():
    with OwnedEvidenceRecords(cache_bytes=0) as store:
        first = PointVersion(_points(64)).seal()
        with producer_record_scope(store) as scope:
            old = record_point_version(first, scope)
        short = PointVersion(previous=first, start=31).seal()
        with producer_record_scope(store) as scope, patch.object(
            scope, 'ensure_record', side_effect=AssertionError('truncation captured old points'),
        ):
            root = record_point_version(short, scope)
        assert old is not None and root is not None
        assert store.materialize(root.reference) == list(first.iter_range(0, 31))
        assert len(store.materialize(old.reference)) == 64
        assert len(short.point_records) == 31


def test_cross_store_and_unsealed_inputs_are_explicit_rejections():
    with OwnedEvidenceRecords(cache_bytes=0) as first_store, OwnedEvidenceRecords(cache_bytes=0) as other_store:
        version = PointVersion(_points(2)).seal()
        with producer_record_scope(first_store) as scope:
            record_point_version(version, scope)
        with producer_record_scope(other_store) as scope:
            with pytest.raises(ValueError, match='cross request stores'):
                record_point_version(version, scope)
        with producer_record_scope(first_store) as scope:
            with pytest.raises(ValueError, match='finally sealed'):
                record_point_version(PointVersion(_points(1)), scope)


def test_scope_budget_failure_never_falls_back_to_full_history_encoding():
    with OwnedEvidenceRecords(cache_bytes=0) as store:
        first = PointVersion(_points(1)).seal()
        with producer_record_scope(store) as scope:
            old = record_point_version(first, scope)
        changed = PointVersion([{'index': 1, 'proof': ['new']}], previous=first, start=1).seal()
        with producer_record_scope(store, memory_bytes=1) as scope:
            with pytest.raises(ValueError, match='scoped memory limit'):
                record_point_version(changed, scope)
        assert changed.evidence_sequence is None
        assert len(changed.point_records) == 1
        assert old is not None and store.materialize(old.reference) == list(first)


def test_cloned_field_uses_its_copy_domain_but_a_cloned_dictionary_root_is_rejected():
    with OwnedEvidenceRecords(cache_bytes=0) as store:
        first = PointVersion(_points(1)).seal()
        with producer_record_scope(store) as scope:
            record_point_version(first, scope)
        record = first.point_records[0]
        cloned = deepcopy(first[0]['proof'])
        next_point = {'index': 1, 'proof': cloned}
        extended = PointVersion([next_point], previous=first, start=1).seal()
        with producer_record_scope(store, inputs=(record,)) as scope:
            reference = record.field_reference('proof')
            assert reference is not None
            scope.bind_clone(cloned, first[0]['proof'], reference)
            root = record_point_version(extended, scope)
        assert root is not None
        actual = store.materialize(root.reference)
        assert actual[0]['proof'] == actual[1]['proof'] and actual[0]['proof'] is not actual[1]['proof']
        copied_point = deepcopy(first[0])
        copied_version = PointVersion([copied_point]).seal()
        with producer_record_scope(store) as scope:
            scope.bind_clone(copied_point, first[0], record.reference)
            with pytest.raises(ValueError, match='dictionary record'):
                record_point_version(copied_version, scope)


def test_public_export_and_missing_scope_keep_plain_dictionary_behavior():
    version = PointVersion(_points(2)).seal()
    assert record_point_version(version) is None
    assert version.evidence_sequence is None and version.record_metadata_bytes == 0
    exported = deepcopy(version)
    assert type(exported) is list and all(type(point) is dict for point in exported)
    assert exported == list(version)


def test_empty_and_unchanged_versions_have_complete_constant_roots():
    with OwnedEvidenceRecords(cache_bytes=0) as store:
        version = PointVersion().seal()
        with producer_record_scope(store) as scope:
            sequence = record_point_version(version, scope)
        assert sequence is not None and store.materialize(sequence.reference) == []
        assert len(version.point_records) == 0
        with producer_record_scope(store) as scope, patch.object(
            scope, 'ensure_record', side_effect=AssertionError('unchanged points were captured'),
        ):
            count = store.record_count
            assert record_point_version(version, scope) is sequence
            assert store.record_count == count


def test_certified_source_inherits_the_same_parallel_record_prefix():
    owner = OwnedEvidence()
    try:
        with OwnedEvidenceRecords(cache_bytes=0) as store:
            first = CertifiedSource(_points(2), [b'a', b'b'], owner, None)
            with producer_record_scope(store) as scope:
                record_point_version(first, scope)
            extended = CertifiedSource.from_tail([{'index': 2}], [b'c'], owner, first, 2)
            with producer_record_scope(store) as scope:
                root = record_point_version(extended, scope)
            assert root is not None and store.materialize(root.reference) == list(extended)
            assert extended.point_records[0].value is first[0]
    finally:
        owner.close()
