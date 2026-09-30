"""Risk exits for an ordinary A-wave rebound after its C equal-wave target."""

from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from wavequant.application.analytics.backtest import run_portfolio
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar, Signal
from wavequant.domain.strategies.wave_exhaustion_exit import observe_wave_exhaustion
from wavequant.interfaces.research_tools.stock_backtest import single_stock_result


def guofang_ordinary_wave():
    rows = [
        ("2020-05-07", 4.86, 4.99, 4.85, 4.97, 5_063_675),
        ("2020-05-13", 5.29, 5.34, 5.26, 5.27, 9_716_400),
        ("2020-05-20", 4.93, 4.94, 4.86, 4.88, 4_618_200),
        ("2020-05-21", 4.91, 5.07, 4.87, 5.00, 6_673_199),
        ("2020-06-01", 5.00, 5.29, 4.99, 5.18, 14_039_674),
        ("2020-06-02", 5.21, 5.56, 5.16, 5.31, 17_938_320),
        ("2020-06-03", 5.29, 5.46, 5.21, 5.33, 14_344_924),
        ("2020-06-04", 5.38, 5.70, 5.29, 5.50, 25_711_399),
        ("2020-06-05", 5.46, 5.50, 5.21, 5.26, 17_800_199),
    ]
    bars = [Bar(datetime.fromisoformat(day), "sh.601086", *prices) for day, *prices in rows]
    ordinary = dict(
        event="wave_ordinary_entry",
        attack=0,
        bar_index=3,
        target=5.529636186216229,
        one_p=5.319383859744129,
        two_t=5.645274965775883,
        a_origin=4.667601647680621,
        a_high=5.34040909239134,
        a_high_index=1,
        b_low=4.85682874150551,
        b_low_index=2,
    )
    return bars, ordinary


def xianfeng_february_wave():
    fixture = Path(__file__).parent / "fixtures/xianfeng_2026_resistance.json"
    raw = json.loads(fixture.read_text(encoding="utf-8"))
    rows = [row for row in raw["bars"] if "2026-01-06" <= row[0] <= "2026-02-04"]
    bars = [Bar(datetime.fromisoformat(day), raw["symbol"], *prices) for day, *prices in rows]
    dates = {bar.timestamp.date().isoformat(): index for index, bar in enumerate(bars)}
    entry = dict(event="wave_ordinary_entry", attack=dates["2026-01-06"],
                 bar_index=dates["2026-01-29"], target=6.03, target_0618=5.50666,
                 one_p=5.24, two_t=5.90, a_high=5.29, b_low=4.66)
    return bars, dates, entry


def test_xianfeng_c_0618_shadow_reduces_and_later_double_break_clears():
    bars, dates, entry = xianfeng_february_wave()
    config = StrategyConfig(wave_exhaustion_exit=True, exit_on_target=False)
    second = dates["2026-02-02"]
    third = dates["2026-02-03"]
    fourth = dates["2026-02-04"]

    assert observe_wave_exhaustion(bars, dates["2026-01-30"], [entry], config, entry_index=entry["bar_index"]) is None
    reduction = observe_wave_exhaustion(bars, second, [entry], config, entry_index=entry["bar_index"])
    assert reduction is not None and reduction["reason"] == "wave_c_0618_upper_shadow_reduce"
    assert reduction["wave_reached_date"] == "2026-01-30"
    assert reduction["wave_reached_price"] == pytest.approx(5.50666)
    assert reduction["wave_upper_shadow_fraction"] == pytest.approx(.45 / .55)
    assert observe_wave_exhaustion(bars, third, [entry], config, reduced=True,
                                   entry_index=entry["bar_index"]) is None
    clear = observe_wave_exhaustion(bars, fourth, [entry], config, reduced=True,
                                    entry_index=entry["bar_index"])
    assert clear is not None and clear["reason"] == "wave_c_0618_shadow_break_clear"
    assert clear["abnormal_date"] == "2026-02-02"
    assert clear["observed_low"] < clear["abnormal_low"]
    assert clear["observed_close"] < clear["abnormal_close"]

    later_target = dict(entry, target_0618=6.01)
    assert observe_wave_exhaustion(bars, second, [later_target], config,
                                   entry_index=entry["bar_index"]) is None
    unbroken_low = bars.copy()
    unbroken_low[fourth] = replace(unbroken_low[fourth], low=5.44)
    assert observe_wave_exhaustion(unbroken_low, fourth, [entry], config, reduced=True,
                                   entry_index=entry["bar_index"]) is None


