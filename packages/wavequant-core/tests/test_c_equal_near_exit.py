"""The July C equal-wave one-tick miss still carries dated exit risk."""

from dataclasses import asdict, replace
from datetime import datetime
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from wavequant.application.analytics.backtest import run_portfolio
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals
from wavequant.domain.strategies.strategy_profiles import whole_wave_profile
from wavequant.domain.strategies.wave_exhaustion_exit import (
    C_EQUAL_NEAR_RESISTANCE_REDUCE, C_EQUAL_NEAR_VOLUME_CLEAR, observe_c_equal_near_risk,
)
from wavequant.interfaces.charts.visualization import ChartRepository
from wavequant.interfaces.research_tools.stock_backtest import single_stock_result


@pytest.fixture(scope="module")
def july_case():
    raw = json.loads((Path(__file__).parent / "fixtures/xianfeng_2026_strong_squeeze.json").read_text(encoding="utf-8"))
    bars = [Bar(datetime.fromisoformat(day), raw["symbol"], *values)
            for day, *values in raw["bars"] if day <= "2026-07-10"]
    dates = {bar.timestamp.date().isoformat(): index for index, bar in enumerate(bars)}
    profile = whole_wave_profile({"scenarios": {"base": {"execution": {}}}})
    strategy = SystemStrategy(**profile["strategy"])
    result = generate_system_signals(bars, strategy)
    events = [event for event in result.audit if event["event"] == "wave_c_equal_target"]
    return bars, dates, strategy, result, events


def test_july_six_warning_and_july_eight_exit_are_in_real_signal_stream(july_case):
    bars, dates, strategy, result, events = july_case
    warning_index, clear_index = dates["2026-07-06"], dates["2026-07-08"]
    entry = next(event for event in result.audit if event["event"] == "long_signal"
                 and event["timestamp"].startswith("2026-07-03"))
    assert (entry["wave_a_class"], entry["wave_equal_target"]) == ("strong", 8.57)
    assert (bars[warning_index].high, bars[warning_index].volume) == (8.56, 99_270_800)
    warning = next(event for event in result.audit if event["event"] == "c_equal_near_risk_observed"
                   and event["bar_index"] == warning_index)
    assert warning["reason"] == C_EQUAL_NEAR_RESISTANCE_REDUCE
    assert warning["wave_target_gap"] == pytest.approx(0.01)
    assert "long_upper_shadow" in warning["target_resistance_patterns"]
    assert warning["wave_upper_shadow_fraction"] > 0.7
    signal = next(signal for signal in result.signals
                  if signal.side == "EXIT" and signal.bar_index == clear_index)
    assert signal.reason == C_EQUAL_NEAR_VOLUME_CLEAR
    clear = next(event for event in result.audit if event["event"] == "exit_signal"
                 and event["bar_index"] == clear_index)
    assert (clear["target_warning_date"], clear["previous_low"], clear["previous_close"],
            clear["bearish_reference_date"], clear["bearish_reference_volume"]) == (
        "2026-07-06", 7.4, 8.15, "2026-06-30", 18_267_900)
    assert (clear["observed_low"], clear["observed_close"], clear["observed_volume"]) == (
        6.58, 7.07, 82_443_254)
    chart = ChartRepository.render_theory(
        None, bars[:clear_index + 1], strategy, result, "2026-07-08",
        geometry={"tertiary_trends": {}},
    )
    assert any(event["event"] == "exit_signal" and event["bar_index"] == clear_index
               and event["reason"] == C_EQUAL_NEAR_VOLUME_CLEAR for event in chart["events"])
    assert any(event["event"] == "c_equal_near_risk_observed" and event["bar_index"] == warning_index
               for event in chart["events"])


