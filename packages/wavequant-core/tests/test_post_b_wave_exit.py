"""A C-segment holding must measure exits from an N rebased at its B low."""

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar, Signal
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals
from wavequant.domain.strategies.strategy_profiles import whole_wave_profile
from wavequant.domain.strategies.wave_exhaustion_exit import observe_wave_exhaustion
from wavequant.interfaces.research_tools.stock_backtest import (
    _post_b_wave_exit_events,
    single_stock_result,
)


def guofang():
    raw = json.loads((Path(__file__).parent / "fixtures/guofang_2020_secondary_wave.json").read_text(encoding="utf-8"))
    bars = [Bar(datetime.fromisoformat(day), raw["symbol"], *prices) for day, *prices in raw["bars"]]
    return bars, {bar.timestamp.date().isoformat(): i for i, bar in enumerate(bars)}


def audit_rows(dates):
    old_attack = dates["2020-05-25"]
    b_low = dates["2020-06-12"]
    new_attack = dates["2020-07-06"]
    return [
        dict(event="wave_projection_ready", attack=old_attack, origin_index=dates["2020-05-22"],
             bar_index=dates["2020-06-04"], two_t=5.5821992677),
        dict(event="n_completed", direction="up", origin=b_low, bar_index=new_attack,
             known_at=new_attack, one_p=5.7510247588, two_t=6.1665849185),
        dict(event="long_signal", channel="wave_push_gap", attack=old_attack,
             bar_index=new_attack, wave_b_low_index=b_low, wave_a_class="strong"),
    ]


def test_guofang_old_a_two_t_cannot_sell_new_b_segment_on_july_7():
    bars, dates = guofang()
    profile = whole_wave_profile({"scenarios": {"base": {"execution": {}}}})
    generated = generate_system_signals(bars, SystemStrategy(**profile["strategy"]))
    new_n = next(row for row in generated.audit if row["event"] == "n_completed"
                 and row["bar_index"] == dates["2020-07-06"])
    assert new_n["origin"] == dates["2020-06-12"]
    assert new_n["box_anchor"] == pytest.approx(bars[dates["2020-07-06"]].high)
    assert new_n["one_p"] == pytest.approx(2 * new_n["box_anchor"] - bars[new_n["origin"]].low)
    assert new_n["two_t"] == pytest.approx(3 * new_n["box_anchor"] - 2 * bars[new_n["origin"]].low)
    audit = audit_rows(dates)
    events = _post_b_wave_exit_events(bars, audit, audit[-1])
    day = dates["2020-07-07"]

    assert all(event["attack"] != dates["2020-05-25"] for event in events)
    assert observe_wave_exhaustion(bars, day, events, StrategyConfig(), entry_index=dates["2020-07-06"]) is None
    assert bars[day].high < audit[1]["one_p"]

    signal = Signal(bars[day - 1].timestamp, bars[day].symbol, day - 1, "LONG",
                    bars[day - 1].close, 4.8, "system_wave_push_gap",
                    bars[dates["2020-05-25"]].timestamp, 0, None, "fixture", 6.2)
    execution = StrategyConfig(entry_at_close=True, wave_exhaustion_exit=True,
                               exit_on_target=False, max_hold_bars=100,
                               slippage_bps_per_side=0, max_participation=1).to_dict()
    for row in audit:
        row.update(timestamp=bars[row["bar_index"]].timestamp.isoformat(), symbol=bars[day].symbol)
    result = single_stock_result(bars[:day + 1], {}, execution,
                                 SimpleNamespace(signals=[signal], audit=audit, counts={}))
    assert [(order["side"], order["timestamp"][:10]) for order in result["orders"]
            if order["status"] == "filled"] == [("BUY", "2020-07-06")]


def test_new_n_one_p_can_trigger_and_reports_its_rebased_origin():
    bars, dates = guofang()
    audit = audit_rows(dates)
    day = dates["2020-07-07"]
    bars[day] = Bar(bars[day].timestamp, bars[day].symbol, 5.36, 5.9, 5.27, 5.30, bars[day].volume)
    events = _post_b_wave_exit_events(bars, audit, audit[-1])

    decision = observe_wave_exhaustion(bars, day, events, StrategyConfig(), entry_index=day - 1)
    assert decision["reason"] == "wave_target_upper_shadow_reduce"
    assert decision["wave_reached_stage"] == "one_p"
    assert decision["wave_reached_date"] == "2020-07-07"
    assert decision["wave_n_date"] == "2020-07-06"
    assert decision["wave_n_origin_date"] == "2020-06-12"
    assert decision["wave_n_origin_price"] == bars[dates["2020-06-12"]].low

    for row in audit:
        row.update(timestamp=bars[row["bar_index"]].timestamp.isoformat(), symbol=bars[day].symbol)
    signal = Signal(bars[day - 1].timestamp, bars[day].symbol, day - 1, "LONG",
                    bars[day - 1].close, 4.8, "system_wave_push_gap",
                    bars[dates["2020-05-25"]].timestamp, 0, None, "fixture", 6.2)
    execution = StrategyConfig(entry_at_close=True, wave_exhaustion_exit=True,
                               exit_on_target=False, max_hold_bars=100,
                               slippage_bps_per_side=0, max_participation=1).to_dict()
    result = single_stock_result(bars[:day + 1], {}, execution,
                                 SimpleNamespace(signals=[signal], audit=audit, counts={}))
    sell = next(order for order in result["orders"] if order["side"] == "SELL" and order["status"] == "filled")
    assert sell["reason"] == "wave_target_upper_shadow_reduce"
    assert sell["wave_reached_stage"] == "one_p"
    assert sell["wave_n_origin_date"] == "2020-06-12"


def test_post_b_projection_uses_its_own_origin_for_five_top():
    bars, dates = guofang()
    audit = audit_rows(dates)
    new_attack = dates["2020-07-06"]
    audit.insert(-1, dict(event="wave_projection_target_reached", attack=new_attack,
                          origin_index=dates["2020-06-12"], bar_index=dates["2020-07-07"],
                          reached_stage="five_top", reached_target=6.8))
    events = _post_b_wave_exit_events(bars, audit, audit[-1])

    assert any(event.get("reached_stage") == "five_top" and
               event["origin_index"] == dates["2020-06-12"] for event in events)
    assert all(event.get("origin_index") != dates["2020-05-22"] for event in events)
