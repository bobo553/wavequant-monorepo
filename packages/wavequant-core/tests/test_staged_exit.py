from dataclasses import asdict, replace
from datetime import datetime, time, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar, Signal
from wavequant.domain.strategies.staged_exit import StagedExitState, observe_staged_exit
from wavequant.infrastructure.market_data.minute import MinuteBar
from wavequant.interfaces.research_tools.stock_backtest import single_stock_result


def sample(rebound_high=12.99):
    rows = [
        ("08", 12.66, 12.96, 12.66, 12.95),
        ("09", 12.91, 12.91, 12.43, 12.68),
        ("12", 12.82, 13.24, 12.78, 13.00),
        ("13", 13.15, 13.21, 12.78, 12.88),
        ("14", 12.88, 12.92, 12.58, 12.73),
        ("15", 12.76, 12.76, 12.42, 12.57),
        ("16", 12.56, rebound_high, 12.49, 12.68),
        ("19", 12.13, 12.14, 11.55, 11.80),
        ("20", 11.87, 11.97, 11.70, 11.89),
    ]
    return [
        Bar(datetime.fromisoformat(f"2025-05-{d}"), "sz.300154", o, h, low, c, 10_000_000) for d, o, h, low, c in rows
    ]


def run(bars, *, minute_loader=None, extra_signals=(), **overrides):
    signal = Signal(
        bars[2].timestamp,
        bars[2].symbol,
        2,
        "LONG",
        bars[2].close,
        10,
        "fixture",
        bars[2].timestamp,
        0,
        None,
        "fixture",
        20,
    )
    execution = dict(
        initial_capital=100_000,
        max_position_weight=0.8,
        risk_fraction=0.2,
        lot_size=100,
        max_participation=1,
        max_hold_bars=100,
        slippage_bps_per_side=0,
        staged_exit_enabled=True,
    )
    execution.update(overrides)
    return single_stock_result(
        bars, {}, asdict(StrategyConfig(**execution)), SimpleNamespace(signals=[signal, *extra_signals], counts={}),
        minute_loader=minute_loader,
    )


def test_ruiling_break_reduces_next_open_but_high_above_two_thirds_prevents_weak_rebound_exit():
    result = run(sample())
    fills = [o for o in result["orders"] if o["status"] == "filled"]
    assert [(o["side"], o["timestamp"][:10]) for o in fills] == [("BUY", "2025-05-13"), ("SELL", "2025-05-16")]
    buy, sell = fills
    assert sell["signal_timestamp"][:10] == "2025-05-15"
    assert sell["support_date"] == "2025-05-09"
    assert sell["decline_high_date"] == "2025-05-12"
    assert sell["rebound_threshold"] == pytest.approx(12.42 + (13.24 - 12.42) * 2 / 3)
    assert sell["quantity"] == buy["quantity"] / 2
    assert sell["position_closed"] is False
    assert result["metrics"]["open_positions"] == 1
    assert result["backtest"]["open_positions"][0]["quantity"] == sell["remaining_quantity"]


def test_partial_exit_does_not_count_as_completed_trade_and_uses_original_cost():
    result = run(sample()[:7])
    buy, sell = [o for o in result["orders"] if o["status"] == "filled"]
    original_cost = buy["price"] * buy["quantity"] + buy["fee"]
    assert result["trades"] == []
    assert result["metrics"]["trades"] == 0
    assert result["metrics"]["win_rate"] is None
    assert result["metrics"]["closed_pnl"] == 0
    assert result["backtest"]["diagnostics"]["closed_trades"] == 0
    assert sell["position_entry_cost"] == pytest.approx(original_cost)
    assert sell["position_net_return"] == pytest.approx(sell["fill_pnl"] / original_cost)
    opened = result["backtest"]["open_positions"][0]
    assert opened["realized_pnl"] == sell["position_pnl"]
    assert opened["total_pnl"] == pytest.approx(opened["realized_pnl"] + opened["unrealized_pnl"])
    assert opened["net_return"] == pytest.approx(opened["total_pnl"] / original_cost)
    assert result["metrics"]["final_equity"] == pytest.approx(100_000 + opened["total_pnl"])


@pytest.mark.parametrize("final_price", [11.87, 10.5])
def test_profitable_reduction_and_losing_final_exit_are_one_weighted_trade(final_price):
    bars = sample()
    bars[6] = replace(bars[6], open=15, high=15)
    bars[8] = replace(bars[8], open=final_price, low=min(final_price, bars[8].low))
    exit_signal = Signal(bars[7].timestamp, bars[7].symbol, 7, "EXIT", bars[7].close,
                         10, "fixture_exit", bars[7].timestamp, 0, None, "fixture")
    result = run(bars, extra_signals=[exit_signal])
    buy, first, last = [o for o in result["orders"] if o["status"] == "filled"]
    cost = buy["price"] * buy["quantity"] + buy["fee"]
    proceeds = sum(o["quantity"] * o["price"] - o["fee"] for o in (first, last))
    assert first["fill_pnl"] > 0
    assert last["fill_pnl"] < 0
    assert len(result["trades"]) == result["metrics"]["trades"] == 1
    trade = result["trades"][0]
    assert trade["quantity"] == buy["quantity"]
    assert trade["pnl"] == pytest.approx(proceeds - cost)
    assert trade["net_return"] == pytest.approx((proceeds - cost) / cost)
    assert last["position_net_return"] == pytest.approx(trade["net_return"])
    assert last["position_pnl"] == pytest.approx(first["fill_pnl"] + last["fill_pnl"])
    assert result["metrics"]["win_rate"] == (1 if proceeds > cost else 0)
    assert result["metrics"]["average_net_return"] == trade["net_return"]
    assert trade["fees"] == pytest.approx(sum(o["fee"] for o in (buy, first, last)))
    assert trade["exit_time"] == last["timestamp"]
    prefix = run(bars[:7])
    assert prefix["orders"] == [o for o in result["orders"] if o["timestamp"] <= bars[6].timestamp.isoformat()]


