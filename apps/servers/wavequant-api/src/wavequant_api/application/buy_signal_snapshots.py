"""Build and query the durable buy-signal read model."""

from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import json
from time import perf_counter
from typing import Any, cast

from wavequant.infrastructure.market_data.data import fingerprint  # type: ignore[import-untyped]

from ..infrastructure import BuySignalSnapshot, Infrastructure


class BuySignalSnapshotUnavailable(RuntimeError):
    """Raised when no completed buy snapshot exists for the request."""


class BuySignalSnapshotService:
    """Publish complete buy calculations and serve SQL/Redis-only queries."""

    def __init__(self, repository: Any, infrastructure: Infrastructure):
        self.repository = repository
        self.infrastructure = infrastructure

    def refresh(
        self,
        run: str,
        variant: str,
        scenario: str,
        source: str,
        start: str,
        *,
        symbol: str | None = None,
        asof: str | None = None,
    ) -> dict[str, object]:
        database = self.infrastructure.database
        if database is None:
            raise ValueError("买点预计算需要配置 WAVEQUANT_DATABASE_URL")
        if source not in {"tdx", "snapshot", "akshare"}:
            raise ValueError("invalid buy signal source")
        if (source == "akshare") != (symbol is not None):
            raise ValueError("AkShare 买点预计算需要且只允许一个 symbol")
        selected_asof = asof or self.repository.structure_scanner.latest_tdx_session()
        for value, name in ((selected_asof, "asof"), (start, "start")):
            if date.fromisoformat(value).isoformat() != value:
                raise ValueError(f"{name} must use YYYY-MM-DD")
        if start > selected_asof:
            raise ValueError("回测起点不能晚于回放日期")

        algorithm_version = self._algorithm_version(run, variant, scenario)
        data_version = self._data_version(source, run, selected_asof, symbol)
        existing = database.find_buy_signal_snapshot(
            run,
            variant,
            scenario,
            source,
            selected_asof,
            start,
            algorithm_version,
            scope_symbol=symbol,
            data_version=data_version,
        )
        if existing is not None:
            return self._refresh_result(existing, "current")

        params: dict[str, object] = {
            "run": run,
            "variant": variant,
            "scenario": scenario,
            "source": source,
            "asof": selected_asof,
            "start": start,
            # Store the widest supported read model once; the API only narrows it.
            "lookback": 20,
        }
        if symbol is not None:
            params["symbol"] = symbol
        started = perf_counter()
        job = self.repository.buy_scanner.start(params)
        while job["status"] in ("running", "cancelling"):
            job = self.repository.buy_scanner.get(job["id"], after=job["revision"], timeout=20)
        if job["status"] != "completed":
            raise RuntimeError(job.get("error") or f"买点预计算未完成：{job['status']}")
        if self._algorithm_version(run, variant, scenario) != algorithm_version:
            raise RuntimeError("买点预计算期间算法版本变化，未发布混合版本结果")
        if self._data_version(source, run, selected_asof, symbol) != data_version:
            raise RuntimeError("买点预计算期间行情版本变化，未发布混合版本结果")

        payload = {
            "results": job["results"],
            "stale": job["stale"],
            "skip_reasons": job["skip_reasons"],
            "errors": job["errors"],
            "funnel": job.get("funnel", {}),
            "performance": {**job.get("performance", {}), "elapsed_seconds": perf_counter() - started},
        }
        snapshot_id = self._snapshot_id(
            run, variant, scenario, source, symbol, selected_asof, start, algorithm_version, data_version
        )
        stored = database.save_buy_signal_snapshot(
            BuySignalSnapshot(
                snapshot_id=snapshot_id,
                run_id=run,
                variant=variant,
                scenario=scenario,
                source=source,
                scope_symbol=symbol,
                asof=selected_asof,
                start=start,
                algorithm_version=algorithm_version,
                data_version=data_version,
                payload=payload,
                total=job["total"],
                skipped=job["skipped"],
                failed=job["failed"],
            )
        )
        return self._refresh_result(stored, "published")

    def query(self, params: dict[str, object]) -> dict[str, object]:
        expected = {"run", "variant", "scenario", "source", "asof", "start", "lookback"}
        online_expected = expected | {"symbol"}
        if set(params) not in (expected, online_expected):
            raise ValueError("invalid precomputed buy signal query")
        run, variant, scenario, source, asof, start = (
            params[name] for name in ("run", "variant", "scenario", "source", "asof", "start")
        )
        lookback = params["lookback"]
        symbol = params.get("symbol")
        if not all(isinstance(value, str) for value in (run, variant, scenario, source, asof, start)):
            raise ValueError("invalid precomputed buy signal query values")
        run = cast(str, run)
        variant = cast(str, variant)
        scenario = cast(str, scenario)
        source = cast(str, source)
        asof = cast(str, asof)
        start = cast(str, start)
        if source not in {"tdx", "snapshot", "akshare"} or (source == "akshare") != isinstance(symbol, str):
            raise ValueError("AkShare 查询需要且只允许一个 symbol")
        if type(lookback) is not int or lookback not in (1, 5, 20):
            raise ValueError("lookback must be 1, 5 or 20")
        database = self.infrastructure.database
        if database is None:
            raise BuySignalSnapshotUnavailable("买点读模型未配置；请启动数据库并运行信号预计算 Worker")
        algorithm_version = self._algorithm_version(run, variant, scenario)
        snapshot = database.find_buy_signal_snapshot(
            run,
            variant,
            scenario,
            source,
            asof,
            start,
            algorithm_version,
            scope_symbol=symbol if isinstance(symbol, str) else None,
        )
        if snapshot is None:
            raise BuySignalSnapshotUnavailable(
                "该行情日、股票范围或当前算法版本尚无完成快照；后台 Worker 将在检测到变化后重建"
            )
        cache_key = f"signal:buy:v1:{snapshot.snapshot_id}:{lookback}"

        def load() -> dict[str, object]:
            rows = snapshot.payload.get("results")
            if not isinstance(rows, list):
                raise RuntimeError("买点快照结果格式无效")
            matches = [
                row
                for row in rows
                if isinstance(row, dict)
                and type(row.get("session_age")) is int
                and row["session_age"] <= lookback
            ]
            matches.sort(
                key=lambda row: (row.get("signal_date", ""), row.get("priority", 0), row.get("symbol", "")),
                reverse=True,
            )
            computed_at = snapshot.updated_at or snapshot.created_at
            return {
                "status": "ready",
                "params": dict(params),
                "snapshot": {
                    "id": snapshot.snapshot_id,
                    "asof": snapshot.asof,
                    "computed_at": computed_at.astimezone(timezone.utc).isoformat() if computed_at else None,
                    "algorithm_version": snapshot.algorithm_version,
                    "data_version": snapshot.data_version,
                },
                "total": snapshot.total,
                "processed": snapshot.total,
                "skipped": snapshot.skipped,
                "failed": snapshot.failed,
                "stale": snapshot.payload.get("stale", 0),
                "skip_reasons": snapshot.payload.get("skip_reasons", {}),
                "errors": snapshot.payload.get("errors", []),
                "funnel": snapshot.payload.get("funnel", {}),
                "performance": snapshot.payload.get("performance", {}),
                "results": matches,
                "notice": "结果由后台按行情与算法版本预计算；次开盘仍须经过执行风控。",
            }

        cached = self.infrastructure.cached_json(cache_key, load)
        if not isinstance(cached, dict):
            raise RuntimeError("买点快照缓存格式无效")
        return {str(key): value for key, value in cached.items()}

    def _algorithm_version(self, run: str, variant: str, scenario: str) -> str:
        config = self.repository.strategy_config(run, variant)
        execution = config["scenarios"][scenario]["execution"]
        payload = ["buy-signal-v1", self.repository.buy_scanner.engine, config["strategy"], execution]
        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def _data_version(self, source: str, run: str, asof: str, symbol: str | None) -> str:
        if source == "tdx":
            state = self.repository.structure_scanner.describe_tdx(asof)
            action = fingerprint(self.repository.tdx.root / "T0002/hq_cache/gbbq")
            payload = [state["data_version"], action]
        elif source == "akshare":
            payload = [self.repository.market_data.view("akshare", symbol, asof)["data_version"]]
        else:
            report = self.repository.runs[run]["report"]
            digest = hashlib.sha256(
                json.dumps(report.get("data"), ensure_ascii=True, sort_keys=True, default=str).encode()
            )
            for stock_symbol, bars in sorted(self.repository.bars(run).items()):
                digest.update(stock_symbol.encode())
                for bar in bars:
                    digest.update(
                        json.dumps(
                            [bar.timestamp, bar.open, bar.high, bar.low, bar.close, bar.volume],
                            ensure_ascii=True,
                            separators=(",", ":"),
                            default=str,
                        ).encode()
                    )
            return digest.hexdigest()
        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest()

    @staticmethod
    def _snapshot_id(*parts: object) -> str:
        return hashlib.sha256(
            json.dumps(parts, ensure_ascii=True, separators=(",", ":")).encode()
        ).hexdigest()

    @staticmethod
    def _refresh_result(snapshot: BuySignalSnapshot, status: str) -> dict[str, object]:
        timestamp = snapshot.updated_at or snapshot.created_at or datetime.now(timezone.utc)
        return {
            "status": status,
            "snapshot_id": snapshot.snapshot_id,
            "source": snapshot.source,
            "symbol": snapshot.scope_symbol,
            "asof": snapshot.asof,
            "algorithm_version": snapshot.algorithm_version,
            "data_version": snapshot.data_version,
            "computed_at": timestamp.astimezone(timezone.utc).isoformat(),
            "total": snapshot.total,
            "matches": len(snapshot.payload.get("results", [])),
            "skipped": snapshot.skipped,
            "failed": snapshot.failed,
        }
