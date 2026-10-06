"""Synthetic complete minutes distinguish intraday exits from daily close models."""

from dataclasses import replace
from datetime import datetime, time
from zoneinfo import ZoneInfo

import pytest

from wavequant.application.analytics.backtest import run_portfolio
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar, Signal
from wavequant.infrastructure.market_data.akshare_history import MinuteCoverageError
from wavequant.infrastructure.market_data.minute import MinuteBar


REASON = "wave_five_top_gap_upper_shadow_clear"


@pytest.fixture
def scenario():
    bars = [
        Bar(datetime(2025, 4, 23), "sh.601086", 9, 9.5, 8.5, 9.5, 4_800_000),
        Bar(datetime(2025, 4, 24), "sh.601086", 9.8, 10.1, 9.7, 10, 4_800_000),
        Bar(datetime(2025, 4, 25), "sh.601086", 9, 10.5, 9, 9, 4_800_000, sellable=False,
            close_sellable=False, nonflat_close_sellable=True, raw_is_st=False, raw_trading_active=True),
        Bar(datetime(2025, 4, 28), "sh.601086", 9.5, 10, 9, 9.8, 4_800_000),
    ]
    clocks = ([time(9, minute) for minute in range(35, 60, 5)]
              + [time(10, minute) for minute in range(0, 60, 5)]
              + [time(11, minute) for minute in range(0, 35, 5)]
              + [time(13, minute) for minute in range(5, 60, 5)]
              + [time(14, minute) for minute in range(0, 60, 5)] + [time(15)])
    minutes = [MinuteBar(datetime.combine(bars[2].timestamp.date(), clock, ZoneInfo("Asia/Shanghai")),
                         9, 9, 9, 9, 100_000) for clock in clocks]
    minutes[0] = replace(minutes[0], high=10.5, close=10.3)
    minutes[1] = replace(minutes[1], open=10.3, high=10.3, low=9.6, close=9.8)
    minutes[2] = replace(minutes[2], open=9.8, high=9.9, close=9.0)
    minutes[3] = replace(minutes[3], open=9.05, high=9.1)
    events = [dict(event="wave_projection_target_reached", attack=0, origin_index=0,
                   a_origin=8.5, bar_index=1, reached_stage="five_top", reached_target=10.0)]
    signal = Signal(bars[1].timestamp, bars[1].symbol, 1, "LONG", bars[1].close,
                    1.0, "fixture", bars[0].timestamp, 0, None, "fixture", 20.0)
    config = StrategyConfig(entry_at_close=True, wave_exhaustion_exit=True, exit_on_target=False,
                            staged_exit_intraday=True, max_hold_bars=200, max_participation=1,
                            slippage_bps_per_side=5, initial_capital=100_000, max_position_weight=1)
    return bars, events, signal, config, minutes


def account(scenario, **changes):
    bars, events, signal, config, minutes = scenario
    return run_portfolio({bars[0].symbol: bars}, [signal], replace(config, **changes),
                         wave_events={bars[0].symbol: events}, minute_loader=lambda _bar: minutes)


def test_body_child_intraday_exit_can_be_bullish_without_a_lower_open(scenario):
    bars, events, signal, config, minutes = scenario
    bars[1] = replace(bars[1], open=10.1, high=10.1, close=9.8)
    signal = replace(signal, reference_price=9.8)
    bars[2] = replace(bars[2], open=9.9, high=10.6, low=9.9, close=10.6,
                      sellable=True, close_sellable=True)
    minutes = [replace(row, open=9.9, high=9.9, low=9.9, close=9.9) for row in minutes]
    minutes[0] = replace(minutes[0], high=10.5, close=10.3)
    minutes[1] = replace(minutes[1], open=10.3, high=10.3, close=10.0)
    minutes[2] = replace(minutes[2], open=10.0, high=10.0)
    minutes[-1] = replace(minutes[-1], high=10.6, close=10.6)
    result = account((bars, events, signal, config, minutes))
    clear = next(row for row in result.orders if row["side"] == "SELL" and row["status"] == "filled")
    assert clear["reason"] == "wave_five_top_body_upper_shadow_clear"
    assert clear["decision_timestamp"][11:16] == clear["execution_timestamp"][11:16] == "09:40"
    assert clear["execution_model"] == "intraday_5m_next_open"
    assert clear["mother_body_low"] == 9.8 and clear["mother_body_high"] == 10.1
    assert clear["child_body_low"] == 9.9 and clear["child_body_high"] == 10.0
    assert clear["observed_close"] == 10.0 < bars[2].close
    assert bars[2].open > bars[1].close and bars[2].close > bars[1].open
    assert clear["remaining_quantity"] == 0


