"""Bounded recovery state for disconnected backtest HTTP requests."""

from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import unittest

from wavequant_api.backtest_jobs import BacktestJobCapacity, BacktestJobConflict, BacktestJobSymbolBusy, BacktestJobs


class BacktestJobsTests(unittest.TestCase):
    def test_one_running_job_per_symbol_across_sources_and_capacity_release(self):
        jobs = BacktestJobs(max_active=2)
        entered, release = Event(), Event()
        calls = []

        def slow_backtest():
            entered.set()
            self.assertTrue(release.wait(5))
            return {"result": 1}

        first = jobs.start("akshare-job", "akshare-args", slow_backtest, symbol="sz.000978")
        try:
            self.assertTrue(entered.wait(5))
            self.assertIs(jobs.start("akshare-job", "akshare-args", slow_backtest, symbol="sz.000978"), first)
            with self.assertRaises(BacktestJobSymbolBusy) as duplicate:
                jobs.start("new-job", "akshare-args", lambda: calls.append(True), symbol="sz.000978")
            self.assertEqual((duplicate.exception.job_id, duplicate.exception.same_request), ("akshare-job", True))
            with self.assertRaises(BacktestJobSymbolBusy) as other_source:
                jobs.start("tdx-job", "tdx-args", lambda: calls.append(True), symbol="sz.000978")
            self.assertEqual(
                (other_source.exception.job_id, other_source.exception.same_request), ("akshare-job", False)
            )
            self.assertEqual(calls, [])
            self.assertIsNone(jobs.get("new-job"))
            other_stock = jobs.start("other-stock", "other-args", lambda: {"result": 2}, symbol="sh.601086")
            self.assertTrue(other_stock.done.wait(5))
        finally:
            release.set()
        self.assertTrue(first.done.wait(5))
        replacement = jobs.start("new-job", "akshare-args", lambda: {"result": 3}, symbol="sz.000978")
        self.assertTrue(replacement.done.wait(5))

    def test_capacity_snapshot_reports_running_jobs_without_starting_rejected_work(self):
        jobs = BacktestJobs(max_active=1)
        entered, release = Event(), Event()
        rejected_calls = []

        def slow_backtest():
            entered.set()
            if not release.wait(5):
                raise RuntimeError("test backtest was not released")
            return {"result": 1}

        first = jobs.start(
            "first-job",
            "same-args",
            slow_backtest,
            details={"path": "/api/akshare-backtest", "params": {"symbol": "sz.000978"}},
        )
        try:
            self.assertTrue(entered.wait(5))
            with self.assertRaises(BacktestJobCapacity):
                jobs.start("second-job", "other-args", lambda: rejected_calls.append(True))
            self.assertEqual(rejected_calls, [])
            self.assertIsNone(jobs.get("second-job"))
            snapshot = jobs.snapshot()
            self.assertGreaterEqual(snapshot["jobs"][0].pop("elapsed_seconds"), 0)
            self.assertEqual(snapshot["jobs"][0].pop("progress_percent"), 0)
            self.assertEqual(snapshot["jobs"][0].pop("progress_stage"), "准备回测")
            self.assertEqual(
                snapshot,
                {
                    "active": 1,
                    "max_active": 1,
                    "jobs": [
                        {
                            "job": "first-job",
                            "status": "running",
                            "path": "/api/akshare-backtest",
                            "params": {"symbol": "sz.000978"},
                        }
                    ],
                    "recent": [],
                },
            )
        finally:
            release.set()
        self.assertTrue(first.done.wait(5))
        finished = jobs.snapshot()
        self.assertEqual((finished["active"], finished["jobs"]), (0, []))
        self.assertEqual(finished["recent"][0]["job"], "first-job")

    def test_progress_is_monotonic_and_isolated_to_one_running_job(self):
        jobs = BacktestJobs(max_active=2)
        entered, release = Event(), Event()

        def slow_backtest():
            entered.set()
            self.assertTrue(release.wait(5))

        first = jobs.start("first", "first-args", slow_backtest, symbol="sz.000978")
        second = jobs.start("second", "second-args", slow_backtest, symbol="sh.601086")
        try:
            self.assertTrue(entered.wait(5))
            jobs.update_progress("first", 46, "模拟成交")
            jobs.update_progress("first", 20, "读取日线")
            jobs.update_progress("missing", 50, "模拟成交")
            by_id = {item["job"]: item for item in jobs.snapshot()["jobs"]}
            self.assertEqual(jobs.progress(first), {"progress_percent": 46, "progress_stage": "模拟成交"})
            self.assertEqual(by_id["second"]["progress_percent"], 0)
            with self.assertRaises(ValueError):
                jobs.update_progress("first", 100, "完成")
        finally:
            release.set()
        self.assertTrue(first.done.wait(5))
        self.assertTrue(second.done.wait(5))

    def test_elapsed_seconds_uses_monotonic_job_start_and_does_not_change_capacity(self):
        now = [100.0]
        jobs = BacktestJobs(max_active=1, clock=lambda: now[0])
        entered, release = Event(), Event()

        def slow_backtest():
            entered.set()
            if not release.wait(5):
                raise RuntimeError("test backtest was not released")
            return {"result": 1}

        job = jobs.start("timed-job", "timed-args", slow_backtest, symbol="sz.300562")
        try:
            self.assertTrue(entered.wait(5))
            now[0] = 112.34
            self.assertEqual(jobs.snapshot()["jobs"][0]["elapsed_seconds"], 12.3)
            self.assertEqual(jobs.elapsed_seconds(job), 12.3)
            self.assertEqual(jobs.snapshot()["active"], 1)
        finally:
            release.set()
        self.assertTrue(job.done.wait(5))
        self.assertEqual(jobs.snapshot()["active"], 0)

    def test_active_limit_conflict_and_completed_retention(self):
        now = [0.0]
        jobs = BacktestJobs(max_active=1, max_completed=1, completed_ttl_seconds=10, clock=lambda: now[0])
        entered = Event()
        release = Event()

        def slow_backtest():
            entered.set()
            if not release.wait(5):
                raise RuntimeError("test backtest was not released")
            return {"result": 1}

        first = jobs.start("first-job", "same-args", slow_backtest)
        try:
            self.assertTrue(entered.wait(5))
            self.assertIs(jobs.start("first-job", "same-args", slow_backtest), first)
            with self.assertRaises(BacktestJobConflict):
                jobs.start("first-job", "different-args", slow_backtest)
            with self.assertRaises(BacktestJobCapacity):
                jobs.start("second-job", "same-args", slow_backtest)
        finally:
            release.set()
        self.assertTrue(first.done.wait(5))
        self.assertEqual(jobs.outcome(first), (True, {"result": 1}, None))

        second = jobs.start("second-job", "same-args", lambda: {"result": 2})
        self.assertTrue(second.done.wait(5))
        self.assertIsNone(jobs.get("first-job"))
        self.assertIs(jobs.get("second-job"), second)
        now[0] = 10
        self.assertIsNone(jobs.get("second-job"))

    def test_completed_metadata_remains_after_result_expires_and_then_ages_out(self):
        now = [0.0]
        jobs = BacktestJobs(
            max_completed=1,
            completed_ttl_seconds=10,
            max_history=2,
            history_ttl_seconds=100,
            clock=lambda: now[0],
            history_clock=lambda: now[0],
        )
        details = {
            "path": "/api/akshare-backtest",
            "params": {"symbol": "sz.300154", "start": "2018-01-01"},
            "symbol": "sz.300154",
            "version": "strategy-v1",
        }
        first = jobs.start(
            "first-job",
            "first-args",
            lambda: {
                "result_scope": "stock",
                "backtest": {"status": "complete"},
                "orders": [{"status": "filled"}, {"status": "cancelled"}],
                "metrics": {"total_return": -0.075, "total_pnl": -7500.0},
            },
            details=details,
        )
        self.assertTrue(first.done.wait(5))
        recent = jobs.snapshot()["recent"]
        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0]["status"], "completed")
        self.assertEqual(recent[0]["fill_count"], 1)
        self.assertEqual(recent[0]["total_return"], -0.075)
        self.assertEqual(recent[0]["total_pnl"], -7500.0)
        self.assertTrue(recent[0]["result_available"])
        self.assertNotIn("result", recent[0])
        now[0] = 10
        self.assertIsNone(jobs.get("first-job"))
        self.assertFalse(jobs.snapshot()["recent"][0]["result_available"])
        now[0] = 100
        self.assertEqual(jobs.snapshot()["recent"], [])

    def test_completed_status_survives_server_registry_recreation(self):
        with TemporaryDirectory() as folder:
            history_path = Path(folder) / "backtest-history.sqlite"
            first = BacktestJobs(history_path=history_path)
            job = first.start(
                "durable-job",
                "durable-args",
                lambda: {
                    "result_scope": "stock",
                    "backtest": {"status": "complete"},
                    "orders": [],
                    "metrics": {"total_return": 0.12, "total_pnl": 12000.0},
                },
                details={"symbol": "sz.300154", "version": "v1", "path": "/api/akshare-backtest"},
            )
            self.assertTrue(job.done.wait(5))
            restored = BacktestJobs(history_path=history_path)
            recent = restored.snapshot()["recent"]
            self.assertEqual(len(recent), 1)
            self.assertEqual(recent[0]["status"], "completed")
            self.assertEqual(recent[0]["version"], "v1")
            self.assertEqual(recent[0]["total_return"], 0.12)
            self.assertEqual(recent[0]["total_pnl"], 12000.0)
            self.assertFalse(recent[0]["result_available"])


if __name__ == "__main__":
    unittest.main()
