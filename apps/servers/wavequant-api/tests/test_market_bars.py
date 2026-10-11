"""年度行情分页先返回 OHLC，完整趋势与周期快照不参与首屏请求。"""

from collections.abc import Callable, Iterator, Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from http.client import HTTPConnection
import hashlib
import json
from threading import Thread
from time import monotonic
from types import SimpleNamespace
from typing import cast
from urllib.parse import urlencode
from unittest.mock import Mock, patch

import pytest

# 与 API 既定类型门禁保持安装后的 Core 分发边界，其尚无 py.typed。
from wavequant.domain.models.model import Bar  # type: ignore[import-untyped]
from wavequant.interfaces.charts.market_data_repository import MarketDataRepository  # type: ignore[import-untyped]
from wavequant_api.application.market_bars import (
    MarketBarsRequest, MarketBarsService, MarketBarsVersionChanged, market_bars_etag, parse_market_bars_query,
)
from wavequant_api.application.market_timeframes import MarketTimeframeService, MarketTimeframeSnapshotStore
from wavequant_api.server import make_server


def row(day: str, open_: float, high: float, low: float, close: float, volume: float) -> dict[str, object]:
    return dict(time=day, open=open_, high=high, low=low, close=close, raw_close=close, volume=volume, factor=1)


class DailyRepository:
    def __init__(self) -> None:
        self.rows = [
            row("2023-12-29", 8, 11, 7, 10, 90),
            row("2025-12-29", 9, 12, 8, 11, 100),
            row("2025-12-30", 11, 14, 10, 13, 200),
            row("2026-01-02", 13, 15, 12, 14, 300),
            row("2026-01-05", 14, 16, 13, 15, 400),
        ]
        self.reads = 0

    def view(self, source: str, symbol: str, asof: str) -> dict[str, object]:
        self.reads += 1
        bars = [dict(item) for item in self.rows if str(item["time"]) <= asof]
        return {
            "symbol": symbol, "asof": bars[-1]["time"], "requested_asof": asof,
            "result_scope": source, "data_source": "provider display name", "resolved_source": source,
            "providers": [source], "supplemented_bars": 0, "source_fallback": False, "source_warning": None,
            "data_version": hashlib.sha256(json.dumps(bars, separators=(",", ":")).encode()).hexdigest(),
            "price_basis": "raw_unadjusted", "fetched_at": f"2026-01-05T12:00:{self.reads:02}+09:00",
            "sessions": [str(item["time"]) for item in self.rows], "bars": bars,
            "markers": [], "signals": [], "orders": [], "trades": [], "curve": [], "metrics": None,
            "evidence": "规范原始日线。",
        }

    def theory(
        self, source: str, symbol: str, asof: str, *, n_target_trend_confirmation_enabled: bool = False,
    ) -> dict[str, object]:
        raise AssertionError("annual OHLC must not compute daily theory")


def service(
    repository: DailyRepository, *, clock: Callable[[], float] = monotonic, cache_capacity: int = 16,
) -> MarketBarsService:
    snapshots = Mock(spec=["find_market_timeframe_snapshot", "save_market_timeframe_snapshot"])
    snapshots.find_market_timeframe_snapshot.side_effect = AssertionError("annual OHLC must not read SQL snapshots")
    snapshots.save_market_timeframe_snapshot.side_effect = AssertionError("annual OHLC must not write SQL snapshots")
    periods = MarketTimeframeService(repository, snapshots=cast(MarketTimeframeSnapshotStore, snapshots))
    return MarketBarsService(periods, clock=clock, cache_capacity=cache_capacity)


class ManualClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def request(
    *, source: str = "tdx", symbol: str = "sz.000678", asof: str = "2026-01-06", timeframe: str = "1d",
    year: int | None = None, data_version: str | None = None,
) -> MarketBarsRequest:
    return MarketBarsRequest(source, symbol, asof, timeframe, year, data_version)


def mapping(value: object) -> Mapping[str, object]:
    assert isinstance(value, Mapping) and all(isinstance(key, str) for key in value)
    return cast(Mapping[str, object], value)


def records(value: object) -> list[Mapping[str, object]]:
    assert isinstance(value, list)
    return [mapping(item) for item in value]


