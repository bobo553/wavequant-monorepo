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
    raw = json.loads((Path(__file__).parent / "fixtures/huaci_2026_exhaustion.json").read_text(encoding="utf-8"))
    bars = [Bar(datetime.fromisoformat(day), raw["symbol"], *values) for day, *values in raw["bars"]]
    dates = {str(b.timestamp.date()): i for i, b in enumerate(bars)}
    return bars, dates, [e for e in raw["events"] if e["event"] == "wave_projection_ready"]


def test_same_day_two_t_upper_rejection_despite_positive_daily_return():
    bars, dates, events = sample()
    index = dates["2026-08-11"]
    decision = observe_wave_exhaustion(bars, index, events, StrategyConfig())
    assert decision["reason"] == "wave_upper_rejection_reduce"
    assert decision["wave_n_date"] == "2026-08-03"
    assert decision["wave_reached_date"] == "2026-08-11"
    assert decision["wave_reached_stage"] == "two_t"
    assert decision["wave_reached_price"] == pytest.approx(17.4890491132)
    assert bars[index].close > bars[index - 1].close
    assert decision["wave_body_fraction"] < 0.05
    assert decision["wave_upper_shadow_fraction"] == pytest.approx(0.4496124031)
    assert observe_wave_exhaustion(bars, index, events, StrategyConfig(), reduced=True) is None
    assert observe_wave_exhaustion(bars, index - 1, events, StrategyConfig()) is None


@pytest.mark.parametrize(
    "case",
    [
        "no_target",
        "future_target",
        "invalidated",
        "equal_volume",
        "no_new_high",
        "bullish",
        "short_upper",
        "long_lower",
        "small_range",
    ],
)
def test_target_and_joint_candle_conditions_are_required(case):
    bars, dates, events = sample()
    index = dates["2026-08-11"]
    if case == "no_target":
        events = []
    elif case == "future_target":
        events = [dict(e, bar_index=index + 1) for e in events]
    elif case == "invalidated":
        events = events + [dict(event="wave_projection_invalidated", bar_index=index)]
    elif case == "equal_volume":
        bars[index] = replace(bars[index], volume=bars[index - 1].volume)
    elif case == "no_new_high":
        bars[index - 1] = replace(bars[index - 1], high=bars[index].high)
    elif case == "bullish":
        bars[index] = replace(bars[index], open=bars[index].close, close=bars[index].open)
    elif case == "short_upper":
        bars[index] = replace(bars[index], open=17.7)
    elif case == "long_lower":
        bars[index] = replace(bars[index], low=16.6)
    else:
        bars[index] = replace(bars[index], high=18.05)
    assert observe_wave_exhaustion(bars, index, events, StrategyConfig()) is None


