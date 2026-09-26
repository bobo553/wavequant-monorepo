from dataclasses import replace
from datetime import datetime, timedelta
import json
from pathlib import Path

import pytest

from wavequant.application.analytics.backtest import run_portfolio
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar, Signal
from wavequant.domain.strategies.hierarchical_entry import hierarchical_history
from wavequant.domain.strategies.trend_flip_exit import (
    resisted_last_fall_high_sessions, trend_flip_exit_history,
)


def lexin_bars():
    raw = json.loads((Path(__file__).parent / "fixtures/lexin_2026_squeeze.json").read_text(encoding="utf-8"))
    bars = [Bar(datetime.fromisoformat(day), raw["symbol"], *values) for day, *values in raw["bars"]]
    dates = {str(bar.timestamp.date()): index for index, bar in enumerate(bars)}
    return bars[:dates["2026-07-06"] + 1], dates


def guofang_bars():
    raw = json.loads((Path(__file__).parent / "fixtures/guofang_2020_secondary_wave.json").read_text(encoding="utf-8"))
    bars = [Bar(datetime.fromisoformat(day), raw["symbol"], *values) for day, *values in raw["bars"]]
    dates = {str(bar.timestamp.date()): index for index, bar in enumerate(bars)}
    return bars[:dates["2020-05-14"] + 1], dates


