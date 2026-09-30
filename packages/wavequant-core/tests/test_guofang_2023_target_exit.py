"""Strong A continuation exits against its parent five-top and ten-full levels."""

from dataclasses import replace
from datetime import datetime

from wavequant.application.analytics.backtest import run_portfolio
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar, Signal
from wavequant.domain.strategies.wave_exhaustion_exit import observe_wave_exhaustion
from wavequant.interfaces.research_tools.stock_backtest import _post_b_wave_exit_events


def sample():
    # Raw TDX prices and volumes; the corresponding adjusted ten-full is 10.98516.
    rows = [
        ("2023-07-20", 4.90, 5.38, 4.90, 5.38, 49_777_665),
        ("2023-07-21", 5.37, 5.70, 5.18, 5.32, 73_203_163),
        ("2023-07-24", 5.13, 5.33, 4.98, 5.26, 47_724_598),
        ("2023-07-25", 5.26, 5.37, 5.17, 5.24, 33_760_865),
        ("2023-07-26", 5.19, 5.76, 5.10, 5.76, 44_259_187),
        ("2023-07-27", 5.99, 6.34, 5.57, 6.34, 102_641_052),
        ("2023-07-28", 6.03, 6.97, 5.92, 6.97, 98_659_162),
        ("2023-07-31", 7.51, 7.67, 6.88, 7.67, 78_280_856),
        ("2023-08-01", 8.10, 8.44, 7.69, 7.73, 121_024_185),
        ("2023-08-02", 7.27, 8.08, 7.27, 7.39, 96_441_827),
    ]
    bars = [Bar(datetime.fromisoformat(day), "sh.601086", *values) for day, *values in rows]
    audit = [
        dict(event="wave_projection_ready", attack=0, bar_index=0, two_t=5.14),
        dict(event="wave_projection_target_reached", attack=0, bar_index=5,
             reached_stage="five_top", reached_target=5.85),
        dict(event="wave_projection_ready", attack=4, origin_index=2,
             bar_index=8, two_t=8.2),
        dict(event="wave_projection_target_reached", attack=0, bar_index=8,
             reached_stage="ten_full", reached_target=8.41),
    ]
    signal_row = dict(event="long_signal", channel="wave_push_gap", attack=0,
                      bar_index=4, wave_b_low_index=2,
                      wave_entry_path="two_t_strong_a_resistance_rebreak")
    events = _post_b_wave_exit_events(bars, audit, signal_row)
    return bars, events


def test_ten_full_bearish_volume_clears_guofang_at_august_first_close():
    bars, events = sample()
    assert {event["attack"] for event in events} == {0, 4}
    decision = observe_wave_exhaustion(bars, 8, events, StrategyConfig(), entry_index=4)
    assert decision["reason"] == "wave_ten_full_bearish_volume_clear"
    assert decision["wave_reached_stage"] == "ten_full"
    assert decision["wave_reached_price"] == 8.41
    assert decision["bearish_reference_date"] == "2023-07-25"
    assert decision["bearish_reference_volume"] == 33_760_865
    assert decision["observed_volume"] == 121_024_185
    assert bars[8].close > bars[7].close  # A positive daily return remains a bearish body.

    signal = Signal(bars[4].timestamp, bars[4].symbol, 4, "LONG", bars[4].close,
                    4.0, "system_wave_push_gap", bars[0].timestamp, 0, None,
                    "fixture", 12.0)
    config = StrategyConfig(entry_at_close=True, wave_exhaustion_exit=True,
                            exit_on_target=False, max_hold_bars=100,
                            max_participation=1, slippage_bps_per_side=0)
    kwargs = dict(signals=[signal], config=config,
                  wave_events={bars[0].symbol: events})
    full = run_portfolio({bars[0].symbol: bars}, **kwargs)
    filled = [order for order in full.orders if order["status"] == "filled"]
    assert [(order["timestamp"][:10], order["reason"]) for order in filled] == [
        ("2023-07-26", "system_wave_push_gap"),
        ("2023-08-01", "wave_ten_full_bearish_volume_clear"),
    ]
    assert filled[-1]["remaining_quantity"] == 0
    assert filled[-1]["price"] == bars[8].close
    prefix = run_portfolio({bars[0].symbol: bars[:9]}, **kwargs)
    assert prefix.orders == [order for order in full.orders
                             if order["timestamp"] <= bars[8].timestamp.isoformat()]


def test_target_bearish_candle_reduces_seventy_percent_without_volume_expansion():
    bars, events = sample()
    bars[5] = replace(bars[5], open=6.20, close=6.10, volume=20_000_000)
    decision = observe_wave_exhaustion(bars, 5, events, StrategyConfig(), entry_index=4)
    assert decision["reason"] == "wave_target_bearish_reduce"
    assert decision["wave_reached_stage"] == "five_top"
    assert decision["exit_target_fraction"] == 0.7
    assert decision["observed_volume"] < bars[4].volume
    assert observe_wave_exhaustion(bars, 5, events, StrategyConfig(),
                                   reduced=True, entry_index=4) is None

    signal = Signal(bars[4].timestamp, bars[4].symbol, 4, "LONG", bars[4].close,
                    4.0, "system_wave_push_gap", bars[0].timestamp, 0, None,
                    "fixture", 12.0)
    config = StrategyConfig(entry_at_close=True, wave_exhaustion_exit=True,
                            exit_on_target=False, max_hold_bars=100,
                            max_participation=1, slippage_bps_per_side=0)
    result = run_portfolio({bars[0].symbol: bars}, [signal], config,
                           wave_events={bars[0].symbol: events})
    buy, reduction, clear = [order for order in result.orders
                             if order["status"] == "filled"]
    assert reduction["reason"] == "wave_target_bearish_reduce"
    assert reduction["quantity"] == int(buy["quantity"] * 0.7 // config.lot_size) * config.lot_size
    assert clear["reason"] == "wave_ten_full_bearish_volume_clear"
    assert clear["quantity"] == reduction["remaining_quantity"]
    assert clear["remaining_quantity"] == 0

    bars, events = sample()
    bars[8] = replace(bars[8], volume=33_760_865)
    decision = observe_wave_exhaustion(bars, 8, events, StrategyConfig(), entry_index=4)
    assert decision["reason"] == "wave_target_bearish_reduce"
    assert decision["wave_reached_stage"] == "ten_full"
    assert decision["exit_target_fraction"] == 0.7


def test_ten_full_clear_requires_current_high_to_touch_target():
    bars, events = sample()
    bars[7] = replace(bars[7], high=8.50)
    bars[8] = replace(bars[8], high=8.40)
    for event in events:
        if event.get("reached_stage") == "ten_full":
            event["bar_index"] = 7
    decision = observe_wave_exhaustion(bars, 8, events, StrategyConfig(), entry_index=4)
    assert decision["reason"] == "wave_target_bearish_reduce"
