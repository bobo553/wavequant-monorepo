"""C-wave target risk for the 2024-04-01 Guofang entry."""

from dataclasses import replace
from datetime import datetime

import pytest

from wavequant.application.analytics.backtest import run_portfolio
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar, Signal
from wavequant.domain.strategies.wave_exhaustion_exit import observe_wave_exhaustion


def guofang_c_wave():
    # TDX raw prices and volumes. A=2024-02-29 low 4.02 to 03-22 high 5.61;
    # B=03-27 low 4.72; C observations are 5.70262 and 6.31.
    rows = [
        ("2024-04-01", 4.91, 4.99, 4.88, 4.97, 21_046_000),
        ("2024-04-02", 4.99, 5.00, 4.90, 4.92, 16_657_800),
        ("2024-04-03", 4.95, 5.02, 4.81, 4.99, 22_117_758),
        ("2024-04-08", 5.02, 5.48, 5.00, 5.28, 48_798_458),
        ("2024-04-09", 5.17, 5.29, 5.02, 5.28, 30_616_846),
        ("2024-04-10", 5.21, 5.80, 5.18, 5.45, 53_138_088),
        ("2024-04-11", 5.35, 5.66, 5.24, 5.60, 39_393_600),
        ("2024-04-12", 5.48, 6.03, 5.46, 5.62, 50_462_700),
        ("2024-04-15", 5.52, 5.68, 5.06, 5.06, 39_859_464),
    ]
    bars = [Bar(datetime.fromisoformat(day), "sh.601086", *prices) for day, *prices in rows]
    event = dict(event="wave_c_entry", attack=0, bar_index=0, owner_signal_index=0,
                 c_0618_target=5.70262, equal_target=6.31,
                 a_origin=4.02, a_high=5.61, b_low=4.72)
    return bars, event


def test_guofang_c_0618_bearish_reduction_then_lower_low_close_volume_clear():
    bars, event = guofang_c_wave()
    config = StrategyConfig(wave_exhaustion_exit=True, exit_on_target=False)
    assert all(observe_wave_exhaustion(bars[:i + 1], i, [event], config,
                                       entry_index=0, signal_index=0) is None for i in range(1, 5))
    reduction = observe_wave_exhaustion(bars[:6], 5, [event], config,
                                        entry_index=0, signal_index=0)
    assert reduction["reason"] == "wave_c_target_bearish_reduce"
    assert reduction["exit_target_fraction"] == 0.7
    assert reduction["wave_reached_stage"] == "c_0618"
    assert reduction["wave_reached_date"] == "2024-04-10"
    assert reduction["wave_reached_price"] == pytest.approx(5.70262)
    assert "long_upper_shadow" in reduction["wave_bearish_patterns"]
    assert all(observe_wave_exhaustion(bars[:i + 1], i, [event], config, reduced=True,
                                       entry_index=0, signal_index=0) is None for i in (6, 7))
    clear = observe_wave_exhaustion(bars, 8, [event], config, reduced=True,
                                    entry_index=0, signal_index=0)
    assert clear["reason"] == "wave_c_target_lower_low_close_volume_clear"
    assert clear["wave_reached_stage"] == "c_0618"
    assert clear["observed_low"] < clear["previous_low"]
    assert clear["observed_close"] < clear["previous_close"]
    assert clear["bearish_reference_date"] == "2024-04-02"
    assert clear["observed_volume"] > clear["bearish_reference_volume"]
    assert observe_wave_exhaustion(bars, 8, [event], config,
                                   entry_index=0, signal_index=0)["reason"] == clear["reason"]

    prior = Bar(datetime.fromisoformat("2024-03-29"), bars[0].symbol,
                4.72, 4.85, 4.70, 4.80, 18_000_000)
    history = [prior, *bars]
    owned = dict(event, attack=1, bar_index=1, owner_signal_index=1)
    signal = Signal(bars[0].timestamp, bars[0].symbol, 1, "LONG", bars[0].close,
                    4.60, "system_wave_push_gap", bars[0].timestamp, 1, None,
                    "fixture", event["equal_target"])
    account = StrategyConfig(wave_exhaustion_exit=True, exit_on_target=False,
                             entry_at_close=True, max_hold_bars=100,
                             slippage_bps_per_side=0, max_participation=1)
    result = run_portfolio({bars[0].symbol: history}, [signal], account,
                           wave_events={bars[0].symbol: [owned]})
    filled = [order for order in result.orders if order["status"] == "filled"]
    assert [(order["timestamp"][:10], order["reason"]) for order in filled] == [
        ("2024-04-01", "system_wave_push_gap"),
        ("2024-04-10", "wave_c_target_bearish_reduce"),
        ("2024-04-15", "wave_c_target_lower_low_close_volume_clear"),
    ]
    assert filled[1]["exit_target_fraction"] == 0.7
    assert filled[2]["remaining_quantity"] == 0


def test_c_equal_target_uses_same_risk_rule_when_reached():
    bars, event = guofang_c_wave()
    bars[7] = replace(bars[7], high=6.35)
    config = StrategyConfig(wave_exhaustion_exit=True)
    warning = observe_wave_exhaustion(bars[:8], 7, [event], config,
                                      entry_index=0, signal_index=0)
    assert warning["wave_reached_stage"] == "c_equal"
    assert warning["wave_reached_date"] == "2024-04-12"
    assert warning["wave_reached_price"] == pytest.approx(6.31)
    clear = observe_wave_exhaustion(bars, 8, [event], config, reduced=True,
                                    entry_index=0, signal_index=0)
    assert clear["wave_reached_stage"] == "c_equal"
    assert clear["reason"] == "wave_c_target_lower_low_close_volume_clear"


@pytest.mark.parametrize("change", [
    {"low": 5.46, "close": 5.50},
    {"close": 5.62},
    {"volume": 16_657_800},
])
def test_c_full_clear_requires_strict_lower_low_close_and_prior_bearish_volume(change):
    bars, event = guofang_c_wave()
    bars[8] = replace(bars[8], **change)
    decision = observe_wave_exhaustion(bars, 8, [event], StrategyConfig(),
                                       reduced=True, entry_index=0, signal_index=0)
    assert decision is None


def test_c_exit_needs_owned_reached_entry_target():
    bars, event = guofang_c_wave()
    config = StrategyConfig()
    assert observe_wave_exhaustion(bars, 8, [event], config,
                                   entry_index=0, signal_index=1) is None
    event["c_0618_target"], event["equal_target"] = 6.5, 7.0
    assert observe_wave_exhaustion(bars, 8, [event], config,
                                   entry_index=0, signal_index=0) is None


def test_frozen_c_entry_requires_volume_even_when_legacy_equal_warning_exists():
    bars, c_entry = guofang_c_wave()
    bars[8] = replace(bars[8], volume=16_657_800)
    ordinary = dict(event="wave_ordinary_entry", attack=0, bar_index=0,
                    target=5.70262, one_p=5.3, two_t=5.8,
                    a_origin=4.62738, a_high=5.61, b_low=4.72)
    config = StrategyConfig()
    assert observe_wave_exhaustion(bars, 8, [ordinary], config,
                                   entry_index=0, signal_index=0)["reason"] == "wave_ordinary_equal_lower_close_clear"
    assert observe_wave_exhaustion(bars, 8, [ordinary, c_entry], config,
                                   entry_index=0, signal_index=0, reduced=True) is None
