"""Signal worker orchestration keeps per-symbol AkShare failures isolated."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch
import unittest

from wavequant_api.cli import _akshare_scope_shard, refresh_signals, refresh_timeframes


class AkShareStructureWorkerTests(unittest.TestCase):
    def test_market_latest_date_is_not_downgraded_to_the_first_stock_session(self) -> None:
        calls: list[dict[str, object]] = []

        class Service:
            algorithm_version = "a" * 64

            def refresh(self, run: str, variant: str, **kwargs: object) -> dict[str, object]:
                calls.append(kwargs)
                return {"status": "published"}

        repository = SimpleNamespace(
            catalog=lambda: {"runs": [{"id": "run-001"}]},
            market_data=SimpleNamespace(
                catalog=lambda source: {
                    "source": source,
                    "latest": "2026-09-14",
                    "stocks": [{"symbol": "sh.600000"}, {"symbol": "sz.000001"}],
                }
            ),
        )
        with patch("wavequant_api.cli.StructureSnapshotService", return_value=Service()):
            refresh_signals(
                SimpleNamespace(database=None),
                repository,
                run=None,
                variant="lecture_v1",
                scenario="base",
                source="akshare",
                symbols=[],
                start="2018-01-01",
                asof=None,
                family="structure",
            )

        self.assertEqual([call["asof"] for call in calls], ["2026-09-14", "2026-09-14"])
        self.assertEqual([call["market_total"] for call in calls], [2, 2])

    def test_complete_catalog_continues_after_one_symbol_fails(self) -> None:
        calls: list[str] = []

        class Service:
            def refresh(self, run: str, variant: str, **kwargs: object) -> dict[str, object]:
                symbol = kwargs["symbol"]
                if not isinstance(symbol, str):
                    raise AssertionError("expected a symbol shard")
                calls.append(symbol)
                if symbol == "sh.600000":
                    raise ValueError("temporary provider failure")
                return {"status": "published", "symbol": symbol}

        repository = SimpleNamespace(
            catalog=lambda: {"runs": [{"id": "run-001"}]},
            market_data=SimpleNamespace(
                catalog=lambda source: {
                    "stocks": [{"symbol": "sh.600000"}, {"symbol": "sz.000001"}],
                    "source": source,
                }
            ),
        )
        with patch("wavequant_api.cli.StructureSnapshotService", return_value=Service()):
            results = refresh_signals(
                SimpleNamespace(),
                repository,
                run=None,
                variant="lecture_v1",
                scenario="base",
                source="akshare",
                symbols=[],
                start="2018-01-01",
                asof="2026-09-11",
                family="structure",
            )

        self.assertEqual(calls, ["sh.600000", "sz.000001"])
        self.assertEqual(results[0]["status"], "failed")
        self.assertEqual(results[1], {"status": "published", "symbol": "sz.000001"})

    def test_complete_catalog_resumes_with_only_unpublished_symbols(self) -> None:
        calls: list[str] = []

        class Service:
            algorithm_version = "a" * 64

            def refresh(self, run: str, variant: str, **kwargs: object) -> dict[str, object]:
                symbol = kwargs["symbol"]
                if not isinstance(symbol, str):
                    raise AssertionError("expected a symbol shard")
                calls.append(symbol)
                return {"status": "published", "symbol": symbol}

        class Database:
            def list_structure_snapshots(self, *args: object) -> list[SimpleNamespace]:
                self.args = args
                return [SimpleNamespace(scope_symbol="sh.600000")]

        database = Database()
        repository = SimpleNamespace(
            catalog=lambda: {"runs": [{"id": "run-001"}]},
            market_data=SimpleNamespace(
                catalog=lambda source: {
                    "stocks": [
                        {"symbol": "sh.600000"},
                        {"symbol": "sz.000001"},
                        {"symbol": "sz.300001"},
                        {"symbol": "sh.688001"},
                        {"symbol": "bj.920000"},
                        {"symbol": "sh.600002", "catalog_source": "tdx"},
                    ],
                    "source": source,
                }
            ),
        )
        with patch("wavequant_api.cli.StructureSnapshotService", return_value=Service()):
            refresh_signals(
                SimpleNamespace(database=database),
                repository,
                run=None,
                variant="lecture_v1",
                scenario="base",
                source="akshare",
                symbols=[],
                start="2018-01-01",
                asof="2026-09-14",
                family="structure",
            )

        self.assertEqual(
            calls,
            ["sz.000001", "sz.300001", "sh.688001", "bj.920000"],
        )
        self.assertEqual(database.args, ("run-001", None, "akshare", "2026-09-14", "a" * 64))

    def test_full_catalog_shards_are_stable_disjoint_and_complete(self) -> None:
        symbols = ["sh.600000", "sh.600001", "sz.000001", "sz.300001", "sh.688001", "bj.920000"]
        first = {symbol for symbol in symbols if _akshare_scope_shard(symbol, 3) == 0}
        second = {symbol for symbol in symbols if _akshare_scope_shard(symbol, 3) == 1}
        third = {symbol for symbol in symbols if _akshare_scope_shard(symbol, 3) == 2}

        self.assertEqual(first | second | third, set(symbols))
        self.assertFalse(first & second)
        self.assertFalse(first & third)
        self.assertFalse(second & third)
        self.assertEqual([_akshare_scope_shard(symbol, 3) for symbol in symbols], [0, 2, 0, 2, 1, 1])

    def test_timeframe_worker_materializes_every_catalog_symbol_and_counts_versions(self) -> None:
        calls: list[tuple[str, str, str]] = []

        class Service:
            def precompute(self, source: str, symbol: str, asof: str) -> dict[str, object]:
                calls.append((source, symbol, asof))
                return {
                    "published": ["1d", "1w"] if symbol == "sh.600000" else [],
                    "unchanged": ["1mo", "3mo", "1y"] if symbol == "sh.600000" else ["1d", "1w", "1mo", "3mo", "1y"],
                }

        market_data = SimpleNamespace(
            catalog=lambda source: {
                "source": source,
                "latest": "2026-09-14",
                "stocks": [{"symbol": "sh.600000"}, {"symbol": "sz.000001"}],
            }
        )
        with patch("wavequant_api.cli.MarketTimeframeService", return_value=Service()):
            result = refresh_timeframes(
                SimpleNamespace(database=object()),
                SimpleNamespace(market_data=market_data),
                source="akshare",
                symbols=[],
                asof=None,
            )

        self.assertEqual(calls, [("akshare", "sh.600000", "2026-09-14"), ("akshare", "sz.000001", "2026-09-14")])
        self.assertEqual(result["published_periods"], 2)
        self.assertEqual(result["unchanged_periods"], 8)


if __name__ == "__main__":
    unittest.main()