def test_guofang_confirmed_drawing_last_fall_high_arms_volume_exit_after_bearish_resistance():
    bars, dates = guofang_bars()
    history = hierarchical_history(bars, include_drawing_turns=True)[0]
    may7, may8, may13, may14 = (dates[day] for day in
                               ("2020-05-07", "2020-05-08", "2020-05-13", "2020-05-14"))
    key = history[may7 - 1][0][-2]
    assert len(history[may7 - 1][0]) == 2
    assert 0 not in hierarchical_history(bars)[0][may7 - 1]
    cache = {}
    hierarchical_history(bars[:-1], prefix_cache=cache)
    assert hierarchical_history(bars, prefix_cache=cache, include_drawing_turns=True)[0] == history
    assert key["kind"] == "H"
    assert bars[key["index"]].timestamp.date().isoformat() == "2020-04-29"
    assert key["value"] == pytest.approx(bars[key["index"]].high)
    assert key["available_at"] < may7
    assert bars[may7 - 1].close <= key["value"] < bars[may7].close
    sessions = resisted_last_fall_high_sessions(bars, history)
    assert may8 not in sessions  # A bullish lower open is not the bearish warning.
    assert may13 in sessions
    prefix = bars[:may13 + 1]
    assert may13 in resisted_last_fall_high_sessions(
        prefix, hierarchical_history(prefix, include_drawing_turns=True)[0],
    )
    assert bars[may13].volume > bars[may13 - 1].volume
    assert bars[may13].close < min(bars[may13].open, bars[may13 - 1].close)
    assert bars[may13].low > bars[may13 - 1].low
    assert bars[may14].low < bars[may13].low and bars[may14].high <= bars[may13].high

    signal = Signal(bars[may7].timestamp, bars[0].symbol, may7, "LONG", bars[may7].close,
                    4.5, "fixture", bars[may7].timestamp, 0, None, "fixture", 10)
    config = StrategyConfig(initial_capital=100_000, risk_fraction=.2, max_position_weight=.8,
                            max_participation=1, slippage_bps_per_side=0, exit_on_target=False,
                            volume_down_exit=True, volume_down_after_milestone=True, max_hold_bars=100)
    result = run_portfolio({bars[0].symbol: bars}, [signal], config)
    buy, reduction, clear = [order for order in result.orders if order["status"] == "filled"]
    assert buy["timestamp"] == bars[may8].timestamp.isoformat()
    assert reduction["timestamp"] == bars[may13].timestamp.isoformat()
    assert reduction["reason"] == "volume_down_reduce_70"
    assert reduction["exit_target_fraction"] == .7
    assert reduction["quantity"] == int(buy["quantity"] * .7 // 100) * 100
    assert clear["timestamp"] == bars[may14].timestamp.isoformat()
    assert clear["reason"] == "volume_down_next_followthrough_clear"
    assert clear["remaining_quantity"] == 0


def test_lexin_secondary_last_fall_high_reduces_once_then_clears_on_first_lower_close():
    bars, dates = lexin_bars()
    risks = trend_flip_exit_history(bars, hierarchical_history(bars)[0])
    warning = risks[dates["2026-07-02"]]
    assert warning["reason"] == "trend_last_fall_high_upper_shadow_reduce"
    assert warning["trend_level"] == 2
    assert warning["trend_key_date"] == "2026-04-30"
    assert warning["trend_key_high"] == pytest.approx(bars[dates["2026-04-30"]].high)
    assert warning["exit_target_fraction"] == 0.8
    assert risks[dates["2026-07-03"]]["reason"] == "trend_last_fall_high_upper_shadow_reduce"
    assert risks[dates["2026-07-06"]]["reason"] == "trend_last_fall_high_lower_close_clear"
    assert dates["2026-07-02"] in resisted_last_fall_high_sessions(
        bars, hierarchical_history(bars)[0],
    )
    prefix = bars[:dates["2026-07-02"] + 1]
    assert trend_flip_exit_history(prefix, hierarchical_history(prefix)[0]) == {
        index: risk for index, risk in risks.items() if index < len(prefix)
    }

    decision = dates["2026-06-30"]
    signal = Signal(bars[decision].timestamp, bars[0].symbol, decision, "LONG", bars[decision].close,
                    10, "fixture", bars[decision].timestamp, 0, None, "fixture", 100)
    config = StrategyConfig(trend_flip_adverse_exit=True, exit_on_target=False, risk_fraction=0.1,
                            max_position_weight=0.8, max_participation=1)
    result = run_portfolio({bars[0].symbol: bars}, [signal], config)
    filled = [order for order in result.orders if order["status"] == "filled"]
    assert [order["reason"] for order in filled[1:]] == [
        "trend_last_fall_high_upper_shadow_reduce", "trend_last_fall_high_lower_close_clear"
    ]
    assert filled[1]["timestamp"].startswith("2026-07-02")
    assert filled[1]["remaining_quantity"] > 0
    assert filled[2]["timestamp"].startswith("2026-07-06")
    assert filled[2]["remaining_quantity"] == 0
    assert not any(order["side"] == "SELL" and order["timestamp"].startswith("2026-07-03")
                   for order in result.orders)

    blocked_bars = bars.copy()
    blocked_bars[dates["2026-07-02"]] = replace(blocked_bars[dates["2026-07-02"]], sellable=False)
    retry = run_portfolio({bars[0].symbol: blocked_bars}, [signal], config)
    retry_fills = [order for order in retry.orders if order["side"] == "SELL" and order["status"] == "filled"]
    assert [order["timestamp"][:10] for order in retry_fills] == ["2026-07-03", "2026-07-06"]


@pytest.mark.parametrize("level", [2, 3])
def test_both_trend_levels_require_known_last_fall_high_and_close_break(level):
    rows = [(9.5, 10, 9, 9.5), (9.1, 9.4, 8.5, 9), (9.5, 11, 9.4, 10.2),
            (10.3, 11.2, 10, 10.5), (10.4, 10.6, 9.8, 10)]
    bars = [Bar(datetime(2026, 1, 1) + timedelta(days=index), "TEST", *row, 1000)
            for index, row in enumerate(rows)]
    high = dict(index=0, kind="H", value=10, available_at=1)
    low = dict(index=1, kind="L", value=8.5, available_at=1)
    history = {index: {level: [high, low]} for index in range(len(bars))}
    risks = trend_flip_exit_history(bars, history)
    assert risks[2]["reason"] == "trend_last_fall_high_upper_shadow_reduce"
    assert risks[2]["trend_level"] == level
    assert risks[3]["reason"] == "trend_last_fall_high_upper_shadow_reduce"
    assert risks[4]["reason"] == "trend_last_fall_high_lower_close_clear"
    high_only = trend_flip_exit_history(bars, {index: {level: [high]} for index in history})
    assert not any(risk["reason"].startswith("trend_last_fall_high_") for risk in high_only.values())
    late_high = dict(high, available_at=2)
    assert trend_flip_exit_history(bars, {index: {level: [late_high, low]} for index in history}) == {}


def test_last_fall_high_milestone_requires_breakout_and_bearish_resistance():
    rows = [(9.5, 10, 9, 9.5), (9.1, 9.4, 8.5, 9), (9.5, 11, 9.4, 10.2)]
    bars = [Bar(datetime(2026, 1, 1) + timedelta(days=index), "TEST", *row, 1000)
            for index, row in enumerate(rows)]
    high = dict(index=0, kind="H", value=10, available_at=1)
    low = dict(index=1, kind="L", value=8.5, available_at=1)
    history = {index: {2: [high, low]} for index in range(len(bars))}
    assert resisted_last_fall_high_sessions(bars, history) == {2}

    clean_breakout = bars.copy()
    clean_breakout[2] = replace(clean_breakout[2], high=10.3)
    assert resisted_last_fall_high_sessions(clean_breakout, history) == set()
    late_high = dict(high, available_at=2)
    assert resisted_last_fall_high_sessions(
        bars, {index: {2: [late_high, low]} for index in history},
    ) == set()
