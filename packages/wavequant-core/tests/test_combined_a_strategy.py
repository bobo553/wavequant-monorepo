"""The combined-A route shares global risk gates and real execution rules."""

from dataclasses import asdict, replace
from datetime import datetime
import importlib
import json
from pathlib import Path

import pytest

from wavequant.application.analytics.backtest import run_portfolio
from wavequant.application.analytics.trade_evidence import enrich_ledger
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals
from wavequant.domain.strategies.strategy_profiles import whole_wave_profile


@pytest.fixture(scope="module")
def guofang_combined():
    raw = json.loads((Path(__file__).parent / "fixtures/guofang_2025_combined_a.json").read_text("utf-8"))
    bars = [Bar(datetime.fromisoformat(day), raw["symbol"],
                *(values[0], values[1], values[2], values[3], values[4]), adjustment_factor=values[5],
                close_buyable=day != "2025-04-03", nonflat_close_buyable=True)
            for day, *values in raw["bars"]]
    now = next(index for index, bar in enumerate(bars) if str(bar.timestamp.date()) == "2025-04-03")
    config = SystemStrategy(**whole_wave_profile({"scenarios": {"base": {"execution": {}}}})["strategy"])
    return bars, now, config, generate_system_signals(bars, config)


def test_real_consolidation_includes_wait_after_low_and_generates_new_global_route(guofang_combined):
    bars, now, config, generated = guofang_combined
    old = generate_system_signals(bars, replace(config, combined_a_entry_enabled=False))
    assert not any(signal.side == "LONG" and signal.bar_index == now for signal in old.signals)
    signal = next(signal for signal in generated.signals if signal.side == "LONG" and signal.bar_index == now)
    assert signal.reason == "system_combined_a_pullback_breakout"
    assert signal.reference_price == pytest.approx(7.158380489944116)
    assert signal.invalidation_price == pytest.approx(5.887904563293728)
    assert signal.target_price == pytest.approx(9.316823462102839)
    proof = next(event for event in generated.audit if event["bar_index"] == now
                 and event["event"] == "long_transition_evidence")
    assert proof["combined_a_pullback_sessions"] == 58
    assert proof["combined_a_internal_pullback_sessions"] == 70
    assert proof["combined_a_child_pullback_sessions"] == 9
    assert proof["combined_a_pullback_date"] == "2025-01-13"
    assert proof["combined_a_minimum_close"] > proof["combined_a_two_thirds_price"]
    assert signal.rvol == pytest.approx(20_135_200 / 6_428_999)


def test_prefix_and_partial_volume_cache_do_not_borrow_later_breakout(guofang_combined):
    bars, now, config, full = guofang_combined
    completed = bars[:now + 1]
    prefix = generate_system_signals(completed, config)
    assert prefix.signals == [signal for signal in full.signals if signal.bar_index <= now]
    assert prefix.audit == [event for event in full.audit if event["bar_index"] <= now]
    cache = {"source_bars": tuple(bars)}
    partial = [*bars[:now], replace(bars[now], volume=bars[now - 1].volume)]
    early = generate_system_signals(partial, config, chart_history_cache=cache)
    assert not any(signal.side == "LONG" and signal.bar_index == now for signal in early.signals)
    replay = generate_system_signals(completed, config, chart_history_cache=cache)
    assert replay.signals == prefix.signals
    assert replay.audit == prefix.audit


