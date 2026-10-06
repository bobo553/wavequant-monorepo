"""Source close permissions distinguish a locked opening from an observed nonflat session."""

from dataclasses import asdict, replace
from datetime import date, datetime, timedelta
import csv
import json
import struct
import sys
from types import ModuleType

import pytest

from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.integrated_strategy import SystemResult
from wavequant.infrastructure.market_data import data
from wavequant.infrastructure.market_data.io import load_bars
from wavequant.infrastructure.market_data.minute import TdxMinuteSource
from wavequant.infrastructure.market_data.tdx import adjust_rows
from wavequant.interfaces.research_tools.tdx_backtest import decode_research, encode_research


def raw_row():
    return dict(date="2025-04-25", isST="0", tradestatus="1", preclose="13.92",
                open="12.53", high="14.30", low="12.53", close="12.53", volume="97118640")


@pytest.mark.parametrize("changes,symbol,expected", [
    ({}, "sh.601086", (False, True, False, True)),
    ({"high": "12.53"}, "sh.601086", (False, False, False, True)),
    ({"tradestatus": "0"}, "sh.601086", (False, False, False, False)),
    ({"volume": "0"}, "sh.601086", (False, False, False, False)),
    ({"preclose": "10", "open": "9.50", "low": "9.50", "close": "9.50", "high": "10", "isST": "1"},
     "sh.601086", (False, True, True, True)),
    ({"preclose": "10", "open": "7", "low": "7", "close": "7", "high": "10"},
     "bj.920001", (False, True, False, True)),
    ({"preclose": "10", "open": "9.5", "low": "9.1", "close": "9.7", "high": "10.8"},
     "sh.601086", (True, True, False, True)),
])
def test_raw_price_status_and_board_define_close_permissions(changes, symbol, expected):
    row = raw_row()
    row.update(changes)
    permissions = data.close_sell_permissions(row, symbol)
    assert tuple(permissions[key] for key in (
        "close_sellable", "nonflat_close_sellable", "raw_is_st", "raw_trading_active")) == expected


@pytest.mark.parametrize("changes,symbol,floor", [
    ({}, "sh.601086", 12.53),
    ({"preclose": "10", "isST": "1"}, "sh.601086", 9.5),
    ({"preclose": "10"}, "bj.920001", 7.0),
])
def test_raw_limit_floor_matches_permission_boundary_for_slippage_clamping(changes, symbol, floor):
    row = raw_row()
    row.update(changes)
    assert data.lower_limit_price(row, symbol) == floor
    row["open"] = str(floor)
    assert data.opening_permissions(row, symbol)[1] is False
    row["open"] = str(floor + 0.01)
    assert data.opening_permissions(row, symbol)[1] is True


def test_tdx_retains_locked_open_and_derives_nonflat_close_from_raw_prices():
    raw = [
        dict(date=date(2025, 4, 24), open=13.0, high=13.92, low=13.0, close=13.92, volume=1000),
        dict(date=date(2025, 4, 25), open=12.53, high=14.30, low=12.53, close=12.53, volume=97118640),
    ]
    rows = adjust_rows(raw, [], date(2025, 4, 24), date(2025, 4, 25),
                       "sh.601086", include_close_permission=True)
    assert rows[-1]["sellable"] == 0
    assert rows[-1]["close_sellable"] is False
    assert rows[-1]["nonflat_close_sellable"] is True
    assert rows[-1]["raw_is_st"] is False
    assert rows[-1]["raw_trading_active"] is True


