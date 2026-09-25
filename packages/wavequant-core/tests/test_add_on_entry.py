"""A later qualified signal can add to an existing holding without splitting its trade."""

from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from wavequant.application.analytics.backtest import run_portfolio
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar, Signal


def sample():
    closes = (9.5, 10.0, 11.0, 12.0, 12.5, 13.0)
    bars = [
        Bar(
            datetime(2026, 9, 1) + timedelta(days=i),
            "TEST",
            close,
            close + 0.2,
            close - 0.2,
            close,
            1_000_000 + i * 1000,
            close_buyable=True,
        )
        for i, close in enumerate(closes)
    ]
    first = Signal(bars[1].timestamp, "TEST", 1, "LONG", 10, 9, "first", bars[1].timestamp, 0, 2, "轧空", 20, 0)
    second = Signal(bars[3].timestamp, "TEST", 3, "LONG", 12, 11, "add", bars[3].timestamp, 0, 2, "轧空", 24, 0)
    config = StrategyConfig(
        initial_capital=100_000,
        max_positions=1,
        max_position_weight=0.5,
        risk_fraction=0.02,
        max_participation=1,
        minimum_commission=0,
        commission_bps_per_side=0,
        slippage_bps_per_side=0,
        a_share_taxes=False,
        entry_at_close=True,
        exit_on_target=False,
        net_reward_risk_filter=False,
        max_hold_bars=100,
    )
    return bars, first, second, config


def filled(result, side):
    return [order for order in result.orders if order["side"] == side and order["status"] == "filled"]


def test_add_on_is_opt_in_and_respects_total_weight_and_risk():
    bars, first, second, config = sample()
    old = run_portfolio({"TEST": bars[:4]}, [first, second], config)
    assert len(filled(old, "BUY")) == 1
    assert len(old.orders) == 1

    result = run_portfolio({"TEST": bars[:4]}, [first, second], replace(config, allow_add_on=True))
    buys = filled(result, "BUY")
    assert len(buys) == 2
    assert buys[1]["add_on"] is True
    assert buys[1]["position_quantity_before"] == buys[0]["quantity"]
    assert buys[1]["position_quantity_after"] == sum(order["quantity"] for order in buys)
    assert buys[1]["position_weight_after_fill"] <= config.max_position_weight + 1e-6
    assert buys[1]["position_risk_after_fill"] <= buys[1]["risk_budget"] + 1e-6
    assert result.open_positions[0]["quantity"] == buys[1]["position_quantity_after"]


@pytest.mark.parametrize(
    ("changes", "blocked_reason"),
    [
        ({"max_position_weight": 0.2}, "position_weight_below_one_lot"),
        ({"risk_fraction": 0.001}, "risk_budget_below_one_lot"),
        ({"liquidity_lookback": 1}, "liquidity_below_one_lot"),
    ],
)
def test_add_on_rejection_keeps_original_shares_and_cost(changes, blocked_reason):
    bars, first, second, config = sample()
    changes = dict(changes)
    if blocked_reason == "risk_budget_below_one_lot":
        second = replace(second, invalidation_price=9)
    if blocked_reason == "liquidity_below_one_lot":
        bars[2] = replace(bars[2], volume=10)
        changes["max_participation"] = 0.01
    config = replace(config, allow_add_on=True, **changes)
    baseline = run_portfolio({"TEST": bars[:2]}, [first], config)
    result = run_portfolio({"TEST": bars[:4]}, [first, second], config)
    assert len(filled(result, "BUY")) == 1
    assert result.orders[-1]["reason"] == blocked_reason
    assert result.open_positions[0]["quantity"] == baseline.open_positions[0]["quantity"]
    assert result.open_positions[0]["entry_price"] == baseline.open_positions[0]["entry_price"]
    assert result.open_positions[0]["entry_cost"] == baseline.open_positions[0]["entry_cost"]