def test_completed_partial_candle_clears_at_next_interval_open(scenario):
    bars, events, signal, config, minutes = scenario
    result = account(scenario)
    clear = next(order for order in result.orders if order["side"] == "SELL" and order["status"] == "filled")
    assert clear["reason"] == REASON
    assert clear["execution_model"] == "intraday_5m_next_open"
    assert clear["decision_timestamp"][11:16] == "09:45"
    assert clear["execution_timestamp"][11:16] == "09:45"
    assert clear["minute_next_open_raw"] == 9.05
    assert clear["remaining_quantity"] == 0
    assert clear["observed_high"] == 10.5
    assert clear["observed_low"] == clear["observed_close"] == 9.0
    assert clear["observed_volume"] == 300_000


def test_intraday_does_not_wait_for_or_borrow_the_final_daily_shape(scenario):
    bars, events, signal, config, minutes = scenario
    bars[2] = replace(bars[2], close=9.9)
    minutes[-1] = replace(minutes[-1], high=9.9, close=9.9)
    result = account((bars, events, signal, config, minutes))
    clear = next(order for order in result.orders if order["side"] == "SELL" and order["status"] == "filled")
    assert clear["execution_model"] == "intraday_5m_next_open"
    assert clear["observed_close"] == 9.0 < bars[2].close
    assert clear["decision_timestamp"][11:16] == "09:45"


def test_global_intraday_target_clears_an_unrelated_holding(scenario):
    bars, events, signal, config, minutes = scenario
    signal = replace(signal, trigger_timestamp=bars[1].timestamp)
    result = account((bars, events, signal, config, minutes))
    clear = next(order for order in result.orders if order["side"] == "SELL" and order["status"] == "filled")
    assert clear["reason"] == REASON
    assert clear["remaining_quantity"] == 0


@pytest.mark.parametrize("kind", ["same_day_goal", "future_goal", "invalidated", "origin_broken"])
def test_minutes_use_only_prior_live_goals_and_cumulative_origin_defense(scenario, kind):
    bars, events, signal, config, minutes = scenario
    if kind in ("same_day_goal", "future_goal"):
        events = [dict(events[0], bar_index=2 if kind == "same_day_goal" else 3)]
    elif kind == "invalidated":
        events.append(dict(events[0], event="wave_projection_invalidated", bar_index=1))
    else:
        events = [dict(events[0], a_origin=9.01)]
    result = account((bars, events, signal, config, minutes))
    assert not any(order.get("execution_model") == "intraday_5m_next_open" for order in result.orders)


def test_future_daily_invalidation_does_not_rewrite_an_earlier_minute_exit(scenario):
    bars, events, signal, config, minutes = scenario
    events.append(dict(events[0], event="wave_projection_invalidated", bar_index=2))
    result = account((bars, events, signal, config, minutes))
    clear = next(order for order in result.orders if order["side"] == "SELL" and order["status"] == "filled")
    assert clear["reason"] == REASON
    assert clear["execution_model"] == "intraday_5m_next_open"


def test_prefix_appending_future_days_keeps_intraday_orders_identical(scenario):
    bars, events, signal, config, minutes = scenario
    full = account(scenario)
    prefix = account((bars[:3], events, signal, config, minutes))
    assert prefix.orders == full.orders


