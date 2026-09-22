"""Date-specific AkShare limit-up snapshots; never substitute another session."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import math
import re
from threading import Lock
from typing import Any, Protocol, TypedDict

from wavequant.infrastructure.market_data.akshare import AkShareUnavailable  # type: ignore[import-untyped]  # Core has no py.typed marker.


CHINA = timezone(timedelta(hours=8))


class PoolProvider(Protocol):
    def call(self, name: str, **kwargs: Any) -> Any: ...


class LimitUpStock(TypedDict):
    code: str
    name: str
    streak: int
    price: float | None
    change_pct: float | None
    amount: float | None
    turnover_pct: float | None
    seal_amount: float | None
    first_seal: str | None
    last_seal: str | None
    open_count: int | None
    industry: str


class LadderSnapshot(TypedDict):
    date: str
    source: str
    fetched_at: str
    status: str
    notice: str
    stocks: list[LimitUpStock]


def finite_number(value: object) -> float | None:
    """Unknown/missing provider values remain missing instead of becoming zero."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(str(value))
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def seal_time(value: object) -> str | None:
    text = str(value).removesuffix(".0").zfill(6)
    if not re.fullmatch(r"\d{6}", text):
        return None
    try:
        return datetime.strptime(text, "%H%M%S").strftime("%H:%M:%S")
    except ValueError:
        return None


def normalize_pool(frame: Any) -> list[LimitUpStock]:
    """Validate pandas at the external boundary before exposing JSON."""
    if not hasattr(frame, "empty") or not hasattr(frame, "to_dict"):
        raise AkShareUnavailable("AkShare 涨停池返回格式异常")
    if frame.empty:
        return []
    required = {"代码", "名称", "连板数"}
    if not required.issubset(frame.columns):
        raise AkShareUnavailable("AkShare 涨停池缺少必要字段")
    stocks: list[LimitUpStock] = []
    seen: set[str] = set()
    for row in frame.to_dict(orient="records"):
        code = str(row["代码"]).removesuffix(".0").zfill(6)
        name = str(row["名称"]).strip()
        streak = finite_number(row["连板数"])
        if (
            not re.fullmatch(r"\d{6}", code)
            or not name
            or name in {"nan", "None"}
            or streak is None
            or not streak.is_integer()
            or not 1 <= streak <= 100
        ):
            raise AkShareUnavailable("AkShare 涨停池包含无效证券或连板数")
        if code in seen:
            raise AkShareUnavailable("AkShare 涨停池包含重复证券")
        seen.add(code)
        openings = finite_number(row.get("炸板次数"))
        stocks.append(
            LimitUpStock(
                code=code,
                name=name,
                streak=int(streak),
                price=finite_number(row.get("最新价")),
                change_pct=finite_number(row.get("涨跌幅")),
                amount=finite_number(row.get("成交额")),
                turnover_pct=finite_number(row.get("换手率")),
                seal_amount=finite_number(row.get("封板资金")),
                first_seal=seal_time(row.get("首次封板时间")),
                last_seal=seal_time(row.get("最后封板时间")),
                open_count=int(openings) if openings is not None and openings >= 0 and openings.is_integer() else None,
                industry=str(row.get("所属行业") or "未分类"),
            )
        )
    return sorted(stocks, key=lambda row: (-row["streak"], row["first_seal"] or "99", row["code"]))


class LimitUpLadderService:
    """Bounded date-keyed cache; short TTL also lets empty intraday pools refresh."""

    def __init__(self, provider: PoolProvider, *, clock: Callable[[], datetime] | None = None):
        self.provider = provider
        self.clock = clock or (lambda: datetime.now(CHINA))
        self._cache: OrderedDict[str, tuple[datetime, LadderSnapshot]] = OrderedDict()
        self._lock = Lock()

    def snapshot(self, selected: str | None = None, *, refresh: bool = False) -> LadderSnapshot:
        now = self.clock().astimezone(CHINA)
        chosen = now.date() if selected is None else date.fromisoformat(selected)
        if selected is not None and chosen.isoformat() != selected:
            raise ValueError("日期必须为 YYYY-MM-DD")
        if chosen > now.date():
            raise ValueError("不能查询未来日期")
        if chosen.year < 1990:
            raise ValueError("日期不能早于 1990-01-01")
        key = chosen.isoformat()
        with self._lock:
            cached = self._cache.get(key)
            if cached and not refresh and now < cached[0]:
                self._cache.move_to_end(key)
                return deepcopy(cached[1])
        stocks = normalize_pool(self.provider.call("stock_zt_pool_em", date=chosen.strftime("%Y%m%d")))
        value = LadderSnapshot(
            date=key,
            source="akshare/eastmoney",
            fetched_at=now.isoformat(),
            status="ok" if stocks else "empty",
            stocks=stocks,
            notice=(
                "东方财富涨停股池口径，不含 ST、科创板及未开板新股；当日数据为抓取时快照。"
                if stocks
                else "该日期暂无涨停池数据，可能为休市日、尚未发布或超出上游历史范围。"
            ),
        )
        ttl = 60 if chosen == now.date() or not stocks else 3600
        with self._lock:
            self._cache[key] = (now + timedelta(seconds=ttl), value)
            self._cache.move_to_end(key)
            while len(self._cache) > 128:
                self._cache.popitem(last=False)
        return deepcopy(value)
