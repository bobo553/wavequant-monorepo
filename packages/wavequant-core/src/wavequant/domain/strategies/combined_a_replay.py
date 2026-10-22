"""有界保存组合 A 实际消费的标量几何，供资格窗口修订后的附件重算。"""

from collections.abc import Mapping, Sequence
from hashlib import sha256
import pickle
from dataclasses import dataclass
from sys import getsizeof
from tempfile import TemporaryFile
from typing import cast
from weakref import ReferenceType, ref
from zlib import compress, decompress, error as ZlibError

from ..market_structure.trend_market_index import _MarketRangeIndex
from ..market_structure.trend_point_version import PointVersion
from ..market_structure.strategy_geometry_version import (
    STRATEGY_POINT_FIELDS, StrategyGeometry, StrategyGeometryStroke, scalar_point,
)
from ..models.model import Bar
from .backtest_history_reuse import _certificate_bytes
from .combined_a_entry import CombinedAContext, GeometryLevel, combined_a_contexts_from_geometry


_POINT_FIELDS = STRATEGY_POINT_FIELDS
_BLOCK = 32
_TreeRef = tuple[int, int, int]


@dataclass(frozen=True, slots=True)
class _PointRoot:
    root: _TreeRef | None
    capacity: int
    length: int


@dataclass(frozen=True, slots=True)
class _CurrentPoints:
    points: ReferenceType[PointVersion] | None
    token: object | None
    root: _PointRoot


@dataclass(frozen=True, slots=True)
class _DrawingPoints:
    length: int
    closed: bool


def attack_certificates(audit: Sequence[Mapping[str, object]], cutoff: int) -> dict[int, bytes] | None:
    """认证每个攻击日最后一条完整记录；只留定长摘要，最多 4096 项。"""
    positive = {cast(int, row['bar_index']): row for row in audit
                if row.get('event') == 'n_completed' and row.get('direction') == 'up'
                and cast(int, row['bar_index']) <= cutoff}
    if len(positive) > 4096:
        return None
    return {index: sha256(_certificate_bytes(row)).digest() for index, row in positive.items()}


def later_attack_revision(audit: Sequence[Mapping[str, object]], previous: object, cutoff: int) -> bool:
    """仅接纳新窗口延后知识日期的修订；早期证据修订仍走完整冷重放。"""
    if type(previous) is not dict:
        return False
    positive = {cast(int, row['bar_index']): row for row in audit
                if row.get('event') == 'n_completed' and row.get('direction') == 'up'
                and cast(int, row['bar_index']) < cutoff}
    for index, row in positive.items():
        if previous.get(index) != sha256(_certificate_bytes(row)).digest():
            known = row.get('known_at', index)
            if type(known) is not int or known < cutoff:
                return False
    return True


def _geometry(level: Mapping[str, object]) -> GeometryLevel | None:
    strokes = []
    for stroke in cast('Sequence[Mapping[str, object]]', level.get('strokes', ())):
        projected: dict[str, object] = {}
        for name in ('id', 'source_path'):
            if name in stroke:
                value = stroke[name]
                if type(value) is not str:
                    return None
                projected[name] = value
        points = []
        for point in cast('Sequence[Mapping[str, object]]', stroke.get('points', ())):
            fields = {name: point[name] for name in _POINT_FIELDS if name in point}
            if any(type(value) not in (str, int, float, bool, type(None)) for value in fields.values()):
                return None
            points.append(fields)
        projected['points'] = points
        strokes.append(projected)
    return cast(GeometryLevel, {'strokes': strokes})


