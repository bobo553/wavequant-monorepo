from datetime import date, datetime
import unittest
from zoneinfo import ZoneInfo

from wavequant.infrastructure.market_data.akshare import AkShareProvider, AkShareUnavailable
from wavequant.interfaces.charts.akshare_browser import AkShareBrowser


class Frame:
    def __init__(self, rows):
        self.rows = rows
        self.columns = list(rows[0]) if rows else []

    def to_dict(self, orientation):
        if orientation != "records":
            raise AssertionError("unexpected orientation")
        return [dict(row) for row in self.rows]


class Client:
    __version__ = "1.18.94"

    def __init__(self):
        self.catalog_calls = 0
        self.history_calls = []

    def stock_info_a_code_name(self):
        self.catalog_calls += 1
        return Frame(
            [
                {"code": "600519", "name": "贵州茅台"},
                {"code": "000001", "name": "平安银行"},
                {"code": "920001", "name": "北交示例"},
                {"code": "510300", "name": "非 A 股基金"},
            ]
        )

    def tool_trade_date_hist_sina(self):
        return Frame(
            [
                {"trade_date": date(2026, 9, 11)},
                {"trade_date": date(2026, 9, 14)},
                {"trade_date": date(2026, 9, 15)},
            ]
        )

    def stock_zh_a_hist(self, **kwargs):
        self.history_calls.append(kwargs)
        return Frame(
            [
                {"日期": date(2026, 1, 2), "开盘": 10, "最高": 12, "最低": 9, "收盘": 11, "成交量": 123},
                {"日期": date(2026, 1, 5), "开盘": 11, "最高": 13, "最低": 10, "收盘": 12, "成交量": 456},
            ]
        )


