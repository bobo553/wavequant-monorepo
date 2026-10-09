"""Exercise N qualification through the real public hierarchy and publication gates."""

from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import datetime, timedelta
from typing import cast

import pytest

from wavequant.domain.market_structure.lecture_trend import reversal_trends
from wavequant.domain.market_structure.n_trend_confirmation import N_TARGET_CONFIRMATION
from wavequant.domain.market_structure.n_trend_reversals import n_target_reversals
from wavequant.domain.market_structure.secondary_trend import secondary_trends
from wavequant.domain.market_structure.tertiary_trend import tertiary_trends
from wavequant.domain.market_structure.trend_confirmation import SAME_LEVEL_KEY_BREAK, qualify_uptrend
from wavequant.domain.market_structure.trend_publication import publish_uptrends
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.chart_entry_history import _has_confirmation_price_cross, chart_entry_history


START = datetime(2024, 1, 1)


@pytest.mark.parametrize('level', (1, 2, 3))
@pytest.mark.parametrize('up', (False, True))
def test_frozen_n_target_refreshes_without_crossing_a_source_vertex(level: int, up: bool) -> None:
    bars, source = _sample(up=up)
    before = _public(level, bars[:10], source, up=up)
    levels: tuple[tuple[Mapping[str, object], Mapping[str, object]], ...] = ((before, {}),)
    assert before['n_target_observations']
    assert _has_confirmation_price_cross([], levels, bars[9], bars[10], '2024-01-11', 10)
    equal = replace(bars[10], high=bars[9].high) if up else replace(bars[10], low=bars[9].low)
    assert not _has_confirmation_price_cross([], levels, bars[9], equal, '2024-01-11', 10)


def _sample(*, up: bool = True) -> tuple[list[Bar], list[dict[str, object]]]:
    prices = [(20, 19, 19.5), (6, 5, 5.5), (6.3, 5.5, 6), (7, 6.4, 6.7),
              (6.8, 6.2, 6.5), (6.5, 6, 6.2), (7, 6.2, 6.6), (7.5, 6.6, 7.2),
              (9.8, 7, 9.5), (10, 9, 9.8), (10.1, 9, 9.9)]
    bars = [Bar(START + timedelta(days=index), "sz.test", (high + low) / 2, high, low, close, 100)
            for index, (high, low, close) in enumerate(prices)]
    source = [_reference(bars, index, kind, known)
              for index, kind, known in ((0, "H", 0), (1, "L", 2), (3, "H", 4), (5, "L", 6))]
    if up:
        return bars, source
    mirrored = [Bar(bar.timestamp, bar.symbol, 30 - bar.open, 30 - bar.low, 30 - bar.high,
                    30 - bar.close, bar.volume) for bar in bars]
    downward = [dict(point, kind="L" if point["kind"] == "H" else "H", value=30 - cast(float, point["value"]))
                for point in source]
    return mirrored, downward


def _reference(bars: Sequence[Bar], index: int, kind: str, known: int) -> dict[str, object]:
    return dict(index=index, ordinal=0, time=bars[index].timestamp.date().isoformat(), kind=kind,
                value=bars[index].high if kind == "H" else bars[index].low,
                available_at=bars[known].timestamp.date().isoformat(), state="confirmed", label=f"{kind}{index}")


def _records(value: object) -> list[Mapping[str, object]]:
    assert isinstance(value, (list, tuple))
    assert all(isinstance(item, dict) for item in value)
    return cast(list[Mapping[str, object]], list(value))


def _points(level: Mapping[str, object], field: str = "strokes") -> list[Mapping[str, object]]:
    return [point for stroke in _records(level.get(field, [])) for point in _records(stroke["points"])]


def _position_field(source_level: int) -> str:
    return "source_turn_position" if source_level == 0 else f"source_level{source_level}_position"


def _public(
    level: int, bars: Sequence[Bar], source: Sequence[Mapping[str, object]], *, up: bool,
    next_source: Sequence[Mapping[str, object]] = (),
) -> Mapping[str, object]:
    first_points = [dict(point) for point in source]
    first = dict(id="source-first", points=first_points)
    paths = [first]
    if next_source:
        paths.append(dict(id="source-next", points=[dict(point) for point in next_source]))
    if level == 1:
        if not next_source:
            end = len(bars) - 1
            tail = dict(_reference(bars, end, "H" if up else "L", end), state="developing")
            first_points.append(tail)
        return cast(Mapping[str, object], reversal_trends(dict(strokes=paths), bars))
    predecessor = dict(strokes=paths)
    if level == 2:
        return cast(Mapping[str, object], secondary_trends(predecessor, bars))
    return cast(Mapping[str, object], tertiary_trends(predecessor, bars))


