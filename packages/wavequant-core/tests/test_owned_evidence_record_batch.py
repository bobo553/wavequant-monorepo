"""生产者新图的有界批写、未提交隔离和完整事务回滚。"""

from sys import getsizeof
from typing import BinaryIO, Never, cast

import pytest

from wavequant.domain.market_structure.owned_evidence_producer import producer_record_scope
from wavequant.domain.market_structure.owned_evidence_records import OwnedEvidenceRecords


class _BatchStream:
    def __init__(self, stream: BinaryIO, *, fail_write: int | None = None, fail_truncate: bool = False) -> None:
        self.stream = stream
        self.writes = self.reads = 0
        self.fail_write = fail_write
        self.fail_truncate = fail_truncate

    def write(self, payload: bytes | memoryview) -> int | None:
        self.writes += 1
        if self.writes == self.fail_write:
            self.fail_write = None
            raise OSError('injected batch write failure')
        return self.stream.write(payload)

    def read(self, size: int = -1) -> bytes:
        self.reads += 1
        return self.stream.read(size)

    def seek(self, offset: int, whence: int = 0) -> int:
        return self.stream.seek(offset, whence)

    def truncate(self, size: int | None = None) -> int:
        if self.fail_truncate:
            raise OSError('injected batch rollback failure')
        return self.stream.truncate(size)

    def close(self) -> None:
        self.stream.close()


def test_one_small_producer_graph_uses_one_write_to_each_stream() -> None:
    with OwnedEvidenceRecords(cache_bytes=64 * 1024) as store, producer_record_scope(store) as scope:
        data, index = _BatchStream(store._data), _BatchStream(store._index)
        store._data, store._index = cast(BinaryIO, data), cast(BinaryIO, index)
        shared = {'items': [1, 2]}
        point: dict[str, object] = {f'field-{number}': number for number in range(32)}
        point.update(first=shared, again=shared, distinct={'items': [1, 2]})
        record = scope.record_point(point)
        assert data.writes == index.writes == 1
        restored = cast(dict[str, object], store.materialize(record.reference))
        assert restored == point
        assert restored['first'] is restored['again']
        assert restored['first'] == restored['distinct'] and restored['first'] is not restored['distinct']
        assert scope.lookup(point) is record


def test_private_batch_references_stay_unreadable_after_flush_until_commit() -> None:
    with OwnedEvidenceRecords(cache_bytes=0) as store:
        old = store.write_scalar('old')
        before = store.record_count
        with store._new_graph_writer(memory_bytes=4096) as writer:
            key = writer.write_scalar('new')
            value = writer.write_list([old])
            root = writer.write_dict([(key, value)])
            writer._flush()
            assert store.record_count == before
            assert getsizeof(writer) + getsizeof(writer._data) + getsizeof(writer._index) <= writer.memory_used <= 4096
            for reference in (key, value, root):
                with pytest.raises(ValueError, match='committed'):
                    store._validate_ref(reference)
                with pytest.raises(ValueError, match='committed'):
                    store._record_header(reference.offset)
                with pytest.raises(ValueError, match='committed'):
                    store.materialize(reference)
            with pytest.raises(ValueError, match='nested'):
                store.write_list([root])
            with producer_record_scope(store) as scope:
                with pytest.raises(ValueError, match='nested'):
                    scope.record_point({'cannot publish inside the batch': 1})
                assert scope.outputs == ()
            # 私有写者仍可引用已刷入但尚未提交的本批记录。
            final = writer.write_list([root, root])
        values = cast(list[object], store.materialize(final))
        assert values == [{'new': ['old']}] * 2 and values[0] is values[1]
        assert writer.memory_used == 0
        with pytest.raises(ValueError, match='active'):
            writer.write_scalar('late')


def test_zero_cache_key_validation_handles_buffered_flushed_and_oversized_records() -> None:
    with OwnedEvidenceRecords(cache_bytes=0) as store:
        index = _BatchStream(store._index)
        store._index = cast(BinaryIO, index)
        with store._new_graph_writer(memory_bytes=4096) as writer:
            key = writer.write_scalar('key before several flushes')
            items = [writer.write_scalar(number) for number in range(100)]
            large_key = writer.write_scalar('large key ' * 400)
            small_key = writer.write_scalar('still buffered')
            root = writer.write_dict([(key, writer.write_tuple(items)), (large_key, items[0]), (small_key, items[1])])
            assert writer.memory_used <= 4096
        result = cast(dict[str, object], store.materialize(root))
        assert result['key before several flushes'] == tuple(range(100))
        assert result['large key ' * 400] == 0 and result['still buffered'] == 1
        assert index.reads > 0
        assert store.cache_memory_used == 0


