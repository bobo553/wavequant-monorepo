"""按年读取规范 OHLC，完整趋势计算仍由独立接口负责。"""

from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import date
import hashlib
import json
import math
import re
from threading import Lock
from time import monotonic
from typing import Literal, Protocol, TypedDict, cast

# Core 分发包尚无 py.typed；股票校验函数在下方收窄为明确的调用边界。
from wavequant.interfaces.charts.tdx_browser import is_stock  # type: ignore[import-untyped]


_SOURCES = frozenset(("tdx", "akshare"))
_TIMEFRAMES = frozenset(("1d", "1w", "1mo", "3mo", "1y"))
_SYMBOL = re.compile(r"(sh|sz|bj)\.([0-9]{6})\Z")
_VERSION = re.compile(r"[0-9a-f]{64}\Z")
_YEAR = re.compile(r"[1-9][0-9]{3}\Z")


class MarketBarsViewPort(Protocol):
    """仅依赖轻量行情入口，不暴露趋势或周期快照入口。"""

    def view(self, source: str, symbol: str, asof: str, timeframe: str = "1d") -> object: ...


class MarketBarsPage(TypedDict):
    schema_version: Literal[1]
    source: str
    symbol: str
    requested_asof: str
    resolved_asof: str
    timeframe: str
    data_version: str
    year: int
    next_year: int | None
    has_more: bool
    available_years: list[int]
    history_start: str
    history_end: str
    total_bars: int
    view: dict[str, object]


class MarketBarsVersionChanged(ValueError):
    """分页所绑定的完整行情前缀已变化，旧页不能与新页合并。"""


@dataclass(frozen=True)
class MarketBarsRequest:
    source: str
    symbol: str
    asof: str
    timeframe: str
    year: int | None = None
    data_version: str | None = None


@dataclass(frozen=True)
class _MarketBarsSelection:
    version: str
    resolved: str
    bars: tuple[dict[str, object], ...]
    sessions: tuple[str, ...]
    years: tuple[int, ...]
    metadata: dict[str, object]


@dataclass(frozen=True)
class _CachedSelection:
    expires_at: float
    selection: _MarketBarsSelection


def parse_market_bars_query(query: Mapping[str, Sequence[str]]) -> MarketBarsRequest:
    """拒绝未知、重复、空白或非规范分页参数，再交给轻量行情入口。"""
    required = {"source", "symbol", "asof", "timeframe"}
    optional = {"year", "data_version"}
    if not required <= query.keys() or query.keys() - required - optional:
        raise ValueError("invalid market bars arguments")
    if any(len(values) != 1 or not values[0] for values in query.values()):
        raise ValueError("market bars arguments must be provided once and nonempty")
    raw_year = query.get("year")
    if raw_year is not None and _YEAR.fullmatch(raw_year[0]) is None:
        raise ValueError("year must be a four-digit calendar year")
    request = MarketBarsRequest(
        query["source"][0], query["symbol"][0], query["asof"][0], query["timeframe"][0],
        int(raw_year[0]) if raw_year is not None else None,
        query["data_version"][0] if "data_version" in query else None,
    )
    _validate_request(request)
    return request


def _day(value: object, field: str) -> date:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a YYYY-MM-DD date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be a YYYY-MM-DD date") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{field} must be a YYYY-MM-DD date")
    return parsed


def _validate_request(request: MarketBarsRequest) -> None:
    if request.source not in _SOURCES:
        raise ValueError("source must be tdx or akshare")
    match = _SYMBOL.fullmatch(request.symbol)
    stock = cast(Callable[[str, str], bool], is_stock)
    if match is None or not stock(match[1], match[2]):
        raise ValueError("symbol must identify a canonical A-share stock")
    asof = _day(request.asof, "asof")
    if request.timeframe not in _TIMEFRAMES:
        raise ValueError("unsupported market timeframe")
    if request.year is not None:
        if type(request.year) is not int or not 1900 <= request.year <= asof.year:
            raise ValueError("year must be between 1900 and the requested asof year")
        if request.data_version is None:
            raise ValueError("year pages require the first page data_version")
    if request.data_version is not None and _VERSION.fullmatch(request.data_version) is None:
        raise ValueError("data_version must be a lowercase SHA256 digest")


def _mapping(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"invalid market {field}")
    return dict(cast(Mapping[str, object], value))


def _number(value: object, field: str, *, positive: bool = True) -> float:
    if type(value) not in (int, float):
        raise ValueError(f"invalid market bar {field}")
    number = float(cast(int | float, value))
    if not math.isfinite(number) or (number <= 0 if positive else number < 0):
        raise ValueError(f"invalid market bar {field}")
    return number


def _bars(view: Mapping[str, object], resolved: date) -> list[dict[str, object]]:
    raw = view.get("bars")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)) or not raw:
        raise ValueError("market view must contain daily or aggregated bars")
    result: list[dict[str, object]] = []
    previous: date | None = None
    for item in raw:
        row = _mapping(item, "bar")
        session = _day(row.get("time"), "bar time")
        if session > resolved or previous is not None and session <= previous:
            raise ValueError("market bars must be an ordered prefix through resolved_asof")
        prices = {field: _number(row.get(field), field) for field in ("open", "high", "low", "close", "raw_close")}
        if not prices["low"] <= min(prices["open"], prices["close"]) <= max(
            prices["open"], prices["close"],
        ) <= prices["high"]:
            raise ValueError("invalid market bar price bounds")
        _number(row.get("volume"), "volume", positive=False)
        if "factor" in row:
            _number(row["factor"], "factor")
        result.append(row)
        previous = session
    if previous != resolved:
        raise ValueError("resolved_asof must match the final market bar")
    return result