@pytest.mark.parametrize("level", (1, 2, 3))
@pytest.mark.parametrize("up", (True, False))
def test_each_public_hierarchy_independently_publishes_the_strict_n_target_route(level: int, up: bool) -> None:
    bars, source = _sample(up=up)
    level_view = _public(level, bars, source, up=up)
    origin = next(point for point in _points(level_view) if point["index"] == 1)
    proof = origin["trend_confirmation"]
    assert isinstance(proof, dict)
    assert proof["confirmation_rule"] == origin["confirmation_rule"] == N_TARGET_CONFIRMATION
    assert proof["direction"] == ("up" if up else "down")
    assert proof["available_at"] == origin["available_at"] == "2024-01-11"
    assert proof["one_p_target"] == (10 if up else 20)
    assert origin[_position_field(level - 1)] == 1
    assert level_view["trend_level"] == level
    if level > 1:
        assert level_view["source_level"] == level - 1
    tail = _records(level_view["developing_strokes"])[0]
    assert tail["source_level"] == level - 1
    assert tail["trend_level"] == level
    assert tail["wave_direction"] == proof["direction"]


@pytest.mark.parametrize("level", (1, 2, 3))
@pytest.mark.parametrize("up", (True, False))
def test_equal_one_p_is_not_an_upgrade_in_any_public_hierarchy(level: int, up: bool) -> None:
    bars, source = _sample(up=up)
    level_view = _public(level, bars[:10], source, up=up)
    assert bars[9].high == 10 if up else bars[9].low == 20
    assert not any(point["index"] == 1 for point in _points(level_view))
    assert not _records(level_view["developing_strokes"])


@pytest.mark.parametrize("level", (1, 2, 3))
@pytest.mark.parametrize("up", (True, False))
def test_n_target_needs_no_earlier_source_key_in_any_public_hierarchy(level: int, up: bool) -> None:
    bars, source = _sample(up=up)
    if level == 1:
        source[0]["state"] = "seed"
    else:
        source = source[1:]
    level_view = _public(level, bars, source, up=up)
    origin = next(point for point in _points(level_view) if point["index"] == 1)
    proof = origin["trend_confirmation"]
    assert isinstance(proof, dict)
    assert proof["confirmation_rule"] == N_TARGET_CONFIRMATION
    assert origin[_position_field(level - 1)] == (1 if level == 1 else 0)


@pytest.mark.parametrize("level", (1, 2, 3))
@pytest.mark.parametrize("up", (True, False))
def test_a_source_path_cannot_borrow_the_next_paths_target_hit(level: int, up: bool) -> None:
    bars, source = _sample(up=up)
    later = [_reference(bars, index, kind, index) for index, kind in
             ((9, "H" if up else "L"), (10, "L" if up else "H"))]
    # The former path ends before index 9; its N has only reached 9.8 / 20.2.
    level_view = _public(level, bars, source, up=up, next_source=later)
    assert not any(point["index"] == 1 for point in _points(level_view))
    assert not any(point["index"] == 1 for point in _points(level_view, "developing_strokes"))


@pytest.mark.parametrize("source_level", (0, 1, 2))
def test_old_own_key_certificate_wins_over_a_later_n_target(source_level: int) -> None:
    bars, source = _sample()
    bars[0] = replace(bars[0], open=8.75, high=9, low=8.5, close=8.8)
    source[0] = _reference(bars, 0, "H", 0)
    field = _position_field(source_level)
    candidates = [dict(source[0], confirmation_rule="existing_structural_pressure", **{field: 0}),
                  dict(source[1], available_at="2024-01-07", confirmation_rule="existing_structural_low", **{field: 1})]
    merged = n_target_reversals(candidates, source, bars, source_level=source_level)
    public = publish_uptrends(merged, source, bars, source_level=source_level)
    origin = next(point for point in public if point["index"] == 1)
    proof = origin["trend_confirmation"]
    assert isinstance(proof, dict)
    assert proof["confirmation_rule"] == SAME_LEVEL_KEY_BREAK
    assert proof["available_at"] == origin["available_at"] == "2024-01-09"
    assert origin[field] == 1


