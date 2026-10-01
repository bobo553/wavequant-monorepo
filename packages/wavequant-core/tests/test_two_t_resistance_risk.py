"""A smaller N cannot bypass an already reached larger N's two-T resistance."""

from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path

import pytest

from wavequant.application.analytics.backtest import run_portfolio
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar, Signal
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals
from wavequant.domain.strategies.two_t_resistance import two_t_resistance_history
from wavequant.domain.strategies.strategy_profiles import whole_wave_profile


@pytest.fixture(scope="module")
def xianfeng_june():
    raw = json.loads((Path(__file__).parent / "fixtures/xianfeng_2026_strong_squeeze.json").read_text(encoding="utf-8"))
    bars = [Bar(datetime.fromisoformat(day), raw["symbol"], *prices)
            for day, *prices in raw["bars"] if day <= "2026-06-10"]
    profile = whole_wave_profile({"scenarios": {"base": {"execution": {}}}})
    config = SystemStrategy(**profile["strategy"])
    return bars, config, generate_system_signals(bars, config)


def test_xianfeng_two_t_warning_blocks_smaller_n_buy_and_next_volume_double_break_exits(xianfeng_june):
    bars, config, result = xianfeng_june
    assert not any(s.side == "LONG" and str(s.timestamp.date()) == "2026-06-09" for s in result.signals)
    clear = next(s for s in result.signals if s.side == "EXIT" and str(s.timestamp.date()) == "2026-06-10")
    assert "wave_two_t_resistance_volume_clear" in clear.reason
    warning = next(e for e in result.audit if e["event"] == "target_resistance_observed"
                   and e["timestamp"].startswith("2026-06-09"))
    assert warning["wave_n_date"] == "2026-05-18"
    assert warning["wave_reached_price"] == 7.30
    assert warning["wave_upper_shadow_fraction"] == pytest.approx(.36 / .76)
    assert any(e["event"] == "entry_rejected" and e["timestamp"].startswith("2026-06-09")
               and e["reason"] == "wave_two_t_resistance_reduce" for e in result.audit)


def target_pair():
    raw = json.loads((Path(__file__).parent / "fixtures/xianfeng_2026_strong_squeeze.json").read_text(encoding="utf-8"))
    days = {"2026-04-29", "2026-05-18", "2026-06-04", "2026-06-05", "2026-06-08", "2026-06-09", "2026-06-10", "2026-06-11"}
    bars = [Bar(datetime.fromisoformat(day), raw["symbol"], *prices)
            for day, *prices in raw["bars"] if day in days]
    event = dict(event="wave_projection_ready", attack=1, origin_index=0, bar_index=5, two_t=7.3)
    return bars, event


def test_actual_target_pair_is_causal_and_compares_with_last_bearish_not_yesterday_volume():
    bars, event = target_pair()
    full = two_t_resistance_history(bars, [event])
    assert full[5]["exit_target_fraction"] == .8
    assert full[6]["exit_fraction"] == 1
    assert full[6]["bearish_reference_date"] == "2026-06-04"
    assert full[6]["bearish_reference_volume"] == 36_051_401
    assert full[6]["observed_volume"] < bars[5].volume
    assert two_t_resistance_history(bars[:5], [event]) == {}
    assert two_t_resistance_history(bars[:6], [event]) == {5: full[5]}
    assert two_t_resistance_history(bars[:7], [event]) == full


def test_real_june9_warning_and_signals_do_not_use_june10_confirmation(xianfeng_june):
    bars, config, full = xianfeng_june
    prefix = generate_system_signals(bars[:-1], config)
    assert prefix.signals == [signal for signal in full.signals if signal.bar_index < len(bars) - 1]
    warning = lambda result: [e for e in result.audit if e["event"] == "target_resistance_observed"
                              and e["timestamp"].startswith("2026-06-09")]
    assert warning(prefix) == warning(full)


@pytest.mark.parametrize("invalid", ["not_reached", "short_shadow", "wick_shorter_than_body", "same_low", "same_close", "equal_volume", "bullish", "invalidated"])
def test_warning_and_clear_have_strict_price_and_volume_boundaries(invalid):
    bars, event = target_pair()
    events = [event]
    if invalid == "not_reached":
        event["two_t"] = 7.31
    elif invalid == "short_shadow":
        bars[5] = replace(bars[5], close=7.1)
    elif invalid == "wick_shorter_than_body":
        bars[5] = replace(bars[5], open=6.3)
    elif invalid == "same_low":
        bars[6] = replace(bars[6], low=bars[5].low, close=6.6)
    elif invalid == "same_close":
        bars[6] = replace(bars[6], open=7.0, high=7.0, close=bars[5].close)
    elif invalid == "equal_volume":
        bars[6] = replace(bars[6], volume=bars[2].volume)
    elif invalid == "bullish":
        bars[6] = replace(bars[6], open=6.4)
    else:
        events.append(dict(event="wave_projection_invalidated", attack=1, origin_index=0, bar_index=5))
    risks = two_t_resistance_history(bars, events)
    if invalid in ("not_reached", "short_shadow", "wick_shorter_than_body", "invalidated"):
        assert not risks
    else:
        assert 5 in risks
        assert 6 not in risks or risks[6]["exit_fraction"] < 1


