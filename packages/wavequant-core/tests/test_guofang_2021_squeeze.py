"""The defended March 22 N confirms the March 25 alternating squeeze, causally."""

from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path

import pytest

from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals
from wavequant.domain.strategies.strategy_profiles import whole_wave_profile


def guofang():
    raw = json.loads((Path(__file__).parent / "fixtures/guofang_2021_squeeze.json").read_text(encoding="utf-8"))
    bars = [Bar(datetime.fromisoformat(day), raw["symbol"], *values) for day, *values in raw["bars"]]
    dates = {str(bar.timestamp.date()): index for index, bar in enumerate(bars)}
    return bars, dates


def profile():
    return SystemStrategy(**whole_wave_profile({"scenarios": {"base": {"execution": {}}}})["strategy"])


def march_signals(bars):
    return [signal for signal in generate_system_signals(bars, profile()).signals
            if "2021-03-22" <= str(signal.timestamp.date()) <= "2021-03-26" and signal.side == "LONG"]


def test_march_25_record_squeeze_confirms_same_n_alternation_and_buy_point():
    bars, dates = guofang()
    result = generate_system_signals(bars, profile())
    attack, confirmation = dates["2021-03-22"], dates["2021-03-25"]
    signals = [signal for signal in result.signals if "2021-03-22" <= str(signal.timestamp.date()) <= "2021-03-26"
               and signal.side == "LONG"]
    assert len(signals) == 1
    assert signals[0].bar_index == confirmation
    assert signals[0].reason == "system_transition_squeeze"
    assert signals[0].trigger_timestamp == bars[attack].timestamp
    assert any(event["event"] == "n_completed" and event["bar_index"] == attack for event in result.audit)
    assert any(event["event"] == "squeeze_alternation_confirmed" and event["bar_index"] == confirmation
               and event["trend_level"] == 2 and event["attack"] == attack for event in result.audit)
    buy = next(event for event in result.audit if event["event"] == "long_signal"
               and event["bar_index"] == confirmation)
    assert buy["squeeze_confirmation"] == "resistance_record_break"
    assert buy["confirmation_low"] < buy["prior_virtual_low"]
    assert buy["confirmation_low"] > buy["stop"]
    assert buy["confirmation_close"] > buy["confirmation_record_high"]
    assert buy["observed_volume"] > buy["previous_volume"]
    assert march_signals(bars[:confirmation + 1]) == signals


@pytest.mark.parametrize("change", ["break_n_defense", "no_record_close", "fresh_resistance", "low_volume"])
def test_march_25_record_squeeze_still_needs_defense_clean_close_and_volume(change):
    bars, dates = guofang()
    index = dates["2021-03-25"]
    if change == "break_n_defense":
        bars[index] = replace(bars[index], low=4.07)
    elif change == "no_record_close":
        bars[index] = replace(bars[index], close=4.38)
    elif change == "fresh_resistance":
        bars[index] = replace(bars[index], open=4.45)
    elif change == "low_volume":
        bars[index] = replace(bars[index], volume=6_000_000)
    assert not any(signal.bar_index == index for signal in march_signals(bars[:index + 1]))
