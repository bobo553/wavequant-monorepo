"""Calendar timeframe aggregation remains separate from daily signal snapshots."""

from __future__ import annotations

from datetime import date
import unittest

from wavequant_api.application.market_timeframes import MarketTimeframeService


def row(value: str, open_: float, high: float, low: float, close: float, volume: float) -> dict[str, object]:
    return {
        "time": value,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "raw_close": close,
        "volume": volume,
        "factor": 1,
    }


class FakeDailyRepository:
    def __init__(self) -> None:
        self.rows = [
            row("2025-12-29", 9, 12, 8, 11, 100),
            row("2025-12-30", 11, 14, 10, 13, 200),
            row("2026-01-02", 13, 15, 12, 14, 300),
            row("2026-01-05", 14, 16, 13, 15, 400),
            row("2026-01-07", 15, 17, 14, 16, 500),
            row("2026-01-09", 16, 18, 15, 17, 600),
            row("2026-03-31", 17, 19, 16, 18, 700),
            row("2026-04-01", 18, 20, 17, 19, 800),
        ]

    def view(self, source: str, symbol: str, asof: str) -> dict[str, object]:
        bars = [item for item in self.rows if str(item["time"]) <= asof]
        return {
            "symbol": symbol,
            "asof": str(bars[-1]["time"]),
            "requested_asof": asof,
            "result_scope": source,
            "data_source": source,
            "resolved_source": source,
            "providers": [source],
            "supplemented_bars": 0,
            "source_fallback": False,
            "source_warning": None,
            "data_version": "daily-version",
            "price_basis": "raw_unadjusted",
            "sessions": [str(item["time"]) for item in self.rows],
            "bars": bars,
            "markers": [],
            "signals": [],
            "orders": [],
            "trades": [],
            "curve": [],
            "metrics": None,
            "evidence": "规范日线。",
        }

    def theory(self, source: str, symbol: str, asof: str) -> dict[str, object]:
        return {"asof": asof, "data_source": source, "symbol": symbol, "computed_from": "daily"}


class MarketTimeframeServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = MarketTimeframeService(FakeDailyRepository())

    def test_weekly_uses_first_open_extremes_last_close_volume_sum_and_last_session(self) -> None:
        weekly = self.service.view("tdx", "sh.600000", "2026-01-09", "1w")

        self.assertEqual(weekly["timeframe_label"], "周线")
        self.assertEqual(weekly["sessions"], ["2026-01-02", "2026-01-09", "2026-04-01"])
        self.assertEqual(
            weekly["bars"][0],
            {
                "time": "2026-01-02",
                "open": 9.0,
                "high": 15.0,
                "low": 8.0,
                "close": 14.0,
                "raw_close": 14.0,
                "volume": 600.0,
                "factor": 1.0,
            },
        )
        self.assertFalse(weekly["is_partial_last_bar"])

    def test_partial_week_replaces_the_future_period_close_in_replay_sessions(self) -> None:
        weekly = self.service.view("tdx", "sh.600000", "2026-01-07", "1w")

        self.assertTrue(weekly["is_partial_last_bar"])
        self.assertEqual([item["time"] for item in weekly["bars"]], ["2026-01-02", "2026-01-07"])
        self.assertEqual(weekly["sessions"][:2], ["2026-01-02", "2026-01-07"])
        self.assertNotIn("2026-01-09", weekly["sessions"])

    def test_month_quarter_and_year_follow_calendar_boundaries(self) -> None:
        monthly = self.service.view("akshare", "sh.600000", "2026-04-01", "1mo")
        quarterly = self.service.view("akshare", "sh.600000", "2026-04-01", "3mo")
        yearly = self.service.view("akshare", "sh.600000", "2026-04-01", "1y")

        self.assertEqual(
            [item["time"] for item in monthly["bars"]], ["2025-12-30", "2026-01-09", "2026-03-31", "2026-04-01"]
        )
        self.assertEqual([item["time"] for item in quarterly["bars"]], ["2025-12-30", "2026-03-31", "2026-04-01"])
        self.assertEqual([item["time"] for item in yearly["bars"]], ["2025-12-30", "2026-04-01"])
        self.assertEqual(quarterly["bars"][1]["volume"], 2_500.0)
        self.assertEqual(yearly["bars"][1]["volume"], 3_300.0)

    def test_theory_uses_the_same_aggregated_version_and_daily_contract_remains_compatible(self) -> None:
        weekly = self.service.view("tdx", "sh.600000", "2026-04-01", "1w")
        theory = self.service.theory("tdx", "sh.600000", "2026-04-01", "1w")
        daily = self.service.view("tdx", "sh.600000", "2026-04-01")
        daily_theory = self.service.theory("tdx", "sh.600000", "2026-04-01")

        self.assertEqual(theory["data_version"], weekly["data_version"])
        self.assertEqual(theory["timeframe"], "1w")
        self.assertEqual(theory["computed_from"], "api_calendar_timeframe_from_canonical_daily")
        self.assertEqual(daily["data_version"], "daily-version")
        self.assertEqual(daily_theory["computed_from"], "daily")

    def test_unknown_timeframe_is_rejected_before_reading_market_data(self) -> None:
        with self.assertRaisesRegex(ValueError, "周期仅支持"):
            self.service.view("tdx", "sh.600000", date.today().isoformat(), "5m")


if __name__ == "__main__":
    unittest.main()
