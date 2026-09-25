from dataclasses import replace
from datetime import datetime, timedelta
import json
from pathlib import Path

import pytest

from wavequant.application.analytics.backtest import run_portfolio
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar, Signal
from wavequant.domain.strategies.pressure_exit import pressure_exit_history


def sample():
    bars = [Bar(datetime(2024, 1, 1) + timedelta(days=i), "TEST", 9, 9.2, 8.8, 9, 10000) for i in range(24)]
    bars[20] = replace(bars[20], open=10.5, high=11, low=9, close=9.2, volume=50000)
    bars[21] = replace(bars[21], open=9.2, high=9.4, low=9, close=9.3)
    bars[22] = replace(bars[22], open=9.4, high=10, low=9.4, close=9.9)
    bars[23] = replace(bars[23], open=9.9, high=10.3, low=9.85, close=9.95)
    return bars, [None] * 22 + [22, 22]


@pytest.mark.parametrize("pattern", ["shadow", "close", "low"])
def test_pressure_any_adverse_pattern_exits_after_n(pattern):
    bars, context = sample()
    if pattern == "close":
        bars[23] = replace(bars[23], open=10, high=10.1, low=9.7, close=9.8)
    if pattern == "low":
        bars[23] = replace(bars[23], open=9.9, high=10, low=9.3, close=9.95)
    result = pressure_exit_history(bars, context, StrategyConfig())
    assert list(result) == [23]
    assert result[23]["pressure_date"] == bars[20].timestamp.date().isoformat()
    assert result[23]["exit_fraction"] == 1
    assert pressure_exit_history(bars[:23], context[:23], StrategyConfig()) == {}


@pytest.mark.parametrize("case", ["no_n", "below_zone", "broken", "low_volume", "small_body", "expired", "favorable"])
def test_no_pressure_exit_without_qualifying_context(case):
    bars, context = sample()
    config = StrategyConfig()
    if case == "no_n":
        context = [None] * len(bars)
    elif case == "below_zone":
        bars[22] = replace(bars[22], open=9.1, high=9.7, low=9, close=9.6)
        bars[23] = replace(bars[23], open=9.5, high=9.7, low=9.4, close=9.5)
    elif case == "broken":
        bars[21] = replace(bars[21], high=11.3, close=11.2)
    elif case == "low_volume":
        bars[20] = replace(bars[20], volume=19999)
    elif case == "small_body":
        bars[20] = replace(bars[20], close=10.4)
    elif case == "expired":
        config = replace(config, pressure_lookback=1)
    else:
        bars[23] = replace(bars[23], open=9.9, high=10.3, low=9.85, close=10.25)
    assert pressure_exit_history(bars, context, config) == {}


def test_guilin_pressure_exit_and_daily_execution_are_causal():
    raw = json.loads((Path(__file__).parent / "fixtures/guilin_2024_pressure.json").read_text(encoding="utf-8"))
    bars = [Bar(datetime.fromisoformat(day), raw["symbol"], *values) for day, *values in raw["bars"]]
    dates = {b.timestamp.date().isoformat(): i for i, b in enumerate(bars)}
    attack, decision = dates["2024-12-12"], dates["2024-12-13"]
    context = [None if i < attack else attack for i in range(len(bars))]
    evidence = pressure_exit_history(bars, context, StrategyConfig())[decision]
    assert evidence["pressure_date"] == "2024-10-08"
    assert evidence["pressure_n_date"] == "2024-12-12"
    assert evidence["pressure_adverse_patterns"] == ["long_upper_shadow"]
    assert evidence["pressure_upper_shadow_fraction"] == pytest.approx(0.72)
    entry = dates["2024-11-28"]
    signal = Signal(
        bars[entry].timestamp,
        raw["symbol"],
        entry,
        "LONG",
        bars[entry].close,
        5,
        "fixture",
        bars[entry].timestamp,
        0,
        None,
        "fixture",
        20,
    )
    config = StrategyConfig(
        pressure_adverse_exit=True,
        staged_exit_enabled=True,
        staged_exit_same_day=True,
        exit_on_target=False,
        max_hold_bars=100,
    )
    options = dict(signals=[signal], config=config, positive_n_bars={raw["symbol"]: {attack: attack}})
    full = run_portfolio({raw["symbol"]: bars}, **options)
    prefix = run_portfolio({raw["symbol"]: bars[: decision + 1]}, **options)
    assert full.orders == prefix.orders
    buy, sell = [o for o in full.orders if o["status"] == "filled"]
    assert sell["reason"] == "pressure_adverse_clear"
    assert sell["timestamp"] == sell["signal_timestamp"] == bars[decision].timestamp.isoformat()
    assert sell["remaining_quantity"] == 0
    assert sell["quantity"] == buy["quantity"]


