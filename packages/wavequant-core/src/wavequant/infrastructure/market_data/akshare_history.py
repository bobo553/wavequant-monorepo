"""Pinned AKShare/Sina history: one upstream, raw prices, explicit coverage."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import math
from typing import Any

from .akshare import AkShareProvider
from .minute import MinuteBar, verify_minute_day
from ..persistence.artifact_cache import ArtifactCache
from ...domain.models.model import Bar


class MinuteCoverageError(ValueError):
    """A requested session is outside the complete same-source minute history."""

    def __init__(self, day: str, start: str | None, end: str | None, provider: str = "AKShare / 新浪"):
        self.coverage = dict(required_date=day, available_start=start, available_end=end, provider=provider)
        available = f"{start} 至 {end}" if start and end else "暂无完整交易日"
        super().__init__(
            f"{provider} 缺少 {day} 的完整五分钟线；当前可用区间：{available}。"
            "本次未生成成交或收益，请补齐同源分钟历史或选择可用区间。"
        )


class AkShareMinuteSource:
    """Never fall back to BaoStock, TDX, another upstream or synthetic bars."""

    def __init__(self, provider: AkShareProvider, cache: ArtifactCache, symbol: str):
        self.provider, self.cache, self.symbol = provider, cache, symbol
        self.rows: dict[str, list[dict[str, str]]] | None = None
        self.digests: dict[str, str] = {}

    def _load(self) -> None:
        frame = self.provider.call("stock_zh_a_minute", symbol=self.symbol.replace(".", ""), period="5", adjust="")
        records = frame.to_dict("records")
        if len(records) > 20000:
            raise ValueError("AKShare 分钟返回超过允许的历史范围")
        grouped: dict[str, list[dict[str, str]]] = {}
        for item in records:
            stamp = datetime.fromisoformat(str(item["day"]))
            day = stamp.date().isoformat()
            grouped.setdefault(day, []).append(
                dict(
                    date=day,
                    time=stamp.strftime("%Y%m%d%H%M%S") + "000",
                    code=self.symbol,
                    adjustflag="3",
                    **{key: str(item[key]) for key in ("open", "high", "low", "close", "volume")},
                )
            )
        for day, rows in grouped.items():
            rows.sort(key=lambda row: row["time"])
            # Keep partial sessions visible as unavailable; never cache them as complete.
            if len(rows) == 48:
                self.cache.put("akshare-sina-minute", dict(symbol=self.symbol, day=day, adjust="raw"), rows)  # type: ignore[no-untyped-call]  # Legacy cache boundary.
        self.rows = grouped

    def get(self, daily: Bar) -> list[MinuteBar]:
        if daily.symbol != self.symbol:
            raise ValueError("分钟证券与日线不一致")
        if self.rows is None:
            self._load()
        assert self.rows is not None
        day = daily.timestamp.date().isoformat()
        rows = self.rows.get(day)
        if rows is None or len(rows) != 48:
            rows = self.cache.get("akshare-sina-minute", dict(symbol=self.symbol, day=day, adjust="raw"))  # type: ignore[no-untyped-call]  # Legacy cache boundary.
        if not rows or len(rows) != 48:
            complete = sorted(key for key, value in self.rows.items() if len(value) == 48)
            raise MinuteCoverageError(day, complete[0] if complete else None, complete[-1] if complete else None)
        minute = verify_minute_day(rows, daily)
        self.digests[day] = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
        return minute

    def provenance(self) -> dict[str, Any]:
        return dict(
            provider="akshare",
            upstream="sina",
            endpoint="stock_zh_a_minute",
            frequency="5m",
            adjustment="raw_then_same_daily_factor",
            volume_unit="shares",
            session_sha256=dict(self.digests),
        )


def sina_factors(provider: AkShareProvider, symbol: str) -> list[tuple[str, float]]:
    """Use the upstream factor table, not rounded adjusted-close ratios."""
    frame = provider.call("stock_zh_a_daily", symbol=symbol.replace(".", ""), adjust="hfq-factor")
    rows = [(str(row["date"]).split()[0], float(row["hfq_factor"])) for row in frame.to_dict("records")]
    if not rows or any(not math.isfinite(value) or value <= 0 for _, value in rows):
        raise ValueError("AKShare / 新浪复权因子缺失或无效")
    rows.sort()
    if len({day for day, _ in rows}) != len(rows):
        raise ValueError("AKShare / 新浪复权因子日期重复")
    return rows
