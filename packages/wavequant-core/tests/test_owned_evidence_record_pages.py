"""跨生产图共享固定页；逻辑提交、旧根恢复及物理失败的边界。"""

from typing import BinaryIO, cast

import pytest

from wavequant.domain.market_structure.owned_evidence_producer import producer_record_scope
from wavequant.domain.market_structure.owned_evidence_records import OwnedEvidenceRecords


class _PageStream:
    def __init__(self, stream: BinaryIO) -> None:
        self.stream = stream
        self.writes = self.reads = 0
        self.fail_write = False
        self.fail_truncate = False
        self.fail_close = False

    def write(self, payload: bytes | memoryview) -> int | None:
        self.writes += 1
        if self.fail_write:
            self.fail_write = False
            # 底层已经部分写出，不能仅假设抛异常就没有改变文件。
            self.stream.write(memoryview(payload)[:max(1, len(payload) // 2)])
            raise OSError('injected partial page write')
        return self.stream.write(payload)

    def read(self, size: int = -1) -> bytes:
        self.reads += 1
        return self.stream.read(size)

    def seek(self, offset: int, whence: int = 0) -> int:
        return self.stream.seek(offset, whence)

    def truncate(self, size: int | None = None) -> int:
        if self.fail_truncate:
            raise OSError('injected page truncate failure')
        return self.stream.truncate(size)

    def close(self) -> None:
        self.stream.close()
        if self.fail_close:
            raise OSError('injected page close failure')


def _streams(store: OwnedEvidenceRecords) -> tuple[_PageStream, _PageStream]:
    data, index = _PageStream(store._data), _PageStream(store._index)
    store._data, store._index = cast(BinaryIO, data), cast(BinaryIO, index)
    return data, index


def test_committed_graphs_share_pages_and_remain_readable_after_cache_eviction() -> None:
    budget = 1024 * 1024
    with OwnedEvidenceRecords(cache_bytes=budget, page_bytes=64 * 1024) as store:
        data, index = _streams(store)
        references = []
        with producer_record_scope(store) as scope:
            for number in range(20):
                shared = [number]
                point = {'first': shared, 'again': shared, 'distinct': [number]}
                references.append(scope.record_point(point).reference)
                store.clear_cache()
                value = cast(dict[str, object], store.materialize(references[-1]))
                assert value == point
                assert value['first'] is value['again']
                assert value['first'] is not value['distinct']
                assert store.storage_memory_used <= budget
        assert data.writes == index.writes == data.reads == index.reads == 0
        first = store.materialize(references[0])
        assert store.materialize(references[0]) == first
        assert store.materialize(references[0]) is not first
        store.flush()
        assert data.writes == index.writes == 1
        store.clear_cache()
        assert store.materialize(references[0]) == first


@pytest.mark.parametrize('failed_stream', ('data', 'index'))
def test_partial_page_write_rolls_back_both_streams_and_preserves_committed_ram_roots(failed_stream: str) -> None:
    with OwnedEvidenceRecords(cache_bytes=8192, page_bytes=96) as store:
        data, index = _streams(store)
        old = store.write_scalar(b'old')
        root = store.write_list([old, old])
        before = store.record_count, store.stored_bytes
        target = data if failed_stream == 'data' else index
        target.fail_write = True
        with pytest.raises(OSError, match='partial page write'):
            with store._new_graph_writer(memory_bytes=4096) as writer:
                for number in range(3):
                    writer.write_scalar(bytes([number]) * 60)
        assert not store._closed
        assert (store.record_count, store.stored_bytes) == before
        store.clear_cache()
        assert store.materialize(root) == [b'old', b'old']
        # 失败偏移可复用，不能命中已撤销图的缓存或残留页字节。
        replacement = store.write_scalar(b'replacement')
        assert replacement.offset == before[0] * 24
        assert store.materialize(replacement) == b'replacement'
        store.flush()
        store.clear_cache()
        assert store.materialize(root) == [b'old', b'old']


def test_validation_failure_after_several_successful_page_flushes_keeps_old_roots() -> None:
    with OwnedEvidenceRecords(cache_bytes=8192, page_bytes=96) as store:
        old = store.write_scalar('kept')
        before = store.record_count, store.stored_bytes
        with pytest.raises(TypeError, match='exact builtin'):
            with store._new_graph_writer(memory_bytes=4096) as writer:
                for number in range(12):
                    writer.write_scalar(bytes([number]) * 60)
                writer.write_scalar(cast(str, object()))
        assert (store.record_count, store.stored_bytes) == before
        store.clear_cache()
        assert store.materialize(old) == 'kept'
        assert store.materialize(store.write_scalar('after')) == 'after'


def test_public_boundary_rejects_uncommitted_pages_even_when_old_keys_are_uncached() -> None:
    with OwnedEvidenceRecords(cache_bytes=8192, page_bytes=96) as store:
        old = store.write_scalar('old')
        store.clear_cache()
        with store._new_graph_writer(memory_bytes=4096) as writer:
            key = writer.write_scalar('new')
            value = writer.write_list([old])
            store.clear_cache()
            root = writer.write_dict([(key, value)])
            writer._flush()
            for item in (key, value, root):
                with pytest.raises(ValueError, match='committed'):
                    store.materialize(item)
            with pytest.raises(ValueError, match='active transaction'):
                store.flush()
        store.clear_cache()
        assert store.materialize(root) == {'new': ['old']}


def test_explicit_flush_failure_keeps_old_roots_and_does_not_drop_dirty_pages() -> None:
    with OwnedEvidenceRecords(cache_bytes=8192, page_bytes=96) as store:
        data, index = _streams(store)
        root = store.capture_new({'kept': [1]})
        index.fail_write = True
        with pytest.raises(OSError, match='partial page write'):
            store.flush()
        assert not store._closed
        store.clear_cache()
        assert store.materialize(root) == {'kept': [1]}
        store.flush()
        store.clear_cache()
        assert store.materialize(root) == {'kept': [1]}
        assert data.writes >= 1 and index.writes >= 2


def test_truncate_failure_closes_every_reference_and_releases_page_reservation() -> None:
    store = OwnedEvidenceRecords(cache_bytes=8192, page_bytes=96)
    data, index = _streams(store)
    old = store.write_scalar('kept')
    data.fail_write = data.fail_truncate = True
    with pytest.raises(OSError, match='truncate failure'):
        store.write_scalar('x' * 200)
    assert store._closed and data.stream.closed and index.stream.closed
    assert store.storage_memory_used == 0
    with pytest.raises(ValueError, match='closed'):
        store.materialize(old)
    store.close()


@pytest.mark.parametrize('failure', ('flush', 'close'))
def test_close_surfaces_failure_and_still_closes_both_streams(failure: str) -> None:
    store = OwnedEvidenceRecords(cache_bytes=8192, page_bytes=96)
    data, index = _streams(store)
    root = store.write_scalar('kept')
    if failure == 'flush':
        data.fail_write = True
    else:
        data.fail_close = True
    with pytest.raises(OSError, match='injected'):
        store.close()
    assert data.stream.closed and index.stream.closed
    assert store.storage_memory_used == 0
    with pytest.raises(ValueError, match='closed'):
        store.materialize(root)


def test_page_payloads_never_need_a_cross_boundary_copy_and_oversize_records_use_file() -> None:
    with OwnedEvidenceRecords(cache_bytes=8192, page_bytes=96) as store:
        first = store.write_scalar(b'a' * 70)
        second = store.write_scalar(b'b' * 70)
        large = store.write_scalar(b'c' * 300)
        last = store.write_scalar(b'd' * 10)
        store.clear_cache()
        for reference, expected in ((first, b'a' * 70), (second, b'b' * 70),
                                    (large, b'c' * 300), (last, b'd' * 10)):
            assert store.materialize(reference) == expected
        assert store._data_page is not None
        with pytest.raises(ValueError, match='cross'):
            store._data_page.read(store._data, store._data_page.base - 1, 2)
        assert store.storage_memory_used <= 8192


def test_zero_budget_remains_unpaged_and_rejects_unfunded_page_allocations() -> None:
    with OwnedEvidenceRecords(cache_bytes=0) as store:
        assert store._data_page is store._index_page is None
        assert store.storage_memory_used == 0
        assert store.materialize(store.write_scalar('direct')) == 'direct'
    with pytest.raises(ValueError, match='shared storage memory'):
        OwnedEvidenceRecords(cache_bytes=0, page_bytes=96)
