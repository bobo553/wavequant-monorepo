"""Command-line entry point for the local WaveQuant API."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
from pathlib import Path
import time

from wavequant.interfaces.charts.visualization import ChartRepository  # type: ignore[import-untyped]

from .application import BuySignalSnapshotService, StructureSnapshotService
from .infrastructure import Infrastructure, InfrastructureSettings, ResearchRun
from .server import serve_dashboard


def default_results_root() -> Path:
    """Resolve the same explicit/local compatibility roots used by Web dev."""
    configured = os.environ.get("WAVEQUANT_RESULTS_ROOT")
    if configured:
        return Path(configured)
    repository_root = Path(__file__).resolve().parents[5]
    packaged = repository_root / "packages" / "wavequant-core" / "results" / "operations_v1"
    legacy = Path(r"E:\WorkSpace\股票\results\operations_v1")
    return packaged if packaged.exists() or not legacy.exists() else legacy


def parser() -> argparse.ArgumentParser:
    """Build the API command-line contract."""
    value = argparse.ArgumentParser(description="WaveQuant local read-only HTTP API")
    value.add_argument("--root", type=Path, default=default_results_root())
    value.add_argument("--port", type=int, default=8765)
    value.add_argument("--tdx-root", type=Path, default=Path("D:/TDX"))
    value.add_argument("--disable-akshare", action="store_true", help="disable the optional AkShare market-data source")
    value.add_argument("--akshare-timeout", type=float, default=30.0, help="AkShare call timeout in seconds (1-60)")
    value.add_argument("--web-root", type=Path)
    value.add_argument(
        "--api-only", action="store_true", help="serve API routes without requiring a built Web workspace"
    )
    value.add_argument(
        "--web-url",
        help="loopback Next.js origin opened when the root of an API-only development server is requested",
    )
    value.add_argument(
        "--allow-origin",
        action="append",
        default=[],
        help="additional exact loopback Web origin accepted by the API proxy",
    )
    actions = value.add_mutually_exclusive_group()
    actions.add_argument("--check-infrastructure", action="store_true", help="check configured SQL and Redis services")
    actions.add_argument("--init-database", action="store_true", help="create the initial SQL schema")
    actions.add_argument("--index-runs", action="store_true", help="upsert the sealed local run catalog into SQL")
    actions.add_argument(
        "--refresh-structures", action="store_true", help="precompute and publish one structure snapshot"
    )
    actions.add_argument(
        "--watch-structures",
        action="store_true",
        help="continuously detect TDX/algorithm versions and refresh structure snapshots",
    )
    actions.add_argument("--refresh-signals", action="store_true", help="precompute and publish signal read models")
    actions.add_argument("--watch-signals", action="store_true", help="watch causal versions and refresh signals")
    value.add_argument("--structure-run", help="sealed run providing the structure strategy configuration")
    value.add_argument("--structure-variant", default="lecture_v1")
    value.add_argument("--structure-asof", help="optional fixed YYYY-MM-DD cutoff; defaults to latest TDX market date")
    value.add_argument("--structure-refresh-interval", type=int, default=300)
    value.add_argument("--signal-family", choices=("all", "buy", "structure"), default="all")
    value.add_argument("--signal-source", choices=("tdx", "snapshot", "akshare"), default="tdx")
    value.add_argument(
        "--signal-symbol",
        action="append",
        default=[],
        help="optional AkShare symbol subset; structure-only refresh defaults to the complete catalog",
    )
    value.add_argument("--signal-shard-count", type=int, default=1, help="AkShare 全目录并行分片总数（1-16）")
    value.add_argument("--signal-shard-index", type=int, default=0, help="当前 AkShare 全目录分片编号（从 0 开始）")
    value.add_argument("--signal-scenario", default="base")
    value.add_argument("--signal-start", default="2018-01-01")
    return value


def index_runs(infrastructure: Infrastructure, catalog: object) -> int:
    """Copy compact sealed-run metadata into the configured shared SQL index."""
    if infrastructure.database is None:
        raise ValueError("WAVEQUANT_DATABASE_URL is not configured")
    if not isinstance(catalog, dict) or not isinstance(catalog.get("runs"), list):
        raise ValueError("WaveQuant catalog does not contain a run list")
    count = 0
    for item in catalog["runs"]:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise ValueError("WaveQuant catalog contains an invalid run")
        payload = {str(key): value for key, value in item.items() if key != "id"}
        infrastructure.database.save(ResearchRun(item["id"], "COMPLETED", payload))
        count += 1
    return count


def _structure_run(repository: ChartRepository, requested: str | None) -> str:
    """Resolve the configured sealed run without duplicating catalog semantics."""
    if requested:
        return requested
    catalog = repository.catalog()
    runs = catalog.get("runs") if isinstance(catalog, dict) else None
    if not isinstance(runs, list) or not runs or not isinstance(runs[0], dict):
        raise ValueError("结构预计算需要至少一个有效封存运行，或显式传入 --structure-run")
    identifier = runs[0].get("id")
    if not isinstance(identifier, str):
        raise ValueError("结构预计算需要至少一个有效封存运行，或显式传入 --structure-run")
    return identifier


def _akshare_scope_priority(scope: str | None) -> int:
    """Build the default selected markets before slower optional boards."""
    if scope is None:
        return 5
    normalized = scope.lower()
    code = normalized.partition(".")[2]
    if normalized.startswith("sh."):
        return 3 if code.startswith(("688", "689")) else 0
    if normalized.startswith("sz."):
        return 2 if code.startswith(("300", "301")) else 1
    if normalized.startswith("bj."):
        return 4
    return 5


def _akshare_scope_shard(scope: str | None, count: int) -> int:
    """Assign a symbol to one deterministic worker partition."""
    encoded = (scope or "").encode()
    return int.from_bytes(hashlib.sha256(encoded).digest()[:8], "big") % count


def refresh_structures(
    infrastructure: Infrastructure,
    repository: ChartRepository,
    *,
    run: str | None,
    variant: str,
    asof: str | None,
) -> dict[str, object]:
    """Run one idempotent refresh used by both cron and the resident worker."""
    return StructureSnapshotService(repository, infrastructure).refresh(
        _structure_run(repository, run),
        variant,
        asof=asof,
    )


def refresh_signals(
    infrastructure: Infrastructure,
    repository: ChartRepository,
    *,
    run: str | None,
    variant: str,
    scenario: str,
    source: str,
    symbols: list[str],
    start: str,
    asof: str | None,
    family: str,
    shard_count: int = 1,
    shard_index: int = 0,
) -> list[dict[str, object]]:
    """Refresh independently published scopes so interrupted runs are resumable."""
    if not 1 <= shard_count <= 16 or not 0 <= shard_index < shard_count:
        raise ValueError("signal shard must use count 1-16 and index 0..count-1")
    if (shard_count, shard_index) != (1, 0) and (source != "akshare" or symbols or family != "structure"):
        raise ValueError("signal sharding only supports the AkShare full-catalog structure worker")
    resolved_run = _structure_run(repository, run)
    scopes: list[str | None]
    if source == "akshare":
        if symbols:
            scopes = list(dict.fromkeys(symbols))
        elif family == "structure":
            catalog = repository.market_data.catalog("akshare")
            stocks = catalog.get("stocks")
            if not isinstance(stocks, list):
                raise ValueError("AkShare 目录未返回股票列表")
            scopes = list(
                dict.fromkeys(
                    stock["symbol"]
                    for stock in stocks
                    if isinstance(stock, dict)
                    and isinstance(stock.get("symbol"), str)
                    and stock.get("catalog_source", "akshare") == "akshare"
                )
            )
            if not scopes:
                raise ValueError("AkShare 目录没有可预计算股票")
        else:
            raise ValueError("AkShare 买点预计算必须至少传一个 --signal-symbol")
    else:
        if symbols:
            raise ValueError("--signal-symbol 只用于 AkShare")
        scopes = [None]
    selected_asof = asof
    if source == "akshare" and selected_asof is None:
        catalog = repository.market_data.catalog("akshare")
        latest = catalog.get("latest")
        reference_symbol = scopes[0] if scopes else None
        if not isinstance(latest, str) or not isinstance(reference_symbol, str):
            raise ValueError("AkShare 目录未返回可解析的最新行情范围")
        reference = repository.market_data.view("akshare", reference_symbol, latest)
        resolved_asof = reference.get("asof")
        if not isinstance(resolved_asof, str):
            raise ValueError("AkShare 参考股票未返回有效行情日期")
        selected_asof = resolved_asof
    results: list[dict[str, object]] = []
    structure_service = StructureSnapshotService(repository, infrastructure) if family in {"all", "structure"} else None
    buy_service = BuySignalSnapshotService(repository, infrastructure) if family in {"all", "buy"} else None
    database = getattr(infrastructure, "database", None)
    if source == "akshare" and family in {"all", "structure"} and not symbols:
        scopes = [scope for scope in scopes if _akshare_scope_shard(scope, shard_count) == shard_index]
        published: set[str | None] = set()
        if selected_asof is not None and structure_service is not None and database is not None:
            published = {
                snapshot.scope_symbol
                for snapshot in database.list_structure_snapshots(
                    resolved_run,
                    None,
                    source,
                    selected_asof,
                    structure_service.algorithm_version,
                )
                if snapshot.scope_symbol is not None
            }
        # A market date and algorithm version are immutable publication
        # partitions. Rebuild only missing scopes; a new date or algorithm
        # naturally has an empty published set. This keeps the resident
        # workers idle after completion instead of saturating the provider and
        # delaying interactive catalog requests.
        scopes = [scope for scope in scopes if scope not in published]
        # Main boards and ChiNext match the default UI scope, so build them
        # before STAR and Beijing without excluding either optional market.
        scopes.sort(key=_akshare_scope_priority)
    for symbol in scopes:
        if family in {"all", "structure"}:
            if source == "snapshot":
                raise ValueError("结构快照 Worker 当前支持 tdx 或 akshare")
            if structure_service is None:
                raise RuntimeError("结构预计算服务未初始化")
            try:
                results.append(
                    structure_service.refresh(resolved_run, variant, source=source, symbol=symbol, asof=selected_asof)
                )
            except (ValueError, RuntimeError, OSError) as exc:
                if source != "akshare":
                    raise
                results.append(
                    {
                        "status": "failed",
                        "family": "structure",
                        "source": source,
                        "symbol": symbol,
                        "error": str(exc),
                    }
                )
        if family in {"all", "buy"}:
            if buy_service is None:
                raise RuntimeError("买点预计算服务未初始化")
            try:
                results.append(
                    buy_service.refresh(
                        resolved_run,
                        variant,
                        scenario,
                        source,
                        start,
                        symbol=symbol,
                        asof=selected_asof,
                    )
                )
            except (ValueError, RuntimeError, OSError) as exc:
                if source != "akshare":
                    raise
                results.append(
                    {
                        "status": "failed",
                        "family": "buy",
                        "source": source,
                        "symbol": symbol,
                        "error": str(exc),
                    }
                )
    return results


def main() -> None:
    """Start the loopback-only dashboard API."""
    args = parser().parse_args()
    if not 1 <= args.akshare_timeout <= 60:
        raise ValueError("--akshare-timeout must be between 1 and 60 seconds")
    if (
        args.check_infrastructure
        or args.init_database
        or args.index_runs
        or args.refresh_structures
        or args.watch_structures
        or args.refresh_signals
        or args.watch_signals
    ):
        infrastructure = Infrastructure.from_settings(InfrastructureSettings.from_env())
        try:
            if args.init_database:
                infrastructure.initialize_database()
                print("WaveQuant database schema initialized.")
                return
            if args.index_runs:
                count = index_runs(infrastructure, ChartRepository(args.root).catalog())
                print(f"Indexed {count} sealed WaveQuant run(s).")
                return
            if args.refresh_structures or args.watch_structures or args.refresh_signals or args.watch_signals:
                if not 60 <= args.structure_refresh_interval <= 86_400:
                    raise ValueError("--structure-refresh-interval must be between 60 and 86400 seconds")
                repository = ChartRepository(
                    args.root,
                    tdx_root=args.tdx_root,
                    akshare_enabled=not args.disable_akshare,
                    akshare_timeout=args.akshare_timeout,
                )
                while True:
                    try:
                        result = (
                            refresh_structures(
                                infrastructure,
                                repository,
                                run=args.structure_run,
                                variant=args.structure_variant,
                                asof=args.structure_asof,
                            )
                            if args.refresh_structures or args.watch_structures
                            else refresh_signals(
                                infrastructure,
                                repository,
                                run=args.structure_run,
                                variant=args.structure_variant,
                                scenario=args.signal_scenario,
                                source=args.signal_source,
                                symbols=args.signal_symbol,
                                start=args.signal_start,
                                asof=args.structure_asof,
                                family=args.signal_family,
                                shard_count=args.signal_shard_count,
                                shard_index=args.signal_shard_index,
                            )
                        )
                        print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
                    except (ValueError, RuntimeError, OSError):
                        if args.refresh_structures or args.refresh_signals:
                            raise
                        logging.exception(
                            "structure snapshot refresh failed; the last published snapshot remains active"
                        )
                    if args.refresh_structures or args.refresh_signals:
                        return
                    time.sleep(args.structure_refresh_interval)
            health = infrastructure.health()
            print(json.dumps(health, ensure_ascii=False, sort_keys=True))
            if health["status"] == "degraded":
                raise SystemExit(1)
            return
        finally:
            infrastructure.close()
    serve_dashboard(
        args.root,
        args.port,
        args.tdx_root,
        args.web_root,
        serve_static=not args.api_only,
        allowed_origins=tuple(args.allow_origin),
        web_url=args.web_url,
        akshare_enabled=not args.disable_akshare,
        akshare_timeout=args.akshare_timeout,
    )


if __name__ == "__main__":
    main()
