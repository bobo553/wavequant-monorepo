"""Bounded, per-process recovery for a backtest whose HTTP client disconnects."""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass, field
import json
import logging
from pathlib import Path
import sqlite3
from threading import Event, Lock, Thread
from time import monotonic, time
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
    started_at: float = 0.0
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
        max_history: int = 256,
        history_ttl_seconds: float = 7 * 86_400,
        history_path: Path | None = None,
        clock: Callable[[], float] = monotonic,
        history_clock: Callable[[], float] = time,
        on_error: Callable[[str, Exception], None] | None = None,
    ) -> None:
        if (
            max_active < 1
            or max_completed < 1
            or completed_ttl_seconds <= 0
            or max_history < 1
            or history_ttl_seconds <= 0
        ):
            raise ValueError("backtest job limits must be positive")
        self.max_active = max_active
        self.max_completed = max_completed
        self.completed_ttl_seconds = completed_ttl_seconds
        self.max_history = max_history
        self.history_ttl_seconds = history_ttl_seconds
        self.history_path = Path(history_path) if history_path is not None else None
        self.clock = clock
        self.history_clock = history_clock
        self.on_error = on_error
        self._jobs: dict[str, BacktestJob] = {}
        self._history: dict[str, tuple[float, dict[str, object]]] = {}
        self._active_symbols: dict[str, str] = {}
        self._active = 0
        self._lock = Lock()
        self._load_history()

    def _history_connection(self) -> sqlite3.Connection:
        assert self.history_path is not None
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.history_path, timeout=0.5)
        try:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS backtest_history "
                "(signature TEXT PRIMARY KEY, completed_at REAL NOT NULL, item TEXT NOT NULL)"
            )
        except sqlite3.Error:
            connection.close()
            raise
        return connection

    def _load_history(self) -> None:
        if self.history_path is None:
            return
        try:
            with closing(self._history_connection()) as connection, connection:
                rows = connection.execute(
                    "SELECT signature, completed_at, item FROM backtest_history "
                    "WHERE completed_at > ? ORDER BY completed_at DESC LIMIT ?",
                    (self.history_clock() - self.history_ttl_seconds, self.max_history),
                ).fetchall()
            for signature, completed_at, payload in rows:
                item = json.loads(payload)
                if isinstance(item, dict) and isinstance(item.get("job"), str):
                    self._history[signature] = (completed_at, item)
        except (OSError, sqlite3.Error, ValueError, TypeError) as exc:
            logging.warning("backtest status history unavailable: %s", exc)

    def _save_history(self, signature: str, completed_at: float, item: dict[str, object]) -> None:
        if self.history_path is None:
            return
        try:
            with closing(self._history_connection()) as connection, connection:
                connection.execute(
                    "INSERT OR REPLACE INTO backtest_history VALUES (?, ?, ?)",
                    (signature, completed_at, json.dumps(item, ensure_ascii=False, allow_nan=False)),
                )
                connection.execute(
                    "DELETE FROM backtest_history WHERE completed_at <= ?",
                    (self.history_clock() - self.history_ttl_seconds,),
                )
                connection.execute(
                    "DELETE FROM backtest_history WHERE signature IN "
                    "(SELECT signature FROM backtest_history ORDER BY completed_at DESC LIMIT -1 OFFSET ?)",
                    (self.max_history,),
                )
        except (OSError, sqlite3.Error, ValueError, TypeError) as exc:
            logging.warning("backtest status history write skipped: %s", exc)

    def _prune(self) -> None:
        now = self.history_clock()
        for signature, (completed_at, _) in list(self._history.items()):
            if now - completed_at >= self.history_ttl_seconds:
                del self._history[signature]
        for signature, _ in sorted(self._history.items(), key=lambda item: item[1][0])[: -self.max_history]:
            del self._history[signature]
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
            job = BacktestJob(signature, symbol=symbol, details=dict(details or {}), started_at=self.clock())
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
                item = dict(job.details)
                item.update(job=job_id, status="failed" if recorded_error is not None else "completed")
                if isinstance(result, dict):
                    backtest = result.get("backtest")
                    valid_result = (
                        result.get("result_scope") == "stock"
                        and isinstance(backtest, dict)
                        and backtest.get("status") != "data_unavailable"
                    )
                    item["result_valid"] = valid_result
                    if valid_result:
                        orders = result.get("orders")
                        if isinstance(orders, list):
                            item["fill_count"] = sum(
                                isinstance(order, dict) and order.get("status") == "filled" for order in orders
                            )
                self._history.pop(job.signature, None)
                history_completed_at = self.history_clock()
                self._history[job.signature] = (history_completed_at, item)
                self._active -= 1
                if job.symbol is not None and self._active_symbols.get(job.symbol) == job_id:
                    del self._active_symbols[job.symbol]
                self._prune()
                self._save_history(job.signature, history_completed_at, item)
                job.done.set()

    def _snapshot_locked(self) -> dict[str, object]:
        running = []
        now = self.clock()
        for job_id, job in self._jobs.items():
            if job.done.is_set():
                continue
            item = dict(job.details)
            item.update(job=job_id, status="running", elapsed_seconds=round(max(0.0, now - job.started_at), 1))
            running.append(item)
        return {"active": self._active, "max_active": self.max_active, "jobs": running}

    def elapsed_seconds(self, job: BacktestJob) -> float:
        """Report a running worker's age without exposing an internal monotonic timestamp."""
        with self._lock:
            end = job.completed_at if job.completed_at is not None else self.clock()
            return round(max(0.0, end - job.started_at), 1)

    def snapshot(self) -> dict[str, object]:
        """Expose bounded job metadata without serializing backtest results."""
        with self._lock:
            self._prune()
            snapshot = self._snapshot_locked()
            snapshot["recent"] = [
                dict(item, result_available=item["job"] in self._jobs)
                for _, item in sorted(self._history.values(), key=lambda record: record[0], reverse=True)
            ]
            return snapshot

    def get(self, job_id: str) -> BacktestJob | None:
        """Get one live or recent job without extending its retention period."""
        with self._lock:
            self._prune()
            return self._jobs.get(job_id)

    def outcome(self, job: BacktestJob) -> tuple[bool, object, Exception | None]:
        """Return a coherent snapshot of completion, result, and failure."""
        with self._lock:
            return job.done.is_set(), job.result, job.error
