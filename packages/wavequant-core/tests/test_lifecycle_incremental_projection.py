"""直接来源关键位只解析变化后缀，日期到期、修订和公开输入保持原语义。"""

from collections.abc import Mapping
from datetime import datetime, timedelta

import pytest

from wavequant.domain.market_structure import trend_lifecycle as lifecycle
from wavequant.domain.market_structure.source_bear_exit import (
    SourceMarketBreaks, _SourceKeyProjection, _prepare_source_market_key,
)
from wavequant.domain.market_structure.trend_point_version import PointVersion
from wavequant.domain.models.model import Bar


def _point(index: int, *, known: int | None = None, price: float | None = None) -> dict[str, object]:
    return dict(index=index, ordinal=0, kind='H' if index % 2 else 'L',
                value=(20. if index % 2 else 10.) if price is None else price,
                available_at=index if known is None else known, state='confirmed')


def test_appending_source_point_does_not_parse_the_retained_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    points = PointVersion(_point(index) for index in range(512)).seal()
    projection = _SourceKeyProjection()
    dates: dict[str, int] = {}
    assert projection.advance(points, dates, 600, restart=True) == _prepare_source_market_key(points, dates, 600)
    appended = PointVersion([_point(512)], previous=points, start=len(points)).seal()
    expected = _prepare_source_market_key(appended, dates, 601)
    old_ids = {id(point) for point in points}
    original = lifecycle._pivot
    visited = 0

    def counted(value: object, clock_dates: Mapping[str, int], now: int) -> lifecycle._Pivot | None:
        nonlocal visited
        assert id(value) not in old_ids, 'unchanged source points were parsed again'
        visited += 1
        return original(value, clock_dates, now)

    monkeypatch.setattr(lifecycle, '_pivot', counted)
    assert projection.advance(appended, dates, 601, restart=False) == expected
    # 新根点一次、最近高点与前驱的原完成证明各一次，与历史长度无关。
    assert visited == 3


def test_source_projection_revision_truncation_order_and_future_rebuild() -> None:
    dates: dict[str, int] = {}
    projection = _SourceKeyProjection()
    first = PointVersion(_point(index) for index in range(8)).seal()
    future = PointVersion([_point(7, known=20)], previous=first, start=7).seal()
    shortened = PointVersion(previous=future, start=3).seal()
    unordered = PointVersion([_point(1)], previous=shortened, start=3).seal()
    repaired = PointVersion([_point(3)], previous=unordered, start=3).seal()
    for version, now, restart in ((first, 10, True), (future, 11, False), (future, 20, True),
                                 (shortened, 21, False), (unordered, 22, False), (repaired, 23, False)):
        assert projection.advance(version, dates, now, restart=restart) == _prepare_source_market_key(version, dates, now)
        assert len(projection.rows) == len(version)


def _row(level: int, points: object) -> dict[str, object]:
    return dict(trend_level=level, strokes=[dict(id='owned', source_path='source')],
                source_strokes=[dict(id='source', points=points)], _path_market_end_indices={})


@pytest.mark.parametrize('level', (1, 2, 3))
def test_source_breaks_incremental_versions_match_public_daily_input(level: int) -> None:
    bars = tuple(Bar(datetime(2026, 1, 1) + timedelta(days=index), 'TEST', 15., 20.,
                     9. if index == 9 else 12., 15., 100.) for index in range(12))
    dates = {bar.timestamp.date().isoformat(): index for index, bar in enumerate(bars)}
    cached, public = SourceMarketBreaks(), SourceMarketBreaks()
    version = PointVersion().seal()
    for now in range(len(bars)):
        if now < 8:
            version = PointVersion([_point(now, known=now + 1)], previous=version, start=len(version)).seal()
        actual = cached.observe([_row(level, version)], bars, now, dates, owned=True)
        expected = public.observe([_row(level, list(version))], bars, now, dates)
        assert actual == expected
    assert cached.observe([], bars, 11, dates, owned=True) == []
    assert not cached._key_states and not cached._clocks and not cached._prepared


def test_source_clock_checks_only_the_appended_point(monkeypatch: pytest.MonkeyPatch) -> None:
    bars = tuple(Bar(datetime(2026, 1, 1) + timedelta(days=index), 'TEST', 15., 20., 12., 15., 100.)
                 for index in range(70))
    dates: dict[str, int] = {}
    first = PointVersion(_point(index) for index in range(64)).seal()
    state = SourceMarketBreaks()
    state.observe([_row(2, first)], bars, 64, dates, owned=True)
    appended = PointVersion([_point(64)], previous=first, start=len(first)).seal()
    original = lifecycle._next_structure_clock
    scanned: list[object] = []

    def counted(value: object, clock_dates: Mapping[str, int], now: int) -> tuple[bool, int | None]:
        scanned.append(value)
        return original(value, clock_dates, now)

    monkeypatch.setattr(lifecycle, '_next_structure_clock', counted)
    state.observe([_row(2, appended)], bars, 65, dates, owned=True)
    assert len(scanned) == 1 and scanned[0] is appended[-1]


