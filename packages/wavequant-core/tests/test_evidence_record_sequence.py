"""持久证据序列的历史可见性、别名及变化路径复杂度。"""

from dataclasses import replace
from unittest.mock import patch

import pytest

from wavequant.domain.market_structure.evidence_record_sequence import (
    derive_evidence_sequence, domain_sequence, iter_point_refs, point_ref_at, validate_evidence_sequence,
)
from wavequant.domain.market_structure.owned_evidence_records import OwnedEvidenceRecords, _DecodedRecord


def test_sequence_revision_and_truncation_preserve_old_visible_versions() -> None:
    with OwnedEvidenceRecords(cache_bytes=0) as store:
        refs = [store.capture_new({'index': index}) for index in range(96)]
        first = derive_evidence_sequence(store, refs)
        short = derive_evidence_sequence(store, (), previous=first, start=33)
        changed = store.capture_new({'index': 900})
        next_version = derive_evidence_sequence(store, (changed,), previous=short, start=33)
        assert store.materialize(first.reference) == [{'index': index} for index in range(96)]
        assert store.materialize(short.reference) == [{'index': index} for index in range(33)]
        assert store.materialize(next_version.reference) == [
            *({'index': index} for index in range(33)), {'index': 900},
        ]


def test_one_row_preserves_aliases_across_fields_and_blocks() -> None:
    with OwnedEvidenceRecords(cache_bytes=0) as store:
        shared = store.capture_new({'proof': [1, 2]})
        sequence = derive_evidence_sequence(store, [shared] * 65)
        root = store.capture_new({'direct': shared, 'points': sequence.reference,
                                  'same_points': sequence.reference})
        first = store.materialize(root)
        second = store.materialize(root)
        assert isinstance(first, dict) and isinstance(second, dict)
        assert first['points'] is first['same_points']
        assert first['direct'] is first['points'][0] is first['points'][32] is first['points'][64]
        assert second['direct'] is second['points'][64]
        assert first['direct'] is not second['direct']
        first['direct']['proof'].append(3)
        assert second['direct']['proof'] == [1, 2]


def test_clone_of_vector_keeps_internal_aliases_and_separates_mutable_values() -> None:
    with OwnedEvidenceRecords(cache_bytes=0) as store:
        point = store.capture_new({'value': [4]})
        sequence = derive_evidence_sequence(store, [point] * 40)
        copied = store.clone(sequence.reference)
        root = store.capture_new([sequence.reference, copied, copied])
        result = store.materialize(root)
        assert isinstance(result, list)
        assert result[1] is result[2] and result[0] is not result[1]
        assert result[1][0] is result[1][39]
        assert result[0][0] is not result[1][0]


def test_domain_sequence_point_proxies_rejoin_vector_and_direct_field_aliases() -> None:
    with OwnedEvidenceRecords(cache_bytes=0) as store:
        point = store.capture_new({'proof': [1]})
        original = derive_evidence_sequence(store, [point] * 65)
        domain = store.new_read_domain()
        version = domain_sequence(store, original, domain)
        validate_evidence_sequence(store, version)
        first, last = point_ref_at(store, version, 0), point_ref_at(store, version, 64)
        assert first != last and store.same_reference(first, last)
        explicit = store.domain_ref(point, domain)
        assert store.same_reference(first, explicit)
        other = domain_sequence(store, original, store.new_read_domain())
        root = store.capture_new([version.reference, first, last, explicit, other.reference, original.reference])
        values = store.materialize(root)
        assert isinstance(values, list)
        assert values[0][0] is values[0][32] is values[0][64] is values[1] is values[2] is values[3]
        assert values[4][0] is values[4][64] and values[4][0] is not values[1]
        assert values[5][0] is not values[1]


def test_domain_sequence_truncation_hides_future_and_derives_without_unscoping_prefix() -> None:
    with OwnedEvidenceRecords(cache_bytes=0) as store:
        shared = store.capture_new({'proof': [1]})
        future = store.capture_new({'future': [99]})
        original = derive_evidence_sequence(store, [shared] * 33 + [future] * 63)
        short = derive_evidence_sequence(store, (), previous=original, start=33)
        version = domain_sequence(store, short, store.new_read_domain())
        reader = store._record

        def visible_only(offset: int) -> _DecodedRecord:
            if offset == future.offset:
                raise AssertionError('a truncated point was read')
            return reader(offset)

        with patch.object(store, '_record', visible_only):
            refs = tuple(iter_point_refs(store, version, start=31, stop=33))
            values = store.materialize(version.reference)
        assert len(refs) == 2 and store.same_reference(refs[0], refs[1])
        assert isinstance(values, list) and len(values) == 33 and values[0] is values[-1]
        with pytest.raises(IndexError):
            point_ref_at(store, version, 33)
        extended = derive_evidence_sequence(store, (refs[0],), previous=version, start=33)
        restored = store.materialize(extended.reference)
        assert isinstance(restored, list) and len(restored) == 34 and restored[0] is restored[-1]
        original_values = store.materialize(original.reference)
        assert isinstance(original_values, list) and len(original_values) == 96


def test_domain_sequence_rejects_other_domain_tree_even_when_layout_and_values_match() -> None:
    with OwnedEvidenceRecords(cache_bytes=0) as store:
        point = store.capture_new({'proof': [1]})
        original = derive_evidence_sequence(store, [point] * 40)
        first = domain_sequence(store, original, store.new_read_domain())
        second = domain_sequence(store, original, store.new_read_domain())
        with pytest.raises(ValueError, match='metadata must match'):
            validate_evidence_sequence(store, replace(first, root=second.root))
        with pytest.raises(ValueError, match='metadata must match'):
            validate_evidence_sequence(store, replace(first, root=original.root))
        layered = domain_sequence(store, first, store.new_read_domain())
        validate_evidence_sequence(store, layered)
        assert store.materialize(layered.reference) == [{'proof': [1]}] * 40