def test_real_candles_execute_same_close_once_and_preserve_prefix_and_n_ownership():
    bars, dates, events = sample()
    decision = dates["2026-08-05"]
    signal = Signal(
        bars[decision].timestamp,
        bars[0].symbol,
        decision,
        "LONG",
        bars[decision].close,
        15.11330455893629,
        "fixture",
        bars[dates["2026-08-03"]].timestamp,
        0,
        None,
        "fixture",
        30,
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
    end = dates["2026-08-11"] + 1
    prefix = run_portfolio({bars[0].symbol: bars[:end]}, **kwargs)
    full = run_portfolio({bars[0].symbol: bars}, **kwargs)
    assert prefix.orders == [o for o in full.orders if o["timestamp"][:10] <= "2026-08-11"]
    buy, reduction = [o for o in prefix.orders if o["status"] == "filled"]
    assert reduction["reason"] == "wave_upper_rejection_reduce"
    assert reduction["timestamp"] == reduction["signal_timestamp"] == bars[end - 1].timestamp.isoformat()
    assert reduction["exit_target_fraction"] == 0.8
    assert reduction["quantity"] == int(buy["quantity"] * 0.8 // config.lot_size) * config.lot_size
    repeated = bars[:end] + [replace(bars[end], open=19, high=20, low=18, close=18.2, volume=bars[end - 1].volume * 2)]
    assert run_portfolio({bars[0].symbol: repeated}, **kwargs).orders == prefix.orders
    wrong = [dict(e, attack=0) for e in events]
    unrelated = run_portfolio(
        {bars[0].symbol: bars[:end]}, signals=[signal], config=config, wave_events={bars[0].symbol: wrong}
    )
    assert not any(o["side"] == "SELL" for o in unrelated.orders)


@pytest.mark.parametrize("reduced", [False, True])
def test_next_session_lower_close_clears_even_without_partial_fill_or_volume(reduced):
    bars, dates, events = sample()
    index = dates["2026-08-12"]
    result = observe_wave_exhaustion(bars, index, events, StrategyConfig(), reduced=reduced)
    assert result["reason"] == "wave_abnormal_followthrough_clear"
    assert result["exit_fraction"] == 1
    assert "exit_target_fraction" not in result
    assert result["abnormal_date"] == "2026-08-11"
    assert result["abnormal_close"] == bars[index - 1].close
    assert result["observed_close"] == bars[index].close
    assert result["observed_volume"] < result["previous_volume"]
    assert result["wave_n_date"] == "2026-08-03"
    assert result["execution_model"] == "same_day_close"


@pytest.mark.parametrize(
    "case", ["equal_close", "higher_close", "no_target", "target_only_today", "no_warning", "later_session"]
)
def test_followthrough_requires_prior_known_warning_and_strict_next_session_close(case):
    bars, dates, events = sample()
    index = dates["2026-08-12"]
    if case in ("equal_close", "higher_close"):
        close = bars[index - 1].close + (0.01 if case == "higher_close" else 0)
        bars[index] = replace(bars[index], close=close, high=max(close, bars[index].high))
    elif case == "no_target":
        events = []
    elif case == "target_only_today":
        events = [dict(e, bar_index=index) for e in events]
    elif case == "no_warning":
        bars[index - 1] = replace(bars[index - 1], volume=bars[index - 2].volume)
    else:
        index += 1
    assert observe_wave_exhaustion(bars, index, events, StrategyConfig(), reduced=True) is None


def test_followthrough_fills_remaining_holding_on_august_12_and_prefix_matches():
    bars, dates, events = sample()
    decision = dates["2026-08-05"]
    signal = Signal(
        bars[decision].timestamp,
        bars[0].symbol,
        decision,
        "LONG",
        bars[decision].close,
        15.11330455893629,
        "fixture",
        bars[dates["2026-08-03"]].timestamp,
        0,
        None,
        "fixture",
        30,
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
    end = dates["2026-08-12"] + 1
    prefix = run_portfolio({bars[0].symbol: bars[:end]}, **kwargs)
    full = run_portfolio({bars[0].symbol: bars}, **kwargs)
    assert prefix.orders == full.orders
    buy, reduction, clear = [o for o in full.orders if o["status"] == "filled"]
    assert clear["reason"] == "wave_abnormal_followthrough_clear"
    assert clear["timestamp"] == clear["signal_timestamp"] == bars[end - 1].timestamp.isoformat()
    assert clear["quantity"] == reduction["remaining_quantity"]
    assert clear["remaining_quantity"] == 0
    assert clear["position_closed"] is True
    # An unsellable warning day cannot cancel the next session's full risk exit.
    blocked = list(bars[:end])
    blocked[end - 2] = replace(blocked[end - 2], sellable=False)
    result = run_portfolio({bars[0].symbol: blocked}, **kwargs)
    filled = [o for o in result.orders if o["side"] == "SELL" and o["status"] == "filled"]
    assert filled[-1]["remaining_quantity"] == 0
    assert filled[-1]["reason"] == "wave_abnormal_followthrough_clear"
