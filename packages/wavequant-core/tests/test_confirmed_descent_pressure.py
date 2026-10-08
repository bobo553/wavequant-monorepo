"""Downward hierarchy facts remain resistance without receiving upward permission."""

from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest

from wavequant.domain.market_structure.lecture_drawing import lecture_drawing
from wavequant.domain.market_structure.lecture_trend import reversal_trends
from wavequant.domain.market_structure.secondary_trend import secondary_trends
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.chart_entry_history import _confirmed_descent_pressures, chart_entry_history

from .test_trend_confirmation_entry_refresh import bars_fixture, empty_level, fake_drawing


def _pressure(**changes: object) -> dict[str, object]:
    return dict(index=1, kind="H", value=6.0, state="confirmed", available_at="2026-01-03",
                wave_direction_after="down", trend_level=2,
                confirmation_rule="level1_structural_key_break") | changes


def _level(point: Mapping[str, object], *, public: bool = False) -> dict[str, object]:
    return dict(strokes=[dict(id="second", points=[dict(point)])] if public else [],
                structure_strokes=[dict(id="second", points=[dict(point)])], bear_bull_alternation_lows=[])


def _dates(bars: Sequence[Bar]) -> dict[str, int]:
    return {bar.timestamp.date().isoformat(): index for index, bar in enumerate(bars)}


def test_private_descent_requires_its_confirmation_day_and_preserves_evidence() -> None:
    dates = _dates(bars_fixture())
    point = _pressure()
    level = _level(point)

    assert _confirmed_descent_pressures(level, dates, 1) == []
    result = _confirmed_descent_pressures(level, dates, 2)
    assert result == [dict(index=1, value=6.0, source="confirmed_descent", available_at="2026-01-03",
                           known_index=2, confirmation_rule="level1_structural_key_break")]
    assert level["strokes"] == []
    assert _confirmed_descent_pressures(level, dates, 3) == result
    assert _confirmed_descent_pressures(_level(_pressure(available_at=2)), dates, 2)[0]["known_index"] == 2


@pytest.mark.parametrize("changes", [
    {"available_at": None}, {"available_at": "2026-01-05"}, {"available_at": 4},
    {"available_at": "unknown"}, {"available_at": True}, {"available_at": 0},
    {"wave_direction_after": None}, {"wave_direction_after": "up"},
    {"state": "developing"}, {"state": "seed"}, {"state": "unknown"},
    {"display_only": True}, {"trend_level": 1}, {"kind": "L"}, {"index": True},
    {"index": 4}, {"value": float("nan")}, {"value": float("inf")},
])
def test_unknown_unconfirmed_and_other_level_highs_do_not_supply_pressure(changes: dict[str, object]) -> None:
    assert _confirmed_descent_pressures(_level(_pressure(**changes)), _dates(bars_fixture()), 3) == []


def test_legacy_structure_fallback_does_not_read_raw_lower_level_highs() -> None:
    point = _pressure()
    dates = _dates(bars_fixture())
    legacy: dict[str, object] = dict(strokes=[dict(points=[point])])
    assert _confirmed_descent_pressures(legacy, dates, 3)
    assert _confirmed_descent_pressures(dict(legacy, structure_strokes=[]), dates, 3) == []
    assert _confirmed_descent_pressures(dict(structure_strokes=[dict(points=[point], display_only=True)]),
                                       dates, 3) == []


def _private_second(_first: object, bars: Sequence[Bar]) -> dict[str, object]:
    return _level(_pressure())


def _public_second(_first: object, bars: Sequence[Bar]) -> dict[str, object]:
    return _level(_pressure(), public=True)