def test_partial_sale_then_add_on_preserves_cycle_cost_and_realized_pnl(monkeypatch):
    bars, first, second, config = sample()
    config = replace(config, allow_add_on=True, volume_down_exit=True, commission_bps_per_side=3, minimum_commission=5)
    monkeypatch.setattr(
        "wavequant.application.analytics.backtest.observe_volume_down_exit",
        lambda history, i, state, **kwargs: (
            dict(reason="reduce", exit_fraction=0.5, execution_model="same_day_close") if i == 2 else None
        ),
    )
    exit_signal = replace(second, timestamp=bars[4].timestamp, bar_index=4, side="EXIT", reason="final")
    result = run_portfolio({"TEST": bars}, [first, second, exit_signal], config)
    buys, sells = filled(result, "BUY"), filled(result, "SELL")
    assert len(buys) == len(sells) == 2
    assert buys[1]["timestamp"] == bars[3].timestamp.isoformat()
    assert sells[0]["position_closed"] is False
    assert sells[1]["position_closed"] is True
    cost = sum(order["price"] * order["quantity"] + order["fee"] for order in buys)
    proceeds = sum(order["price"] * order["quantity"] - order["fee"] for order in sells)
    trade = result.trades[0]
    assert trade.pnl == pytest.approx(proceeds - cost)
    assert trade.net_return == pytest.approx((proceeds - cost) / cost)
    assert sells[-1]["position_entry_cost"] == pytest.approx(cost)
    assert result.metrics["final_equity"] - config.initial_capital == pytest.approx(trade.pnl)


def test_same_day_exit_sells_old_shares_and_defers_new_add_on_to_next_open():
    bars, first, second, config = sample()
    second = replace(second, timestamp=bars[2].timestamp, bar_index=2, reference_price=11, invalidation_price=10)
    config = replace(config, allow_add_on=True, volume_inverse_n_clear=True)
    exit_signal = replace(second, side="EXIT", reason="inverse_n_risk_exit")
    intraday = {
        ("TEST", 2): dict(
            signal=second,
            price=11,
            buyable=True,
            remaining_low=10.8,
            remaining_high=11.2,
            timing=dict(
                execution_model="intraday_5m_next_open",
                decision_timestamp="2026-09-03T10:00:00",
                execution_timestamp="2026-09-03T10:05:00",
            ),
        ),
    }
    result = run_portfolio({"TEST": bars[:4]}, [first, second, exit_signal], config, entry_executions=intraday)
    buys, sells = filled(result, "BUY"), filled(result, "SELL")
    assert len(buys) == len(sells) == 2
    assert sells[0]["timestamp"] == bars[2].timestamp.isoformat()
    assert sells[0]["quantity"] == buys[0]["quantity"]
    assert sells[0]["position_closed"] is False
    assert sells[1]["timestamp"] == bars[3].timestamp.isoformat()
    assert sells[1]["quantity"] == buys[1]["quantity"]
    assert sells[1]["position_closed"] is True


def test_intraday_add_on_does_not_apply_new_stop_to_earlier_low():
    bars, first, second, config = sample()
    bars[2] = replace(bars[2], low=10.2)
    second = replace(second, timestamp=bars[2].timestamp, bar_index=2, reference_price=11, invalidation_price=10.8)
    intraday = {
        ("TEST", 2): dict(
            signal=second,
            price=11.2,
            buyable=True,
            remaining_low=11,
            remaining_high=11.4,
            timing=dict(
                execution_model="intraday_5m_next_open",
                decision_timestamp="2026-09-03T10:00:00",
                execution_timestamp="2026-09-03T10:05:00",
            ),
        ),
    }
    result = run_portfolio(
        {"TEST": bars[:3]}, [first, second], replace(config, allow_add_on=True), entry_executions=intraday
    )
    assert len(filled(result, "BUY")) == 2
    assert not any(order["side"] == "SELL" for order in result.orders)
    assert result.open_positions[0]["pending_exit"] is None


