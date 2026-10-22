"""策略几何的私有九标量版本；不把证书或命名附件加入归档通道。"""

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from sys import getsizeof
from typing import cast
from weakref import ReferenceType, ref

from .trend_frozen_state import FrozenPrefixList
from .trend_point_version import PointVersion


STRATEGY_POINT_FIELDS = ('index', 'ordinal', 'time', 'kind', 'value', 'available_at', 'state', 'label', 'flip')


def permission_label(kind: str, counts: Counter[str]) -> str:
    """完整命名与标量投影共用整条许可路径的 H/L 序号。"""
    counts[kind] += 1
    return f'{kind}{counts[kind]}'


def scalar_point(point: Mapping[str, object]) -> dict[str, object] | None:
    fields = {name: point[name] for name in STRATEGY_POINT_FIELDS if name in point}
    if any(type(value) not in (str, int, float, bool, type(None)) for value in fields.values()):
        return None
    return fields


@dataclass(frozen=True, slots=True)
class StrategyGeometryStroke:
    names: tuple[tuple[str, str], ...]
    points: PointVersion


@dataclass(frozen=True, slots=True)
class StrategyGeometry:
    strokes: tuple[StrategyGeometryStroke, ...]


@dataclass(frozen=True, slots=True)
class _Projection:
    path: str
    source: ReferenceType[FrozenPrefixList[dict[str, object]]]
    token: object | None
    points: PointVersion
    counts: FrozenPrefixList[tuple[int, int]]
    costs: FrozenPrefixList[int]
    charged: int


class PermissionGeometryVersions:
    """每级只保留当前路径；原发布树弱引用不延长完整证书生命周期。"""

    def __init__(self, memory_bytes: int) -> None:
        self.limit = memory_bytes
        self._current: dict[int, _Projection] = {}
        self._blocked: dict[int, str] = {}

    @property
    def memory_used(self) -> int:
        return 4096 + sum(item.charged for item in self._current.values()) if self._current else 0

    def discard_optional(self) -> None:
        for level, saved in self._current.items():
            self._blocked[level] = saved.path
        self._current.clear()

    def project(self, level: int, path: str,
                source: Sequence[dict[str, object]]) -> PointVersion | None:
        if type(level) is not int or level not in (1, 2, 3) or type(path) is not str or len(path) > 1024:
            return None
        if (type(source) not in (FrozenPrefixList, PointVersion) or source.frozen_count != len(source)
                or type(source) is PointVersion and not source._sealed):
            return None
        if self.memory_used > self.limit:
            self.discard_optional()
        if self._blocked.get(level) == path:
            return None
        saved = self._current.get(level)
        if saved is not None and saved.path != path:
            self._current.pop(level)
            saved = None
        previous = saved.source() if saved is not None else None
        if saved is not None and type(source) is PointVersion and source.token is saved.token:
            common = len(source)
        elif (saved is not None and saved.token is not None and type(source) is PointVersion
                and source.parent is saved.token):
            common = source.changed_from
        else:
            common = source.shared_prefix(previous) if previous is not None else 0
        if not 0 <= common <= min(len(source), len(saved.points) if saved is not None else 0):
            raise ValueError('permission projection requires an exact source prefix')
        if saved is not None and common == len(source) == len(saved.points):
            self._current[level] = _Projection(path, ref(source),
                source.token if type(source) is PointVersion else None,
                saved.points, saved.counts, saved.costs, saved.charged)
            return saved.points
        counts = saved.counts.copy() if saved is not None else FrozenPrefixList[tuple[int, int]]()
        costs = saved.costs.copy() if saved is not None else FrozenPrefixList[int]([0])
        del counts[common:]
        del costs[common + 1:]
        highs, lows = counts[-1] if counts else (0, 0)
        labels: Counter[str] = Counter(H=highs, L=lows)
        last_order = (cast(tuple[int, int], (saved.points[common - 1]['index'], saved.points[common - 1]['ordinal']))
                      if saved is not None and common else None)
        suffix: list[dict[str, object]] = []
        remaining = (self.limit - self.memory_used + (saved.charged if saved is not None else 0)
                     - (4096 if not self._current else 0))
        for point in source.iter_range(common):
            projected = scalar_point(point)
            kind = cast(str, point.get('kind'))
            order = cast(tuple[int, int], (point.get('index'), point.get('ordinal')))
            if (projected is None or kind not in ('H', 'L')
                    or any(type(value) is not int for value in order)
                    or last_order is not None and order <= last_order):
                self._current.pop(level, None)
                self._blocked[level] = path
                return None
            projected['label'] = permission_label(kind, labels)
            last_order = order
            suffix.append(projected)
            counts.append((labels['H'], labels['L']))
            # 每项独占计费是共享标量图的保守上界；树节点另计，无逐日全图扫描。
            charged = getsizeof(projected) + sum(getsizeof(key) + getsizeof(value)
                for key, value in projected.items()) + 256
            costs.append(costs[-1] + charged)
            if costs[-1] > remaining:
                self._current.pop(level, None)
                self._blocked[level] = path
                return None
        result = PointVersion(suffix, previous=saved.points if saved is not None else None, start=common).seal()
        counts.freeze(len(counts))
        costs.freeze(len(costs))
        charged = costs[-1] + result.storage_bytes + counts.storage_bytes + costs.storage_bytes + 1024 + getsizeof(path)
        if charged > remaining:
            self._current.pop(level, None)
            self._blocked[level] = path
            return None
        self._blocked.pop(level, None)
        self._current[level] = _Projection(path, ref(source),
            source.token if type(source) is PointVersion else None, result, counts, costs, charged)
        return result
