"""Build and query the durable structure-signal read model."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from datetime import datetime, timezone
import hashlib
import json
from time import perf_counter
from typing import Any

from wavequant.interfaces.screening.structure_scanner import LANDMARK_FIELDS  # type: ignore[import-untyped]

from ..infrastructure import Infrastructure, StructureSnapshot


STRUCTURE_MARKET_ORDER = ("shanghai", "shenzhen", "chinext", "star", "beijing")
DEFAULT_STRUCTURE_MARKETS = ("shanghai", "shenzhen", "chinext")
STRUCTURE_MARKETS = frozenset(STRUCTURE_MARKET_ORDER)


class StructureSnapshotUnavailable(RuntimeError):
    """Raised when no completed snapshot exists for the requested causal version."""


class StructureSnapshotService:
    """Keep slow full-market calculation outside the interactive request path.

    A refresh computes the widest supported contract once (both signal types,
    all levels, 20 sessions).  Query-time filters only narrow that immutable
    result.  Publication happens in one SQL transaction after Core reports a
    completed job, so a failed refresh cannot replace the previous snapshot.
    """

    def __init__(self, repository: Any, infrastructure: Infrastructure):
        self.repository = repository
        self.infrastructure = infrastructure
        # Resolve code identity once at process start. Interactive queries do
        # not hash source files or invoke any Core calculation.
        self.algorithm_version = repository.structure_scanner.algorithm_version()

    def refresh(
        self,
        run: str,
        variant: str,
        *,
        source: str = "tdx",
        symbol: str | None = None,
        asof: str | None = None,
    ) -> dict[str, object]:
        database = self.infrastructure.database
        if database is None:
            raise ValueError("结构预计算需要配置 WAVEQUANT_DATABASE_URL")
        scanner = self.repository.structure_scanner
        if source not in {"tdx", "akshare"}:
            raise ValueError("结构预计算仅支持 tdx 或 akshare")
        if (source == "akshare") != (symbol is not None):
            raise ValueError("AkShare 结构预计算需要且只允许一个 symbol")
        if asof is not None:
            selected_asof = asof
        elif source == "tdx":
            selected_asof = scanner.latest_tdx_session()
        else:
            catalog_latest = self.repository.market_data.catalog("akshare").get("latest")
            if not isinstance(catalog_latest, str):
                raise RuntimeError("AkShare 目录未返回有效最新日期")
            selected_asof = catalog_latest
        source_state = (
            scanner.describe_tdx(selected_asof)
            if source == "tdx"
            else self.repository.market_data.view("akshare", symbol, selected_asof)
        )
        if source == "akshare":
            resolved_asof = source_state.get("asof")
            if not isinstance(resolved_asof, str):
                raise RuntimeError("AkShare 未返回有效行情日期")
            selected_asof = resolved_asof
        algorithm_version = self.algorithm_version
        data_version = source_state["data_version"]
        existing = database.find_structure_snapshot(
            run,
            variant,
            source,
            selected_asof,
            algorithm_version,
            scope_symbol=symbol,
            data_version=data_version,
        )
        if existing is not None:
            return self._refresh_result(existing, "current")

        snapshot_id = self._snapshot_id(run, variant, source, symbol, selected_asof, algorithm_version, data_version)
        started = perf_counter()
        request = {
            "run": run,
            "variant": variant,
            "source": source,
            "asof": selected_asof,
            "lookback": 20,
            "signal_type": "any",
            "trend_level": 0,
        }
        if symbol is not None:
            request["symbol"] = symbol
        if source == "tdx":
            job = scanner.start(request)
            while job["status"] in ("running", "cancelling"):
                job = scanner.get(job["id"], after=job["revision"], timeout=20)
            if job["status"] != "completed":
                raise RuntimeError(job.get("error") or f"结构预计算未完成：{job['status']}")
            if job.get("algorithm_version") != algorithm_version or job.get("data_version") != data_version:
                raise RuntimeError("结构预计算期间行情或算法版本变化，未发布混合版本结果")
        else:
            job = scanner.current_akshare(request)
            after = self.repository.market_data.view("akshare", symbol, selected_asof)
            if after.get("data_version") != data_version:
                raise RuntimeError("结构预计算期间行情版本变化，未发布混合版本结果")
        payload = {
            "results": job["results"],
            "stale": job["stale"],
            "skip_reasons": job["skip_reasons"],
            "errors": job["errors"],
            "elapsed_seconds": perf_counter() - started,
        }
        stored = database.save_structure_snapshot(
            StructureSnapshot(
                snapshot_id=snapshot_id,
                run_id=run,
                variant=variant,
                source=source,
                scope_symbol=symbol,
                asof=selected_asof,
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
        """Return a bounded filter over SQL/Redis without invoking Core theory."""
        expected = {"run", "variant", "source", "asof", "lookback", "signal_type", "trend_level"}
        optional = {"symbol", "markets"}
        if not expected.issubset(params) or not set(params).issubset(expected | optional):
            raise ValueError("invalid precomputed structure query")
        query_params = dict(params)
        compatibility_symbol = query_params.pop("symbol", None)
        selected_markets = self._parse_markets(query_params.get("markets"))
        market_key = ",".join(selected_markets)
        query_params["markets"] = market_key
        run = params["run"]
        variant = params["variant"]
        source = params["source"]
        asof = params["asof"]
        lookback = params["lookback"]
        signal_type = params["signal_type"]
        trend_level = params["trend_level"]
        if (
            not isinstance(run, str)
            or not isinstance(variant, str)
            or not isinstance(source, str)
            or not isinstance(asof, str)
            or not isinstance(signal_type, str)
        ):
            raise ValueError("invalid precomputed structure query values")
        if source not in {"tdx", "akshare"}:
            raise ValueError("invalid structure signal source")
        if compatibility_symbol is not None and (source != "akshare" or not isinstance(compatibility_symbol, str)):
            raise ValueError("symbol is only accepted for legacy AkShare clients")
        if type(lookback) is not int or lookback not in (1, 5, 20):
            raise ValueError("lookback must be 1, 5 or 20")
        if type(trend_level) is not int or trend_level not in (0, 1, 2, 3):
            raise ValueError("trend level must be 0, 1, 2 or 3")
        if signal_type not in {"any", *LANDMARK_FIELDS}:
            raise ValueError("invalid structure signal type")
        database = self.infrastructure.database
        if database is None:
            raise StructureSnapshotUnavailable("结构读模型未配置；请启动 MySQL/PostgreSQL 并运行结构预计算 Worker")
        if source == "akshare":
            # Structure landmarks depend on market data and the structure
            # engine, not on the selected buy-strategy profile. Reuse shards
            # across variants to avoid false unavailable states.
            shards = database.list_structure_snapshots(run, None, source, asof, self.algorithm_version)
            if not shards:
                raise StructureSnapshotUnavailable(
                    "AkShare 市场结构快照尚未生成；请保持结构预计算 Worker 运行，已完成股票会自动发布"
                )
            market_snapshot = self._aggregate_akshare_shards(run, variant, asof, shards)
        else:
            stored_snapshot = database.find_structure_snapshot(run, variant, source, asof, self.algorithm_version)
            if stored_snapshot is None:
                raise StructureSnapshotUnavailable(
                    "该行情日或当前算法版本尚无完成快照；后台 Worker 将在检测到变化后自动重建"
                )
            market_snapshot = stored_snapshot
        snapshot = market_snapshot

        cache_key = (
            f"signal:structure:v3:{snapshot.snapshot_id}:{signal_type}:{trend_level}:{lookback}:{market_key}"
        )

        def load() -> dict[str, object]:
            rows = snapshot.payload.get("results")
            if not isinstance(rows, list):
                raise RuntimeError("结构快照结果格式无效")
            matches = [
                row
                for row in rows
                if isinstance(row, dict)
                and self._row_is_in_selected_market(row, selected_markets)
                and not self._has_starred_name(row)
                and (signal_type == "any" or row.get("signal_type") == signal_type)
                and (trend_level == 0 or row.get("trend_level") == trend_level)
                and type(row.get("session_age")) is int
                and row["session_age"] <= lookback
            ]
            matches.sort(
                key=lambda row: (
                    row["available_at"],
                    row["trend_level"],
                    row["event_date"],
                    row["symbol"],
                    row["id"],
                ),
                reverse=True,
            )
            computed_at = snapshot.updated_at or snapshot.created_at
            return {
                "status": "ready",
                "params": query_params,
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
                "results": matches,
                "matched_stocks": len({row["symbol"] for row in matches}),
                "coverage": {
                    "scope": "akshare_market" if source == "akshare" else "tdx_market",
                    "published_stocks": snapshot.total,
                },
                "filters": {
                    "markets": list(selected_markets),
                    "excluded_name_markers": ["*", "＊"],
                },
                "notice": "结果由后台按行情与算法版本预计算；结构出现不构成买卖建议。",
            }

        cached = self.infrastructure.cached_json(cache_key, load)
        if not isinstance(cached, dict):
            raise RuntimeError("结构快照缓存格式无效")
        return {str(key): value for key, value in cached.items()}

    @staticmethod
    def _parse_markets(raw: object) -> tuple[str, ...]:
        if raw is None:
            return DEFAULT_STRUCTURE_MARKETS
        if not isinstance(raw, str):
            raise ValueError("markets must be a comma-separated string")
        requested = raw.split(",")
        if not requested or any(not market for market in requested):
            raise ValueError("至少选择一个结构市场")
        if len(set(requested)) != len(requested) or not set(requested).issubset(STRUCTURE_MARKETS):
            raise ValueError("invalid structure markets")
        return tuple(market for market in STRUCTURE_MARKET_ORDER if market in requested)

    @staticmethod
    def _row_is_in_selected_market(row: dict[str, object], selected: tuple[str, ...]) -> bool:
        symbol = row.get("symbol")
        if not isinstance(symbol, str):
            return False
        normalized = symbol.lower()
        code = normalized.partition(".")[2]
        if normalized.startswith("bj."):
            market = "beijing"
        elif normalized.startswith("sh."):
            market = "star" if code.startswith(("688", "689")) else "shanghai"
        elif normalized.startswith("sz."):
            market = "chinext" if code.startswith(("300", "301")) else "shenzhen"
        else:
            return False
        return market in selected

    @staticmethod
    def _has_starred_name(row: dict[str, object]) -> bool:
        name = row.get("name")
        return isinstance(name, str) and ("*" in name or "＊" in name)

    def _aggregate_akshare_shards(
        self,
        run: str,
        variant: str,
        asof: str,
        shards: Sequence[StructureSnapshot],
    ) -> StructureSnapshot:
        """Build an immutable market view from independently published shards."""
        ordered = sorted(shards, key=lambda item: item.scope_symbol or "")
        results: list[object] = []
        errors: list[object] = []
        skip_reasons: Counter[str] = Counter()
        stale = 0
        for shard in ordered:
            shard_results = shard.payload.get("results", [])
            if isinstance(shard_results, list):
                results.extend(shard_results)
            shard_errors = shard.payload.get("errors", [])
            if isinstance(shard_errors, list):
                errors.extend(shard_errors)
            reasons = shard.payload.get("skip_reasons", {})
            if isinstance(reasons, dict):
                for reason, count in reasons.items():
                    if isinstance(reason, str) and type(count) is int:
                        skip_reasons[reason] += count
            shard_stale = shard.payload.get("stale", 0)
            if type(shard_stale) is int:
                stale += shard_stale
        identity = [[shard.scope_symbol, shard.snapshot_id] for shard in ordered]
        snapshot_id = hashlib.sha256(
            json.dumps(identity, ensure_ascii=True, separators=(",", ":")).encode()
        ).hexdigest()
        data_version = hashlib.sha256(
            json.dumps(
                [[shard.scope_symbol, shard.data_version] for shard in ordered],
                ensure_ascii=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        timestamps = [stamp for shard in ordered if (stamp := shard.updated_at or shard.created_at) is not None]
        computed_at = max(timestamps) if timestamps else datetime.now(timezone.utc)
        return StructureSnapshot(
            snapshot_id=snapshot_id,
            run_id=run,
            variant=variant,
            source="akshare",
            asof=asof,
            algorithm_version=self.algorithm_version,
            data_version=data_version,
            payload={
                "results": results,
                "errors": errors[:30],
                "skip_reasons": dict(skip_reasons),
                "stale": stale,
            },
            total=len(ordered),
            skipped=sum(shard.skipped for shard in ordered),
            failed=sum(shard.failed for shard in ordered),
            updated_at=computed_at,
        )

    @staticmethod
    def _snapshot_id(
        run: str,
        variant: str,
        source: str,
        symbol: str | None,
        asof: str,
        algorithm_version: str,
        data_version: str,
    ) -> str:
        encoded = json.dumps(
            [run, variant, source, symbol, asof, algorithm_version, data_version],
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _refresh_result(snapshot: StructureSnapshot, status: str) -> dict[str, object]:
        timestamp = snapshot.updated_at or snapshot.created_at or datetime.now(timezone.utc)
        return {
            "status": status,
            "snapshot_id": snapshot.snapshot_id,
            "asof": snapshot.asof,
            "algorithm_version": snapshot.algorithm_version,
            "data_version": snapshot.data_version,
            "computed_at": timestamp.astimezone(timezone.utc).isoformat(),
            "total": snapshot.total,
            "matches": len(snapshot.payload.get("results", [])),
            "skipped": snapshot.skipped,
            "failed": snapshot.failed,
        }
