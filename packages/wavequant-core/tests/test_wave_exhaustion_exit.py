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
    raw = json.loads((Path(__file__).parent / "fixtures/guilin_2026_exhaustion.json").read_text(encoding="utf-8"))
    bars = [Bar(datetime.fromisoformat(day), raw["symbol"], *values) for day, *values in raw["bars"]]
    dates = {b.timestamp.date().isoformat(): i for i, b in enumerate(bars)}
    attack = dates["2026-08-31"]
    events = [
        dict(event="wave_projection_ready", attack=attack, bar_index=dates["2026-09-07"], two_t=7.2889145677),
        dict(
            event="wave_projection_target_reached",
            attack=attack,
            bar_index=dates["2026-09-09"],
            two_t=7.2889145677,
            reached_stage="five_top",
            reached_target=8.4316681228,
        ),
    ]
    return bars, dates, events


def test_reached_target_alone_does_not_sell_but_double_shadows_reduce_and_engulf_clears():
    bars, dates, events = sample()
    config = StrategyConfig(wave_exhaustion_exit=True)
    assert observe_wave_exhaustion(bars, dates["2026-09-09"], events, config) is None
    assert observe_wave_exhaustion(bars, dates["2026-09-10"], events, config) is None
    reduced = observe_wave_exhaustion(bars, dates["2026-09-11"], events, config)
    assert reduced["exit_target_fraction"] == 0.8
    assert reduced["wave_reached_stage"] == "five_top"
    assert reduced["wave_reached_date"] == "2026-09-09"
    assert observe_wave_exhaustion(bars, dates["2026-09-11"], events, config, reduced=True) is None
    clear = observe_wave_exhaustion(bars, dates["2026-09-15"], events, config)
    assert clear["reason"] == "wave_bearish_engulf_clear"
    assert clear["exit_fraction"] == 1
    assert clear["observed_volume"] < clear["previous_volume"]


@pytest.mark.parametrize(
    "case", ["no_target", "future_target", "equal_volume", "small_range", "one_shadow", "invalidated"]
)
def test_exhaustion_requires_known_active_target_and_all_candle_conditions(case):
    bars, dates, events = sample()
    index = dates["2026-09-11"]
    if case == "no_target":
        events = []
    elif case == "future_target":
        events = [dict(e, bar_index=index + 1) for e in events]
    elif case == "equal_volume":
        bars[index] = replace(bars[index], volume=bars[index - 1].volume)
    elif case == "small_range":
        bars[index] = replace(bars[index], high=10.4, low=10.1)
    elif case == "one_shadow":
        bars[index] = replace(bars[index], low=bars[index].close)
    else:
        events.append(dict(event="wave_projection_invalidated", attack=events[0]["attack"], bar_index=index))
    result = observe_wave_exhaustion(bars, index, events, StrategyConfig())
    if case in ("equal_volume", "one_shadow"):
        # A long upper shadow after five-top reach stands on its own;
        # the double-shadow rule still requires growing volume.
        assert result["reason"] == "wave_target_upper_shadow_reduce"
    else:
        assert result is None


def test_daily_fills_reduce_original_holding_then_clear_and_prefix_matches():
    bars, dates, events = sample()
    decision = dates["2026-09-02"]
    signal = Signal(
        bars[decision].timestamp,
        bars[0].symbol,
        decision,
        "LONG",
        bars[decision].close,
        6,
        "fixture",
        bars[events[0]["attack"]].timestamp,
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
    result = run_portfolio({bars[0].symbol: bars}, **kwargs)
    buy, reduction, clear = [o for o in result.orders if o["status"] == "filled"]
    assert reduction["timestamp"] == reduction["signal_timestamp"] == bars[dates["2026-09-11"]].timestamp.isoformat()
    assert reduction["quantity"] == int(buy["quantity"] * 0.8 // config.lot_size) * config.lot_size
    assert clear["timestamp"] == clear["signal_timestamp"] == bars[dates["2026-09-15"]].timestamp.isoformat()
    assert clear["remaining_quantity"] == 0
    assert clear["quantity"] == reduction["remaining_quantity"]
    prefix = run_portfolio({bars[0].symbol: bars[: dates["2026-09-15"] + 1]}, **kwargs)
    assert result.orders == prefix.orders
    # An old/unrelated N's targets must never arm this holding's exit rules.
    wrong = [dict(e, attack=0) for e in events]
    unrelated = run_portfolio(
        {bars[0].symbol: bars}, signals=[signal], config=config, wave_events={bars[0].symbol: wrong}
    )
    assert not any(o["side"] == "SELL" for o in unrelated.orders)


def test_abnormal_warning_next_trading_session_lower_close_clears():
    bars, dates, events = sample()
    index = dates["2026-09-11"] + 1
    close = bars[index - 1].close * 0.99
    bars[index] = replace(
        bars[index], close=close, low=min(bars[index].low, close), high=max(bars[index].high, close), volume=1
    )
    result = observe_wave_exhaustion(bars, index, events, StrategyConfig(), reduced=True)
    assert result["reason"] == "wave_abnormal_followthrough_clear"
    assert result["abnormal_date"] == "2026-09-11"
    assert result["exit_fraction"] == 1
