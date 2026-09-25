"""Canonical market-data repository with source-isolated provider adapters.

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
        return {
            "provider_version": self.browser.provider.version,
            "upstream": "sina" if getattr(self.browser, "pinned_history", False) else "legacy_auto",
        }


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

    AkShare is the default when no source is specified. Chart and theory
    generation share a contract, while every read stays with its selected
    provider. An unavailable or stale provider cannot borrow another source.
    """

    def __init__(
        self,
        adapters: Sequence[MarketDataAdapter],
        *,
        default_source: str = "akshare",
    ):
        self.adapters = {adapter.source: adapter for adapter in adapters}
        if self.adapters and default_source not in self.adapters:
            default_source = next(iter(self.adapters))
        self.default_source = default_source
        self._theory: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._lock = Lock()

    def available_sources(self) -> tuple[str, ...]:
        """Return configured source identifiers in deterministic order."""

        return tuple(self.adapters)

    def catalog(self, source: str | None = None) -> dict[str, Any]:
        """Return only the selected provider's normalized security catalog."""

        selected = self._selected(source)
        try:
            payload = self.adapters[selected].catalog()
        except (FileNotFoundError, OSError, ValueError) as error:
            # Provider exceptions may contain a local path or upstream URL.
            raise ValueError(f"{selected} 行情目录暂不可用") from error

        merged: dict[str, dict[str, Any]] = {}
        rows = payload.get("stocks")
        if isinstance(rows, list):
            for raw in rows:
                if not isinstance(raw, dict) or not isinstance(raw.get("symbol"), str):
                    continue
                symbol = raw["symbol"]
                merged[symbol] = {
                    **raw,
                    "symbol": symbol,
                    "source": selected,
                    "catalog_source": selected,
                    "has_data": bool(raw.get("has_data", True)),
                    "data_source": selected,
                }
        if not merged:
            raise ValueError(f"{selected} 行情源未返回有效证券目录")

        stocks = [merged[symbol] for symbol in sorted(merged)]
        provider_warnings = payload.get("warnings", [])
        if not isinstance(provider_warnings, list):
            provider_warnings = []
        return {
            **payload,
            "available": True,
            "source": selected,
            "default_source": self.default_source,
            "providers": [selected],
            "stocks": stocks,
            "with_daily": sum(bool(stock.get("has_data")) for stock in stocks),
            "supplemented_stocks": 0,
            "warnings": provider_warnings,
        }

    def window(self, source: str | None, symbol: str, asof: str) -> MarketDataWindow:
        """Load one canonical series from the selected provider only."""

        selected = self._selected(source)
        requested = date.fromisoformat(asof)
        if requested.isoformat() != asof:
            raise ValueError("use YYYY-MM-DD date")
        try:
            prefix, complete = self.adapters[selected].load(symbol, asof)
            self._validate_series(symbol, prefix, complete)
        except (FileNotFoundError, OSError, ValueError) as error:
            raise ValueError(f"{selected} 数据暂不可用") from error

        # Keep the provider's complete known sessions for date navigation, but
        # never extend the requested prefix with bars from another provider.
        complete = sorted(complete, key=lambda bar: bar.timestamp)
        prefix = [bar for bar in complete if bar.timestamp.date() <= requested]
        if not prefix:
            raise ValueError("该回放日期之前没有日线数据")
        return MarketDataWindow(
            requested_source=selected,
            resolved_source=selected,
            requested_asof=asof,
            asof=prefix[-1].timestamp.date().isoformat(),
            bars=tuple(prefix),
            sessions=tuple(bar.timestamp.date().isoformat() for bar in complete),
            providers=(selected,),
            supplemented_bars=0,
        )

    def view(self, source: str | None, symbol: str, asof: str) -> dict[str, Any]:
        """Serialize a canonical chart view independent of provider schema."""

        window = self.window(source, symbol, asof)
        data_version = self._bars_digest(window.bars)
        return {
            **self.adapters[window.requested_source].metadata(),
            "symbol": symbol,
            "asof": window.asof,
            "source_policy": "single_upstream_v1",
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
        # These legacy market-structure builders have no typed boundary yet.
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
        return f"{window.requested_source} 原始不复权日线，仅用于行情与讲义绘图；未执行回测。"
