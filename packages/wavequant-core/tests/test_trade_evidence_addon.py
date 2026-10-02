"""Regression coverage for one position cycle with multiple buy fills."""

from datetime import datetime
from types import SimpleNamespace

from wavequant.application.analytics.trade_evidence import enrich_ledger, result_markers
from wavequant.application.analytics.holding_drawdown import holding_drawdowns
from wavequant.domain.models.model import Bar


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
    orders[4]['quantity'] = 200.0  # Full liquidation of the remaining two lots.
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


def test_cycle_identity_uses_filled_quantity_even_with_misleading_close_flags() -> None:
    symbol = 'TEST'
    bars = [Bar(datetime(2026, 1, 1), symbol, 10, 20, 8, 20, 10000)]
    orders = [dict(symbol=symbol, timestamp='2026-01-01T00:00:00', status='filled', side=side,
                   quantity=quantity, price=price, position_closed=closed,
                   execution_model='intraday_5m_next_open', execution_timestamp=f'2026-01-01T{when}')
              for side, quantity, price, closed, when in [
                  ('BUY', 100, 10, False, '09:30:00'), ('SELL', 40, 9, True, '10:00:00'),
                  ('SELL', 60, 9, False, '11:00:00'), ('BUY', 200, 20, False, '14:00:00')]]
    result = SimpleNamespace(orders=orders, holding_drawdowns=holding_drawdowns({symbol: bars}, orders),
                             open_positions=[dict(symbol=symbol)])
    enrich_ledger(bars, result, SimpleNamespace(signals=[], audit=[]), {})
    first, second = result.holding_drawdowns
    assert {o['trade_id'] for o in orders[:3]} == {first['trade_id']}
    assert orders[3]['trade_id'] == second['trade_id'] != first['trade_id']
    assert result.open_positions[0]['trade_id'] == second['trade_id']