@pytest.mark.parametrize("gate", ["two_t", "five_top", "ten_full", "secondary", "inverse", "new_inverse_low"])
def test_combined_route_cannot_bypass_global_gates(guofang_combined, monkeypatch, gate):
    bars, now, config, _ = guofang_combined
    reason = "test_global_gate"
    if gate in ("two_t", "five_top", "ten_full", "secondary"):
        module_name, function_name = {
            "two_t": ("integrated_strategy", "two_t_resistance_history"),
            "five_top": ("integrated_strategy", "five_top_entry_history"),
            "ten_full": ("integrated_strategy", "ten_full_entry_history"),
            "secondary": ("secondary_resistance", "secondary_resistance_history"),
        }[gate]
        risk: dict[str, object] = {"secondary_high": 10.0, "secondary_resistance_high": 10.0,
                "secondary_attack_date": "2025-03-17"} if gate == "secondary" else {"reason": reason}
        if gate == "two_t":
            risk["exit_fraction"] = 0.8
        if gate == "secondary":
            reason = "secondary_breakout_resistance_unresolved"
        module = importlib.import_module("wavequant.domain.strategies." + module_name)
        monkeypatch.setattr(module, function_name, lambda *args, **kwargs: {now: risk})
    elif gate == "inverse":
        module = importlib.import_module("wavequant.domain.strategies.inverse_reentry")
        monkeypatch.setattr(module, "inverse_reentry_rejection", lambda *args, **kwargs: {"reason": reason})
    else:
        module = importlib.import_module("wavequant.domain.strategies.inverse_n_entry")
        risk = {"reason": reason, "inverse_neckline_date": "2025-03-17", "inverse_rebound_date": "2025-03-26"}
        monkeypatch.setattr(module, "inverse_n_low_entry_risk",
                            lambda bars, index, points: risk if index == now else None)
    generated = generate_system_signals(bars[:now + 1], config)
    assert not any(signal.side == "LONG" and signal.bar_index == now for signal in generated.signals)
    assert any(event["event"] == "entry_rejected" and event["bar_index"] == now
               and event["reason"] == reason for event in generated.audit)


def test_real_same_close_fill_and_ledger_preserve_permissions_and_reward_risk(guofang_combined):
    bars, now, config, generated = guofang_combined
    bars = bars[:now + 1]
    signal = next(signal for signal in generated.signals if signal.side == "LONG" and signal.bar_index == now)
    execution = StrategyConfig(entry_at_close=True, nonflat_limit_close_fill=True, exit_on_target=False)
    result = run_portfolio({signal.symbol: bars}, [signal], execution)
    enrich_ledger(bars, result, generated, asdict(config))
    order = result.orders[-1]
    assert order["status"] == "filled"
    assert order["timestamp"].startswith("2025-04-03")
    assert order["raw_price"] == pytest.approx(5.24)
    assert order["net_reward_risk"] >= signal.minimum_reward_risk
    assert order["fill_assumption"] == "nonflat_limit_close_without_queue_verification"
    assert all(check["passed"] is True for check in order["entry_conditions"])
    strict = run_portfolio({signal.symbol: bars}, [signal], replace(execution, nonflat_limit_close_fill=False))
    assert strict.orders[-1]["status"] == "cancelled"
    poor_reward = run_portfolio({signal.symbol: bars}, [replace(signal, minimum_reward_risk=2.0)], execution)
    assert not any(order["status"] == "filled" for order in poor_reward.orders)


def test_exhausted_combined_target_does_not_suppress_independent_shallow_route(guofang_combined, monkeypatch):
    bars, now, config, _ = guofang_combined
    combined_module = importlib.import_module("wavequant.domain.strategies.integrated_strategy")
    shallow_module = importlib.import_module("wavequant.domain.strategies.shallow_base_breakout")
    observe_combined = combined_module.combined_a_entry_history

    def exhausted(*args, **kwargs):
        events, proofs = observe_combined(*args, **kwargs)
        proofs[now] = dict(proofs[now], target=bars[now].close)
        return events, proofs

    monkeypatch.setattr(combined_module, "combined_a_entry_history", exhausted)
    special = dict(stop=6.0, target=9.5, counter_ratio=0.62, breakout_volume_multiple=3.13,
                   buy_point_type="shallow_base_breakout")
    monkeypatch.setattr(shallow_module, "shallow_base_history", lambda *args, **kwargs: ([], {now: special}))
    generated = generate_system_signals(bars[:now + 1], config)
    signal = next(signal for signal in generated.signals if signal.side == "LONG" and signal.bar_index == now)
    assert signal.reason == "system_shallow_base_breakout"
    assert any(event["bar_index"] == now and event["event"] == "entry_rejected"
               and event["reason"] == "no_live_structural_risk_reward" for event in generated.audit)


def test_new_route_is_v3_only_and_boolean_validation_is_explicit():
    assert not SystemStrategy().combined_a_entry_enabled
    profile = whole_wave_profile({"scenarios": {"base": {"execution": {}}}})
    assert profile["strategy"]["combined_a_entry_enabled"]
    with pytest.raises(ValueError, match="combined A entry switch"):
        SystemStrategy(**dict(asdict(SystemStrategy()), combined_a_entry_enabled=1)).validate()