@pytest.mark.parametrize('stream_name', ('_data', '_index'))
def test_producer_final_flush_failure_publishes_no_carrier_and_keeps_old_roots(stream_name: str) -> None:
    with OwnedEvidenceRecords(cache_bytes=64 * 1024) as store, producer_record_scope(store) as scope:
        old = scope.record_point({'old': [1]})
        count, size, start = store.record_count, store.stored_bytes, store._committed_index_end
        bindings, outputs, used = dict(scope._bindings), scope.outputs, scope.memory_used
        stream = cast(BinaryIO, getattr(store, stream_name))
        setattr(store, stream_name, cast(BinaryIO, _BatchStream(stream, fail_write=1)))
        point: dict[str, object] = {'new': {'items': [2, 3]}}
        with pytest.raises(OSError, match='injected batch write failure'):
            scope.record_point(point)
        assert scope.lookup(point) is None and scope.lookup(old.value) is old
        assert scope._bindings == bindings and scope.outputs == outputs and scope.memory_used == used
        assert (store.record_count, store.stored_bytes) == (count, size)
        assert all(offset < start for offset in store._cache)
        assert store.materialize(old.reference) == {'old': [1]}
        new = store.write_scalar('replacement')
        assert new.offset == start and store.materialize(new) == 'replacement'


def test_failure_after_an_earlier_chunk_flush_rolls_back_the_whole_graph() -> None:
    with OwnedEvidenceRecords(cache_bytes=8192) as store:
        old = store.capture_new({'old': [1]})
        count, size, start = store.record_count, store.stored_bytes, store._committed_index_end
        store._index = cast(BinaryIO, _BatchStream(store._index, fail_write=2))
        with pytest.raises(OSError, match='injected batch write failure'):
            with store._new_graph_writer(memory_bytes=4096) as writer:
                for number in range(100):
                    writer.write_scalar(number)
        assert (store.record_count, store.stored_bytes) == (count, size)
        assert all(offset < start for offset in store._cache)
        assert writer.memory_used == 0
        assert store.materialize(old) == {'old': [1]}


def test_batch_rollback_failure_closes_store_and_releases_cache_and_buffers() -> None:
    with OwnedEvidenceRecords(cache_bytes=8192) as store:
        old = store.write_scalar('old')
        store._data = cast(BinaryIO, _BatchStream(store._data, fail_write=1, fail_truncate=True))
        with pytest.raises(OSError, match='injected batch rollback failure'):
            with store._new_graph_writer(memory_bytes=4096) as writer:
                writer.write_scalar('new')
        assert writer.memory_used == store.cache_memory_used == 0
        with pytest.raises(ValueError, match='closed'):
            store.materialize(old)


def test_batch_rejects_unknown_values_and_non_scalar_new_keys_without_publishing_records() -> None:
    class Audit:
        def __sizeof__(self) -> Never:
            raise AssertionError('unknown size hook executed')

        def __reduce_ex__(self, protocol: int) -> Never:
            raise AssertionError('unknown serialization hook executed')

    with OwnedEvidenceRecords(cache_bytes=8192) as store, producer_record_scope(store) as scope:
        count, size = store.record_count, store.stored_bytes
        with pytest.raises(TypeError, match='exact builtin'):
            scope.record_point({'known': [1], 'unknown': Audit()})
        assert scope.outputs == () and (store.record_count, store.stored_bytes) == (count, size)
        with pytest.raises(TypeError, match='scalar records'):
            with store._new_graph_writer(memory_bytes=4096) as writer:
                key = writer.write_list([])
                writer.write_dict([(key, writer.write_scalar(1))])
        assert (store.record_count, store.stored_bytes) == (count, size)
        assert store.cache_memory_used == 0


def test_batch_memory_comes_from_the_same_producer_scope_budget() -> None:
    with OwnedEvidenceRecords(cache_bytes=0) as store, producer_record_scope(store, memory_bytes=16 * 1024) as scope:
        with pytest.raises(ValueError, match='memory limit'):
            scope.record_point({'payload': b'x' * 2000})
        assert scope.outputs == () and store.record_count == 0
        with pytest.raises(ValueError, match='memory limit'):
            with store._new_graph_writer(memory_bytes=2048):
                raise AssertionError('undersized batch was admitted')