@pytest.mark.parametrize("prices", [(12.53, 14.30, 12.53, 12.53), (5.03, 5.11, 5.01, 5.02)])
def test_native_tdx_float32_prices_restore_cent_quotes_and_reconcile_daily(tmp_path, prices):
    source = TdxMinuteSource(tmp_path)
    path = source.path("sh.601086")
    path.parent.mkdir(parents=True)
    packed_date = (2025 - 2004) * 2048 + 4 * 100 + 25
    clocks = [9 * 60 + 35 + index * 5 for index in range(24)]
    clocks.extend(13 * 60 + 5 + index * 5 for index in range(24))
    layout = struct.Struct("<HHfffffII")
    path.write_bytes(b"".join(layout.pack(packed_date, clock, *prices, 0, 1000, 0)
                              for clock in clocks))
    daily = Bar(datetime(2025, 4, 25), "sh.601086", open=prices[0], high=prices[1],
                low=prices[2], close=prices[3], volume=48000)
    minutes = source.get(daily)
    assert len(minutes) == 48
    assert all((bar.open, bar.high, bar.low, bar.close) == prices for bar in minutes)
    assert source.provenance()["session_sha256"]
    if prices[0] == 12.53:
        assert minutes[0].open == data.lower_limit_price(raw_row(), daily.symbol)
    with pytest.raises(ValueError, match="同源日线 OHLC 不一致"):
        source.get(replace(daily, high=daily.high + 0.5))


@pytest.mark.parametrize("symbol", ["sh.510300", "sz.123456"])
def test_native_tdx_other_securities_keep_the_original_float32_price_precision(tmp_path, symbol):
    source = TdxMinuteSource(tmp_path)
    path = source.path(symbol)
    path.parent.mkdir(parents=True)
    packed_date = (2025 - 2004) * 2048 + 4 * 100 + 25
    prices = (0.123, 0.125, 0.122, 0.124)
    layout = struct.Struct("<HHfffffII")
    clocks = [9 * 60 + 35 + index * 5 for index in range(24)]
    clocks.extend(13 * 60 + 5 + index * 5 for index in range(24))
    payload = b"".join(layout.pack(packed_date, clock, *prices, 0, 1000, 0) for clock in clocks)
    path.write_bytes(payload)
    decoded = layout.unpack_from(payload)[2:6]
    minutes = source.get(Bar(datetime(2025, 4, 25), symbol, open=prices[0], high=prices[1],
                             low=prices[2], close=prices[3], volume=48000))
    assert (minutes[0].open, minutes[0].high, minutes[0].low, minutes[0].close) == decoded


def test_csv_hydration_retains_explicit_close_permissions_and_legacy_custom_veto(tmp_path):
    base = dict(timestamp="2025-04-25", symbol="sh.601086", open=12.53, high=14.30,
                low=12.53, close=12.53, volume=97118640, buyable=1, sellable=0, adjustment_factor=1)
    legacy = tmp_path / "legacy.csv"
    current = tmp_path / "current.csv"
    for path, row in ((legacy, base), (current, dict(base, close_sellable=0,
                            nonflat_close_sellable=1, raw_is_st=0, raw_trading_active=1))):
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(row))
            writer.writeheader()
            writer.writerow(row)
    old_bar = load_bars(legacy)["sh.601086"][0]
    new_bar = load_bars(current)["sh.601086"][0]
    assert old_bar.sellable is False
    assert old_bar.close_sellable is old_bar.nonflat_close_sellable is None
    assert old_bar.raw_is_st is old_bar.raw_trading_active is None
    assert (new_bar.close_sellable, new_bar.nonflat_close_sellable,
            new_bar.raw_is_st, new_bar.raw_trading_active) == (False, True, False, True)


def test_research_roundtrip_preserves_close_source_metadata_and_legacy_none():
    bar = Bar(datetime(2025, 4, 25), "sh.601086", 12.53, 14.30, 12.53, 12.53, 97118640,
              True, False, 1.0, True, True, close_sellable=False,
              nonflat_close_sellable=True, raw_is_st=False, raw_trading_active=True)
    generated = SystemResult([], [], {})
    assert decode_research(encode_research([bar], generated))[0] == [bar]
    legacy = asdict(bar)
    legacy["timestamp"] = bar.timestamp.isoformat()
    for field in ("close_sellable", "nonflat_close_sellable", "raw_is_st", "raw_trading_active"):
        legacy.pop(field)
    recovered = decode_research(dict(bars=[legacy], signals=[], audit=[], counts={}))[0][0]
    assert recovered.sellable is False
    assert recovered.close_sellable is recovered.nonflat_close_sellable is None
    assert recovered.raw_is_st is recovered.raw_trading_active is None


