"""Server-side calendar aggregation for read-only market charts.

The signal snapshot algorithm version intentionally covers WaveQuant Core.
Chart-only timeframe support therefore lives in this API application service:
daily structure snapshots keep their existing version, while ad-hoc market
views and overlays can use the exact same aggregated OHLCV sequence.
"""

from __future__ import annotations

import calendar
from collections import OrderedDict
from datetime import date, datetime
import hashlib
import json
from threading import Lock
from typing import Any, Protocol, Sequence, cast

from wavequant.domain.market_structure.lecture_drawing import lecture_drawing  # type: ignore[import-untyped]
from wavequant.domain.market_structure.lecture_trend import reversal_trends  # type: ignore[import-untyped]
from wavequant.domain.market_structure.secondary_trend import secondary_trends  # type: ignore[import-untyped]
from wavequant.domain.market_structure.tertiary_trend import tertiary_trends  # type: ignore[import-untyped]
from wavequant.domain.models.model import Bar  # type: ignore[import-untyped]


TIMEFRAME_LABELS = {
    "1d": "日线",
    "1w": "周线",
    "1mo": "月线",
    "3mo": "季线",
    "1y": "年线",
}


class DailyMarketDataRepository(Protocol):
    """Minimum Core market-data contract used by the timeframe service."""

    def view(self, source: str, symbol: str, asof: str) -> dict[str, Any]:
        """Return a validated daily market view."""

    def theory(self, source: str, symbol: str, asof: str) -> dict[str, Any]:
        """Return canonical daily display theory."""


