"""A broken, high-volume bearish inside child keeps its mother as entry resistance."""

import json
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals
from wavequant.domain.strategies.bearish_mother_child_resistance import (
    bearish_mother_child_resistance_history,
)
from wavequant.domain.strategies.strategy_profiles import whole_wave_profile


def guilin_entry_window_bars() -> list[Bar]:
    fixture = Path(__file__).parent / "fixtures/guilin_2026_bearish_entry_window.json"
    data = json.loads(fixture.read_text(encoding="utf-8"))
    return [Bar(datetime.fromisoformat(day), data["symbol"], *values) for day, *values in data["bars"]]


def guilin_april_bars() -> list[Bar]:
    return guilin_entry_window_bars()[-26:]


def test_guilin_v3_april_entry_is_rejected_while_mother_resistance_remains():
    bars = guilin_entry_window_bars()
    config = SystemStrategy(**(
        whole_wave_profile({"scenarios": {"base": {"execution": {}}}})["strategy"]
        | {"volume_filter": False}
    ))

    result = generate_system_signals(bars, config)

    assert not any(
        signal.side == "LONG" and signal.timestamp.date().isoformat() in ("2026-04-15", "2026-04-16")
        for signal in result.signals
    )
    rejected = [
        event for event in result.audit
        if event["timestamp"].startswith("2026-04-16")
        and event["event"] == "entry_rejected"
        and event["reason"] == "bearish_mother_child_resistance_unresolved"
    ]
    assert rejected
    assert all(event["mother_date"] == "2026-04-09" for event in rejected)
    prefix = generate_system_signals(bars[:-1], config)
    assert prefix.signals == [signal for signal in result.signals if signal.bar_index < len(bars) - 1]


def test_guilin_bearish_mother_child_blocks_entry_until_mother_high_breaks():
    bars = guilin_april_bars()
    dates = {bar.timestamp.date().isoformat(): index for index, bar in enumerate(bars)}

    blocked = bearish_mother_child_resistance_history(bars)

    assert dates["2026-04-10"] not in blocked
    assert list(blocked) == [dates[day] for day in ("2026-04-13", "2026-04-14", "2026-04-15", "2026-04-16")]
    evidence = blocked[dates["2026-04-15"]]
    assert evidence["mother_date"] == "2026-04-09"
    assert evidence["child_date"] == "2026-04-10"
    assert evidence["break_date"] == "2026-04-13"
    assert evidence["mother_high"] == bars[dates["2026-04-09"]].high
    assert evidence["mother_volume_multiple"] > 2
    assert bars[dates["2026-04-13"]].close == bars[dates["2026-04-10"]].low
    assert bearish_mother_child_resistance_history(bars[:dates["2026-04-15"] + 1]) == {
        index: value for index, value in blocked.items() if index <= dates["2026-04-15"]
    }


def test_close_must_strictly_break_mother_high_to_release_pending_resistance():
    bars = guilin_april_bars()
    mother_high = next(bar.high for bar in bars if bar.timestamp.date().isoformat() == "2026-04-09")
    next_day = bars[-1].timestamp + timedelta(days=1)
    touch = replace(bars[-1], timestamp=next_day, open=mother_high - 0.2,
                    high=mother_high + 0.1, low=mother_high - 0.3, close=mother_high)
    breakout = replace(touch, timestamp=next_day + timedelta(days=3), close=mother_high + 0.01)

    blocked = bearish_mother_child_resistance_history([*bars, touch, breakout])

    assert len(bars) in blocked
    assert len(bars) + 1 not in blocked


def test_pattern_requires_bearish_inside_child_and_exceptional_mother_volume():
    bars = guilin_april_bars()
    mother_index = next(i for i, bar in enumerate(bars) if bar.timestamp.date().isoformat() == "2026-04-09")
    child_index = mother_index + 1
    prior_max = max(bar.volume for bar in bars[mother_index - 20:mother_index])
    ordinary_mother = [*bars]
    ordinary_mother[mother_index] = replace(bars[mother_index], volume=prior_max)
    bullish_child = [*bars]
    bullish_child[child_index] = replace(bars[child_index], close=bars[child_index].open + 0.01)
    outside_child = [*bars]
    outside_child[child_index] = replace(bars[child_index], high=bars[mother_index].high + 0.01)

    for invalid in (ordinary_mother, bullish_child, outside_child):
        assert bearish_mother_child_resistance_history(invalid) == {}


def test_record_volume_still_needs_twice_the_prior_twenty_day_mean():
    start = datetime(2026, 1, 1)
    prior = [Bar(start + timedelta(days=i), "TEST", 9, 9.5, 8.5, 9, 20) for i in range(20)]
    mother = Bar(start + timedelta(days=20), "TEST", 10, 11, 8, 9.5, 39)
    child = Bar(start + timedelta(days=21), "TEST", 9.5, 10.5, 8.5, 9, 15)
    break_bar = Bar(start + timedelta(days=22), "TEST", 9, 9.2, 8.4, 8.8, 16)

    assert bearish_mother_child_resistance_history([*prior, mother, child, break_bar]) == {}
    qualified = bearish_mother_child_resistance_history([
        *prior, replace(mother, volume=40), child, break_bar
    ])
    assert 22 in qualified


def test_later_child_break_starts_resistance_but_prior_mother_break_cancels_it():
    start = datetime(2026, 1, 1)
    prior = [Bar(start + timedelta(days=i), "TEST", 9, 9.5, 8.5, 9, 20) for i in range(20)]
    mother = Bar(start + timedelta(days=20), "TEST", 10, 11, 8, 9.5, 40)
    child = Bar(start + timedelta(days=21), "TEST", 9.5, 10.5, 8.5, 9, 15)
    pause = Bar(start + timedelta(days=22), "TEST", 9, 10, 8.6, 9.1, 16)
    break_bar = Bar(start + timedelta(days=23), "TEST", 9, 9.2, 8.4, 8.8, 18)

    blocked = bearish_mother_child_resistance_history([*prior, mother, child, pause, break_bar])
    assert 22 not in blocked
    assert blocked[23]["break_date"] == break_bar.timestamp.date().isoformat()

    recovered = replace(pause, high=11.2, low=8.6, close=11.1)
    assert bearish_mother_child_resistance_history([*prior, mother, child, recovered, break_bar]) == {}
