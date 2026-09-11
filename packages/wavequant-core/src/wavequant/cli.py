from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from .backtest import run_portfolio
from .config import StrategyConfig
from .data import dump_json, synthetic_dataset
from .io import load_bars, write_signals
from .project_paths import PROJECT_ROOT
from .research import make_signals, run_research, save_result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="通达信主控波浪日线研究（不接实盘）")
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate", help="validate OHLCV CSV")
    validate.add_argument("csv", type=Path)
    for name in ("backtest", "research"):
        p = commands.add_parser(name)
        p.add_argument("csv", type=Path)
        p.add_argument("--config", type=Path)
        p.add_argument("--output-dir", type=Path, default=Path("results") / name)
        if name == "research":
            p.add_argument("--protocol", type=Path, default=Path("configs/research_protocol.json"))
    for name in ("import-tdx", "run-tdx"):
        p = commands.add_parser(name)
        p.add_argument("--tdx-root", type=Path, default=Path("D:/TDX"))
        p.add_argument("--csv", type=Path, default=Path("data/tdx_daily.csv"))
        p.add_argument("--start", default="2018-01-01")
        p.add_argument("--end")
        p.add_argument("--symbols", nargs="+")
        if name == "run-tdx":
            p.add_argument("--config", type=Path)
            p.add_argument("--protocol", type=Path, default=Path("configs/research_protocol.json"))
            p.add_argument("--output-dir", type=Path, default=Path("results/tdx"))
    demo = commands.add_parser("demo", help="offline deterministic engineering fixture, not market evidence")
    demo.add_argument("--output-dir", type=Path, default=Path("results/demo"))
    notion = commands.add_parser("notion-research", help="test fixed hypotheses from the local Notion export")
    notion.add_argument("--csv", type=Path, default=Path("data/tdx_daily.csv"))
    notion.add_argument("--notes-dir", type=Path, default=Path("notes/notion_20260908"))
    notion.add_argument("--protocol", type=Path, default=Path("configs/notion_protocol.json"))
    notion.add_argument("--output-dir", type=Path, default=Path("results/notion_v2"))
    notion.add_argument("--config", type=Path)
    foundation = commands.add_parser(
        "foundation-audit", help="annotate daily foundation relations from the N-shape lecture"
    )
    foundation.add_argument("csv", type=Path)
    foundation.add_argument("--output-dir", type=Path, default=Path("results/foundations"))
    system = commands.add_parser(
        "system-research", help="fixed integrated theory strategy and separately labelled daily proxy backtest"
    )
    system.add_argument("--csv", type=Path, default=Path("data/tdx_system_20260908.csv"))
    system.add_argument("--protocol", type=Path, default=Path("configs/system_squeeze_v4.json"))
    system.add_argument("--output-dir", type=Path, default=Path("results/system_squeeze_v4_20260908"))
    platform = commands.add_parser(
        "platform-audit", help="offline platform acceptance, PIT readiness and fixed-rule validation"
    )
    platform.add_argument("--csv", type=Path, default=Path("data/tdx_system_20260908.csv"))
    platform.add_argument("--protocol", type=Path, default=Path("configs/system_squeeze_v4.json"))
    platform.add_argument("--output-dir", type=Path, default=Path("results/platform_v1_20260908"))
    platform.add_argument("--security-master", type=Path)
    platform.add_argument("--calendar", type=Path)
    platform.add_argument(
        "--engineering-only",
        action="store_true",
        help="skip historical rolling/cost diagnostics, not data readiness checks",
    )
    paper = commands.add_parser(
        "paper-demo", help="deterministic signal/order/restart/reconciliation acceptance; no market evidence"
    )
    paper.add_argument("--output-dir", type=Path, default=Path("results/paper_demo"))
    journal = commands.add_parser("journal-check", help="verify existing SQLite event chain and optionally back up")
    journal.add_argument("database", type=Path)
    journal.add_argument("--backup", type=Path)
    evidence = commands.add_parser(
        "strategy-evidence", help="frozen squeeze challengers and return-blind expanded-panel evidence study"
    )
    evidence.add_argument("--tdx-root", type=Path, default=Path("D:/TDX"))
    evidence.add_argument("--protocol", type=Path, default=Path("configs/squeeze_evidence_v5.json"))
    evidence.add_argument("--output-dir", type=Path, default=Path("results/squeeze_evidence_v5_20260908"))
    evidence.add_argument(
        "--workers", type=int, default=4, help="independent stock calculations; stable sorted reduction, 1-8 processes"
    )
    ops = commands.add_parser("system-run", help="one-shot tested offline operations, diagnostics and recovery drill")
    ops.add_argument("--config", type=Path, default=Path("configs/operations.json"))
    ops.add_argument("--run-id", required=True, help="new immutable run identity; successful IDs verify and return")
    status = commands.add_parser("system-status", help="inspect durable runs, local alerts and artifact integrity")
    status.add_argument("--root", type=Path, default=Path("results/operations_v1"))
    backup = commands.add_parser(
        "system-backup", help="back up quiescent local operations workspace to a NEW directory"
    )
    backup.add_argument("--root", type=Path, default=Path("results/operations_v1"))
    backup.add_argument("--destination", type=Path, required=True)
    restore = commands.add_parser(
        "system-restore", help="verify bundle and restore to a NEW directory; never overwrite"
    )
    restore.add_argument("--bundle", type=Path, required=True)
    restore.add_argument("--destination", type=Path, required=True)
    ack = commands.add_parser("system-alert-ack", help="acknowledge local alert, without clearing the health fault")
    ack.add_argument("--root", type=Path, default=Path("results/operations_v1"))
    ack.add_argument("--id", required=True)
    ack.add_argument("--reason", required=True)
    account = commands.add_parser("account-report", help="as-of FIFO costs, fees, corporate cash and conserved P&L")
    account.add_argument("database", type=Path)
    account.add_argument("--asof", required=True, help="timezone-aware ISO timestamp")
    account.add_argument("--output", type=Path, required=True, help="new JSON report path")
    return parser