def test_missing_minutes_is_recorded_as_daily_close_fallback(scenario):
    bars, events, signal, config, _ = scenario

    def missing(_bar):
        raise MinuteCoverageError("2025-04-25", None, None)

    result = run_portfolio({bars[0].symbol: bars[:3]}, [signal],
                           replace(config, missing_minute_daily_fallback=True),
                           wave_events={bars[0].symbol: events}, minute_loader=missing)
    assert result.minute_fallbacks
    assert result.minute_fallbacks[0]["purpose"] == "five_top_upper_shadow_exit"
    assert result.minute_fallbacks[0]["execution_model"] == "same_day_close"
    with pytest.raises(MinuteCoverageError):
        run_portfolio({bars[0].symbol: bars[:3]}, [signal], config,
                      wave_events={bars[0].symbol: events}, minute_loader=missing)


def test_corrupt_minutes_do_not_turn_into_missing_history(scenario):
    bars, events, signal, config, _ = scenario

    def corrupt(_bar):
        raise ValueError("same-source OHLC mismatch")

    with pytest.raises(ValueError, match="OHLC mismatch"):
        run_portfolio({bars[0].symbol: bars[:3]}, [signal],
                      replace(config, missing_minute_daily_fallback=True),
                      wave_events={bars[0].symbol: events}, minute_loader=corrupt)


def test_opened_limit_can_model_the_next_minute_limit_price_without_slippage(scenario):
    bars, events, signal, config, minutes = scenario
    minutes[3] = replace(minutes[3], open=9.0)
    result = account((bars, events, signal, config, minutes), nonflat_limit_close_fill=True)
    clear = next(order for order in result.orders if order["side"] == "SELL" and order["status"] == "filled")
    assert clear["price"] == 9.0
    assert clear["fill_assumption"] == "observed_nonflat_limit_intraday_sell_without_queue_verification"
    assert clear["applied_slippage_bps"] == 0.0
    assert clear["execution_observed_high"] == 10.5
    assert clear["execution_observed_volume"] == 300_000


def test_normal_minute_slippage_cannot_cross_the_official_lower_limit(scenario):
    bars, events, signal, config, minutes = scenario
    minutes[3] = replace(minutes[3], open=9.0001)
    result = account((bars, events, signal, config, minutes))
    clear = next(order for order in result.orders if order["side"] == "SELL" and order["status"] == "filled")
    assert clear["price"] == clear["execution_lower_limit_raw"] == 9.0
    assert 0 < clear["applied_slippage_bps"] < config.slippage_bps_per_side
    assert clear["slippage_price_floor"] == "a_share_lower_limit"
    assert "fill_assumption" not in clear


def test_zero_observed_volume_does_not_borrow_later_trades_for_limit_queue_access(scenario):
    bars, events, signal, config, minutes = scenario
    for index in range(3):
        minutes[index] = replace(minutes[index], volume=0)
    minutes[3] = replace(minutes[3], open=9.0, volume=400_000)
    result = account((bars, events, signal, config, minutes), nonflat_limit_close_fill=True)
    first = next(order for order in result.orders if order["side"] == "SELL")
    assert first["reason"] == "not_sellable"
    assert first["execution_observed_volume"] == 0
    assert first["execution_timestamp"][11:16] == "09:45"
    clear = next(order for order in result.orders if order["side"] == "SELL" and order["status"] == "filled")
    assert clear["execution_timestamp"][11:16] == "09:50"
    assert clear["execution_observed_volume"] == 400_000


@pytest.mark.parametrize("source", ["unknown", "suspended"])
def test_source_unknown_or_suspended_never_overrides_explicit_sell_veto(scenario, source):
    bars, events, signal, config, minutes = scenario
    bars[2] = replace(bars[2], raw_is_st=None if source == "unknown" else False,
                      raw_trading_active=None if source == "unknown" else False,
                      nonflat_close_sellable=None)
    result = account((bars, events, signal, config, minutes), nonflat_limit_close_fill=True)
    assert not any(order["side"] == "SELL" and order["status"] == "filled"
                   and order["timestamp"][:10] == "2025-04-25" for order in result.orders)