def test_public_mutation_does_not_reuse_a_source_prefix() -> None:
    bars = tuple(Bar(datetime(2026, 1, 1) + timedelta(days=index), 'TEST', 15., 20., 9., 15., 100.)
                 for index in range(5))
    points = [_point(0), _point(1)]
    state = SourceMarketBreaks()
    dates: dict[str, int] = {}
    state.observe([_row(1, points)], bars, 2, dates)
    points[0]['value'] = 8.
    expected = SourceMarketBreaks().observe([_row(1, points)], bars, 3, dates)
    assert state.observe([_row(1, points)], bars, 3, dates) == expected
    assert not state._key_states and not state._clocks


def _observed(index: int, *, known: int | None = None) -> dict[str, object]:
    point = _point(index, known=known)
    point['observations'] = [dict(title='头部疑虑' if index % 2 else '底部疑虑',
                                  available_at=index if known is None else known)]
    return point


def test_structure_projection_parses_facts_only_for_changed_tail(monkeypatch: pytest.MonkeyPatch) -> None:
    first = PointVersion(_observed(index) for index in range(512)).seal()
    dates: dict[str, int] = {}
    projection = lifecycle._StructureProjection()
    old = projection.advance(first, dates, 600, restart=True)
    assert old is not None and old[0] == lifecycle._facts(first, dates, 600)
    appended = PointVersion([_observed(512)], previous=first, start=len(first)).seal()
    expected = lifecycle._facts(appended, dates, 601)
    original = lifecycle._point_facts
    visited: list[int] = []

    def counted(raw: Mapping[str, object], point: lifecycle._Pivot,
                own: Mapping[tuple[int, int], lifecycle._Pivot], clock_dates: Mapping[str, int],
                now: int) -> tuple[lifecycle._Fact, ...]:
        visited.append(point.index)
        return original(raw, point, own, clock_dates, now)

    monkeypatch.setattr(lifecycle, '_point_facts', counted)
    actual = projection.advance(appended, dates, 601, restart=False)
    assert actual is not None and actual[0] == expected
    assert visited == [512]
    assert len(old[0]) == 512 and old[0][-1].point.index == 511


def test_structure_projection_revisions_restore_the_right_fact_prefix() -> None:
    first = PointVersion(_observed(index) for index in range(8)).seal()
    revised = PointVersion([_observed(6, known=20), _observed(7)], previous=first, start=6).seal()
    shortened = PointVersion(previous=revised, start=4).seal()
    duplicate = PointVersion([_observed(3)], previous=shortened, start=4).seal()
    repaired = PointVersion([_observed(4)], previous=duplicate, start=4).seal()
    projection = lifecycle._StructureProjection()
    dates: dict[str, int] = {}
    for points, now, restart in ((first, 10, True), (revised, 11, False), (revised, 20, True),
                                 (shortened, 21, False), (duplicate, 22, False), (repaired, 23, False)):
        projected = projection.advance(points, dates, now, restart=restart)
        if points is duplicate:
            assert projected is None
            assert projection.points is None and not projection.own
        else:
            assert projected is not None
            assert projected[0] == lifecycle._facts(points, dates, now)
            assert tuple(point for _, point in projected[1]) == tuple(
                point for raw in points if (point := lifecycle._pivot(raw, dates, now)) is not None)


@pytest.mark.parametrize('level', (1, 2, 3))
def test_lifecycle_owned_projection_matches_public_with_late_knowledge_and_revision(level: int) -> None:
    bars = tuple(Bar(datetime(2026, 1, 1) + timedelta(days=index), 'TEST', 15., 20., 12., 15., 100.)
                 for index in range(12))
    dates = {bar.timestamp.date().isoformat(): index for index, bar in enumerate(bars)}
    cached, public = lifecycle.TrendLifecycle(), lifecycle.TrendLifecycle()
    points = PointVersion().seal()
    for now in range(len(bars)):
        if now < 8:
            points = PointVersion([_observed(now, known=now + 1)], previous=points, start=len(points)).seal()
        if now == 9:
            points = PointVersion([_point(6), _observed(7)], previous=points, start=6).seal()
        owned_row = dict(trend_level=level, strokes=[dict(id='path', source_path='source', points=points)])
        public_row = dict(trend_level=level, strokes=[dict(id='path', source_path='source', points=list(points))])
        assert cached.advance([owned_row], bars, now, dates, _owned_structure=True) == public.advance(
            [public_row], bars, now, dates)
        assert cached.checkpoint() == public.checkpoint()
