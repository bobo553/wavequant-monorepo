"""Command-line entry point for the local WaveQuant API."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from wavequant.interfaces.charts.visualization import ChartRepository  # type: ignore[import-untyped]

from .infrastructure import Infrastructure, InfrastructureSettings, ResearchRun
from .server import serve_dashboard


def parser() -> argparse.ArgumentParser:
    """Build the API command-line contract."""
    value = argparse.ArgumentParser(description="WaveQuant local read-only HTTP API")
    value.add_argument("--root", type=Path, default=Path("results/operations_v1"))
    value.add_argument("--port", type=int, default=8765)
    value.add_argument("--tdx-root", type=Path, default=Path("D:/TDX"))
    value.add_argument("--web-root", type=Path)
    value.add_argument("--api-only", action="store_true", help="serve API routes without requiring a built Web workspace")
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


def main() -> None:
    """Start the loopback-only dashboard API."""
    args = parser().parse_args()
    if args.check_infrastructure or args.init_database or args.index_runs:
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
    )


if __name__ == "__main__":
    main()
