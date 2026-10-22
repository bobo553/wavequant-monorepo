"""已提交记录的有界读缓存、回滚偏移复用和原证据池预算划拨。"""

from typing import BinaryIO, Never, cast

import pytest

from wavequant.domain.market_structure.owned_evidence import owned_evidence_scope
from wavequant.domain.market_structure.owned_evidence_records import OwnedEvidenceRecords, _STR


class _ObservedStream:
    def __init__(self, stream: BinaryIO, *, fail_on_write: int | None = None) -> None:
        self.stream = stream
        self.reads = self.writes = 0
        self.fail_on_write = fail_on_write

    def read(self, size: int = -1) -> bytes:
        self.reads += 1
        return self.stream.read(size)

    def write(self, payload: bytes | memoryview) -> int | None:
        self.writes += 1
        if self.writes == self.fail_on_write:
            self.fail_on_write = None
            raise OSError('injected index write failure')
        return self.stream.write(payload)

    def seek(self, offset: int, whence: int = 0) -> int:
        return self.stream.seek(offset, whence)

    def truncate(self, size: int | None = None) -> int:
        return self.stream.truncate(size)

    def close(self) -> None:
        self.stream.close()


def test_committed_typed_records_do_not_read_back_just_written_keys_and_fields() -> None:
    with OwnedEvidenceRecords(cache_bytes=64 * 1024) as records:
        index, data = _ObservedStream(records._index), _ObservedStream(records._data)
        records._index, records._data = cast(BinaryIO, index), cast(BinaryIO, data)
        key = records.write_scalar('proof')
        value = records.write_list([records.write_scalar(1)])
        root = records.write_dict([(key, value)])
        assert records.dictionary_refs(root) == ((key, value),)
        assert records.materialize(root) == {'proof': [1]}
        assert records.materialize(root) == {'proof': [1]}
        assert index.reads == data.reads == 0
        assert 0 < records.cache_memory_used <= 64 * 1024
        records.clear_cache()
        assert records.materialize(root) == {'proof': [1]}
        cold_reads = index.reads, data.reads
        assert min(cold_reads) > 0
        assert records.materialize(root) == {'proof': [1]}
        assert (index.reads, data.reads) == cold_reads


def test_rollback_removes_prefilled_headers_and_values_before_offsets_are_reused() -> None:
    with OwnedEvidenceRecords(cache_bytes=64 * 1024) as records:
        old = records.write_scalar('committed')
        start = records._committed_index_end
        before_count, before_size = records.record_count, records.stored_bytes
        records._index = cast(BinaryIO, _ObservedStream(records._index, fail_on_write=2))
        with pytest.raises(OSError, match='injected index write failure'):
            records.capture_new(['first cached child', 'second failing child'])
        assert all(offset < start for offset in records._cache)
        assert (records.record_count, records.stored_bytes) == (before_count, before_size)
        assert records.materialize(old) == 'committed'
        new = records.write_scalar('replacement at the same offset')
        assert new.offset == start
        assert records.materialize(new) == 'replacement at the same offset'
        assert records.cache_memory_used <= 64 * 1024


def test_prefilled_uncommitted_records_are_not_readable_until_transaction_commit() -> None:
    with OwnedEvidenceRecords(cache_bytes=64 * 1024) as records:
        with records._transaction():
            reference = records._append(_STR, b'pending')
            assert reference.offset in records._cache
            with pytest.raises(ValueError, match='committed'):
                records._record_header(reference.offset)
            with pytest.raises(ValueError, match='committed'):
                records._record(reference.offset)
        assert records.materialize(reference) == 'pending'


