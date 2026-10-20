"""Request-owned writers retain per-artifact durability and bounded waits."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import sqlite3
from time import perf_counter

import pytest

from wavequant.infrastructure.persistence.artifact_cache import ArtifactCache
from wavequant.infrastructure.persistence.calculation_step_cache import CalculationStepCache


def test_many_writes_use_one_writer_and_commit_before_session_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = ArtifactCache(tmp_path)
    connect = cache._connect
    writers: list[sqlite3.Connection] = []

    def record(*, timeout_seconds: float | None = None) -> sqlite3.Connection:
        db = connect(timeout_seconds=timeout_seconds)
        writers.append(db)
        return db

    monkeypatch.setattr(cache, '_connect', record)
    with cache.write_session():
        assert not writers
        for index in range(6):
            assert cache.put('test', index, dict(index=index))
            assert len(writers) == 1
            assert not writers[0].in_transaction
            # Another connection observes each commit, not just the final one.
            assert ArtifactCache(tmp_path).get('test', index) == dict(index=index)
        with closing(sqlite3.connect(cache.path, timeout=0)) as other, other:
            other.execute('BEGIN IMMEDIATE')
    with pytest.raises(sqlite3.ProgrammingError):
        writers[0].execute('SELECT 1')


def test_failed_write_rolls_back_and_discards_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = ArtifactCache(tmp_path)
    with cache.write_session():
        assert cache.put('test', 0, dict(ok=True))
        failed = cache._write_db
        evict = cache._evict_over_budget

        def disk_failure(db: sqlite3.Connection, key: str, size: int) -> None:
            raise sqlite3.OperationalError('simulated disk full')

        monkeypatch.setattr(cache, '_evict_over_budget', disk_failure)
        assert not cache.put('test', 1, dict(ok=False))
        assert cache._write_db is None
        assert ArtifactCache(tmp_path).get('test', 1) is None
        assert ArtifactCache(tmp_path).get('test', 0) == dict(ok=True)
        assert failed is not None
        with pytest.raises(sqlite3.ProgrammingError):
            failed.execute('SELECT 1')
        monkeypatch.setattr(cache, '_evict_over_budget', evict)
        assert cache.put('test', 2, dict(ok=True))
        assert cache._write_db is not failed


def test_foreign_thread_writes_do_not_borrow_owner_connection(tmp_path: Path) -> None:
    cache = ArtifactCache(tmp_path)
    with cache.write_session():
        assert cache.put('test', 0, {})
        owned = cache._write_db
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(cache.put, 'test', 1, {}).result()
        assert cache._write_db is owned
        assert cache.get('test', 1) == {}
        assert cache.put('test', 2, {})


def test_busy_reused_writer_keeps_short_timeout_and_releases_handle(tmp_path: Path) -> None:
    cache = ArtifactCache(tmp_path)
    with cache.write_session():
        assert cache.put('test', 0, {})
        with closing(sqlite3.connect(cache.path, isolation_level=None)) as blocker:
            blocker.execute('BEGIN IMMEDIATE')
            started = perf_counter()
            assert not cache.put('test', 1, {})
            assert perf_counter() - started < 2
            assert cache._write_db is None
            blocker.execute('ROLLBACK')
        assert cache.put('test', 2, {})


def test_cancelled_session_closes_writer_without_undoing_prior_commit(tmp_path: Path) -> None:
    cache = ArtifactCache(tmp_path)
    with pytest.raises(KeyboardInterrupt):
        with cache.write_session():
            assert cache.put('test', 0, {})
            raise KeyboardInterrupt
    assert cache._write_db is None and cache._write_owner is None
    assert cache.get('test', 0) == {}


def test_rejected_large_payload_never_opens_writer(tmp_path: Path) -> None:
    cache = ArtifactCache(tmp_path, max_bytes=1)
    with cache.write_session():
        assert not cache.put('test', 0, {})
        assert cache._write_db is None
    assert not cache.path.exists()


def test_recorder_finish_releases_writer_even_if_publish_raises(tmp_path: Path) -> None:
    recorder = CalculationStepCache(tmp_path, {}, lambda value: None, record_details=True)
    recorder.start_write_session()
    assert recorder.cache is not None
    assert recorder.cache.put('test', 0, {})

    def fail_publish(value: dict[str, object]) -> None:
        raise RuntimeError('publisher failed')

    recorder.publish = fail_publish
    with pytest.raises(RuntimeError, match='publisher failed'):
        recorder.finish(None)
    assert recorder.cache._write_db is None
    assert recorder.cache._write_owner is None


def test_recorder_stop_releases_writer_and_default_recording_remains_lazy(tmp_path: Path) -> None:
    recorder = CalculationStepCache(tmp_path, {}, lambda value: None, record_details=True)
    recorder.start_write_session()
    assert recorder.cache is not None
    assert recorder.cache.put('test', 0, {})
    recorder._stop_cache()
    assert recorder.cache_disabled
    assert recorder.cache._write_db is None and recorder.cache._write_owner is None
    default = CalculationStepCache(tmp_path / 'unused', {}, lambda value: None)
    default.start_write_session()
    default.finish(None)
    assert default.cache is None
    assert not (tmp_path / 'unused').exists()
