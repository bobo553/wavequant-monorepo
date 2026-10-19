"""Create causal strategy snapshots with the same reducer used by the chart.

The child/mother order is an explicit lecture convention, not observed ticks.
Snapshots share unchanged frozen turns; later drawing changes never backdate a signal.
Only undefined doji/initial-direction issues delimit structural episodes.
"""
import math
from collections.abc import Mapping, Sequence
from typing import TypeGuard, TypedDict, cast

from ..market_structure.lecture_drawing import lecture_drawing
from ..market_structure.polyline import LinePoint,PointKind,ReversalPoint


_PIVOT_SOURCE = 'lecture_close_confirmed_convention_not_intrabar_execution'


class _PivotPoint(TypedDict):
    index: int
    ordinal: int
    kind: str
    value: float
    state: str


def _same_frozen_pivot(previous: ReversalPoint | None, index: int, ordinal: int,
                       kind: str, price: float, confirmed: int) -> TypeGuard[ReversalPoint]:
    """Share an already validated value, preserving scalar types and provenance."""
    if type(previous) is not ReversalPoint or len(vars(previous)) != 3:
        return False
    point = previous.point
    return (
        type(point) is LinePoint and len(vars(point)) == 4
        and type(point.index) is type(index) is int
        and type(point.ordinal) is type(ordinal) is int
        and type(point.price) is type(price) and type(price) in (float, int)
        and type(point.kind) is PointKind and point.kind.value == kind
        and point.index == index and point.ordinal == ordinal and point.price == price
        and index >= 0 and ordinal >= 0 and math.isfinite(price) and price > 0
        and type(previous.confirmed_index) is type(confirmed) is int
        and previous.confirmed_index == confirmed and confirmed >= index
        and type(previous.source) is str and previous.source == _PIVOT_SOURCE
    )


class LecturePivotState:
    """Consume each published polyline once, freezing its first knowledge dates."""

    def __init__(self) -> None:
        self.snapshots: dict[int, tuple[ReversalPoint, ...]] = {}
        self.epochs: dict[int, int] = {}
        self.blocked: set[int] = set()
        self.prior: dict[tuple[int, int, str, float], ReversalPoint] = {}
        self.last_epoch: int | None = None

    def advance(self, i: int, epoch: int, points: Sequence[Mapping[str, object]]) -> tuple[ReversalPoint, ...]:
        if i != len(self.snapshots):
            raise ValueError("pivot days must be consecutive")
        prior, last_epoch = self.prior, self.last_epoch
        snapshots, epochs, blocked = self.snapshots, self.epochs, self.blocked
        if epoch!=last_epoch: prior={}
        last_epoch=epoch;epochs[i]=epoch
        if i and not points and epoch==i: blocked.add(i)
        known: list[ReversalPoint] = []
        now={}
        # Match reversal_trends' geometric-turn selection, not synthetic bridges.
        compact: list[_PivotPoint] = []
        for raw_point in points:
            p = cast(_PivotPoint, raw_point)
            if not compact or p['value']!=compact[-1]['value']:compact.append(p)
        for left,p,right in zip(compact,compact[1:],compact[2:]):
            if p['state'] in ('seed','developing'):continue
            kind=('H' if p['value']>left['value'] and p['value']>right['value'] else
                  'L' if p['value']<left['value'] and p['value']<right['value'] else None)
            if kind is None:continue
            # A geometric seam may turn at a candle high while looking like a
            # local low. Do not relabel that price as the candle's actual low.
            if kind!=p['kind']:continue
            key=(p['index'],p['ordinal'],kind,p['value'])
            previous=prior.get(key)
            confirmed=previous.confirmed_index if previous is not None else i
            if known:confirmed=max(confirmed,known[-1].confirmed_index)
            ref=(previous if _same_frozen_pivot(previous,p['index'],p['ordinal'],kind,p['value'],confirmed)
                 else ReversalPoint(LinePoint(p['index'],p['ordinal'],PointKind(kind),p['value']),confirmed,
                                    _PIVOT_SOURCE))
            # Geometric extrema alternate even through mother/child seams.
            if known and ref.point.kind==known[-1].point.kind:
                sign=1 if kind=='H' else -1
                if sign*(ref.point.price-known[-1].point.price)<=0:continue
                known.pop()
            known.append(ref);now[key]=ref
        prior=now;snapshots[i]=tuple(known)
        self.prior, self.last_epoch = prior, last_epoch
        return snapshots[i]


def lecture_pivot_history(bars, *, prefix_cache=None, _frames=None):
    snapshots={};epochs={};limits={};blocked=set();prior={};last_epoch=None
    last = len(bars) - 1
    prefix_key = tuple(bars[:-1])
    cached_key = prefix_cache.get('key') if prefix_cache is not None else None
    resume_start = (last if cached_key == prefix_key else
                    last - 1 if cached_key == tuple(bars[:-2]) else 0)
    if resume_start:
        saved_snapshots, saved_epochs, saved_blocked, saved_prior, last_epoch = prefix_cache['checkpoint']
        # Snapshot values are tuples of frozen ReversalPoints; only the outer
        # maps and set are extended while today's partial candle is reduced.
        snapshots, epochs, blocked, prior = (
            saved_snapshots.copy(), saved_epochs.copy(), saved_blocked.copy(), saved_prior.copy())
    state = LecturePivotState()
    state.snapshots, state.epochs, state.blocked = snapshots, epochs, blocked
    state.prior, state.last_epoch = prior, last_epoch

    def accept(i,epoch,points):
        nonlocal prior,last_epoch
        if i < resume_start:
            return
        state.advance(i, epoch, points)
        prior, last_epoch = state.prior, state.last_epoch
        if prefix_cache is not None and resume_start != last and i == last - 1:
            prefix_cache['key'] = prefix_key
            prefix_cache['checkpoint'] = (
                snapshots.copy(), epochs.copy(), blocked.copy(), prior.copy(), last_epoch)
    if _frames is None:
        lecture_drawing(bars,on_step=accept)
    else:
        for i, epoch, points in _frames:
            accept(i, epoch, points)
    # Existing event pipeline bounds setups to a causal episode; prefix tests
    # enforce that a future episode boundary cannot alter any earlier signal.
    end=len(bars)-1
    for i in range(end,-1,-1):
        if i in blocked:end=i-1
        limits[i]=end
    return snapshots,epochs,limits,blocked
