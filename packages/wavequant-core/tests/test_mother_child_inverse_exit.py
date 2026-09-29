"""A bullish mother-child pullback can complete an inverse N on a wick break."""

from datetime import datetime

from wavequant.application.analytics.backtest import run_backtest
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar, Signal
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals
from wavequant.domain.strategies.mother_child_inverse_n import mother_child_inverse_n_break


def guofang_march_2023() -> list[Bar]:
    factor = 1.2824530716204
    rows = [
        ("2023-03-22", 4.41, 4.43, 4.31, 4.37, 4_858_956),
        ("2023-03-23", 4.38, 4.40, 4.34, 4.40, 4_996_359),
        ("2023-03-24", 4.40, 4.41, 4.28, 4.31, 4_297_000),
    ]
    return [
        Bar(datetime.fromisoformat(day), "sh.601086", *(price * factor for price in (opening, high, low, close)),
            volume, adjustment_factor=factor)
        for day, opening, high, low, close, volume in rows
    ]


def test_guofang_march_24_breaks_bullish_child_of_bearish_mother_and_clears_position():
    bars = guofang_march_2023()
    assert bars[0].close < bars[0].open < bars[0].high
    assert bars[1].close > bars[1].open
    assert bars[2].high > bars[1].high and bars[2].volume < bars[1].volume
    evidence = mother_child_inverse_n_break(bars, 2)
    assert evidence is not None
    assert evidence["mother_date"] == "2023-03-22"
    assert evidence["child_date"] == "2023-03-23"
    assert evidence["child_low"] == bars[1].low
    assert evidence["child_close"] == bars[1].close

    strategy = SystemStrategy(pivot_mode="lecture_causal", entry_policy="hierarchical_two_buy_points",
                              buy_point_definition="whole_flip_wave_v3")
    generated = generate_system_signals(bars, strategy)
    assert any(signal.timestamp == bars[2].timestamp and
               "mother_child_inverse_n_low_break" in signal.reason for signal in generated.signals)

    entry = Signal(bars[1].timestamp, bars[1].symbol, 1, "LONG", bars[1].close, 5.2,
                   "fixture", bars[1].timestamp, 0, None, "fixture", 6.0)
    config = StrategyConfig(entry_at_close=True, inverse_n_close_reduce=True, volume_inverse_n_clear=True,
                            staged_exit_enabled=False, exit_on_target=False, max_participation=1,
                            risk_fraction=0.2, max_position_weight=0.8, slippage_bps_per_side=0)
    result = run_backtest(bars, [entry, *generated.signals], config)
    fills = [order for order in result.orders if order["status"] == "filled"]
    assert [(order["side"], order["timestamp"][:10]) for order in fills] == [
        ("BUY", "2023-03-23"), ("SELL", "2023-03-24"),
    ]
    assert fills[-1]["reason"] == "mother_child_inverse_n_low_break"
    assert fills[-1]["position_closed"] is True
    assert fills[-1]["remaining_quantity"] == 0
    assert result.open_positions == []


def guofang_july_2022() -> list[Bar]:
    factor = 1.2824530731884245
    rows = [
        ("2022-06-29", 3.84, 3.87, 3.81, 3.82, 6_098_000),
        ("2022-06-30", 3.82, 3.93, 3.82, 3.88, 7_554_177),
        ("2022-07-01", 3.88, 3.92, 3.85, 3.90, 5_414_431),
        ("2022-07-04", 3.89, 3.90, 3.84, 3.86, 4_077_500),
    ]
    return [
        Bar(datetime.fromisoformat(day), "sh.601086", *(price * factor for price in (opening, high, low, close)),
            volume, adjustment_factor=factor)
        for day, opening, high, low, close, volume in rows
    ]


def test_guofang_july_4_child_low_break_clears_at_close_without_volume_confirmation():
    bars = guofang_july_2022()
    strategy = SystemStrategy(
        pivot_mode="lecture_causal",
        entry_policy="hierarchical_two_buy_points",
        buy_point_definition="whole_flip_wave_v3",
    )
    generated = generate_system_signals(bars, strategy)
    exits = [signal for signal in generated.signals if signal.side == "EXIT"]
    assert [(signal.timestamp.date().isoformat(), signal.reason) for signal in exits] == [
        ("2022-07-04", "mother_child_inverse_n_low_break")
    ]

    entry = Signal(bars[1].timestamp, bars[1].symbol, 1, "LONG", bars[1].close, 4.5,
                   "fixture", bars[1].timestamp, 0, None, "fixture", 6.0)
    config = StrategyConfig(entry_at_close=True, inverse_n_close_reduce=True, volume_inverse_n_clear=True,
                            staged_exit_enabled=False, exit_on_target=False, max_participation=1,
                            risk_fraction=0.2, max_position_weight=0.8, slippage_bps_per_side=0)
    result = run_backtest(bars, [entry, *generated.signals], config)
    fills = [order for order in result.orders if order["status"] == "filled"]
    assert [(order["side"], order["timestamp"][:10]) for order in fills] == [
        ("BUY", "2022-06-30"), ("SELL", "2022-07-04")
    ]
    assert fills[-1]["reason"] == "mother_child_inverse_n_low_break"
    assert fills[-1]["execution_model"] == "same_day_close"
    assert fills[-1]["position_closed"] is True
    assert fills[-1]["price"] == bars[-1].close
    assert result.open_positions == []