def _run_tests(output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    root = PROJECT_ROOT
    tests = root / "tests"
    if not tests.exists() or not list(tests.glob("test_*.py")):
        raise RuntimeError("tests missing; run pipeline from the source checkout")
    result = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", str(tests), "-v"],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    log = result.stdout + result.stderr
    (output / "test_log.txt").write_text(log, encoding="utf-8")
    print(log, flush=True)
    if result.returncode:
        raise RuntimeError("engineering tests failed; research pipeline stopped (see test_log.txt)")
    return dict(status="passed", returncode=0, command="python -m unittest discover -s tests -v", log="test_log.txt")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    args = _parser().parse_args()
    if args.command == "account-report":
        from .account_report import account_report
        from .event_store import EventStore

        if not args.database.is_file() or args.output.exists():
            raise ValueError("existing account and new report path required")
        store = EventStore(args.database, readonly=True)
        try:
            report = account_report(store, args.asof)
        finally:
            store.close()
        dump_json(args.output, report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    if args.command in ("system-run", "system-status", "system-backup", "system-restore", "system-alert-ack"):
        from .operations import OperationsConfig, run_operations, operations_status
        from .backup_bundle import create_bundle, restore_bundle
        from .operational_store import workspace_lock, acknowledge_alert, now
        from .event_store import EventStore

        if args.command == "system-run":
            cfg = OperationsConfig.load(args.config)
            report = run_operations(cfg, args.run_id)
            result = dict(
                report=str(cfg.root / "runs" / args.run_id / "report.md"),
                recovery_drill_passed=report["recovery_drill_passed"],
                production_ready=False,
                readiness=report["readiness"],
            )
        elif args.command == "system-status":
            result = operations_status(args.root)
        elif args.command == "system-restore":
            result = restore_bundle(args.bundle, args.destination)
        else:
            if not (args.root / "operations.sqlite").is_file():
                raise ValueError("existing operations root required")
            with workspace_lock(args.root):
                if args.command == "system-backup":
                    result = create_bundle(args.root, args.destination)
                    result = dict(
                        files=len(result["files"]), destination=str(args.destination.resolve()), verified=True
                    )
                else:
                    store = EventStore(args.root / "operations.sqlite")
                    try:
                        result = acknowledge_alert(store, args.id, args.reason, now())
                    finally:
                        store.close()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    config = StrategyConfig.from_json(args.config) if getattr(args, "config", None) else StrategyConfig()
    if args.command == "strategy-evidence":
        from .strategy_evidence import run_strategy_evidence

        if args.output_dir.exists() and any(args.output_dir.iterdir()):
            raise ValueError("output already contains files; choose a new directory")
        tests = _run_tests(args.output_dir)
        report = run_strategy_evidence(args.tdx_root, args.protocol, args.output_dir, tests, workers=args.workers)
        print(
            json.dumps(
                dict(
                    report=str((args.output_dir / "report.md").resolve()),
                    selected=report["selected_for_forward_observation"],
                    validated=report["validated"],
                    universe=report["universe_summary"],
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if args.command == "platform-audit":
        from .platform_audit import run_platform_audit

        if args.output_dir.exists() and any(args.output_dir.iterdir()):
            raise ValueError("output already contains files; choose a new directory")
        status = _run_tests(args.output_dir)
        report = run_platform_audit(
            args.csv,
            args.protocol,
            args.output_dir,
            status,
            master_path=args.security_master,
            calendar_path=args.calendar,
            with_research=not args.engineering_only,
        )
        print(
            json.dumps(
                dict(
                    report=str((args.output_dir / "report.md").resolve()),
                    capabilities=report["capabilities"],
                    production_ready=report["production_ready"],
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if args.command == "paper-demo":
        from .platform_audit import run_paper_demo

        print(json.dumps(run_paper_demo(args.output_dir), ensure_ascii=False, indent=2))
        return
    if args.command == "journal-check":
        from .event_store import EventStore

        if not args.database.is_file():
            raise ValueError("existing journal required")
        store = EventStore(args.database)
        try:
            result = dict(events=len(store.events()), head=store.verify(), integrity="passed")
            if args.backup:
                store.backup(args.backup)
                result["backup"] = str(args.backup.resolve())
            print(json.dumps(result, ensure_ascii=False, indent=2))
        finally:
            store.close()
        return
    if args.command == "system-research":
        from .system_research import run_system_research

        if args.output_dir.exists() and any(args.output_dir.iterdir()):
            raise ValueError("output already contains files; choose a new directory")
        status = _run_tests(args.output_dir)
        report = run_system_research(args.csv, args.output_dir, args.protocol, status)
        print(
            json.dumps(
                dict(report=str((args.output_dir / "report.html").resolve()), gates=report["gates"]),
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if args.command == "foundation-audit":
        from .foundation_audit import audit_foundations

        status = _run_tests(args.output_dir)
        report = audit_foundations(args.csv, args.output_dir, status)
        print(
            json.dumps(
                dict(
                    report=str((args.output_dir / "report.md").resolve()),
                    rows=report["rows"],
                    annotated_rows=report["annotated_rows"],
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if args.command == "notion-research":
        from .notion import run_notion

        status = _run_tests(args.output_dir)
        report = run_notion(args.csv, args.notes_dir, args.output_dir, args.protocol, config, status)
        print(
            json.dumps(
                dict(
                    report=str((args.output_dir / "report.html").resolve()),
                    selected=report["selected_by_train"],
                    research_gate=report["research_gate"],
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if args.command in ("import-tdx", "run-tdx"):
        from .tdx import import_tdx

        status = _run_tests(args.output_dir) if args.command == "run-tdx" else None
        metadata = import_tdx(args.tdx_root, args.csv, args.start, args.end, args.symbols)
        if args.command == "import-tdx":
            print(json.dumps(metadata, ensure_ascii=False, indent=2))
            return
        report = run_research(args.csv, args.output_dir, config, args.protocol, status)
        print(
            json.dumps(
                dict(
                    report=str((args.output_dir / "report.html").resolve()),
                    data_end=report["data"]["end"],
                    research_gate=report["research_gate"],
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if args.command == "demo":
        path = args.output_dir / "synthetic.csv"
        synthetic_dataset(path, sessions=500, symbols=5)
        grouped = load_bars(path)
        signals = make_signals(grouped, config)
        result = run_portfolio(grouped, signals, config)
        save_result(args.output_dir, result)
        write_signals(args.output_dir / "signals.csv", signals)
        print(json.dumps(dict(kind="synthetic_engineering_only", **result.metrics), indent=2))
        return
    grouped = load_bars(args.csv)
    if args.command == "validate":
        print(
            json.dumps(
                dict(
                    symbols=len(grouped),
                    rows=sum(map(len, grouped.values())),
                    coverage={
                        s: dict(first=b[0].timestamp.isoformat(), last=b[-1].timestamp.isoformat(), rows=len(b))
                        for s, b in grouped.items()
                    },
                ),
                indent=2,
            )
        )
        return
    if args.command == "research":
        report = run_research(args.csv, args.output_dir, config, args.protocol)
        print(json.dumps(report["research_gate"], ensure_ascii=False, indent=2))
        return
    signals = make_signals(grouped, config)
    result = run_portfolio(grouped, signals, config)
    save_result(args.output_dir, result)
    write_signals(args.output_dir / "signals.csv", signals)
    dump_json(
        args.output_dir / "report.json",
        dict(config=config.to_dict(), overall=result.metrics, signal_count=len(signals)),
    )
    print(json.dumps(result.metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
