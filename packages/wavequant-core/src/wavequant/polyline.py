"""Causal high/low lecture polyline; ambiguous bars require explicit path evidence."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Sequence
from collections.abc import Mapping as MappingABC

from .model import Bar
from .price_action import Direction, _index, _positive, _validate_bar, _ordered_pair
from .n_shape import BoxAnchorMode, NSetup, PivotRef


class PointKind(str, Enum):
    HIGH = 'H'
    LOW = 'L'


@dataclass(frozen=True)
class BarRelations:
    shrinking_head: bool
    shrinking_foot: bool
    extending_head: bool
    falling_tail: bool
    sunrise: bool
    sunset: bool
    inside: bool
    outside: bool
    equal_high: bool
    equal_low: bool


def observe_bar_relations(previous: Bar, current: Bar) -> BarRelations:
    _ordered_pair(previous, current)
    h, l = current.high, current.low
    return BarRelations(h < previous.high, l > previous.low,
        h > previous.high, l < previous.low,
        h > previous.high and l > previous.low and current.close > previous.high,
        h < previous.high and l < previous.low and current.close < previous.low,
        h < previous.high and l > previous.low, h > previous.high and l < previous.low,
        h == previous.high, l == previous.low)


@dataclass(frozen=True)
class LinePoint:
    index: int
    ordinal: int
    kind: PointKind
    price: float

    def __post_init__(self):
        _index(self.index, 'point index')
        _index(self.ordinal, 'intrabar ordinal')
        _positive(self.price, 'point price')
        if not isinstance(self.kind, PointKind):
            raise ValueError('PointKind required')


@dataclass(frozen=True)
class ReversalPoint:
    point: LinePoint
    confirmed_index: int
    source: str

    def __post_init__(self):
        if not isinstance(self.point, LinePoint):
            raise ValueError('LinePoint required')
        _index(self.confirmed_index, 'confirmation index')
        if self.confirmed_index < self.point.index or not isinstance(self.source, str) or not self.source.strip():
            raise ValueError('causal confirmation and provenance required')

    @property
    def reversal(self) -> str:
        return '正反转' if self.point.kind == PointKind.LOW else '负反转'


@dataclass(frozen=True)
class BarPathEvidence:
    """Caller-supplied order of extremes, not inferred from candle colour."""
    index: int
    order: tuple[PointKind, PointKind]
    source: str

    def __post_init__(self):
        _index(self.index, 'path index')
        if (not isinstance(self.order, tuple) or len(self.order) != 2 or
                any(not isinstance(k, PointKind) for k in self.order) or
                set(self.order) != {PointKind.HIGH, PointKind.LOW}):
            raise ValueError('ordered high/low pair required')
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError('path provenance required')


@dataclass(frozen=True)
class TeachingPath:
    vertices: tuple[LinePoint, ...]
    status: str
    provenance: str = 'lecture_child_mother_colour_convention_not_observed_intrabar_path'


def child_mother_path(child: Bar, mother: Bar, *, child_index: int) -> TeachingPath:
    """Four explicit child-then-mother teaching cases; no reverse-order inference."""
    r = observe_bar_relations(child, mother)
    _index(child_index, 'child index')
    if not r.outside:
        raise ValueError('strict child then encompassing mother required')
    if child.open == child.close or mother.open == mother.close:
        return TeachingPath((), 'undefined_doji')
    first = PointKind.HIGH if child.close > child.open else PointKind.LOW
    order = ((PointKind.LOW, PointKind.HIGH) if mother.close > mother.open else
             (PointKind.HIGH, PointKind.LOW))
    def vertex(bar, index, ordinal, kind):
        return LinePoint(index, ordinal, kind, bar.high if kind == PointKind.HIGH else bar.low)
    return TeachingPath((vertex(child, child_index, 0, first),
        vertex(mother, child_index+1, 0, order[0]),
        vertex(mother, child_index+1, 1, order[1])), 'teaching_convention')


@dataclass(frozen=True)
class PolylineFrame:
    bar_index: int
    direction: Direction
    candidate: LinePoint
    new_reversals: tuple[ReversalPoint, ...]
    relations: BarRelations | None
    blocked_at: int | None
    path_source: str


@dataclass(frozen=True)
class PolylineObservation:
    symbol: str
    timeframe: str
    start_index: int
    asof_index: int
    reversals: tuple[ReversalPoint, ...]
    frames: tuple[PolylineFrame, ...]

    @property
    def blocked_at(self):
        return self.frames[-1].blocked_at


def observe_polyline(bars: Sequence[Bar], *, symbol: str, timeframe: str,
                     initial_direction: Direction, start_index: int,
                     paths: Mapping[int, BarPathEvidence] | None = None,
                     asof_index: int | None = None) -> PolylineObservation:
    """Initial direction is an explicit seed, never a fabricated confirmed pivot.

    Confirmed turns are append-only. The unfinished endpoint may extend, and is
    never offered as a known N anchor. Inside/outside ambiguity blocks this run
    until an explicitly evidenced new run, rather than silently skipping a bar.
    """
    end = len(bars)-1 if asof_index is None else asof_index
    _index(start_index, 'start_index')
    _index(end, 'asof_index')
    if end >= len(bars) or start_index > end:
        raise ValueError('available ordered window required')
    if (not isinstance(initial_direction, Direction) or not isinstance(symbol, str) or not symbol.strip()
            or not isinstance(timeframe, str) or not timeframe.strip()):
        raise ValueError('explicit direction, symbol and timeframe required')
    if paths is not None and not isinstance(paths, MappingABC):
        raise ValueError('paths must map bar indices to explicit evidence')
    for i in range(start_index, end+1):
        _validate_bar(bars[i])
        if bars[i].symbol != symbol:
            raise ValueError('symbol mismatch')
        if i > start_index:
            _ordered_pair(bars[i-1], bars[i])
    direction = initial_direction
    kind = PointKind.HIGH if direction == Direction.UP else PointKind.LOW
    candidate = LinePoint(start_index, 0, kind,
                          bars[start_index].high if kind == PointKind.HIGH else bars[start_index].low)
    frames = [PolylineFrame(start_index, direction, candidate, (), None, None, 'explicit_seed')]
    reversals, blocked = [], None
    paths = {} if paths is None else paths
    for i in range(start_index+1, end+1):
        r = observe_bar_relations(bars[i-1], bars[i])
        new = []
        source = 'lecture_bar_relation_v1'
        if blocked is None:
            if r.inside or r.outside:
                evidence = paths.get(i)
                if evidence is None:
                    blocked = i
                    source = 'requires_ordered_intrabar_evidence'
                    vertices = ()
                else:
                    if not isinstance(evidence, BarPathEvidence) or evidence.index != i:
                        raise ValueError('matching BarPathEvidence required')
                    source = evidence.source
                    vertices = tuple(LinePoint(i, j, k, bars[i].high if k == PointKind.HIGH else bars[i].low)
                                     for j, k in enumerate(evidence.order))
            else:
                reverse = (r.falling_tail or r.shrinking_head) if direction == Direction.UP else (
                    r.extending_head or r.shrinking_foot)
                target = (PointKind.LOW if direction == Direction.UP else PointKind.HIGH) if reverse else candidate.kind
                vertices = (LinePoint(i, 0, target, bars[i].high if target == PointKind.HIGH else bars[i].low),)
            for vertex in vertices:
                sign = 1 if direction == Direction.UP else -1
                if vertex.kind == candidate.kind:
                    if sign * (vertex.price-candidate.price) > 0:
                        candidate = vertex
                elif sign * (vertex.price-candidate.price) < 0:
                    turn = ReversalPoint(candidate, i, source)
                    new.append(turn)
                    reversals.append(turn)
                    candidate = vertex
                    direction = Direction.DOWN if direction == Direction.UP else Direction.UP
                # Zero-length legs are not reversal events.
        else:
            source = 'blocked_by_unresolved_earlier_bar'
        frames.append(PolylineFrame(i, direction, candidate, tuple(new), r, blocked, source))
    return PolylineObservation(symbol, timeframe, start_index, end, tuple(reversals), tuple(frames))


def n_setup_from_polyline(line: PolylineObservation, *, box_anchor_mode: BoxAnchorMode) -> NSetup:
    """Bridge only confirmed, distinct-bar A/B/C; N layer validates geometry."""
    if not isinstance(line, PolylineObservation) or line.blocked_at is not None:
        raise ValueError('unblocked PolylineObservation required')
    if len(line.reversals) < 3:
        raise ValueError('three confirmed pivots required')
    a, b, c = line.reversals[-3:]
    if not a.point.index < b.point.index < c.point.index:
        raise ValueError('same-bar pivots require a lower-timeframe N, not daily projection')
    direction = Direction.UP if a.point.kind == PointKind.LOW else Direction.DOWN
    return NSetup(line.symbol, line.timeframe, direction,
        *(PivotRef(p.point.index, p.confirmed_index) for p in (a, b, c)),
        source='polyline_confirmed:' + '|'.join(p.source for p in (a, b, c)),
        box_anchor_mode=box_anchor_mode)