class AkShareBrowserTests(unittest.TestCase):
    def setUp(self):
        self.client = Client()
        self.provider = AkShareProvider(self.client, timeout=2)
        self.browser = AkShareBrowser(self.provider, catalog_ttl=60, history_ttl=60)

    def test_catalog_normalizes_a_share_codes_and_caches(self):
        first = self.browser.catalog()
        second = self.browser.catalog()
        self.assertIs(first, second)
        self.assertEqual(self.client.catalog_calls, 1)
        self.assertEqual([row["symbol"] for row in first["stocks"]], ["sh.600519", "sz.000001", "bj.920001"])
        self.assertEqual(first["provider_version"], "1.18.94")

    def test_catalog_latest_is_last_completed_china_market_session(self):
        before_ready = AkShareBrowser(
            self.provider,
            clock=lambda: datetime(2026, 9, 15, 16, 59, tzinfo=ZoneInfo("Asia/Shanghai")),
        ).catalog()
        after_ready = AkShareBrowser(
            self.provider,
            clock=lambda: datetime(2026, 9, 15, 17, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        ).catalog()
        tokyo_midnight = AkShareBrowser(
            self.provider,
            clock=lambda: datetime(2026, 9, 16, 0, 30, tzinfo=ZoneInfo("Asia/Tokyo")),
        ).catalog()

        self.assertEqual(before_ready["latest"], "2026-09-14")
        self.assertEqual(after_ready["latest"], "2026-09-15")
        self.assertEqual(tokyo_midnight["latest"], "2026-09-15")
        self.assertTrue(all(stock["last"] == "2026-09-15" for stock in tokyo_midnight["stocks"]))

    def test_view_uses_unadjusted_daily_data_and_converts_lots_to_shares(self):
        view = self.browser.view("sh.600519", "2026-01-03")
        self.assertEqual(view["asof"], "2026-01-02")
        self.assertEqual(view["requested_asof"], "2026-01-03")
        self.assertEqual(view["price_basis"], "raw_unadjusted")
        self.assertEqual(view["history_endpoint"], "stock_zh_a_hist")
        self.assertEqual(view["bars"][0]["volume"], 12_300)
        self.assertEqual(view["sessions"], ["2026-01-02", "2026-01-05"])
        self.assertIsNone(view["metrics"])
        self.assertEqual(
            self.client.history_calls[0],
            {
                "symbol": "600519",
                "period": "daily",
                "start_date": "19900101",
                "end_date": date.today().strftime("%Y%m%d"),
                "adjust": "",
                "timeout": 2.0,
            },
        )

    def test_theory_only_uses_requested_prefix(self):
        theory = self.browser.theory("sz.000001", "2026-01-02")
        self.assertEqual(theory["asof"], "2026-01-02")
        self.assertEqual(theory["computed_from"], "akshare_raw_prefix_display_only")

    def test_rejects_invalid_symbols_dates_and_provider_schema(self):
        for symbol in ("../600519", "hk.600519", "sh.510300", "sz.600519"):
            with self.assertRaises(ValueError):
                self.browser.view(symbol, "2026-01-03")
        with self.assertRaises(ValueError):
            self.browser.view("sh.600519", "2999-01-01")

        class BadClient(Client):
            def stock_info_a_code_name(self):
                return Frame([{"unexpected": "field"}])

        bad = AkShareBrowser(AkShareProvider(BadClient(), timeout=2))
        with self.assertRaises(AkShareUnavailable):
            bad.catalog()

    def test_history_falls_back_to_fast_sina_interface_and_bypasses_primary_temporarily(self):
        class FallbackClient(Client):
            def __init__(self):
                super().__init__()
                self.primary_calls = 0

            def stock_zh_a_hist(self, **kwargs):
                self.primary_calls += 1
                raise ConnectionError("primary unavailable")

            def stock_zh_a_daily(self, **kwargs):
                self.history_calls.append(kwargs)
                return Frame(
                    [
                        {
                            "date": date(2026, 1, 2),
                            "open": 10,
                            "high": 12,
                            "low": 9,
                            "close": 11,
                            "volume": 12_300,
                        }
                    ]
                )

        client = FallbackClient()
        browser = AkShareBrowser(AkShareProvider(client, timeout=2))
        view = browser.view("sh.600519", "2026-01-03")
        self.assertEqual(view["history_endpoint"], "stock_zh_a_daily")
        self.assertEqual(view["bars"][0]["volume"], 12_300)
        self.assertEqual(client.history_calls[0]["symbol"], "sh600519")
        second = browser.view("sz.000001", "2026-01-03")
        self.assertEqual(second["history_endpoint"], "stock_zh_a_daily")
        self.assertEqual(client.primary_calls, 1)
        self.assertEqual(client.history_calls[1]["symbol"], "sz000001")

    def test_history_uses_tencent_when_primary_and_sina_are_unavailable(self):
        class TencentFallbackClient(Client):
            def __init__(self):
                super().__init__()
                self.sina_calls = 0

            def stock_zh_a_hist(self, **kwargs):
                raise ConnectionError("primary unavailable")

            def stock_zh_a_daily(self, **kwargs):
                self.sina_calls += 1
                raise ConnectionError("sina unavailable")

            def stock_zh_a_hist_tx(self, **kwargs):
                self.history_calls.append(kwargs)
                return Frame(
                    [
                        {
                            "date": date(2026, 1, 2),
                            "open": 10,
                            "high": 12,
                            "low": 9,
                            "close": 11,
                            "volume": 12_300,
                        }
                    ]
                )

        client = TencentFallbackClient()
        browser = AkShareBrowser(AkShareProvider(client, timeout=2))
        view = browser.view("sh.600519", "2026-01-03")
        self.assertEqual(view["history_endpoint"], "stock_zh_a_hist_tx")
        self.assertEqual(view["bars"][0]["volume"], 12_300)
        self.assertEqual(client.history_calls[0]["symbol"], "sh600519")
        self.assertNotEqual(client.history_calls[0]["start_date"], "19900101")
        self.assertEqual(len(client.history_calls[0]["start_date"]), 8)
        second = browser.view("sz.000001", "2026-01-03")
        self.assertEqual(second["history_endpoint"], "stock_zh_a_hist_tx")
        self.assertEqual(client.sina_calls, 1)
        self.assertEqual(client.history_calls[1]["symbol"], "sz000001")

    def test_provider_hides_upstream_exception_details(self):
        class FailedClient:
            def stock_info_a_code_name(self):
                raise RuntimeError("secret upstream payload")

        with self.assertRaisesRegex(AkShareUnavailable, "上游请求失败") as caught:
            AkShareProvider(FailedClient(), timeout=2).call("stock_info_a_code_name")
        self.assertNotIn("secret upstream payload", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
