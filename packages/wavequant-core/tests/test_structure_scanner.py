from datetime import datetime
from types import SimpleNamespace
import time
import unittest
from unittest.mock import Mock

from wavequant.interfaces.screening.structure_scanner import StructureScanner, structure_matches


def landmark(identifier, *, event_date, available_at, level, kind="H", value=12.3):
    return dict(
        id=identifier,
        time=event_date,
        available_at=available_at,
        trend_level=level,
        kind=kind,
        value=value,
        label=("H9" if kind == "H" else "L9"),
    )


def theory():
    return dict(
        price_basis="raw_unadjusted",
        reversal_trends=dict(
            bear_to_bull_highs=[landmark("level1-flip", event_date="2026-01-01", available_at="2026-01-04", level=1)],
            bear_bull_alternation_lows=[
                landmark(
                    "level1-alternation",
                    event_date="2026-01-03",
                    available_at="2026-01-05",
                    level=1,
                    kind="L",
                    value=10.2,
                )
            ],
            bullish_turn_signals=[
                landmark(
                    "level1-bullish-turn",
                    event_date="2026-01-05",
                    available_at="2026-01-05",
                    level=1,
                    kind="K",
                    value=13.1,
                )
            ],
        ),
        secondary_trends=dict(
            bear_to_bull_highs=[landmark("level2-flip", event_date="2026-01-02", available_at="2026-01-05", level=2)],
            bear_bull_alternation_lows=[],
            bullish_turn_signals=[],
        ),
        tertiary_trends=dict(bear_to_bull_highs=[], bear_bull_alternation_lows=[], bullish_turn_signals=[]),
    )


class StructureMatchTests(unittest.TestCase):
    def setUp(self):
        self.sessions = [f"2026-01-0{day}" for day in range(1, 6)]

    def test_window_uses_confirmation_date_instead_of_historical_event_date(self):
        matches, stale = structure_matches(
            theory(),
            self.sessions[:4],
            symbol="sh.600000",
            name="测试银行",
            asof="2026-01-04",
            lookback=1,
            signal_type="bear_to_bull",
            trend_level=0,
        )
        self.assertIsNone(stale)
        self.assertEqual([item["id"] for item in matches], ["level1-flip"])
        self.assertEqual(matches[0]["event_date"], "2026-01-01")
        self.assertEqual(matches[0]["available_at"], "2026-01-04")
        self.assertEqual(matches[0]["session_age"], 1)

    def test_signal_and_level_filters_are_independent(self):
        level_two, _ = structure_matches(
            theory(),
            self.sessions,
            symbol="sh.600000",
            name=None,
            asof="2026-01-05",
            lookback=1,
            signal_type="bear_to_bull",
            trend_level=2,
        )
        alternation, _ = structure_matches(
            theory(),
            self.sessions,
            symbol="sh.600000",
            name=None,
            asof="2026-01-05",
            lookback=1,
            signal_type="bear_bull_alternation",
            trend_level=0,
        )
        self.assertEqual([item["id"] for item in level_two], ["level2-flip"])
        self.assertEqual([item["id"] for item in alternation], ["level1-alternation"])
        self.assertEqual(level_two[0]["session_age"], 1)

    def test_any_signal_type_returns_all_confirmed_contracts(self):
        matches, _ = structure_matches(
            theory(),
            self.sessions,
            symbol="sh.600000",
            name=None,
            asof="2026-01-05",
            lookback=5,
            signal_type="any",
            trend_level=1,
        )
        self.assertEqual(
            {item["signal_type"] for item in matches},
            {"bear_to_bull", "bear_bull_alternation", "bullish_turn"},
        )

    def test_bullish_turn_filter_returns_only_breakout_k_signal(self):
        matches, _ = structure_matches(
            theory(),
            self.sessions,
            symbol="sh.600000",
            name=None,
            asof="2026-01-05",
            lookback=1,
            signal_type="bullish_turn",
            trend_level=0,
        )
        self.assertEqual([item["id"] for item in matches], ["level1-bullish-turn"])
        self.assertEqual(matches[0]["kind"], "K")

    def test_non_session_cutoff_is_stale_and_never_backfills_a_match(self):
        matches, stale = structure_matches(
            theory(),
            self.sessions,
            symbol="sh.600000",
            name=None,
            asof="2026-01-06",
            lookback=5,
            signal_type="bear_to_bull",
            trend_level=0,
        )
        self.assertEqual(matches, [])
        self.assertEqual(stale, "stale")


