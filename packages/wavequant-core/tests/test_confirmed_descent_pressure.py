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

from .test_trend_confirmation_entry_refresh import bars_fixture, empty_level, fake_drawing, fake_prepared


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


def _pressure_permissions(prepared: Mapping[str, object], *, public: bool) -> dict[str, object]:
    result = _level(_pressure(), public=public) if prepared['trend_level'] == 2 else empty_level()
    return dict(result, qualified_strokes=result['strokes'])


def _private_second(prepared: Mapping[str, object], bars: Sequence[Bar], *,
                    qualified_source_points: Sequence[Mapping[str, object]] | None = None,
                    n_target_trend_confirmation_enabled: bool = False) -> dict[str, object]:
    return _pressure_permissions(prepared, public=False)


def _public_second(prepared: Mapping[str, object], bars: Sequence[Bar], *,
                   qualified_source_points: Sequence[Mapping[str, object]] | None = None,
                   n_target_trend_confirmation_enabled: bool = False) -> dict[str, object]:
    return _pressure_permissions(prepared, public=True)


def test_chart_uses_private_pressure_without_publishing_an_entry_context_and_invalidates_old_cache() -> None:
    bars = bars_fixture()
    old_cache: dict[str, object] = dict(
        key=(tuple(bars[:-1]), frozenset(), False, False, False, "daily_trend_confirmation_v1"),
        checkpoint="obsolete",
    )
    with patch("wavequant.domain.strategies.chart_entry_history.lecture_drawing", side_effect=fake_drawing), \
            patch("wavequant.domain.strategies.chart_entry_history.prepare_trend_lines", side_effect=fake_prepared), \
            patch("wavequant.domain.strategies.chart_entry_history.attach_trend_permissions", side_effect=_private_second):
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
            patch("wavequant.domain.strategies.chart_entry_history.prepare_trend_lines", side_effect=fake_prepared), \
            patch("wavequant.domain.strategies.chart_entry_history.attach_trend_permissions", side_effect=_public_second):
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


def _lexin_reference(dates: Mapping[str, int], day: str, kind: str, value: float,
                     known: str, label: str, *, ordinal: int = 0) -> dict[str, object]:
    return dict(index=dates[day], ordinal=ordinal, kind=kind, value=value,
                time=day, available_at=known, label=label)


def _assert_lexin_source(reference: Mapping[str, object], first: Mapping[str, object]) -> None:
    matches = [point for point in _points(first, 'strokes')
               if (point['index'], point['ordinal'], point['kind'])
               == (reference['index'], reference['ordinal'], reference['kind'])]
    assert len(matches) == 1
    assert {field: matches[0][field] for field in reference} == reference


@pytest.mark.parametrize("asof", ["2026-03-23", "2026-03-24", "2026-04-16",
                                 "2026-04-17", "2026-05-20", "2026-07-02"])