def test_xianfeng_c_0618_holding_reduces_february_2_and_clears_february_4():
    bars, dates, entry = xianfeng_february_wave()
    entry_index = entry["bar_index"]
    signal = Signal(bars[entry_index].timestamp, bars[entry_index].symbol, entry_index, "LONG",
                    bars[entry_index].close, 4.07, "system_wave_push_gap",
                    bars[entry["attack"]].timestamp, 0, None, "fixture", entry["target"])
    config = StrategyConfig(entry_at_close=True, wave_exhaustion_exit=True, volume_down_exit=True,
                            staged_exit_enabled=False, exit_on_target=False, max_hold_bars=100,
                            max_participation=1, slippage_bps_per_side=0)
    kwargs = dict(signals=[signal], config=config, wave_events={bars[0].symbol: [entry]})

    result = run_portfolio({bars[0].symbol: bars}, **kwargs)
    orders = [order for order in result.orders if order["status"] == "filled"]
    assert [(order["side"], order["timestamp"][:10], order["reason"]) for order in orders] == [
        ("BUY", "2026-01-29", "system_wave_push_gap"),
        ("SELL", "2026-02-02", "wave_c_0618_upper_shadow_reduce"),
        ("SELL", "2026-02-04", "wave_c_0618_shadow_break_clear"),
    ]
    assert orders[1]["remaining_quantity"] > 0
    assert orders[2]["remaining_quantity"] == 0
    for day in ("2026-02-02", "2026-02-03"):
        prefix = run_portfolio({bars[0].symbol: bars[:dates[day] + 1]}, **kwargs)
        assert prefix.orders == [order for order in result.orders if order["timestamp"][:10] <= day]


def test_xianfeng_ordinary_entry_binds_c_0618_to_its_backtest_position():
    bars, dates, entry = xianfeng_february_wave()
    entry_index = entry["bar_index"]
    signal = Signal(bars[entry_index].timestamp, bars[entry_index].symbol, entry_index, "LONG",
                    bars[entry_index].close, 4.07, "system_wave_push_gap",
                    bars[entry["attack"]].timestamp, 0, None, "fixture", entry["target"])
    audit = [dict(event="long_signal", channel="wave_push_gap", attack=entry["attack"],
                  bar_index=entry_index, timestamp=bars[entry_index].timestamp.isoformat(),
                  symbol=bars[0].symbol, wave_a_class="ordinary", wave_b_low_index=dates["2026-01-27"],
                  wave_equal_target=entry["target"], wave_c_0618_target=entry["target_0618"],
                  wave_entry_one_p=entry["one_p"], wave_entry_two_t=entry["two_t"],
                  wave_a_origin=3.92, wave_a_high=entry["a_high"],
                  wave_a_high_index=dates["2026-01-16"], wave_b_low=entry["b_low"])]
    execution = StrategyConfig(entry_at_close=True, wave_exhaustion_exit=True,
                               staged_exit_enabled=False, exit_on_target=False,
                               max_hold_bars=100, max_participation=1,
                               slippage_bps_per_side=0).to_dict()

    result = single_stock_result(bars, {}, execution, SimpleNamespace(signals=[signal], audit=audit, counts={}))
    orders = [order for order in result["orders"] if order["status"] == "filled"]
    assert [(order["side"], order["timestamp"][:10], order["reason"]) for order in orders] == [
        ("BUY", "2026-01-29", "system_wave_push_gap"),
        ("SELL", "2026-02-02", "wave_c_0618_upper_shadow_reduce"),
        ("SELL", "2026-02-04", "wave_c_0618_shadow_break_clear"),
    ]