def test_baostock_cached_raw_status_and_prices_are_preserved_in_csv(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "baostock", ModuleType("baostock"))
    cache = tmp_path / "cache"
    cache.mkdir()
    raw = dict(raw_row(), code="sh.601086")
    adjusted = dict(raw, open="25.06", high="28.60", low="25.06", close="25.06")
    source = dict(raw=[raw], adjusted=[adjusted], retrieved_at="2026-10-06T00:00:00+00:00")
    (cache / "sh.601086_2025-04-25_2025-04-25.json").write_text(json.dumps(source), encoding="utf-8")
    target = tmp_path / "daily.csv"
    data.fetch_baostock(target, ["sh.601086"], "2025-04-25", "2025-04-25", cache=cache)
    bar = load_bars(target)["sh.601086"][0]
    assert (bar.close, bar.adjustment_factor, bar.sellable) == (25.06, 2.0, False)
    assert (bar.close_sellable, bar.nonflat_close_sellable,
            bar.raw_is_st, bar.raw_trading_active) == (False, True, False, True)


def test_sina_adapter_versions_permission_source_and_reuses_only_new_schema(tmp_path, monkeypatch):
    import wavequant.interfaces.research_tools.akshare_backtest as module
    from wavequant.infrastructure.market_data.akshare import AkShareProvider
    from wavequant.interfaces.charts.akshare_browser import AkShareBrowser

    class Frame:
        columns = ["date", "hfq_factor"]

        def to_dict(self, _):
            return [dict(date="2000-01-01", hfq_factor=1)]

    class Provider(AkShareProvider):
        @property
        def version(self) -> str:
            return "frozen-fixture"

        def call(self, name: str, *, call_timeout: float | None = None, **kwargs: object) -> Frame:
            assert name == "stock_zh_a_daily"
            return Frame()

    browser = AkShareBrowser(Provider(), pinned_history=True)
    raw = [Bar(datetime(2025, 4, 4) + timedelta(days=index), "sh.601086",
               13.0, 13.92, 13.0, 13.92, 1000) for index in range(22)]
    raw[-1] = replace(raw[-1], open=12.53, high=14.30, low=12.53, close=12.53, volume=97118640)
    monkeypatch.setattr(browser, "bars", lambda *_: (raw, raw))
    generated = SystemResult([], [], {})
    calls = []

    def generate(*_):
        calls.append(1)
        return generated

    monkeypatch.setattr(module, "generate_system_signals", generate)
    tester = module.AkShareBacktester(browser, tmp_path)
    bars, _, view = tester.run("sh.601086", "2025-04-24", "2025-04-25", {}, StrategyConfig().to_dict())
    assert bars[-1].sellable is False
    assert (bars[-1].close_sellable, bars[-1].nonflat_close_sellable,
            bars[-1].raw_is_st, bars[-1].raw_trading_active) == (False, True, False, True)
    assert view["backtest"]["source"]["bar_permission_schema"] == data.BAR_PERMISSION_SCHEMA
    assert tester.run("sh.601086", "2025-04-24", "2025-04-25", {}, StrategyConfig().to_dict())[0] == bars
    assert len(calls) == 1

    old_source = {key: value for key, value in view["backtest"]["source"].items()
                  if key not in ("minute", "bar_permission_schema")}
    old_inputs = dict(symbol="sh.601086", start="2025-04-24", asof="2025-04-25",
                      source=old_source, strategy={})
    old_research = encode_research(bars, generated)
    for row in old_research["bars"]:
        for field in ("close_sellable", "nonflat_close_sellable", "raw_is_st", "raw_trading_active"):
            row.pop(field)
    legacy_cache = module.AkShareBacktester(browser, tmp_path / "legacy-cache")
    legacy_cache.artifacts.put("akshare-signals", old_inputs, old_research)
    rehydrated = legacy_cache.run("sh.601086", "2025-04-24", "2025-04-25", {}, StrategyConfig().to_dict())[0]
    assert len(calls) == 2
    assert rehydrated[-1].nonflat_close_sellable is True
