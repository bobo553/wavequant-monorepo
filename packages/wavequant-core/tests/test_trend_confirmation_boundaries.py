"""The public and developing entrances obey the same causal boundaries."""

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
from typing import cast

import pytest

from wavequant.domain.market_structure import secondary_trend, tertiary_trend
from wavequant.domain.market_structure.hierarchical_confirmation import TrendReference, market_trend_confirmation
from wavequant.domain.market_structure.hierarchical_development import hierarchical_developing_path
from wavequant.domain.market_structure.trend_confirmation import qualify_uptrend
from wavequant.domain.market_structure.trend_publication import publish_uptrends
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies import hierarchical_entry
from wavequant.domain.strategies.chart_entry_history import _has_confirmation_price_cross


START = datetime(2024, 1, 1)


def _point(index: int, kind: str, value: float, known: int) -> dict[str, object]:
    return dict(index=index, ordinal=0, time=(START + timedelta(days=index)).date().isoformat(),
                kind=kind, value=value, available_at=(START + timedelta(days=known)).date().isoformat(),
                state="confirmed", label=f"{kind}{index}")


def _sample() -> tuple[list[Bar], list[dict[str, object]]]:
    prices = [(10, 8, 9), (7, 5, 6), (9, 6, 8), (11, 8, 10.5), (10.8, 9, 10.2), (9.5, 8, 9)]
    bars = [Bar(START + timedelta(days=index), "sz.test", (high + low) / 2, high, low, close, 100)
            for index, (high, low, close) in enumerate(prices)]
    source = [_point(0, "H", 10, 0), _point(1, "L", 5, 2),
              _point(3, "H", 11, 4), _point(5, "L", 8, 5)]
    return bars, source


def test_development_cannot_use_a_same_level_key_first_known_on_the_breaking_session():
    bars, source = _sample()
    key = dict(source[0], available_at="2024-01-04", source_level1_position=0,
               wave_direction_after="down", confirmation_rule="structure")
    assert qualify_uptrend(source, source[1], 1, key, bars, 3) is None
    assert market_trend_confirmation(cast(Sequence[TrendReference], source), cast(TrendReference, key), 0, bars, 3) is None
    assert hierarchical_developing_path(
        dict(id="old", points=source), [key], trend_level=2, source_level=1, kind="secondary",
        bars=bars, end_index=3, structural=[key],
    ) is None


def test_a_candidate_cannot_publish_using_another_same_kind_source_origin():
    bars, source = _sample()
    bars.append(Bar(START + timedelta(days=6), "sz.test", 9, 10.5, 8.5, 10, 100))
    high = dict(source[0], source_level1_position=0, confirmation_rule="structure")
    wrong = dict(source[3], source_level1_position=1, confirmation_rule="structure")
    with pytest.raises(ValueError, match="source"):
        publish_uptrends([high, wrong], source, bars, source_level=1)


def test_a_new_confirmed_pressure_break_can_replace_an_older_invalidated_public_impulse():
    prices = [(13, 12.5, 13), (12.8, 12, 12.3), (13, 12.2, 12.8), (15, 13, 14),
              (14.8, 13, 14), (11, 8, 9), (10, 8.5, 9.5), (10, 8, 9.5),
              (9.5, 7, 8), (7, 5, 6), (9, 6, 8), (11, 6, 10.5)]
    bars = [Bar(START + timedelta(days=index), "sz.test", (high + low) / 2, high, low, close, 100)
            for index, (high, low, close) in enumerate(prices)]
    source = [_point(0, "H", 13, 0), _point(1, "L", 12, 2), _point(3, "H", 15, 4),
              _point(5, "L", 8, 6), _point(7, "H", 10, 8), _point(9, "L", 5, 10)]
    first = qualify_uptrend(source, source[1], 1, source[0], bars, 3)
    assert first is not None
    public_high = dict(source[2], source_level1_position=2, wave_direction_after="down",
                       incoming_trend_confirmation=first)
    local_low = dict(source[3], available_at="2024-01-09", source_level1_position=3, wave_direction_after="up")
    pressure = dict(source[4], available_at="2024-01-11", source_level1_position=4, wave_direction_after="down")
    tail = hierarchical_developing_path(
        dict(id="whole", points=source), [public_high], trend_level=2, source_level=1, kind="secondary",
        bars=bars, end_index=11, structural=[public_high, local_low, pressure],
    )
    assert tail is not None and tail["wave_direction"] == "up"
    assert tail["confirmation"]["origin"]["value"] == 5
    assert tail["confirmation"]["available_at"] == "2024-01-12"