def test_july_holding_reduces_then_clears_at_completed_closes(july_case):
    bars, dates, _, result, events = july_case
    buy = next(signal for signal in result.signals if signal.side == "LONG"
               and signal.timestamp.date().isoformat() == "2026-07-03")
    clear = next(signal for signal in result.signals if signal.side == "EXIT"
                 and signal.timestamp.date().isoformat() == "2026-07-08")
    config = StrategyConfig(entry_at_close=True, wave_exhaustion_exit=True, exit_on_target=False,
                            staged_exit_enabled=False, pressure_adverse_exit=False,
                            trend_flip_adverse_exit=False, volume_down_exit=False,
                            net_reward_risk_filter=False, max_participation=1,
                            slippage_bps_per_side=0)
    kwargs = dict(signals=[buy, clear], config=config,
                  wave_events={bars[0].symbol: events})
    portfolio = run_portfolio({bars[0].symbol: bars[:dates["2026-07-08"] + 1]}, **kwargs)
    filled = [order for order in portfolio.orders if order["status"] == "filled"]
    assert [(order["side"], order["timestamp"][:10], order["reason"], order["price"])
            for order in filled] == [
        ("BUY", "2026-07-03", buy.reason, 7.5),
        ("SELL", "2026-07-06", C_EQUAL_NEAR_RESISTANCE_REDUCE, 7.98),
        ("SELL", "2026-07-08", C_EQUAL_NEAR_VOLUME_CLEAR, 7.07),
    ]
    assert filled[1]["exit_target_fraction"] == config.wave_exhaustion_reduction
    assert filled[1]["remaining_quantity"] > 0
    assert filled[2]["quantity"] == filled[1]["remaining_quantity"]
    assert filled[2]["remaining_quantity"] == 0
    assert filled[1]["execution_model"] == filled[2]["execution_model"] == "same_day_close"
    stock = single_stock_result(
        bars[:dates["2026-07-08"] + 1], {}, asdict(config),
        SimpleNamespace(signals=[buy, clear], audit=events, counts={}),
    )
    assert [(order["timestamp"][:10], order["reason"])
            for order in stock["orders"] if order["status"] == "filled"] == [
        ("2026-07-03", buy.reason),
        ("2026-07-06", C_EQUAL_NEAR_RESISTANCE_REDUCE),
        ("2026-07-08", C_EQUAL_NEAR_VOLUME_CLEAR),
    ]
    for date in ("2026-07-06", "2026-07-07"):
        end = dates[date]
        prefix = run_portfolio(
            {bars[0].symbol: bars[:end + 1]},
            [signal for signal in (buy, clear) if signal.bar_index <= end], config,
            wave_events={bars[0].symbol: [event for event in events if event["bar_index"] <= end]},
        )
        assert prefix.orders == [order for order in portfolio.orders if order["timestamp"][:10] <= date]


def test_near_equal_requires_one_tick_resistance_and_later_strict_volume_break(july_case):
    bars, dates, _, _, events = july_case
    warning, clear = dates["2026-07-06"], dates["2026-07-08"]
    selected = [event for event in events if event["bar_index"] == dates["2026-07-03"]]
    assert observe_c_equal_near_risk(bars, warning, selected)["reason"] == C_EQUAL_NEAR_RESISTANCE_REDUCE
    assert observe_c_equal_near_risk(bars, dates["2026-07-07"], selected) is None
    assert observe_c_equal_near_risk(bars, clear, selected)["reason"] == C_EQUAL_NEAR_VOLUME_CLEAR
    assert observe_c_equal_near_risk(bars, dates["2026-07-09"], selected) is None
    assert observe_c_equal_near_risk(bars, clear, [dict(selected[0], defense=7.5)]) is None
    for changed_warning in (replace(bars[warning], high=8.55),
                            replace(bars[warning], high=8.58),
                            replace(bars[warning], close=8.4)):
        altered = list(bars)
        altered[warning] = changed_warning
        assert observe_c_equal_near_risk(altered, warning, selected) is None
        assert observe_c_equal_near_risk(altered, clear, selected) is None
    for changed_bars in (
        {clear: replace(bars[clear], volume=18_267_900)},
        {dates["2026-07-07"]: replace(bars[dates["2026-07-07"]], low=6.58)},
        {dates["2026-07-07"]: replace(bars[dates["2026-07-07"]], low=7.0, close=7.07)},
    ):
        altered = list(bars)
        for index, bar in changed_bars.items():
            altered[index] = bar
        assert observe_c_equal_near_risk(altered, clear, selected) is None
