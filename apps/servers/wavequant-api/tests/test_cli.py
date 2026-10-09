"""Signal worker orchestration keeps per-symbol AkShare failures isolated."""

from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from wavequant_api.cli import _akshare_scope_shard, main, parser, refresh_signals, refresh_structures, refresh_timeframes


class AkShareStructureWorkerTests(unittest.TestCase):
    def test_n_target_cli_defaults_to_both_and_threads_explicit_selection_to_every_worker(self) -> None:
        self.assertIsNone(parser().parse_args([]).n_target_trend_confirmation_enabled)
        self.assertTrue(parser().parse_args(["--n-target-trend-confirmation-enabled"]).n_target_trend_confirmation_enabled)
        self.assertFalse(parser().parse_args(["--n-target-trend-confirmation-enabled", "false"]).n_target_trend_confirmation_enabled)
        calls: list[tuple[str, dict[str, object]]] = []

        class StructureService:
            def refresh(self, *args: object, **kwargs: object) -> dict[str, object]:
                calls.append(("structure", kwargs))
                return {"status": "published"}

        class BuyService:
            def refresh(self, *args: object, **kwargs: object) -> dict[str, object]:
                calls.append(("buy", kwargs))
                return {"status": "published"}

        class TimeframeService:
            def precompute(self, *args: object, **kwargs: object) -> dict[str, object]:
                calls.append(("timeframe", kwargs))
                return {"published": ["1d"], "unchanged": []}

        repository = SimpleNamespace(market_data=SimpleNamespace(catalog=lambda _: {"stocks": []}))
        infrastructure = SimpleNamespace(database=object())
        with patch("wavequant_api.cli.StructureSnapshotService", return_value=StructureService()), patch(
            "wavequant_api.cli.BuySignalSnapshotService", return_value=BuyService()
        ), patch("wavequant_api.cli.MarketTimeframeService", return_value=TimeframeService()):
            for enabled in (False, True):
                refresh_structures(infrastructure, repository, run="example", variant="lecture_v1", asof="2026-09-25",
                                   n_target_trend_confirmation_enabled=enabled)
                refresh_signals(infrastructure, repository, run="example", variant="lecture_v2", scenario="base",
                                source="akshare", symbols=["sz.000678"], start="2018-01-01", asof="2026-09-25",
                                family="all", n_target_trend_confirmation_enabled=enabled)
                refresh_timeframes(infrastructure, repository, source="akshare", symbols=["sz.000678"],
                                   asof="2026-09-25", n_target_trend_confirmation_enabled=enabled)
                self.assertEqual([name for name, _ in calls[-4:]], ["structure", "structure", "buy", "timeframe"])
                self.assertTrue(all(values["n_target_trend_confirmation_enabled"] is enabled for _, values in calls[-4:]))

    def test_worker_cycle_builds_both_modes_serially_or_only_the_explicit_mode(self) -> None:
        for action, target in (("--refresh-structures", "refresh_structures"),
                               ("--refresh-signals", "refresh_signals"),
                               ("--refresh-timeframes", "refresh_timeframes")):
            for arguments, expected in (([], [False, True]),
                                        (["--n-target-trend-confirmation-enabled"], [True]),
                                        (["--n-target-trend-confirmation-enabled", "true"], [True]),
                                        (["--n-target-trend-confirmation-enabled", "false"], [False])):
                with self.subTest(action=action, arguments=arguments):
                    infrastructure = SimpleNamespace(close=lambda: None)
                    with patch("sys.argv", ["wavequant-api", action, *arguments]), patch(
                        "wavequant_api.cli.InfrastructureSettings.from_env", return_value=object()
                    ), patch("wavequant_api.cli.Infrastructure.from_settings", return_value=infrastructure), patch(
                        "wavequant_api.cli.ChartRepository", return_value=object()
                    ), patch(f"wavequant_api.cli.{target}", return_value={"status": "current"}) as refresh, redirect_stdout(StringIO()):
                        main()
                    self.assertEqual([call.kwargs["n_target_trend_confirmation_enabled"] for call in refresh.call_args_list], expected)
                    self.assertTrue(all(call.kwargs.get("shard_count", 1) == 1 for call in refresh.call_args_list))

    def test_market_latest_date_is_not_downgraded_to_the_first_stock_session(self) -> None:
        calls: list[dict[str, object]] = []

        class Service:
            algorithm_version = "a" * 64

            def algorithm_version_for(self, enabled: bool) -> str:
                return self.algorithm_version

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

            def algorithm_version_for(self, enabled: bool) -> str:
                return self.algorithm_version

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
            def precompute(self, source: str, symbol: str, asof: str, **options: object) -> dict[str, object]:
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