def test_invalidated_n_does_not_leak_pressure_state_to_later_low_price_entry():
    bars, context = sample()
    bars.extend(
        [
            replace(bars[-1], timestamp=bars[-1].timestamp + timedelta(days=1), open=9.5, high=9.6, low=9, close=9.1),
            replace(bars[-1], timestamp=bars[-1].timestamp + timedelta(days=2), open=9.1, high=9.5, low=9, close=9.4),
            replace(bars[-1], timestamp=bars[-1].timestamp + timedelta(days=3), open=9.4, high=9.5, low=9.1, close=9.3),
        ]
    )
    context.extend([None, 25, 25])
    result = pressure_exit_history(bars, context, StrategyConfig())
    assert 24 in result
    assert 26 not in result


def test_guilin_2022_unfilled_gap_reduces_then_lower_close_clears_remaining():
    raw = json.loads((Path(__file__).parent / "fixtures/guilin_2022_hierarchy.json").read_text(encoding="utf-8"))
    bars = [Bar(datetime.fromisoformat(day), raw["symbol"], *values) for day, *values in raw["bars"]]
    dates = {bar.timestamp.date().isoformat(): index for index, bar in enumerate(bars)}
    entry, attack = dates["2022-06-24"], dates["2022-06-24"]
    warning, clear = dates["2022-06-27"], dates["2022-06-29"]
    context = [None if index < attack else attack for index in range(len(bars))]
    risks = pressure_exit_history(bars[: clear + 1], context[: clear + 1], StrategyConfig())
    assert risks[warning]["reason"] == "pressure_gap_adverse_reduce"
    assert risks[warning]["exit_fraction"] == 0.5
    assert risks[warning]["pressure_gap_unfilled"] is True

    signal = Signal(
        bars[entry].timestamp,
        raw["symbol"],
        entry,
        "LONG",
        bars[entry].close,
        5,
        "fixture",
        bars[entry].timestamp,
        0,
        None,
        "fixture",
        20,
    )
    config = StrategyConfig(pressure_adverse_exit=True, entry_at_close=True, exit_on_target=False, max_hold_bars=100)
    options = dict(signals=[signal], config=config, positive_n_bars={raw["symbol"]: {attack: attack}})
    full = run_portfolio({raw["symbol"]: bars[: clear + 1]}, **options)
    prefix = run_portfolio({raw["symbol"]: bars[: warning + 1]}, **options)
    assert full.orders[: len(prefix.orders)] == prefix.orders
    buy, first, second = [order for order in full.orders if order["status"] == "filled"]
    assert first["reason"] == "pressure_gap_adverse_reduce"
    assert first["timestamp"] == bars[warning].timestamp.isoformat()
    assert first["closed_position_fraction"] == pytest.approx(0.5, rel=0.02)
    assert first["remaining_quantity"] > 0
    assert second["reason"] == "pressure_reduced_lower_close_clear"
    assert second["timestamp"] == bars[clear].timestamp.isoformat()
    assert second["quantity"] == pytest.approx(first["remaining_quantity"])
    assert second["remaining_quantity"] == 0
    assert first["quantity"] + second["quantity"] == pytest.approx(buy["quantity"])


