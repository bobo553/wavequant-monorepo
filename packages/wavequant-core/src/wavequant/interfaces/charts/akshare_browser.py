"""Read-only A-share chart browser backed by AkShare public data."""

from __future__ import annotations

from collections import OrderedDict
from datetime import date, datetime, time as wall_time, timedelta
import hashlib
import json
import re
from threading import Lock
import time
from typing import Any, Callable
from zoneinfo import ZoneInfo

from wavequant.domain.market_structure.lecture_drawing import lecture_drawing
from wavequant.domain.market_structure.lecture_trend import reversal_trends
from wavequant.domain.market_structure.secondary_trend import secondary_trends
from wavequant.domain.market_structure.tertiary_trend import tertiary_trends
from wavequant.domain.models.model import Bar
from wavequant.infrastructure.market_data.akshare import AkShareProvider, AkShareUnavailable
from wavequant.infrastructure.market_data.io import _validate_bar


_COLUMNS = ("日期", "开盘", "最高", "最低", "收盘", "成交量")
_PRIMARY_PROBE_TIMEOUT = 3.0
_SINA_FALLBACK_TIMEOUT = 10.0
_PRIMARY_RETRY_DELAY = 600.0
_SINA_RETRY_DELAY = 600.0
_TENCENT_LOOKBACK_DAYS = 180
_CHINA_MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")
# AkShare's daily endpoints do not publish a completed candle at the 15:00
# closing auction instant.  The two-hour buffer keeps the nightly worker from
# advertising a market date while upstream daily files are still converging.
_DAILY_DATA_READY_AT = wall_time(17, 0)