def test_add_on_rejects_market_price_below_existing_stop_despite_buy_slippage():
    bars, first, second, config = sample()
    bars[2] = replace(bars[2], open=12.1, high=12.3, low=11.9, close=12.1)
    bars[3] = replace(bars[3], open=12, high=12.2, low=11.8, close=12)
    tighten = replace(
        second,
        timestamp=bars[2].timestamp,
        bar_index=2,
        reference_price=12.1,
        invalidation_price=12.003,
        target_price=12,
    )
    second = replace(second, invalidation_price=11)
    intraday = {
        ("TEST", 3): dict(
            signal=second,
            price=12,
            buyable=True,
            remaining_low=11.8,
            remaining_high=12.2,
            timing=dict(
                execution_model="intraday_5m_next_open",
                decision_timestamp="2026-09-04T10:00:00",
                execution_timestamp="2026-09-04T10:05:00",
            ),
        ),
    }
    result = run_portfolio(
        {"TEST": bars[:4]},
        [first, tighten, second],
        replace(config, allow_add_on=True, slippage_bps_per_side=5),
        entry_executions=intraday,
    )
    assert len(filled(result, "BUY")) == 1
    assert result.orders[-1]["reason"] == "existing_stop_at_or_above_execution"
    assert 12 < 12.003 < 12 * (1 + 5 / 10_000)


def test_same_day_risk_reduction_blocks_add_on_at_close(monkeypatch):
    bars, first, second, config = sample()
    monkeypatch.setattr(
        "wavequant.application.analytics.backtest.observe_volume_down_exit",
        lambda history, i, state, **kwargs: (
            dict(reason="reduce", exit_fraction=0.5, execution_model="same_day_close") if i == 3 else None
        ),
    )
    result = run_portfolio(
        {"TEST": bars[:4]}, [first, second], replace(config, allow_add_on=True, volume_down_exit=True)
    )
    buys, sells = filled(result, "BUY"), filled(result, "SELL")
    assert len(buys) == len(sells) == 1
    assert sells[0]["timestamp"] == second.timestamp.isoformat()
    assert sells[0]["quantity"] < buys[0]["quantity"]
    assert result.open_positions[0]["quantity"] == buys[0]["quantity"] - sells[0]["quantity"]


def test_pending_exit_blocks_a_later_add_on_signal():
    bars, first, second, config = sample()
    bars[3] = replace(bars[3], sellable=False)
    exit_signal = replace(second, timestamp=bars[2].timestamp, bar_index=2, side="EXIT", reason="exit")
    result = run_portfolio({"TEST": bars[:4]}, [first, exit_signal, second], replace(config, allow_add_on=True))
    assert len(filled(result, "BUY")) == 1
    assert not any(
        order["side"] == "BUY" and order["timestamp"] == second.timestamp.isoformat() for order in result.orders
    )
    assert result.open_positions[0]["pending_exit"] == "exit"


def test_two_buy_fills_on_one_day_share_prior_volume_capacity():
    bars, first, second, config = sample()
    second = replace(second, timestamp=bars[2].timestamp, bar_index=2, reference_price=11, invalidation_price=10)
    intraday = {
        ("TEST", 2): dict(
            signal=second,
            price=11,
            buyable=True,
            remaining_low=10.8,
            remaining_high=11.2,
            timing=dict(
                execution_model="intraday_5m_next_open",
                decision_timestamp="2026-09-03T10:00:00",
                execution_timestamp="2026-09-03T10:05:00",
            ),
        ),
    }
    result = run_portfolio(
        {"TEST": bars[:3]},
        [first, second],
        replace(config, allow_add_on=True, entry_at_close=False, max_entry_gap=1, max_participation=0.0001),
        entry_executions=intraday,
    )
    buys = filled(result, "BUY")
    assert len(buys) == 1
    assert buys[0]["quantity"] == 100
    assert buys[0]["timestamp"] == bars[2].timestamp.isoformat()
    assert result.orders[-1]["reason"] == "liquidity_below_one_lot"
    assert buys[0]["quantity"] <= ((bars[0].volume + bars[1].volume) / 2) * 0.0001


