"""Canonical market-data repository with provider adapters and safe fallback.

External providers expose different symbols, schemas, date semantics, and
failure modes.  This module is the anti-corruption layer consumed by charts
and signal calculations: after data enters here, downstream code only sees
validated :class:`Bar` objects and one stable read-model contract.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import json
from threading import Lock
from typing import Any, Protocol, Sequence, cast

from wavequant.domain.market_structure.lecture_drawing import lecture_drawing
from wavequant.domain.market_structure.lecture_trend import reversal_trends
from wavequant.domain.market_structure.secondary_trend import secondary_trends
from wavequant.domain.market_structure.tertiary_trend import tertiary_trends
from wavequant.domain.models.model import Bar
from wavequant.infrastructure.market_data.akshare import AkShareUnavailable


class MarketDataAdapter(Protocol):
    """Port implemented by each external market-data source."""

    source: str

    def catalog(self) -> dict[str, Any]:
        """Return provider securities in the canonical symbol namespace."""

    def load(self, symbol: str, asof: str) -> tuple[list[Bar], list[Bar]]:
        """Return the requested prefix and the provider's complete local view."""

    def metadata(self) -> dict[str, object]:
        """Return safe provider metadata for the public read model."""


class TdxMarketDataAdapter:
    """Adapt the local TDX browser to the common market-data port."""

    source = "tdx"

    def __init__(self, browser: Any):
        # The legacy browser remains dynamically typed while the adapter keeps
        # that looseness from leaking into the repository and domain layers.
        self.browser = browser

    def catalog(self) -> dict[str, Any]:
        return cast(dict[str, Any], self.browser.catalog())

    def load(self, symbol: str, asof: str) -> tuple[list[Bar], list[Bar]]:
        prefix = list(self.browser.bars(symbol, asof))
        complete = list(self.browser.bars(symbol, "9999-12-31"))
        return prefix, complete

    def metadata(self) -> dict[str, object]:
        return {}


class AkShareMarketDataAdapter:
    """Adapt AkShare's online frames to the common market-data port."""

    source = "akshare"

    def __init__(self, browser: Any):
        self.browser = browser

    def catalog(self) -> dict[str, Any]:
        return cast(dict[str, Any], self.browser.catalog())

    def load(self, symbol: str, asof: str) -> tuple[list[Bar], list[Bar]]:
        prefix, complete = self.browser.bars(symbol, asof)
        return list(prefix), list(complete)

    def metadata(self) -> dict[str, object]:
        return {"provider_version": self.browser.provider.version}


@dataclass(frozen=True)
class MarketDataWindow:
    """One normalized, provenance-carrying market-data selection."""

    requested_source: str
    resolved_source: str
    requested_asof: str
    asof: str
    bars: tuple[Bar, ...]
    sessions: tuple[str, ...]
    providers: tuple[str, ...]
    supplemented_bars: int
    primary_error: str | None = None