@pytest.mark.parametrize("integer_clock", (False, True))
def test_the_first_session_after_key_confirmation_refreshes_a_newly_eligible_market_break(integer_clock):
    bars, all_source = _sample()
    source = all_source[:2]
    bars[4] = replace(bars[4], high=11.2, close=10.6)
    if integer_clock:
        source[0]["available_at"] = 0
        source[1]["available_at"] = 2
    key = dict(source[0], available_at=3 if integer_clock else "2024-01-04")
    assert qualify_uptrend(source, source[1], 1, key, bars, 3) is None
    proof = qualify_uptrend(source, source[1], 1, key, bars, 4)
    assert proof is not None and proof["available_at"] == (4 if integer_clock else "2024-01-05")
    level: dict[str, object] = dict(candidate_strokes=[dict(points=[key])])
    assert _has_confirmation_price_cross(source, ((level, {}),), bars[3], bars[4], "2024-01-05", 4)


@pytest.mark.parametrize("level", (2, 3))
def test_formal_publication_cannot_borrow_the_first_market_bar_of_the_next_source_path(level, monkeypatch):
    bars, source = _sample()
    source_level = level - 1
    position_field = f"source_level{source_level}_position"
    high = dict(source[0], available_at="2024-01-03", **{position_field: 0},
                wave_direction_before="up", wave_direction_after="down", flip="翻多为空",
                confirmation_rule="structure", broken_key=source[1])
    low = dict(source[1], **{position_field: 1}, wave_direction_before="down", wave_direction_after="up",
               flip="翻空为多", confirmation_rule="structure", broken_key=source[0])

    def candidates(points: Sequence[Mapping[str, object]], *, source_level: int = 1) -> list[dict[str, object]]:
        # The reducer's confirmed output isolates the path cutoff from its geometry.
        return deepcopy([high, low]) if points and points[0]["index"] == 0 else []

    module = secondary_trend if level == 2 else tertiary_trend
    monkeypatch.setattr(module, "_candidate_structural_reversals", candidates)
    lower = dict(strokes=[], structure_strokes=[dict(id="old", points=source[:2]),
                                               dict(id="next", points=source[2:])])
    result = module.secondary_trends(lower, bars) if level == 2 else module.tertiary_trends(lower, bars)
    old_public = next(path for path in result["strokes"] if path["source_path"] == "old")
    assert [point["kind"] for point in old_public["points"]] == ["H"]
    assert all(point.get("trend_confirmation") is None for point in old_public["points"])


def test_daily_history_keeps_confirmed_candidate_lows_for_the_next_level_without_publishing_their_rise(monkeypatch):
    bars, source = _sample()
    bars.extend([Bar(START + timedelta(days=6), "sz.test", 9, 10.5, 8.5, 10, 100),
                 Bar(START + timedelta(days=7), "sz.test", 10, 12, 9, 11.5, 100)])
    source[3]["available_at"] = 6
    for known, point in zip((0, 2, 4, 6), source):
        point["available_at"] = known
    seen = []

    def drawing(prefix, *, on_step):
        for index, bar in enumerate(prefix):
            raw = [dict(_point(0, "L", 8, 0), available_at=0, state="seed")]
            for point in source:
                known = point["available_at"]
                assert isinstance(known, int)
                if known <= index:
                    raw.append(dict(point, ordinal=1 if point["index"] == 0 else 0))
            kind = "L" if raw[-1]["kind"] == "H" else "H"
            raw.append(dict(_point(index, kind, bar.low if kind == "L" else bar.high, index),
                            available_at=index, state="developing"))
            on_step(index, 0, raw)

    def waves(points):
        if len(points) < 4:
            return []
        return [dict(point, available_at=6, source_turn_position=position,
                     confirmation_rule="structure", wave_direction_after="down" if point["kind"] == "H" else "up")
                for position, point in enumerate(points)]

    def candidates(points, *, source_level=1):
        if source_level != 1 or len(points) < 4:
            return []
        seen.append(tuple((point["kind"], point["index"], point["available_at"]) for point in points))
        return [dict(points[position], source_level1_position=position, confirmation_rule="structure")
                for position in (2, 3)]

    monkeypatch.setattr(hierarchical_entry, "lecture_drawing", drawing)
    monkeypatch.setattr(hierarchical_entry, "_wave_reversals", waves)
    monkeypatch.setattr(hierarchical_entry, "_candidate_structural_reversals", candidates)
    history, _ = hierarchical_entry.hierarchical_history(bars)
    assert ("L", 5, 6) in seen[0]
    assert [point["kind"] for point in history[6][2]] == ["H"]
    assert [(point["kind"], point["index"], point["available_at"]) for point in history[7][2]] == [
        ("H", 3, 6), ("L", 5, 7)]
    assert not any(point["kind"] == "L" and point["index"] == 5 for point in history[6][1])
    cache: dict[str, object] = {}
    assert hierarchical_entry.hierarchical_history(bars, prefix_cache=cache)[0] == history
    assert hierarchical_entry.hierarchical_history(bars, prefix_cache=cache)[0] == history
    following = [*bars, Bar(START + timedelta(days=8), "sz.test", 10, 12, 9.5, 11.3, 100)]
    assert hierarchical_entry.hierarchical_history(following, prefix_cache=cache) == (
        hierarchical_entry.hierarchical_history(following))
