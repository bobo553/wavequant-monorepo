"""Target-stage exhaustion exits on the Xinhua Winshare January 2026 candles."""

import json
from datetime import datetime
from pathlib import Path

import pytest

from wavequant.application.analytics.backtest import run_portfolio
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar, Signal
from wavequant.domain.strategies.wave_exhaustion_exit import observe_wave_exhaustion


def sample():
    fixture = Path(__file__).parent / "fixtures/xinhuawenxuan_2026_trend.json"
    raw = json.loads(fixture.read_text(encoding="utf-8"))
    bars = [
        Bar(datetime.fromisoformat(day), raw["symbol"], *prices)
        for day, *prices in raw["bars"]
        if "2026-01-12" <= day <= "2026-01-16"
    ]
    events = [
        dict(
            event="wave_projection_ready",
            attack=1,
            bar_index=1,
            two_t=18.95657459062264,
        ),
        dict(
            event="wave_projection_target_reached",
            attack=1,
            bar_index=2,
            reached_stage="five_top",
            reached_target=20.523233647698888,
        ),
    ]
    return bars, events


@pytest.mark.parametrize("stage", ["two_t", "five_top", "ten_full"])
def test_two_t_or_higher_long_upper_shadow_reduces_then_first_lower_close_clears(stage):
    bars, events = sample()
    if stage == "two_t":
        events = [events[0]]
    else:
        events[1]["reached_stage"] = stage
    config = StrategyConfig(wave_exhaustion_exit=True, exit_on_target=False)
    reduction = observe_wave_exhaustion(bars, 2, events, config, entry_index=1)
    assert reduction is not None
    assert reduction["reason"] == "wave_target_upper_shadow_reduce"
    assert reduction["wave_reached_stage"] == stage
    assert reduction["wave_reached_date"] == (
        "2026-01-13" if stage == "two_t" else "2026-01-14"
    )
    assert reduction["exit_target_fraction"] == 0.8
    assert (
        observe_wave_exhaustion(bars, 3, events, config, reduced=True, entry_index=1)
        is None
    )
    clear = observe_wave_exhaustion(
        bars, 4, events, config, reduced=True, entry_index=1
    )
    assert clear is not None
    assert clear["reason"] == "wave_abnormal_followthrough_clear"
    assert clear["abnormal_date"] == "2026-01-14"
    assert clear["previous_close"] == bars[3].close
    assert clear["observed_close"] == bars[4].close
    unfilled_clear = observe_wave_exhaustion(bars, 4, events, config, entry_index=1)
    assert unfilled_clear is not None and unfilled_clear["exit_fraction"] == 1.0

    signal = Signal(
        bars[1].timestamp,
        bars[1].symbol,
        1,
        "LONG",
        bars[1].close,
        17,
        "fixture",
        bars[1].timestamp,
        0,
        None,
        "fixture",
        30,
    )
    account = StrategyConfig(
        wave_exhaustion_exit=True,
        exit_on_target=False,
        entry_at_close=True,
        max_hold_bars=100,
        liquidity_lookback=1,
        max_participation=1,
        slippage_bps_per_side=0,
    )
    kwargs = dict(
        signals=[signal], config=account, wave_events={bars[0].symbol: events}
    )
    result = run_portfolio({bars[0].symbol: bars}, **kwargs)
    fills = [order for order in result.orders if order["status"] == "filled"]
    assert [(order["timestamp"][:10], order["reason"]) for order in fills] == [
        ("2026-01-13", "fixture"),
        ("2026-01-14", "wave_target_upper_shadow_reduce"),
        ("2026-01-16", "wave_abnormal_followthrough_clear"),
    ], result.orders
    assert fills[-1]["remaining_quantity"] == 0
    prefix = run_portfolio({bars[0].symbol: bars[:3]}, **kwargs)
    assert prefix.orders == [
        order for order in result.orders if order["timestamp"][:10] <= "2026-01-14"
    ]


def test_five_top_upper_shadow_requires_reached_target_and_half_range():
    bars, events = sample()
    config = StrategyConfig(wave_exhaustion_exit=True)
    assert observe_wave_exhaustion(bars, 2, [], config, entry_index=1) is None
    bars[2] = Bar(
        bars[2].timestamp, bars[2].symbol, 19.97, 21.41, 19.78, 20.70, bars[2].volume
    )
    assert observe_wave_exhaustion(bars, 2, events, config, entry_index=1) is None