def test_zero_cache_and_wide_records_do_not_predecode_for_rejected_cache_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_decode(offset: int, kind: int, payload: bytes) -> Never:
        raise AssertionError('cache prefill decoded a record that cannot be admitted')

    with OwnedEvidenceRecords(cache_bytes=0) as records:
        with monkeypatch.context() as patch:
            patch.setattr(records, '_decode_record', unexpected_decode)
            root = records.write_list([records.write_scalar('small')])
        assert records.cache_memory_used == 0
        assert records.materialize(root) == ['small']
    with OwnedEvidenceRecords(cache_bytes=1024) as records:
        with monkeypatch.context() as patch:
            patch.setattr(records, '_decode_record', unexpected_decode)
            wide = records.write_scalar('x' * 4096)
            root = records.write_list([wide] * 128)
        assert records.cache_memory_used <= 1024
        assert records.materialize(root) == ['x' * 4096] * 128
        assert records.cache_memory_used <= 1024


def test_header_and_decoded_entries_share_one_limit_through_eviction_and_clear() -> None:
    with OwnedEvidenceRecords(cache_bytes=2048) as records:
        oldest = records.capture_new({'proof': [1]})
        roots = [oldest]
        for number in range(64):
            roots.append(records.write_list([records.write_scalar(number), oldest]))
            assert records.cache_memory_used <= 2048
        before_count, before_size = records.record_count, records.stored_bytes
        for root in roots:
            records._record_header(root.offset)
            records.materialize(root)
            assert records.cache_memory_used <= 2048
        assert (records.record_count, records.stored_bytes) == (before_count, before_size)
        records.clear_cache()
        assert records.cache_memory_used == 0
        assert records.materialize(oldest) == {'proof': [1]}
        records.close()
        assert records.cache_memory_used == 0


def test_experiment_cache_reservation_stays_inside_the_original_owner_budget() -> None:
    budget = 2 * 1024 * 1024
    with owned_evidence_scope(memory_bytes=budget) as owner:
        old = 'owned before record cache ' * 8000
        owner.publish(old)
        original_used = owner.memory_used
        assert owner.reference(old) is not None
        owner.record_history_enabled = True
        records = owner.record_store()
        assert owner._record_cache_reserved == records._cache_limit + records._page_reserved == 1024 * 1024
        assert owner.memory_used == original_used + 1024 * 1024
        assert owner.memory_used + owner.admission_remaining == budget
        too_large = 'z' * owner.admission_remaining
        owner.publish(too_large)
        assert owner.reference(too_large) is None
        root = records.capture_new({'current': [1]})
        assert records.storage_memory_used <= owner._record_cache_reserved
        used, remaining = owner.memory_used, owner.admission_remaining
        records.clear_cache()
        assert records.cache_memory_used == 0
        assert records.storage_memory_used == records._page_reserved > 0
        assert (owner.memory_used, owner.admission_remaining) == (used, remaining)
        assert records.materialize(root) == {'current': [1]}
        assert owner.memory_used <= budget
        owner.close()
        assert owner.memory_used == owner._record_cache_reserved == records.cache_memory_used == 0
        with pytest.raises(ValueError, match='closed'):
            records.materialize(root)


@pytest.mark.parametrize('budget', (0, 4096))
def test_small_owner_reserves_only_remaining_capacity_and_zero_budget_stays_zero(budget: int) -> None:
    with owned_evidence_scope(memory_bytes=budget) as owner:
        old = {'keep': [1]}
        owner.publish(old)
        original_used, remaining = owner.memory_used, owner.admission_remaining
        owner.record_history_enabled = True
        records = owner.record_store()
        assert records._cache_limit == owner._record_cache_reserved == remaining
        assert owner.memory_used == original_used + remaining == budget
        assert owner.admission_remaining == 0
        assert records.materialize(records.capture_new({'current': [2]})) == {'current': [2]}
        assert records.cache_memory_used <= remaining
        if not budget:
            assert records.cache_memory_used == 0


def test_legacy_owner_does_not_reserve_a_record_cache() -> None:
    with owned_evidence_scope(memory_bytes=4096) as owner:
        records = owner.record_store()
        assert owner._record_cache_reserved == records._cache_limit == 0
        assert owner.memory_used == 0 and owner.admission_remaining == 4096
