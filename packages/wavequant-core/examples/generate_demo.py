from __future__ import annotations

import csv
from datetime import datetime, timedelta
from pathlib import Path


def main() -> None:
    target = Path(__file__).with_name("demo_daily.csv")
    start = datetime(2025, 1, 2)
    rows = []
    close = 10.0
    for index in range(55):
        # Quiet history, then a high-volume breakout, shallow pause, and renewal.
        if index < 40:
            close += 0.025 + (0.015 if index % 3 == 0 else -0.005)
            volume = 100_000 + (index % 5) * 2_000
        elif index == 40:
            close += 0.65
            volume = 190_000
        elif index == 41:
            close -= 0.08
            volume = 110_000
        elif index == 42:
            close += 0.35
            volume = 140_000
        elif index < 49:
            close += 0.18
            volume = 125_000
        else:
            close -= 0.12
            volume = 135_000
        open_price = close - 0.04
        rows.append({
            "timestamp": (start + timedelta(days=index)).date().isoformat(),
            "symbol": "DEMO",
            "open": f"{open_price:.3f}",
            "high": f"{close + 0.04:.3f}",
            "low": f"{open_price - 0.10:.3f}",
            "close": f"{close:.3f}",
            "volume": str(volume),
            "buyable": "1",
            "sellable": "1",
        })
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    print(target)


if __name__ == "__main__":
    main()