def test_daily_nonflat_limit_close_fill_uses_raw_reference_and_explicit_assumption(scenario):
    result = account(scenario, staged_exit_intraday=False, nonflat_limit_close_fill=True)
    clear = next(order for order in result.orders if order["side"] == "SELL" and order["status"] == "filled")
    assert clear["timestamp"][:10] == "2025-04-25"
    assert clear["execution_model"] == "same_day_close"
    assert clear["price"] == 9.0
    assert clear["remaining_quantity"] == 0
    assert clear["fill_assumption"] == "nonflat_limit_close_sell_without_queue_verification"
    assert clear["applied_slippage_bps"] == 0.0


def test_daily_close_permission_is_independent_of_the_opening_veto(scenario):
    bars, events, signal, config, minutes = scenario
    bars[2] = replace(bars[2], close=9.1, close_sellable=True, nonflat_close_sellable=False)
    exit_signal = replace(signal, timestamp=bars[2].timestamp, bar_index=2, side="EXIT",
                          reference_price=bars[2].close, reason=REASON)
    result = run_portfolio({bars[0].symbol: bars[:3]}, [signal, exit_signal],
                           replace(config, staged_exit_intraday=False, wave_exhaustion_exit=False))
    clear = next(order for order in result.orders if order["side"] == "SELL" and order["status"] == "filled")
    assert clear["timestamp"][:10] == "2025-04-25"
    assert clear["price"] == pytest.approx(9.1 * (1 - config.slippage_bps_per_side / 10000))
    assert "fill_assumption" not in clear


@pytest.mark.parametrize("change", ["flat", "zero_volume", "unknown_flags"])
def test_daily_nonflat_model_keeps_flat_zero_volume_and_legacy_vetoes(scenario, change):
    bars, events, signal, config, minutes = scenario
    if change == "flat":
        bars[2] = replace(bars[2], high=9.0)
    elif change == "zero_volume":
        bars[2] = replace(bars[2], volume=0)
    else:
        bars[2] = replace(bars[2], close_sellable=None, nonflat_close_sellable=None)
    exit_signal = replace(signal, timestamp=bars[2].timestamp, bar_index=2, side="EXIT", reason=REASON)
    result = run_portfolio({bars[0].symbol: bars[:3]}, [signal, exit_signal],
                           replace(config, staged_exit_intraday=False, nonflat_limit_close_fill=True))
    assert not any(order["side"] == "SELL" and order["status"] == "filled" for order in result.orders)


def intraday_add_on(bars, signal, at):
    current_signal = replace(signal, timestamp=bars[2].timestamp, bar_index=2,
                             reference_price=9.8, invalidation_price=9.0, reason="fixture_add_on")
    return dict(signal=current_signal, price=9.8, buyable=True, remaining_low=9.0, remaining_high=10.5,
                timing=dict(execution_model="intraday_5m_next_open", decision_source="completed_five_minute_bar",
                            decision_timestamp=at.isoformat(), execution_timestamp=at.isoformat()))


def test_later_add_on_cannot_change_an_earlier_clear_quantity_or_cost(scenario):
    bars, events, signal, config, minutes = scenario
    at = datetime(2025, 4, 25, 10, tzinfo=ZoneInfo("Asia/Shanghai"))
    extra = intraday_add_on(bars, signal, at)
    result = run_portfolio({bars[0].symbol: bars}, [signal, extra["signal"]], replace(config, allow_add_on=True),
                           wave_events={bars[0].symbol: events}, minute_loader=lambda _bar: minutes,
                           entry_executions={(bars[0].symbol, 2): extra})
    buy, clear = [order for order in result.orders if order["status"] == "filled"]
    assert clear["quantity"] == buy["quantity"]
    assert clear["remaining_quantity"] == 0
    rejected = next(order for order in result.orders if order["side"] == "BUY" and order["status"] == "cancelled")
    assert rejected["reason"] == "same_day_exit_priority"


