"""A bullish gap can resolve an ambiguous structure exit before it is filled."""

from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path

from wavequant.application.analytics.backtest import run_backtest, run_portfolio
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar, Signal
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals
from wavequant.domain.strategies.strategy_profiles import whole_wave_profile


def sample():
    rows = [
        ("2026-09-02", 10, 10.2, 9.8, 10),
        ("2026-09-03", 10.1, 10.8, 9.9, 10.7),
        ("2026-09-04", 10.8, 12.5, 10.5, 12.4),
        ("2026-09-07", 12.5, 15.5, 12.3, 15),
        ("2026-09-08", 15, 16, 14.9, 15.8),
        ("2026-09-09", 15.8, 16.5, 15.5, 16),
    ]
    bars = [Bar(datetime.fromisoformat(day), "sh.601086", o, h, low, close, 10_000_000)
            for day, o, h, low, close in rows]
    entry = Signal(bars[0].timestamp, bars[0].symbol, 0, "LONG", 10, 9, "entry",
                   bars[0].timestamp, 0, None, "fixture", 11)
    config = StrategyConfig(net_reward_risk_filter=False, exit_on_target=False,
                            max_hold_bars=100, slippage_bps_per_side=0)
    return bars, entry, config


def exit_at(entry, bars, index, reason):
    return replace(entry, timestamp=bars[index].timestamp, bar_index=index,
                   reference_price=bars[index].close, side="EXIT", reason=reason)


def sells(result):
    return [order for order in result.orders if order["side"] == "SELL" and order["status"] == "filled"]


def test_gap_up_and_higher_close_cancels_only_the_ambiguous_exit():
    bars, entry, config = sample()
    ambiguous = exit_at(entry, bars, 2, "strict_structure_unresolved")
    prefix = run_backtest(bars[:4], [entry, ambiguous], config)
    assert sells(prefix) == []
    assert prefix.open_positions[0]["pending_exit"] is None

    later = exit_at(entry, bars, 4, "inverse_n_risk_exit")
    full = run_backtest(bars, [entry, ambiguous, later], config)
    assert [order["reason"] for order in sells(full)] == ["inverse_n_risk_exit"]
    assert sells(full)[0]["timestamp"] == bars[5].timestamp.isoformat()
    assert prefix.orders == [order for order in full.orders
                             if order["timestamp"] <= bars[3].timestamp.isoformat()]


def test_gap_up_but_weak_close_executes_ambiguous_exit_at_observed_close():
    bars, entry, config = sample()
    for close in (12.2, bars[2].close):
        observed = list(bars)
        observed[3] = replace(bars[3], low=12.1, close=close)
        ambiguous = exit_at(entry, observed, 2, "strict_structure_unresolved")
        result = run_backtest(observed[:4], [entry, ambiguous], config)
        assert [order["reason"] for order in sells(result)] == ["strict_structure_unresolved"]
        assert sells(result)[0]["timestamp"] == observed[3].timestamp.isoformat()
        assert sells(result)[0]["execution_model"] == "same_day_close"
        assert sells(result)[0]["price"] == close


def test_no_gap_and_mixed_risk_reasons_keep_next_open_exit():
    bars, entry, config = sample()
    no_gap = list(bars)
    no_gap[3] = replace(bars[3], open=12.4)
    result = run_backtest(no_gap[:4], [entry, exit_at(entry, no_gap, 2, "strict_structure_unresolved")], config)
    assert sells(result)[0]["execution_model"] == "next_open"
    assert sells(result)[0]["price"] == 12.4

    mixed = run_backtest(bars[:4], [entry, exit_at(entry, bars, 2,
                         "strict_structure_unresolved|inverse_n_risk_exit")], config)
    assert sells(mixed)[0]["execution_model"] == "next_open"
    assert sells(mixed)[0]["price"] == bars[3].open


def test_gap_observation_does_not_mask_a_new_structural_stop():
    bars, entry, config = sample()
    bars[3] = replace(bars[3], low=8.9)
    ambiguous = exit_at(entry, bars, 2, "strict_structure_unresolved")
    result = run_backtest(bars[:5], [entry, ambiguous], config)
    assert [order["reason"] for order in sells(result)] == ["structural_stop_observed"]
    assert sells(result)[0]["timestamp"] == bars[4].timestamp.isoformat()


def test_guofang_september_7_strong_gap_keeps_the_holding():
    raw = json.loads((Path(__file__).parent / "fixtures/guofang_2026_consolidation.json")
                     .read_text(encoding="utf-8"))
    bars = [Bar(datetime.fromisoformat(day), raw["symbol"], *values)
            for day, *values in raw["bars"]]
    dates = {bar.timestamp.date().isoformat(): i for i, bar in enumerate(bars)}
    gap_index = dates["2026-09-07"]
    prior_close = bars[gap_index - 1].close
    assert bars[gap_index].open > prior_close
    assert bars[gap_index].close > prior_close

    strategy = SystemStrategy(**whole_wave_profile(
        {"scenarios": {"base": {"execution": {}}}})["strategy"])
    generated = generate_system_signals(bars[:gap_index + 1], strategy)
    structure_exit = next(signal for signal in generated.signals
                          if signal.timestamp == bars[dates["2026-09-04"]].timestamp
                          and signal.side == "EXIT")
    assert structure_exit.reason == "strict_structure_unresolved"

    entry_index = dates["2026-08-27"]
    entry = Signal(bars[entry_index].timestamp, raw["symbol"], entry_index, "LONG",
                   bars[entry_index].close, 9, "entry", bars[entry_index].timestamp,
                   0, None, "fixture", 18)
    config = StrategyConfig(net_reward_risk_filter=False, exit_on_target=False,
                            max_hold_bars=100, slippage_bps_per_side=0)
    result = run_portfolio({raw["symbol"]: bars[:gap_index + 1]},
                           [entry, structure_exit], config)
    assert sells(result) == []
    assert result.open_positions[0]["pending_exit"] is None
