"""有界股票快照进程池；数据、解释器和缓存均不跨请求复用。"""

import atexit
from collections import deque
from collections.abc import Iterable, Iterator, Sequence
from concurrent.futures import Future, ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass
from multiprocessing import get_context
import os
import pickle
from threading import Lock
from typing import cast
from zlib import compress, decompress

from wavequant.domain.market_structure.trend_evidence_reuse import _geometry_signature
from wavequant.domain.market_structure.trend_stage_work import StageKind, TrendStageComputer
from wavequant.domain.market_structure.trend_lines import _FiniteEvidenceCertificates
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.backtest_history_reuse import _bar_certificate, _UncertifiableValue
from .process_memory import peak_rss_bytes


_computer: TrendStageComputer | None = None
_QUEUE_BYTES = 24 * 1024 * 1024
_PAYLOAD_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class _Outcome:
    payload: bytes | None
    compressed: bool
    pid: int
    peak_rss: int | None
    stage: tuple[StageKind, int]
    state: dict[str, int]


class _StageTicket:
    def __init__(self, owner: 'TrendStageExecutor', future: Future[_Outcome] | None, size: int) -> None:
        self.owner, self.future, self.size = owner, future, size
        self.ready = False
        self.value: object | None = None

    def result(self) -> object | None:
        if not self.ready:
            self.value = self.owner._collect(self.future, self.size)
            self.ready = True
        return self.value


def _initialize(bars: tuple[Bar, ...]) -> None:
    global _computer
    _computer = TrendStageComputer(bars)
    atexit.register(_computer.close)


def _compute(kind: StageKind, index: int, payload: bytes, level: int,
             n_enabled: bool, options: tuple[bool, ...]) -> _Outcome:
    if _computer is None:
        raise RuntimeError('trend worker requires its own stock snapshot')
    # 只接受本池 exact-builtin Pickler 写出的请求内字节，不接受路径或外部缓存。
    value: object = pickle.loads(payload)
    try:
        result = _computer.compute(kind, index, value, level=level, n_enabled=n_enabled, options=options)
    except Exception:
        # 回到当日原串行入口重现异常；未触发资格刷新的日期不提前抛出领域错误。
        result = None
    encoded = pickle.dumps(result, protocol=5) if result is not None else None
    packed = compress(encoded, level=1) if encoded is not None and len(encoded) >= 64 * 1024 else encoded
    compressed = encoded is not None and packed is not None and len(packed) < len(encoded) * .85
    states = tuple(_computer.judgements._states.values())
    diagnostics = {name: sum(getattr(state, name) for state in states)
                   for name in ('cold_runs', 'append_runs', 'tail_restores', 'budget_rejections', 'memory_used')}
    diagnostics['source_count'] = sum(len(state._source_keys) for state in states)
    if _computer.frozen is not None:
        diagnostics.update(frozen_hits=_computer.frozen._hits, frozen_misses=_computer.frozen._misses,
                           frozen_fallbacks=_computer.frozen._fallbacks, frozen_bytes=_computer.frozen.memory_used)
    return _Outcome(packed if compressed else encoded, compressed, os.getpid(), peak_rss_bytes(),
                    (kind, level), diagnostics)


