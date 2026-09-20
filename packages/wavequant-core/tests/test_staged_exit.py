from dataclasses import asdict, replace
from datetime import datetime, time
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
        100_000 + sum(t["pnl"] for t in result["trades"]) + result["backtest"]["open_positions"][0]["unrealized_pnl"]
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
