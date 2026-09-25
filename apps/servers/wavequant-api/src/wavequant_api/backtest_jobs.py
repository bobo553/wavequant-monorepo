"""Bounded, per-process recovery for a backtest whose HTTP client disconnects."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from threading import Event, Lock, Thread
from time import monotonic
from typing import Callable


class BacktestJobConflict(Exception):
    """A request ID was already assigned to different backtest arguments."""


class BacktestJobCapacity(Exception):
    """All bounded backtest execution slots are occupied."""

    def __init__(self, snapshot: dict[str, object]) -> None:
        self.snapshot = snapshot
        super().__init__("backtest capacity reached; retry shortly")


class BacktestJobSymbolBusy(Exception):
    """Another request is already calculating this stock."""

    def __init__(self, job_id: str, same_request: bool, snapshot: dict[str, object]) -> None:
        self.job_id = job_id
        self.same_request = same_request
        self.snapshot = snapshot
        super().__init__("stock already has a running backtest")


@dataclass
class BacktestJob:
    signature: str
    symbol: str | None = None
    details: dict[str, object] = field(default_factory=dict)
    done: Event = field(default_factory=Event)
    result: object = None
    error: Exception | None = None
    completed_at: float | None = None


class BacktestJobs:
    """Keep one calculation per request ID through HTTP disconnects.

    This registry intentionally does not reuse work by backtest parameters. A new
    request ID always checks current market data, while a retry of the same ID
    retrieves exactly the original calculation for a short recovery window.
    """

    def __init__(
        self,
        *,
        max_active: int = 4,
        max_completed: int = 24,
        completed_ttl_seconds: float = 900,
        clock: Callable[[], float] = monotonic,
        on_error: Callable[[str, Exception], None] | None = None,
    ) -> None:
        if max_active < 1 or max_completed < 1 or completed_ttl_seconds <= 0:
            raise ValueError("backtest job limits must be positive")
        self.max_active = max_active
        self.max_completed = max_completed
        self.completed_ttl_seconds = completed_ttl_seconds
        self.clock = clock
        self.on_error = on_error
        self._jobs: dict[str, BacktestJob] = {}
        self._active_symbols: dict[str, str] = {}
        self._active = 0
        self._lock = Lock()

    def _prune(self) -> None:
        now = self.clock()
        completed = [(job_id, job) for job_id, job in self._jobs.items() if job.completed_at is not None]
        for job_id, job in completed:
            if job.completed_at is not None and now - job.completed_at >= self.completed_ttl_seconds:
                del self._jobs[job_id]
        completed = [(job_id, job) for job_id, job in self._jobs.items() if job.completed_at is not None]
        for job_id, _ in sorted(completed, key=lambda item: item[1].completed_at or 0)[: -self.max_completed]:
            del self._jobs[job_id]

    def start(
        self,
        job_id: str,
        signature: str,
        work: Callable[[], object],
        *,
        symbol: str | None = None,
        details: dict[str, object] | None = None,
    ) -> BacktestJob:
        """Return an existing identical job or start one bounded worker."""
        with self._lock:
            self._prune()
            job = self._jobs.get(job_id)
            if job is not None:
                if job.signature != signature:
                    raise BacktestJobConflict("backtest job ID belongs to different arguments")
                return job
            symbol = symbol.lower() if symbol is not None else None
            existing_id = self._active_symbols.get(symbol) if symbol is not None else None
            if existing_id is not None:
                raise BacktestJobSymbolBusy(
                    existing_id, self._jobs[existing_id].signature == signature, self._snapshot_locked()
                )
            if self._active >= self.max_active:
                raise BacktestJobCapacity(self._snapshot_locked())
            job = BacktestJob(signature, symbol=symbol, details=dict(details or {}))
            self._jobs[job_id] = job
            if symbol is not None:
                self._active_symbols[symbol] = job_id
            self._active += 1
            try:
                Thread(target=self._run, args=(job_id, job, work), name=f"backtest-{job_id[:12]}", daemon=True).start()
            except Exception:
                del self._jobs[job_id]
                if symbol is not None:
                    del self._active_symbols[symbol]
                self._active -= 1
                raise
            return job

    def _run(self, job_id: str, job: BacktestJob, work: Callable[[], object]) -> None:
        result: object = None
        error: Exception | None = None
        succeeded = False
        try:
            result = work()
            succeeded = True
        except Exception as exc:
            error = exc
        finally:
            # Even an unexpected BaseException must release the capacity slot.
            recorded_error = (
                error if succeeded or error is not None else RuntimeError("backtest worker stopped unexpectedly")
            )
            if recorded_error is not None and self.on_error is not None:
                try:
                    self.on_error(job_id, recorded_error)
                except Exception:
                    logging.exception("backtest job failure logging failed")
            with self._lock:
                job.result = result
                job.error = recorded_error
                job.completed_at = self.clock()
                self._active -= 1
                if job.symbol is not None and self._active_symbols.get(job.symbol) == job_id:
                    del self._active_symbols[job.symbol]
                job.done.set()
                self._prune()

    def _snapshot_locked(self) -> dict[str, object]:
        running = []
        for job_id, job in self._jobs.items():
            if job.done.is_set():
                continue
            item = dict(job.details)
            item.update(job=job_id, status="running")
            running.append(item)
        return {"active": self._active, "max_active": self.max_active, "jobs": running}

    def snapshot(self) -> dict[str, object]:
        """Expose bounded live-job metadata without serializing backtest results."""
        with self._lock:
            self._prune()
            return self._snapshot_locked()

    def get(self, job_id: str) -> BacktestJob | None:
        """Get one live or recent job without extending its retention period."""
        with self._lock:
            self._prune()
            return self._jobs.get(job_id)

    def outcome(self, job: BacktestJob) -> tuple[bool, object, Exception | None]:
        """Return a coherent snapshot of completion, result, and failure."""
        with self._lock:
            return job.done.is_set(), job.result, job.error