def test_guofang_ordinary_c_equal_target_upper_wick_reduces_then_first_lower_close_clears():
    bars, ordinary = guofang_ordinary_wave()
    config = StrategyConfig(wave_exhaustion_exit=True, exit_on_target=False)
    events = [ordinary, dict(event="wave_projection_ready", attack=0, bar_index=7, two_t=ordinary["two_t"])]

    assert observe_wave_exhaustion(bars, 4, events, config, entry_index=3) is None
    reduction = observe_wave_exhaustion(bars, 5, events, config, entry_index=3)
    assert reduction["reason"] == "wave_ordinary_equal_upper_shadow_reduce"
    assert reduction["exit_target_fraction"] == config.wave_exhaustion_reduction
    assert reduction["wave_reached_stage"] == "ordinary_equal"
    assert reduction["wave_reached_date"] == "2020-06-02"
    assert reduction["wave_reached_price"] == pytest.approx(ordinary["target"])
    assert reduction["wave_upper_shadow_fraction"] == pytest.approx(0.625)
    assert bars[1].high >= ordinary["one_p"] and bars[1].high < ordinary["two_t"]
    assert observe_wave_exhaustion(bars, 6, events, config, entry_index=3, reduced=True) is None
    assert observe_wave_exhaustion(bars, 7, events, config, entry_index=3, reduced=True) is None
    clear = observe_wave_exhaustion(bars, 8, events, config, entry_index=3, reduced=True)
    assert clear["reason"] == "wave_ordinary_equal_lower_close_clear"
    assert clear["abnormal_date"] == "2020-06-02"
    assert clear["observed_close"] < clear["previous_close"]
    assert clear["execution_model"] == "same_day_close"

    signal = Signal(
        bars[3].timestamp,
        bars[3].symbol,
        3,
        "LONG",
        bars[3].close,
        4.76,
        "system_wave_push_gap",
        bars[0].timestamp,
        0,
        None,
        "fixture",
        ordinary["target"],
    )
    account = StrategyConfig(
        wave_exhaustion_exit=True,
        exit_on_target=False,
        entry_at_close=True,
        max_hold_bars=100,
        slippage_bps_per_side=0,
        max_participation=1,
    )
    kwargs = dict(signals=[signal], config=account, wave_events={bars[0].symbol: events})
    result = run_portfolio({bars[0].symbol: bars}, **kwargs)
    buy, sell, final = [order for order in result.orders if order["status"] == "filled"]
    assert buy["timestamp"] == bars[3].timestamp.isoformat()
    assert sell["timestamp"] == sell["signal_timestamp"] == bars[5].timestamp.isoformat()
    assert sell["reason"] == "wave_ordinary_equal_upper_shadow_reduce"
    assert sell["exit_target_fraction"] == 0.8
    assert final["timestamp"] == final["signal_timestamp"] == bars[8].timestamp.isoformat()
    assert final["reason"] == "wave_ordinary_equal_lower_close_clear"
    assert final["remaining_quantity"] == 0
    prefix = run_portfolio(
        {bars[0].symbol: bars[:6]}, signals=[signal], config=account, wave_events={bars[0].symbol: [ordinary]}
    )
    assert prefix.orders == [order for order in result.orders if order["timestamp"] <= bars[5].timestamp.isoformat()]


def test_ordinary_c_exit_keeps_signal_owner_when_fill_is_next_open():
    bars, ordinary = guofang_ordinary_wave()
    signal = Signal(
        bars[3].timestamp,
        bars[3].symbol,
        3,
        "LONG",
        bars[3].close,
        4.76,
        "system_wave_push_gap",
        bars[0].timestamp,
        0,
        None,
        "fixture",
        ordinary["target"],
    )
    config = StrategyConfig(
        wave_exhaustion_exit=True,
        exit_on_target=False,
        entry_at_close=False,
        max_hold_bars=100,
        slippage_bps_per_side=0,
        max_participation=1,
    )
    result = run_portfolio({bars[0].symbol: bars}, [signal], config, wave_events={bars[0].symbol: [ordinary]})
    filled = [order for order in result.orders if order["status"] == "filled"]
    assert [(order["timestamp"][:10], order["reason"]) for order in filled] == [
        ("2020-06-01", "system_wave_push_gap"),
        ("2020-06-02", "wave_ordinary_equal_upper_shadow_reduce"),
        ("2020-06-05", "wave_ordinary_equal_lower_close_clear"),
    ]


@pytest.mark.parametrize("case", ["no_target", "future_entry", "strong_a", "no_volume", "short_wick", "no_warning"])
def test_ordinary_c_exit_requires_owned_reached_target_and_abnormal_candle(case):
    bars, ordinary = guofang_ordinary_wave()
    if case == "no_target":
        ordinary["target"] = 5.71
    elif case == "future_entry":
        ordinary["bar_index"] = 6
    elif case == "strong_a":
        ordinary["two_t"] = 5.30
    elif case == "no_volume":
        bars[5] = replace(bars[5], volume=bars[4].volume)
    elif case == "short_wick":
        bars[5] = replace(bars[5], open=5.30, close=5.45)
    else:
        ordinary["event"] = "unrelated_entry"
    config = StrategyConfig(wave_exhaustion_exit=True)
    assert observe_wave_exhaustion(bars, 5, [ordinary], config, entry_index=3) is None
    if case in ("no_target", "future_entry", "strong_a", "no_warning"):
        assert observe_wave_exhaustion(bars, 8, [ordinary], config, entry_index=3) is None
