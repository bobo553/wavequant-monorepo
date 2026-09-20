"""Targets measure entry reward; V3 holdings leave on exits rather than milestones."""

from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from wavequant.application.analytics.backtest import run_backtest
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar, Signal
from wavequant.domain.strategies.strategy_profiles import (
    WAVE_PROFILES,
    whole_wave_profile,
    research_profile,
    hierarchical_profile,
)


def sample():
    bars = [
        Bar(datetime(2026, 8, 4) + timedelta(days=i), "sz.300154", o, h, low, c, 10_000_000)
        for i, (o, h, low, c) in enumerate(
            [
                (10, 10.2, 9.8, 10),
                (10, 10.8, 9.9, 10.7),
                (10.8, 11.8, 10.5, 11.5),
                (11.6, 12.5, 11.3, 12.4),
                (12.5, 15.5, 12.3, 15),
                (15, 22, 14.9, 21),
            ]
        )
    ]
    signal = Signal(
        bars[0].timestamp, bars[0].symbol, 0, "LONG", 10, 9, "entry", bars[0].timestamp, 0, None, "fixture", 11
    )
    config = StrategyConfig(net_reward_risk_filter=False, max_hold_bars=100, slippage_bps_per_side=0)
    return bars, signal, config


def test_v3_holds_across_targets_without_exit_and_prefixes_agree():
    bars, signal, config = sample()
    config = replace(config, exit_on_target=False)
    result = run_backtest(bars, [signal], config)
    assert not result.trades
    assert result.open_positions[0]["pending_exit"] is None
    assert [o["side"] for o in result.orders] == ["BUY"]
    assert result.orders[0]["target_price"] == 11
    for cut in range(2, len(bars) + 1):
        prefix = run_backtest(bars[:cut], [signal], config)
        assert prefix.orders == [o for o in result.orders if o["timestamp"] <= bars[cut - 1].timestamp.isoformat()]


def test_legacy_still_sells_after_target_observed():
    bars, signal, config = sample()
    sell = run_backtest(bars, [signal], config).orders[-1]
    assert sell["side"] == "SELL"
    assert sell["reason"] == "target_observed"
    assert sell["timestamp"] == bars[3].timestamp.isoformat()


@pytest.mark.parametrize("reason", ["inverse_n_risk_exit", "last_rise_low_close_broken", "strict_structure_unresolved"])
def test_strategy_exit_after_target_still_executes_next_open(reason):
    bars, signal, config = sample()
    exit_signal = replace(signal, side="EXIT", timestamp=bars[3].timestamp, bar_index=3, reason=reason)
    result = run_backtest(bars, [signal, exit_signal], replace(config, exit_on_target=False))
    sell = result.orders[-1]
    assert sell["reason"] == reason
    assert sell["signal_timestamp"] == bars[3].timestamp.isoformat()
    assert sell["timestamp"] == bars[4].timestamp.isoformat()
    assert sell["decision_source"] == "strategy_exit_signal"
    assert sell["position_closed"] is True


@pytest.mark.parametrize("reason", ["structural_stop_observed", "time_exit"])
def test_risk_exits_remain_active_when_target_exits_disabled(reason):
    bars, signal, config = sample()
    config = replace(config, exit_on_target=False)
    if reason == "structural_stop_observed":
        bars[2] = replace(bars[2], low=8.9)
    else:
        config = replace(config, max_hold_bars=1)
    sell = run_backtest(bars, [signal], config).orders[-1]
    assert sell["reason"] == reason
    assert sell["timestamp"] == bars[3].timestamp.isoformat()


@pytest.mark.parametrize("variant", WAVE_PROFILES)
def test_all_v3_profiles_disable_target_exits_without_mutating_legacy(variant):
    legacy = {"scenarios": {"base": {"execution": {}}, "stress": {"execution": {"exit_on_target": True}}}}
    profile = whole_wave_profile(legacy, variant)
    assert all(s["execution"]["exit_on_target"] is False for s in profile["scenarios"].values())
    assert "target_observed_then_next_open" not in profile["definition"]["exits"]
    for maker in (research_profile, hierarchical_profile):
        assert maker(legacy)["scenarios"]["base"]["execution"].get("exit_on_target", True) is True
    assert legacy["scenarios"]["stress"]["execution"]["exit_on_target"] is True


@pytest.mark.parametrize("invalid", [0, 1, "false", None])
def test_target_exit_option_requires_boolean(invalid):
    with pytest.raises(ValueError, match="exit_on_target must be boolean"):
        StrategyConfig(exit_on_target=invalid).validate()
