"""同坐标的结构升级与晚到反向定义各自保存真实知识日和来源证据。"""

from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from datetime import datetime, timedelta
from typing import cast

import pytest

from wavequant.domain.market_structure.bottom_n_targets import DeclineStart
from wavequant.domain.market_structure.trend_judgement import judge_trend
from wavequant.domain.market_structure.trend_structure import StructuralTrend
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies import hierarchical_entry


START = datetime(2024, 1, 1)
VALUES = (20, 30, 10, 20, 14, 26, 18, 35, 28, 45, 38, 42, 32, 36, 24, 29, 26, 50, 30, 48, 28, 46)


def _point(index: int, kind: str, value: float, known: int) -> dict[str, object]:
    return dict(index=index, ordinal=0, time=(START + timedelta(days=index)).date().isoformat(),
                kind=kind, value=value, available_at=known, state="confirmed", label=f"{kind}{index}", trend_level=0)


def _source() -> list[dict[str, object]]:
    return [_point(index, "L" if index % 2 == 0 else "H", value, index + 1)
            for index, value in enumerate(VALUES)]


def _reference(point: Mapping[str, object]) -> tuple[int, str, float, int]:
    return (cast(int, point["index"]), cast(str, point["kind"]),
            cast(float, point["value"]), cast(int, point["available_at"]))


def test_later_negative_reversal_preserves_its_own_fact_without_delaying_the_earlier_upgrade() -> None:
    source = _source()
    original = deepcopy(source)
    judgement = judge_trend(source, trend_level=1, asof=22)
    facts = [point for point in judgement.candidates if point["index"] == 17 and point["kind"] == "H"]
    assert len(facts) == 2
    early, late = sorted(facts, key=lambda point: cast(int, point["available_at"]))
    assert _reference(early) == (17, "H", 50, 18)
    assert early["confirmation_rule"] == "level0_confirmed_high_breaks_known_level1_last_fall_high"
    assert (early["wave_direction_before"], early["wave_direction_after"]) == ("up", "up")
    assert (early["source_turn_position"], early["confirmed_on_turn"]) == (17, 17)
    upgrade = cast(Mapping[str, object], early["upgrade_evidence"])
    assert (upgrade["direction"], upgrade["role"], upgrade["available_at"]) == ("up", "endpoint", 18)
    assert _reference(cast(Mapping[str, object], upgrade["origin"])) == (14, "L", 24, 15)
    assert _reference(cast(Mapping[str, object], upgrade["broken_key"])) == (9, "H", 45, 13)
    assert _reference(cast(Mapping[str, object], upgrade["confirmed_by"])) == (17, "H", 50, 18)
    assert _reference(cast(Mapping[str, object], early["confirmed_by"])) == (17, "H", 50, 18)

    # H48 < H50 已经成立，L28 < L30 到达即构成空头；不再等未来 H46。
    # 未跌穿 L24，不会产生镜像升级来替代这份独立的普通方向证据。
    assert _reference(late) == (17, "H", 50, 21)
    assert late["confirmation_rule"] == "ordered_HH_then_HL_or_LL_then_LH_switch"
    assert (late["wave_direction_before"], late["wave_direction_after"]) == ("up", "down")
    assert late["direction_definition"] == StructuralTrend.BEAR.value
    assert (late["source_turn_position"], late["confirmed_on_turn"], late["source_reversal_available_at"]) == (
        17, 20, 18)
    assert _reference(cast(Mapping[str, object], late["broken_key"])) == (16, "L", 26, 17)
    expected_evidence = ((17, "H", 50, 18), (19, "H", 48, 20), (18, "L", 30, 19), (20, "L", 28, 21))
    assert tuple(_reference(point) for point in cast(Sequence[Mapping[str, object]], late["definition_evidence"])) == (
        expected_evidence)
    assert tuple(_reference(point) for point in cast(Sequence[Mapping[str, object]], late["confirmed_by"])) == (
        expected_evidence)
    assert late.get("upgrade_evidence") is None and early.get("definition_evidence") is None

    geometry = [point for point in judgement.points if point["index"] == 17]
    assert len(geometry) == 1 and _reference(geometry[0]) == (17, "H", 50, 18)
    assert geometry[0]["wave_direction_after"] == "up"
    before = judge_trend(source, trend_level=1, asof=20)
    assert [_reference(point) for point in before.candidates if point["index"] == 17] == [(17, "H", 50, 18)]
    assert all(point["wave_direction_after"] == "up" for point in before.candidates if point["index"] == 17)
    assert source == original


