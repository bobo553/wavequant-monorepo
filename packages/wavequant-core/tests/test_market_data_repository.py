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
        self.catalog_calls = 0
        self.load_calls = 0

    def catalog(self):
        self.catalog_calls += 1
        if self.error:
            raise self.error
        return {"available": True, "stocks": self.stocks, "with_daily": len(self.stocks), "warnings": []}

    def load(self, symbol, asof):
        self.load_calls += 1
        if self.error:
            raise self.error
        complete = list(self.rows[symbol])
        prefix = [value for value in complete if value.timestamp.date().isoformat() <= asof]
        return prefix, complete

    def metadata(self):
        return {"provider_version": "fake"} if self.source == "akshare" else {}


class MarketDataRepositoryTests(unittest.TestCase):
    def test_default_catalog_contains_only_akshare_stocks_and_metadata(self):
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
        self.assertEqual(catalog["providers"], ["akshare"])
        self.assertEqual(catalog["supplemented_stocks"], 0)
        self.assertEqual([stock["symbol"] for stock in catalog["stocks"]], ["sh.600000"])
        self.assertEqual(catalog["stocks"][0]["name"], "")
        self.assertEqual(catalog["stocks"][0]["catalog_source"], "akshare")
        self.assertEqual(online.catalog_calls, 1)
        self.assertEqual(local.catalog_calls, 0)

    def test_stale_akshare_window_does_not_append_tdx_sessions(self):
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
        self.assertEqual(window.providers, ("akshare",))
        self.assertEqual(window.supplemented_bars, 0)
        self.assertEqual(window.asof, "2026-01-02")
        self.assertEqual([value.close for value in window.bars], [10, 12])
        self.assertEqual(local.load_calls, 0)

    def test_unavailable_akshare_fails_without_testing_tdx(self):
        online = FakeAdapter("akshare", {}, [], error=AkShareUnavailable("online timeout"))
        local = FakeAdapter(
            "tdx",
            {"sh.600000": [bar(1, 10), bar(2, 11)]},
            [{"symbol": "sh.600000", "name": "浦发银行", "has_data": True}],
        )

        with self.assertRaisesRegex(AkShareUnavailable, "online timeout"):
            MarketDataRepository([online, local]).view("akshare", "sh.600000", "2026-01-02")
        self.assertEqual(local.load_calls, 0)

    def test_unavailable_akshare_catalog_fails_without_testing_tdx(self):
        online = FakeAdapter("akshare", {}, [], error=AkShareUnavailable("online timeout"))
        local = FakeAdapter(
            "tdx",
            {"sh.600000": [bar(1, 10)]},
            [{"symbol": "sh.600000", "name": "浦发银行", "has_data": True}],
        )

        with self.assertRaisesRegex(AkShareUnavailable, "online timeout"):
            MarketDataRepository([online, local]).catalog("akshare")
        self.assertEqual(local.catalog_calls, 0)

    def test_short_akshare_history_is_not_backfilled_from_tdx(self):
        online = FakeAdapter(
            "akshare",
            {"sh.600000": [bar(2, 12), bar(3, 13)]},
            [{"symbol": "sh.600000", "name": "浦发银行", "has_data": True}],
        )
        local = FakeAdapter(
            "tdx",
            {"sh.600000": [bar(1, 10), bar(2, 99)]},
            [{"symbol": "sh.600000", "name": "浦发银行", "has_data": True}],
        )

        window = MarketDataRepository([online, local]).window("akshare", "sh.600000", "2026-01-03")

        self.assertEqual(window.providers, ("akshare",))
        self.assertEqual(window.supplemented_bars, 0)
        self.assertEqual([value.close for value in window.bars], [12, 13])
        self.assertEqual(local.load_calls, 0)

    def test_explicit_tdx_selection_never_reads_akshare(self):
        online = FakeAdapter(
            "akshare",
            {"sh.600000": [bar(1, 99)]},
            [{"symbol": "sh.600000", "name": "网络证券", "has_data": True}],
        )
        local = FakeAdapter(
            "tdx",
            {"sh.600000": [bar(1, 10)]},
            [{"symbol": "sh.600000", "name": "本地证券", "has_data": True}],
        )
        repository = MarketDataRepository([online, local])

        catalog = repository.catalog("tdx")
        view = repository.view("tdx", "sh.600000", "2026-01-02")

        self.assertEqual(catalog["providers"], ["tdx"])
        self.assertEqual(catalog["stocks"][0]["name"], "本地证券")
        self.assertEqual(view["resolved_source"], "tdx")
        self.assertEqual(view["providers"], ["tdx"])
        self.assertEqual(view["source_policy"], "single_upstream_v1")
        self.assertFalse(view["source_fallback"])
        self.assertEqual(view["supplemented_bars"], 0)
        self.assertEqual(view["bars"][0]["close"], 10)
        self.assertEqual(online.catalog_calls, 0)
        self.assertEqual(online.load_calls, 0)

    def test_unavailable_tdx_fails_without_testing_akshare(self):
        online = FakeAdapter(
            "akshare",
            {"sh.600000": [bar(1, 99)]},
            [{"symbol": "sh.600000", "name": "网络证券", "has_data": True}],
        )
        local = FakeAdapter("tdx", {}, [], error=FileNotFoundError("local TDX missing"))
        repository = MarketDataRepository([online, local])

        with self.assertRaisesRegex(ValueError, "tdx 行情目录暂不可用"):
            repository.catalog("tdx")
        with self.assertRaisesRegex(ValueError, "tdx 数据暂不可用"):
            repository.window("tdx", "sh.600000", "2026-01-02")

        self.assertEqual(online.catalog_calls, 0)
        self.assertEqual(online.load_calls, 0)

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
