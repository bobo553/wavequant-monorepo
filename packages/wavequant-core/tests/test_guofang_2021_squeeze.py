"""The March 22 N cannot borrow an old level-two alternation after a path change."""

from datetime import datetime
import json
from pathlib import Path

from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.hierarchical_entry import hierarchical_history
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


def test_march_25_record_squeeze_has_no_level_two_alternation_or_buy_point():
    bars, dates = guofang()
    result = generate_system_signals(bars, profile())
    attack, confirmation = dates["2021-03-22"], dates["2021-03-25"]
    levels, _ = hierarchical_history(bars)
    assert not levels[confirmation][2]
    assert any(event["event"] == "n_completed" and event["bar_index"] == attack for event in result.audit)
    assert not any(event["event"] == "squeeze_alternation_confirmed" and event["bar_index"] == confirmation
                   and event["attack"] == attack for event in result.audit)
    assert march_signals(bars) == march_signals(bars[:confirmation + 1]) == []