def test_lexin_strict_and_ordered_pressure_use_their_own_lower_level_dates(
    lexin: tuple[Bar, ...], asof: str,
) -> None:
    bars = [bar for bar in lexin if bar.timestamp.date().isoformat() <= asof]
    first = reversal_trends(lecture_drawing(bars), bars)
    second = secondary_trends(first, bars)
    dates = _dates(bars)
    pressures = _confirmed_descent_pressures(second, dates, len(bars) - 1)
    january = [point for point in pressures if point["index"] == dates["2026-01-14"]]

    up_key = _lexin_reference(dates, '2025-11-27', 'H', 16.238397005037697, '2025-11-28', 'H233')
    up_origin = _lexin_reference(dates, '2025-12-16', 'L', 13.962884793147548, '2025-12-17', 'L233')
    up_turn = _lexin_reference(dates, '2026-01-14', 'H', 18.642107088020254, '2026-01-15', 'H234')
    up_rule = 'level1_confirmed_high_breaks_known_level2_last_fall_high'
    up_evidence = dict(direction='up', role='endpoint', available_at='2026-01-15',
                       confirmation_rule=up_rule, origin=up_origin, broken_key=up_key, confirmed_by=up_turn)
    for field in ('strokes', 'qualified_strokes'):
        points = [point for point in _points(second, field) if point['time'] == '2026-01-14']
        assert len(points) == 1
        point = points[0]
        assert (point['index'], point['ordinal'], point['kind'], point['value'], point['available_at'],
                point['trend_level'], point['source_level']) == (1946, 0, 'H', 18.642107088020254, '2026-01-15', 2, 1)
        assert point['wave_direction_after'] == 'up'
        assert point['confirmation_rule'] == up_rule
        assert point['broken_key'] == up_key
        assert point['confirmed_by'] == up_turn
        assert point['upgrade_evidence'] == up_evidence
    for reference in (up_key, up_origin, up_turn):
        _assert_lexin_source(reference, first)

    # 一月的上涨许可不能让后来下降提前可知。三月末来源低点仍发展时，
    # 两条下降证据都不存在；其后相同事实在四月快照中也不倒填到价格日。
    assert _confirmed_descent_pressures(second, dates, dates['2026-03-23']) == [
        point for point in pressures if cast(int, point['known_index']) <= dates['2026-03-23']]
    assert not any(point['index'] == 1946
                   for point in _confirmed_descent_pressures(second, dates, dates['2026-03-23']))
    if asof == '2026-03-23':
        assert january == []
        assert not any(point['time'] == '2026-03-23' for point in _points(first, 'strokes'))
        assert not any(point['time'] == '2026-03-23' for point in _points(second, 'qualified_strokes'))
        return

    down_turn = _lexin_reference(dates, '2026-03-23', 'L', 12.809103953315923, '2026-03-24', 'L236')
    down_rule = 'level1_confirmed_low_breaks_known_level2_last_rise_low'
    ordered_turn = _lexin_reference(dates, '2026-03-11', 'H', 15.212814036298477, '2026-03-24', 'H236')
    ordered_rule = 'ordered_HH_then_HL_or_LL_then_LH_switch'
    assert january == [
        dict(index=1946, value=18.642107088020254, source='confirmed_descent', available_at='2026-03-24',
             known_index=1989, confirmation_rule=f'{down_rule}_origin', broken_key=up_origin, confirmed_by=down_turn),
        dict(index=1946, value=18.642107088020254, source='confirmed_descent', available_at='2026-03-24',
             known_index=1989, confirmation_rule=ordered_rule, broken_key=up_origin, confirmed_by=ordered_turn),
    ]
    assert cast(float, down_turn['value']) < cast(float, up_origin['value'])
    down_evidence = dict(direction='down', role='endpoint', available_at='2026-03-24',
                         confirmation_rule=down_rule, origin=up_turn, broken_key=up_origin, confirmed_by=down_turn)
    for field in ('strokes', 'qualified_strokes'):
        points = [point for point in _points(second, field) if point['time'] == '2026-03-23']
        assert len(points) == 1
        assert (points[0]['value'], points[0]['available_at']) == (12.809103953315923, '2026-03-24')
        assert points[0]['upgrade_evidence'] == down_evidence
    candidates = [point for point in _points(second, 'structure_strokes')
                  if point['time'] == '2026-01-14' and point['wave_direction_after'] == 'down']
    assert len(candidates) == 2
    strict = next(point for point in candidates if point['confirmation_rule'] == f'{down_rule}_origin')
    assert strict['upgrade_evidence'] == dict(down_evidence, role='origin')
    ordinary = next(point for point in candidates if point['confirmation_rule'] == ordered_rule)
    evidence = [
        _lexin_reference(dates, '2026-02-11', 'H', 16.05678335432346, '2026-03-02', 'H235'), ordered_turn,
        _lexin_reference(dates, '2026-02-02', 'L', 15.362378219239613, '2026-02-09', 'L234', ordinal=1),
        _lexin_reference(dates, '2026-03-04', 'L', 14.347478406424756, '2026-03-05', 'L235'),
    ]
    assert ordinary['definition_evidence'] == evidence
    assert ordinary['direction_definition'] == '空头趋势'
    for reference in [down_turn, *evidence]:
        _assert_lexin_source(reference, first)

    if asof == '2026-05-20':
        assert any(point['time'] == '2026-04-30' and point['available_at'] == '2026-05-06'
                   for point in _points(first, 'strokes'))
        assert not any(point['time'] == '2026-04-30' for point in _points(second, 'strokes'))
        assert not any(point['time'] == '2026-04-30' for point in _points(second, 'qualified_strokes'))
    if asof == "2026-07-02":
        april_evidence = [
            _lexin_reference(dates, '2026-04-30', 'H', 15.768338144365554, '2026-05-06', 'H238'),
            _lexin_reference(dates, '2026-05-19', 'H', 15.008843481531517, '2026-05-21', 'H239', ordinal=1),
            _lexin_reference(dates, '2026-04-24', 'L', 14.667973084155763, '2026-05-06', 'L237'),
            _lexin_reference(dates, '2026-05-19', 'L', 14.58833709088516, '2026-05-19', 'L238'),
        ]
        for field in ('strokes', 'qualified_strokes'):
            points = [point for point in _points(second, field) if point['time'] == '2026-04-30']
            assert len(points) == 1
            assert (points[0]['value'], points[0]['available_at']) == (15.768338144365554, '2026-05-21')
            assert points[0]['confirmation_rule'] == ordered_rule
            assert points[0]['confirmed_by'] == april_evidence[1]
            assert points[0]['definition_evidence'] == april_evidence
        for reference in april_evidence:
            _assert_lexin_source(reference, first)
        latest_key = _lexin_reference(dates, '2026-06-08', 'L', 12.475022922508597, '2026-06-09', 'L243')
        latest_turn = _lexin_reference(dates, '2026-06-12', 'L', 12.205467543889137, '2026-06-12', 'L244')
        assert max(pressures, key=lambda point: cast(int, point['index'])) == dict(
            index=2040, value=13.04648032518185, source='confirmed_descent', available_at='2026-06-12',
            known_index=2043, confirmation_rule=f'{down_rule}_origin', broken_key=latest_key, confirmed_by=latest_turn)
        assert bars[2040].timestamp.date().isoformat() == '2026-06-09'
        for reference in (latest_key, latest_turn):
            _assert_lexin_source(reference, first)
        assert cast(float, january[0]['value']) > bars[-1].high