class TrendStageExecutor:
    """固定进程数、排队字节上限和有序背压，不能积压整份历史证据树。"""

    def __init__(self, bars: tuple[Bar, ...], *, workers: int | None = None) -> None:
        if workers is not None and (type(workers) is not int or not 1 <= workers <= 8):
            raise ValueError('trend workers must be an integer from 1 to 8')
        self.bars = bars
        self.certificate = _bar_certificate(bars)
        self.workers = workers if workers is not None else min(6, max(1, (os.cpu_count() or 1)//2))
        self.pool: ProcessPoolExecutor | None = None
        self.closed = False
        self.submissions = 0
        self.fallbacks = 0
        self.peak_queue_bytes = 0
        self.worker_peaks: dict[int, int | None] = {}
        self.worker_states: dict[str, dict[str, int]] = {}
        self.broken = False
        self._budget_lock = Lock()
        self._inflight_bytes = 0
        self._finite = _FiniteEvidenceCertificates()

    def _certified(self, kind: StageKind, value: object, signature: bytes) -> bool:
        if kind in ('geometry', 'hierarchy') and type(value) is tuple and len(value) == 2:
            parts = cast(tuple[object, object], value)
            groups = (parts[0],) if kind == 'geometry' and type(parts[1]) is str else parts
            if all(type(group) in (tuple, list) for group in groups):
                return all(self._finite.certify(point) for group in groups
                           for point in cast(Sequence[object], group))
        return self._finite.certify(value, signature)

    def matches(self, bars: Sequence[Bar]) -> bool:
        try:
            return not self.closed and _bar_certificate(bars) == self.certificate
        except (_UncertifiableValue, RecursionError):
            return False

    def _submit(self, kind: StageKind, index: int, value: object, level: int,
                n_enabled: bool, options: tuple[bool, ...]) -> tuple[Future[_Outcome] | None, int]:
        if self.broken:
            self.fallbacks += 1
            return None, 0
        signature = _geometry_signature(value)
        if signature is None or len(signature) > _PAYLOAD_BYTES:
            self.fallbacks += 1
            return None, 0
        with self._budget_lock:
            if self._inflight_bytes + len(signature) > _QUEUE_BYTES:
                self.fallbacks += 1
                return None, 0
        if not self._certified(kind, value, signature):
            self.fallbacks += 1
            return None, 0
        if self.pool is None:
            self.pool = ProcessPoolExecutor(max_workers=self.workers, mp_context=get_context('spawn'),
                                            initializer=_initialize, initargs=(self.bars,))
        self.submissions += 1
        try:
            future = self.pool.submit(_compute, kind, index, signature, level, n_enabled, options)
            with self._budget_lock:
                self._inflight_bytes += len(signature)
                self.peak_queue_bytes = max(self.peak_queue_bytes, self._inflight_bytes)
            return future, len(signature)
        except BrokenProcessPool:
            self.broken = True
            self.fallbacks += 1
            return None, 0

    def _release(self, size: int) -> None:
        with self._budget_lock:
            self._inflight_bytes -= size

    def _collect(self, future: Future[_Outcome] | None, size: int) -> object | None:
        outcome = None
        try:
            outcome = future.result() if future is not None else None
        except BrokenProcessPool:
            self.broken = True
            self.fallbacks += 1
        finally:
            self._release(size)
        if outcome is not None:
            self.worker_states[f'{outcome.pid}:{outcome.stage[0]}:{outcome.stage[1]}'] = outcome.state
            previous = self.worker_peaks.get(outcome.pid)
            self.worker_peaks[outcome.pid] = (max(previous or 0, outcome.peak_rss)
                                             if outcome.peak_rss is not None else previous)
            if outcome.payload is None:
                self.fallbacks += 1
        if outcome is None or outcome.payload is None:
            return None
        return pickle.loads(decompress(outcome.payload) if outcome.compressed else outcome.payload)

    def submit(self, kind: StageKind, index: int, value: object, *, level: int = 1,
               n_enabled: bool = False, options: tuple[bool, ...] = ()) -> _StageTicket:
        if self.closed:
            raise ValueError('a closed trend stage cannot accept work')
        future, size = self._submit(kind, index, value, level, n_enabled, options)
        return _StageTicket(self, future, size)

    def map(self, kind: StageKind, rows: Iterable[tuple[int, object]], *, level: int = 1,
            n_enabled: bool = False, options: tuple[bool, ...] = ()) -> Iterator[tuple[int, object | None]]:
        if self.closed:
            raise ValueError('a closed trend stage cannot accept work')
        source = iter(rows)
        pending: deque[tuple[int, Future[_Outcome] | None, int]] = deque()
        queued = 0
        exhausted = False
        try:
            while pending or not exhausted:
                # 每个worker最多一个待收回快照；一个最大行的余量覆盖下一次准入。
                while not exhausted and len(pending) < self.workers and queued <= _QUEUE_BYTES-_PAYLOAD_BYTES:
                    try:
                        index, value = next(source)
                    except StopIteration:
                        exhausted = True
                        break
                    future, size = self._submit(kind, index, value, level, n_enabled, options)
                    pending.append((index, future, size))
                    queued += size
                    self.peak_queue_bytes = max(self.peak_queue_bytes, queued)
                if pending:
                    index, future, size = pending.popleft()
                    # 有序汇总保持首次观察、修订、失效及checkpoint的原推进次序。
                    result = self._collect(future, size)
                    queued -= size
                    yield index, result
        finally:
            for _, future, size in pending:
                if future is not None:
                    if future.cancel():
                        self._release(size)
                    else:
                        def release_done(done: Future[_Outcome], amount: int = size) -> None:
                            self._release(amount)
                        future.add_done_callback(release_done)

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            if self.pool is not None:
                for pid, previous in self.worker_peaks.items():
                    current = peak_rss_bytes(pid)
                    if current is not None:
                        self.worker_peaks[pid] = max(previous or 0, current)
                self.pool.shutdown(wait=True, cancel_futures=True)
                self.pool = None

    def statistics(self) -> dict[str, object]:
        peaks = {str(pid): max(previous or 0, current) if current is not None else previous
                 for pid, previous in self.worker_peaks.items() for current in [peak_rss_bytes(pid)]}
        return dict(workers=self.workers, submissions=self.submissions, fallbacks=self.fallbacks,
                    peak_queue_bytes=self.peak_queue_bytes, queue_limit_bytes=_QUEUE_BYTES,
                    worker_peak_rss_bytes=peaks, worker_states=dict(self.worker_states),
                    broken=self.broken, closed=self.closed)


def stock_stage_runner(bars: tuple[Bar, ...]) -> TrendStageExecutor | None:
    return TrendStageExecutor(bars) if len(bars) >= 512 else None