def test_child_low_touch_and_bearish_child_do_not_claim_an_inverse_n():
    bars = guofang_july_2022()
    strategy = SystemStrategy(
        pivot_mode="lecture_causal",
        entry_policy="hierarchical_two_buy_points",
        buy_point_definition="whole_flip_wave_v3",
    )
    touching = [*bars[:-1], Bar(bars[-1].timestamp, bars[-1].symbol, bars[-1].open, bars[-1].high,
                               bars[-2].low, bars[-1].close, bars[-1].volume)]
    bearish_child = [bars[0], bars[1],
                     Bar(bars[2].timestamp, bars[2].symbol, bars[2].open, bars[2].high,
                         bars[2].low, bars[2].open - 0.01, bars[2].volume), bars[3]]
    mother = bars[1]
    doji_mother = [bars[0],
                   Bar(mother.timestamp, mother.symbol, mother.close, mother.high,
                       mother.low, mother.close, mother.volume), bars[2], bars[3]]
    for sample in (touching, bearish_child, doji_mother):
        generated = generate_system_signals(sample, strategy)
        assert not any("mother_child_inverse_n_low_break" in signal.reason for signal in generated.signals)


def test_child_low_break_still_exits_when_break_bar_makes_a_higher_high():
    bars = guofang_july_2022()
    bars[-1] = Bar(bars[-1].timestamp, bars[-1].symbol, bars[-1].open,
                   bars[-2].high + 0.01, bars[-1].low, bars[-1].close, bars[-1].volume)
    strategy = SystemStrategy(pivot_mode="lecture_causal", entry_policy="hierarchical_two_buy_points",
                              buy_point_definition="whole_flip_wave_v3")
    generated = generate_system_signals(bars, strategy)
    assert any("mother_child_inverse_n_low_break" in signal.reason for signal in generated.signals)


def guofang_august_2020() -> list[Bar]:
    factor = 1.0735341245715067
    rows = [
        ("2020-08-04", 5.38, 5.42, 5.33, 5.40, 9_423_000),
        ("2020-08-05", 5.40, 5.80, 5.34, 5.69, 11_996_775),
        ("2020-08-06", 5.58, 5.72, 5.51, 5.67, 12_981_873),
        ("2020-08-07", 5.61, 5.84, 5.48, 5.84, 16_943_324),
    ]
    return [
        Bar(datetime.fromisoformat(day), "sh.601086", *(price * factor for price in (opening, high, low, close)),
            volume, adjustment_factor=factor)
        for day, opening, high, low, close, volume in rows
    ]


def test_guofang_august_7_close_above_child_suppresses_child_low_exit():
    bars = guofang_august_2020()
    assert bars[-1].low < bars[-2].low
    assert bars[-1].close > bars[-2].close and bars[-1].high > bars[-2].high
    assert mother_child_inverse_n_break(bars, 3) is None
    strategy = SystemStrategy(pivot_mode="lecture_causal", entry_policy="hierarchical_two_buy_points",
                              buy_point_definition="whole_flip_wave_v3")
    generated = generate_system_signals(bars, strategy)
    assert not any("mother_child_inverse_n_low_break" in signal.reason for signal in generated.signals)

    entry = Signal(bars[1].timestamp, bars[1].symbol, 1, "LONG", bars[1].close, 5.0,
                   "fixture", bars[1].timestamp, 0, None, "fixture", 7.0)
    config = StrategyConfig(entry_at_close=True, inverse_n_close_reduce=True, volume_inverse_n_clear=True,
                            staged_exit_enabled=False, exit_on_target=False, max_participation=1,
                            risk_fraction=0.2, max_position_weight=0.8, slippage_bps_per_side=0)
    result = run_backtest(bars, [entry, *generated.signals], config)
    assert any(order["side"] == "BUY" and order["status"] == "filled" for order in result.orders)
    assert not any(order["side"] == "SELL" for order in result.orders)
    assert len(result.open_positions) == 1


def test_child_low_and_close_must_both_strictly_break_previous_bar():
    bars = guofang_august_2020()
    last, child = bars[-1], bars[-2]
    close_equal = Bar(last.timestamp, last.symbol, last.open, last.high, last.low,
                      child.close, last.volume)
    low_equal = Bar(last.timestamp, last.symbol, last.open, last.high, child.low,
                    child.close - 0.01, last.volume)
    close_lower = Bar(last.timestamp, last.symbol, last.open, last.high, last.low,
                      child.close - 0.01, last.volume)
    assert mother_child_inverse_n_break([*bars[:-1], close_equal], 3) is None
    assert mother_child_inverse_n_break([*bars[:-1], low_equal], 3) is None
    assert last.high > child.high
    assert mother_child_inverse_n_break([*bars[:-1], close_lower], 3) is not None