def _sessions(view: Mapping[str, object]) -> tuple[str, ...]:
    raw = view.get("sessions")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)) or not raw:
        raise ValueError("market view must contain a trading calendar")
    sessions: list[str] = []
    previous: date | None = None
    for value in raw:
        session = _day(value, "session")
        if previous is not None and session <= previous:
            raise ValueError("market sessions must be strictly ordered")
        sessions.append(session.isoformat())
        previous = session
    return tuple(sessions)


class MarketBarsService:
    """先完成既有周期聚合，再仅传输一个年份，不计算或截断趋势来源。"""

    def __init__(
        self, views: MarketBarsViewPort, *, cache_ttl_seconds: float = 30, cache_capacity: int = 16,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if not math.isfinite(cache_ttl_seconds) or cache_ttl_seconds <= 0 or cache_capacity < 1:
            raise ValueError("market bars cache requires a positive TTL and capacity")
        self.views = views
        self._cache_ttl = cache_ttl_seconds
        self._cache_capacity = cache_capacity
        self._clock = clock
        self._cache: OrderedDict[tuple[str, str, str, str], _CachedSelection] = OrderedDict()
        self._cache_lock = Lock()

    def _read_selection(self, request: MarketBarsRequest) -> _MarketBarsSelection:
        view = _mapping(self.views.view(request.source, request.symbol, request.asof, request.timeframe), "view")
        expected = {
            "symbol": request.symbol, "requested_asof": request.asof, "timeframe": request.timeframe,
            "resolved_source": request.source, "result_scope": request.source,
            "price_basis": "raw_unadjusted", "providers": [request.source],
            "source_fallback": False, "supplemented_bars": 0,
        }
        if any(view.get(field) != value for field, value in expected.items()):
            raise ValueError("market view does not match the requested source, stock or prefix")
        resolved = _day(view.get("asof"), "resolved_asof")
        if resolved > _day(request.asof, "asof"):
            raise ValueError("resolved_asof exceeds requested_asof")
        version = view.get("data_version")
        if not isinstance(version, str) or _VERSION.fullmatch(version) is None:
            raise ValueError("invalid market view data_version")
        bars = _bars(view, resolved)
        sessions = _sessions(view)
        if not {cast(str, row["time"]) for row in bars} <= set(sessions):
            raise ValueError("market calendar must include each visible bar")
        years = sorted({cast(str, row["time"])[:4] for row in bars}, reverse=True)
        return _MarketBarsSelection(
            version, resolved.isoformat(), tuple(deepcopy(bars)), sessions, tuple(int(year) for year in years),
            deepcopy({key: value for key, value in view.items() if key not in ("bars", "sessions")}),
        )

    def _selection(self, request: MarketBarsRequest) -> _MarketBarsSelection:
        key = (request.source, request.symbol, request.asof, request.timeframe)
        with self._cache_lock:
            cached = self._cache.get(key)
            if cached is not None and self._clock() < cached.expires_at:
                self._cache.move_to_end(key)
                return cached.selection
            self._cache.pop(key, None)
        # 源读取不占用全局缓存锁；短 TTL 使年度链复用完整前缀而不永久冻结行情。
        selection = self._read_selection(request)
        with self._cache_lock:
            self._cache[key] = _CachedSelection(self._clock() + self._cache_ttl, selection)
            self._cache.move_to_end(key)
            while len(self._cache) > self._cache_capacity:
                self._cache.popitem(last=False)
        return selection

    def page(self, request: MarketBarsRequest) -> MarketBarsPage:
        _validate_request(request)
        selection = self._selection(request)
        if request.data_version is not None and request.data_version != selection.version:
            raise MarketBarsVersionChanged("行情数据版本已变化，请重新加载最新年度")
        selected = request.year if request.year is not None else selection.years[0]
        page_bars = [deepcopy(row) for row in selection.bars if int(cast(str, row["time"])[:4]) == selected]
        next_year = next((year for year in selection.years if year < selected), None)
        # 保留整个请求截面的身份；历史年切片不能把 asof/data_version 改成年末。
        # 完整源日历仍含回放截面之后的已知交易日，年度传输保留同年导航能力。
        page_sessions = [session for session in selection.sessions if int(session[:4]) == selected]
        page_view = dict(deepcopy(selection.metadata), bars=page_bars, sessions=page_sessions)
        return MarketBarsPage(
            schema_version=1, source=request.source, symbol=request.symbol,
            requested_asof=request.asof, resolved_asof=selection.resolved, timeframe=request.timeframe,
            data_version=selection.version, year=selected, next_year=next_year, has_more=next_year is not None,
            available_years=list(selection.years), history_start=cast(str, selection.bars[0]["time"]),
            history_end=cast(str, selection.bars[-1]["time"]), total_bars=len(selection.bars), view=page_view,
        )


def market_bars_etag(page: MarketBarsPage) -> str:
    """年度、源、周期与行情版本共同隔离缓存，不受趋势规则或读取时刻影响。"""
    view = {key: value for key, value in page["view"].items() if key != "fetched_at"}
    identity = {**page, "view": view}
    body = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(body.encode()).hexdigest()