def test_earlier_add_on_is_preserved_and_only_old_shares_sell_under_t1(scenario):
    bars, events, signal, config, minutes = scenario
    at = datetime(2025, 4, 25, 9, 40, tzinfo=ZoneInfo("Asia/Shanghai"))
    extra = intraday_add_on(bars, signal, at)
    result = run_portfolio({bars[0].symbol: bars}, [signal, extra["signal"]], replace(config, allow_add_on=True),
                           wave_events={bars[0].symbol: events}, minute_loader=lambda _bar: minutes,
                           entry_executions={(bars[0].symbol, 2): extra})
    old_buy, add_on, first_clear, next_clear = [order for order in result.orders if order["status"] == "filled"]
    assert first_clear["execution_timestamp"][11:16] == "09:45"
    assert first_clear["quantity"] == old_buy["quantity"]
    assert first_clear["remaining_quantity"] == add_on["quantity"]
    assert first_clear["t1_deferred_quantity"] == add_on["quantity"]
    assert next_clear["timestamp"][:10] == "2025-04-28"
    assert next_clear["quantity"] == add_on["quantity"]
    assert next_clear["remaining_quantity"] == 0


def test_an_earlier_other_symbol_buy_cannot_use_a_later_exit_cash(scenario):
    bars, events, signal, config, minutes = scenario
    other = "sz.000001"
    other_bars = [replace(bar, symbol=other, open=10, high=10, low=10, close=10, buyable=True) for bar in bars]
    other_signal = replace(signal, symbol=other, timestamp=bars[2].timestamp, bar_index=2)
    at = datetime(2025, 4, 25, 9, 35)
    extra = dict(signal=other_signal, price=10, buyable=True, remaining_low=10, remaining_high=10,
                 timing=dict(execution_model="intraday_5m_next_open", execution_timestamp=at.isoformat()))
    result = run_portfolio({bars[0].symbol: bars, other: other_bars}, [signal, other_signal],
                           replace(config, initial_capital=2_000, risk_fraction=0.9),
                           wave_events={bars[0].symbol: events}, minute_loader=lambda _bar: minutes,
                           entry_executions={(other, 2): extra})
    assert not any(order["symbol"] == other and order["status"] == "filled" for order in result.orders)
    rejected = next(order for order in result.orders if order["symbol"] == other and order["side"] == "BUY")
    assert rejected["reason"] == "cash_below_one_lot"


def test_missing_minutes_close_fill_is_explicitly_identified_as_a_fallback(scenario):
    bars, events, signal, config, _ = scenario

    def missing(_bar):
        raise MinuteCoverageError("2025-04-25", None, None)

    result = run_portfolio({bars[0].symbol: bars[:3]}, [signal],
                           replace(config, missing_minute_daily_fallback=True, nonflat_limit_close_fill=True),
                           wave_events={bars[0].symbol: events}, minute_loader=missing)
    clear = next(order for order in result.orders if order["side"] == "SELL" and order["status"] == "filled")
    assert clear["execution_model"] == "same_day_close"
    assert clear["minute_fallback"]["purpose"] == "five_top_upper_shadow_exit"
    assert "execution_timestamp" not in clear


def test_adjustment_rounding_at_exact_lower_limit_is_clamped_without_negative_slippage(scenario):
    bars, events, signal, config, minutes = scenario
    factor = 1.3661031469359
    bars = [replace(bar, open=bar.open * factor, high=bar.high * factor,
                    low=bar.low * factor, close=bar.close * factor, adjustment_factor=factor) for bar in bars]
    bars[2] = replace(bars[2], low=bars[2].low - 1e-14, close=bars[2].close - 1e-14)
    events = [dict(events[0], a_origin=8.5 * factor, reached_target=10.0 * factor)]
    signal = replace(signal, reference_price=bars[1].close)
    result = account((bars, events, signal, config, minutes), staged_exit_intraday=False, nonflat_limit_close_fill=True)
    clear = next(order for order in result.orders if order["side"] == "SELL" and order["status"] == "filled")
    assert clear["price"] / factor >= 9.0
    assert clear["applied_slippage_bps"] == 0.0


