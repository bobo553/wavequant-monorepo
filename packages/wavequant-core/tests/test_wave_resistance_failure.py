from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path

import pytest

from wavequant.application.analytics.backtest import run_portfolio
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar, Signal
from wavequant.domain.strategies.wave_exhaustion_exit import observe_wave_exhaustion


def sample():
    raw = json.loads((Path(__file__).parent / "fixtures/lexin_2026_squeeze.json").read_text(encoding="utf-8"))
    bars = [Bar(datetime.fromisoformat(day), raw["symbol"], *values) for day, *values in raw["bars"]]
    dates = {b.timestamp.date().isoformat(): i for i, b in enumerate(bars)}
    events = [
        dict(
            event="wave_projection_target_reached",
            attack=dates["2021-12-20"],
            bar_index=dates["2021-12-30"],
            reached_stage="five_top",
            reached_target=16.9968,
        )
    ]
    return bars, dates["2022-01-18"], events


def test_lower_open_bearish_failure_clears_target_stage_remaining_position_causally():
    bars, index, events = sample()
    config = StrategyConfig()
    clear = observe_wave_exhaustion(bars, index, events, config, reduced=True)
    assert clear is not None
    assert clear["reason"] == "wave_bull_resistance_failed_clear"
    assert clear["exit_fraction"] == 1
    assert clear["resistance_date"] == "2022-01-17"
    assert clear["resistance_virtual_low"] == pytest.approx(15.704244)
    assert clear["observed_open"] < clear["previous_close"]
    assert clear["observed_volume"] < clear["previous_volume"]
    assert clear == observe_wave_exhaustion(bars[: index + 1], index, events, config, reduced=True)


@pytest.mark.parametrize("case", ["no_target", "future_target", "touch_low", "small_body", "no_resistance"])
def test_target_resistance_failure_requires_known_target_large_body_and_strict_break(case):
    bars, index, events = sample()
    if case == "no_target":
        events = []
    elif case == "future_target":
        events = [dict(events[0], bar_index=index + 1)]
    elif case == "touch_low":
        bars[index] = replace(bars[index], close=bars[index - 1].low)
    elif case == "small_body":
        bars[index] = replace(bars[index], open=bars[index].close + 0.1)
    else:
        bars[index - 1] = replace(bars[index - 1], open=bars[index - 2].close)
    assert observe_wave_exhaustion(bars, index, events, StrategyConfig()) is None


def test_existing_position_is_fully_sold_on_failure_day_and_prefix_matches():
    bars, index, events = sample()
    decision = index - 4
    signal = Signal(
        bars[decision].timestamp,
        bars[0].symbol,
        decision,
        "LONG",
        bars[decision].close,
        10,
        "fixture",
        bars[events[0]["attack"]].timestamp,
        0,
        None,
        "fixture",
        30,
    )
    config = StrategyConfig(
        wave_exhaustion_exit=True,
        exit_on_target=False,
        risk_fraction=0.1,
        max_position_weight=0.0025,
        max_participation=1,
        slippage_bps_per_side=0,
    )
    kwargs = dict(signals=[signal], config=config, wave_events={bars[0].symbol: events})
    full = run_portfolio({bars[0].symbol: bars[: index + 6]}, **kwargs)
    buy, clear = [o for o in full.orders if o["status"] == "filled"]
    assert buy["quantity"] == config.lot_size
    assert clear["timestamp"] == clear["signal_timestamp"] == bars[index].timestamp.isoformat()
    assert clear["reason"] == "wave_bull_resistance_failed_clear"
    assert clear["quantity"] == buy["quantity"]
    assert clear["remaining_quantity"] == 0
    prefix = run_portfolio({bars[0].symbol: bars[: index + 1]}, **kwargs)
    assert full.orders == prefix.orders