class MarketDataRepository:
    """Resolve providers into one stable repository contract.

    AkShare is the default preference.  A provider switch changes acquisition
    priority only: chart and theory generation below are shared.  If the
    preferred provider is unavailable or stale for the requested cutoff, the
    next configured adapter may supply the complete series or missing dates.
    Primary values always win on duplicate dates so fallback never silently
    overwrites the user's selected source.
    """

    def __init__(
        self,
        adapters: Sequence[MarketDataAdapter],
        *,
        default_source: str = "akshare",
        fallback_order: dict[str, tuple[str, ...]] | None = None,
    ):
        self.adapters = {adapter.source: adapter for adapter in adapters}
        if self.adapters and default_source not in self.adapters:
            default_source = next(iter(self.adapters))
        self.default_source = default_source
        self.fallback_order = fallback_order or {
            source: tuple(candidate for candidate in self.adapters if candidate != source) for source in self.adapters
        }
        self._theory: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._lock = Lock()

    def available_sources(self) -> tuple[str, ...]:
        """Return configured source identifiers in deterministic order."""

        return tuple(self.adapters)

    def catalog(self, source: str | None = None) -> dict[str, Any]:
        """Return a normalized catalog and supplement missing provider rows.

        Catalog union is metadata-only.  A fallback-only stock is explicitly
        marked so callers can audit that its bars will resolve through another
        adapter instead of assuming the preferred provider supplied it.
        """

        selected = self._selected(source)
        order = self._order(selected)
        payloads: list[tuple[str, dict[str, Any]]] = []
        errors: list[str] = []
        for candidate in order:
            try:
                payloads.append((candidate, self.adapters[candidate].catalog()))

            except (AkShareUnavailable, FileNotFoundError, OSError, ValueError):
                # Provider exceptions may contain an absolute local path or
                # upstream URL, neither of which belongs in a public warning.
                errors.append(f"{candidate}: unavailable")
        if not payloads:
            raise ValueError("所有已配置行情源均不可用：" + "；".join(errors))

        merged: dict[str, dict[str, Any]] = {}
        # Fallback rows establish defaults; preferred rows are applied last and
        # therefore own every field they actually provide.
        for provider, payload in reversed(payloads):
            rows = payload.get("stocks")
            if not isinstance(rows, list):
                continue
            for raw in rows:
                if not isinstance(raw, dict) or not isinstance(raw.get("symbol"), str):
                    continue
                symbol = raw["symbol"]
                previous = merged.get(symbol, {})
                normalized = {**previous, **raw}
                normalized.update(
                    symbol=symbol,
                    source=selected,
                    catalog_source=provider,
                    has_data=bool(raw.get("has_data", True)),
                    data_source=provider,
                )
                if not normalized["has_data"] and previous.get("has_data"):
                    normalized["has_data"] = True
                    normalized["data_source"] = previous.get("data_source", previous.get("catalog_source"))
                if not raw.get("name") and previous.get("name"):
                    normalized["name"] = previous["name"]
                    normalized["name_source"] = previous.get("catalog_source", provider)
                merged[symbol] = normalized
        if not merged:
            raise ValueError("行情源未返回有效证券目录")

        primary_payload = next((payload for provider, payload in payloads if provider == selected), payloads[0][1])
        stocks = [merged[symbol] for symbol in sorted(merged)]
        primary_symbols = {
            row.get("symbol")
            for provider, payload in payloads
            if provider == selected and isinstance(payload.get("stocks"), list)
            for row in payload["stocks"]
            if isinstance(row, dict)
        }
        provider_warnings = primary_payload.get("warnings", [])
        if not isinstance(provider_warnings, list):
            provider_warnings = []
        return {
            **primary_payload,
            "available": True,
            "source": selected,
            "default_source": self.default_source,
            "providers": [provider for provider, _ in payloads],
            "stocks": stocks,
            "with_daily": sum(bool(stock.get("has_data")) for stock in stocks),
            "supplemented_stocks": sum(stock["symbol"] not in primary_symbols for stock in stocks),
            "warnings": [*provider_warnings, *errors],
        }

    def window(self, source: str | None, symbol: str, asof: str) -> MarketDataWindow:
        """Load one canonical series, falling back only when data is missing."""

        selected = self._selected(source)
        requested = date.fromisoformat(asof)
        if requested.isoformat() != asof:
            raise ValueError("use YYYY-MM-DD date")
        order = self._order(selected)
        primary_error: str | None = None
        resolved_source: str | None = None
        primary_prefix: list[Bar] = []
        primary_complete: list[Bar] = []
        for candidate in order:
            try:
                prefix, complete = self.adapters[candidate].load(symbol, asof)
                self._validate_series(symbol, prefix, complete)
                resolved_source = candidate
                primary_prefix, primary_complete = prefix, complete
                break
            except (AkShareUnavailable, FileNotFoundError, OSError, ValueError):
                if candidate == selected:
                    primary_error = f"{selected} 数据暂不可用"
        if resolved_source is None:
            raise ValueError(primary_error or "所有已配置行情源均未返回该股票行情")

        providers = [resolved_source]
        supplemented = 0
        complete_by_day = {bar.timestamp.date(): bar for bar in primary_complete}
        # Only a stale/failed preferred source needs supplementation.  This
        # avoids turning a healthy local read into an unnecessary network call.
        needs_supplement = (
            resolved_source != selected
            or primary_prefix[-1].timestamp.date() < requested
            or len(primary_complete) < 250
        )
        if needs_supplement:
            for candidate in order:
                if candidate == resolved_source or (candidate == selected and primary_error is not None):
                    continue
                try:
                    _prefix, complete = self.adapters[candidate].load(symbol, asof)
                    self._validate_series(symbol, _prefix, complete)
                except (AkShareUnavailable, FileNotFoundError, OSError, ValueError):
                    continue
                added = 0
                for bar in complete:
                    day = bar.timestamp.date()
                    if day not in complete_by_day:
                        complete_by_day[day] = bar
                        added += 1
                if added:
                    providers.append(candidate)
                    supplemented += added

        complete = sorted(complete_by_day.values(), key=lambda bar: bar.timestamp)
        prefix = [bar for bar in complete if bar.timestamp.date() <= requested]
        if not prefix:
            raise ValueError("该回放日期之前没有日线数据")
        return MarketDataWindow(
            requested_source=selected,
            resolved_source=resolved_source,
            requested_asof=asof,
            asof=prefix[-1].timestamp.date().isoformat(),
            bars=tuple(prefix),
            sessions=tuple(bar.timestamp.date().isoformat() for bar in complete),
            providers=tuple(providers),
            supplemented_bars=supplemented,
            primary_error=primary_error,
        )

    def view(self, source: str | None, symbol: str, asof: str) -> dict[str, Any]:
        """Serialize a canonical chart view independent of provider schema."""

        window = self.window(source, symbol, asof)
        data_version = self._bars_digest(window.bars)
        return {
            **self.adapters[window.requested_source].metadata(),
            "symbol": symbol,
            "asof": window.asof,
            "requested_asof": window.requested_asof,
            "result_scope": window.requested_source,
            "data_source": window.requested_source,
            "resolved_source": window.resolved_source,
            "providers": list(window.providers),
            "supplemented_bars": window.supplemented_bars,
            "source_fallback": window.resolved_source != window.requested_source,
            "source_warning": window.primary_error,
            "data_version": data_version,
            "price_basis": "raw_unadjusted",
            "fetched_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "sessions": list(window.sessions),
            "bars": [self._serialize_bar(bar) for bar in window.bars],
            "markers": [],
            "signals": [],
            "orders": [],
            "trades": [],
            "curve": [],
            "metrics": None,
            "evidence": self._evidence(window),
        }

    def theory(self, source: str | None, symbol: str, asof: str) -> dict[str, Any]:
        """Run the same domain theory regardless of the selected provider."""

        window = self.window(source, symbol, asof)
        digest = self._bars_digest(window.bars)
        key = f"{window.requested_source}:{symbol}:{window.asof}:{digest}"
        with self._lock:
            cached = self._theory.get(key)
            if cached is not None:
                self._theory.move_to_end(key)
                return cached
        bars = list(window.bars)
        drawing = lecture_drawing(bars)  # type: ignore[no-untyped-call]
        first = reversal_trends(drawing, bars)  # type: ignore[no-untyped-call]
        second = secondary_trends(first, bars)  # type: ignore[no-untyped-call]
        result = {
            "asof": window.asof,
            "requested_asof": window.requested_asof,
            "data_source": window.requested_source,
            "resolved_source": window.resolved_source,
            "providers": list(window.providers),
            "supplemented_bars": window.supplemented_bars,
            "source_fallback": window.resolved_source != window.requested_source,
            "source_warning": window.primary_error,
            "data_version": digest,
            "points": [],
            "polyline_segments": [],
            "events": [],
            "shapes": [],
            "counts": {},
            "lecture_drawing": drawing,
            "reversal_trends": first,
            "secondary_trends": second,
            "tertiary_trends": tertiary_trends(second, bars),  # type: ignore[no-untyped-call]
            "interrupted": False,
            "computed_from": "canonical_market_data_repository",
            "price_basis": "raw_unadjusted",
        }
        with self._lock:
            self._theory[key] = result
            self._theory.move_to_end(key)
            while len(self._theory) > 32:
                self._theory.popitem(last=False)
        return result

    def _selected(self, source: str | None) -> str:
        selected = source or self.default_source
        if selected not in self.adapters:
            raise ValueError(f"行情源不可用：{selected}")
        return selected

    def _order(self, source: str) -> tuple[str, ...]:
        fallbacks = tuple(candidate for candidate in self.fallback_order.get(source, ()) if candidate in self.adapters)
        return (source, *fallbacks)

    @staticmethod
    def _validate_series(symbol: str, prefix: Sequence[Bar], complete: Sequence[Bar]) -> None:
        if not prefix or not complete:
            raise ValueError("行情源未返回有效日线")
        if any(bar.symbol != symbol for bar in complete):
            raise ValueError("行情源返回了错误证券")
        if any(current.timestamp <= previous.timestamp for previous, current in zip(complete, complete[1:])):
            raise ValueError("行情源返回重复或倒序日期")
        complete_days = {bar.timestamp for bar in complete}
        if any(bar.timestamp not in complete_days for bar in prefix):
            raise ValueError("行情前缀不属于完整序列")

    @staticmethod
    def _serialize_bar(bar: Bar) -> dict[str, object]:
        return {
            "time": bar.timestamp.date().isoformat(),
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "raw_close": bar.close,
            "volume": bar.volume,
            "factor": 1,
        }

    @staticmethod
    def _bars_digest(bars: Sequence[Bar]) -> str:
        payload = [
            [bar.timestamp.date().isoformat(), bar.open, bar.high, bar.low, bar.close, bar.volume] for bar in bars
        ]
        return hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()

    @staticmethod
    def _evidence(window: MarketDataWindow) -> str:
        if window.resolved_source != window.requested_source:
            return (
                f"首选 {window.requested_source} 不可用，已由 {window.resolved_source} 补齐；"
                "原始不复权日线，仅用于行情与讲义绘图，未执行回测。"
            )
        if window.supplemented_bars:
            return (
                f"{window.requested_source} 原始不复权日线，并由备用源补齐 {window.supplemented_bars} 个缺失交易日；"
                "仅用于行情与讲义绘图，未执行回测。"
            )
        return f"{window.requested_source} 原始不复权日线，仅用于行情与讲义绘图；未执行回测。"
