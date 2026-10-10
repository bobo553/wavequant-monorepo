"""The public and developing entrances obey the same causal boundaries."""

from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
from typing import cast

import pytest

from wavequant.domain.market_structure.hierarchical_confirmation import TrendReference, market_trend_confirmation
from wavequant.domain.market_structure.hierarchical_development import hierarchical_developing_path
from wavequant.domain.market_structure.trend_confirmation import qualify_uptrend
from wavequant.domain.market_structure.trend_judgement import TrendJudgement, judge_trend
from wavequant.domain.market_structure.trend_levels import Moment
from wavequant.domain.market_structure.trend_lines import qualify_trend_structure
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
def test_qualification_cannot_borrow_the_first_market_bar_of_the_next_source_path(level: int) -> None:
    bars, source = _sample()
    source_level = level - 1
    position_field = f"source_level{source_level}_position"
    high = dict(source[0], available_at="2024-01-03", trend_level=level, **{position_field: 0},
                wave_direction_before="up", wave_direction_after="down", flip="翻多为空",
                confirmation_rule="structure", broken_key=source[1])
    low = dict(source[1], trend_level=level, **{position_field: 1}, wave_direction_before="down",
               wave_direction_after="up", flip="翻空为多", confirmation_rule="structure", broken_key=source[0])
    candidates, old_source = [high, low], source[:2]
    original_candidates, original_source = deepcopy(candidates), deepcopy(old_source)
    cutoff = cast(int, source[2]["index"])
    # 这里只核验已知本级结构的独立资格；下一路径的首根 K 不属于旧路径前缀。
    old_permissions = qualify_trend_structure(candidates, old_source, bars[:cutoff], trend_level=level)
    assert [(point["index"], point["kind"]) for point in old_permissions] == [(0, "H")]
    assert all(point.get("trend_confirmation") is None for point in old_permissions)

    full_permissions = qualify_trend_structure(candidates, old_source, bars[:cutoff + 1], trend_level=level)
    assert [(point["index"], point["kind"]) for point in full_permissions] == [(0, "H"), (1, "L")]
    licensed_low = full_permissions[1]
    proof = cast(Mapping[str, object], licensed_low["trend_confirmation"])
    attack = cast(Mapping[str, object], proof["confirmed_by"])
    key = cast(Mapping[str, object], proof["broken_key"])
    assert licensed_low["available_at"] == proof["available_at"] == "2024-01-04"
    assert proof["direction"] == "up" and proof["confirmation_rule"] == "strict_same_level_market_key_break"
    assert (attack["index"], attack["kind"], attack["value"], attack["time"]) == (3, "H", 11, "2024-01-04")
    assert (key["index"], key["kind"], key["value"], key["available_at"]) == (0, "H", 10, "2024-01-03")
    assert candidates == original_candidates and old_source == original_source


def _coordinates(points: Sequence[Mapping[str, object]]) -> tuple[tuple[int, int, str, float, int], ...]:
    return tuple((cast(int, point["index"]), cast(int, point["ordinal"]), cast(str, point["kind"]),
                  cast(float, point["value"]), cast(int, point["available_at"])) for point in points)


