from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from wavequant.application.analytics.backtest import run_portfolio
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar, Signal
from wavequant.domain.strategies.hierarchical_entry import hierarchical_history
from wavequant.domain.strategies.trend_flip_exit import trend_flip_exit_history


def lexin_bars():
    raw = json.loads((Path(__file__).parent / "fixtures/lexin_2026_squeeze.json").read_text(encoding="utf-8"))
    bars = [Bar(datetime.fromisoformat(day), raw["symbol"], *values) for day, *values in raw["bars"]]
    dates = {str(bar.timestamp.date()): index for index, bar in enumerate(bars)}
    return bars[:dates["2026-07-06"] + 1], dates


def test_lexin_secondary_last_fall_high_reduces_once_then_clears_on_double_break():
    bars, dates = lexin_bars()
    risks = trend_flip_exit_history(bars, hierarchical_history(bars)[0])
    warning = risks[dates["2026-07-02"]]
    assert warning["reason"] == "trend_last_fall_high_upper_shadow_reduce"
    assert warning["trend_level"] == 2
    assert warning["trend_key_date"] == "2026-04-30"
    assert warning["trend_key_high"] == pytest.approx(bars[dates["2026-04-30"]].high)
    assert warning["exit_target_fraction"] == 0.8
    assert risks[dates["2026-07-03"]]["reason"] == "trend_last_fall_high_upper_shadow_reduce"
    assert bars[dates["2026-07-06"]].low < bars[dates["2026-07-03"]].low
    assert risks[dates["2026-07-06"]]["reason"] == "trend_last_fall_high_breakdown_clear"
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
        "trend_last_fall_high_upper_shadow_reduce", "trend_last_fall_high_breakdown_clear"
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
    assert risks[4]["reason"] == "trend_last_fall_high_breakdown_clear"
    high_only = trend_flip_exit_history(bars, {index: {level: [high]} for index in history})
    assert not any(risk["reason"].startswith("trend_last_fall_high_") for risk in high_only.values())
    late_high = dict(high, available_at=2)
    assert trend_flip_exit_history(bars, {index: {level: [late_high, low]} for index in history}) == {}


def test_guofang_source_falling_high_break_then_direct_or_staged_exit():
    raw = json.loads((Path(__file__).parent / "fixtures/guofang_2022_last_fall_high.json").read_text(encoding="utf-8"))
    bars = [Bar(datetime.fromisoformat(day), raw["symbol"], *values) for day, *values in raw["bars"]]
    dates = {str(bar.timestamp.date()): index for index, bar in enumerate(bars)}
    history = hierarchical_history(bars, include_base=True)[0]
    assert 0 not in hierarchical_history(bars)[0][dates["2022-06-29"]]

    risks = trend_flip_exit_history(bars, history)
    attack, entry, clear, next_day = (dates[day] for day in
                                      ("2022-06-29", "2022-06-30", "2022-07-04", "2022-07-05"))
    assert risks[attack]["reason"] == "trend_last_fall_high_bearish_reduce"
    assert risks[attack]["trend_adverse_patterns"] == ["bearish_body", "long_upper_shadow"]
    assert risks[attack]["exit_target_fraction"] == 0.8
    assert bars[attack].low > bars[attack - 1].low  # Not a double-break clearance.
    assert entry not in risks
    assert risks[clear]["reason"] == "trend_last_fall_high_breakdown_clear"
    assert risks[clear]["trend_level"] == 0
    assert risks[clear]["trend_key_date"] == "2022-06-15"
    assert risks[clear]["trend_attack_date"] == "2022-06-29"
    assert risks[clear]["trend_breakout_basis"] == "high"
    assert risks[clear]["observed_close"] < risks[clear]["previous_close"]
    assert risks[clear]["observed_low"] < risks[clear]["previous_low"]

    staged = bars.copy()
    staged[clear] = replace(staged[clear], low=4.94)
    staged_risks = trend_flip_exit_history(staged, hierarchical_history(staged, include_base=True)[0])
    assert staged_risks[clear]["reason"] == "trend_last_fall_high_bearish_reduce"
    assert staged_risks[clear]["exit_target_fraction"] == 0.8
    assert staged_risks[next_day]["reason"] == "trend_last_fall_high_breakdown_clear"
    prefix = bars[:clear]
    assert trend_flip_exit_history(prefix, hierarchical_history(prefix, include_base=True)[0]) == {
        index: risk for index, risk in risks.items() if index < len(prefix)
    }

    signal = Signal(bars[entry].timestamp, raw["symbol"], entry, "LONG", bars[entry].close,
                    4.82202355, "fixture", bars[entry].timestamp, 0, None, "fixture", 5.129812)
    config = StrategyConfig(trend_flip_adverse_exit=True, entry_at_close=True, exit_on_target=False,
                            risk_fraction=0.1, max_position_weight=0.8, max_participation=1,
                            max_hold_bars=100)
    result = run_portfolio({raw["symbol"]: bars}, [signal], config)
    filled = [order for order in result.orders if order["status"] == "filled"]
    assert [(order["timestamp"][:10], order["side"], order["reason"]) for order in filled] == [
        ("2022-06-30", "BUY", "fixture"),
        ("2022-07-04", "SELL", "trend_last_fall_high_breakdown_clear"),
    ]
    assert filled[-1]["trend_key_date"] == "2022-06-15"
    assert filled[-1]["remaining_quantity"] == 0


