"""Create causal strategy snapshots with the same reducer used by the chart.

The child/mother order is an explicit lecture convention, not observed ticks.
Snapshots share unchanged frozen turns; later drawing changes never backdate a signal.
Only undefined doji/initial-direction issues delimit structural episodes.
"""
import math
from bisect import bisect_left
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TypeGuard, TypedDict, cast

from ..market_structure.lecture_drawing import lecture_drawing
from ..market_structure.polyline import LinePoint,PointKind,ReversalPoint
from ..market_structure.trend_point_version import PointVersion


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


class _OwnedLecturePivotVersions:
    """仅供 lecture_drawing 的私有逐日回调发布不可变尾部版本。"""

    def __init__(self) -> None:
        self._epoch: int | None = None
        self._version: PointVersion | None = None

    def publish(self, epoch: int, points: Sequence[Mapping[str, object]]) -> PointVersion:
        previous = self._version if epoch == self._epoch else None
        count = len(previous) if previous is not None else 0
        # 生产者只替换尾点；相邻锚被替换或路径缩短时不能继承旧前缀。
        retained = previous is not None and len(points) >= count and (
            count < 2 or points[count - 2] is previous[count - 2])
        start = max(0, count - 1) if retained else 0
        if (previous is not None and retained and count == len(points)
                and (not count or points[-1] is previous[-1])):
            return previous
        version = PointVersion(cast(Sequence[dict[str, object]], points[start:]),
            previous=previous, start=start).seal()
        self._epoch, self._version = epoch, version
        return version


_PivotKey = tuple[int, int, str, float]


@dataclass(frozen=True, slots=True)
class _PivotEdit:
    key: _PivotKey
    previous: ReversalPoint | None
    removed: ReversalPoint | None


class _VersionedLecturePivots:
    """只撤销变化三点组之后的编辑，保留被同类极值替换点的知识日期。"""

    def __init__(self) -> None:
        self.source: PointVersion | None = None
        self.positions: list[int] = []
        self.compact: list[_PivotPoint] = []
        self.known: list[ReversalPoint] = []
        self.prior: dict[_PivotKey, ReversalPoint] = {}
        self.edits: list[_PivotEdit | None] = [None]
        self._duplicate_keys = 0

    def advance(self, source: PointVersion, index: int,
                prior: Mapping[_PivotKey, ReversalPoint]) -> tuple[ReversalPoint, ...]:
        # 同坐标重复事件的后项可覆盖 prior 的知识日，下一日会影响前项。
        # 这种输入保留全量重放；正常讲义生产者的唯一坐标继续复用前缀。
        start = source.changed_since(self.source) if not self._duplicate_keys else 0
        if self.source is source and not self._duplicate_keys:
            return tuple(self.known)
        keep = bisect_left(self.positions, start)
        affected = max(1, keep - 1)
        original: dict[_PivotKey, ReversalPoint | None] = {}
        for edit in reversed(self.edits[affected:]):
            if edit is None:
                continue
            original.setdefault(edit.key, self.prior.get(edit.key))
            self.known.pop()
            if edit.removed is not None:
                self.known.append(edit.removed)
            if edit.previous is None:
                self.prior.pop(edit.key, None)
            else:
                self.prior[edit.key] = edit.previous
                self._duplicate_keys -= 1
        del self.edits[affected:]
        del self.positions[keep:]
        del self.compact[keep:]
        for position, raw in enumerate(source.iter_range(start), start):
            point = cast(_PivotPoint, raw)
            if not self.compact or point['value'] != self.compact[-1]['value']:
                self.positions.append(position)
                self.compact.append(point)
        for position in range(affected, len(self.compact) - 1):
            left, point, right = self.compact[position - 1:position + 2]
            edit = None
            if point['state'] not in ('seed', 'developing'):
                kind = ('H' if point['value'] > left['value'] and point['value'] > right['value'] else
                        'L' if point['value'] < left['value'] and point['value'] < right['value'] else None)
                if kind is not None and kind == point['kind']:
                    key = point['index'], point['ordinal'], kind, point['value']
                    previous = (original.get(key, prior.get(key)) if prior is self.prior else prior.get(key))
                    confirmed = previous.confirmed_index if previous is not None else index
                    if self.known:
                        confirmed = max(confirmed, self.known[-1].confirmed_index)
                    ref = (previous if _same_frozen_pivot(previous, point['index'], point['ordinal'], kind,
                           point['value'], confirmed) else ReversalPoint(LinePoint(point['index'],
                           point['ordinal'], PointKind(kind), point['value']), confirmed, _PIVOT_SOURCE))
                    removed = None
                    if self.known and ref.point.kind == self.known[-1].point.kind:
                        sign = 1 if kind == 'H' else -1
                        if sign * (ref.point.price - self.known[-1].point.price) <= 0:
                            self.edits.append(None)
                            continue
                        removed = self.known.pop()
                    original.setdefault(key, prior.get(key))
                    edit = _PivotEdit(key, self.prior.get(key), removed)
                    self._duplicate_keys += edit.previous is not None
                    self.known.append(ref)
                    self.prior[key] = ref
            self.edits.append(edit)
        self.source = source
        return tuple(self.known)


class LecturePivotState:
    """Consume each published polyline once, freezing its first knowledge dates."""

    def __init__(self) -> None:
        self.snapshots: dict[int, tuple[ReversalPoint, ...]] = {}
        self.epochs: dict[int, int] = {}
        self.blocked: set[int] = set()
        self.prior: dict[tuple[int, int, str, float], ReversalPoint] = {}
        self.last_epoch: int | None = None
        self._versioned: _VersionedLecturePivots | None = None

    def advance(self, i: int, epoch: int, points: Sequence[Mapping[str, object]]) -> tuple[ReversalPoint, ...]:
        if i != len(self.snapshots):
            raise ValueError("pivot days must be consecutive")
        prior, last_epoch = self.prior, self.last_epoch
        snapshots, epochs, blocked = self.snapshots, self.epochs, self.blocked
        if epoch!=last_epoch: prior={}
        last_epoch=epoch;epochs[i]=epoch
        if i and not points and epoch==i: blocked.add(i)
        if type(points) is PointVersion and points._sealed:
            if self._versioned is None or epoch != self.last_epoch:
                self._versioned = _VersionedLecturePivots()
            snapshots[i] = self._versioned.advance(points, i, prior)
            self.prior, self.last_epoch = self._versioned.prior, last_epoch
            return snapshots[i]
        self._versioned = None
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
