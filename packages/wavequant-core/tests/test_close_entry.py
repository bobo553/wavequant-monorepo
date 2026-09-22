from dataclasses import replace
from datetime import date, datetime, timedelta
from types import SimpleNamespace

from wavequant.application.analytics.backtest import run_portfolio
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar, Signal
from wavequant.infrastructure.market_data.tdx import adjust_rows
from wavequant.application.analytics.trade_evidence import enrich_ledger


def fixture():
    bars = [
        Bar(datetime(2026, 8, 25) + timedelta(days=i), "sh.601086", 10, 10.6, 9.8, 10.4, 1000000, close_buyable=True)
        for i in range(4)
    ]
    signal = Signal(
        bars[1].timestamp, bars[1].symbol, 1, "LONG", 10.4, 9, "test", bars[0].timestamp, 0, 2, "轧空", 14, 0
    )
    return bars, signal, StrategyConfig(entry_at_close=True, net_reward_risk_filter=False)


def test_close_entry_fills_last_visible_signal_without_waiting_for_next_day():
    bars, signal, config = fixture()
    full = run_portfolio({signal.symbol: bars}, [signal], config)
    prefix = run_portfolio({signal.symbol: bars[:2]}, [signal], config)
    assert full.orders[0] == prefix.orders[0]
    order = prefix.orders[0]
    assert order["timestamp"] == signal.timestamp.isoformat()
    assert order["price"] == bars[1].close * (1 + config.slippage_bps_per_side / 10000)
    assert order["execution_model"] == "same_day_close"
    assert order["execution_timestamp"].endswith("15:00:00")
    assert "equity_at_open" not in order
    assert prefix.metrics["unexecuted_end_signals"] == 0
    legacy = run_portfolio({signal.symbol: bars}, [signal], replace(config, entry_at_close=False))
    assert legacy.orders[0]["timestamp"] == bars[2].timestamp.isoformat()


def test_disabled_reward_risk_filter_is_not_reported_as_a_failed_executed_gate():
    bars, signal, config = fixture()
    signal = replace(signal, target_price=11, minimum_reward_risk=1.5)
    result = run_portfolio({signal.symbol: bars}, [signal], config)
    enrich_ledger(bars, result, SimpleNamespace(signals=[signal], audit=[]), {"volume_filter": False})
    order = result.orders[0]
    assert order["status"] == "filled"
    assert order["net_reward_risk"] < 1.5
    assert order["entry_conditions"][-1]["passed"] is None
    strict = run_portfolio({signal.symbol: bars}, [signal], replace(config, net_reward_risk_filter=True))
    assert strict.orders[0]["status"] != "filled"


def test_close_permission_is_independent_of_open_and_cancel_does_not_retry():
    bars, signal, config = fixture()
    bars[1] = replace(bars[1], buyable=False, close_buyable=True)
    assert run_portfolio({signal.symbol: bars}, [signal], config).orders[0]["status"] == "filled"
    bars[1] = replace(bars[1], buyable=True, close_buyable=False)
    orders = run_portfolio({signal.symbol: bars}, [signal], config).orders
    assert len(orders) == 1
    assert orders[0]["reason"] == "not_buyable_at_close"


def test_entry_does_not_use_earlier_daily_low_as_post_fill_stop():
    bars, signal, config = fixture()
    bars[1] = replace(bars[1], low=8)
    result = run_portfolio({signal.symbol: bars[:2]}, [signal], config)
    assert [o["side"] for o in result.orders] == ["BUY"]
    assert result.open_positions[0]["pending_exit"] is None


def test_same_day_exit_wins_over_long_signal_in_either_order():
    bars, signal, config = fixture()
    exit_signal = replace(signal, side="EXIT", reason="risk")
    for signals in ([signal, exit_signal], [exit_signal, signal]):
        result = run_portfolio({signal.symbol: bars}, signals, config)
        assert not any(o["side"] == "BUY" and o["status"] == "filled" for o in result.orders)


def test_tdx_uses_raw_close_limit_with_independent_open_permission():
    raw = [
        dict(date=date(2026, 8, 27), open=10, high=10, low=10, close=10, volume=1000),
        dict(date=date(2026, 8, 28), open=10.2, high=11, low=10.1, close=11, volume=2000),
    ]
    rows = adjust_rows(raw, [], raw[0]["date"], raw[-1]["date"], "sh.601086", include_close_permission=True)
    assert rows[-1]["buyable"]
    assert not rows[-1]["close_buyable"]
    assert rows[-1]["nonflat_close_buyable"]
    assert "close_buyable" not in adjust_rows(raw, [], raw[0]["date"], raw[-1]["date"], "sh.601086")[-1]


def test_nonflat_limit_close_simulation_is_explicit_and_never_prices_above_close():
    bars, signal, config = fixture()
    bars[1] = replace(bars[1], buyable=False, close_buyable=False, nonflat_close_buyable=True)
    assert run_portfolio({signal.symbol: bars}, [signal], config).orders[0]["status"] == "cancelled"
    config = replace(config, nonflat_limit_close_fill=True)
    order = run_portfolio({signal.symbol: bars[:2]}, [signal], config).orders[0]
    assert order["status"] == "filled"
    assert order["price"] == bars[1].close
    assert order["fill_assumption"] == "nonflat_limit_close_without_queue_verification"
    assert order["applied_slippage_bps"] == 0
    for blocked in (
        replace(bars[1], high=10.4, low=10.4, open=10.4),
        replace(bars[1], volume=0),
        replace(bars[1], nonflat_close_buyable=None),
        replace(bars[1], nonflat_close_buyable=False),
    ):
        assert run_portfolio({signal.symbol: [bars[0], blocked]}, [signal], config).orders[0]["status"] == "cancelled"