def test_latest_first_pages_follow_real_years_and_keep_one_fixed_prefix() -> None:
    repository = DailyRepository()
    before = deepcopy(repository.rows)
    pages = service(repository)
    latest = pages.page(request())
    prior = pages.page(request(year=latest["next_year"], data_version=latest["data_version"]))
    oldest = pages.page(request(year=prior["next_year"], data_version=latest["data_version"]))
    assert [page["year"] for page in (latest, prior, oldest)] == [2026, 2025, 2023]
    assert latest["available_years"] == prior["available_years"] == oldest["available_years"] == [2026, 2025, 2023]
    assert latest["has_more"] and prior["has_more"] and not oldest["has_more"]
    assert oldest["next_year"] is None
    for page in (latest, prior, oldest):
        assert page["schema_version"] == 1
        assert (page["requested_asof"], page["resolved_asof"]) == ("2026-01-06", "2026-01-05")
        assert (page["history_start"], page["history_end"], page["total_bars"]) == ("2023-12-29", "2026-01-05", 5)
        assert page["data_version"] == latest["data_version"] == page["view"]["data_version"]
        assert page["view"]["asof"] == "2026-01-05"
        bars = records(page["view"]["bars"])
        assert all(str(bar["time"]).startswith(str(page["year"])) for bar in bars)
        assert page["view"]["sessions"] == [bar["time"] for bar in bars]
        assert all(bar["raw_close"] == bar["close"] and bar["factor"] == 1 for bar in bars)
        assert all(page["view"][field] == [] for field in ("markers", "signals", "orders", "trades", "curve"))
        assert page["view"]["metrics"] is None
    assert repository.rows == before
    assert repository.reads == 1


def test_empty_year_skips_to_the_previous_real_year_and_latest_is_not_requested_year() -> None:
    pages = service(DailyRepository())
    latest = pages.page(request(asof="2027-01-01"))
    assert latest["year"] == 2026
    empty = pages.page(request(asof="2027-01-01", year=2024, data_version=latest["data_version"]))
    assert empty["view"]["bars"] == empty["view"]["sessions"] == []
    assert empty["next_year"] == 2023 and empty["has_more"]


def test_week_crossing_year_is_aggregated_before_year_filter_and_version_matches_full_view() -> None:
    repository = DailyRepository()
    full = MarketTimeframeService(repository).view("tdx", "sz.000678", "2026-01-06", "1w")
    page = service(repository).page(request(timeframe="1w"))
    bars = records(page["view"]["bars"])
    assert [(bar["time"], bar["open"], bar["high"], bar["low"], bar["close"], bar["volume"])
            for bar in bars] == [
        ("2026-01-02", 9, 15, 8, 14, 600), ("2026-01-05", 14, 16, 13, 15, 400),
    ]
    assert page["data_version"] == full["data_version"]
    assert page["view"]["is_partial_last_bar"] is True
    assert page["available_years"] == [2026, 2023]
    assert page["view"]["sessions"] == ["2026-01-02", "2026-01-05"]


class KnownCalendarAdapter:
    source = "tdx"

    def catalog(self) -> dict[str, object]:
        raise AssertionError("a single stock page does not read the catalog")

    def metadata(self) -> dict[str, object]:
        return {}

    def load(self, symbol: str, asof: str) -> tuple[list[Bar], list[Bar]]:
        complete = [
            Bar(
                datetime.fromisoformat(cast(str, item["time"])), symbol,
                open=float(cast(int | float, item["open"])), high=float(cast(int | float, item["high"])),
                low=float(cast(int | float, item["low"])), close=float(cast(int | float, item["close"])),
                volume=float(cast(int | float, item["volume"])),
                buyable=False, sellable=False,
            )
            for item in DailyRepository().rows
        ]
        return [bar for bar in complete if bar.timestamp.date().isoformat() <= asof], complete


@pytest.mark.parametrize("timeframe", ["1d", "1w"])
def test_real_repository_historical_prefix_preserves_future_known_sessions_only_within_page_year(
    timeframe: str,
) -> None:
    repository = MarketDataRepository([KnownCalendarAdapter()])
    periods = MarketTimeframeService(repository)
    with patch.object(repository, "theory", side_effect=AssertionError("must not calculate theory")):
        page = MarketBarsService(periods).page(request(asof="2026-01-02", timeframe=timeframe))
    assert page["resolved_asof"] == page["view"]["asof"] == "2026-01-02"
    assert [bar["time"] for bar in records(page["view"]["bars"])] == ["2026-01-02"]
    assert page["view"]["sessions"] == ["2026-01-02", "2026-01-05"]
    full = periods.view("tdx", "sz.000678", "2026-01-02", timeframe)
    assert page["data_version"] == full["data_version"]


