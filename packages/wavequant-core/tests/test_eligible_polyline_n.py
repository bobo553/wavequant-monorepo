"""Only the selected confirmed window exposes polyline N geometry."""

from dataclasses import replace

import pytest

from wavequant.domain.market_structure.polyline import LinePoint, PointKind, ReversalPoint
from wavequant.domain.strategies.eligible_polyline_n import EligiblePolylineN


def point(index, kind, price, *, ordinal=0):
    return ReversalPoint(LinePoint(index, ordinal, PointKind(kind), price), index, 'polyline')


def opening(now, low, *, ordinal=0, level=2, path='p'):
    return dict(event='trend_lifecycle_state', bar_index=now, trend_level=level, source_path=path,
        state='空多交替', flip_confirmed=True, flip_index=0, flip_known_index=1, counter_index=low,
        counter_ordinal=ordinal, counter_price=10.0, alternation_index=now)


def test_window_low_excludes_earlier_vertices_and_cannot_backdate_knowledge():
    stream = EligiblePolylineN(2)
    points = tuple(point(i, 'L' if i % 2 == 0 else 'H', 10 + i) for i in range(7))
    for now in range(6):
        assert stream.advance(now, points[:now + 1], ()) == ()
    sources = stream.advance(6, points, (opening(6, 4),))
    assert len(sources) == 1
    assert sources[0].points == points[4:7]
    assert sources[0].known_at == 6
    assert sources[0].low_index == 4
    assert stream.advance(7, points, ()) == ()


def test_same_day_low_ordinal_and_selected_level_are_respected():
    stream = EligiblePolylineN(2)
    points = (point(1, 'H', 16, ordinal=0), point(1, 'L', 10, ordinal=1),
              point(2, 'H', 14), point(3, 'L', 12))
    for now in range(4):
        assert stream.advance(now, (), (opening(3, 1, ordinal=1, level=1),) if now == 3 else ()) == ()
    sources = stream.advance(4, points, (opening(4, 1, ordinal=1),))
    assert [source.points for source in sources] == [points[1:]]


def test_revisions_are_new_knowledge_and_closed_paths_stop_producing_sources():
    stream = EligiblePolylineN(2)
    points = (point(1, 'L', 10), point(2, 'H', 14), point(3, 'L', 12))
    for now in range(4):
        stream.advance(now, (), ())
    assert stream.advance(4, points, (opening(4, 1),))
    revised = (*points[:2], replace(points[2], confirmed_index=5, point=replace(points[2].point, price=11)))
    assert stream.advance(5, revised, ())[0].known_at == 5
    closed = dict(opening(6, 1), state='多空交替')
    assert stream.advance(6, (*revised, point(6, 'H', 15)), (closed,)) == ()


def test_unknown_vertices_are_rejected_without_advancing_clock():
    stream = EligiblePolylineN(2)
    with pytest.raises(ValueError, match='unknown'):
        stream.advance(0, (point(1, 'L', 10),), ())
    assert stream.advance(0, (), ()) == ()