def test_reentry_starts_a_new_cost_basis_and_never_merges_separate_holdings():
    first = sample(12.9)
    second = [replace(bar, timestamp=bar.timestamp + timedelta(days=20)) for bar in first]
    bars = first + second
    entry = Signal(bars[11].timestamp, bars[11].symbol, 11, "LONG", bars[11].close,
                   10, "second_entry", bars[11].timestamp, 0, None, "fixture", 20)
    result = run(bars, extra_signals=[entry], slippage_bps_per_side=5, minimum_commission=10)
    assert len(result["trades"]) == 2
    buys = [o for o in result["orders"] if o["side"] == "BUY" and o["status"] == "filled"]
    for buy, trade in zip(buys, result["trades"], strict=True):
        sells = [o for o in result["orders"] if o["side"] == "SELL" and o["status"] == "filled"
                 and o["trade_id"] == buy["trade_id"]]
        cost = buy["quantity"] * buy["price"] + buy["fee"]
        pnl = sum(o["quantity"] * o["price"] - o["fee"] for o in sells) - cost
        assert len(sells) == 2
        assert trade["entry_time"] == buy["timestamp"]
        assert trade["net_return"] == pytest.approx(pnl / cost)
        assert trade["pnl"] == pytest.approx(pnl)
        assert sells[-1]["position_entry_cost"] == pytest.approx(cost)
    assert result["metrics"]["final_equity"] == pytest.approx(100_000 + result["metrics"]["closed_pnl"])


@pytest.mark.parametrize("exit_on_target", [True, False])
def test_failed_rebound_clears_remainder_and_conserves_quantity_fees_pnl_and_trade_identity(exit_on_target):
    result = run(sample(12.90), exit_on_target=exit_on_target)
    fills = [o for o in result["orders"] if o["status"] == "filled"]
    assert [o["timestamp"][:10] for o in fills] == ["2025-05-13", "2025-05-16", "2025-05-20"]
    assert fills[-1]["signal_timestamp"][:10] == "2025-05-19"
    assert fills[-1]["reason"] == "weak_rebound_two_thirds_exit"
    assert fills[-1]["position_closed"] is True
    assert len({o["trade_id"] for o in fills}) == 1
    assert fills[0]["quantity"] == sum(o["quantity"] for o in fills[1:])
    assert result["metrics"]["open_positions"] == 0
    assert result["metrics"]["final_equity"] == pytest.approx(100_000 + sum(t["pnl"] for t in result["trades"]))
    assert sum(t["fees"] for t in result["trades"]) == pytest.approx(sum(o["fee"] for o in fills))


def test_future_rebound_does_not_change_initial_reduction_and_legacy_default_stays_disabled():
    bars = sample()
    short = run(bars[:7])
    long = run(bars)
    assert short["orders"] == [o for o in long["orders"] if o["timestamp"] <= bars[6].timestamp.isoformat()]
    assert len(run(bars, staged_exit_enabled=False)["orders"]) == 1
    assert len(run(bars[:6])["orders"]) == 1


def test_both_low_and_close_must_break_and_reduction_only_occurs_once():
    bars = sample()
    bars[5] = replace(bars[5], close=12.68)
    state = StagedExitState()
    assert observe_staged_exit(bars, 5, state, 0.5) is None
    bars = sample()
    state = StagedExitState()
    assert observe_staged_exit(bars, 5, state, 0.5)["exit_fraction"] == 0.5
    assert all(observe_staged_exit(bars, i, state, 0.5) is None for i in range(6, len(bars)))


def test_equal_two_thirds_is_not_a_breakout_and_outside_bar_breach_prevents_false_clear():
    bars = sample(12.90)
    bars[2] = replace(bars[2], high=13.2)
    bars[5] = replace(bars[5], low=12.3)
    state = StagedExitState()
    observe_staged_exit(bars, 5, state, 0.5)
    assert observe_staged_exit(bars, 6, state, 0.5) is None
    assert not state.recovered
    assert observe_staged_exit(bars, 7, state, 0.5)["exit_fraction"] == 1
    state = StagedExitState()
    observe_staged_exit(bars, 5, state, 0.5)
    observe_staged_exit(bars, 6, state, 0.5)
    bars[7] = replace(bars[7], high=13.1)
    assert observe_staged_exit(bars, 7, state, 0.5) is None
    assert state.recovered


def test_hard_stop_overrides_reduction_and_unsplittable_lot_exits_fully():
    bars = sample()
    bars[5] = replace(bars[5], low=9.9)
    sell = next(o for o in run(bars)["orders"] if o["side"] == "SELL")
    assert sell["reason"] == "structural_stop_observed"
    assert sell["position_closed"] is True
    result = run(sample(), initial_capital=2_000)
    fills = [o for o in result["orders"] if o["status"] == "filled"]
    assert fills[0]["quantity"] == 100
    assert fills[1]["quantity"] == 100
    assert fills[1]["position_closed"] is True


