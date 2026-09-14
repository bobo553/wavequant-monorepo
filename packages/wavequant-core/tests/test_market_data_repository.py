"""Provider switching is verified at the canonical repository boundary."""

from datetime import datetime
import unittest

from wavequant.domain.models.model import Bar
from wavequant.infrastructure.market_data.akshare import AkShareUnavailable
from wavequant.interfaces.charts.market_data_repository import MarketDataRepository


def bar(day: int, close: float, *, symbol: str = "sh.600000") -> Bar:
    """Build the smallest valid daily bar needed by repository tests."""

    return Bar(datetime(2026, 1, day), symbol, close, close + 1, close - 1, close, 1_000)


class FakeAdapter:
    """Deterministic provider fake implementing the public adapter port."""

    def __init__(self, source, rows, stocks, *, error=None):
        self.source = source
        self.rows = rows
        self.stocks = stocks
        self.error = error

    def catalog(self):
        if self.error:
            raise self.error
        return {"available": True, "stocks": self.stocks, "with_daily": len(self.stocks), "warnings": []}

    def load(self, symbol, asof):
        if self.error:
            raise self.error
        complete = list(self.rows[symbol])
        prefix = [value for value in complete if value.timestamp.date().isoformat() <= asof]
        return prefix, complete

    def metadata(self):
        return {"provider_version": "fake"} if self.source == "akshare" else {}


class MarketDataRepositoryTests(unittest.TestCase):
    def test_default_catalog_uses_akshare_and_supplements_missing_metadata_and_stocks(self):
        online = FakeAdapter(
            "akshare",
            {"sh.600000": [bar(1, 10)]},
            [{"symbol": "sh.600000", "name": "", "has_data": True}],
        )
        local = FakeAdapter(
            "tdx",
            {"sh.600000": [bar(1, 9)], "sz.000001": [bar(1, 8, symbol="sz.000001")]},
            [
                {"symbol": "sh.600000", "name": "浦发银行", "has_data": True},
                {"symbol": "sz.000001", "name": "平安银行", "has_data": True},
            ],
        )

        catalog = MarketDataRepository([online, local]).catalog()

        self.assertEqual(catalog["source"], "akshare")
        self.assertEqual(catalog["default_source"], "akshare")
        self.assertEqual(catalog["supplemented_stocks"], 1)
        self.assertEqual([stock["symbol"] for stock in catalog["stocks"]], ["sh.600000", "sz.000001"])
        self.assertEqual(catalog["stocks"][0]["name"], "浦发银行")
        self.assertEqual(catalog["stocks"][1]["catalog_source"], "tdx")

    def test_stale_primary_keeps_primary_values_and_adds_missing_fallback_sessions(self):
        online = FakeAdapter(
            "akshare",
            {"sh.600000": [bar(1, 10), bar(2, 12)]},
            [{"symbol": "sh.600000", "name": "浦发银行", "has_data": True}],
        )
        local = FakeAdapter(
            "tdx",
            {"sh.600000": [bar(1, 99), bar(2, 99), bar(3, 13)]},
            [{"symbol": "sh.600000", "name": "浦发银行", "has_data": True}],
        )

        window = MarketDataRepository([online, local]).window("akshare", "sh.600000", "2026-01-03")

        self.assertEqual(window.resolved_source, "akshare")
        self.assertEqual(window.providers, ("akshare", "tdx"))
        self.assertEqual(window.supplemented_bars, 1)
        self.assertEqual([value.close for value in window.bars], [10, 12, 13])

    def test_unavailable_primary_falls_back_without_changing_requested_contract(self):
        online = FakeAdapter("akshare", {}, [], error=AkShareUnavailable("online timeout"))
        local = FakeAdapter(
            "tdx",
            {"sh.600000": [bar(1, 10), bar(2, 11)]},
            [{"symbol": "sh.600000", "name": "浦发银行", "has_data": True}],
        )

        view = MarketDataRepository([online, local]).view("akshare", "sh.600000", "2026-01-02")

        self.assertEqual(view["data_source"], "akshare")
        self.assertEqual(view["resolved_source"], "tdx")
        self.assertTrue(view["source_fallback"])
        self.assertEqual(len(view["bars"]), 2)

    def test_provider_switches_share_the_same_domain_theory_contract(self):
        rows = [bar(1, 10), bar(2, 12), bar(3, 11), bar(4, 13)]
        stocks = [{"symbol": "sh.600000", "name": "浦发银行", "has_data": True}]
        repository = MarketDataRepository(
            [FakeAdapter("akshare", {"sh.600000": rows}, stocks), FakeAdapter("tdx", {"sh.600000": rows}, stocks)]
        )

        online = repository.theory("akshare", "sh.600000", "2026-01-04")
        local = repository.theory("tdx", "sh.600000", "2026-01-04")

        self.assertEqual(online["computed_from"], "canonical_market_data_repository")
        self.assertEqual(online["data_version"], local["data_version"])
        for field in ("lecture_drawing", "reversal_trends", "secondary_trends", "tertiary_trends"):
            self.assertEqual(online[field], local[field])


if __name__ == "__main__":
    unittest.main()