def test_domain_materialize_and_validation_are_pure_reads_even_after_tail_derivation() -> None:
    with OwnedEvidenceRecords(cache_bytes=0) as store:
        point = store.capture_new({'proof': [1]})
        original = derive_evidence_sequence(store, [point] * 96)
        scoped = domain_sequence(store, original, store.new_read_domain())
        short = derive_evidence_sequence(store, (), previous=scoped, start=33)
        alias = point_ref_at(store, short, 0)
        revised = derive_evidence_sequence(store, (alias,), previous=short, start=33)
        layered = domain_sequence(store, revised, store.new_read_domain())
        root = store.write_list((original.reference, scoped.reference, short.reference,
                                 revised.reference, layered.reference, store.clone(layered.reference)))
        before_count, before_size = store.record_count, store.stored_bytes
        for _ in range(2):
            for sequence in (original, scoped, short, revised, layered):
                validate_evidence_sequence(store, sequence)
            values = store.materialize(root)
            assert isinstance(values, list)
            assert [len(value) for value in values] == [96, 96, 33, 34, 34, 34]
            assert all(value[0] is value[-1] for value in values)
            assert values[1][0] is values[2][0] is values[3][0]
            assert values[3][0] is not values[4][0] and values[4][0] is not values[5][0]
            assert (store.record_count, store.stored_bytes) == (before_count, before_size)


@pytest.mark.parametrize('count', (40, 8192))
@pytest.mark.parametrize('depth', (1, 3))
def test_single_point_domain_proxy_writes_depend_on_domain_depth_only(count: int, depth: int) -> None:
    with OwnedEvidenceRecords(cache_bytes=0) as store:
        point = store.capture_new({'proof': [1]})
        sequence = derive_evidence_sequence(store, [point] * count)
        for _ in range(depth):
            sequence = domain_sequence(store, sequence, store.new_read_domain())
        before = store.record_count
        validate_evidence_sequence(store, sequence)
        assert tuple(iter_point_refs(store, sequence, start=31, stop=31)) == ()
        assert store.record_count == before
        last = point_ref_at(store, sequence, count - 1)
        assert store.record_count - before == depth
        after = store.record_count
        first = point_ref_at(store, sequence, 0)
        assert store.record_count - after == depth
        assert store.same_reference(first, last)


def test_append_writes_changed_leaf_and_paths_without_reencoding_old_points() -> None:
    with OwnedEvidenceRecords(cache_bytes=0) as store:
        point = store.capture_new({'value': [7]})
        first = derive_evidence_sequence(store, [point] * 8192)
        count = store.record_count
        extended = derive_evidence_sequence(store, [point], previous=first, start=first.length)
        assert store.record_count - count <= 14
        result = store.materialize(extended.reference)
        assert isinstance(result, list) and len(result) == 8193
        assert result[0] is result[-1]


def test_dictionary_fields_are_references_to_same_store_without_materializing_children() -> None:
    with OwnedEvidenceRecords(cache_bytes=0) as store:
        child = store.capture_new({'proof': [1, 2]})
        root = store.capture_new({'left': child, 'right': child})
        fields = dict(store.dictionary_items(root))
        assert fields == {'left': child, 'right': child}
        assert store.materialize(fields['left']) == {'proof': [1, 2]}


@pytest.mark.parametrize('capacity,length', [(0, 1), (3, 1), (1, 33), (2, 1)])
def test_prior_layout_is_validated_before_writing(capacity: int, length: int) -> None:
    with OwnedEvidenceRecords(cache_bytes=0) as store:
        point = store.capture_new({'index': 0})
        first = derive_evidence_sequence(store, (point,))
        forged = replace(first, capacity=capacity, length=length)
        count = store.record_count
        with pytest.raises(ValueError):
            derive_evidence_sequence(store, (point,), previous=forged, start=1)
        assert store.record_count == count
        assert store.materialize(first.reference) == [{'index': 0}]


def test_random_reference_read_skips_unchanged_point_values_and_outside_branches() -> None:
    with OwnedEvidenceRecords(cache_bytes=0) as store:
        point = store.capture_new({'proof': [8]})
        version = derive_evidence_sequence(store, [point] * 8192)
        read = store._record
        with patch.object(store, '_record', wraps=read) as reads, patch.object(
            store, 'materialize', side_effect=AssertionError('point values must remain opaque'),
        ):
            assert point_ref_at(store, version, 8191) == point
            assert reads.call_count <= 16
            assert list(iter_point_refs(store, version, start=31, stop=34)) == [point] * 3
        with pytest.raises(IndexError):
            point_ref_at(store, version, 8192)


def test_failed_vector_root_does_not_replace_old_version_and_retry_remains_valid() -> None:
    with OwnedEvidenceRecords(cache_bytes=0) as store:
        old = store.capture_new({'value': [1]})
        new = store.capture_new({'value': [2]})
        version = derive_evidence_sequence(store, [old] * 65)
        with patch.object(store, '_write_vector', side_effect=OSError('root publication failed')):
            with pytest.raises(OSError, match='root publication failed'):
                derive_evidence_sequence(store, [new], previous=version, start=64)
        assert store.materialize(version.reference) == [{'value': [1]}] * 65
        revised = derive_evidence_sequence(store, [new], previous=version, start=64)
        assert store.materialize(revised.reference) == [{'value': [1]}] * 64 + [{'value': [2]}]