class StructureScannerTests(unittest.TestCase):
    def setUp(self):
        bars = [SimpleNamespace(timestamp=datetime(2026, 1, day)) for day in range(1, 6)]
        self.repository = SimpleNamespace(
            tdx=None,
            _run=lambda run: None,
            strategy_config=Mock(return_value={}),
            runs={"test": {"report": {"data": {"end": "2026-01-05"}}}},
            bars=lambda run: {"sh.600000": bars, "sz.000001": bars},
            theory=Mock(return_value=theory()),
        )
        self.scanner = StructureScanner(self.repository)
        self.params = dict(
            run="test",
            variant="lecture_v1",
            source="snapshot",
            asof="2026-01-05",
            lookback=1,
            signal_type="bear_bull_alternation",
            trend_level=1,
        )

    def wait(self, identifier):
        for _ in range(200):
            job = self.scanner.get(identifier)
            if job["status"] not in ("running", "cancelling"):
                return job
            time.sleep(0.01)
        self.fail("structure scan did not finish")

    def test_snapshot_job_publishes_confirmed_matches_for_each_stock(self):
        job = self.wait(self.scanner.start(self.params)["id"])
        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["processed"], 2)
        self.assertEqual(len(job["results"]), 2)
        self.assertEqual({row["symbol"] for row in job["results"]}, {"sh.600000", "sz.000001"})
        self.assertTrue(all(row["available_at"] == "2026-01-05" for row in job["results"]))

    def test_job_rejects_unknown_contract_values(self):
        for invalid in (
            dict(self.params, signal_type="later_high"),
            dict(self.params, trend_level=4),
            dict(self.params, lookback=2),
            dict(self.params, source="remote"),
            dict(self.params, path="secret"),
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.scanner.start(invalid)

    def test_tdx_structure_search_includes_non_mainboard_without_corporate_actions(self):
        bars = [SimpleNamespace(timestamp=datetime(2026, 1, day)) for day in range(1, 6)]
        file_stat = SimpleNamespace(st_mtime_ns=1, st_size=32)
        tdx = SimpleNamespace(
            catalog=lambda: {
                "stocks": [
                    {"symbol": "sh.688001", "name": "科创样本", "has_data": True, "last": "2026-01-05"},
                    {"symbol": "bj.920001", "name": "北交样本", "has_data": True, "last": "2026-01-05"},
                ]
            },
            _path=lambda symbol: SimpleNamespace(stat=lambda: file_stat),
            bars=lambda symbol, asof: bars,
            theory=lambda symbol, asof: theory(),
        )
        repository = SimpleNamespace(
            tdx=tdx,
            _run=lambda run: None,
            strategy_config=lambda run, variant: {},
            runs={"test": {"report": {"data": {"end": "2026-01-05"}}}},
        )
        scanner = StructureScanner(repository)
        params = dict(self.params, source="tdx")
        job_id = scanner.start(params)["id"]
        for _ in range(200):
            job = scanner.get(job_id)
            if job["status"] not in ("running", "cancelling"):
                break
            time.sleep(0.01)
        self.assertEqual(job["status"], "completed")
        self.assertEqual({row["symbol"] for row in job["results"]}, {"sh.688001", "bj.920001"})
        self.assertEqual(job["algorithm_version"], scanner.algorithm_version())
        self.assertEqual(len(job["data_version"]), 64)
        self.assertEqual(scanner.latest_tdx_session(), "2026-01-05")

    def test_akshare_current_stock_query_reuses_online_bars_and_theory(self):
        bars = [
            SimpleNamespace(
                timestamp=datetime(2026, 1, day),
                open=10.0,
                high=12.0,
                low=9.0,
                close=11.0,
                volume=1000.0,
            )
            for day in range(1, 6)
        ]
        akshare = SimpleNamespace(
            catalog=lambda: {"stocks": [{"symbol": "sh.600000", "name": "在线样本"}]},
        )
        market_data = SimpleNamespace(
            catalog=Mock(return_value={"stocks": [{"symbol": "sh.600000", "name": "在线样本"}]}),
            window=Mock(
                return_value=SimpleNamespace(
                    bars=tuple(bars),
                    requested_source="akshare",
                    resolved_source="akshare",
                    providers=("akshare",),
                    supplemented_bars=0,
                )
            ),
            theory=Mock(return_value=theory()),
        )
        repository = SimpleNamespace(
            akshare=akshare,
            market_data=market_data,
            _run=lambda run: None,
            strategy_config=Mock(return_value={}),
        )
        scanner = StructureScanner(repository)
        response = scanner.current_akshare(
            dict(self.params, source="akshare", symbol="sh.600000")
        )
        self.assertEqual(response["status"], "ready")
        self.assertEqual(response["total"], 1)
        self.assertEqual(response["matched_stocks"], 1)
        self.assertEqual(response["results"][0]["name"], "在线样本")
        self.assertEqual(response["snapshot"]["source"], "akshare")
        self.assertEqual(response["snapshot"]["resolved_source"], "akshare")
        self.assertEqual(response["snapshot"]["providers"], ["akshare"])
        market_data.catalog.assert_not_called()
        market_data.window.assert_called_once_with("akshare", "sh.600000", "2026-01-05")
        market_data.theory.assert_called_once_with("akshare", "sh.600000", "2026-01-05")


if __name__ == "__main__":
    unittest.main()