@pytest.mark.parametrize(
    ("max_weight", "risk_fraction", "first_quantity", "add_quantity"),
    [(0.2, 0.01, 1000, 900), (1.0, 0.02, 2000, 1900)],
)
def test_add_on_respects_weight_and_risk_caps_after_buy_fee(max_weight, risk_fraction, first_quantity, add_quantity):
    bars, first, second, config = sample()
    bars = [replace(bar, open=10, high=10.2, low=9.8, close=10) for bar in bars]
    second = replace(second, reference_price=10, invalidation_price=9.5)
    result = run_portfolio(
        {"TEST": bars[:4]},
        [first, second],
        replace(
            config,
            allow_add_on=True,
            initial_capital=100_005,
            max_position_weight=max_weight,
            risk_fraction=risk_fraction,
            minimum_commission=5,
        ),
    )
    buys = filled(result, "BUY")
    assert [buy["quantity"] for buy in buys] == [first_quantity, add_quantity]
    add_on = buys[1]
    post_fee_equity = add_on["equity_at_execution"] - add_on["fee"]
    assert add_on["position_weight_after_fill"] <= max_weight + 1e-12
    assert add_on["position_risk_after_fill"] <= post_fee_equity * risk_fraction + 1e-12


def test_huaci_body_buy_partial_sale_and_gap_add_on_share_one_open_cycle():
    from .test_wave_continuation import sample as huaci_sample
    from wavequant.domain.strategies.strategy_profiles import whole_wave_profile
    from wavequant.infrastructure.market_data.akshare_history import MinuteCoverageError
    from wavequant.infrastructure.market_data.minute import MinuteBar
    from wavequant.interfaces.research_tools.stock_backtest import single_stock_result

    bars, dates, _ = huaci_sample()
    bars = bars[: dates["2026-09-15"] + 1]
    body = bars[dates["2026-08-26"]]
    minute = [
        MinuteBar(body.timestamp.replace(hour=9, minute=35), body.open, body.high, body.low, 16.70, 2_100_000),
        MinuteBar(body.timestamp.replace(hour=9, minute=40), 16.70, 16.70, 16.60, 16.65, 800_000),
        MinuteBar(body.timestamp.replace(hour=9, minute=45), 16.65, 16.65, 16.40, body.close, body.volume - 2_900_000),
    ]

    def load(bar):
        if bar.timestamp == body.timestamp:
            return minute
        raise MinuteCoverageError(str(bar.timestamp.date()), None, None, "fixture")

    profile = whole_wave_profile({"scenarios": {"base": {"execution": StrategyConfig().to_dict()}}})
    execution = dict(
        profile["scenarios"]["base"]["execution"],
        initial_capital=100_000,
        max_position_weight=1,
        net_reward_risk_filter=False,
    )
    view = single_stock_result(bars, profile["strategy"], execution, minute_loader=load)
    buys = [order for order in view["orders"] if order["side"] == "BUY" and order["status"] == "filled"]
    body_buy = next(order for order in buys if order["timestamp"].startswith("2026-08-26"))
    gap_buy = next(order for order in buys if order["timestamp"].startswith("2026-09-15"))
    partial = next(
        order
        for order in view["orders"]
        if order["side"] == "SELL" and order["status"] == "filled" and order["timestamp"].startswith("2026-09-09")
    )
    assert partial["position_closed"] is False
    assert body_buy["trade_id"] == partial["trade_id"] == gap_buy["trade_id"]
    assert gap_buy["add_on"] is True
    assert gap_buy["position_quantity_before"] == partial["remaining_quantity"]
    assert gap_buy["position_quantity_after"] == partial["remaining_quantity"] + gap_buy["quantity"]
    assert gap_buy["position_weight_after_fill"] > gap_buy["entry_position_weight"]
    proof = next(event for event in gap_buy["decision_evidence"] if event.get("wave_entry_path"))
    assert proof["wave_confirmation_phase"] == "gap"
    assert proof["wave_gap_trigger"] == "breakout_and_volume"
    opened = view["backtest"]["open_positions"][0]
    assert opened["trade_id"] == body_buy["trade_id"]
    assert opened["quantity"] == gap_buy["position_quantity_after"]
    assert opened["entry_cost"] == pytest.approx(
        sum(o["price"] * o["quantity"] + o["fee"] for o in buys if o["trade_id"] == opened["trade_id"])
    )
