"""Signal worker orchestration keeps per-symbol AkShare failures isolated."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch
import unittest

from wavequant_api.cli import _akshare_scope_shard, refresh_signals


class AkShareStructureWorkerTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