def test_guilin_2022_resisted_breakout_clears_on_later_adverse_candle():
    raw = json.loads((Path(__file__).parent / "fixtures/guilin_2022_hierarchy.json").read_text(encoding="utf-8"))
    bars = [Bar(datetime.fromisoformat(day), raw["symbol"], *values) for day, *values in raw["bars"]]
    dates = {bar.timestamp.date().isoformat(): index for index, bar in enumerate(bars)}
    breakout, clear = dates["2022-06-29"], dates["2022-07-01"]
    context = [None] * len(bars)
    result = pressure_exit_history(bars[: clear + 1], context[: clear + 1], StrategyConfig())
    assert breakout not in result
    assert result[clear]["reason"] == "pressure_breakout_adverse_clear"
    assert result[clear]["pressure_date"] == "2022-04-13"
    assert result[clear]["pressure_breakout_date"] == "2022-06-29"
    assert result[clear]["pressure_rally_low_date"] == "2022-04-27"
    assert result[clear]["pressure_adverse_patterns"]
    assert pressure_exit_history(bars[:clear], context[:clear], StrategyConfig()).get(clear) is None

    entry = dates["2022-06-24"]
    signal = Signal(
        bars[entry].timestamp,
        raw["symbol"],
        entry,
        "LONG",
        bars[entry].close,
        5,
        "fixture",
        bars[entry].timestamp,
        0,
        None,
        "fixture",
        20,
    )
    config = StrategyConfig(pressure_adverse_exit=True, entry_at_close=True, exit_on_target=False, max_hold_bars=100)
    portfolio = run_portfolio({raw["symbol"]: bars[: clear + 1]}, [signal], config)
    buy, sell = [order for order in portfolio.orders if order["status"] == "filled"]
    assert buy["timestamp"] == bars[entry].timestamp.isoformat()
    assert sell["reason"] == "pressure_breakout_adverse_clear"
    assert sell["timestamp"] == bars[clear].timestamp.isoformat()
    assert sell["remaining_quantity"] == 0

    later_entry = dates["2022-06-30"]
    later_signal = Signal(
        bars[later_entry].timestamp,
        raw["symbol"],
        later_entry,
        "LONG",
        bars[later_entry].close,
        5,
        "fixture",
        bars[later_entry].timestamp,
        0,
        None,
        "fixture",
        20,
    )
    later_portfolio = run_portfolio({raw["symbol"]: bars[: clear + 1]}, [later_signal], config)
    assert [order["side"] for order in later_portfolio.orders if order["status"] == "filled"] == ["BUY"]


@pytest.mark.parametrize("rally_low, qualifies", [(9.4, False), (9.0, True)])
def test_resisted_breakout_requires_substantial_prior_rise(rally_low, qualifies):
    bars, _ = sample()
    bars[21] = replace(bars[21], open=9.6, high=9.8, low=rally_low, close=9.6)
    bars[22] = replace(bars[22], open=9.6, high=10.2, low=9.4, close=10.1)
    bars[23] = replace(bars[23], open=10.3, high=11.1, low=10, close=10.05)
    bars.append(
        replace(bars[23], timestamp=bars[23].timestamp + timedelta(days=1), open=10.05, high=10.1, low=9.8, close=9.8)
    )
    result = pressure_exit_history(bars, [None] * len(bars), StrategyConfig())
    assert (24 in result) is qualifies


def test_unfilled_gap_one_lot_does_not_force_full_exit():
    raw = json.loads((Path(__file__).parent / "fixtures/guilin_2022_hierarchy.json").read_text(encoding="utf-8"))
    bars = [Bar(datetime.fromisoformat(day), raw["symbol"], *values) for day, *values in raw["bars"]]
    dates = {bar.timestamp.date().isoformat(): index for index, bar in enumerate(bars)}
    entry, warning = dates["2022-06-24"], dates["2022-06-27"]
    signal = Signal(
        bars[entry].timestamp,
        raw["symbol"],
        entry,
        "LONG",
        bars[entry].close,
        5,
        "fixture",
        bars[entry].timestamp,
        0,
        None,
        "fixture",
        20,
    )
    config = StrategyConfig(
        initial_capital=1000,
        max_position_weight=1,
        risk_fraction=1,
        pressure_adverse_exit=True,
        entry_at_close=True,
        exit_on_target=False,
        max_hold_bars=100,
    )
    result = run_portfolio(
        {raw["symbol"]: bars[: warning + 1]}, [signal], config, positive_n_bars={raw["symbol"]: {entry: entry}}
    )
    assert [order["side"] for order in result.orders if order["status"] == "filled"] == ["BUY"]
    assert result.orders[-1]["reason"] == "reduction_below_one_lot"
    assert result.open_positions[0]["quantity"] > 0
