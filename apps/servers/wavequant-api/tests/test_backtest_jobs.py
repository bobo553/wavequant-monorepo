"""Bounded recovery state for disconnected backtest HTTP requests."""

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
            self.assertEqual(
                jobs.snapshot(),
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
                },
            )
        finally:
            release.set()
        self.assertTrue(first.done.wait(5))
        self.assertEqual(jobs.snapshot(), {"active": 0, "max_active": 1, "jobs": []})

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


if __name__ == "__main__":
    unittest.main()
