"""A massive gap-up reversal clears a holding before partial volume or wave exits."""

from dataclasses import replace
from datetime import datetime

import pytest

from wavequant.application.analytics.backtest import run_portfolio
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar, Signal
from wavequant.domain.strategies.staged_exit import StagedExitState, observe_volume_down_exit
from wavequant.domain.strategies.wave_exhaustion_exit import observe_wave_exhaustion


def guofang_august_bars():
    rows = [
        ("2020-07-27", 5.37, 5.44, 5.28, 5.32, 4_295_800),
        ("2020-07-28", 5.32, 5.43, 5.32, 5.43, 5_981_400),
        ("2020-07-29", 5.41, 5.64, 5.36, 5.57, 10_663_400),
        ("2020-07-30", 5.56, 5.69, 5.53, 5.64, 11_290_900),
        ("2020-07-31", 5.64, 5.78, 5.58, 5.67, 11_205_500),
        ("2020-08-03", 5.73, 5.82, 5.65, 5.82, 13_186_536),
        ("2020-08-04", 5.78, 5.82, 5.72, 5.80, 9_423_000),
        ("2020-08-05", 5.80, 6.23, 5.73, 6.11, 11_996_775),
        ("2020-08-06", 5.99, 6.14, 5.92, 6.09, 12_981_873),
        ("2020-08-07", 6.02, 6.27, 5.88, 6.27, 16_943_324),
        ("2020-08-10", 6.22, 6.54, 6.10, 6.54, 19_679_943),
        ("2020-08-11", 6.39, 6.78, 6.28, 6.74, 28_644_174),
        ("2020-08-12", 7.42, 7.42, 7.42, 7.42, 44_329_073),
        ("2020-08-13", 8.16, 8.16, 8.16, 8.16, 2_711_329),
        ("2020-08-14", 8.97, 8.97, 7.34, 7.34, 53_163_008),
    ]
    return [Bar(datetime.fromisoformat(day), "sh.601086", *prices) for day, *prices in rows]


def test_guofang_massive_gap_reversal_clears_instead_of_partial_wave_exit():
    bars = guofang_august_bars()
    state = StagedExitState()
    assert observe_volume_down_exit(bars, 13, state) is None
    clear = observe_volume_down_exit(bars, 14, state)
    assert clear["reason"] == "volume_massive_gap_reversal_clear"
    assert clear["exit_fraction"] == 1.0
    assert clear["execution_model"] == "same_day_close"
    assert clear["observed_volume"] == 53_163_008
    assert clear["observed_open"] > clear["previous_high"]
    assert clear["observed_close"] < clear["previous_low"]
    assert clear["massive_volume_multiple"] > 3
    assert clear["bearish_body_fraction"] > 0.18

    wave_event = dict(event="wave_projection_ready", attack=3, bar_index=12, two_t=7.4)
    wave = observe_wave_exhaustion(bars, 14, [wave_event], StrategyConfig(wave_exhaustion_exit=True))
    assert wave["reason"] == "wave_gap_reversal_reduce"
    signal = Signal(
        bars[3].timestamp,
        bars[3].symbol,
        3,
        "LONG",
        bars[3].close,
        5.1,
        "system_wave_push_gap",
        bars[3].timestamp,
        0,
        None,
        "fixture",
        10.0,
    )
    config = StrategyConfig(
        volume_down_exit=True,
        wave_exhaustion_exit=True,
        entry_at_close=True,
        exit_on_target=False,
        max_hold_bars=100,
        slippage_bps_per_side=0,
        max_participation=1,
    )
    result = run_portfolio({bars[0].symbol: bars}, [signal], config, wave_events={bars[0].symbol: [wave_event]})
    filled = [order for order in result.orders if order["status"] == "filled"]
    assert [(order["timestamp"][:10], order["reason"]) for order in filled] == [
        ("2020-07-30", "system_wave_push_gap"),
        ("2020-08-14", "volume_massive_gap_reversal_clear"),
    ]
    assert filled[-1]["quantity"] == filled[0]["quantity"]
    assert filled[-1]["remaining_quantity"] == 0
    assert filled[-1]["signal_timestamp"] == filled[-1]["timestamp"]
    prefix = run_portfolio({bars[0].symbol: bars[:14]}, [signal], config, wave_events={bars[0].symbol: [wave_event]})
    assert prefix.orders == [order for order in result.orders if order["timestamp"] < bars[14].timestamp.isoformat()]


@pytest.mark.parametrize(
    "case", ["insufficient_history", "ordinary_volume", "not_record_volume", "no_gap", "no_close_break", "small_body"]
)
def test_massive_gap_reversal_requires_every_global_risk_condition(case):
    bars = guofang_august_bars()
    if case == "insufficient_history":
        bars = bars[-10:]
    elif case == "ordinary_volume":
        bars[-1] = replace(bars[-1], volume=30_000_000)
    elif case == "not_record_volume":
        bars[-3] = replace(bars[-3], volume=60_000_000)
    elif case == "no_gap":
        bars[-1] = replace(bars[-1], open=8.16, high=8.97)
    elif case == "no_close_break":
        bars[-1] = replace(bars[-1], low=8.16, close=8.16)
    else:
        bars[-1] = replace(bars[-1], open=8.17, high=8.97, low=8.15, close=8.15)
    decision = observe_volume_down_exit(bars, len(bars) - 1, StagedExitState())
    assert decision is None or decision["reason"] != "volume_massive_gap_reversal_clear"
