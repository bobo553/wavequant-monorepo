"""Read-only A-share chart browser backed by AkShare public data."""

from __future__ import annotations

from collections import OrderedDict
from datetime import date, datetime
import hashlib
import json
import re
from threading import Lock
import time
from typing import Any

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
    ):
        self.provider = provider or AkShareProvider(timeout=timeout)
        self.catalog_ttl = catalog_ttl
        self.history_ttl = history_ttl
        self._catalog: dict[str, Any] | None = None
        self._catalog_loaded_at = 0.0
        self._history: OrderedDict[str, tuple[float, list[Bar]]] = OrderedDict()
        self._history_endpoint: dict[str, str] = {}
        self._theory: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._primary_retry_at = 0.0
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
        catalog = dict(
            stocks=stocks,
            source="AkShare stock_info_a_code_name",
            available=True,
            latest=date.today().isoformat(),
            with_daily=len(stocks),
            scope="online_SH_SZ_BJ_A_shares",
            provider_version=self.provider.version,
            warnings=[],
            notice="AkShare 在线沪深北 A 股目录；行情按需拉取，实际最新交易日以个股返回为准。",
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
            if not symbol.startswith("bj."):
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
                    pass
            return (
                "stock_zh_a_hist_tx",
                self.provider.call(
                    "stock_zh_a_hist_tx",
                    symbol=compact_symbol,
                    start_date="19900101",
                    end_date=date.today().strftime("%Y%m%d"),
                    adjust="",
                ),
            )

        if use_primary:
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