def test_unsellable_reduction_can_be_upgraded_to_full_exit_before_execution():
    bars = sample(12.9)
    bars[6] = replace(bars[6], sellable=False)
    bars[7] = replace(bars[7], sellable=False)
    result = run(bars)
    fills = [o for o in result["orders"] if o["status"] == "filled"]
    assert fills[-1]["timestamp"][:10] == "2025-05-20"
    assert fills[-1]["quantity"] == fills[0]["quantity"]
    assert fills[-1]["reason"] == "weak_rebound_two_thirds_exit"


@pytest.mark.parametrize("exit_on_target", [True, False])
def test_tiered_double_break_executes_65_percent_on_may_15_and_preserves_prefix(exit_on_target):
    bars = sample()
    result = run(bars, staged_exit_same_day=True, exit_on_target=exit_on_target)
    fills = [o for o in result["orders"] if o["status"] == "filled"]
    buy, sell = fills
    assert sell["timestamp"][:10] == sell["signal_timestamp"][:10] == "2025-05-15"
    assert sell["execution_model"] == "same_day_close"
    assert sell["price"] == bars[5].close
    assert sell["quantity"] == (buy["quantity"] * 65 // 10000) * 100
    assert sell["exit_target_fraction"] == 0.65
    assert sell["closed_position_fraction"] == sell["quantity"] / buy["quantity"]
    assert sell["remaining_quantity"] + sell["quantity"] == buy["quantity"]
    assert run(bars[:6], staged_exit_same_day=True, exit_on_target=exit_on_target)["orders"] == result["orders"]


def test_tiered_low_only_then_close_break_sells_only_increment_to_65_percent():
    bars = sample(12.9)
    bars[5] = replace(bars[5], close=12.70)
    bars[6] = replace(bars[6], low=12.40, close=12.60)
    result = run(bars[:7], staged_exit_same_day=True)
    buy, first, second = [o for o in result["orders"] if o["status"] == "filled"]
    assert [first["timestamp"][:10], second["timestamp"][:10]] == ["2025-05-15", "2025-05-16"]
    assert first["reason"] == "support_low_break_reduce"
    assert first["exit_target_fraction"] == 0.35
    assert first["quantity"] == (buy["quantity"] * 35 // 10000) * 100
    assert first["quantity"] + second["quantity"] == (buy["quantity"] * 65 // 10000) * 100
    assert len({o["trade_id"] for o in [buy, first, second]}) == 1
    assert second["closed_position_fraction"] == second["quantity"] / first["remaining_quantity"]
    assert result["metrics"]["final_equity"] == pytest.approx(
        100_000 + result["metrics"]["realized_pnl"] + result["backtest"]["open_positions"][0]["unrealized_pnl"]
    )


def test_same_day_reduction_defers_unsellable_and_does_not_force_one_lot_liquidation():
    bars = sample()
    bars[5] = replace(bars[5], sellable=False)
    result = run(bars, staged_exit_same_day=True)
    sells = [o for o in result["orders"] if o["side"] == "SELL"]
    assert sells[0]["status"] == "deferred" and sells[0]["reason"] == "not_sellable"
    assert sells[1]["timestamp"][:10] == "2025-05-16"
    assert sells[1]["price"] == bars[6].close
    small = run(sample(), staged_exit_same_day=True, initial_capital=2_000)
    assert all(o["side"] == "BUY" or o["status"] != "filled" for o in small["orders"])


def test_five_minute_closing_window_sells_35_then_only_30_more_at_next_interval_open():
    bars = sample()[:6]
    clocks = ([time(9, minute) for minute in range(35, 60, 5)]
              + [time(10, minute) for minute in range(0, 60, 5)]
              + [time(11, minute) for minute in range(0, 35, 5)]
              + [time(13, minute) for minute in range(5, 60, 5)]
              + [time(14, minute) for minute in range(0, 60, 5)] + [time(15)])
    minute = []
    for clock in clocks:
        opening = 12.69 if clock == time(14, 35) else 12.56 if clock == time(14, 40) else 12.70
        close = 12.57 if clock >= time(14, 35) else 12.70
        minute.append(MinuteBar(
            datetime.combine(bars[5].timestamp.date(), clock, ZoneInfo("Asia/Shanghai")),
            opening, max(opening, close, 12.76 if clock == time(9, 35) else 0),
            12.42 if clock == time(9, 35) else min(opening, close), close, 100_000,
        ))
    result = run(bars, staged_exit_intraday=True, missing_minute_daily_fallback=True, minute_loader=lambda bar: minute)
    assert result["backtest"]["minute_fallbacks"] == []
    buy, first, second = [order for order in result["orders"] if order["status"] == "filled"]
    assert [first["decision_timestamp"][11:16], second["decision_timestamp"][11:16]] == ["14:30", "14:35"]
    assert [first["timestamp"][11:16], second["timestamp"][11:16]] == ["14:30", "14:35"]
    assert [first["price"], second["price"]] == [12.69, 12.56]
    assert [first["exit_target_fraction"], second["exit_target_fraction"]] == [0.35, 0.65]
    assert first["quantity"] + second["quantity"] == (buy["quantity"] * 65 // 10000) * 100
    assert second["remaining_quantity"] + first["quantity"] + second["quantity"] == buy["quantity"]
    # Current minute highs are diagnostic only; preserving a lower reported high
    # cannot change low/close decisions, next-open prices, quantities or fees.
    lower_highs = [replace(bar, high=max(bar.open, bar.close)) for bar in minute]
    lower_result = run(bars, staged_exit_intraday=True, minute_loader=lambda bar: lower_highs)
    assert lower_result["trades"] == result["trades"]
    assert [{key: value for key, value in order.items() if key != "observed_high"}
            for order in lower_result["orders"]] == [
        {key: value for key, value in order.items() if key != "observed_high"} for order in result["orders"]
    ]


def test_intraday_mode_requires_minute_history():
    with pytest.raises(ValueError, match="分钟行情"):
        run(sample(), staged_exit_intraday=True)


@pytest.mark.parametrize("close,target", [(12.70, 0.35), (12.57, 0.65)])
def test_missing_minutes_fall_back_to_daily_close_only_when_enabled(close, target):
    from wavequant.infrastructure.market_data.akshare_history import MinuteCoverageError

    bars = sample()[:6]
    bars[-1] = replace(bars[-1], close=close)

    def missing(bar):
        raise MinuteCoverageError(bar.timestamp.date().isoformat(), None, None)

    result = run(bars, staged_exit_intraday=True, missing_minute_daily_fallback=True, minute_loader=missing)
    buy, sell = [item for item in result["orders"] if item["status"] == "filled"]
    assert sell["timestamp"][:10] == "2025-05-15"
    assert sell["execution_model"] == "same_day_close"
    assert sell["price"] == close
    assert sell["exit_target_fraction"] == target
    assert sell["quantity"] == (buy["quantity"] * target // 100) * 100
    assert sell["minute_fallback"]["reason"] == "minute_history_missing"
    assert result["backtest"]["minute_fallbacks"][0]["date"] == "2025-05-15"
    with pytest.raises(MinuteCoverageError):
        run(bars, staged_exit_intraday=True, minute_loader=missing)


def test_corrupt_minute_data_is_not_treated_as_missing():
    def corrupt(bar):
        raise ValueError("OHLC mismatch")

    with pytest.raises(ValueError, match="OHLC mismatch"):
        run(sample(), staged_exit_intraday=True, missing_minute_daily_fallback=True, minute_loader=corrupt)


def inverse_signal(bars, index, reason="inverse_n_risk_exit"):
    bar = bars[index]
    return Signal(bar.timestamp, bar.symbol, index, "EXIT", bar.close, bar.high,
                  reason, bar.timestamp, 0, None, "risk_exit")


@pytest.mark.parametrize("first_close", [12.70, 12.57])
def test_inverse_n_after_reduction_tops_up_to_ninety_and_does_not_repeat(first_close):
    bars = sample()
    bars[5] = replace(bars[5], close=first_close)
    signals = [inverse_signal(bars, 6), inverse_signal(bars, 7)]
    options = dict(staged_exit_same_day=True, inverse_n_after_reduction=True, exit_on_target=False)
    result = run(bars, extra_signals=signals, **options)
    buy, first, final = [row for row in result["orders"] if row["status"] == "filled"]
    assert final["reason"] == "inverse_n_after_reduction_90"
    assert final["timestamp"][:10] == "2025-05-19"
    assert final["signal_timestamp"][:10] == "2025-05-16"
    assert final["exit_target_fraction"] == 0.9
    target_quantity = int(buy["quantity"] * 0.9 // 100) * 100
    assert first["quantity"] + final["quantity"] == target_quantity
    assert final["remaining_quantity"] == buy["quantity"] - target_quantity
    assert final["remaining_quantity"] > 0
    prefix = run(bars[:8], extra_signals=signals, **options)
    assert prefix["orders"] == result["orders"]


@pytest.mark.parametrize("reason", ["last_rise_low_close_broken", "inverse_n_risk_exit|negative_turn_risk_exit"])
def test_other_exit_reasons_still_clear_the_remainder(reason):
    bars = sample()
    result = run(bars, staged_exit_same_day=True, inverse_n_after_reduction=True,
                 extra_signals=[inverse_signal(bars, 6, reason)])
    assert result["orders"][-1]["remaining_quantity"] == 0
    assert result["orders"][-1]["reason"] == reason


def test_inverse_n_without_prior_reduction_retains_full_exit():
    bars = sample()[:5]
    result = run(bars, inverse_n_after_reduction=True, extra_signals=[inverse_signal(bars, 3)])
    assert result["orders"][-1]["remaining_quantity"] == 0
    assert result["orders"][-1]["reason"] == "inverse_n_risk_exit"


def test_hard_stop_has_priority_over_inverse_n_ninety_percent():
    bars = sample()
    bars[6] = replace(bars[6], low=9.9)
    result = run(bars, staged_exit_same_day=True, inverse_n_after_reduction=True,
                 extra_signals=[inverse_signal(bars, 6)])
    assert result["orders"][-1]["remaining_quantity"] == 0
    assert result["orders"][-1]["reason"] == "structural_stop_observed"


def test_ruiling_may_19_inverse_n_is_generated_at_confirmation_and_sold_next_open():
    from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals

    bars = sample()
    strategy = SystemStrategy(pivot_mode="lecture_causal", entry_policy="hierarchical_two_buy_points",
                              buy_point_definition="whole_flip_wave_v3", volume_filter=False)
    generated = generate_system_signals(bars, strategy)
    inverse = [signal for signal in generated.signals if "inverse_n_risk_exit" in signal.reason.split("|")]
    assert any(signal.timestamp.date().isoformat() == "2025-05-19" for signal in inverse)
    prefix = generate_system_signals(bars[:8], strategy)
    assert [s for s in generated.signals if s.bar_index <= 7] == prefix.signals
    assert not any("inverse_n_risk_exit" in s.reason.split("|")
                   for s in generate_system_signals(bars[:7], strategy).signals)
    # The short fixture also breaks another structural level. Isolate the N's
    # sizing here; combined-risk full liquidation has its own regression.
    result = run(bars, staged_exit_same_day=True, inverse_n_after_reduction=True,
                 extra_signals=[replace(signal, reason="inverse_n_risk_exit") for signal in inverse])
    last = result["orders"][-1]
    assert last["reason"] == "inverse_n_after_reduction_90"
    assert last["timestamp"][:10] == "2025-05-20"
    assert last["signal_timestamp"][:10] == "2025-05-19"
    assert last["exit_target_fraction"] == 0.9


def test_confirmed_pullback_does_not_require_rebound_above_support_candle_high():
    bars = [Bar(datetime(2026, 5, 20 + index), "TEST", *values, 100000)
            for index, values in enumerate([(13, 14, 12, 13), (12, 13, 10, 12),
                                            (12, 12.5, 10.5, 12.3), (12, 12.4, 9.9, 11.5)])]
    assert observe_staged_exit(bars, 3, StagedExitState(), .65) is None
    decision = observe_staged_exit(bars, 3, StagedExitState(), .65, tiered=True)
    assert decision["exit_target_fraction"] == .65
    assert decision["support_date"] == "2026-05-21"


def test_inverse_close_reduces_without_prior_support_break_and_never_sells_lower_target_again():
    bars = sample()[:7]
    result = run(bars, extra_signals=[inverse_signal(bars, 4), inverse_signal(bars, 6)],
                 inverse_n_close_reduce=True, staged_exit_same_day=True, exit_on_target=False)
    buy, sell = [o for o in result["orders"] if o["status"] == "filled"]
    assert sell["timestamp"] == sell["signal_timestamp"] == bars[4].timestamp.isoformat()
    assert sell["price"] == bars[4].close
    assert sell["execution_model"] == "same_day_close"
    assert sell["reason"] == "inverse_n_close_reduce_90"
    assert sell["quantity"] == int(buy["quantity"] * 0.9 // 100) * 100
    assert result["backtest"]["open_positions"][0]["pending_exit"] is None


def test_inverse_close_t_plus_one_retries_at_next_close():
    bars = sample()[:5]
    result = run(bars, extra_signals=[inverse_signal(bars, 3)],
                 inverse_n_close_reduce=True, exit_on_target=False)
    buy, sell = [o for o in result["orders"] if o["status"] == "filled"]
    assert any(o["reason"] == "T+1" for o in result["orders"])
    assert sell["timestamp"] == bars[4].timestamp.isoformat()
    assert sell["signal_timestamp"] == bars[3].timestamp.isoformat()
    assert sell["price"] == bars[4].close


def inverse_resistance_sample():
    rows = [("15",14.93,15.79,14.66,15.41),("18",15.36,15.40,14.66,14.87),
            ("19",14.98,15.00,14.41,14.63),("20",14.57,14.75,14.26,14.31),
            ("21",14.33,15.08,13.81,14.00),("22",14.05,14.35,13.83,14.23),
            ("25",14.27,15.04,14.14,14.26),("26",14.36,14.37,13.29,13.56),
            ("27",13.42,13.70,12.96,13.10)]
    return [Bar(datetime.fromisoformat("2026-05-"+day), "sz.300154", *values, 10_000_000)
            for day,*values in rows]


def test_inverse_resistance_failure_clears_on_may26_and_prefix_fills_match():
    bars = inverse_resistance_sample()
    options = dict(extra_signals=[inverse_signal(bars,4)], inverse_n_close_reduce=True, volume_down_exit=True,
                   staged_exit_same_day=True, exit_on_target=False)
    result = run(bars, **options)
    buy, reduction, clear = [o for o in result["orders"] if o["status"] == "filled"]
    assert reduction["timestamp"][:10] == "2026-05-21"
    assert clear["timestamp"] == clear["signal_timestamp"] == bars[7].timestamp.isoformat()
    assert clear["reason"] == "inverse_n_bull_resistance_failed_exit"
    assert clear["price"] == bars[7].close
    assert clear["remaining_quantity"] == 0
    assert clear["quantity"] == reduction["remaining_quantity"]
    assert clear["resistance_date"] == "2026-05-25"
    assert clear["resistance_virtual_low"] == 14.14
    assert result["orders"] == run(bars[:8], **options)["orders"]


@pytest.mark.parametrize("change", ["equal", "wick_only", "invalidated", "no_reduction"])
def test_inverse_resistance_does_not_clear_without_a_valid_close_failure(change):
    from wavequant.domain.strategies.staged_exit import observe_inverse_resistance_exit
    bars = inverse_resistance_sample()
    state = StagedExitState(inverse_index=4 if change != "no_reduction" else None)
    if change in ("equal", "wick_only"):
        bars[7] = replace(bars[7], close=14.14 if change == "equal" else 14.2)
    if change == "invalidated":
        bars[6] = replace(bars[6], high=15.09)
    assert observe_inverse_resistance_exit(bars,5,state) is None
    assert observe_inverse_resistance_exit(bars,6,state) is None
    assert observe_inverse_resistance_exit(bars,7,state) is None


def volume_down_sample():
    rows = [("13",7.11,7.39,7.07,7.35,39286133),("14",7.13,7.42,7.12,7.29,27600316),
            ("15",7.32,7.78,7.24,7.55,47054489),("16",7.46,7.87,7.13,7.13,46943322),
            ("19",7.12,7.84,7.09,7.84,76817115),("20",7.80,7.95,7.61,7.84,74155416),
            ("21",7.67,7.81,7.48,7.75,39381140),("22",7.68,7.76,7.48,7.59,43005792),
            ("23",7.48,7.55,7.11,7.13,37887874),("26",7.03,7.39,7.01,7.07,36559989)]
    return [Bar(datetime.fromisoformat("2022-09-"+d), "sz.000978", *values) for d,*values in rows]


def test_volume_down_freezes_confirmed_support_and_clears_on_low_break():
    from wavequant.domain.strategies.staged_exit import observe_volume_down_exit
    bars = volume_down_sample()
    bars[8] = replace(bars[8], open=7.12, close=7.13)
    state = StagedExitState()
    for i in range(3,7):
        assert observe_volume_down_exit(bars,i,state) is None
    reduction = observe_volume_down_exit(bars,7,state)
    assert reduction["exit_target_fraction"] == .7
    assert reduction["execution_model"] == "same_day_close"
    assert reduction["volume_support_date"] == "2022-09-19"
    assert reduction["trigger_volume"] > reduction["previous_volume"]
    assert observe_volume_down_exit(bars,8,state) is None
    clear = observe_volume_down_exit(bars,9,state)
    assert clear["exit_fraction"] == 1
    assert clear["execution_model"] == "same_day_close"
    assert clear["volume_support_low"] == 7.09


@pytest.mark.parametrize("change", ["equal_volume", "less_volume", "equal_close", "bullish"])
def test_volume_down_strict_boundaries(change):
    from wavequant.domain.strategies.staged_exit import observe_volume_down_exit
    bars=volume_down_sample()
    if change=="equal_volume": bars[7]=replace(bars[7],volume=bars[6].volume)
    if change=="less_volume": bars[7]=replace(bars[7],volume=bars[6].volume-1)
    if change=="equal_close": bars[7]=replace(bars[7],open=7.76,close=bars[6].close)
    if change=="bullish": bars[7]=replace(bars[7],open=7.50)
    assert observe_volume_down_exit(bars,7,StagedExitState()) is None


def test_volume_down_execution_same_close_seventy_then_same_day_full_exit():
    from wavequant.application.analytics.backtest import run_portfolio
    bars=volume_down_sample()
    bars[8] = replace(bars[8], open=7.12, close=7.13)
    signal=Signal(bars[0].timestamp,bars[0].symbol,0,"LONG",bars[0].close,6,"fixture",
                  bars[0].timestamp,0,None,"fixture",20)
    config=StrategyConfig(initial_capital=100000,risk_fraction=.2,max_position_weight=.8,
                          max_participation=1,slippage_bps_per_side=0,exit_on_target=False,
                          volume_down_exit=True,max_hold_bars=100)
    result=run_portfolio({bars[0].symbol:bars},[signal],config)
    buy,reduction,clear=[o for o in result.orders if o['status']=='filled']
    assert reduction['timestamp'][:10]=='2022-09-22'
    assert reduction['signal_timestamp'][:10]=='2022-09-22'
    assert reduction['price']==bars[7].close
    assert reduction['quantity']==int(buy['quantity']*.7//100)*100
    assert clear['timestamp'][:10]=='2022-09-26'
    assert clear['price']==bars[9].close
    assert clear['remaining_quantity']==0
    prefix=run_portfolio({bars[0].symbol:bars[:8]},[signal],config)
    assert prefix.orders==[o for o in result.orders if o['timestamp']<=bars[7].timestamp.isoformat()]


def test_volume_down_does_not_repeat_reduction_or_clear_at_equal_support():
    from wavequant.domain.strategies.staged_exit import observe_volume_down_exit
    bars=volume_down_sample()
    bars[8] = replace(bars[8], open=7.12, close=7.13)
    state=StagedExitState()
    observe_volume_down_exit(bars,7,state)
    bars[8]=replace(bars[8],volume=50_000_000)
    assert observe_volume_down_exit(bars,8,state) is None
    bars[9]=replace(bars[9],open=7.1,low=7.09,close=7.1)
    assert observe_volume_down_exit(bars,9,state) is None
    assert state.volume_support_index==4


def test_guofang_volume_down_reduces_at_close_and_next_low_break_clears():
    from wavequant.application.analytics.backtest import run_portfolio
    from wavequant.domain.strategies.staged_exit import observe_volume_down_exit

    rows = [
        ("2020-04-28", 5.11, 5.12, 4.67, 4.92, 7_600_677),
        ("2020-05-07", 4.86, 4.99, 4.85, 4.97, 5_063_675),
        ("2020-05-11", 5.05, 5.31, 5.05, 5.26, 14_013_970),
        ("2020-05-12", 5.27, 5.33, 5.20, 5.31, 6_451_974),
        ("2020-05-13", 5.29, 5.34, 5.26, 5.27, 9_716_400),
        ("2020-05-14", 5.30, 5.30, 5.04, 5.07, 7_399_800),
        ("2020-05-20", 4.93, 4.94, 4.86, 4.88, 4_618_200),
    ]
    bars = [Bar(datetime.fromisoformat(day), "sh.601086", *prices) for day, *prices in rows]
    state = StagedExitState()
    reduction = observe_volume_down_exit(bars, 4, state)
    assert reduction["exit_target_fraction"] == .7
    assert reduction["execution_model"] == "same_day_close"
    assert reduction["volume_trigger_date"] == "2020-05-13"
    clear = observe_volume_down_exit(bars, 5, state)
    assert clear["reason"] == "volume_down_next_followthrough_clear"
    assert clear["execution_model"] == "same_day_close"
    assert clear["warning_low"] == bars[4].low
    assert bars[5].open > bars[4].close
    assert bars[5].close < bars[4].low

    signal = Signal(bars[1].timestamp, bars[1].symbol, 1, "LONG", bars[1].close, 4.67,
                    "fixture", bars[1].timestamp, 0, None, "fixture", 6)
    config = StrategyConfig(initial_capital=100_000, risk_fraction=.2, max_position_weight=.8,
                            max_participation=1, slippage_bps_per_side=0, exit_on_target=False,
                            volume_down_exit=True, max_hold_bars=100)
    result = run_portfolio({bars[0].symbol: bars}, [signal], config)
    buy, sell, final = [order for order in result.orders if order["status"] == "filled"]
    assert buy["timestamp"] == bars[2].timestamp.isoformat()
    assert sell["timestamp"] == sell["signal_timestamp"] == bars[4].timestamp.isoformat()
    assert sell["quantity"] == int(buy["quantity"] * .7 // 100) * 100
    assert final["timestamp"] == bars[5].timestamp.isoformat()
    assert final["reason"] == "volume_down_next_followthrough_clear"
    assert final["remaining_quantity"] == 0
    assert all(order["timestamp"] != bars[6].timestamp.isoformat() for order in result.orders)
    prefix = run_portfolio({bars[0].symbol: bars[:5]}, [signal], config)
    assert prefix.orders == [order for order in result.orders if order["timestamp"] <= bars[4].timestamp.isoformat()]


@pytest.mark.parametrize("open_price, close_price", [(7.70, 7.48), (7.12, 7.13), (7.12, 7.12)])
def test_volume_down_next_session_does_not_clear_without_gap_fade_or_bearish_low_break(open_price, close_price):
    from wavequant.domain.strategies.staged_exit import observe_volume_down_exit

    bars = volume_down_sample()
    bars[8] = replace(bars[8], open=open_price, high=7.76, close=close_price)
    state = StagedExitState()
    assert observe_volume_down_exit(bars, 7, state)["exit_target_fraction"] == .7
    assert observe_volume_down_exit(bars, 8, state) is None


def test_volume_down_next_session_higher_open_bearish_low_break_clears():
    from wavequant.domain.strategies.staged_exit import observe_volume_down_exit

    bars = volume_down_sample()
    bars[8] = replace(bars[8], open=7.70, high=7.76, close=7.13)
    state = StagedExitState()
    assert observe_volume_down_exit(bars, 7, state)["exit_target_fraction"] == .7
    clear = observe_volume_down_exit(bars, 8, state)
    assert clear["reason"] == "volume_down_next_followthrough_clear"
    assert clear["warning_low"] == bars[7].low


def test_volume_down_gap_fade_clear_is_limited_to_next_session():
    from wavequant.domain.strategies.staged_exit import observe_volume_down_exit

    bars = volume_down_sample()
    bars[8] = replace(bars[8], open=7.12, close=7.13)
    bars[9] = replace(bars[9], open=7.12, high=7.39, low=7.11, close=7.11)
    state = StagedExitState()
    observe_volume_down_exit(bars, 7, state)
    assert observe_volume_down_exit(bars, 8, state) is None
    assert observe_volume_down_exit(bars, 9, state) is None


def test_guofang_cached_may14_bearish_low_break_clears_despite_higher_open():
    from wavequant.domain.strategies.staged_exit import observe_volume_down_exit

    bars = [
        Bar(datetime(2020, 5, 12), "sh.601086", 5.266820778126105, 5.329896476067735,
            5.203745080184475, 5.3088712434205245, 6_451_974),
        Bar(datetime(2020, 5, 13), "sh.601086", 5.287846010773315, 5.34040909239134,
            5.2563081618025, 5.266820778126105, 9_716_400),
        Bar(datetime(2020, 5, 14), "sh.601086", 5.29835862709692, 5.29835862709692,
            5.035543219006795, 5.06708106797761, 7_399_800),
    ]
    state = StagedExitState()
    assert observe_volume_down_exit(bars, 1, state)["exit_target_fraction"] == .7
    assert bars[2].open > bars[1].close
    clear = observe_volume_down_exit(bars, 2, state)
    assert clear["reason"] == "volume_down_next_followthrough_clear"
    assert clear["warning_low"] == bars[1].low


def small_inside_n_sample():
    bars=[Bar(datetime(2021,12,1)+timedelta(days=i),'TEST',5.0,5.3,4.9,5.15,1000) for i in range(13)]
    bars[10]=replace(bars[10],open=5.08,low=5.08,high=5.28,close=5.19,volume=2000)
    bars[11]=replace(bars[11],open=5.22,low=5.20,high=5.27,close=5.26,volume=1000)
    bars[12]=replace(bars[12],open=5.23,low=5.19,high=5.26,close=5.19,volume=1100)
    return bars


@pytest.mark.parametrize('bullish',[False,True])
def test_small_candle_inside_positive_n_reduces_thirty_and_can_escalate(bullish):
    from wavequant.domain.strategies.staged_exit import observe_volume_down_exit
    bars=small_inside_n_sample()
    if bullish: bars[12]=replace(bars[12],open=5.18,low=5.17)
    state=StagedExitState()
    decision=observe_volume_down_exit(bars,12,state,positive_n_index=10)
    assert decision['exit_target_fraction']==.3
    assert decision['positive_n_date']==bars[10].timestamp.date().isoformat()
    bars.append(replace(bars[12],timestamp=bars[12].timestamp+timedelta(days=1),open=5.25,close=5.18,low=5.17,volume=1200))
    assert observe_volume_down_exit(bars,13,state,positive_n_index=10)['exit_target_fraction']==.7
    assert state.volume_support_index is None


@pytest.mark.parametrize('change',['outside_high','outside_low','large','no_n','future_n','large_relative'])
def test_thirty_exception_requires_all_small_body_and_known_n_conditions(change):
    from wavequant.domain.strategies.staged_exit import observe_volume_down_exit
    bars=small_inside_n_sample(); anchor=10
    if change=='outside_high': bars[12]=replace(bars[12],high=5.29)
    if change=='outside_low': bars[12]=replace(bars[12],low=5.07)
    if change=='large': bars[12]=replace(bars[12],close=5.17,low=5.17)
    if change=='no_n': anchor=None
    if change=='future_n': anchor=12
    if change=='large_relative':
        bars[:10]=[replace(b,close=b.open) for b in bars[:10]]
    assert observe_volume_down_exit(bars,12,StagedExitState(),positive_n_index=anchor)['exit_target_fraction']==.7


def test_small_body_one_percent_boundary_is_inclusive():
    from wavequant.domain.strategies.staged_exit import observe_volume_down_exit
    bars=small_inside_n_sample()
    bars[12]=replace(bars[12],close=5.1777,low=5.17)
    assert observe_volume_down_exit(bars,12,StagedExitState(),positive_n_index=10)['exit_target_fraction']==.3


def test_thirty_reduction_context_respects_n_availability_and_invalidation():
    from wavequant.application.analytics.backtest import run_portfolio
    bars=small_inside_n_sample()
    bars.append(replace(bars[-1],timestamp=bars[-1].timestamp+timedelta(days=1),volume=1000))
    signal=Signal(bars[9].timestamp,'TEST',9,'LONG',5.15,1,'fixture',bars[9].timestamp,0,None,'fixture',10)
    config=StrategyConfig(initial_capital=100000,risk_fraction=.2,max_position_weight=.8,
        max_participation=1,max_hold_bars=100,exit_on_target=False,volume_down_exit=True,small_n_reduction=True)
    bars=[replace(b,volume=b.volume*100000) for b in bars]
    def reduction(known, series=bars):
        r=run_portfolio({'TEST':series},[signal],config,positive_n_bars={'TEST':known})
        return next(o for o in r.orders if o['side']=='SELL' and o['status']=='filled')
    assert reduction({10:10})['exit_target_fraction']==.3
    assert reduction({13:10})['exit_target_fraction']==.7
    broken=list(bars);broken[11]=replace(broken[11],low=5.0)
    assert reduction({10:10},broken)['exit_target_fraction']==.7


@pytest.mark.parametrize("volume_ratio,expected_clear", [(1.01, True), (1.0, False), (.99, False)])
def test_volume_inverse_clear_overrides_daily_partial_even_when_close_is_flat(volume_ratio, expected_clear):
    bars = sample()[:6]
    bars[4] = replace(bars[4], close=12.60)
    bars[5] = replace(bars[5], volume=bars[4].volume * volume_ratio, close=12.60)
    options = dict(extra_signals=[inverse_signal(bars, 5)], volume_inverse_n_clear=True,
                   inverse_n_close_reduce=True, staged_exit_same_day=True, exit_on_target=False)
    result = run(bars, **options)
    fills = [row for row in result["orders"] if row["status"] == "filled"]
    if expected_clear:
        buy, sell = fills
        assert sell["reason"] == "volume_inverse_n_clear"
        assert sell["timestamp"] == sell["signal_timestamp"] == bars[5].timestamp.isoformat()
        assert sell["price"] == bars[5].close
        assert sell["remaining_quantity"] == 0
        assert sell["quantity"] == buy["quantity"]
    else:
        assert fills[-1]["reason"] == "inverse_n_close_reduce_90"
        assert fills[-1]["remaining_quantity"] > 0


def test_volume_increase_without_inverse_n_does_not_clear():
    bars = sample()[:6]
    bars[5] = replace(bars[5], volume=bars[4].volume * 2)
    fills = [row for row in run(bars, volume_inverse_n_clear=True, staged_exit_same_day=True)["orders"]
             if row["status"] == "filled"]
    assert fills[-1]["reason"] == "support_low_close_break_reduce"
    assert fills[-1]["remaining_quantity"] > 0


def test_volume_inverse_clear_respects_t_plus_one():
    bars = sample()[:5]
    bars[3] = replace(bars[3], volume=bars[2].volume * 2)
    result = run(bars, volume_inverse_n_clear=True, inverse_n_close_reduce=True,
                 extra_signals=[inverse_signal(bars, 3)])
    buy, sell = [row for row in result["orders"] if row["status"] == "filled"]
    assert any(row["reason"] == "T+1" for row in result["orders"])
    assert sell["reason"] == "volume_inverse_n_clear"
    assert sell["signal_timestamp"] == buy["timestamp"]
    assert sell["timestamp"] == bars[4].timestamp.isoformat()
    assert sell["remaining_quantity"] == 0
