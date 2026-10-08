"""Restartable, bounded refresh of all saved watchlist groups."""

from __future__ import annotations

import hashlib
import json
import logging
import math
from threading import Event, Lock, Thread
from time import time
from typing import Callable, cast

from .backtest_jobs import BacktestJob, BacktestJobCapacity, BacktestJobSymbolBusy, BacktestJobs
from .watchlists import WatchlistStore, record


class WatchlistRefresh:
    def __init__(self, store: WatchlistStore, jobs: BacktestJobs, *,
                 version: Callable[[str, str], str], catalog: Callable[[str], dict[str, object]],
                 submit: Callable[[str, dict[str, list[str]]], BacktestJob],
                 clock: Callable[[], float] = time) -> None:
        self.store, self.jobs = store, jobs
        self.version, self.catalog, self.submit, self.clock = version, catalog, submit, clock
        self.stopped = Event()
        self.wake = Event()
        self.lock = Lock()
        self.active: tuple[str, BacktestJob, int] | None = None
        self.status: dict[str, object] = {"status": "waiting", "error": ""}
        self.catalog_at = 0.0
        self.catalog_source = ""
        self.stocks: dict[str, dict[str, object]] = {}
        self.thread: Thread | None = None
        self.desired: set[str] | None = None

    def start(self) -> None:
        self.thread = Thread(target=self.run, name="watchlist-refresh", daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.stopped.set()
        self.wake.set()
        if self.thread:
            self.thread.join(timeout=5)

    def run(self) -> None:
        while not self.stopped.is_set():
            try:
                self.tick()
            except Exception:
                logging.exception("saved watchlist refresh failed")
                with self.lock:
                    self.status = {"status": "error", "error": "自动刷新暂不可用，稍后重试"}
            self.wake.wait(5)
            self.wake.clear()

    def state(self) -> dict[str, object]:
        with self.lock:
            return dict(self.status)

    def tick(self) -> None:
        with self.lock:
            self._tick()

    def _tick(self) -> None:
        now = self.clock()
        if self.active:
            key, job, attempts = self.active
            if not job.done.is_set():
                return
            _, result, error = self.jobs.outcome(job)
            result_data = record(result) if isinstance(result, dict) else {}
            backtest = result_data.get("backtest")
            expected = record(job.details.get("params"))
            completed = (error is None and isinstance(backtest, dict)
                         and backtest.get("status") != "data_unavailable" and result_data.get("result_scope") == "stock"
                         and result_data.get("symbol") == expected.get("symbol")
                         and result_data.get("asof") == expected.get("asof"))
            summary = dict(job.details)
            summary.update(status="completed", result_valid=True, persisted_current=True, result_available=False)
            metrics = result_data.get("metrics")
            if isinstance(metrics, dict):
                for name in ("total_return", "total_pnl", "max_drawdown", "holding_max_drawdown",
                             "holding_current_max_drawdown"):
                    value = metrics.get(name)
                    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
                        summary[name] = value
                for name in ("holding_drawdown_version", "holding_drawdown_status", "holding_current_drawdown_status"):
                    if isinstance(metrics.get(name), str):
                        summary[name] = metrics[name]
            orders = result_data.get("orders")
            if isinstance(orders, list):
                summary["fill_count"] = sum(isinstance(order, dict) and order.get("status") == "filled" for order in orders)
            self.store.finish(key, attempts + 1, now + (5, 15, 45)[min(attempts, 2)], completed, summary)
            self.active = None
        document = self.store.load()
        configured = document["settings"]
        if configured is None:
            self.status = {"status": "waiting", "error": "", "total": 0, "completed": 0, "failed": 0}
            return
        configured = record(configured)
        if not configured["enabled"]:
            self.status = {**self.status, "status": "paused"}
            return
        context = cast(dict[str, str], record(configured["context"]))
        version = self.version(context["run"], context["variant"])
        source = context["source"]
        if self.catalog_source != source or now - self.catalog_at >= 30 or not self.stocks:
            catalog = self.catalog(source)
            raw_stocks = catalog.get("stocks")
            if not isinstance(raw_stocks, list):
                raise ValueError("market catalog unavailable")
            self.stocks = {str(stock["symbol"]): stock for raw in raw_stocks
                           if isinstance(raw, dict) and (stock := record(raw)).get("symbol")}
            self.catalog_source, self.catalog_at = source, now
        saved = record(document["snapshot"])
        members = [record(item) for item in cast(list[object], saved["memberships"])]
        candidates: dict[str, tuple[str, dict[str, list[str]]]] = {}
        seen: set[str] = set()
        for member in members:
            symbol = str(member["symbol"])
            available_stock = self.stocks.get(symbol)
            if symbol in seen or not available_stock or available_stock.get("has_data") is False:
                continue
            seen.add(symbol)
            asof = available_stock.get("last")
            if not isinstance(asof, str) or asof < context["start"]:
                continue
            params = {key: [value] for key, value in context.items() if key != "source"}
            params.update(symbol=[symbol], asof=[asof])
            key = hashlib.sha256(json.dumps([version, source, params], sort_keys=True).encode()).hexdigest()
            candidates[key] = (symbol, params)
        desired = set(candidates)
        if desired != self.desired:
            self.store.prune_runs(desired)
            self.desired = desired
        known = self.store.run_states()
        recent = self.jobs.snapshot().get("recent")
        if isinstance(recent, list):
            current_records = {}
            for raw_item in recent:
                item = record(raw_item)
                if (item.get("version") == version and item.get("path") == f"/api/{source}-backtest"
                    and item.get("status") == "completed" and item.get("result_valid") is True):
                    current_records[json.dumps(item.get("params"), sort_keys=True)] = item
            for key, (symbol, params) in candidates.items():
                if known.get(key, (0, 0, False))[2]:
                    continue
                expected = {name: values[0] for name, values in params.items()}
                matching_item = current_records.get(json.dumps(expected, sort_keys=True))
                if matching_item and matching_item.get("symbol") == symbol:
                    self.store.finish(key, 1, 0, True, {**matching_item, "persisted_current": True, "result_available": False})
                    known[key] = (1, 0, True)
        states = {key: known.get(key, (0, 0, False)) for key in candidates}
        completed = sum(state[2] for state in states.values())
        failed = sum(not state[2] and state[0] >= 3 for state in states.values())
        self.status = {"status": "idle", "error": "", "version": version, "source": source,
                       "total": len(candidates), "completed": completed, "failed": failed}
        # Foreground work keeps the existing global slots and same-stock exclusion.
        if self.jobs.snapshot()["active"]:
            self.status["status"] = "waiting"
            return
        for key, (symbol, params) in candidates.items():
            attempts, retry_at, done = states[key]
            if done or attempts >= 3 or now < retry_at:
                continue
            try:
                job = self.submit(f"/api/{source}-backtest", params)
            except (BacktestJobCapacity, BacktestJobSymbolBusy):
                return
            self.active = (key, job, attempts)
            self.status.update(status="running", symbol=symbol)
            return