@pytest.mark.parametrize("source_level", (0, 1, 2))
def test_later_n_at_the_held_floor_cannot_replace_an_earlier_local_certificate(source_level: int) -> None:
    bars, source = _sample()
    field = _position_field(source_level)
    candidates = [dict(source[position], confirmation_rule="existing_structural_turn", **{field: position})
                  for position in (0, 1, 2, 3)]
    candidates[1]["available_at"] = "2024-01-07"
    candidates[3]["available_at"] = "2024-01-08"
    earlier_candidates = n_target_reversals(candidates, source, bars[:8], source_level=source_level)
    later_candidates = n_target_reversals(candidates, source, bars, source_level=source_level)
    earlier = publish_uptrends(earlier_candidates, source, bars[:8], source_level=source_level)
    later = publish_uptrends(later_candidates, source, bars, source_level=source_level)
    early_low = next(point for point in earlier if point["kind"] == "L")
    late_low = next(point for point in later if point["kind"] == "L")
    assert early_low["index"] == late_low["index"] == 1
    early_proof = early_low["trend_confirmation"]
    assert isinstance(early_proof, dict)
    assert early_proof["confirmation_rule"] == SAME_LEVEL_KEY_BREAK
    assert early_proof["origin"]["index"] == 5
    assert early_proof["wave_origin"]["index"] == 1
    assert early_proof["available_at"] == "2024-01-08"
    assert late_low["trend_confirmation"] == early_proof
    assert late_low["available_at"] == early_low["available_at"] == "2024-01-08"


@pytest.mark.parametrize("source_level", (0, 1, 2))
def test_unknown_local_candidate_does_not_backdate_an_older_market_certificate(source_level: int) -> None:
    bars, source = _sample()
    bars.append(Bar(START + timedelta(days=11), "sz.test", 8, 11, 6.1, 8, 100))
    field = _position_field(source_level)
    candidates = [dict(source[position], confirmation_rule="existing_structural_turn", **{field: position})
                  for position in (0, 1, 2, 3)]
    candidates[1]["available_at"] = "2024-01-07"
    candidates[3]["available_at"] = "2024-01-12"
    merged = n_target_reversals(candidates, source, bars, source_level=source_level)
    public = publish_uptrends(merged, source, bars, source_level=source_level)
    origin = next(point for point in public if point["kind"] == "L")
    proof = origin["trend_confirmation"]
    assert isinstance(proof, dict)
    # The local key crossed on January 8, but its structural candidate only
    # becomes known after the original floor's January 11 N certificate.
    assert origin["available_at"] == proof["available_at"] == "2024-01-11"
    assert proof["confirmation_rule"] == N_TARGET_CONFIRMATION
    assert proof["origin"]["index"] == 1


@pytest.mark.parametrize("source_level", (0, 1, 2))
@pytest.mark.parametrize("up", (True, False))
@pytest.mark.parametrize("peak_known", (11, 12))
def test_later_lower_or_higher_n_does_not_rewrite_an_earlier_qualified_origin(
    source_level: int, up: bool, peak_known: int,
) -> None:
    bars, source = _sample()
    rows = [(6, 4.5, 5), (6.2, 5, 6), (6.5, 6, 6.3), (6.2, 5.8, 6),
            (6, 5.5, 5.8), (6.5, 5.6, 6.3), (7, 6, 6.8), (9.6, 7, 9.5)]
    bars.extend(Bar(START + timedelta(days=index), "sz.test", (high + low) / 2, high, low, close, 100)
                for index, (high, low, close) in enumerate(rows, 11))
    source.extend(_reference(bars, index, kind, known) for index, kind, known in
                  ((10, "H", peak_known), (11, "L", 12), (13, "H", 14), (15, "L", 16)))
    if not up:
        bars = [Bar(bar.timestamp, bar.symbol, 30 - bar.open, 30 - bar.low, 30 - bar.high,
                    30 - bar.close, bar.volume) for bar in bars]
        source = [dict(point, kind="L" if point["kind"] == "H" else "H", value=30 - cast(float, point["value"]))
                  for point in source]
    earlier = n_target_reversals([], source, bars[:11], source_level=source_level)
    later = n_target_reversals([], source, bars, source_level=source_level)
    prefix = [point for point in later if cast(str, point["available_at"]) <= "2024-01-11"]
    assert prefix == earlier
    assert earlier[0]["index"] == 1
    assert any(point["index"] == 11 for point in later)
    endpoint = next(point for point in later if point["index"] == 10)
    assert endpoint["available_at"] == bars[peak_known].timestamp.date().isoformat()
    assert endpoint[_position_field(source_level)] == 4