def test_a_materially_below_limit_next_open_is_rejected_even_when_queue_model_is_enabled(scenario):
    bars, events, signal, config, minutes = scenario
    bars[2] = replace(bars[2], low=8.8)
    minutes[3] = replace(minutes[3], open=8.8, low=8.8)
    result = account((bars, events, signal, config, minutes), nonflat_limit_close_fill=True)
    first = next(order for order in result.orders if order["side"] == "SELL")
    assert first["status"] == "deferred"
    assert first["reason"] == "execution_below_lower_limit"
    clear = next(order for order in result.orders if order["side"] == "SELL" and order["status"] == "filled")
    assert clear["price"] >= 9.0


def test_frozen_a_origin_equality_holds_even_when_squeeze_defense_is_higher(scenario):
    bars, events, signal, config, minutes = scenario
    events = [dict(events[0], a_origin=9.0, defense=9.5)]
    result = account((bars, events, signal, config, minutes))
    clear = next(order for order in result.orders if order["side"] == "SELL" and order["status"] == "filled")
    assert clear["execution_model"] == "intraday_5m_next_open"


def test_liquidity_capacity_still_defers_a_requested_intraday_full_exit(scenario):
    bars, events, signal, config, minutes = scenario
    bars[1] = replace(bars[1], volume=0)
    result = account((bars, events, signal, config, minutes), liquidity_lookback=1)
    first = next(order for order in result.orders if order["side"] == "SELL")
    assert first["reason"] == "liquidity_capacity"
    assert not any(order["side"] == "SELL" and order["status"] == "filled"
                   and order["timestamp"][:10] == "2025-04-25" for order in result.orders)


@pytest.mark.parametrize("naive_minutes", [False, True])
def test_existing_staged_minutes_recompute_permissions_after_a_locked_open(naive_minutes):
    rows = [("08", 12.66, 12.96, 12.66, 12.95), ("09", 12.91, 12.91, 12.43, 12.68),
            ("12", 12.82, 13.24, 12.78, 13), ("13", 13.15, 13.21, 12.78, 12.88),
            ("14", 12.88, 12.92, 12.58, 12.73), ("15", 10.18, 12.76, 10.18, 10.18)]
    bars = [Bar(datetime.fromisoformat(f"2025-05-{day}"), "sz.300154", opened, high, low, close, 4_800_000)
            for day, opened, high, low, close in rows]
    bars[-1] = replace(bars[-1], sellable=False, close_sellable=False, nonflat_close_sellable=True,
                       raw_is_st=False, raw_trading_active=True)
    clocks = ([time(9, minute) for minute in range(35, 60, 5)]
              + [time(10, minute) for minute in range(0, 60, 5)]
              + [time(11, minute) for minute in range(0, 35, 5)]
              + [time(13, minute) for minute in range(5, 60, 5)]
              + [time(14, minute) for minute in range(0, 60, 5)] + [time(15)])
    minutes = []
    for clock in clocks:
        opening = 12.69 if clock == time(14, 35) else 12.56 if clock == time(14, 40) else 12.70
        close = 12.57 if clock >= time(14, 35) else 12.70
        minutes.append(MinuteBar(datetime.combine(bars[-1].timestamp.date(), clock, ZoneInfo("Asia/Shanghai")),
                                 opening, max(opening, close), min(opening, close), close, 100_000))
    minutes[0] = replace(minutes[0], open=10.18, high=12.76, low=10.18)
    minutes[-1] = replace(minutes[-1], low=10.18, close=10.18)
    if naive_minutes:
        minutes = [replace(minute, timestamp=minute.timestamp.replace(tzinfo=None)) for minute in minutes]
    signal = Signal(bars[2].timestamp, bars[2].symbol, 2, "LONG", bars[2].close, 10,
                    "fixture", bars[2].timestamp, 0, None, "fixture", 20)
    at = datetime.combine(bars[-1].timestamp.date(), time(9, 35), ZoneInfo("Asia/Shanghai"))
    extra = dict(signal=replace(signal, timestamp=bars[-1].timestamp, bar_index=5), price=12.70,
                 buyable=True, remaining_low=10.18, remaining_high=12.76,
                 timing=dict(execution_model="intraday_5m_next_open", execution_timestamp=at.isoformat()))
    config = StrategyConfig(staged_exit_enabled=True, staged_exit_intraday=True, max_hold_bars=200,
                            initial_capital=100_000, max_position_weight=0.8, risk_fraction=0.2,
                            max_participation=1, slippage_bps_per_side=0)
    result = run_portfolio({bars[0].symbol: bars}, [signal], config, minute_loader=lambda _bar: minutes,
                           entry_executions={(bars[0].symbol, 5): extra})
    sells = [order for order in result.orders if order["side"] == "SELL" and order["status"] == "filled"]
    assert [order["exit_target_fraction"] for order in sells] == [0.35, 0.65]
    assert [order["minute_next_open_raw"] for order in sells] == [12.69, 12.56]
    assert all(order["execution_model"] == "intraday_5m_next_open" for order in sells)


