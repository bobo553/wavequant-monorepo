"""开放路径点数组的持久分块版本；只改变化块，读取边界恢复完整数组。"""

from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass
from sys import getsizeof
from typing import cast
from uuid import uuid4

from .owned_evidence import OwnedEvidence, owned_signature
from .trend_staging import TrendStageRows
from .trend_frozen_state import FrozenPrefixList
from .trend_point_version import PointVersion


_BLOCK = 32
_Node = tuple[str, object, object]


def _node(value: object) -> _Node:
    if type(value) is not tuple or len(value) != 3 or value[0] not in ('branch', 'leaf'):
        raise ValueError('point history requires an owned tree node')
    return cast(_Node, value)


@dataclass(frozen=True, slots=True)
class PreparedPointReference:
    owner: str
    root: int | None
    capacity: int
    length: int


class PreparedPointHistory:
    """每条通道保存当前键；历史树节点放在有界缓冲的临时流中。

    32 点一块，替换一个块只写该块与 O(log 块数) 个新索引节点；旧根从不
    改写。缩短版本只缩短可见长度，随后追加必须覆盖对应尾块，不能暴露旧
    根中保留的未来点。外部未知对象与内存预算不足继续走完整行协议。
    """

    def __init__(self, evidence: OwnedEvidence, *, memory_bytes: int = 8 * 1024 * 1024) -> None:
        if type(memory_bytes) is not int or memory_bytes < 0:
            raise ValueError('point history memory_bytes must be a nonnegative integer')
        self.owner = uuid4().hex
        self.evidence = evidence
        self._limit = memory_bytes
        self._used = 0
        self._current: dict[tuple[str, str], tuple[FrozenPrefixList[bytes], PreparedPointReference, int]] = {}
        self._key_costs: dict[tuple[str, str], FrozenPrefixList[int]] = {}
        self._owned_points: dict[tuple[str, str], Sequence[dict[str, object]]] = {}
        self._rows = TrendStageRows(_node, payload_cache_bytes=0)
        self._cache: OrderedDict[int, _Node] = OrderedDict()
        self._cache_bytes = 0

    def _write(self, node: _Node) -> int:
        index = self._rows.row_count
        self._rows.write(node)
        self._remember(index, node)
        return index

    def _remember(self, index: int, node: _Node) -> None:
        # 只缓存固定大小的树索引；证明叶子仍由流与有界证据池拥有。
        if node[0] != 'branch':
            return
        self._cache[index] = node
        self._cache_bytes += getsizeof(node) + 128
        while self._cache and (len(self._cache) > 256 or self._cache_bytes > 64 * 1024):
            _, removed = self._cache.popitem(last=False)
            self._cache_bytes -= getsizeof(removed) + 128

    def _get(self, index: int) -> _Node:
        cached = self._cache.get(index)
        if cached is not None:
            self._cache.move_to_end(index)
            return cached
        reader = self._rows.iter_rows(seal=False, start_row=index)
        try:
            value = next(reader)
        finally:
            reader.close()
        self._remember(index, value)
        return value

    def _replace(self, root: int | None, capacity: int, position: int, block: int) -> int:
        if capacity == 1:
            return block
        left: int | None = None
        right: int | None = None
        if root is not None:
            kind, raw_left, raw_right = self._get(root)
            if kind != 'branch':
                raise ValueError('point history branch cannot contain a leaf')
            left, right = cast(int | None, raw_left), cast(int | None, raw_right)
        half = capacity // 2
        if position < half:
            left = self._replace(left, half, position, block)
        else:
            right = self._replace(right, half, position - half, block)
        return self._write(('branch', left, right))

    def pack(self, channel: tuple[str, str], points: Sequence[dict[str, object]], *,
             _owned_previous: Sequence[dict[str, object]] | None = None,
             _changed_from: int | None = None) -> PreparedPointReference | None:
        previous = self._current.get(channel)
        owned = _changed_from is not None
        parent = self._owned_points.get(channel)
        if isinstance(points, PointVersion) and points._sealed and previous is not None:
            if points is parent:
                return previous[1]
            if isinstance(parent, PointVersion) and (not owned or parent is not _owned_previous):
                _owned_previous, _changed_from = parent, points.changed_since(parent)
                owned = True
        keep = (_changed_from if owned and previous is not None and parent is not None
                and parent is _owned_previous else 0)
        if keep is None or not 0 <= keep <= min(len(points), len(previous[0]) if previous is not None else 0):
            raise ValueError('point history delta must cover the changed suffix')
        tail = points[keep:]
        if not self.evidence.finite(tail):
            return None
        # 块之间的共享必须有同一请求的引用，不能把跨块别名拆成多个副本。
        identities: set[int] = set()
        for point in tail:
            if owned:
                # 固化块以后可能与新尾部共享同一根点；首次写入便建立引用，
                # 否则后来出现的别名会与旧块中内嵌的根对象分裂。
                self.evidence.publish(point)
                if self.evidence.reference(point) is None:
                    return None
            if id(point) in identities:
                self.evidence.publish(point)
                if self.evidence.reference(point) is None:
                    return None
            identities.add(id(point))
            for key, value in point.items():
                for child in (key, value):
                    if type(child) in (dict, list, tuple, str):
                        self.evidence.publish(child)
                        if self.evidence.reference(child) is None:
                            return None
        raw_keys = tuple(owned_signature(point) for point in tail)
        if any(key is None for key in raw_keys):
            return None
        keys = previous[0].copy() if previous is not None else FrozenPrefixList[bytes]()
        del keys[keep:]
        keys.extend(cast(tuple[bytes, ...], raw_keys))
        keys.freeze(len(keys))
        costs = self._key_costs[channel].copy() if previous is not None else FrozenPrefixList([0])
        del costs[keep + 1:]
        for key in cast(tuple[bytes, ...], raw_keys):
            costs.append(costs[-1] + getsizeof(key))
        costs.freeze(len(costs))
        # 保留当前只读数组的引用也属于通道预算。
        charged = costs[-1] + keys.storage_bytes + costs.storage_bytes + len(points) * 16 + 512
        if self._used - (previous[2] if previous is not None else 0) + charged > self._limit:
            return None
        if previous is not None and not owned:
            while keep < min(len(previous[0]), len(keys)) and previous[0][keep] == keys[keep]:
                keep += 1
        root = previous[1].root if previous is not None else None
        capacity = previous[1].capacity if previous is not None else 1
        # 截短同样重写最后一个不完整块；旧根内部可有更长尾部，但长度永远封住它。
        first = keep // _BLOCK
        count = (len(points) + _BLOCK - 1) // _BLOCK
        if previous is not None and keep == len(keys) == len(previous[0]):
            first = count
        for position in range(first, count):
            while position >= capacity:
                root = self._write(('branch', root, None))
                capacity *= 2
            payload = self.evidence.dumps(list(points[position * _BLOCK:(position + 1) * _BLOCK]))
            leaf = self._write(('leaf', payload, None))
            root = self._replace(root, capacity, position, leaf)
        result = PreparedPointReference(self.owner, root, capacity, len(points))
        self._used += charged - (previous[2] if previous is not None else 0)
        self._current[channel] = keys, result, charged
        self._key_costs[channel] = costs
        if owned:
            self._owned_points[channel] = points
        else:
            self._owned_points.pop(channel, None)
        return result

    def unpack(self, reference: PreparedPointReference, *, references: dict[int, object] | None = None,
               copies: dict[int, object] | None = None) -> list[dict[str, object]]:
        if (reference.owner != self.owner or type(reference.length) is not int or reference.length < 0
                or type(reference.capacity) is not int or reference.capacity < 1
                or reference.capacity & (reference.capacity - 1)):
            raise ValueError('point array reference must belong to its history')
        result: list[dict[str, object]] = []
        shared_references = references if references is not None else {}
        shared_copies = copies if copies is not None else {}
        pending = [(reference.root, reference.capacity, 0)]
        while pending and len(result) < reference.length:
            root, capacity, start = pending.pop()
            if start * _BLOCK >= reference.length:
                continue
            if root is None:
                raise ValueError('point history is missing a visible block')
            kind, left, right = self._get(root)
            if capacity == 1:
                if kind != 'leaf' or type(left) is not bytes:
                    raise ValueError('point history requires a complete leaf payload')
                restored = self.evidence.loads(left, references=shared_references, copies=shared_copies)
                if type(restored) is not list or any(type(point) is not dict for point in restored):
                    raise ValueError('point history leaf requires ordinary point mappings')
                result.extend(cast(list[dict[str, object]], restored))
            else:
                if kind != 'branch':
                    raise ValueError('point history requires an index branch')
                half = capacity // 2
                pending.append((cast(int | None, right), half, start + half))
                pending.append((cast(int | None, left), half, start))
        if len(result) < reference.length:
            raise ValueError('point history has an incomplete visible prefix')
        return result[:reference.length]

    def close(self) -> None:
        self._current.clear()
        self._key_costs.clear()
        self._owned_points.clear()
        self._cache.clear()
        self._cache_bytes = self._used = 0
        self._rows.close()
