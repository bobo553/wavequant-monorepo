"""Regression coverage for one position cycle with multiple buy fills."""

from datetime import datetime
from types import SimpleNamespace

from wavequant.application.analytics.trade_evidence import enrich_ledger, result_markers


def test_addon_buys_keep_cycle_identity_and_open_position_link() -> None:
    symbol = "sz.001216"
    dates = ["2026-08-26", "2026-09-15", "2026-09-16", "2026-09-17", "2026-09-18", "2026-09-21", "2026-09-22"]
    bars = [
        SimpleNamespace(timestamp=datetime.fromisoformat(day), symbol=symbol, adjustment_factor=1.0) for day in dates
    ]
    orders = [
        {
            "timestamp": f"{day}T00:00:00",
            "symbol": symbol,
            "side": side,
            "status": status,
            "price": 10.0,
            "quantity": 100.0,
            "position_closed": closed,
        }
        for day, side, status, closed in [
            (dates[0], "BUY", "filled", False),
            (dates[1], "BUY", "filled", False),
            (dates[2], "SELL", "filled", False),
            (dates[3], "BUY", "filled", False),
            (dates[4], "SELL", "filled", True),
            (dates[5], "BUY", "filled", False),
            (dates[6], "BUY", "filled", False),
        ]
    ]
    result = SimpleNamespace(
        orders=orders,
        open_positions=[{"symbol": symbol, "entry_time": f"{dates[5]}T00:00:00"}],
    )
    generated = SimpleNamespace(signals=[], audit=[])

    enrich_ledger(bars, result, generated, {})

    first_cycle = {order["trade_id"] for order in orders[:5]}
    second_cycle = {order["trade_id"] for order in orders[5:]}
    assert len(first_cycle) == len(second_cycle) == 1
    assert first_cycle != second_cycle
    assert result.open_positions[0]["trade_id"] == orders[5]["trade_id"]
    assert [
        marker["trade_id"] for marker in result_markers({"orders": orders, "signals": []}) if marker["kind"] == "fill"
    ] == [order["trade_id"] for order in orders]
