"""Confirmed descents remain measurement facts without granting a public rise."""

from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import datetime, timedelta
import json
from pathlib import Path

import pytest

from wavequant.domain.market_structure import a_wave
from wavequant.domain.market_structure.a_wave import AWaveEvent, AWaveSeed, AWaveTurn
from wavequant.domain.market_structure.bottom_n_targets import (
    BottomNTargetHistory, DeclineStart, PositiveNCompletion, bottom_n_target_history,
)
from wavequant.domain.market_structure.n_shape import ValueDomain, project_n_targets
from wavequant.domain.market_structure.price_action import Direction
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies import hierarchical_entry, integrated_strategy
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals
from wavequant.domain.strategies.strategy_profiles import whole_wave_profile


@pytest.fixture(scope="module")
def xiangyang_declines():
    raw = json.loads((Path(__file__).parent / "fixtures" / "xiangyang_2025_trend_break.json").read_text(encoding="utf8"))
    bars = [Bar(datetime.fromisoformat(day), raw["symbol"], *prices)
            for day, *prices in raw["rows"] if day <= "2022-06-20"]
    dates = {str(bar.timestamp.date()): index for index, bar in enumerate(bars)}
    sink: dict[int, tuple[DeclineStart, ...]] = {}
    history, _ = hierarchical_entry.hierarchical_history(bars, decline_sink=sink)
    return bars, dates, history, sink


def _declines(sink: Mapping[int, tuple[DeclineStart, ...]]) -> list[DeclineStart]:
    return [DeclineStart(highs[-1].index, now) for now, highs in sink.items() if highs]


def test_june_two_high_is_a_known_june_nine_descent_without_a_qualified_public_rising_wave(xiangyang_declines):
    bars, dates, history, sink = xiangyang_declines
    high, known = dates["2022-06-02"], dates["2022-06-09"]
    assert bars[high].high == 5.31
    assert all(point.index != high for point in sink[dates["2022-06-08"]])
    assert sink[known][-1] == DeclineStart(high, known)
    assert sink[dates["2022-06-20"]][-1] == DeclineStart(high, known)
    assert not any(point["index"] == high for point in history[known][1])
    assert not any(point["index"] == dates["2022-04-27"] for point in history[known][1])
    assert all(point.known_at <= now for now, points in sink.items() for point in points)


def test_the_confirmed_descent_restores_the_june_twenty_floor_n_without_admitting_earlier_internal_ns(xiangyang_declines):
    bars, dates, history, sink = xiangyang_declines
    shapes = [("2022-05-10", "2022-05-13"), ("2022-05-12", "2022-05-18"),
              ("2022-05-17", "2022-05-23"), ("2022-05-24", "2022-06-02"),
              ("2022-06-14", "2022-06-20")]
    completions = [PositiveNCompletion(dates[origin], dates[attack], dates[attack]) for origin, attack in shapes]
    measured = bottom_n_target_history(bars, _declines(sink), completions)
    launch = dates["2022-06-20"]
    qualification = measured.qualifications[launch]
    assert qualification.eligible and qualification.reason == "first_n_at_decline_floor"
    assert qualification.decline_index == dates["2022-06-02"]
    assert qualification.bottom_index == dates["2022-06-14"]
    assert qualification.source_attack == launch
    assert all(not measured.qualifications[dates[attack]].eligible for _, attack in shapes[:-1])
    public_declines = [DeclineStart(highs[-1]["index"], now) for now, levels in history.items()
                       if (highs := [point for point in levels[1] if point["kind"] == "H"])]
    wrong = bottom_n_target_history(bars, public_declines, completions).qualifications[launch]
    assert not wrong.eligible and wrong.bottom_index == dates["2022-04-27"]
    targets = project_n_targets(
        bars[dates["2022-06-14"]].low, bars[dates["2022-06-15"]].high, bars[dates["2022-06-16"]].low,
        box_anchor=bars[launch].high, direction=Direction.UP, domain=ValueDomain.PRICE,
    )
    assert targets.one_p == 7.16 and targets.two_t == 8.53


