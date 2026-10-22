"""私有写者一次完整引用校验，公共提交边界与失败语义保持严格。"""

from typing import cast
from unittest.mock import patch

import pytest

from wavequant.domain.market_structure.owned_evidence_records import OwnedEvidenceRecords, RecordRef


def test_writer_reuses_committed_reference_without_a_second_public_validation() -> None:
    with OwnedEvidenceRecords(cache_bytes=0) as store:
        old = store.capture_new({'proof': [1]})
        with store._new_graph_writer(memory_bytes=4096) as writer:
            with patch.object(store, '_validate_ref', side_effect=AssertionError('reference was checked twice')):
                root = writer.write_list([old, old])
        restored = cast(list[object], store.materialize(root))
        assert restored == [{'proof': [1]}, {'proof': [1]}]
        assert restored[0] is restored[1]


@pytest.mark.parametrize('invalid', ('unknown', 'foreign', 'namespace_type', 'negative', 'misaligned',
                                   'bool_offset', 'offset_subclass', 'future'))
def test_writer_first_validation_still_rejects_invalid_references(invalid: str) -> None:
    class Offset(int):
        pass

    with OwnedEvidenceRecords(cache_bytes=0) as store, OwnedEvidenceRecords(cache_bytes=0) as foreign:
        old = store.write_scalar('old')
        other = foreign.write_scalar('other')
        count, size = store.record_count, store.stored_bytes
        bad: object
        if invalid == 'unknown':
            bad = object()
        elif invalid == 'foreign':
            bad = other
        elif invalid == 'namespace_type':
            bad = RecordRef(cast(bytes, bytearray(store.namespace)), old.offset)
        elif invalid == 'negative':
            bad = RecordRef(store.namespace, -24)
        elif invalid == 'misaligned':
            bad = RecordRef(store.namespace, old.offset + 1)
        elif invalid == 'bool_offset':
            bad = RecordRef(store.namespace, cast(int, False))
        elif invalid == 'offset_subclass':
            bad = RecordRef(store.namespace, Offset(old.offset))
        else:
            bad = RecordRef(store.namespace, store._index_end)
        with pytest.raises(ValueError, match='earlier record in its request'):
            with store._new_graph_writer(memory_bytes=4096) as writer:
                writer.write_list([cast(RecordRef, bad)])
        assert (store.record_count, store.stored_bytes) == (count, size)
        assert store.materialize(old) == 'old'


def test_new_batch_references_remain_private_until_commit_even_after_flush() -> None:
    with OwnedEvidenceRecords(cache_bytes=0) as store:
        old = store.write_scalar('old')
        with store._new_graph_writer(memory_bytes=4096) as writer:
            new = writer.write_list([old])
            writer._flush()
            with pytest.raises(ValueError, match='committed'):
                store._validate_ref(new)
            with pytest.raises(ValueError, match='committed'):
                store.materialize(new)
            root = writer.write_list([new, new])
        result = cast(list[object], store.materialize(root))
        assert result == [['old'], ['old']] and result[0] is result[1]
        with pytest.raises(ValueError, match='active'):
            writer.write_list([old])


def test_rolled_back_reference_is_rejected_before_its_offset_is_reallocated() -> None:
    with OwnedEvidenceRecords(cache_bytes=0) as store:
        old = store.write_scalar('old')
        count, size = store.record_count, store.stored_bytes
        with pytest.raises(RuntimeError, match='abort'):
            with store._new_graph_writer(memory_bytes=4096) as abandoned:
                revoked = abandoned.write_list([old])
                abandoned._flush()
                raise RuntimeError('abort')
        assert (store.record_count, store.stored_bytes) == (count, size)
        with pytest.raises(ValueError, match='earlier record in its request'):
            with store._new_graph_writer(memory_bytes=4096) as writer:
                writer.write_list([revoked])
        assert (store.record_count, store.stored_bytes) == (count, size)
        assert store.materialize(old) == 'old'


def test_owner_closed_during_batch_is_rejected_before_reference_lookup() -> None:
    store = OwnedEvidenceRecords(cache_bytes=0)
    try:
        old = store.write_scalar('old')
        with store._new_graph_writer(memory_bytes=4096) as writer:
            # 只模拟存活标志；不破坏流句柄，退出批次后仍走正常清理。
            with patch.object(store, '_closed', True):
                with pytest.raises(ValueError, match='closed'):
                    writer.write_list([old])
        store.close()
        with pytest.raises(ValueError, match='closed'):
            store.write_list([old])
    finally:
        store.close()