class MarketTimeframeService:
    """Aggregate validated daily views and compute matching display theory."""

    def __init__(self, repository: DailyMarketDataRepository):
        self.repository = repository
        self._theory: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._lock = Lock()

    def view(self, source: str, symbol: str, asof: str, timeframe: str = "1d") -> dict[str, Any]:
        """Return one market view at the requested supported timeframe."""

        self._validate_timeframe(timeframe)
        daily = self.repository.view(source, symbol, asof)
        if timeframe == "1d":
            return {
                **daily,
                "timeframe": timeframe,
                "timeframe_label": TIMEFRAME_LABELS[timeframe],
                "is_partial_last_bar": False,
                "timeframe_source": "provider_daily",
            }

        raw_bars = self._bars(daily)
        bars = self._aggregate_bars(raw_bars, timeframe)
        daily_sessions = self._sessions(daily)
        sessions = self._aggregate_sessions(daily_sessions, bars, timeframe)
        partial = self._is_partial_last_bar(raw_bars[-1], daily_sessions, timeframe)
        evidence = str(daily.get("evidence", "")).rstrip("。")
        return {
            **daily,
            "asof": bars[-1]["time"],
            "bars": bars,
            "sessions": sessions,
            "data_version": self._bars_digest(bars),
            "timeframe": timeframe,
            "timeframe_label": TIMEFRAME_LABELS[timeframe],
            "is_partial_last_bar": partial,
            "timeframe_source": "server_aggregated_from_daily",
            "evidence": f"{evidence}；服务器由规范日线按自然周期聚合为{TIMEFRAME_LABELS[timeframe]}。",
        }

    def theory(self, source: str, symbol: str, asof: str, timeframe: str = "1d") -> dict[str, Any]:
        """Compute display-only structure from the same bars returned by :meth:`view`."""

        if timeframe == "1d":
            # Calling the Core daily theory preserves every existing contract
            # and cache behavior without duplicating the canonical path.
            theory = self.repository.theory(source, symbol, asof)
            return {
                **theory,
                "timeframe": timeframe,
                "timeframe_label": TIMEFRAME_LABELS[timeframe],
                "is_partial_last_bar": False,
                "timeframe_source": "provider_daily",
            }

        view = self.view(source, symbol, asof, timeframe)
        data_version = str(view["data_version"])
        key = f"{source}:{symbol}:{view['asof']}:{timeframe}:{data_version}"
        with self._lock:
            cached = self._theory.get(key)
            if cached is not None:
                self._theory.move_to_end(key)
                return cached

        bars = [self._to_bar(symbol, row) for row in self._bars(view)]
        drawing = lecture_drawing(bars)
        first = reversal_trends(drawing, bars)
        second = secondary_trends(first, bars)
        result = {
            "asof": view["asof"],
            "requested_asof": view.get("requested_asof", asof),
            "data_source": view.get("data_source", source),
            "resolved_source": view.get("resolved_source", source),
            "providers": view.get("providers", [source]),
            "supplemented_bars": view.get("supplemented_bars", 0),
            "source_fallback": view.get("source_fallback", False),
            "source_warning": view.get("source_warning"),
            "data_version": data_version,
            "timeframe": timeframe,
            "timeframe_label": TIMEFRAME_LABELS[timeframe],
            "is_partial_last_bar": view["is_partial_last_bar"],
            "timeframe_source": "server_aggregated_from_daily",
            "points": [],
            "polyline_segments": [],
            "events": [],
            "shapes": [],
            "counts": {},
            "lecture_drawing": drawing,
            "reversal_trends": first,
            "secondary_trends": second,
            "tertiary_trends": tertiary_trends(second, bars),
            "interrupted": False,
            "computed_from": "api_calendar_timeframe_from_canonical_daily",
            "price_basis": view.get("price_basis", "raw_unadjusted"),
        }
        with self._lock:
            self._theory[key] = result
            self._theory.move_to_end(key)
            while len(self._theory) > 32:
                self._theory.popitem(last=False)
        return result

    @staticmethod
    def _validate_timeframe(timeframe: str) -> None:
        if timeframe not in TIMEFRAME_LABELS:
            raise ValueError("周期仅支持 1d、1w、1mo、3mo 或 1y")

    @staticmethod
    def _bars(payload: dict[str, Any]) -> list[dict[str, Any]]:
        rows = payload.get("bars")
        if not isinstance(rows, list) or not rows or not all(isinstance(row, dict) for row in rows):
            raise ValueError("日线行情响应缺少有效 K 线")
        return cast(list[dict[str, Any]], rows)

    @staticmethod
    def _sessions(payload: dict[str, Any]) -> list[date]:
        values = payload.get("sessions")
        if not isinstance(values, list) or not values or not all(isinstance(value, str) for value in values):
            raise ValueError("日线行情响应缺少有效交易日")
        return [date.fromisoformat(value) for value in cast(list[str], values)]

    @staticmethod
    def _number(row: dict[str, Any], field: str) -> float:
        value = row.get(field)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError(f"日线行情字段无效：{field}")
        return float(value)

    @classmethod
    def _aggregate_bars(cls, bars: Sequence[dict[str, Any]], timeframe: str) -> list[dict[str, Any]]:
        aggregated: list[dict[str, Any]] = []
        start = 0
        while start < len(bars):
            start_day = cls._row_day(bars[start])
            bucket = cls._bucket(start_day, timeframe)
            end = start + 1
            while end < len(bars) and cls._bucket(cls._row_day(bars[end]), timeframe) == bucket:
                end += 1
            group = bars[start:end]
            first = group[0]
            last = group[-1]
            aggregated.append(
                {
                    "time": cls._row_day(last).isoformat(),
                    "open": cls._number(first, "open"),
                    "high": max(cls._number(row, "high") for row in group),
                    "low": min(cls._number(row, "low") for row in group),
                    "close": cls._number(last, "close"),
                    "raw_close": cls._number(last, "raw_close"),
                    "volume": sum(cls._number(row, "volume") for row in group),
                    "factor": cls._number(last, "factor"),
                }
            )
            start = end
        return aggregated

    @classmethod
    def _aggregate_sessions(
        cls,
        daily_sessions: Sequence[date],
        prefix_bars: Sequence[dict[str, Any]],
        timeframe: str,
    ) -> list[str]:
        complete = cls._last_days_by_bucket(daily_sessions, timeframe)
        prefix_last = cls._row_day(prefix_bars[-1])
        last_bucket = cls._bucket(prefix_last, timeframe)
        complete_in_bucket = next((value for value in complete if cls._bucket(value, timeframe) == last_bucket), None)
        if complete_in_bucket is None or complete_in_bucket <= prefix_last:
            return [value.isoformat() for value in complete]
        prefix_sessions = [cls._row_day(row) for row in prefix_bars]
        future = [value for value in complete if cls._bucket(value, timeframe) > last_bucket]
        return [value.isoformat() for value in [*prefix_sessions, *future]]

    @classmethod
    def _is_partial_last_bar(
        cls,
        last_daily_bar: dict[str, Any],
        daily_sessions: Sequence[date],
        timeframe: str,
    ) -> bool:
        last_day = cls._row_day(last_daily_bar)
        bucket = cls._bucket(last_day, timeframe)
        complete_in_bucket = next(
            (value for value in reversed(daily_sessions) if cls._bucket(value, timeframe) == bucket),
            None,
        )
        if complete_in_bucket is not None and complete_in_bucket > last_day:
            return True
        return last_day == daily_sessions[-1] and not cls._is_calendar_period_end(last_day, timeframe)

    @staticmethod
    def _row_day(row: dict[str, Any]) -> date:
        value = row.get("time")
        if not isinstance(value, str):
            raise ValueError("日线行情日期无效")
        return date.fromisoformat(value)

    @staticmethod
    def _bucket(trading_day: date, timeframe: str) -> tuple[int, ...]:
        if timeframe == "1w":
            iso_year, iso_week, _weekday = trading_day.isocalendar()
            return iso_year, iso_week
        if timeframe == "1mo":
            return trading_day.year, trading_day.month
        if timeframe == "3mo":
            return trading_day.year, (trading_day.month - 1) // 3 + 1
        if timeframe == "1y":
            return (trading_day.year,)
        return trading_day.year, trading_day.month, trading_day.day

    @classmethod
    def _last_days_by_bucket(cls, days: Sequence[date], timeframe: str) -> list[date]:
        result: list[date] = []
        for trading_day in days:
            if result and cls._bucket(result[-1], timeframe) == cls._bucket(trading_day, timeframe):
                result[-1] = trading_day
            else:
                result.append(trading_day)
        return result

    @staticmethod
    def _is_calendar_period_end(trading_day: date, timeframe: str) -> bool:
        if timeframe == "1w":
            return trading_day.weekday() == 4
        if timeframe == "1mo":
            final_month = trading_day.month
        elif timeframe == "3mo":
            if trading_day.month not in {3, 6, 9, 12}:
                return False
            final_month = trading_day.month
        elif timeframe == "1y":
            if trading_day.month != 12:
                return False
            final_month = 12
        else:
            return True
        final_day = calendar.monthrange(trading_day.year, final_month)[1]
        while date(trading_day.year, final_month, final_day).weekday() > 4:
            final_day -= 1
        return trading_day.day == final_day

    @classmethod
    def _to_bar(cls, symbol: str, row: dict[str, Any]) -> Bar:
        return Bar(
            timestamp=datetime.combine(cls._row_day(row), datetime.min.time()),
            symbol=symbol,
            open=cls._number(row, "open"),
            high=cls._number(row, "high"),
            low=cls._number(row, "low"),
            close=cls._number(row, "close"),
            volume=cls._number(row, "volume"),
            adjustment_factor=cls._number(row, "factor"),
        )

    @classmethod
    def _bars_digest(cls, bars: Sequence[dict[str, Any]]) -> str:
        payload = [
            [
                cls._row_day(row).isoformat(),
                cls._number(row, "open"),
                cls._number(row, "high"),
                cls._number(row, "low"),
                cls._number(row, "close"),
                cls._number(row, "volume"),
            ]
            for row in bars
        ]
        return hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()
