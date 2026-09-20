"""Verified five-minute BaoStock history for closing-window research."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from threading import RLock
from zoneinfo import ZoneInfo
import hashlib
import json
import math
from pathlib import Path
import re
import struct

from wavequant.domain.models.model import Bar
from wavequant.infrastructure.persistence.artifact_cache import ArtifactCache


_BAOSTOCK_LOCK = RLock()
_SHANGHAI = ZoneInfo("Asia/Shanghai")
_FIELDS = "date,time,code,open,high,low,close,volume,amount,adjustflag"


@dataclass(frozen=True)
class MinuteBar:
    """A completed five-minute bar; timestamp is its end, in Shanghai time."""

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


def _expected_times() -> list[time]:
    return (
        [time(9, minute) for minute in range(35, 60, 5)]
        + [time(10, minute) for minute in range(0, 60, 5)]
        + [time(11, minute) for minute in range(0, 35, 5)]
        + [time(13, minute) for minute in range(5, 60, 5)]
        + [time(14, minute) for minute in range(0, 60, 5)]
        + [time(15, 0)]
    )


def fetch_baostock_day(symbol: str, day: date) -> list[dict[str, str]]:
    """Read one complete unadjusted session; serialize BaoStock's process-wide socket."""

    try:
        import baostock as bs  # type: ignore[import-untyped]  # BaoStock ships no type metadata.
    except ImportError as exc:
        raise ValueError("缺少 baostock 分钟行情依赖，无法运行五分钟精确回测") from exc
    with _BAOSTOCK_LOCK:
        login = bs.login()
        if login.error_code != "0":
            raise ValueError(f"BaoStock 登录失败：{login.error_msg}")
        try:
            response = bs.query_history_k_data_plus(
                symbol,
                _FIELDS,
                start_date=day.isoformat(),
                end_date=day.isoformat(),
                frequency="5",
                adjustflag="3",
            )
            if response.error_code != "0":
                raise ValueError(f"BaoStock 五分钟行情查询失败：{response.error_msg}")
            rows = []
            while response.next():
                rows.append(dict(zip(response.fields, response.get_row_data())))
            return rows
        finally:
            bs.logout()


def verify_minute_day(rows: list[dict[str, str]], daily: Bar) -> list[MinuteBar]:
    """Reject incomplete or mismatched data before any fill may use it."""

    day = daily.timestamp.date()
    if len(rows) != 48:
        raise ValueError(f"{day} 五分钟行情只有 {len(rows)} 根，需完整 48 根才能精确回测")
    result = []
    for row, expected in zip(rows, _expected_times()):
        stamp = row["time"]
        if (
            row["date"] != day.isoformat()
            or row["code"] != daily.symbol
            or row["adjustflag"] != "3"
            or len(stamp) != 17
            or stamp[:8] != day.strftime("%Y%m%d")
            or stamp[14:] != "000"
        ):
            raise ValueError(f"{day} 五分钟行情日期、证券或复权口径不一致")
        ended = datetime.strptime(stamp[:14], "%Y%m%d%H%M%S").replace(tzinfo=_SHANGHAI)
        if ended.time() != expected:
            raise ValueError(f"{day} 五分钟行情时间段不完整或顺序异常")
        values = [float(row[key]) for key in ("open", "high", "low", "close", "volume")]
        opening, high, low, close, volume = values
        if (
            not all(math.isfinite(value) for value in values)
            or min(opening, high, low, close) <= 0
            or volume < 0
            or low > min(opening, close)
            or high < max(opening, close)
        ):
            raise ValueError(f"{day} 五分钟行情价格或成交量无效")
        result.append(MinuteBar(ended, opening, high, low, close, volume))
    factor = daily.adjustment_factor
    checks = (
        (result[0].open, daily.open / factor),
        (max(item.high for item in result), daily.high / factor),
        (min(item.low for item in result), daily.low / factor),
        (result[-1].close, daily.close / factor),
    )
    if any(abs(actual - expected) > 0.015 for actual, expected in checks):
        raise ValueError(f"{day} 五分钟与同源日线 OHLC 不一致")
    if abs(sum(item.volume for item in result) - daily.volume) > max(1, daily.volume * 0.00001):
        raise ValueError(f"{day} 五分钟与同源日线成交量不一致")
    return result