def _calendar_session(value: object) -> date:
    """Normalize AkShare calendar values without depending on pandas types."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value).split()[0])


def _latest_completed_session(frame: Any, now: datetime) -> date:
    """Return the latest exchange session whose daily bars should be ready."""
    if now.tzinfo is None:
        raise ValueError("AkShare market clock must be timezone-aware")
    market_now = now.astimezone(_CHINA_MARKET_TIMEZONE)
    cutoff = market_now.date() if market_now.time() >= _DAILY_DATA_READY_AT else market_now.date() - timedelta(days=1)
    sessions = [
        _calendar_session(row["trade_date"])
        for row in _records(frame, {"trade_date"}, limit=20_000)
    ]
    eligible = [session for session in sessions if session <= cutoff]
    if not eligible:
        raise ValueError("AkShare 交易日历没有已完成交易日")
    return max(eligible)


def _weekday_fallback_session(now: datetime) -> date:
    """Conservative fallback used only when the exchange calendar is down."""
    if now.tzinfo is None:
        raise ValueError("AkShare market clock must be timezone-aware")
    market_now = now.astimezone(_CHINA_MARKET_TIMEZONE)
    candidate = (
        market_now.date()
        if market_now.time() >= _DAILY_DATA_READY_AT
        else market_now.date() - timedelta(days=1)
    )
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate


def _symbol(code: str) -> str | None:
    if re.fullmatch(r"(60|68)\d{4}", code):
        return f"sh.{code}"
    if re.fullmatch(r"(00|30)\d{4}", code):
        return f"sz.{code}"
    if re.fullmatch(r"(43|82|83|87|88|92)\d{4}", code):
        return f"bj.{code}"
    return None


def _code(symbol: str) -> str:
    if not re.fullmatch(r"(sh|sz|bj)\.\d{6}", symbol):
        raise ValueError("invalid AkShare symbol")
    market, code = symbol.split(".")
    if _symbol(code) != f"{market}.{code}":
        raise ValueError("not an A-share symbol")
    return code


def _records(frame: Any, required: set[str], *, limit: int) -> list[dict[str, Any]]:
    columns = {str(value) for value in getattr(frame, "columns", [])}
    if not required <= columns:
        raise ValueError("AkShare 返回字段不完整")
    try:
        rows = frame.to_dict("records")
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("AkShare 返回表格格式无效") from exc
    if not isinstance(rows, list) or len(rows) > limit:
        raise ValueError("AkShare 返回记录数量异常")
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError("AkShare 返回记录格式无效")
    return rows


class AkShareBrowser:
    """Normalize a small audited subset of AkShare into chart read models."""

    def __init__(
        self,
        provider: AkShareProvider | None = None,
        *,
        timeout: float = 30.0,
        catalog_ttl: float = 3600.0,
        history_ttl: float = 300.0,
        pinned_history: bool = False,
        clock: Callable[[], datetime] | None = None,
    ):
        self.provider = provider or AkShareProvider(timeout=timeout)
        self.catalog_ttl = catalog_ttl
        self.history_ttl = history_ttl
        self.pinned_history = pinned_history
        self._clock = clock or (lambda: datetime.now(_CHINA_MARKET_TIMEZONE))
        self._catalog: dict[str, Any] | None = None
        self._catalog_loaded_at = 0.0
        self._history: OrderedDict[str, tuple[float, list[Bar]]] = OrderedDict()
        self._history_endpoint: dict[str, str] = {}
        self._theory: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._primary_retry_at = 0.0
        self._sina_retry_at = 0.0
        self._lock = Lock()

    def catalog(self) -> dict[str, Any]:
        with self._lock:
            if self._catalog is not None and time.monotonic() - self._catalog_loaded_at < self.catalog_ttl:
                return self._catalog
        frame = self.provider.call("stock_info_a_code_name")
        try:
            rows = _records(frame, {"code", "name"}, limit=20_000)
        except ValueError as exc:
            raise AkShareUnavailable("AkShare 股票目录格式异常") from exc
        stocks = []
        seen = set()
        for row in rows:
            code, name = str(row["code"]).strip(), str(row["name"]).strip()
            symbol = _symbol(code)
            if symbol is None or symbol in seen or not name or len(name) > 80:
                continue
            seen.add(symbol)
            stocks.append(
                dict(
                    symbol=symbol,
                    name=name,
                    source="akshare",
                    has_data=True,
                    status="available_on_demand",
                    first=None,
                    last=date.today().isoformat(),
                    bar_count=None,
                )
            )
        if not stocks:
            raise ValueError("AkShare 未返回有效 A 股目录")
        warnings = []
        try:
            calendar = self.provider.call(
                "tool_trade_date_hist_sina",
                call_timeout=min(self.provider.timeout, 10.0),
            )
            latest_session = _latest_completed_session(calendar, self._clock())
        except (AkShareUnavailable, ValueError, TypeError, OverflowError):
            # Catalog browsing must remain available during a calendar-provider
            # outage.  The snapshot health gate independently prevents this
            # weekday approximation from being published as a bad generation.
            latest_session = _weekday_fallback_session(self._clock())
            warnings.append("AkShare 交易日历暂不可用，最新日期按中国市场工作日保守估算。")
        latest = latest_session.isoformat()
        for stock in stocks:
            stock["last"] = latest
        catalog = dict(
            stocks=stocks,
            source="AkShare stock_info_a_code_name + tool_trade_date_hist_sina",
            available=True,
            latest=latest,
            with_daily=len(stocks),
            scope="online_SH_SZ_BJ_A_shares",
            provider_version=self.provider.version,
            warnings=warnings,
            notice="AkShare 在线沪深北 A 股目录；最新日期按中国市场交易日历和日线就绪时点确定。",
        )
        with self._lock:
            self._catalog = catalog
            self._catalog_loaded_at = time.monotonic()
        return catalog

    def _all_bars(self, symbol: str) -> list[Bar]:
        code = _code(symbol)
        now = time.monotonic()
        with self._lock:
            cached = self._history.get(symbol)
            if cached is not None and now - cached[0] < self.history_ttl:
                self._history.move_to_end(symbol)
                return cached[1]
        primary_timeout = min(self.provider.timeout, _PRIMARY_PROBE_TIMEOUT)
        with self._lock:
            use_primary = now >= self._primary_retry_at
        endpoint = "stock_zh_a_hist"

        def fallback() -> tuple[str, Any]:
            compact_symbol = symbol.replace(".", "")
            with self._lock:
                use_sina = time.monotonic() >= self._sina_retry_at
            if not symbol.startswith("bj.") and use_sina:
                try:
                    return (
                        "stock_zh_a_daily",
                        self.provider.call(
                            "stock_zh_a_daily",
                            call_timeout=min(self.provider.timeout, _SINA_FALLBACK_TIMEOUT),
                            symbol=compact_symbol,
                            start_date="19900101",
                            end_date=date.today().strftime("%Y%m%d"),
                            adjust="",
                        ),
                    )
                except AkShareUnavailable:
                    # Sina availability is normally provider-wide.  Do not
                    # spend the same timeout again for every stock in a full
                    # market rebuild; Tencent remains the bounded fallback.
                    with self._lock:
                        self._sina_retry_at = time.monotonic() + _SINA_RETRY_DELAY
            return (
                "stock_zh_a_hist_tx",
                self.provider.call(
                    "stock_zh_a_hist_tx",
                    symbol=compact_symbol,
                    start_date=(date.today() - timedelta(days=_TENCENT_LOOKBACK_DAYS)).strftime("%Y%m%d"),
                    end_date=date.today().strftime("%Y%m%d"),
                    adjust="",
                ),
            )

        if self.pinned_history:
            if symbol.startswith('bj.'):
                raise AkShareUnavailable('当前统一行情源为 AKShare / 新浪，暂不支持北交所历史回测')
            endpoint = 'stock_zh_a_daily'
            frame = self.provider.call(endpoint, symbol=symbol.replace('.', ''), start_date='19900101',
                                       end_date=date.today().strftime('%Y%m%d'), adjust='')
        elif use_primary:
            try:
                frame = self.provider.call(
                    endpoint,
                    call_timeout=primary_timeout,
                    symbol=code,
                    period="daily",
                    start_date="19900101",
                    end_date=date.today().strftime("%Y%m%d"),
                    adjust="",
                    timeout=primary_timeout,
                )
            except AkShareUnavailable:
                with self._lock:
                    self._primary_retry_at = time.monotonic() + _PRIMARY_RETRY_DELAY
                endpoint, frame = fallback()
        else:
            endpoint, frame = fallback()
        try:
            rows = _records(
                frame,
                set(_COLUMNS) if endpoint == "stock_zh_a_hist" else {"date", "open", "high", "low", "close", "volume"},
                limit=20_000,
            )
        except ValueError as exc:
            raise AkShareUnavailable("AkShare 日线格式异常") from exc
        values = []
        for number, row in enumerate(rows, start=1):
            try:
                stock_columns = endpoint == "stock_zh_a_hist"
                stamp = date.fromisoformat(str(row["日期" if stock_columns else "date"]).split()[0])
                bar = Bar(
                    timestamp=datetime.combine(stamp, datetime.min.time()),
                    symbol=symbol,
                    open=float(row["开盘" if stock_columns else "open"]),
                    high=float(row["最高" if stock_columns else "high"]),
                    low=float(row["最低" if stock_columns else "low"]),
                    close=float(row["收盘" if stock_columns else "close"]),
                    volume=float(row["成交量" if stock_columns else "volume"]) * (100 if stock_columns else 1),
                    buyable=False,
                    sellable=False,
                )
            except (TypeError, ValueError, OverflowError) as exc:
                raise AkShareUnavailable(f"AkShare 第 {number} 行行情无效") from exc
            try:
                _validate_bar(bar, number)
            except ValueError as exc:
                raise AkShareUnavailable(f"AkShare 第 {number} 行行情无效") from exc
            values.append(bar)
        values.sort(key=lambda item: item.timestamp)
        if not values:
            raise ValueError("AkShare 未返回该股票日线")
        if any(current.timestamp <= previous.timestamp for previous, current in zip(values, values[1:])):
            raise ValueError("AkShare 返回重复或倒序日期")
        with self._lock:
            self._history[symbol] = (time.monotonic(), values)
            self._history_endpoint[symbol] = endpoint
            self._history.move_to_end(symbol)
            while len(self._history) > 32:
                expired, _ = self._history.popitem(last=False)
                self._history_endpoint.pop(expired, None)
        return values

    def bars(self, symbol: str, asof: str) -> tuple[list[Bar], list[Bar]]:
        requested = date.fromisoformat(asof)
        if requested.isoformat() != asof:
            raise ValueError("use YYYY-MM-DD date")
        if requested > date.today():
            raise ValueError("AkShare 回放日期不能晚于今天")
        all_bars = self._all_bars(symbol)
        prefix = [bar for bar in all_bars if bar.timestamp.date() <= requested]
        if not prefix:
            raise ValueError("该回放日期之前没有日线数据")
        return prefix, all_bars

    def view(self, symbol: str, asof: str) -> dict[str, Any]:
        bars, all_bars = self.bars(symbol, asof)
        resolved = bars[-1].timestamp.date().isoformat()
        return dict(
            symbol=symbol,
            asof=resolved,
            requested_asof=asof,
            result_scope="akshare",
            data_source="akshare",
            price_basis="raw_unadjusted",
            provider_version=self.provider.version,
            history_endpoint=self._history_endpoint[symbol],
            fetched_at=datetime.now().astimezone().isoformat(timespec="seconds"),
            sessions=[bar.timestamp.date().isoformat() for bar in all_bars],
            bars=[
                dict(
                    time=bar.timestamp.date().isoformat(),
                    open=bar.open,
                    high=bar.high,
                    low=bar.low,
                    close=bar.close,
                    raw_close=bar.close,
                    volume=bar.volume,
                    factor=1,
                )
                for bar in bars
            ],
            markers=[],
            signals=[],
            orders=[],
            trades=[],
            curve=[],
            metrics=None,
            evidence="AkShare 在线原始不复权日线，仅用于行情与讲义绘图；未执行回测，除权缺口可能影响形态。",
        )

    def theory(self, symbol: str, asof: str) -> dict[str, Any]:
        bars, _ = self.bars(symbol, asof)
        payload = [[bar.timestamp.date().isoformat(), bar.open, bar.high, bar.low, bar.close, bar.volume] for bar in bars]
        digest = hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()
        key = f"{symbol}:{bars[-1].timestamp.date().isoformat()}:{digest}"
        with self._lock:
            cached = self._theory.get(key)
            if cached is not None:
                self._theory.move_to_end(key)
                return cached
        drawing = lecture_drawing(bars)  # type: ignore[no-untyped-call]
        first = reversal_trends(drawing, bars)  # type: ignore[no-untyped-call]
        second = secondary_trends(first, bars)  # type: ignore[no-untyped-call]
        result = dict(
            asof=bars[-1].timestamp.date().isoformat(),
            points=[],
            polyline_segments=[],
            events=[],
            shapes=[],
            counts={},
            lecture_drawing=drawing,
            reversal_trends=first,
            secondary_trends=second,
            tertiary_trends=tertiary_trends(second, bars),  # type: ignore[no-untyped-call]
            interrupted=False,
            computed_from="akshare_raw_prefix_display_only",
            price_basis="raw_unadjusted",
        )
        with self._lock:
            self._theory[key] = result
            while len(self._theory) > 16:
                self._theory.popitem(last=False)
        return result