def test_data_changes_cannot_mix_old_and_new_pages_and_etag_excludes_read_clock() -> None:
    repository = DailyRepository()
    clock = ManualClock()
    pages = service(repository, clock=clock)
    first = pages.page(request())
    clock.advance(30)
    repeated = pages.page(request())
    assert first["view"]["fetched_at"] != repeated["view"]["fetched_at"]
    assert market_bars_etag(first) == market_bars_etag(repeated)
    other_year = pages.page(request(year=2025, data_version=first["data_version"]))
    other_source = pages.page(request(source="akshare"))
    other_stock = pages.page(request(symbol="sz.000001"))
    other_cutoff = pages.page(request(asof="2026-01-05"))
    other_period = pages.page(request(timeframe="1mo"))
    assert len({market_bars_etag(page) for page in (
        first, other_year, other_source, other_stock, other_cutoff, other_period,
    )}) == 6
    repository.rows[0]["volume"] = 91
    clock.advance(30)
    with pytest.raises(MarketBarsVersionChanged):
        pages.page(request(year=2025, data_version=first["data_version"]))
    changed = pages.page(request())
    assert changed["data_version"] != first["data_version"]
    assert market_bars_etag(changed) != market_bars_etag(first)


def test_page_chain_reuses_a_short_lived_prefix_then_expiry_detects_version_change() -> None:
    repository = DailyRepository()
    clock = ManualClock()
    pages = service(repository, clock=clock)
    latest = pages.page(request())
    repository.rows[0]["volume"] = 91
    clock.advance(29.999)
    prior = pages.page(request(year=2025, data_version=latest["data_version"]))
    assert prior["data_version"] == latest["data_version"] and repository.reads == 1
    with pytest.raises(MarketBarsVersionChanged):
        pages.page(request(year=2023, data_version="b" * 64))
    assert repository.reads == 1
    clock.advance(0.001)
    with pytest.raises(MarketBarsVersionChanged):
        pages.page(request(year=2023, data_version=latest["data_version"]))
    assert repository.reads == 2
    refreshed = pages.page(request())
    assert refreshed["data_version"] != latest["data_version"] and repository.reads == 2


def test_cache_is_bounded_by_recent_identities_and_page_mutation_cannot_change_the_prefix() -> None:
    repository = DailyRepository()
    pages = service(repository, cache_capacity=2)
    first = pages.page(request())
    original_etag = market_bars_etag(first)
    mutable_bars = cast(list[dict[str, object]], first["view"]["bars"])
    mutable_bars[0]["close"] = 999
    first["view"]["providers"] = ["wrong"]
    first["available_years"].clear()
    assert market_bars_etag(pages.page(request())) == original_etag
    assert repository.reads == 1
    pages.page(request(source="akshare"))
    pages.page(request())
    pages.page(request(symbol="sz.000001"))
    assert repository.reads == 3
    pages.page(request(source="akshare"))
    assert repository.reads == 4


@pytest.mark.parametrize("change", [
    {"source": ["wrong"]}, {"symbol": ["../000678"]}, {"symbol": ["sh.000001"]},
    {"symbol": ["sz.000678", "sz.000001"]}, {"source": ["tdx", "akshare"]},
    {"asof": ["2026-01-06", "2026-01-05"]}, {"asof": ["20260106"]}, {"asof": ["2026-02-30"]},
    {"timeframe": ["5m"]}, {"year": ["2025"]}, {"year": ["02025"], "data_version": ["a" * 64]},
    {"year": ["2027"], "data_version": ["a" * 64]}, {"year": ["1899"], "data_version": ["a" * 64]},
    {"year": ["2025", "2024"], "data_version": ["a" * 64]}, {"data_version": ["A" * 64]},
    {"data_version": ["a" * 63]}, {"data_version": ["a" * 64, "b" * 64]},
    {"source": [""]}, {"unexpected": ["x"]},
])
def test_query_boundary_rejects_invalid_identity_year_hash_and_duplicate_parameters(change: dict[str, list[str]]) -> None:
    query = dict(source=["tdx"], symbol=["sz.000678"], asof=["2026-01-06"], timeframe=["1d"])
    query.update(change)
    with pytest.raises(ValueError):
        parse_market_bars_query(query)