@pytest.mark.parametrize("level", (1, 2, 3))
def test_existing_structural_low_cannot_qualify_from_market_after_its_source_path(level: int) -> None:
    prices = [(20, 19, 19.5), (16, 15, 15.5), (18, 16, 17), (15, 14, 14.5), (16, 14.5, 15.5),
              (6, 5, 5.5), (6.3, 5.5, 6), (7, 6.4, 6.7), (6.8, 6.2, 6.5), (6.5, 6, 6.2),
              (7, 6.2, 6.6), (7.5, 6.6, 7.2), (7.2, 6.7, 6.9), (7, 6.5, 6.7), (7.2, 6.7, 7),
              (7.3, 7, 7.1), (7.1, 6.9, 7), (7, 6.8, 6.9), (9, 8.5, 8.8), (9.8, 8.8, 9.5), (10.1, 9, 9.9)]
    bars = [Bar(START + timedelta(days=index), "sz.test", (high + low) / 2, high, low, close, 100)
            for index, (high, low, close) in enumerate(prices)]
    source = [_reference(bars, index, kind, known) for index, kind, known in
              ((0, "H", 1), (1, "L", 2), (2, "H", 3), (3, "L", 4), (4, "H", 5), (5, "L", 6),
               (7, "H", 8), (9, "L", 10), (11, "H", 12), (13, "L", 14), (15, "H", 16), (17, "L", 18))]
    later = [_reference(bars, index, kind, index) for index, kind in ((18, "H"), (19, "L"), (20, "H"))]
    level_view = _public(level, bars, source, up=True, next_source=later)
    # The first source can supply a structural origin, but its first one-P
    # crossing occurs in another source path and supplies no upward permission.
    assert not any(point["index"] == 5 for point in _points(level_view))
    for tail in _records(level_view["developing_strokes"]):
        proof = tail["confirmation"]
        assert isinstance(proof, dict)
        origin = proof["origin"]
        assert isinstance(origin, dict)
        assert not (proof["direction"] == "up" and origin["index"] == 5)


@pytest.mark.parametrize("up", (True, False))
def test_defense_failure_after_certification_does_not_fix_the_wave_endpoint(up: bool) -> None:
    bars, source = _sample(up=up)
    last = Bar(START + timedelta(days=11), "sz.test", 6.5, 7, 5.9, 6, 100)
    if not up:
        last = Bar(last.timestamp, last.symbol, 30 - last.open, 30 - last.low, 30 - last.high,
                   30 - last.close, last.volume)
    bars.append(last)
    source.append(_reference(bars, 10, "H" if up else "L", 11))
    result = n_target_reversals([], source, bars, source_level=1)
    assert any(point["index"] == 1 for point in result)
    assert not any(point["index"] == 10 for point in result)


@pytest.mark.parametrize("level", (1, 2, 3))
def test_inverse_n_high_never_grants_a_bullish_landmark(level: int) -> None:
    bars, source = _sample(up=False)
    level_view = _public(level, bars, source, up=False)
    origin = next(point for point in _points(level_view) if point["index"] == 1)
    proof = origin["trend_confirmation"]
    assert isinstance(proof, dict) and proof["direction"] == "down"
    assert not level_view["bear_to_bull_highs"]
    assert not level_view["bear_bull_alternation_lows"]
    assert not level_view["bullish_turn_signals"]
    assert qualify_uptrend(source, source[1], 1, None, bars, len(bars) - 1) is None


def test_cached_chart_replay_keeps_inverse_n_from_granting_upward_entry_permission() -> None:
    bars, source = _sample(up=False)
    cache: dict[str, object] = {}
    chart_entry_history(bars[:10], prefix_cache=cache)
    cached = chart_entry_history(bars, prefix_cache=cache)
    assert cached == chart_entry_history(bars)
    history, events = cached
    assert history[10] == ()
    assert not events
    # Both bars are beyond every prior source low. The frozen N target still
    # needs a new replay when its market extreme extends across one P.
    assert _has_confirmation_price_cross(source, (), bars[9], bars[10], "2024-01-11", 10)