def test_chart_uses_private_pressure_without_publishing_an_entry_context_and_invalidates_old_cache() -> None:
    bars = bars_fixture()
    old_cache: dict[str, object] = dict(
        key=(tuple(bars[:-1]), frozenset(), False, False, False, "daily_trend_confirmation_v1"),
        checkpoint="obsolete",
    )
    with patch("wavequant.domain.strategies.chart_entry_history.lecture_drawing", side_effect=fake_drawing), \
            patch("wavequant.domain.strategies.chart_entry_history.reversal_trends", side_effect=empty_level), \
            patch("wavequant.domain.strategies.chart_entry_history.secondary_trends", side_effect=_private_second), \
            patch("wavequant.domain.strategies.chart_entry_history.tertiary_trends", side_effect=empty_level):
        history, events = chart_entry_history(bars, prefix_cache=old_cache)
        assert all(not entries for entries in history.values())
        assert len(events) == 1
        key = cast(Mapping[str, object], events[0]["key"])
        assert (events[0]["bar_index"], key["index"], key["source"]) == (2, 1, "confirmed_descent")
        assert chart_entry_history(bars[:3])[1] == events
        partial = [*bars[:-1], replace(bars[-1], high=6.7)]
        assert chart_entry_history(partial, prefix_cache=old_cache) == chart_entry_history(partial)


def test_public_high_keeps_formal_audit_source_when_it_is_also_a_descending_high() -> None:
    with patch("wavequant.domain.strategies.chart_entry_history.lecture_drawing", side_effect=fake_drawing), \
            patch("wavequant.domain.strategies.chart_entry_history.reversal_trends", side_effect=empty_level), \
            patch("wavequant.domain.strategies.chart_entry_history.secondary_trends", side_effect=_public_second), \
            patch("wavequant.domain.strategies.chart_entry_history.tertiary_trends", side_effect=empty_level):
        _, events = chart_entry_history(bars_fixture())
    key = cast(Mapping[str, object], events[0]["key"])
    assert key == dict(index=1, value=6.0, source="formal")


@pytest.fixture(scope="module")
def lexin() -> tuple[Bar, ...]:
    raw = json.loads((Path(__file__).parent / "fixtures/lexin_2026_squeeze.json").read_text(encoding="utf-8"))
    return tuple(Bar(datetime.fromisoformat(day), raw["symbol"], *prices) for day, *prices in raw["bars"])


def _points(level: Mapping[str, object], field: str) -> list[Mapping[str, object]]:
    paths = cast(Sequence[Mapping[str, object]], level[field])
    return [point for path in paths for point in cast(Sequence[Mapping[str, object]], path["points"])]


@pytest.mark.parametrize("asof", ["2026-04-16", "2026-04-17", "2026-07-02"])
def test_lexin_january_pressure_is_known_only_in_april_and_april_source_high_is_not_secondary(
    lexin: tuple[Bar, ...], asof: str,
) -> None:
    bars = [bar for bar in lexin if bar.timestamp.date().isoformat() <= asof]
    first = reversal_trends(lecture_drawing(bars), bars)
    second = secondary_trends(first, bars)
    dates = _dates(bars)
    pressures = _confirmed_descent_pressures(second, dates, len(bars) - 1)
    january = [point for point in pressures if point["index"] == dates["2026-01-14"]]

    assert not any(point["time"] == "2026-01-14" for point in _points(second, "strokes"))
    if asof == "2026-04-16":
        assert january == []
        return
    assert len(january) == 1
    pressure = january[0]
    assert pressure["available_at"] == "2026-04-17"
    assert pressure["value"] == pytest.approx(18.642107088020254)
    assert pressure["confirmation_rule"] == "level1_structural_key_break"
    key, turn = cast(Mapping[str, object], pressure["broken_key"]), cast(Mapping[str, object], pressure["confirmed_by"])
    assert (key["time"], key["available_at"]) == ("2025-12-16", "2026-01-08")
    assert (turn["time"], turn["available_at"]) == ("2026-03-23", "2026-04-17")
    assert cast(float, turn["value"]) < cast(float, key["value"])
    if asof == "2026-07-02":
        assert any(point["time"] == "2026-04-30" for point in _points(first, "structure_strokes"))
        assert not any(point["time"] == "2026-04-30" for point in _points(second, "structure_strokes"))
        assert max(pressures, key=lambda point: cast(int, point["index"])) == pressure
        assert cast(float, pressure["value"]) > bars[-1].high