def test_daily_history_higher_level_uses_complete_lower_level_geometry_independently_of_permissions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = (20, 30, 10, 20, 14, 26, 18, 35, 28, 45, 38, 42, 32, 36, 24, 29, 26, 33, 30, 42, 34)
    following: list[Bar] = []
    for index, value in enumerate(values):
        low = value if index % 2 == 0 else value - 1
        high = 40 if index == 0 else value + 1 if index % 2 == 0 else value
        following.append(Bar(START + timedelta(days=index), "sz.test", (high + low) / 2,
                             high, low, (high + low) / 2, 100))
    bars = following[:-1]
    seen: dict[tuple[int, int], tuple[tuple[int, int, str, float, int], ...]] = {}

    def drawing(prefix: Sequence[Bar], *, on_step: Callable[[int, int, list[dict[str, object]]], None]) -> None:
        # 仅控制绘图端口；全部真实峰谷、统一判断与行情资格规则仍执行生产实现。
        for index, _ in enumerate(prefix):
            raw = [dict(_point(0, "H", 40, 0), state="seed")]
            for source_index in range(index):
                raw.append(dict(_point(source_index, "L" if source_index % 2 == 0 else "H",
                                       values[source_index], source_index + 1),
                                ordinal=1 if source_index == 0 else 0))
            raw.append(dict(_point(index, "L" if index % 2 == 0 else "H", values[index], index),
                            state="developing", ordinal=1 if index == 0 else 0))
            on_step(index, 0, raw)

    def observe_judgement(
        points: Sequence[Mapping[str, object]], *, trend_level: int = 1, asof: Moment | None = None,
    ) -> TrendJudgement:
        assert type(asof) is int
        seen[(asof, trend_level)] = _coordinates(points)
        return judge_trend(points, trend_level=trend_level, asof=asof)

    monkeypatch.setattr(hierarchical_entry, "lecture_drawing", drawing)
    monkeypatch.setattr(hierarchical_entry, "judge_trend", observe_judgement)
    structures: dict[int, dict[int, tuple[dict[str, object], ...]]] = {}
    history, epochs = hierarchical_entry.hierarchical_history(bars, structure_sink=structures)
    for now in range(len(bars)):
        for level in (2, 3):
            assert seen[(now, level)] == _coordinates(structures[now][level - 1])
            assert all(point[-1] <= now for point in seen[(now, level)])
    # 结构首次可知日由两组严格比较齐备决定，策略资格保持独立。
    assert structures[5][1] == ()
    assert _coordinates(structures[6][1]) == ((2, 0, "L", 10, 6),)
    assert _coordinates(structures[7][1]) == ((2, 0, "L", 10, 6),)
    assert history[6][1] == ()
    assert _coordinates(structures[12][1]) == ((2, 0, "L", 10, 6),)
    assert _coordinates(structures[13][1]) == ((2, 0, "L", 10, 6), (9, 0, "H", 45, 13))
    assert _coordinates(structures[17][1]) == ((2, 0, "L", 10, 6), (9, 0, "H", 45, 13))
    assert _coordinates(structures[18][1]) == ((2, 0, "L", 10, 6), (9, 0, "H", 45, 13), (14, 0, "L", 24, 18))
    assert seen[(19, 2)] == ((2, 0, "L", 10, 6), (9, 0, "H", 45, 13), (14, 0, "L", 24, 18))
    assert _coordinates(history[19][1]) == ((9, 0, "H", 45, 13),)
    assert seen[(19, 2)] != _coordinates(history[19][1])

    for end in (8, 15, 19):
        prefix_structures: dict[int, dict[int, tuple[dict[str, object], ...]]] = {}
        prefix_history, prefix_epochs = hierarchical_entry.hierarchical_history(
            bars[:end], structure_sink=prefix_structures)
        assert prefix_history == {now: history[now] for now in range(end)}
        assert prefix_epochs == {now: epochs[now] for now in range(end)}
        assert prefix_structures == {now: structures[now] for now in range(end)}

    cache: dict[str, object] = {}
    for _ in range(2):
        cached_structures: dict[int, dict[int, tuple[dict[str, object], ...]]] = {}
        assert hierarchical_entry.hierarchical_history(bars, prefix_cache=cache,
            structure_sink=cached_structures) == (history, epochs)
        assert cached_structures == structures
    warm_structures: dict[int, dict[int, tuple[dict[str, object], ...]]] = {}
    fresh_structures: dict[int, dict[int, tuple[dict[str, object], ...]]] = {}
    assert hierarchical_entry.hierarchical_history(following, prefix_cache=cache, structure_sink=warm_structures) == (
        hierarchical_entry.hierarchical_history(following, structure_sink=fresh_structures))
    assert warm_structures == fresh_structures
