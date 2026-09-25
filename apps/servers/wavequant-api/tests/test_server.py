"""Independent sealed fixtures: no dependency on the user's local market data."""

from datetime import datetime, timezone
from contextlib import nullcontext
import csv
import hashlib
from http.client import HTTPConnection
import json
from pathlib import Path
import tempfile
from threading import Event, Thread
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from wavequant.infrastructure.market_data.akshare import AkShareUnavailable
from wavequant.infrastructure.persistence.event_store import EventStore
from wavequant.interfaces.charts.visualization import ChartRepository, day, metrics_at, confirmed_polyline_segments
from wavequant_api.infrastructure import (
    Infrastructure,
    InfrastructureSettings,
    ResearchRunRepository,
    StructureSnapshot,
)
from wavequant_api.server import make_server
from wavequant_api.backtest_jobs import BacktestJobs


class VisualizationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.run = self.root / "runs" / "example"
        self.run.mkdir(parents=True)
        self.write_json(
            "report.json",
            dict(data=dict(start="2026-01-01", end="2026-01-03", rows=3), readiness=[], recovery_drill_passed=True),
        )
        self.write_json(
            "research/report.json",
            {
                "variants": {
                    "proxy_full": {"strategy": {}, "scenarios": {"base": {"execution": {"initial_capital": 1000}}}}
                }
            },
        )
        self.write_csv(
            "snapshot/daily.csv",
            [
                dict(
                    timestamp=f"2026-01-0{i}",
                    symbol="TEST",
                    open=10,
                    high=11,
                    low=9,
                    close=10,
                    volume=1000,
                    buyable="true",
                    sellable="no",
                    adjustment_factor=2,
                )
                for i in (1, 2, 3)
            ],
        )
        self.write_csv(
            "research/proxy_full/base/equity.csv",
            [
                dict(timestamp=f"2026-01-0{i}", equity=value, cash=500, exposure=0.5)
                for i, value in ((1, 950), (2, 1010), (3, 1100))
            ],
        )
        self.write_csv(
            "research/proxy_full/base/orders.csv",
            [
                dict(timestamp=f"2026-01-0{i}", symbol="TEST", side=side, status="filled", price=10, reason="test")
                for i, side in ((1, "BUY"), (3, "SELL"))
            ],
        )
        self.write_csv(
            "research/proxy_full/base/trades.csv",
            [dict(symbol="TEST", entry_time="2026-01-01", exit_time="2026-01-03", pnl=100)],
        )
        self.write_csv(
            "research/proxy_full/signals.csv",
            [
                dict(timestamp=f"2026-01-0{i}", symbol="TEST", side="LONG", reference_price=10, reason="test")
                for i in (1, 3)
            ],
        )
        manifest = {
            p.relative_to(self.run).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in self.run.rglob("*")
            if p.is_file()
        }
        self.write_json("artifacts.json", manifest)
        store = EventStore(self.root / "operations.sqlite")
        with store.transaction():
            store.append("start", "RUN_STARTED", datetime.now(timezone.utc), dict(run_id="example"))
            store.append(
                "finish",
                "RUN_FINISHED",
                datetime.now(timezone.utc),
                dict(
                    run_id="example",
                    status="COMPLETED",
                    artifacts_sha256=hashlib.sha256((self.run / "artifacts.json").read_bytes()).hexdigest(),
                ),
            )
        store.close()
        self.repo = ChartRepository(self.root)

    def write_json(self, name, data):
        path = self.run / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")

    def write_csv(self, name, rows):
        path = self.run / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

    def test_prefix_excludes_future_exit_pnl_signals_and_bars(self):
        v = self.repo.view("example", "proxy_full", "TEST", "2026-01-02")
        self.assertEqual(len(v["bars"]), 2)
        self.assertEqual(v["trades"], [])
        self.assertEqual(len(v["orders"]), 1)
        self.assertEqual(len(v["signals"]), 1)
        self.assertIsNone(v["metrics"]["win_rate"])
        self.assertEqual(v["metrics"]["trades"], 0)
        self.assertTrue(all(x["time"] <= "2026-01-02" for x in v["curve"] + v["markers"]))
        self.assertEqual(v["bars"][0]["raw_close"], 5)

    def test_live_lecture_profile_cannot_relabel_sealed_portfolio(self):
        with self.assertRaisesRegex(ValueError, "旧封存组合"):
            self.repo.view("example", "lecture_v1", "TEST", "2026-01-03")
        with patch.object(
            self.repo,
            "_json",
            return_value={
                "variants": {
                    "strict_full": {
                        "strategy": {},
                        "scenarios": {"base": {"execution": {"initial_capital": 1000}, "metrics": {"trades": 99}}},
                        "folds": [{"trades": 99}],
                    }
                }
            },
        ):
            c = self.repo.strategy_config("example", "lecture_v1")
            self.assertEqual(c["strategy"]["pivot_mode"], "lecture_causal")
            self.assertNotIn("folds", c)
            self.assertNotIn("metrics", c["scenarios"]["base"])

    def test_closed_trade_available_on_exit_session_only(self):
        v = self.repo.view("example", "proxy_full", "TEST", "2026-01-03")
        self.assertEqual(len(v["trades"]), 1)
        self.assertEqual(v["metrics"]["win_rate"], 1)
        self.assertAlmostEqual(v["metrics"]["total_return"], 0.1)

    def test_whole_wave_profiles_are_distinct_and_do_not_inherit_results(self):
        from wavequant.domain.strategies.strategy_profiles import WAVE_PROFILES
        from wavequant.interfaces.charts.visualization import VARIANTS

        source = {
            "variants": {
                "strict_full": {
                    "strategy": {},
                    "scenarios": {"base": {"execution": {"initial_capital": 1000}, "metrics": {"trades": 99}}},
                    "folds": [99],
                }
            }
        }
        with patch.object(self.repo, "_json", return_value=source):
            configs = [self.repo.strategy_config("example", v) for v in WAVE_PROFILES]
        self.assertEqual(len({json.dumps(c["strategy"], sort_keys=True) for c in configs}), len(WAVE_PROFILES))
        for v, c in zip(WAVE_PROFILES, configs):
            self.assertIn(v, VARIANTS)
            self.assertNotIn("folds", c)
            self.assertNotIn("metrics", c["scenarios"]["base"])
            with self.assertRaisesRegex(ValueError, "旧封存组合"):
                self.repo.view("example", v, "TEST", "2026-01-03")

    def test_exit_signals_and_order_status_not_conflated(self):
        original = self.repo._csv

        def rows(rid, path):
            values = original(rid, path)
            if path.endswith("/signals.csv"):
                values[-1]["side"] = "EXIT"
            if path.endswith("/orders.csv"):
                values[-1]["status"] = "deferred"
            return values

        with patch.object(self.repo, "_csv", side_effect=rows):
            markers = self.repo.view("example", "proxy_full", "TEST", "2026-01-03")["markers"]
        self.assertEqual(len({m["id"] for m in markers}), len(markers))
        exit_marker = next(m for m in markers if m["side"] == "EXIT")
        self.assertEqual(exit_marker["kind"], "signal")
        self.assertIsNone(exit_marker["stop"])
        order = next(m for m in markers if m["side"] == "SELL")
        self.assertEqual(order["kind"], "order")
        self.assertEqual(order["status"], "deferred")

    def test_n_rule_levels_reuse_existing_projection_and_known_date(self):
        from wavequant.domain.models.model import Bar
        from types import SimpleNamespace

        bars = [
            Bar(datetime(2026, 1, i), "TEST", o, h, l, c, 1000)
            for i, o, h, l, c in [(1, 9, 10, 8, 9), (2, 11, 12, 10, 11), (3, 10, 11, 9, 10), (4, 12, 14, 11, 13)]
        ]
        result = SimpleNamespace(
            audit=[
                dict(
                    event="n_completed",
                    timestamp="2026-01-04",
                    bar_index=3,
                    known_at=3,
                    direction="up",
                    origin=0,
                    neckline=1,
                    pullback=2,
                    defense=10,
                )
            ],
            counts={},
        )
        with (
            patch.object(self.repo, "selection", return_value=bars),
            patch("wavequant.interfaces.charts.visualization.generate_system_signals", return_value=result),
            patch(
                "wavequant.interfaces.charts.visualization.pivot_history",
                return_value=([[], [], [], []], [0, 0, 0, 0], [], set()),
            ),
        ):
            theory = self.repo.theory("example", "proxy_full", "TEST", "2026-01-04")
        event = theory["events"][0]
        levels = {v["name"]: v["price"] for v in event["levels"]}
        self.assertEqual(event["available_at"], "2026-01-04")
        self.assertEqual(event["price"], 13)
        self.assertEqual(levels["轧空低"], 10)
        self.assertEqual(levels["颈线（高低点）"], 12)
        self.assertEqual(levels["颈线（前波收盘）"], 11)
        self.assertEqual(levels["等浪投影"], 13)
        self.assertEqual(levels["1P 投影"], 20)
        self.assertEqual(levels["2T 投影"], 26)

    def test_initial_capital_is_drawdown_peak(self):
        v = self.repo.view("example", "proxy_full", "TEST", "2026-01-01")
        self.assertAlmostEqual(v["metrics"]["max_drawdown"], -0.05)

    def test_empty_metrics_are_explicit(self):
        metrics, curve = metrics_at([], [], 1000)
        self.assertIsNone(metrics["win_rate"])
        self.assertEqual(curve, [])
        self.assertEqual(metrics["equity"], 1000)

    def test_stock_account_does_not_relabel_portfolio_results(self):
        portfolio = self.repo.view("example", "proxy_full", "TEST", "2026-01-03")
        stock = self.repo.stock_view("example", "proxy_full", "TEST", "2026-01-03")
        self.assertEqual(portfolio["metrics"]["trades"], 1)
        self.assertEqual(stock["metrics"]["trades"], 0)
        self.assertEqual(stock["orders"], [])
        self.assertEqual(stock["result_scope"], "stock")
        self.assertEqual(stock["backtest"]["initial_capital"], 1000)
        self.assertTrue(all(p["time"] <= "2026-01-03" for p in stock["curve"]))
        self.assertEqual(
            self.repo.stock_summary("example", "proxy_full", "2026-01-03")["results"][0]["metrics"], stock["metrics"]
        )

    def test_single_stock_signals_receive_only_selected_prefix(self):
        from types import SimpleNamespace

        with patch(
            "wavequant.interfaces.charts.visualization.generate_system_signals",
            return_value=SimpleNamespace(signals=[], counts={}),
        ) as generate:
            self.repo.stock_view("example", "proxy_full", "TEST", "2026-01-02")
            self.assertEqual(len(generate.call_args.args[0]), 2)

    def test_invalid_selection_never_exposes_another_path(self):
        cases = [
            ("../example", "proxy_full", "TEST", "2026-01-02", "base"),
            ("example", "../proxy_full", "TEST", "2026-01-02", "base"),
            ("example", "proxy_full", "OTHER", "2026-01-02", "base"),
            ("example", "proxy_full", "TEST", "2025-12-31", "base"),
            ("example", "proxy_full", "TEST", "2026-01-04", "base"),
            ("example", "proxy_full", "TEST", "20260102", "base"),
            ("example", "proxy_full", "TEST", "2026-01-02", "../base"),
        ]
        for args in cases:
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.repo.selection(*args)

    def test_boolean_parser_matches_strategy_loader(self):
        b = self.repo.bars("example")["TEST"][0]
        self.assertTrue(b.buyable)
        self.assertFalse(b.sellable)

    def test_first_read_detects_tampered_data(self):
        with (self.run / "snapshot/daily.csv").open("a") as f:
            f.write("\n")
        with self.assertRaisesRegex(ValueError, "sealed input"):
            self.repo.bars("example")

    def test_startup_detects_tampered_seal(self):
        self.write_json("artifacts.json", {})
        with self.assertRaises(ValueError):
            ChartRepository(self.root)

    def test_cached_verified_bytes_do_not_read_replacements(self):
        self.repo.bars("example")
        (self.run / "snapshot/daily.csv").write_text("corrupt", encoding="utf-8")
        self.assertEqual(len(self.repo.bars("example")["TEST"]), 3)

    def test_theory_engine_receives_only_the_selected_prefix(self):
        from types import SimpleNamespace

        result = SimpleNamespace(
            audit=[
                dict(event="n_forming", timestamp="2026-01-01", bar_index=0),
                dict(event="future", timestamp="2026-01-01", bar_index=0, known_at=2),
            ],
            counts={},
        )
        with (
            patch("wavequant.interfaces.charts.visualization.generate_system_signals", return_value=result) as engine,
            patch(
                "wavequant.interfaces.charts.visualization.pivot_history", return_value=([[], []], [0, 0], [], set())
            ),
        ):
            theory = self.repo.theory("example", "proxy_full", "TEST", "2026-01-02")
        self.assertEqual(len(engine.call_args.args[0]), 2)
        self.assertEqual([e["event"] for e in theory["events"]], ["n_forming"])

    def test_session_timezone_is_shanghai(self):
        self.assertEqual(day("2026-01-01T23:00:00+00:00"), "2026-01-02")

    def test_polyline_preserves_old_segments_without_bridging_ambiguity(self):
        from types import SimpleNamespace as S
        from wavequant.domain.market_structure.polyline import PointKind

        bars = [S(timestamp=datetime(2026, 1, i)) for i in range(1, 7)]

        def pivot(i, known):
            return S(point=S(index=i, price=10 + i, kind=PointKind.LOW), confirmed_index=known)

        a, b, c, d, future = pivot(0, 1), pivot(1, 1), pivot(3, 4), pivot(4, 4), pivot(5, 6)
        snapshots = [[], [a, b], [a, b], [], [c, d], [c, d, future]]
        segments = confirmed_polyline_segments(bars, snapshots, [0, 0, 0, 3, 3, 3], {2})
        self.assertEqual([s["id"] for s in segments], ["epoch-0", "epoch-3"])
        self.assertEqual(
            [[p["time"] for p in s["points"]] for s in segments],
            [["2026-01-01", "2026-01-02"], ["2026-01-04", "2026-01-05"]],
        )
        prefix = confirmed_polyline_segments(bars[:3], snapshots[:3], [0, 0, 0], {2})
        self.assertEqual(prefix, [segments[0]])

    def http_server(self, infrastructure=None):
        assets = self.root / "web"
        sdk = assets / "vendor"
        sdk.mkdir(parents=True)
        next_static = assets / "_next/static"
        next_static.mkdir(parents=True)
        (sdk / "lightweight-charts.js").write_text("// test SDK fixture")
        (sdk / "LICENSE").write_text("license fixture")
        (sdk / "NOTICE").write_text("notice fixture")
        (next_static / "app.js").write_text("// next fixture")
        (assets / "index.html").write_text("<html>fixture</html>")
        (assets / "market.html").write_text("<html>market fixture</html>")
        (assets / "research.html").write_text("<html>research fixture</html>")
        server = make_server(self.repo, port=0, web_root=assets, infrastructure=infrastructure)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close(), thread.join()))
        return server

    def test_http_readonly_allowlist_and_security_headers(self):
        server = self.http_server()

        def request(path, method="GET", headers=None):
            conn = HTTPConnection("127.0.0.1", server.server_port)
            try:
                conn.request(method, path, headers=headers or {})
                r = conn.getresponse()
                return r.status, dict(r.getheaders()), r.read()
            finally:
                conn.close()

        status, headers, body = request("/")
        self.assertEqual(status, 200)
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(request("/research")[0], 200)
        self.assertEqual(request("/research.html")[0], 200)
        self.assertEqual(request("/market")[0], 200)
        self.assertEqual(request("/market.html")[0], 200)
        self.assertEqual(request("/_next/static/app.js")[0], 200)
        self.assertEqual(request("/../../package.json")[0], 404)
        self.assertEqual(request("/vendor/NOTICE")[0], 200)
        self.assertEqual(request("/snapshot/daily.csv")[0], 404)
        self.assertEqual(request("/api/catalog", "POST")[0], 405)
        status, catalog_headers, _body = request("/api/tdx-catalog")
        self.assertEqual(status, 200)
        self.assertIn("ETag", catalog_headers)
        self.assertEqual(catalog_headers["Cache-Control"], "private, no-cache")
        self.assertEqual(
            request("/api/tdx-catalog", headers={"If-None-Match": catalog_headers["ETag"]})[0],
            304,
        )
        self.assertEqual(request("/api/tdx-view?symbol=sh.600104&asof=2026-01-02")[0], 400)
        self.assertEqual(request("/api/tdx-view?symbol=../x&asof=2026-01-02&path=/secret")[0], 400)
        self.assertEqual(
            request(
                "/api/tdx-backtest?run=example&variant=proxy_full&symbol=sh.600000&asof=2026-01-02&scenario=base&start=2020-01-01"
            )[0],
            400,
        )
        self.assertEqual(request("/api/tdx-backtest?run=example&start=2020-01-01&path=/secret")[0], 400)
        self.assertEqual(request("/api/catalog", headers={"Host": "evil.example"})[0], 403)
        self.assertEqual(request("/api/catalog", headers={"Origin": "https://evil.example"})[0], 403)
        self.assertEqual(request("/api/catalog")[0], 200)
        self.assertEqual(
            request(
                "/api/structure-signals?run=example&variant=lecture_v1&source=tdx&asof=2026-01-03&lookback=1&signal_type=any&trend_level=0"
            )[0],
            503,
        )
        self.assertEqual(request("/api/health?run=example&run=example")[0], 400)
        self.assertEqual(request("/api/view?run=example")[0], 400)
        self.assertEqual(request("/api/health?run=example")[0], 200)

    def test_tdx_backtest_volume_filter_is_optional_and_strictly_typed(self):
        server = self.http_server()
        base = (
            "/api/tdx-backtest?run=example&variant=lecture_v3&symbol=sz.300154"
            "&asof=2026-09-07&scenario=base&start=2018-01-02"
        )

        def request(suffix=""):
            conn = HTTPConnection("127.0.0.1", server.server_port)
            try:
                conn.request("GET", base + suffix)
                response = conn.getresponse()
                return response.status, json.loads(response.read())
            finally:
                conn.close()

        with patch.object(self.repo, "tdx_backtest", return_value={"ok": True}) as backtest:
            self.assertEqual(request()[0], 200)
            self.assertIs(backtest.call_args.kwargs["volume_filter"], True)
            self.assertIs(backtest.call_args.kwargs["net_reward_risk_filter"], False)
            self.assertIs(backtest.call_args.kwargs["shallow_base_breakout_enabled"], True)
            self.assertEqual(backtest.call_args.kwargs["initial_capital"], 100_000)
            self.assertEqual(backtest.call_args.kwargs["max_position_weight"], 1.0)
            self.assertEqual(request("&initial_capital=250000&max_position_weight=0.25")[0], 200)
            self.assertEqual(backtest.call_args.kwargs["initial_capital"], 250_000)
            self.assertEqual(backtest.call_args.kwargs["max_position_weight"], 0.25)
            self.assertEqual(request("&net_reward_risk_filter=true")[0], 200)
            self.assertIs(backtest.call_args.kwargs["net_reward_risk_filter"], True)
            self.assertEqual(request("&net_reward_risk_filter=false")[0], 200)
            self.assertIs(backtest.call_args.kwargs["net_reward_risk_filter"], False)
            self.assertEqual(request("&volume_filter=false")[0], 200)
            self.assertIs(backtest.call_args.kwargs["volume_filter"], False)
            self.assertEqual(request("&volume_filter=true")[0], 200)
            self.assertIs(backtest.call_args.kwargs["volume_filter"], True)
            self.assertEqual(request("&shallow_base_breakout_enabled=false")[0], 200)
            self.assertIs(backtest.call_args.kwargs["shallow_base_breakout_enabled"], False)
            self.assertEqual(request("&shallow_base_breakout_enabled=true")[0], 200)
            self.assertIs(backtest.call_args.kwargs["shallow_base_breakout_enabled"], True)
            before = backtest.call_count
            self.assertEqual(request("&net_reward_risk_filter=1")[0], 400)
            self.assertEqual(request("&net_reward_risk_filter=")[0], 400)
            self.assertEqual(request("&net_reward_risk_filter=true&net_reward_risk_filter=false")[0], 400)
            self.assertEqual(request("&volume_filter=0")[0], 400)
            self.assertEqual(request("&volume_filter=")[0], 400)
            self.assertEqual(request("&volume_filter=true&volume_filter=false")[0], 400)
            self.assertEqual(request("&shallow_base_breakout_enabled=1")[0], 400)
            self.assertEqual(request("&shallow_base_breakout_enabled=")[0], 400)
            self.assertEqual(request("&shallow_base_breakout_enabled=true&shallow_base_breakout_enabled=false")[0], 400)
            self.assertEqual(request("&volume_filter=false&unexpected=1")[0], 400)
            for invalid in (
                "&initial_capital=0",
                "&initial_capital=-1",
                "&initial_capital=nan",
                "&initial_capital=inf",
                "&initial_capital=1000000001",
                "&initial_capital=",
                "&max_position_weight=0",
                "&max_position_weight=1.1",
                "&max_position_weight=nan",
            ):
                self.assertEqual(request(invalid)[0], 400, invalid)
            self.assertEqual(backtest.call_count, before)

        akshare_base = base.replace("/api/tdx-backtest", "/api/akshare-backtest")
        with patch.object(self.repo, "akshare_backtest", return_value={"ok": True}) as backtest:
            conn = HTTPConnection("127.0.0.1", server.server_port)
            try:
                conn.request(
                    "GET",
                    akshare_base
                    + "&initial_capital=300000&max_position_weight=0.75&shallow_base_breakout_enabled=false",
                )
                self.assertEqual(conn.getresponse().status, 200)
            finally:
                conn.close()
            self.assertEqual(backtest.call_args.kwargs["initial_capital"], 300_000)
            self.assertEqual(backtest.call_args.kwargs["max_position_weight"], 0.75)
            self.assertIs(backtest.call_args.kwargs["shallow_base_breakout_enabled"], False)

    def test_running_backtest_status_rejects_same_stock_and_capacity(self):
        with patch("wavequant_api.server.BacktestJobs", return_value=BacktestJobs(max_active=1)):
            server = self.http_server()
        base = (
            "/api/akshare-backtest?run=example&variant=lecture_v3&symbol=sz.300154"
            "&asof=2026-09-07&scenario=base&start=2018-01-02"
        )
        entered, release = Event(), Event()

        def backtest(*args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(5))
            return {"symbol": args[2]}

        def request(path):
            conn = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            try:
                conn.request("GET", path)
                response = conn.getresponse()
                return response.status, json.loads(response.read())
            finally:
                conn.close()

        with (
            patch.object(self.repo, "akshare_backtest", side_effect=backtest) as mock,
            patch.object(self.repo, "backtest_version", return_value={"version": "strategy-v1"}),
        ):
            first = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            try:
                first.request("GET", base + "&backtest_job=running-stock-0001")
                self.assertTrue(entered.wait(5))
                status, snapshot = request("/api/backtest-jobs")
                self.assertEqual((status, snapshot["active"], snapshot["max_active"]), (200, 1, 1))
                self.assertEqual(snapshot["jobs"][0]["symbol"], "sz.300154")
                self.assertEqual(snapshot["jobs"][0]["job"], "running-stock-0001")
                self.assertGreaterEqual(snapshot["jobs"][0]["elapsed_seconds"], 0)
                self.assertNotIn("result", snapshot["jobs"][0])
                status, duplicate = request(base + "&backtest_job=other-stock-job-0002")
                self.assertEqual(
                    (status, duplicate["code"], duplicate["running_job"]),
                    (409, "BACKTEST_SYMBOL_RUNNING", "running-stock-0001"),
                )
                self.assertTrue(duplicate["same_request"])
                status, capacity = request(base.replace("sz.300154", "sh.601086"))
                self.assertEqual((status, capacity["code"], capacity["active"]), (503, "BACKTEST_CAPACITY", 1))
                self.assertEqual(mock.call_count, 1)
            finally:
                release.set()
                self.assertEqual(first.getresponse().status, 200)
                first.close()
            completed = request("/api/backtest-jobs")[1]
            self.assertEqual(completed["jobs"], [])
            self.assertEqual(completed["recent"][0]["status"], "completed")
            self.assertEqual(completed["recent"][0]["version"], "strategy-v1")
            self.assertEqual(completed["recent"][0]["params"]["max_position_weight"], "1")

    def test_backtest_job_survives_disconnect_and_new_id_rechecks_data(self):
        server = self.http_server()
        base = (
            "/api/tdx-backtest?run=example&variant=lecture_v3&symbol=sz.300154"
            "&asof=2026-09-07&scenario=base&start=2018-01-02"
        )
        job_id = "backtest-recovery-0001"
        entered = Event()
        release = Event()

        def backtest(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("test backtest was not released")
            return {"symbol": args[2], "fresh_call": mock.call_count}

        def request(path):
            conn = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            try:
                conn.request("GET", path)
                response = conn.getresponse()
                return response.status, json.loads(response.read())
            finally:
                conn.close()

        with patch.object(self.repo, "tdx_backtest", side_effect=backtest) as mock:
            first = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            try:
                first.request("GET", base + f"&backtest_job={job_id}")
                self.assertTrue(entered.wait(5))
                first.close()
                status, running = request(f"/api/backtest-job?job={job_id}")
                self.assertEqual((status, running["status"]), (202, "running"))
                self.assertGreaterEqual(running["elapsed_seconds"], 0)
            finally:
                release.set()
                first.close()

            status, result = request(base + f"&backtest_job={job_id}")
            self.assertEqual(status, 200)
            self.assertEqual(result, {"symbol": "sz.300154", "fresh_call": 1})
            self.assertEqual(
                request(f"/api/backtest-job?job={job_id}"),
                (200, {"status": "completed", "result": result}),
            )
            self.assertEqual(mock.call_count, 1)
            self.assertEqual(request(base + f"&backtest_job={job_id}&initial_capital=100000")[0], 200)
            self.assertEqual(mock.call_count, 1)
            self.assertEqual(request(base + f"&backtest_job={job_id}&start=2019-01-01")[0], 400)
            self.assertEqual(
                request(base.replace("start=2018-01-02", "start=2019-01-01") + f"&backtest_job={job_id}")[0], 409
            )
            self.assertEqual(request(base + "&backtest_job=backtest-recovery-0002")[1]["fresh_call"], 2)
            self.assertEqual(mock.call_count, 2)

    def test_backtest_job_reports_safe_failure_and_rejects_invalid_queries(self):
        server = self.http_server()
        base = (
            "/api/akshare-backtest?run=example&variant=lecture_v3&symbol=sz.300154"
            "&asof=2026-09-07&scenario=base&start=2018-01-02"
        )

        def request(path):
            conn = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            try:
                conn.request("GET", path)
                response = conn.getresponse()
                return response.status, json.loads(response.read())
            finally:
                conn.close()

        self.assertEqual(request("/api/backtest-job?job=unknown-job-0000")[0], 404)
        self.assertEqual(request("/api/backtest-job?job=short")[0], 400)
        self.assertEqual(request("/api/backtest-job?job=unknown-job-0000&extra=1")[0], 400)
        with patch.object(self.repo, "akshare_backtest", side_effect=ValueError("回测区间无数据")):
            status, body = request(base + "&backtest_job=backtest-invalid-0001")
            self.assertEqual((status, body["error"]), (400, "回测参数或行情数据无效，请检查输入后重试"))
            self.assertEqual(
                request("/api/backtest-job?job=backtest-invalid-0001"),
                (200, {"status": "failed", "error": "回测参数或行情数据无效，请检查输入后重试", "http_status": 400}),
            )
        with patch.object(self.repo, "akshare_backtest", side_effect=AkShareUnavailable("secret provider detail")):
            status, body = request(base + "&backtest_job=backtest-unavailable-0002")
            self.assertEqual(status, 503)
            self.assertNotIn("secret", body["error"])
            status, body = request("/api/backtest-job?job=backtest-unavailable-0002")
            self.assertEqual((status, body["status"], body["http_status"]), (200, "failed", 503))
            self.assertNotIn("secret", body["error"])
        with self.assertLogs(level="ERROR") as captured:
            with patch.object(self.repo, "akshare_backtest", side_effect=RuntimeError("secret internal path")):
                status, body = request(base + "&backtest_job=backtest-failure-0002")
                self.assertEqual(status, 500)
                self.assertNotIn("secret", body["error"])
                status, body = request("/api/backtest-job?job=backtest-failure-0002")
                self.assertEqual((status, body["status"], body["http_status"]), (200, "failed", 500))
                self.assertNotIn("secret", body["error"])
        self.assertEqual(len(captured.output), 1)

    def test_backtest_version_requires_exact_parameters_and_reads_each_time(self):
        server = self.http_server()

        def request(path):
            conn = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            try:
                conn.request("GET", path)
                response = conn.getresponse()
                return response.status, json.loads(response.read())
            finally:
                conn.close()

        with patch.object(
            self.repo, "backtest_version", create=True, side_effect=[{"version": "a"}, {"version": "b"}]
        ) as version:
            self.assertEqual(request("/api/backtest-version?run=example&variant=lecture_v3"), (200, {"version": "a"}))
            self.assertEqual(request("/api/backtest-version?run=example&variant=lecture_v3"), (200, {"version": "b"}))
            version.assert_called_with("example", "lecture_v3")
            self.assertEqual(version.call_count, 2)
            for path in (
                "/api/backtest-version",
                "/api/backtest-version?run=example",
                "/api/backtest-version?run=&variant=lecture_v3",
                "/api/backtest-version?run=example&variant=",
                "/api/backtest-version?run=example&variant=lecture_v3&extra=1",
                "/api/backtest-version?run=example&run=other&variant=lecture_v3",
                "/api/backtest-version?run=%20example&variant=lecture_v3",
            ):
                self.assertEqual(request(path)[0], 400, path)
            self.assertEqual(version.call_count, 2)
        status, body = request("/api/backtest-version?run=example&variant=proxy_full")
        self.assertEqual(status, 200)
        self.assertEqual(len(body["version"]), 64)
        self.assertEqual(body["profile_version"], "proxy_full")

    def test_tdx_backtest_volume_setting_is_part_of_strategy_and_theory_cache_key(self):
        profile = {
            "strategy": {"pivot_mode": "lecture_causal", "volume_filter": True},
            "scenarios": {"base": {"execution": {}}},
            "definition": {"primary_filters": ["rvol_1_2", "gross_rr_1_5"]},
        }
        strategies, executions, keys = [], [], []

        class Cache:
            def key(self, kind, value):
                keys.append((kind, value))
                return kind

            def lock(self, _key):
                return nullcontext()

            def get(self, _kind, _key):
                return {}  # Cached theory keeps this test focused on strategy plumbing.

        def run(_symbol, _start, _asof, strategy, _execution):
            strategies.append(strategy.copy())
            executions.append(_execution.copy())
            return (
                [],
                None,
                {
                    "price_basis": "causal_adjusted_equivalent",
                    "run_id": "test-run",
                    "backtest": {"source": {"engine": "test"}, "strategy": strategy.copy()},
                },
            )

        self.repo.tdx_backtester = SimpleNamespace(run=run, artifacts=Cache())
        with patch.object(self.repo, "strategy_config", return_value=profile):
            enabled = self.repo.tdx_backtest("example", "lecture_v3", "sz.300154", "2026-09-07", "base", "2018-01-02")
            disabled = self.repo.tdx_backtest(
                "example",
                "lecture_v3",
                "sz.300154",
                "2026-09-07",
                "base",
                "2018-01-02",
                volume_filter=False,
                net_reward_risk_filter=True,
                shallow_base_breakout_enabled=False,
                initial_capital=250_000,
                max_position_weight=0.25,
            )
        self.assertEqual([e["net_reward_risk_filter"] for e in executions], [False, True])
        self.assertEqual([e["initial_capital"] for e in executions], [100_000, 250_000])
        self.assertEqual([e["max_position_weight"] for e in executions], [1.0, 0.25])
        self.assertNotIn("net_reward_risk_filter", profile["scenarios"]["base"]["execution"])
        self.assertEqual([strategy["volume_filter"] for strategy in strategies], [True, False])
        self.assertEqual([strategy["shallow_base_breakout_enabled"] for strategy in strategies], [True, False])
        self.assertNotEqual(keys[0][1]["strategy"], keys[1][1]["strategy"])
        self.assertTrue(profile["strategy"]["volume_filter"])
        self.assertIn("rvol_1_2", enabled["strategy_profile"]["definition"]["primary_filters"])
        self.assertNotIn("rvol_1_2", disabled["strategy_profile"]["definition"]["primary_filters"])

    def test_akshare_backtest_passes_sizing_without_mutating_the_profile(self):
        profile = {"strategy": {}, "scenarios": {"base": {"execution": {"initial_capital": 1_000_000}}}}
        executions = []

        def run(_symbol, _start, asof, _strategy, execution):
            executions.append(execution.copy())
            return [], None, {"asof": asof, "price_basis": "causal_adjusted_equivalent", "run_id": "test-run"}

        self.repo.akshare_backtester = SimpleNamespace(run=run)
        with (
            patch.object(self.repo, "strategy_config", return_value=profile),
            patch.object(self.repo, "render_theory", return_value={}),
        ):
            self.repo.akshare_backtest("example", "lecture_v3", "sz.300154", "2026-09-07", "base", "2018-01-02")
            self.repo.akshare_backtest(
                "example",
                "lecture_v3",
                "sz.300154",
                "2026-09-07",
                "base",
                "2018-01-02",
                initial_capital=200_000,
                max_position_weight=0.4,
            )
        self.assertEqual([item["initial_capital"] for item in executions], [100_000, 200_000])
        self.assertEqual([item["max_position_weight"] for item in executions], [1.0, 0.4])
        self.assertEqual(profile["scenarios"]["base"]["execution"], {"initial_capital": 1_000_000})

    def test_akshare_routes_are_read_only_strict_and_report_provider_failures(self):
        class AkShare:
            def catalog(self):
                return {"available": True, "with_daily": 1, "stocks": [{"symbol": "sh.600519"}]}

            def view(self, symbol, asof):
                return {
                    "symbol": symbol,
                    "asof": asof,
                    "requested_asof": asof,
                    "result_scope": "akshare",
                    "data_source": "akshare",
                    "data_version": "fake-daily",
                    "price_basis": "raw_unadjusted",
                    "sessions": [asof],
                    "bars": [
                        {
                            "time": asof,
                            "open": 10,
                            "high": 12,
                            "low": 9,
                            "close": 11,
                            "raw_close": 11,
                            "volume": 1000,
                            "factor": 1,
                        }
                    ],
                    "evidence": "测试日线。",
                }

            def theory(self, symbol, asof):
                return {"symbol": symbol, "asof": asof, "computed_from": "akshare_raw_prefix_display_only"}

        class MarketData:
            def __init__(self, source):
                self.source = source

            def catalog(self, source):
                self.assert_source(source)
                return self.source.catalog()

            def view(self, source, symbol, asof, timeframe="1d"):
                self.assert_source(source)
                return {**self.source.view(symbol, asof), "timeframe": timeframe}

            def theory(self, source, symbol, asof, timeframe="1d"):
                self.assert_source(source)
                return {**self.source.theory(symbol, asof), "timeframe": timeframe}

            @staticmethod
            def assert_source(source):
                if source != "akshare":
                    raise AssertionError("unexpected source")

        self.repo.akshare = AkShare()
        self.repo.market_data = MarketData(self.repo.akshare)
        server = self.http_server()

        def request(path):
            conn = HTTPConnection("127.0.0.1", server.server_port)
            try:
                conn.request("GET", path)
                response = conn.getresponse()
                return response.status, json.loads(response.read())
            finally:
                conn.close()

        self.assertEqual(request("/api/akshare-catalog")[0], 200)
        self.assertEqual(request("/api/akshare-catalog?unexpected=1")[0], 400)
        conn = HTTPConnection("127.0.0.1", server.server_port)
        try:
            conn.request("GET", "/api/akshare-catalog")
            response = conn.getresponse()
            etag = response.getheader("ETag")
            response.read()
        finally:
            conn.close()
        self.assertIsNotNone(etag)
        conn = HTTPConnection("127.0.0.1", server.server_port)
        try:
            conn.request("GET", "/api/akshare-catalog", headers={"If-None-Match": etag})
            response = conn.getresponse()
            self.assertEqual(response.status, 304)
            self.assertEqual(response.read(), b"")
        finally:
            conn.close()
        self.repo.akshare.catalog = lambda: {
            "available": True,
            "with_daily": 2,
            "stocks": [{"symbol": "sh.600519"}, {"symbol": "sz.000001"}],
        }
        conn = HTTPConnection("127.0.0.1", server.server_port)
        try:
            conn.request("GET", "/api/akshare-catalog", headers={"If-None-Match": etag})
            response = conn.getresponse()
            self.assertEqual(response.status, 200)
            self.assertNotEqual(response.getheader("ETag"), etag)
            response.read()
        finally:
            conn.close()
        status, view = request("/api/akshare-view?symbol=sh.600519&asof=2026-01-02")
        self.assertEqual(status, 200)
        self.assertEqual(view["result_scope"], "akshare")
        self.assertEqual(view["timeframe"], "1d")
        status, weekly = request("/api/akshare-view?symbol=sh.600519&asof=2026-01-02&timeframe=1w")
        self.assertEqual(status, 200)
        self.assertEqual(weekly["timeframe"], "1w")
        self.assertEqual(request("/api/akshare-view?symbol=sh.600519")[0], 400)
        conn = HTTPConnection("127.0.0.1", server.server_port)
        try:
            conn.request(
                "GET",
                "/api/market-timeframe?source=akshare&symbol=sh.600519&asof=2026-01-02&timeframe=1d",
            )
            response = conn.getresponse()
            self.assertEqual(response.status, 200)
            snapshot_etag = response.getheader("ETag")
            bundle = json.loads(response.read())
            self.assertEqual(bundle["view"]["timeframe"], "1d")
            self.assertEqual(bundle["theory"]["timeframe"], "1d")
        finally:
            conn.close()
        conn = HTTPConnection("127.0.0.1", server.server_port)
        try:
            conn.request(
                "GET",
                "/api/market-timeframe?source=akshare&symbol=sh.600519&asof=2026-01-02&timeframe=1d",
                headers={"If-None-Match": snapshot_etag},
            )
            response = conn.getresponse()
            self.assertEqual(response.status, 304)
            self.assertEqual(response.read(), b"")
        finally:
            conn.close()

        status, structure = request(
            "/api/structure-signals?run=example&variant=lecture_v1&source=akshare&asof=2026-01-02&lookback=5&signal_type=any&trend_level=0"
        )
        self.assertEqual(status, 503)
        self.assertIn("读模型", structure["error"])

        def unavailable():
            raise AkShareUnavailable("AkShare 上游请求超时，请稍后重试")

        self.repo.akshare.catalog = unavailable
        status, body = request("/api/akshare-catalog")
        self.assertEqual(status, 503)
        self.assertEqual(body["error"], "AkShare 上游请求超时，请稍后重试")

    def test_infrastructure_health_is_secret_safe_and_catalog_uses_cache(self):
        class Services:
            def __init__(self):
                self.keys = []

            def health(self):
                return {
                    "status": "ok",
                    "database": {"configured": True, "healthy": True, "backend": "mysql"},
                    "redis": {"configured": True, "healthy": True, "tls": False},
                }

            def cached_json(self, key, loader):
                self.keys.append(key)
                return loader()

        services = Services()
        server = self.http_server(services)

        def request(path):
            conn = HTTPConnection("127.0.0.1", server.server_port)
            try:
                conn.request("GET", path)
                response = conn.getresponse()
                return response.status, json.loads(response.read())
            finally:
                conn.close()

        status, health = request("/api/infrastructure/health")
        self.assertEqual(status, 200)
        self.assertEqual(health["database"]["backend"], "mysql")
        self.assertNotIn("url", json.dumps(health))
        self.assertEqual(request("/api/catalog")[0], 200)
        self.assertEqual(services.keys, ["api:catalog:v1"])

    def test_interactive_scan_mutations_are_disabled(self):
        server = self.http_server()

        def post(path, body, headers=None, method="POST"):
            conn = HTTPConnection("127.0.0.1", server.server_port)
            try:
                conn.request(method, path, json.dumps(body), headers=headers or {"Content-Type": "application/json"})
                response = conn.getresponse()
                data = response.read()
                return response.status, data
            finally:
                conn.close()

        self.assertEqual(post("/api/buy-scan", {})[0], 405)
        self.assertEqual(post("/api/buy-scan", {}, {"Content-Type": "text/plain"})[0], 405)
        self.assertEqual(post("/api/buy-scan", {"path": "/secret"})[0], 405)
        self.assertEqual(post("/api/buy-scan/cancel", {"id": "missing"})[0], 405)
        self.assertEqual(post("/api/structure-scan", {"path": "/secret"})[0], 405)
        self.assertEqual(post("/api/structure-scan/cancel", {"id": "missing"})[0], 405)
        self.assertEqual(post("/api/buy-scan", {}, method="DELETE")[0], 405)

    def test_precomputed_structure_endpoint_reads_completed_sql_snapshot(self):
        database = ResearchRunRepository(
            create_engine(
                "sqlite+pysqlite:///:memory:",
                connect_args={"check_same_thread": False},
                poolclass=StaticPool,
            )
        )
        database.initialize()
        infrastructure = Infrastructure(InfrastructureSettings(), database=database)
        self.addCleanup(infrastructure.close)
        algorithm_version = self.repo.structure_scanner.algorithm_version()
        database.save_structure_snapshot(
            StructureSnapshot(
                snapshot_id="c" * 64,
                run_id="example",
                variant="lecture_v1",
                source="tdx",
                asof="2026-01-03",
                algorithm_version=algorithm_version,
                data_version="b" * 64,
                payload={
                    "results": [
                        {
                            "id": "confirmed-low",
                            "symbol": "sh.600000",
                            "signal_type": "bear_bull_alternation",
                            "trend_level": 2,
                            "event_date": "2026-01-01",
                            "available_at": "2026-01-03",
                            "session_age": 1,
                        }
                    ],
                    "stale": 0,
                    "skip_reasons": {},
                    "errors": [],
                },
                total=5_549,
                skipped=355,
                failed=0,
            )
        )
        server = self.http_server(infrastructure)
        connection = HTTPConnection("127.0.0.1", server.server_port)
        try:
            connection.request(
                "GET",
                "/api/structure-signals?run=example&variant=lecture_v1&source=tdx&asof=2026-01-03&lookback=1&signal_type=any&trend_level=0&markets=shanghai",
            )
            response = connection.getresponse()
            body = json.loads(response.read())
        finally:
            connection.close()
        self.assertEqual(response.status, 200)
        self.assertEqual(body["status"], "ready")
        self.assertEqual(body["processed"], 5_549)
        self.assertEqual(body["results"][0]["id"], "confirmed-low")
        self.assertEqual(body["params"]["markets"], "shanghai")

        connection = HTTPConnection("127.0.0.1", server.server_port)
        try:
            connection.request(
                "GET",
                "/api/structure-signals?run=example&variant=lecture_v1&source=tdx&asof=2026-01-03&lookback=1&signal_type=any&trend_level=0&markets=",
            )
            invalid_response = connection.getresponse()
            invalid_response.read()
        finally:
            connection.close()
        self.assertEqual(invalid_response.status, 400)

    def test_precomputed_structure_endpoint_returns_503_until_worker_publishes(self):
        database = ResearchRunRepository(
            create_engine(
                "sqlite+pysqlite:///:memory:",
                connect_args={"check_same_thread": False},
                poolclass=StaticPool,
            )
        )
        database.initialize()
        infrastructure = Infrastructure(InfrastructureSettings(), database=database)
        self.addCleanup(infrastructure.close)
        server = self.http_server(infrastructure)
        connection = HTTPConnection("127.0.0.1", server.server_port)
        try:
            connection.request(
                "GET",
                "/api/structure-signals?run=example&variant=lecture_v1&source=tdx&asof=2026-01-03&lookback=1&signal_type=any&trend_level=0",
            )
            response = connection.getresponse()
            body = json.loads(response.read())
        finally:
            connection.close()
        self.assertEqual(response.status, 503)
        self.assertIn("后台 Worker", body["error"])

    def test_network_bind_and_missing_web_build_rejected(self):
        with self.assertRaises(ValueError):
            make_server(self.repo, host="0.0.0.0", port=0)
        with self.assertRaisesRegex(ValueError, "Web build missing"):
            make_server(self.repo, port=0, web_root=self.root / "missing")
        for invalid_url in (
            "https://127.0.0.1:3003",
            "http://example.com:3003",
            "http://127.0.0.1:3003/research",
        ):
            with self.subTest(invalid_url=invalid_url), self.assertRaisesRegex(ValueError, "loopback HTTP origin"):
                make_server(self.repo, port=0, serve_static=False, web_url=invalid_url)

    def test_api_only_mode_does_not_require_web_build_and_accepts_explicit_loopback_proxy(self):
        server = make_server(
            self.repo,
            port=0,
            web_root=self.root / "missing",
            serve_static=False,
            allowed_origins=("http://127.0.0.1:3003",),
            web_url="http://127.0.0.1:3003",
        )
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close(), thread.join()))

        connection = HTTPConnection("127.0.0.1", server.server_port)
        try:
            connection.request("GET", "/api/catalog", headers={"Origin": "http://127.0.0.1:3003"})
            response = connection.getresponse()
            response.read()
        finally:
            connection.close()
        self.assertEqual(response.status, 200)

        connection = HTTPConnection("127.0.0.1", server.server_port)
        try:
            connection.request("GET", "/")
            response = connection.getresponse()
            body = response.read()
        finally:
            connection.close()
        self.assertEqual(response.status, 307)
        self.assertEqual(response.getheader("Location"), "http://127.0.0.1:3003/")
        self.assertEqual(body, b"")

        connection = HTTPConnection("127.0.0.1", server.server_port)
        try:
            connection.request(
                "POST",
                "/api/buy-scan",
                json.dumps({}),
                headers={"Content-Type": "application/json", "Origin": "http://127.0.0.1:3003"},
            )
            response = connection.getresponse()
            response.read()
        finally:
            connection.close()
        self.assertEqual(response.status, 405)

    def test_legacy_scan_job_routes_are_not_public(self):
        server = self.http_server()
        self.repo.buy_scanner.jobs["partial-test"] = dict(
            status="running", revision=3, processed=1, total=3, results=[{"symbol": "sh.600000"}]
        )

        def request(query):
            conn = HTTPConnection("127.0.0.1", server.server_port, timeout=2)
            try:
                conn.request("GET", "/api/buy-scan?" + query)
                r = conn.getresponse()
                return r.status, json.loads(r.read())
            finally:
                conn.close()

        status, body = request("id=partial-test&after=2")
        self.assertEqual(status, 404)
        self.assertEqual(body["error"], "not found")

    def test_legacy_structure_job_route_is_not_public(self):
        server = self.http_server()
        self.repo.structure_scanner.jobs["structure-partial"] = dict(
            status="running",
            revision=2,
            processed=1,
            total=2,
            results=[{"symbol": "sh.600000", "signal_type": "bear_to_bull"}],
        )

        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=2)
        try:
            connection.request("GET", "/api/structure-scan?id=structure-partial&after=1")
            response = connection.getresponse()
            body = json.loads(response.read())
        finally:
            connection.close()
        self.assertEqual(response.status, 404)
        self.assertEqual(body["error"], "not found")


if __name__ == "__main__":
    unittest.main()