def test_t1_residual_next_open_does_not_reuse_yesterdays_queue_fill_assumption(scenario):
    bars, events, signal, config, minutes = scenario
    minutes[3] = replace(minutes[3], open=9.0)
    at = datetime(2025, 4, 25, 9, 40, tzinfo=ZoneInfo("Asia/Shanghai"))
    extra = intraday_add_on(bars, signal, at)
    result = run_portfolio({bars[0].symbol: bars}, [signal, extra["signal"]],
                           replace(config, allow_add_on=True, nonflat_limit_close_fill=True),
                           wave_events={bars[0].symbol: events}, minute_loader=lambda _bar: minutes,
                           entry_executions={(bars[0].symbol, 2): extra})
    clears = [order for order in result.orders if order["side"] == "SELL" and order["status"] == "filled"]
    assert clears[0]["fill_assumption"] == "observed_nonflat_limit_intraday_sell_without_queue_verification"
    assert clears[0]["applied_slippage_bps"] == 0.0
    assert clears[1]["execution_model"] == "next_open"
    assert clears[1]["price"] == pytest.approx(bars[3].open * (1 - config.slippage_bps_per_side / 10000))
    assert "fill_assumption" not in clears[1]
    assert clears[1].get("applied_slippage_bps", config.slippage_bps_per_side) == config.slippage_bps_per_side
    assert clears[1]["execution_timestamp"].startswith("2025-04-28T09:30")
    assert "execution_observed_high" not in clears[1]


def test_a_prior_known_full_exit_uses_the_opened_minute_window_and_keeps_its_reason(scenario):
    bars, events, signal, config, minutes = scenario
    bars = [replace(bars[0], timestamp=datetime(2025, 4, 22)), *bars]
    events = [dict(events[0], attack=1, origin_index=1, bar_index=2)]
    old_buy = replace(signal, timestamp=bars[1].timestamp, bar_index=1, reference_price=bars[1].close)
    old_exit = replace(signal, bar_index=2, side="EXIT", reason="prior_known_full_clear")
    result = run_portfolio({bars[0].symbol: bars}, [old_buy, old_exit], config,
                           wave_events={bars[0].symbol: events}, minute_loader=lambda _bar: minutes)
    clear = next(order for order in result.orders if order["side"] == "SELL" and order["status"] == "filled")
    assert clear["timestamp"].startswith("2025-04-25T09:45")
    assert clear["reason"] == clear["decision_reason"] == "prior_known_full_clear"
    assert clear["signal_timestamp"].startswith("2025-04-24")
    assert clear["execution_model"] == "intraday_5m_next_open"
    assert clear["execution_trigger_reason"] == REASON
    assert "wave_reached_price" not in clear
    assert clear["remaining_quantity"] == 0
