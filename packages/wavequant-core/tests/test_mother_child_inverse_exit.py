"""A bullish mother-child pullback can complete an inverse N on a wick break."""

from datetime import datetime

from wavequant.application.analytics.backtest import run_backtest
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar, Signal
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals


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
    for sample in (touching, bearish_child):
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