class CachedMinuteSource:
    """Pin observed minute rows to a verified local daily bar and record lineage."""

    def __init__(self, cache: ArtifactCache):
        self.cache = cache
        self.digests: dict[str, str] = {}

    def get(self, daily: Bar) -> list[MinuteBar]:
        day = daily.timestamp.date()
        if day < date(2020, 1, 1):
            raise ValueError(f"{day} 无可用五分钟历史行情；精确回测请从 2020-01-01 起选择区间")
        key = dict(
            provider="baostock",
            frequency="5",
            adjustflag="3",
            symbol=daily.symbol,
            date=day.isoformat(),
            daily=[daily.open, daily.high, daily.low, daily.close, daily.volume, daily.adjustment_factor],
        )
        rows = self.cache.get("minute", key)  # type: ignore[no-untyped-call]  # Legacy cache API is untyped.
        if rows is None:
            rows = fetch_baostock_day(daily.symbol, day)
            verify_minute_day(rows, daily)
            self.cache.put("minute", key, rows)  # type: ignore[no-untyped-call]  # Legacy cache API is untyped.
        minute = verify_minute_day(rows, daily)
        self.digests[day.isoformat()] = hashlib.sha256(
            json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return minute

    def provenance(self) -> dict[str, object]:
        return dict(
            provider="BaoStock",
            frequency="5m",
            adjustflag="3",
            bar_time="Asia/Shanghai interval end",
            session_sha256=dict(self.digests),
        )


class TdxMinuteSource:
    """Use native TDX lc5 sessions with TDX daily bars; never substitute a feed."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.digests: dict[str, str] = {}

    def path(self, symbol: str) -> Path:
        if not re.fullmatch(r"(sh|sz|bj)\.\d{6}", symbol):
            raise ValueError("invalid minute symbol")
        market, code = symbol.split(".")
        return self.root / "vipdoc" / market / "fzline" / f"{market}{code}.lc5"

    def get(self, daily: Bar) -> list[MinuteBar]:
        path = self.path(daily.symbol)
        if not path.is_file():
            from .akshare_history import MinuteCoverageError

            raise MinuteCoverageError(daily.timestamp.date().isoformat(), None, None, "通达信本地")
        payload = path.read_bytes()
        layout = struct.Struct("<HHfffffII")
        if len(payload) % layout.size:
            raise ValueError("通达信五分钟文件记录不完整")
        rows = []
        for packed, clock, opening, high, low, close, _amount, volume, _reserved in layout.iter_unpack(payload):
            stamp = datetime(2004 + packed // 2048, packed % 2048 // 100, packed % 2048 % 100, clock // 60, clock % 60)
            if stamp.date() == daily.timestamp.date():
                rows.append(
                    dict(
                        date=stamp.date().isoformat(),
                        time=stamp.strftime("%Y%m%d%H%M%S") + "000",
                        code=daily.symbol,
                        adjustflag="3",
                        open=str(opening),
                        high=str(high),
                        low=str(low),
                        close=str(close),
                        volume=str(volume),
                    )
                )
        if len(rows) != 48:
            from .akshare_history import MinuteCoverageError

            raise MinuteCoverageError(daily.timestamp.date().isoformat(), None, None, "通达信本地")
        result = verify_minute_day(rows, daily)
        self.digests[daily.timestamp.date().isoformat()] = hashlib.sha256(payload).hexdigest()
        return result

    def provenance(self) -> dict[str, object]:
        return dict(provider="tdx", upstream="tdx_local", frequency="5m", session_sha256=dict(self.digests))