def test_cached_partial_confirmation_cannot_borrow_the_final_june_nine_decline(xiangyang_declines):
    bars, dates, history, full_sink = xiangyang_declines
    prefix = bars[:dates["2022-06-09"] + 1]
    high = dates["2022-06-02"]
    cache: dict[str, object] = {}
    observed = full_sink.copy()
    original = hierarchical_entry.hierarchical_history(prefix, prefix_cache=cache, decline_sink=observed)
    assert max(observed) == len(prefix) - 1
    assert original[0][len(prefix) - 1] == history[len(prefix) - 1]
    assert observed[len(prefix) - 1][-1].index == high
    checkpoint = cache["checkpoint"]
    partial = [*prefix[:-1], replace(prefix[-2], timestamp=prefix[-1].timestamp)]
    cached = hierarchical_entry.hierarchical_history(partial, prefix_cache=cache, decline_sink=observed)
    fresh: dict[int, tuple[DeclineStart, ...]] = {}
    assert cached == hierarchical_entry.hierarchical_history(partial, decline_sink=fresh)
    assert observed == fresh
    assert all(point.index != high for point in observed[len(prefix) - 1])
    assert cache["checkpoint"] == checkpoint
    assert hierarchical_entry.hierarchical_history(prefix, prefix_cache=cache, decline_sink=observed) == original
    assert observed[len(prefix) - 1][-1].index == high


def test_requesting_the_decline_channel_invalidates_a_checkpoint_without_that_channel(monkeypatch):
    bars = [Bar(datetime(2024, 1, 1) + timedelta(days=index), "TEST", 14-index, 15-index, 13-index, 14-index, 100)
            for index in range(8)]
    cache: dict[str, object] = {}
    expected = hierarchical_entry.hierarchical_history(bars, prefix_cache=cache)
    original = hierarchical_entry._wave_reversals
    calls = []

    def counted(points):
        calls.append(1)
        return original(points)

    monkeypatch.setattr(hierarchical_entry, "_wave_reversals", counted)
    sink: dict[int, tuple[DeclineStart, ...]] = {}
    assert hierarchical_entry.hierarchical_history(bars, prefix_cache=cache, decline_sink=sink) == expected
    assert len(calls) == len(bars) and set(sink) == set(range(len(bars)))
    calls.clear()
    assert hierarchical_entry.hierarchical_history(bars, prefix_cache=cache, decline_sink=sink) == expected
    assert len(calls) == 1
    calls.clear()
    assert hierarchical_entry.hierarchical_history(bars, prefix_cache=cache) == expected
    assert len(calls) == len(bars)


def test_system_measurement_and_a_top_turns_use_the_decline_channel_when_public_history_has_no_high(monkeypatch):
    bars = [Bar(datetime(2024, 1, 1) + timedelta(days=index), "TEST", 14-index, 15-index, 13-index, 14-index, 100)
            for index in range(8)]
    source = DeclineStart(3, 5)

    def hierarchy(prefix: Sequence[Bar], *, prefix_cache: object = None,
                  decline_sink: dict[int, tuple[DeclineStart, ...]] | None = None):
        assert decline_sink is not None
        decline_sink.update({now: (source,) if now >= source.known_at else () for now in range(len(prefix))})
        return ({now: {level: () for level in (1, 2, 3)} for now in range(len(prefix))},
                {now: 0 for now in range(len(prefix))})

    measured: list[tuple[DeclineStart, ...]] = []
    real_bottom = integrated_strategy.bottom_n_target_history

    def bottom(prefix: Sequence[Bar], declines: Sequence[DeclineStart], ns: Sequence[PositiveNCompletion]) -> BottomNTargetHistory:
        measured.append(tuple(declines))
        return real_bottom(prefix, declines, ns)

    turns: list[AWaveTurn] = []
    real_a = a_wave.a_wave_history

    def observe_a(prefix: Sequence[Bar], seeds: Sequence[AWaveSeed], known_turns: Sequence[AWaveTurn]) -> tuple[AWaveEvent, ...]:
        turns.extend(known_turns)
        return real_a(prefix, seeds, known_turns)

    monkeypatch.setattr(hierarchical_entry, "hierarchical_history", hierarchy)
    monkeypatch.setattr(integrated_strategy, "bottom_n_target_history", bottom)
    monkeypatch.setattr(a_wave, "a_wave_history", observe_a)
    profile = whole_wave_profile({"scenarios": {"base": {"execution": {}}}})
    result = generate_system_signals(bars, SystemStrategy(**profile["strategy"]))
    assert result.signals == []
    assert measured and all(source in inputs for inputs in measured)
    assert turns == [AWaveTurn(source.index, source.known_at)]
