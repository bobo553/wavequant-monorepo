"""Buy-signal HTTP reads must never start calculation jobs."""

import unittest

from sqlalchemy import create_engine

from wavequant_api.application import BuySignalSnapshotService, BuySignalSnapshotUnavailable
from wavequant_api.infrastructure import Infrastructure, InfrastructureSettings, ResearchRunRepository


class FakeBuyScanner:
    def __init__(self) -> None:
        self.engine = {"strategy.py": "a" * 64}
        self.starts = 0

    def start(self, params):
        self.starts += 1
        return {"id": "buy-job", "status": "running", "revision": 0}

    def get(self, identifier, *, after, timeout):
        return {
            "id": identifier,
            "status": "completed",
            "revision": after + 1,
            "total": 2,
            "skipped": 0,
            "failed": 0,
            "stale": 0,
            "skip_reasons": {},
            "errors": [],
            "funnel": {"stocks": {}},
            "performance": {},
            "results": [
                {"symbol": "sh.600000", "signal_date": "2026-09-07", "priority": 2, "session_age": 1},
                {"symbol": "sz.000001", "signal_date": "2026-09-01", "priority": 1, "session_age": 5},
            ],
        }


class FakeRepository:
    def __init__(self) -> None:
        self.buy_scanner = FakeBuyScanner()
        self.runs = {"run-001": {"report": {"data": {"end": "2026-09-07"}}}}

    def strategy_config(self, run, variant):
        return {"strategy": {"entry_policy": "transitioned_squeeze"}, "scenarios": {"base": {"execution": {}}}}

    def bars(self, run):
        return {"sh.600000": [], "sz.000001": []}


class FakeCache:
    def __init__(self) -> None:
        self.keys = []
        self.values = {}

    def get_json(self, key):
        self.keys.append(key)
        return self.values.get(key)

    def set_json(self, key, value):
        self.values[key] = value


class BuySignalSnapshotServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database = ResearchRunRepository(create_engine("sqlite+pysqlite:///:memory:"))
        self.addCleanup(self.database.close)
        self.database.initialize()
        self.repository = FakeRepository()
        self.cache = FakeCache()
        infrastructure = Infrastructure(InfrastructureSettings(), database=self.database, cache=self.cache)
        self.service = BuySignalSnapshotService(self.repository, infrastructure)
        self.params = {
            "run": "run-001",
            "variant": "lecture_v1",
            "scenario": "base",
            "source": "snapshot",
            "asof": "2026-09-07",
            "start": "2020-01-01",
            "lookback": 1,
        }

    def test_refresh_is_idempotent_and_queries_only_filter_published_results(self) -> None:
        first = self.service.refresh(
            "run-001", "lecture_v1", "base", "snapshot", "2020-01-01", asof="2026-09-07"
        )
        second = self.service.refresh(
            "run-001", "lecture_v1", "base", "snapshot", "2020-01-01", asof="2026-09-07"
        )
        one_day = self.service.query(self.params)
        five_days = self.service.query({**self.params, "lookback": 5})

        self.assertEqual(first["status"], "published")
        self.assertEqual(second["status"], "current")
        self.assertEqual([row["symbol"] for row in one_day["results"]], ["sh.600000"])
        self.assertEqual(len(five_days["results"]), 2)
        self.assertEqual(self.repository.buy_scanner.starts, 1)
        self.assertTrue(all(key.startswith("signal:buy:v1:") for key in self.cache.keys))

    def test_missing_snapshot_fails_closed(self) -> None:
        with self.assertRaises(BuySignalSnapshotUnavailable):
            self.service.query(self.params)


if __name__ == "__main__":
    unittest.main()
