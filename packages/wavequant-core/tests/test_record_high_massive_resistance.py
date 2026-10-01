"""An old-high volume bearish breakout reduces 30%; its next massive decline clears."""

from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path

import pytest

from wavequant.application.analytics.backtest import run_portfolio
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar, Signal
from wavequant.domain.strategies.pressure_exit import record_high_massive_resistance_history


def xianfeng_march() -> tuple[list[Bar], dict[str, int]]:
    raw = json.loads((Path(__file__).parent / "fixtures/xianfeng_2026_resistance.json").read_text(encoding="utf-8"))
    bars = [Bar(datetime.fromisoformat(day), raw["symbol"], *prices)
            for day, *prices in raw["bars"] if "2021-09-01" <= day <= "2022-03-23"]
    return bars, {bar.timestamp.date().isoformat(): i for i, bar in enumerate(bars)}


def test_xianfeng_old_resisted_high_and_close_above_it_still_reduce_then_clear():
    bars, dates = xianfeng_march()
    warning, clear = dates["2022-03-21"], dates["2022-03-22"]
    risks = record_high_massive_resistance_history(bars, StrategyConfig())
    first, second = risks[warning], risks[clear]
    assert first["reason"] == "record_high_massive_resistance_reduce_30"
    assert first["exit_target_fraction"] == .3
    assert first["record_high_date"] == "2021-09-22"
    assert first["record_high"] == 4.56
    assert first["observed_close"] == 5.07 > first["record_high"]
    assert first["record_volume_mean"] == 16_904_716
    assert first["record_warning_date"] == "2022-03-21"
    assert "higher_open_bearish_body" in first["record_resistance_patterns"]
    assert second["reason"] == "record_high_massive_followthrough_clear"
    assert second["exit_fraction"] == 1
    assert second["observed_volume"] == 152_565_213 < first["observed_volume"]
    assert second["observed_close"] == 4.42
    assert second["record_volume_mean"] == first["record_volume_mean"]
    assert second["record_warning_close"] == 5.07
    assert clear + 1 not in risks
    for end in (warning + 1, clear + 1):
        assert record_high_massive_resistance_history(bars[:end], StrategyConfig()) == {
            i: proof for i, proof in risks.items() if i < end}


@pytest.mark.parametrize("change", [
    {"volume": 33_809_431}, {"open": 5.0}, {"close": 5.34},
    {"open": 4.45, "high": 4.56, "low": 4.3, "close": 4.50},
])
def test_breakout_warning_needs_strict_old_high_cross_massive_volume_and_bearish_resistance(change):
    bars, dates = xianfeng_march()
    warning = dates["2022-03-21"]
    bars = bars[:warning + 1]
    bars[warning] = replace(bars[warning], **change)
    assert warning not in record_high_massive_resistance_history(bars, StrategyConfig())


@pytest.mark.parametrize("change", [
    {"volume": 33_809_431}, {"close": 5.07, "high": 5.10},
    {"open": 4.40},
])
def test_followthrough_needs_massive_volume_bearish_body_and_lower_close(change):
    bars, dates = xianfeng_march()
    clear = dates["2022-03-22"]
    bars[clear] = replace(bars[clear], **change)
    risks = record_high_massive_resistance_history(bars, StrategyConfig())
    assert dates["2022-03-21"] in risks
    assert clear not in risks
    assert dates["2022-03-23"] not in risks


def test_exact_volume_threshold_qualifies_and_reference_is_frozen():
    bars, dates = xianfeng_march()
    warning, clear = dates["2022-03-21"], dates["2022-03-22"]
    bars[warning] = replace(bars[warning], volume=33_809_432)
    bars[clear] = replace(bars[clear], volume=33_809_432)
    risks = record_high_massive_resistance_history(bars, StrategyConfig())
    assert risks[warning]["record_volume_ratio"] == 2
    assert risks[clear]["record_volume_ratio"] == 2


def test_next_session_bearish_resistance_can_arm_after_a_bullish_breakout():
    bars, dates = xianfeng_march()
    warning, response = dates["2022-03-21"], dates["2022-03-22"]
    bars[warning] = replace(bars[warning], open=4.78, close=5.20)
    bars[response + 1] = replace(bars[response + 1], open=4.40)
    risks = record_high_massive_resistance_history(bars, StrategyConfig())
    assert warning not in risks
    assert risks[response]["reason"] == "record_high_massive_resistance_reduce_30"
    assert risks[response]["record_breakout_date"] == "2022-03-21"
    assert risks[response]["record_warning_date"] == "2022-03-22"
    assert risks[response + 1]["reason"] == "record_high_massive_followthrough_clear"


