"""Adapt BaoStock daily data, explicit snapshots, and seeded offline fixtures."""
from __future__ import annotations

import csv
import hashlib
import json
import random
import re
import socket
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from .io import load_bars


DEFAULT_SYMBOLS = ["sh.600036", "sh.601318", "sh.600519", "sh.600900",
                   "sh.600104", "sz.000651", "sz.000333", "sz.000858",
                   "sh.600276", "sz.002415"]
FIELDS = "date,code,open,high,low,close,preclose,volume,tradestatus,isST"
COLUMNS = ["timestamp", "symbol", "open", "high", "low", "close", "volume",
           "buyable", "sellable", "adjustment_factor"]


def dump_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_dataset(path: Path, rows: list[dict], metadata: dict) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    grouped = load_bars(path)
    metadata.update({"rows": sum(map(len, grouped.values())), "sha256": fingerprint(path),
                     "symbols": sorted(grouped), "csv": path.name})
    dump_json(path.with_suffix(".metadata.json"), metadata)
    return metadata


def opening_permissions(row: dict) -> tuple[bool, bool]:
    """Main-board policy: skip new ST entries; reject opens at either daily limit.

    Uses open and official preclose, never the day's closing price or volume to
    infer whether an opening order could have filled. No queue simulation.
    """
    if row["tradestatus"] != "1":
        return False, False
    d = date.fromisoformat(row["date"])
    st = row["isST"] == "1"
    rate = Decimal("0.05") if st and d < date(2026, 7, 6) else Decimal("0.10")
    previous = Decimal(row["preclose"])
    upper = (previous * (1 + rate)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    lower = (previous * (1 - rate)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    price = Decimal(row["open"])
    return not st and price < upper, price > lower


def fetch_baostock(path: Path, symbols: list[str], start: str, end: str,
                   cache: Path, refresh: bool = False) -> dict:
    start_date, end_date = date.fromisoformat(start), date.fromisoformat(end)
    if start_date > end_date or start_date < date(2018, 1, 1):
        raise ValueError("supported sample: start >= 2018-01-01 and start <= end")
    if not symbols or len(symbols) != len(set(symbols)):
        raise ValueError("provide a non-empty unique symbol list")
    for symbol in symbols:
        if not re.fullmatch(r"(?:sh\.60\d{4}|sz\.00\d{4})", symbol):
            raise ValueError(f"{symbol}: downloader supports main-board A shares only")
    try:
        import baostock as bs
    except ImportError as exc:
        raise RuntimeError('install data extra: python -m pip install -e ".[data]"') from exc

    cache.mkdir(parents=True, exist_ok=True)
    socket.setdefaulttimeout(20)
    rows, snapshots = [], []
    logged_in = False
    try:
        for symbol in symbols:
            snapshot = cache / f"{symbol}_{start}_{end}.json"
            if snapshot.exists() and not refresh:
                source = json.loads(snapshot.read_text(encoding="utf-8"))
            else:
                if not logged_in:
                    login = bs.login()
                    if login.error_code != "0":
                        raise RuntimeError(f"BaoStock login: {login.error_msg}")
                    logged_in = True
                source = {"symbol": symbol, "start": start, "end": end,
                          "retrieved_at": datetime.now(timezone.utc).isoformat(),
                          "provider_version": getattr(bs, "__version__", "unknown")}
                for key, flag in (("raw", "3"), ("adjusted", "1")):
                    response = bs.query_history_k_data_plus(
                        symbol, FIELDS, start_date=start, end_date=end, frequency="d", adjustflag=flag)
                    if response.error_code != "0":
                        raise RuntimeError(f"{symbol} {key}: {response.error_msg}")
                    records = []
                    while response.next():
                        records.append(dict(zip(response.fields, response.get_row_data())))
                    if response.error_code != "0" or not records:
                        raise RuntimeError(f"{symbol}: empty/failed {key} response")
                    source[key] = records
                dump_json(snapshot, source)
            raw_by_day = {row["date"]: row for row in source["raw"]}
            if {row["date"] for row in source["adjusted"]} != set(raw_by_day):
                raise ValueError(f"{symbol}: raw/adjusted dates do not match")
            for adjusted in source["adjusted"]:
                raw = raw_by_day[adjusted["date"]]
                if not raw["close"] or not adjusted["close"]:
                    raise ValueError(f"{symbol} {raw['date']}: missing price; import a validated suspension mark")
                buyable, sellable = opening_permissions(raw)
                factor = float(adjusted["close"]) / float(raw["close"])
                row = {name: adjusted[name] for name in ("open", "high", "low", "close")}
                row.update({"timestamp": raw["date"], "symbol": symbol, "volume": raw["volume"] or 0,
                            "buyable": int(buyable), "sellable": int(sellable),
                            "adjustment_factor": factor})
                rows.append(row)
            snapshots.append({"file": str(snapshot.resolve()), "sha256": fingerprint(snapshot),
                              "retrieved_at": source["retrieved_at"]})
            print(f"data {symbol}: {len(source['raw'])} bars", flush=True)
    finally:
        if logged_in:
            bs.logout()
    return write_dataset(path, rows, {
        "kind": "real_market", "source": "BaoStock", "frequency": "daily",
        "start": start, "end": end, "snapshots": snapshots,
        "price_basis": "back-adjusted OHLC with raw-volume and raw-price factor",
        "limitations": ["Fixed convenience universe; survival/selection bias; not all-market evidence.",
                        "Adjusted equivalent units approximate reinvested corporate actions, not a broker ledger.",
                        "Opening limit and ST flags are coarse; no auction queue or bid-ask history.",
                        "No IPO exception model: use established main-board stocks only."]})


def synthetic_dataset(path: Path, seed: int = 20260907, sessions: int = 1200,
                      symbols: int = 8) -> dict:
    if sessions < 50 or symbols < 1:
        raise ValueError("synthetic data requires >=50 sessions and >=1 symbol")
    rng = random.Random(seed)
    days = []
    current = date(2018, 1, 2)
    while len(days) < sessions:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    market = [rng.gauss(0, .007) for _ in days]
    rows = []
    for number in range(symbols):
        price = 12 + number * 4
        for i, day in enumerate(days):
            regime = ((i + number * 17) // 90) % 4
            drift = [.002, -.0015, 0, .0008][regime]
            overnight = rng.gauss(0, .004)
            op = max(1, price * (1 + overnight))
            ret = drift + .5 * market[i] + rng.gauss(0, .013)
            close = max(1, op * (1 + ret))
            spread = abs(rng.gauss(.008, .003))
            high, low = max(op, close) * (1 + spread), min(op, close) * (1 - spread)
            volume = int(2_000_000 * rng.lognormvariate(0, .3) * (1 + abs(ret) * 30))
            rows.append(dict(timestamp=day.isoformat(), symbol=f"SYN{number:03d}",
                             open=op, high=high, low=low, close=close, volume=volume,
                             buyable=1, sellable=1, adjustment_factor=1.0))
            price = close
    return write_dataset(path, rows, {"kind": "synthetic", "source": "seeded simulation", "seed": seed,
                                     "frequency": "daily", "start": days[0].isoformat(),
                                     "end": days[-1].isoformat(),
                                     "limitations": ["Engineering validation only; simulated weekday calendar."]})
