from __future__ import annotations

import csv
import math
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .model import Bar, Signal, Trade


REQUIRED_COLUMNS = {"timestamp", "symbol", "open", "high", "low", "close", "volume"}


def _parse_bool(value: str | None, default: bool = True) -> bool:
    if value is None or value.strip() == "":
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y"}:
        return True
    if normalized in {"0", "false", "no", "n"}:
        return False
    raise ValueError(f"invalid boolean value: {value!r}")


def _parse_timestamp(value: str) -> datetime:
    normalized = value.strip().replace("Z", "+00:00")
    return datetime.fromisoformat(normalized)


def load_bars(path: str | Path) -> dict[str, list[Bar]]:
    """Load OHLCV data and return bars grouped by symbol.

    Optional ``buyable``/``sellable`` columns let the caller model limit-up,
    limit-down, suspension, or any other venue-specific execution constraint.
    """

    result: dict[str, list[Bar]] = {}
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = REQUIRED_COLUMNS - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"missing columns: {sorted(missing)}")
        for row_number, row in enumerate(reader, start=2):
            try:
                bar = Bar(
                    timestamp=_parse_timestamp(row["timestamp"]),
                    symbol=row["symbol"].strip(),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                    buyable=_parse_bool(row.get("buyable")),
                    sellable=_parse_bool(row.get("sellable")),
                    adjustment_factor=float(row.get("adjustment_factor") or 1.0),
                )
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid row {row_number}: {exc}") from exc
            _validate_bar(bar, row_number)
            result.setdefault(bar.symbol, []).append(bar)

    if not result:
        raise ValueError("CSV contains no bars")
    awareness = {bar.timestamp.tzinfo is not None for bars in result.values() for bar in bars}
    if len(awareness) != 1:
        raise ValueError("do not mix timezone-aware and naive timestamps")
    for symbol, bars in result.items():
        bars.sort(key=lambda item: item.timestamp)
        for previous, current in zip(bars, bars[1:]):
            if current.timestamp <= previous.timestamp:
                raise ValueError(f"duplicate/non-increasing timestamp for {symbol}: {current.timestamp}")
    return result


def _validate_bar(bar: Bar, row_number: int) -> None:
    for name in ("open", "high", "low", "close", "volume", "adjustment_factor"):
        if not math.isfinite(getattr(bar, name)):
            raise ValueError(f"row {row_number}: {name} must be finite")
    if bar.adjustment_factor <= 0:
        raise ValueError(f"row {row_number}: adjustment_factor must be positive")
    if not bar.symbol:
        raise ValueError(f"row {row_number}: symbol is empty")
    if min(bar.open, bar.high, bar.low, bar.close) <= 0:
        raise ValueError(f"row {row_number}: OHLC prices must be positive")
    if bar.high < max(bar.open, bar.close, bar.low):
        raise ValueError(f"row {row_number}: high is inconsistent with OHLC")
    if bar.low > min(bar.open, bar.close, bar.high):
        raise ValueError(f"row {row_number}: low is inconsistent with OHLC")
    if bar.volume < 0:
        raise ValueError(f"row {row_number}: volume must be non-negative")


def write_signals(path: str | Path, signals: Iterable[Signal]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "timestamp", "symbol", "side", "reference_price", "invalidation_price",
        "reason", "trigger_timestamp", "retracement", "rvol", "regime", "target_price", "minimum_reward_risk",
    ]
    with target.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for signal in signals:
            writer.writerow({
                "timestamp": signal.timestamp.isoformat(),
                "symbol": signal.symbol,
                "side": signal.side,
                "reference_price": signal.reference_price,
                "invalidation_price": signal.invalidation_price,
                "reason": signal.reason,
                "trigger_timestamp": signal.trigger_timestamp.isoformat(),
                "retracement": signal.retracement,
                "rvol": signal.rvol,
                "regime": signal.regime,
                "target_price": signal.target_price,
                "minimum_reward_risk": signal.minimum_reward_risk,
            })


def write_trades(path: str | Path, trades: Iterable[Trade]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "symbol", "entry_time", "exit_time", "entry_price", "exit_price",
        "stop_price", "quantity", "gross_return", "net_return", "bars_held",
        "entry_reason", "exit_reason", "pnl", "fees",
    ]
    with target.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for trade in trades:
            row = trade.__dict__.copy()
            row["entry_time"] = trade.entry_time.isoformat()
            row["exit_time"] = trade.exit_time.isoformat()
            writer.writerow(row)