def test_fresh_high_and_zero_preceding_volume_are_not_old_volume_breakouts():
    bars, dates = xianfeng_march()
    warning = dates["2022-03-21"]
    bars[warning - 2] = replace(bars[warning - 2], high=4.60)
    assert warning not in record_high_massive_resistance_history(bars, StrategyConfig())
    bars, dates = xianfeng_march()
    for i in range(warning - 20, warning):
        bars[i] = replace(bars[i], volume=0)
    assert warning not in record_high_massive_resistance_history(bars, StrategyConfig())


def test_third_session_resistance_is_outside_the_attack_response_window():
    bars, dates = xianfeng_march()
    attack = dates["2022-03-21"]
    bars[attack] = replace(bars[attack], open=4.78, close=5.20)
    bars[attack + 1] = replace(bars[attack + 1], open=4.40, close=4.85)
    bars[attack + 2] = replace(bars[attack + 2], open=4.40)
    risks = record_high_massive_resistance_history(bars, StrategyConfig())
    assert all(i not in risks for i in range(attack, attack + 3))


def test_configured_volume_ratio_and_lookback_apply_to_both_stages():
    bars, dates = xianfeng_march()
    warning, clear = dates["2022-03-21"], dates["2022-03-22"]
    risks = record_high_massive_resistance_history(bars, StrategyConfig(pressure_volume_ratio=10))
    assert warning in risks and clear not in risks
    assert risks[warning]["record_volume_threshold"] == 169_047_160
    assert warning not in record_high_massive_resistance_history(bars, StrategyConfig(pressure_lookback=20))


def test_structural_stop_is_not_downgraded_to_thirty_percent_reduction():
    bars, dates = xianfeng_march()
    entry, warning = dates["2022-03-18"], dates["2022-03-21"]
    bars[warning] = replace(bars[warning], low=2.9)
    signal = Signal(bars[entry].timestamp, bars[entry].symbol, entry, "LONG", bars[entry].close, 3.0,
                    "fixture", bars[entry].timestamp, 0, None, "fixture", 10.0)
    config = StrategyConfig(entry_at_close=True, pressure_adverse_exit=True, exit_on_target=False,
                            max_hold_bars=100)
    fills = [o for o in run_portfolio({bars[0].symbol: bars}, [signal], config).orders
             if o["status"] == "filled"]
    assert len(fills) == 2
    assert fills[-1]["reason"] == "structural_stop_observed"
    assert fills[-1]["remaining_quantity"] == 0


@pytest.mark.parametrize("capital", [100_000, 1_000])
def test_position_reduces_cumulative_thirty_and_clears_even_when_one_lot_cannot_reduce(capital):
    bars, dates = xianfeng_march()
    entry = dates["2022-03-18"]
    signal = Signal(bars[entry].timestamp, bars[entry].symbol, entry, "LONG", bars[entry].close, 3.0,
                    "fixture", bars[entry].timestamp, 0, None, "fixture", 10.0)
    config = StrategyConfig(initial_capital=capital, entry_at_close=True, pressure_adverse_exit=True,
                            exit_on_target=False, staged_exit_enabled=False, volume_down_exit=False,
                            max_hold_bars=100, max_participation=1, risk_fraction=1, max_position_weight=1,
                            slippage_bps_per_side=0)
    result = run_portfolio({bars[0].symbol: bars}, [signal], config)
    fills = [o for o in result.orders if o["status"] == "filled"]
    expected = [("BUY", "2022-03-18", "fixture"),
                ("SELL", "2022-03-21", "record_high_massive_resistance_reduce_30"),
                ("SELL", "2022-03-22", "record_high_massive_followthrough_clear")]
    if capital == 1_000:
        expected.pop(1)
        assert any(o["reason"] == "reduction_below_one_lot" for o in result.orders)
    else:
        assert fills[1]["quantity"] == (fills[0]["quantity"] * 30 // 10000) * 100
    assert [(o["side"], o["timestamp"][:10], o["reason"]) for o in fills] == expected
    assert fills[-1]["remaining_quantity"] == 0
    for end in (dates["2022-03-21"] + 1, dates["2022-03-22"] + 1):
        prefix = run_portfolio({bars[0].symbol: bars[:end]}, [signal], config)
        assert prefix.orders == [o for o in result.orders if o["timestamp"] <= bars[end - 1].timestamp.isoformat()]
    assert run_portfolio({bars[0].symbol: bars}, [signal], replace(config, pressure_adverse_exit=False)).trades == []