class CombinedAReplay:
    """只保存算法声明消费的几何字段，不保存完整证明树或另算趋势。

    单行原始编码不超过 1 MiB；实时请求的几何附件写入专属临时文件，
    内存只保留有界索引。文件由请求作用域关闭，不接收外部路径或负载。
    缺行、未知字段或索引/磁盘超限仍拒绝局部修复，沿用正确的完整重放。
    """

    def __init__(self, *, memory_bytes: int = 4 * 1024 * 1024, spill: bool = False) -> None:
        self._limit = memory_bytes
        self._bytes = 4096
        self._rows: dict[int, bytes | tuple[int, int]] = {}
        try:
            self._stream = TemporaryFile(mode='w+b', buffering=0) if spill else None
        except OSError:
            self._stream = None
        self._disk_bytes = 0
        self._disk_limit = 128 * 1024 * 1024
        self._accepting = memory_bytes >= 4096 + 512
        self._point_roots: dict[tuple[int, int, tuple[tuple[str, str], ...]], _CurrentPoints] = {}
        self._drawing: dict[str, _DrawingPoints] = {}
        self._drawing_index = -1
        self._tree_writes = 0

    @property
    def _metadata_bytes(self) -> int:
        return (sum(1024 + getsizeof(key) + getsizeof(key[2]) + sum(getsizeof(name) + getsizeof(value)
                    for name, value in key[2]) for key in self._point_roots)
                + sum(256 + getsizeof(path) for path in self._drawing))

    @classmethod
    def for_replay(cls, previous: object, *, resume_start: int, spill: bool = False) -> 'CombinedAReplay':
        """冷重放不能重置本请求已确认的容量拒绝，否则重复编码同一旧前缀。"""
        if isinstance(previous, cls) and (resume_start or not previous._accepting):
            return previous
        if isinstance(previous, cls):
            previous.close()
        return cls(spill=spill)

    def close(self) -> None:
        self._accepting = False
        self._rows.clear()
        self._point_roots.clear()
        self._drawing.clear()
        self._bytes = 4096
        if self._stream is not None:
            self._stream.close()

    def _stop_admission(self) -> None:
        # 缺少任一有效日后，完整前缀附件无法局部修复；不再反复编码必定
        # 用不上的后续行。后续冷重放保留拒绝状态，直到请求结束。
        self._accepting = False
        self._rows.clear()
        self._point_roots.clear()
        self._drawing.clear()
        self._bytes = 4096
        if self._stream is not None:
            self._stream.close()

    def _append_payload(self, payload: bytes) -> tuple[int, int]:
        if self._stream is None:
            raise ValueError('persistent geometry requires its request stream')
        if self._disk_bytes + len(payload) > self._disk_limit:
            raise ValueError('geometry archive disk budget exceeded')
        self._stream.seek(0, 2)
        offset = self._stream.tell()
        if self._stream.write(payload) != len(payload):
            raise ValueError('incomplete request-owned geometry write')
        self._disk_bytes += len(payload)
        return offset, len(payload)

    def _node(self, reference: _TreeRef) -> tuple[object, ...]:
        offset, size, _logical_bytes = reference
        try:
            node = pickle.loads(decompress(self._read_span((offset, size))))
        except (ZlibError, pickle.UnpicklingError, EOFError) as error:
            raise ValueError('invalid request-owned geometry node') from error
        if not (type(node) is tuple and (len(node) == 2 and node[0] == 'leaf' and type(node[1]) is list
                or len(node) == 3 and node[0] == 'branch')):
            raise ValueError('invalid request-owned geometry node shape')
        return cast(tuple[object, ...], node)

    def _write_node(self, node: tuple[object, ...], logical_bytes: int) -> _TreeRef:
        payload = compress(pickle.dumps(node, protocol=5), level=1)
        offset, size = self._append_payload(payload)
        self._tree_writes += 1
        return offset, size, logical_bytes

    def _replace_block(self, root: _TreeRef | None, capacity: int,
                       position: int, leaf: _TreeRef) -> _TreeRef:
        if capacity == 1:
            return leaf
        left: _TreeRef | None = None
        right: _TreeRef | None = None
        if root is not None:
            tag, left, right = cast(tuple[str, _TreeRef | None, _TreeRef | None], self._node(root))
            if tag != 'branch':
                raise ValueError('geometry tree requires a branch')
        half = capacity // 2
        if position < half:
            left = self._replace_block(left, half, position, leaf)
        else:
            right = self._replace_block(right, half, position - half, leaf)
        return self._write_node(('branch', left, right), (left[2] if left else 0) + (right[2] if right else 0))

    def _trim_blocks(self, root: _TreeRef | None, capacity: int, blocks: int) -> _TreeRef | None:
        if blocks <= 0:
            return None
        if root is None or blocks >= capacity:
            return root
        tag, left, right = cast(tuple[str, _TreeRef | None, _TreeRef | None], self._node(root))
        if tag != 'branch':
            raise ValueError('geometry tree requires a branch')
        half = capacity // 2
        left = self._trim_blocks(left, half, blocks)
        right = self._trim_blocks(right, half, blocks - half)
        return self._write_node(('branch', left, right), (left[2] if left else 0) + (right[2] if right else 0))

    def _pack_points(self, channel: tuple[int, int, tuple[tuple[str, str], ...]],
                     points: Sequence[Mapping[str, object]], version: PointVersion | None,
                     *, _changed_from: int | None = None) -> _PointRoot:
        if version is None and _changed_from is None:
            # 未认证来源沿用整条标量编码，不把普通批量退化为每块重建索引树。
            block = [scalar_point(point) for point in points]
            if any(point is None for point in block):
                raise ValueError('geometry archive requires scalar consumed fields')
            raw = pickle.dumps(('leaf', block), protocol=5)
            if len(raw) > 1024 * 1024:
                raise ValueError('geometry archive logical row budget exceeded')
            offset, size = self._append_payload(compress(raw, level=1))
            self._tree_writes += 1
            self._point_roots.pop(channel, None)
            return _PointRoot((offset, size, len(raw)), 1, len(points))
        saved = self._point_roots.get(channel)
        previous = saved.points() if saved is not None and saved.points is not None else None
        if _changed_from is not None and saved is not None:
            common = _changed_from
        elif version is not None and saved is not None and version.token is saved.token:
            common = len(version)
        elif (version is not None and saved is not None and saved.token is not None
              and version.parent is saved.token):
            common = version.changed_from
        else:
            common = version.changed_since(previous) if version is not None and previous is not None else 0
        if not 0 <= common <= min(len(points), saved.root.length if saved is not None else 0):
            raise ValueError('geometry archive requires an exact changed suffix')
        if saved is not None and common == len(points) == saved.root.length:
            if version is not None:
                self._point_roots[channel] = _CurrentPoints(ref(version), version.token, saved.root)
            return saved.root
        root = saved.root.root if saved is not None else None
        capacity = saved.root.capacity if saved is not None else 1
        # 可见长度封住旧尾部。后续追加必从含变化边界的块重写，不能露出未来点。
        for position in range(common // _BLOCK, (len(points) + _BLOCK - 1) // _BLOCK):
            block = [scalar_point(point) for point in points[position * _BLOCK:(position + 1) * _BLOCK]]
            if any(point is None for point in block):
                raise ValueError('geometry archive requires scalar consumed fields')
            raw = pickle.dumps(('leaf', block), protocol=5)
            while position >= capacity:
                root = self._write_node(('branch', root, None), root[2] if root else 0)
                capacity *= 2
            offset, size = self._append_payload(compress(raw, level=1))
            self._tree_writes += 1
            root = self._replace_block(root, capacity, position, (offset, size, len(raw)))
        if saved is not None and len(points) < saved.root.length:
            root = self._trim_blocks(root, capacity, (len(points) + _BLOCK - 1) // _BLOCK)
        result = _PointRoot(root, capacity, len(points))
        if version is not None:
            self._point_roots[channel] = _CurrentPoints(ref(version), version.token, result)
        elif _changed_from is not None:
            self._point_roots[channel] = _CurrentPoints(None, None, result)
        else:
            self._point_roots.pop(channel, None)
        return result

    def _drawing_geometry(self, index: int, epoch: int,
                          drawing: Mapping[str, object]) -> dict[tuple[tuple[str, str], ...], int]:
        """只供真实 live frame 使用现有 lecture 尾点/关闭路径协议。"""
        if index <= self._drawing_index:
            self._drawing.clear()
        self._drawing_index = index
        changes: dict[tuple[tuple[str, str], ...], int] = {}
        visible: set[str] = set()
        for stroke in cast(Sequence[Mapping[str, object]], drawing.get('strokes', ())):
            path = stroke.get('id')
            if type(path) is not str:
                raise ValueError('owned drawing requires its explicit path identity')
            visible.add(path)
            points = cast(Sequence[Mapping[str, object]], stroke['points'])
            saved = self._drawing.get(path)
            closed = path != f'lecture-{epoch}'
            if saved is not None and saved.closed:
                if len(points) != saved.length:
                    raise ValueError('owned closed drawing path changed its length')
                common = saved.length
            else:
                common = max(0, saved.length - 1) if saved is not None and len(points) >= saved.length - 1 else 0
            self._drawing[path] = _DrawingPoints(len(points), closed)
            changes[(('id', path),)] = common
        for path in tuple(self._drawing):
            if path not in visible:
                del self._drawing[path]
        return changes

    def _versioned_geometry(self, levels: Sequence[Mapping[str, object]],
                            drawing: dict[tuple[tuple[str, str], ...], int] | None) -> tuple[object, ...]:
        result: list[object] = []
        channels: set[tuple[int, int, tuple[tuple[str, str], ...]]] = set()
        logical_bytes = 0
        for number, level in enumerate(levels):
            descriptor = None if number == 0 else level.get('_combined_a_geometry')
            versions = ({item.names: item.points for item in descriptor.strokes
                         if type(item) is StrategyGeometryStroke and type(item.points) is PointVersion
                         and item.points._sealed}
                        if type(descriptor) is StrategyGeometry else {})
            strokes = []
            for position, stroke in enumerate(cast(Sequence[Mapping[str, object]], level.get('strokes', ()))):
                names = tuple((name, stroke[name]) for name in ('id', 'source_path') if name in stroke)
                if any(type(value) is not str for _name, value in names):
                    raise ValueError('geometry path identity must be a string')
                names = cast(tuple[tuple[str, str], ...], names)
                version = versions.get(names)
                points = version if version is not None else cast(Sequence[Mapping[str, object]], stroke.get('points', ()))
                channel = number, position, names
                root = self._pack_points(channel, points, version,
                    _changed_from=drawing.get(names) if number == 0 and drawing is not None else None)
                logical_bytes += root.root[2] if root.root is not None else 0
                if logical_bytes > 1024 * 1024:
                    raise ValueError('geometry archive logical row budget exceeded')
                channels.add(channel)
                strokes.append((names, root.root, root.capacity, root.length))
            result.append(tuple(strokes))
        for channel in tuple(self._point_roots):
            if channel not in channels:
                del self._point_roots[channel]
        descriptor = ('scalar_tree_v1', tuple(result))
        if logical_bytes + len(pickle.dumps(descriptor, protocol=5)) > 1024 * 1024:
            raise ValueError('geometry archive logical row budget exceeded')
        return descriptor

    def remember(self, index: int, levels: Sequence[Mapping[str, object]], *,
                 _fixed_day: bool = False, _drawing_epoch: int | None = None) -> None:
        if not self._accepting:
            return
        # 只读 live owner 的昨日行情已完整封存；audit 修订由 recompute 消费，
        # 不会修改当日四层几何。公共/分钟重放仍覆盖同一天，不能按日期猜复用。
        if _fixed_day and index in self._rows:
            return
        previous = self._rows.pop(index, None)
        if previous is not None:
            self._bytes -= (len(previous) if isinstance(previous, bytes) else 0) + 512
        # 上次调用的最后一天可能未完成；重放当天时必须替换其几何。
        if self._stream is not None and (_drawing_epoch is not None or any(
                type(level.get('_combined_a_geometry')) is StrategyGeometry for level in levels)):
            try:
                drawing = (self._drawing_geometry(index, _drawing_epoch, levels[0])
                           if _drawing_epoch is not None else None)
                geometry = self._versioned_geometry(levels, drawing)
            except (OSError, ValueError):
                self._stop_admission()
                return
        else:
            geometry = tuple(_geometry(level) for level in levels)
        if any(level is None for level in geometry):
            self._stop_admission()
            return
        payload = pickle.dumps(geometry, protocol=5)
        if len(payload) > 1024 * 1024:
            self._stop_admission()
            return
        payload = compress(payload, level=1)
        charged = (len(payload) if self._stream is None else 0) + 512
        if self._bytes + self._metadata_bytes + charged > self._limit:
            self._stop_admission()
            return
        if self._stream is None:
            self._rows[index] = payload
        else:
            if self._disk_bytes + len(payload) > self._disk_limit:
                self._stop_admission()
                return
            try:
                self._stream.seek(0, 2)
                offset = self._stream.tell()
                if self._stream.write(payload) != len(payload):
                    self._stop_admission()
                    return
            except OSError:
                self._stop_admission()
                return
            self._disk_bytes += len(payload)
            self._rows[index] = offset, len(payload)
        self._bytes += charged

    def _read_span(self, row: tuple[int, int]) -> bytes:
        if self._stream is None:
            raise RuntimeError('disk replay requires its request stream')
        offset, length = row
        self._stream.seek(offset)
        payload = self._stream.read(length)
        if len(payload) != length:
            raise ValueError('incomplete request-owned geometry row')
        return payload

    def _read(self, index: int) -> bytes:
        row = self._rows[index]
        return row if isinstance(row, bytes) else self._read_span(row)

    def _restore_geometry(self, archived: object) -> tuple[GeometryLevel, ...]:
        if type(archived) is tuple and len(archived) == 4:
            return cast(tuple[GeometryLevel, ...], archived)
        if not (type(archived) is tuple and len(archived) == 2 and archived[0] == 'scalar_tree_v1'
                and type(archived[1]) is tuple and len(archived[1]) == 4):
            raise ValueError('geometry archive requires its four dated levels')
        levels = []
        for rows in archived[1]:
            strokes = []
            for names, root, capacity, length in rows:
                points = []
                pending = [(root, capacity, 0)]
                while pending:
                    reference, width, start = pending.pop()
                    if start >= length:
                        continue
                    if reference is None:
                        raise ValueError('geometry archive is missing a visible point block')
                    node = self._node(reference)
                    if width == 1:
                        if node[0] != 'leaf':
                            raise ValueError('geometry point block cannot contain a branch')
                        points.extend(cast(list[dict[str, object]], node[1])[:length - start])
                    else:
                        if node[0] != 'branch':
                            raise ValueError('geometry index cannot contain a point block')
                        half = width // 2
                        pending.extend(((cast(_TreeRef | None, node[2]), half, start + half * _BLOCK),
                                        (cast(_TreeRef | None, node[1]), half, start)))
                if len(points) != length:
                    raise ValueError('geometry archive has an incomplete point prefix')
                strokes.append(dict(names, points=points))
            levels.append(dict(strokes=strokes))
        return cast(tuple[GeometryLevel, ...], tuple(levels))

    def recompute(
        self, bars: Sequence[Bar], dates: Mapping[str, int], entry_days: Sequence[bool], cutoff: int,
        audit: Sequence[Mapping[str, object]], market_index: _MarketRangeIndex | None,
    ) -> dict[int, tuple[CombinedAContext, ...]] | None:
        if not self._accepting:
            return None
        indices = [index for index in range(cutoff) if entry_days[index]]
        if any(index not in self._rows for index in indices):
            return None
        result = {}
        for index in indices:
            try:
                payload = self._read(index)
                geometry = self._restore_geometry(pickle.loads(decompress(payload)))
            except (OSError, ValueError, ZlibError, pickle.UnpicklingError, EOFError):
                self._stop_admission()
                return None
            drawing, first, second, third = geometry
            result[index] = combined_a_contexts_from_geometry(
                bars, drawing, first, second, third, dates, index, audit, _market_index=market_index)
        return result