def test_next_session_shadow_is_allowed_but_later_shadow_cannot_reuse_old_target_window():
    bars, event = target_pair()
    bars[5] = replace(bars[5], close=7.2)
    bars[6] = replace(bars[6], open=6.65, high=6.89, close=6.7)
    assert 6 in two_t_resistance_history(bars, [event])
    bars[6] = replace(bars[6], close=6.87)
    bars[7] = replace(bars[7], open=6.4, high=7.0, close=6.45)
    assert not two_t_resistance_history(bars, [event])


def test_holding_reduces_and_clears_even_when_its_signal_owns_a_different_n():
    bars, event = target_pair()
    signal = Signal(bars[3].timestamp, bars[3].symbol, 3, "LONG", bars[3].close, 4.38,
                    "fixture", bars[3].timestamp, 0, None, "fixture", 9.68)
    repeat = replace(signal, timestamp=bars[5].timestamp, bar_index=5, reference_price=bars[5].close)
    config = StrategyConfig(entry_at_close=True, allow_add_on=True, exit_on_target=False,
                            wave_exhaustion_exit=True, max_hold_bars=100, max_participation=1,
                            liquidity_lookback=1, slippage_bps_per_side=0)
    account = run_portfolio({bars[0].symbol: bars}, [signal, repeat], config,
                            wave_events={bars[0].symbol: [event]})
    sells = [order for order in account.orders if order["side"] == "SELL" and order["status"] == "filled"]
    assert [(row["timestamp"][:10], row["reason"]) for row in sells] == [
        ("2026-06-09", "wave_two_t_resistance_reduce"),
        ("2026-06-10", "wave_two_t_resistance_volume_clear"),
    ]
    assert sells[0]["remaining_quantity"] > 0 and sells[1]["remaining_quantity"] == 0
    assert not account.open_positions
    assert any(row["side"] == "BUY" and row["status"] == "cancelled"
               and row["reason"] == "wave_two_t_resistance_reduce" for row in account.orders)
    flat = run_portfolio({bars[0].symbol: bars}, [repeat], config, wave_events={bars[0].symbol: [event]})
    assert not any(row["side"] == "BUY" and row["status"] == "filled" for row in flat.orders)
    small = run_portfolio({bars[0].symbol: bars}, [signal],
        replace(config, initial_capital=1000, max_position_weight=1, risk_fraction=1),
        wave_events={bars[0].symbol: [event]})
    assert any(row["reason"] == "reduction_below_one_lot" for row in small.orders)
    assert any(row["reason"] == "wave_two_t_resistance_volume_clear" and row["status"] == "filled"
               for row in small.orders)
    assert not small.open_positions


def test_close_known_warning_does_not_retroactively_cancel_previous_signal_next_open_buy():
    bars, event = target_pair()
    signal = Signal(bars[4].timestamp, bars[4].symbol, 4, "LONG", bars[4].close, 4.38,
                    "fixture", bars[4].timestamp, 0, None, "fixture", 9.68)
    config = StrategyConfig(entry_at_close=False, exit_on_target=False, wave_exhaustion_exit=True,
                            max_hold_bars=100, max_participation=1, liquidity_lookback=1,
                            slippage_bps_per_side=0)
    result = run_portfolio({bars[0].symbol: bars}, [signal], config, wave_events={bars[0].symbol: [event]})
    buy = next(row for row in result.orders if row["side"] == "BUY" and row["status"] == "filled")
    assert buy["timestamp"][:10] == "2026-06-09"
    assert buy["price"] == bars[5].open
    assert not result.open_positions


def test_global_confirmed_exit_signal_clears_at_close_without_owned_projection_metadata():
    bars, _ = target_pair()
    buy = Signal(bars[3].timestamp, bars[3].symbol, 3, "LONG", bars[3].close, 4.38,
                 "fixture", bars[3].timestamp, 0, None, "fixture", 9.68)
    clear = Signal(bars[6].timestamp, bars[6].symbol, 6, "EXIT", bars[6].close, bars[6].high,
                   "wave_two_t_resistance_volume_clear", bars[6].timestamp, 0, None, "risk_exit")
    config = StrategyConfig(entry_at_close=True, exit_on_target=False, max_participation=1,
                            liquidity_lookback=1, slippage_bps_per_side=0)
    result = run_portfolio({bars[0].symbol: bars}, [buy, clear], config)
    sell = next(row for row in result.orders if row["side"] == "SELL" and row["status"] == "filled")
    assert sell["timestamp"][:10] == "2026-06-10"
    assert sell["execution_model"] == "same_day_close"
    assert not result.open_positions
