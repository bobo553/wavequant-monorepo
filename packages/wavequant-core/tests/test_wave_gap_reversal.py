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
    raw = json.loads((Path(__file__).parent / "fixtures/ruiling_2024_exhaustion.json").read_text(encoding="utf-8"))
    bars = [Bar(datetime.fromisoformat(day), raw["symbol"], *values) for day, *values in raw["bars"]]
    dates = {str(b.timestamp.date()): i for i, b in enumerate(bars)}
    events = [
        dict(event="wave_projection_ready", attack=dates["2024-09-24"], bar_index=dates["2024-09-30"], two_t=7.20377),
        dict(
            event="wave_projection_target_reached",
            attack=dates["2024-09-24"],
            bar_index=dates["2024-10-08"],
            reached_stage="five_top",
            reached_target=8.48611761,
        ),
    ]
    return bars, dates, events


def test_positive_daily_return_can_still_be_target_stage_gap_reversal():
    bars, dates, events = sample()
    index = dates["2024-10-08"]
    result = observe_wave_exhaustion(bars, index, events, StrategyConfig())
    assert result["reason"] == "wave_gap_reversal_reduce"
    assert result["exit_target_fraction"] == 0.8
    assert result["wave_reached_stage"] == "five_top"
    assert bars[index].close > bars[index - 1].close
    assert result["wave_body_fraction"] == pytest.approx(0.06986, abs=0.00001)
    assert observe_wave_exhaustion(bars, index, events, StrategyConfig(), reduced=True) is None


@pytest.mark.parametrize("case", ["no_target", "future_target", "equal_volume", "no_gap", "small_body", "long_upper"])
def test_gap_reversal_requires_known_target_and_joint_conditions(case):
    bars, dates, events = sample()
    index = dates["2024-10-08"]
    if case == "no_target":
        events = []
    elif case == "future_target":
        events = [dict(e, bar_index=index + 1) for e in events]
    elif case == "equal_volume":
        bars[index] = replace(bars[index], volume=bars[index - 1].volume)
    elif case == "no_gap":
        bars[index - 1] = replace(bars[index - 1], high=bars[index].open)
    elif case == "small_body":
        bars[index] = replace(bars[index], close=bars[index].open - 0.01)
    else:
        bars[index] = replace(bars[index], high=9.6)
    assert observe_wave_exhaustion(bars, index, events, StrategyConfig()) is None


def test_gap_reversal_executes_same_close_and_prefix_is_identical():
    bars, dates, events = sample()
    decision = dates["2024-09-26"]
    signal = Signal(
        bars[decision].timestamp,
        bars[0].symbol,
        decision,
        "LONG",
        bars[decision].close,
        5,
        "fixture",
        bars[dates["2024-09-24"]].timestamp,
        0,
        None,
        "fixture",
        20,
    )
    config = StrategyConfig(
        wave_exhaustion_exit=True,
        exit_on_target=False,
        risk_fraction=0.1,
        max_position_weight=0.8,
        max_participation=1,
        slippage_bps_per_side=0,
    )
    kwargs = dict(signals=[signal], config=config, wave_events={bars[0].symbol: events})
    end = dates["2024-10-08"] + 1
    prefix = run_portfolio({bars[0].symbol: bars[:end]}, **kwargs)
    full = run_portfolio({bars[0].symbol: bars}, **kwargs)
    assert prefix.orders == [o for o in full.orders if o["timestamp"][:10] <= "2024-10-08"]
    buy, reduction = [o for o in prefix.orders if o["status"] == "filled"]
    assert reduction["timestamp"] == reduction["signal_timestamp"] == bars[end - 1].timestamp.isoformat()
    assert reduction["exit_target_fraction"] == 0.8
    assert reduction["quantity"] == int(buy["quantity"] * 0.8 // config.lot_size) * config.lot_size
    repeated = bars[:end] + [
        replace(bars[end], open=10.5, high=10.5, low=9.4, close=9.7, volume=bars[end - 1].volume * 2)
    ]
    assert run_portfolio({bars[0].symbol: repeated}, **kwargs).orders == prefix.orders