class MismatchedView:
    def __init__(self, field: str, value: object) -> None:
        self.field, self.value = field, value

    def view(self, source: str, symbol: str, asof: str, timeframe: str = "1d") -> dict[str, object]:
        result = DailyRepository().view(source, symbol, asof)
        result["timeframe"] = timeframe
        result[self.field] = self.value
        return result


@pytest.mark.parametrize(("field", "value"), [
    ("symbol", "sz.000001"), ("requested_asof", "2026-01-05"), ("resolved_source", "akshare"),
    ("providers", ["tdx", "akshare"]), ("source_fallback", True), ("price_basis", "adjusted"),
    ("data_version", "bad"), ("asof", "2026-01-07"),
    ("sessions", []), ("sessions", ["2026-01-05", "2026-01-05"]),
    ("sessions", ["2026-01-05"]),
])
def test_wrong_provider_stock_cutoff_or_adjustment_cannot_enter_a_page(field: str, value: object) -> None:
    with pytest.raises(ValueError):
        MarketBarsService(MismatchedView(field, value)).page(request())


@dataclass(frozen=True)
class HttpMarketBars:
    port: int
    daily: DailyRepository
    clock: ManualClock

    def get(self, query: str, etag: str | None = None) -> tuple[int, str | None, object]:
        connection = HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request("GET", f"/api/market-bars?{query}", headers={"If-None-Match": etag} if etag else {})
            response = connection.getresponse()
            body = response.read()
            return response.status, response.getheader("ETag"), json.loads(body) if body else None
        finally:
            connection.close()


@pytest.fixture
def http_market_bars() -> Iterator[HttpMarketBars]:
    daily = DailyRepository()
    repository = SimpleNamespace(market_data=daily, tdx=object(), akshare=object())
    clock = ManualClock()
    cached = service(daily, clock=clock)
    with patch.object(MarketTimeframeService, "bundle", side_effect=AssertionError("must not read full bundle")), patch(
        "wavequant_api.server.MarketBarsService", return_value=cached,
    ):
        server = make_server(repository, port=0, serve_static=False, limit_up_ladder=object())
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            yield HttpMarketBars(server.server_port, daily, clock)
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)


def test_http_route_returns_latest_ohlc_without_theory_then_version_bound_year_and_304(
    http_market_bars: HttpMarketBars,
) -> None:
    query = urlencode(dict(source="tdx", symbol="sz.000678", asof="2026-01-06", timeframe="1d"))
    status, etag, body = http_market_bars.get(query)
    page = mapping(body)
    assert status == 200 and etag is not None
    assert page["year"] == 2026
    assert records(mapping(page["view"])["bars"])[0]["time"] == "2026-01-02"
    assert http_market_bars.get(query, etag) == (304, etag, None)
    prior_query = query + f"&year=2025&data_version={page['data_version']}"
    prior_status, prior_etag, prior = http_market_bars.get(prior_query, etag)
    assert prior_status == 200 and prior_etag != etag and mapping(prior)["year"] == 2025
    assert http_market_bars.daily.reads == 1
    http_market_bars.daily.rows[0]["volume"] = 91
    http_market_bars.clock.advance(30)
    status, _, error = http_market_bars.get(prior_query)
    assert status == 409 and mapping(error)["code"] == "MARKET_DATA_VERSION_CHANGED"


@pytest.mark.parametrize("suffix", [
    "&year=2025", "&year=2025&data_version=bad", "&year=&data_version=" + "a" * 64,
    "&source=akshare", "&timeframe=1w", "&unknown=1", "&asof=2026-01-05", "&symbol=sz.000001",
])
def test_http_route_rejects_duplicate_or_invalid_queries_before_provider_read(
    http_market_bars: HttpMarketBars, suffix: str,
) -> None:
    query = "source=tdx&symbol=sz.000678&asof=2026-01-06&timeframe=1d" + suffix
    assert http_market_bars.get(query)[0] == 400
    assert http_market_bars.daily.reads == 0