def test_source_intraday_break_and_double_break_same_candle_clears_directly():
    rows = [(4.8, 5.0, 4.6, 4.8), (4.1, 4.2, 4.0, 4.1),
            (4.2, 4.5, 4.1, 4.3), (3.9, 4.0, 3.8, 3.9),
            (4.0, 4.4, 3.9, 4.0), (4.1, 4.6, 3.7, 3.9)]
    bars = [Bar(datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=index), "TEST", *row, 1000)
            for index, row in enumerate(rows)]
    anchor = {"index": 0, "kind": "H", "value": 5.0, "available_at": 1}
    turns = [{"index": 1, "kind": "L", "value": 4.0, "available_at": 2},
             {"index": 2, "kind": "H", "value": 4.5, "available_at": 3},
             {"index": 3, "kind": "L", "value": 3.8, "available_at": 4}]
    history = {index: {0: [point for point in turns if point["available_at"] <= index],
                       1: [anchor]} for index in range(len(bars))}

    risk = trend_flip_exit_history(bars, history)[5]

    assert risk["reason"] == "trend_last_fall_high_breakdown_clear"
    assert risk["trend_attack_index"] == 5
    assert risk["trend_breakout_basis"] == "high"


@pytest.mark.parametrize("today,expected", [
    ((10.3, 10.6, 10.0, 10.2), "trend_last_fall_high_bearish_reduce"),
    ((10.3, 10.6, 9.9, 10.5), None),
    ((10.3, 10.6, 9.9, 10.2), "trend_last_fall_high_breakdown_clear"),
])
def test_breakdown_needs_lower_close_and_lower_low(today, expected):
    rows = [(9.5, 10, 9, 9.5), (9.4, 9.8, 8.8, 9),
            (10.1, 10.6, 10.0, 10.5), today]
    bars = [Bar(datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=index), "TEST", *row, 1000)
            for index, row in enumerate(rows)]
    high = {"index": 0, "kind": "H", "value": 10, "available_at": 1}
    low = {"index": 1, "kind": "L", "value": 8.8, "available_at": 1}
    history = {index: {2: [high, low]} for index in range(len(bars))}

    risk = trend_flip_exit_history(bars, history).get(3)

    assert (risk["reason"] if risk else None) == expected


def test_double_break_clears_without_a_prior_reduction_fill():
    bars, dates = lexin_bars()
    bars[dates["2026-07-02"]] = replace(bars[dates["2026-07-02"]], high=16.20)
    bars[dates["2026-07-03"]] = replace(bars[dates["2026-07-03"]], high=16.45)
    risks = trend_flip_exit_history(bars, hierarchical_history(bars)[0])
    assert dates["2026-07-02"] not in risks
    assert dates["2026-07-03"] not in risks
    assert risks[dates["2026-07-06"]]["reason"] == "trend_last_fall_high_breakdown_clear"

    decision = dates["2026-06-30"]
    signal = Signal(bars[decision].timestamp, bars[0].symbol, decision, "LONG", bars[decision].close,
                    10, "fixture", bars[decision].timestamp, 0, None, "fixture", 100)
    config = StrategyConfig(trend_flip_adverse_exit=True, exit_on_target=False, risk_fraction=0.1,
                            max_position_weight=0.8, max_participation=1)
    result = run_portfolio({bars[0].symbol: bars}, [signal], config)
    filled = [order for order in result.orders if order["status"] == "filled"]
    assert [order["reason"] for order in filled[1:]] == ["trend_last_fall_high_breakdown_clear"]
    assert filled[1]["remaining_quantity"] == 0


def test_bearish_warning_below_key_remains_armed_until_double_break():
    rows = [(9.5, 10, 9, 9.5), (9.4, 9.8, 8.5, 9),
            (10.1, 10.6, 9.4, 10.5), (10.0, 10.1, 9.4, 9.9),
            (9.9, 9.95, 9.3, 9.8)]
    bars = [Bar(datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=index), "TEST", *row, 1000)
            for index, row in enumerate(rows)]
    high = {"index": 0, "kind": "H", "value": 10, "available_at": 1}
    low = {"index": 1, "kind": "L", "value": 8.5, "available_at": 1}
    history = {index: {2: [high, low]} for index in range(len(bars))}

    risks = trend_flip_exit_history(bars, history)

    assert risks[3]["reason"] == "trend_last_fall_high_bearish_reduce"
    assert risks[4]["reason"] == "trend_last_fall_high_breakdown_clear"
    assert risks[4]["trend_warning_index"] == 4
    assert risks[4]["trend_prior_warning_index"] == 3
