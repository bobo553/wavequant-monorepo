"""The durable read model is tested without the user's TDX files or external services."""

from __future__ import annotations

from types import SimpleNamespace
import unittest

from sqlalchemy import create_engine

from wavequant_api.application import StructureSnapshotService, StructureSnapshotUnavailable
from wavequant_api.infrastructure import Infrastructure, InfrastructureSettings, ResearchRunRepository


class FakeStructureScanner:
    def __init__(self) -> None:
        self.algorithm = "a" * 64
        self.data = "b" * 64
        self.starts = 0
        self.online_calculations = 0
        self.names: dict[str, str] = {}
        self.stale = 0

    def algorithm_version(self) -> str:
        return self.algorithm

    def latest_tdx_session(self) -> str:
        return "2026-09-07"

    def describe_tdx(self, asof: str) -> dict[str, object]:
        return {"data_version": self.data}

    def start(self, params: dict[str, object]) -> dict[str, object]:
        self.starts += 1
        return {"id": f"job-{self.starts}", "status": "running", "revision": 0}

    def get(self, identifier: str, *, after: int, timeout: int) -> dict[str, object]:
        return {
            "id": identifier,
            "status": "completed",
            "revision": after + 1,
            "algorithm_version": self.algorithm,
            "data_version": self.data,
            "total": 5_549,
            "processed": 5_549,
            "skipped": 355,
            "failed": 0,
            "stale": 0,
            "skip_reasons": {"stale_daily": 355},
            "errors": [],
            "results": [
                {
                    "id": "new-flip",
                    "symbol": "sh.600000",
                    "signal_type": "bear_to_bull",
                    "trend_level": 1,
                    "event_date": "2026-09-01",
                    "available_at": "2026-09-07",
                    "session_age": 1,
                },
                {
                    "id": "old-low",
                    "symbol": "sz.000001",
                    "signal_type": "bear_bull_alternation",
                    "trend_level": 2,
                    "event_date": "2026-08-20",
                    "available_at": "2026-09-01",
                    "session_age": 5,
                },
                {
                    "id": "old-bullish-turn",
                    "symbol": "sz.000002",
                    "signal_type": "bullish_turn",
                    "trend_level": 1,
                    "event_date": "2026-09-01",
                    "available_at": "2026-09-01",
                    "session_age": 5,
                },
            ],
        }

    def current_akshare(self, params: dict[str, object]) -> dict[str, object]:
        self.online_calculations += 1
        return {
            "status": "ready",
            "params": params,
            "total": 1,
            "skipped": 0,
            "failed": 0,
            "stale": self.stale,
            "skip_reasons": {},
            "errors": [],
            "results": [
                {
                    "id": "online-flip",
                    "symbol": params["symbol"],
                    "name": self.names.get(str(params["symbol"])),
                    "signal_type": "bear_to_bull",
                    "trend_level": 1,
                    "event_date": "2026-09-01",
                    "available_at": "2026-09-07",
                    "session_age": 1,
                }
            ],
        }


class FakeMarketData:
    def __init__(self, scanner: FakeStructureScanner) -> None:
        self.scanner = scanner
        self.reads = 0
        self.resolved_asof: str | None = None
        self.unavailable_through: str | None = None
        self.future_first_session: str | None = None

    def catalog(self, source: str) -> dict[str, object]:
        return {"source": source, "latest": "2026-09-14"}

    def view(self, source: str, symbol: str, asof: str) -> dict[str, object]:
        self.reads += 1
        if self.unavailable_through is not None and asof <= self.unavailable_through:
            raise RuntimeError("akshare 数据暂不可用")
        bars = [{"time": self.future_first_session}] if self.future_first_session is not None else []
        return {"data_version": self.scanner.data, "asof": self.resolved_asof or asof, "bars": bars}


class FakeCache:
    def __init__(self) -> None:
        self.keys = []
        self.values = {}

    def get_json(self, key):
        self.keys.append(key)
        return self.values.get(key)

    def set_json(self, key, value):
        self.values[key] = value


class StructureSnapshotServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database = ResearchRunRepository(create_engine("sqlite+pysqlite:///:memory:"))
        self.addCleanup(self.database.close)
        self.database.initialize()
        self.scanner = FakeStructureScanner()
        self.market_data = FakeMarketData(self.scanner)
        repository = SimpleNamespace(structure_scanner=self.scanner, market_data=self.market_data)
        self.cache = FakeCache()
        infrastructure = Infrastructure(InfrastructureSettings(), database=self.database, cache=self.cache)
        self.service = StructureSnapshotService(repository, infrastructure)
        self.params = {
            "run": "run-001",
            "variant": "lecture_v1",
            "source": "tdx",
            "asof": "2026-09-07",
            "lookback": 1,
            "signal_type": "any",
            "trend_level": 0,
        }

    def test_refresh_publishes_once_and_query_only_filters_the_snapshot(self) -> None:
        self.assertEqual(self.service.refresh("run-001", "lecture_v1")["status"], "published")
        self.assertEqual(self.service.refresh("run-001", "lecture_v1")["status"], "current")
        self.assertEqual(self.scanner.starts, 1)

        one_day = self.service.query(self.params)
        five_day_level_two = self.service.query(
            {**self.params, "lookback": 5, "signal_type": "bear_bull_alternation", "trend_level": 2}
        )
        five_day_bullish_turn = self.service.query(
            {**self.params, "lookback": 5, "signal_type": "bullish_turn", "trend_level": 1}
        )
        self.assertEqual([row["id"] for row in one_day["results"]], ["new-flip"])
        self.assertEqual([row["id"] for row in five_day_level_two["results"]], ["old-low"])
        self.assertEqual([row["id"] for row in five_day_bullish_turn["results"]], ["old-bullish-turn"])
        self.assertEqual(self.scanner.starts, 1, "interactive queries must never invoke Core calculation")
        self.assertTrue(all(key.startswith("signal:structure:v4:") for key in self.cache.keys))

    def test_data_or_algorithm_change_creates_a_new_snapshot(self) -> None:
        first = self.service.refresh("run-001", "lecture_v1")
        self.scanner.data = "c" * 64
        data_changed = self.service.refresh("run-001", "lecture_v1")
        self.scanner.algorithm = "d" * 64
        restarted = StructureSnapshotService(
            SimpleNamespace(structure_scanner=self.scanner),
            Infrastructure(InfrastructureSettings(), database=self.database),
        )
        with self.assertRaises(StructureSnapshotUnavailable):
            restarted.query(self.params)
        algorithm_changed = restarted.refresh("run-001", "lecture_v1")

        self.assertNotEqual(first["snapshot_id"], data_changed["snapshot_id"])
        self.assertNotEqual(data_changed["snapshot_id"], algorithm_changed["snapshot_id"])
        self.assertEqual(self.scanner.starts, 3)

    def test_query_fails_closed_when_database_or_snapshot_is_missing(self) -> None:
        missing_database = StructureSnapshotService(
            SimpleNamespace(structure_scanner=self.scanner), Infrastructure(InfrastructureSettings())
        )
        with self.assertRaises(StructureSnapshotUnavailable):
            missing_database.query(self.params)
        with self.assertRaises(StructureSnapshotUnavailable):
            self.service.query(self.params)

    def test_akshare_calculation_runs_only_in_refresh_worker(self) -> None:
        refresh = self.service.refresh(
            "run-001", "lecture_v1", source="akshare", symbol="sh.600519", asof="2026-09-07"
        )
        self.service.refresh("run-001", "lecture_v2", source="akshare", symbol="sz.000001", asof="2026-09-07")
        params = {**self.params, "variant": "lecture_v3", "source": "akshare"}
        reads_after_refresh = self.market_data.reads
        result = self.service.query(params)

        self.assertEqual(refresh["status"], "published")
        self.assertEqual({row["symbol"] for row in result["results"]}, {"sh.600519", "sz.000001"})
        self.assertEqual(
            result["coverage"],
            {
                "scope": "akshare_market",
                "published_stocks": 2,
                "building_stocks": 2,
                "expected_stocks": 2,
            },
        )
        self.assertNotIn("symbol", result["params"])
        self.assertEqual(self.scanner.online_calculations, 2)
        self.assertEqual(self.market_data.reads, reads_after_refresh)

    def test_akshare_suspended_stock_stays_in_requested_market_date_partition(self) -> None:
        self.market_data.resolved_asof = "2026-09-04"

        refresh = self.service.refresh(
            "run-001", "lecture_v1", source="akshare", symbol="sh.600519", asof="2026-09-07"
        )
        result = self.service.query({**self.params, "source": "akshare"})

        self.assertEqual(refresh["asof"], "2026-09-07")
        self.assertEqual(
            result["coverage"],
            {
                "scope": "akshare_market",
                "published_stocks": 1,
                "building_stocks": 1,
                "expected_stocks": 1,
            },
        )
        self.assertEqual([row["symbol"] for row in result["results"]], ["sh.600519"])

    def test_akshare_rebuild_serves_last_complete_generation_until_atomic_switch(self) -> None:
        symbols = ("sh.600519", "sz.000001")
        for symbol in symbols:
            self.service.refresh(
                "run-001",
                "lecture_v1",
                source="akshare",
                symbol=symbol,
                asof="2026-09-07",
                market_total=2,
            )

        self.scanner.algorithm = "c" * 64
        current = StructureSnapshotService(
            SimpleNamespace(structure_scanner=self.scanner, market_data=self.market_data),
            Infrastructure(InfrastructureSettings(), database=self.database, cache=self.cache),
        )
        params = {**self.params, "source": "akshare", "asof": "2026-09-14"}
        calculations_before_queries = self.scanner.online_calculations

        no_current_shards = current.query(params)
        current.refresh(
            "run-001",
            "lecture_v1",
            source="akshare",
            symbol=symbols[0],
            asof="2026-09-14",
            market_total=3,
        )
        one_current_shard = current.query(params)
        current.refresh(
            "run-001",
            "lecture_v1",
            source="akshare",
            symbol=symbols[1],
            asof="2026-09-14",
            market_total=3,
        )
        two_current_shards = current.query(params)

        for rebuilding, published, expected in (
            (no_current_shards, 0, 2),
            (one_current_shard, 1, 3),
            (two_current_shards, 2, 3),
        ):
            self.assertEqual(rebuilding["status"], "rebuilding")
            self.assertEqual(rebuilding["snapshot"]["asof"], "2026-09-07")
            self.assertTrue(rebuilding["snapshot"]["is_fallback"])
            self.assertEqual(rebuilding["coverage"]["building_stocks"], published)
            self.assertEqual(rebuilding["coverage"]["expected_stocks"], expected)
            self.assertEqual(rebuilding["coverage"]["published_stocks"], 2)
        current.refresh(
            "run-001",
            "lecture_v1",
            source="akshare",
            symbol="sz.300001",
            asof="2026-09-14",
            market_total=3,
        )
        complete_current_generation = current.query(params)
        self.assertEqual(complete_current_generation["status"], "ready")
        self.assertEqual(complete_current_generation["snapshot"]["asof"], "2026-09-14")
        self.assertFalse(complete_current_generation["snapshot"]["is_fallback"])
        self.assertEqual(complete_current_generation["coverage"]["building_stocks"], 3)
        self.assertEqual(
            self.scanner.online_calculations,
            calculations_before_queries + 3,
            "interactive reads must not run Core calculations",
        )

    def test_complete_but_market_date_stale_generation_keeps_last_healthy_snapshot(self) -> None:
        symbols = ("sh.600519", "sz.000001")
        for symbol in symbols:
            self.service.refresh(
                "run-001",
                "lecture_v1",
                source="akshare",
                symbol=symbol,
                asof="2026-09-07",
                market_total=2,
            )

        self.scanner.stale = 1
        for symbol in symbols:
            self.service.refresh(
                "run-001",
                "lecture_v1",
                source="akshare",
                symbol=symbol,
                asof="2026-09-14",
                market_total=2,
            )

        result = self.service.query({**self.params, "source": "akshare", "asof": "2026-09-14"})

        self.assertEqual(result["status"], "rebuilding")
        self.assertEqual(result["snapshot"]["asof"], "2026-09-07")
        self.assertTrue(result["snapshot"]["is_fallback"])
        self.assertEqual(result["stale"], 0)
        self.assertEqual({row["symbol"] for row in result["results"]}, set(symbols))
        self.assertIn("行情日期一致性校验失败", result["notice"])

    def test_first_akshare_generation_returns_published_partial_shards_without_calculating(self) -> None:
        self.service.refresh(
            "run-001",
            "lecture_v1",
            source="akshare",
            symbol="sh.600519",
            asof="2026-09-14",
            market_total=2,
        )
        calculations_after_refresh = self.scanner.online_calculations

        result = self.service.query({**self.params, "source": "akshare", "asof": "2026-09-14"})

        self.assertEqual(result["status"], "rebuilding")
        self.assertEqual(result["snapshot"]["asof"], "2026-09-14")
        self.assertFalse(result["snapshot"]["is_fallback"])
        self.assertEqual(result["coverage"]["published_stocks"], 1)
        self.assertEqual(result["coverage"]["building_stocks"], 1)
        self.assertEqual(result["coverage"]["expected_stocks"], 2)
        self.assertEqual(self.scanner.online_calculations, calculations_after_refresh)

    def test_empty_akshare_database_returns_rebuilding_without_calculating(self) -> None:
        calculations_before_query = self.scanner.online_calculations

        result = self.service.query({**self.params, "source": "akshare", "asof": "2026-09-14"})

        self.assertEqual(result["status"], "rebuilding")
        self.assertEqual(result["snapshot"]["asof"], "2026-09-14")
        self.assertFalse(result["snapshot"]["is_fallback"])
        self.assertEqual(result["results"], [])
        self.assertEqual(result["coverage"]["published_stocks"], 0)
        self.assertEqual(result["coverage"]["building_stocks"], 0)
        self.assertIsNone(result["coverage"]["expected_stocks"])
        self.assertEqual(self.scanner.online_calculations, calculations_before_query)

    def test_akshare_stock_listed_after_cutoff_publishes_an_explicit_empty_scope(self) -> None:
        self.market_data.unavailable_through = "2026-09-07"
        self.market_data.future_first_session = "2026-09-11"

        refresh = self.service.refresh(
            "run-001", "lecture_v1", source="akshare", symbol="sh.688801", asof="2026-09-07"
        )
        result = self.service.query({**self.params, "source": "akshare"})

        self.assertEqual(refresh["status"], "published")
        self.assertEqual(
            result["coverage"],
            {
                "scope": "akshare_market",
                "published_stocks": 1,
                "building_stocks": 1,
                "expected_stocks": 1,
            },
        )
        self.assertEqual(result["results"], [])
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(result["skip_reasons"], {"not_listed_asof": 1})
        self.assertEqual(self.scanner.online_calculations, 0)

    def test_market_filters_use_symbol_boards_and_exclude_starred_names(self) -> None:
        stocks = {
            "sh.600000": "浦发银行",
            "sh.600001": "*ST 沪股",
            "sh.688001": "科创样本",
            "sz.000001": "平安银行",
            "sz.300001": "创业样本",
            "bj.430001": "北交样本",
        }
        self.scanner.names.update(stocks)
        for symbol in stocks:
            self.service.refresh(
                "run-001", "lecture_v1", source="akshare", symbol=symbol, asof="2026-09-07"
            )

        default_result = self.service.query({**self.params, "source": "akshare"})
        star_and_beijing = self.service.query(
            {**self.params, "source": "akshare", "markets": "beijing,star"}
        )

        self.assertEqual(
            {row["symbol"] for row in default_result["results"]},
            {"sh.600000", "sz.000001", "sz.300001"},
        )
        self.assertEqual(
            {row["symbol"] for row in star_and_beijing["results"]},
            {"sh.688001", "bj.430001"},
        )
        self.assertEqual(default_result["params"]["markets"], "shanghai,shenzhen,chinext")
        self.assertEqual(star_and_beijing["params"]["markets"], "star,beijing")
        self.assertEqual(star_and_beijing["filters"]["markets"], ["star", "beijing"])
        self.assertNotIn("sh.600001", {row["symbol"] for row in default_result["results"]})
        self.assertNotEqual(self.cache.keys[-1], self.cache.keys[-2])
        self.assertNotIn(",", self.cache.keys[-1])
        self.assertTrue(self.cache.keys[-1].endswith(":star-beijing"))

    def test_market_filter_rejects_empty_unknown_and_duplicate_values(self) -> None:
        for markets in ("", "shanghai,unknown", "star,star"):
            with self.subTest(markets=markets), self.assertRaises(ValueError):
                self.service.query({**self.params, "markets": markets})


if __name__ == "__main__":
    unittest.main()