def test_decline_history_admits_the_later_negative_reversal_only_at_its_own_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = (*VALUES, 27)
    bars: list[Bar] = []
    for index, value in enumerate(values):
        low = value if index % 2 == 0 else value - 1
        high = 40 if index == 0 else value + 1 if index % 2 == 0 else value
        bars.append(Bar(START + timedelta(days=index), "sz.test", (high + low) / 2,
                        high, low, (high + low) / 2, 100))

    def drawing(prefix: Sequence[Bar], *, on_step: Callable[[int, int, list[dict[str, object]]], None]) -> None:
        for index, _ in enumerate(prefix):
            raw = [dict(_point(0, "H", 40, 0), state="seed")]
            for source_index in range(index):
                raw.append(dict(_point(source_index, "L" if source_index % 2 == 0 else "H",
                                       values[source_index], source_index + 1),
                                ordinal=1 if source_index == 0 else 0))
            raw.append(dict(_point(index, "L" if index % 2 == 0 else "H", values[index], index),
                            state="developing", ordinal=1 if index == 0 else 0))
            on_step(index, 0, raw)

    monkeypatch.setattr(hierarchical_entry, "lecture_drawing", drawing)
    declines: dict[int, tuple[DeclineStart, ...]] = {}
    structures: dict[int, dict[int, tuple[dict[str, object], ...]]] = {}
    hierarchical_entry.hierarchical_history(bars, decline_sink=declines, structure_sink=structures)
    assert all(not any(point.index == 17 for point in declines[now]) for now in range(21))
    assert DeclineStart(17, 21) in declines[21]
    assert DeclineStart(17, 21) in declines[22]
    assert DeclineStart(9, 13) in declines[22]
    assert _reference(next(point for point in structures[18][1] if point["index"] == 17)) == (17, "H", 50, 18)
    assert _reference(next(point for point in structures[22][1] if point["index"] == 17)) == (17, "H", 50, 18)
    prefix_declines: dict[int, tuple[DeclineStart, ...]] = {}
    hierarchical_entry.hierarchical_history(bars[:22], decline_sink=prefix_declines)
    assert prefix_declines == {now: declines[now] for now in range(22)}


def test_later_strict_down_origin_keeps_the_earliest_independent_decline_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = [*_source(), _point(22, "L", 23, 23)]
    judgement = judge_trend(source, trend_level=1, asof=23)
    down_facts = [point for point in judgement.candidates
                  if point["index"] == 17 and point["kind"] == "H" and point["wave_direction_after"] == "down"]
    assert len(down_facts) == 2
    ordinary, strict = sorted(down_facts, key=lambda point: cast(int, point["available_at"]))
    assert _reference(ordinary) == (17, "H", 50, 21)
    assert ordinary["confirmation_rule"] == "ordered_HH_then_HL_or_LL_then_LH_switch"
    assert (ordinary["source_turn_position"], ordinary["confirmed_on_turn"]) == (17, 20)
    ordinary_evidence = cast(Sequence[Mapping[str, object]], ordinary["definition_evidence"])
    assert tuple(_reference(point) for point in ordinary_evidence) == (
        (17, "H", 50, 18), (19, "H", 48, 20), (18, "L", 30, 19), (20, "L", 28, 21))
    assert _reference(strict) == (17, "H", 50, 23)
    assert strict["confirmation_rule"] == "level0_confirmed_low_breaks_known_level1_last_rise_low_origin"
    assert (strict["source_turn_position"], strict["confirmed_on_turn"]) == (17, 22)
    upgrade = cast(Mapping[str, object], strict["upgrade_evidence"])
    assert (upgrade["direction"], upgrade["role"], upgrade["available_at"]) == ("down", "origin", 23)
    assert _reference(cast(Mapping[str, object], upgrade["origin"])) == (17, "H", 50, 18)
    assert _reference(cast(Mapping[str, object], upgrade["broken_key"])) == (14, "L", 24, 18)
    assert _reference(cast(Mapping[str, object], upgrade["confirmed_by"])) == (22, "L", 23, 23)
    assert _reference(next(point for point in judgement.points if point["index"] == 17)) == (17, "H", 50, 18)

    values = (*VALUES, 23, 40)
    bars: list[Bar] = []
    for index, value in enumerate(values):
        low = value if index % 2 == 0 else value - 1
        high = 40 if index == 0 else value + 1 if index % 2 == 0 else value
        bars.append(Bar(START + timedelta(days=index), "sz.test", (high + low) / 2,
                        high, low, (high + low) / 2, 100))

    def drawing(prefix: Sequence[Bar], *, on_step: Callable[[int, int, list[dict[str, object]]], None]) -> None:
        for index, _ in enumerate(prefix):
            raw = [dict(_point(0, "H", 40, 0), state="seed")]
            for source_index in range(index):
                raw.append(dict(_point(source_index, "L" if source_index % 2 == 0 else "H",
                                       values[source_index], source_index + 1),
                                ordinal=1 if source_index == 0 else 0))
            raw.append(dict(_point(index, "L" if index % 2 == 0 else "H", values[index], index),
                            state="developing", ordinal=1 if index == 0 else 0))
            on_step(index, 0, raw)

    monkeypatch.setattr(hierarchical_entry, "lecture_drawing", drawing)
    declines: dict[int, tuple[DeclineStart, ...]] = {}
    hierarchical_entry.hierarchical_history(bars, decline_sink=declines)
    assert all(not any(point.index == 17 for point in declines[now]) for now in range(21))
    assert DeclineStart(17, 21) in declines[21]
    assert DeclineStart(17, 21) in declines[22]
    assert DeclineStart(17, 21) in declines[23]
    assert DeclineStart(17, 23) not in declines[23]
