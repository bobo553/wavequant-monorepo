"""Request-local publication checkpoints for immutable, causal input versions."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from math import isfinite
from sys import getsizeof
from typing import cast
from weakref import ReferenceType, ref

from .owned_evidence import CertifiedSource, _UNINDEXED_SCALAR_TYPES, current_evidence
from .trend_frozen_state import FrozenPrefixList, _SequenceNode
from .trend_market_index import _MarketPrefixIndex
from .trend_point_version import PointVersion
from .trend_readonly_cache import _builtin_size


@dataclass(frozen=True, slots=True)
class _PublicationCheckpoint:
    offset: int
    horizon: int
    result: FrozenPrefixList[dict[str, object]]
    pressure: Mapping[str, object] | None
    pending_origin: Mapping[str, object] | None
    pending_down_origin: Mapping[str, object] | None
    last_low: Mapping[str, object] | None
    active: Mapping[str, object] | None
    extreme: Mapping[str, object] | None


@dataclass(frozen=True, slots=True)
class _PendingPublicationQuery:
    offset: int
    position: int
    key: Mapping[str, object] | None


@dataclass(frozen=True, slots=True)
class _PublicationFrame:
    checkpoint: _PublicationCheckpoint
    queries: tuple[_PendingPublicationQuery, ...]
    introduced: tuple[int, ...]
    charged: int


class _PublicationDependencies:
    """候选位置上的依赖最大值；修订在 O(log N) 找到最早受影响候选。"""

    def __init__(self) -> None:
        self.capacity = 1
        self.values: list[tuple[int, int, int]] = [(0, 0, -1)] * 2

    @staticmethod
    def _merge(left: tuple[int, int, int], right: tuple[int, int, int]) -> tuple[int, int, int]:
        return max(left[0], right[0]), max(left[1], right[1]), max(left[2], right[2])

    def set(self, offset: int, value: tuple[int, int, int]) -> None:
        if offset >= self.capacity:
            previous, capacity = self.values, self.capacity
            while offset >= self.capacity:
                self.capacity *= 2
            self.values = [(0, 0, -1)] * (2 * self.capacity)
            self.values[self.capacity:self.capacity + capacity] = previous[capacity:]
            for position in range(self.capacity - 1, 0, -1):
                self.values[position] = self._merge(self.values[position * 2], self.values[position * 2 + 1])
        position = self.capacity + offset
        self.values[position] = value
        while position > 1:
            position //= 2
            self.values[position] = self._merge(self.values[position * 2], self.values[position * 2 + 1])

    def horizon(self, offset: int, cutoff: int) -> None:
        source, candidates, old = self.values[self.capacity + offset]
        self.set(offset, (source, candidates, max(old, cutoff)))

    def first(self, source_common: int, candidate_common: int, changed_known: int) -> int | None:
        def changed(value: tuple[int, int, int]) -> bool:
            return value[0] > source_common or value[1] > candidate_common or value[2] >= changed_known

        if not changed(self.values[1]):
            return None
        position = 1
        while position < self.capacity:
            position *= 2
            if not changed(self.values[position]):
                position += 1
        return position - self.capacity

    @property
    def size(self) -> int:
        # 每个节点包括三元组、三个整数及列表槽；共享标量允许保守重复计费。
        return getsizeof(self) + getsizeof(vars(self)) + getsizeof(self.values) + len(self.values) * 192


def _publication_size(value: object, limit: int) -> int | None:
    """请求池已持有并计费的不可变证据只计引用，不再遍历或重复收费。"""
    owner = current_evidence()
    if owner is None:
        return _builtin_size(value, limit)
    pending = [value]
    seen: set[int] = set()
    owned_objects = owner._objects
    size = 0
    while pending:
        item = pending.pop()
        identity = id(item)
        if identity in seen:
            continue
        seen.add(identity)
        owned = owned_objects.get(identity) if type(item) not in _UNINDEXED_SCALAR_TYPES else None
        if owned is not None and owned[0] is item:
            size += 8
        else:
            kind = type(item)
            if kind not in (dict, list, tuple, str, bytes, int, float, bool, type(None)):
                return None
            size += getsizeof(item)
            if kind is dict:
                mapping = cast(dict[object, object], item)
                pending.extend(mapping.keys())
                pending.extend(mapping.values())
            elif kind in (list, tuple):
                pending.extend(cast(Sequence[object], item))
        if size > limit:
            return None
    return size


class _SequenceBudget:
    """只给不可变序列的新后缀计费；替换/截短同时撤销旧后缀费用。

    各记录独立计费形成保守上界，账本自身也计入预算。账本沿用持久前缀，
    不持有逐日版本链，不按每次保存重新遍历旧证据。
    """

    def __init__(self) -> None:
        self.costs = FrozenPrefixList[int]([0])

    @property
    def size(self) -> int:
        return self.costs[-1] + self.costs.storage_bytes + len(self.costs) * 32 + 256

    def update(self, values: FrozenPrefixList[dict[str, object]], start: int, limit: int) -> bool:
        del self.costs[start + 1:]
        for point in values.iter_range(start):
            size = _publication_size(point, limit - self.size)
            if size is None:
                return False
            self.costs.append(self.costs[-1] + size)
            if self.size > limit:
                return False
        self.costs.freeze(len(self.costs))
        return self.size <= limit


class _InputVersion:
    """借用输入版本，只拥有因果校验需要的标量索引。

    来源证明由趋势生产者拥有；发布缓存不能为比较版本而延长整棵旧证明
    的生命周期。弱引用失效时，直接子版本仍可用令牌定位修订；跨版本
    无共享树可用时从头校验标量，不能假定已丢失的前缀仍然相同。
    """

    def __init__(self) -> None:
        self.version: ReferenceType[PointVersion] | None = None
        self.token: object | None = None
        self.rows = FrozenPrefixList[tuple[int, int, int, int]]()

    @property
    def current(self) -> PointVersion | None:
        return self.version() if self.version is not None else None

    @property
    def size(self) -> int:
        return self.rows.storage_bytes + (self.rows[-1][3] if self.rows else 0) + 512

    def changed_from(self, version: PointVersion) -> int:
        if version.token is self.token:
            return len(version)
        if self.token is not None and version.parent is self.token:
            return version.changed_from
        return version.changed_since(self.current)

    def update(self, version: PointVersion, start: int, market: _MarketPrefixIndex,
               limit: int, *, candidates: bool) -> bool:
        del self.rows[start:]
        previous = self.rows[-1] if self.rows else None
        for point in version.iter_range(start):
            index, ordinal = point.get('index'), point.get('ordinal', 0)
            known = _PublicationCache._known(point, market)
            if (type(index) is not int or type(ordinal) is not int or known is None
                    or not 0 <= index <= known < len(market.bars)):
                return False
            order = index, ordinal
            if previous is not None and (order <= previous[:2]
                                         or not candidates and known < previous[2]):
                return False
            # 每项拥有的标量、累计费用整数和持久树容器均计入原有预算。
            cumulative = previous[3] if previous is not None else 0
            cumulative += getsizeof((index, ordinal, known, cumulative)) + sum(
                getsizeof(value) for value in (index, ordinal, known, cumulative)) + 32
            row = (index, ordinal, known, cumulative)
            self.rows.append(row)
            if self.size > limit:
                return False
            previous = row
        self.rows.freeze(len(self.rows))
        if self.size > limit:
            return False
        self.version, self.token = ref(version), version.token
        return True


class _PublicationCache:
    """按候选保存发布状态，并单独重查仍等待未来行情的资格。

    只保留当前候选序列的一组状态；尾部撤销同时释放其独占图节点和账本。
    首证书、真实失败及固定截止查询使用实际知识边界，未决 None 不冻结。
    """

    def __init__(self, *, memory_bytes: int = 8 * 1024 * 1024) -> None:
        self.limit = memory_bytes
        self.checkpoint: _PublicationCheckpoint | None = None
        self._output_token: object | None = None
        self._output_root: ReferenceType[FrozenPrefixList[dict[str, object]]] | None = None
        self._candidate_input = _InputVersion()
        self._source_input = _InputVersion()
        self._owner: object | None = None
        self._evidence_namespace: bytes | None = None
        self._level = -1
        self._end = -1
        self._input_bytes = 0
        self._checkpoint_bytes = 0
        self._frames: list[_PublicationFrame] = []
        self._dependencies = _PublicationDependencies()
        self._pending: dict[int, _PendingPublicationQuery] = {}
        self._seen: set[int] = set()
        self._blocked = False
        self._offset = 0
        self._reads = (0, 0, -1)
        self._queries: list[_PendingPublicationQuery] = []
        self.stats: dict[str, int] = {}

    def clear(self) -> None:
        self.checkpoint = None
        self._output_token = None
        self._output_root = None
        self._candidate_input = _InputVersion()
        self._source_input = _InputVersion()
        self._owner = None
        self._evidence_namespace = None
        self._level = self._end = -1
        self._input_bytes = 0
        self._checkpoint_bytes = 0
        self._frames = []
        self._dependencies = _PublicationDependencies()
        self._pending = {}
        self._seen = set()
        self._blocked = False
        self._queries = []

    def _count(self, name: str, amount: int = 1) -> None:
        self.stats[name] = self.stats.get(name, 0) + amount

    @property
    def memory_bytes(self) -> int:
        fields = vars(self)
        shallow = getsizeof(self) + getsizeof(fields)
        shallow += sum(getsizeof(name) + getsizeof(value) for name, value in fields.items())
        shallow += sum(getsizeof(name) + getsizeof(value) for name, value in self.stats.items())
        shallow += sum(getsizeof(value) for value in self._reads)
        return self._input_bytes + self._checkpoint_bytes + self._dependencies.size + shallow

    def _drop_from(self, offset: int) -> None:
        for position in range(len(self._frames) - 1, offset - 1, -1):
            frame = self._frames.pop()
            self._seen.difference_update(frame.introduced)
            self._checkpoint_bytes -= frame.charged
            for query in frame.queries:
                self._pending.pop(id(query), None)
            self._dependencies.set(position, (0, 0, -1))
        self.checkpoint = self._frames[-1].checkpoint if self._frames else None

    def _disable(self, reason: str) -> None:
        self._count(reason)
        self._frames = []
        self._pending = {}
        self._seen = set()
        self._queries = []
        self._dependencies = _PublicationDependencies()
        self.checkpoint = None
        self._checkpoint_bytes = 0
        self._candidate_input, self._source_input = _InputVersion(), _InputVersion()
        self._input_bytes = 0
        self._blocked = True

    @property
    def _candidates(self) -> PointVersion | None:
        return self._candidate_input.current

    @property
    def _source(self) -> PointVersion | None:
        return self._source_input.current

    @staticmethod
    def _version(points: Sequence[Mapping[str, object]]) -> PointVersion | None:
        if type(points) not in (PointVersion, CertifiedSource):
            return None
        version = cast(PointVersion, points)
        if not version._sealed:
            return None
        if type(points) is CertifiedSource and points.owner is not current_evidence():
            return None
        return version

    @staticmethod
    def _known(point: Mapping[str, object], market: _MarketPrefixIndex) -> int | None:
        value = point.get('available_at')
        if type(value) is int:
            return value
        if type(value) is str:
            positions = market.owner.positions.get(value)
            if positions is not None and len(positions) == 1:
                return positions[0]
        return None

    def _admit_version(
        self, version: PointVersion, prior: _InputVersion,
        market: _MarketPrefixIndex, *, candidates: bool,
    ) -> tuple[bool, int]:
        if version.token is prior.token:
            return True, prior.size
        if not prior.update(version, prior.changed_from(version), market, self.limit, candidates=candidates):
            return False, 0
        return True, prior.size

    def admit(
        self, candidates: Sequence[Mapping[str, object]], source: Sequence[Mapping[str, object]],
        market: _MarketPrefixIndex | None, source_level: int,
    ) -> bool:
        candidate_version, source_version = self._version(candidates), self._version(source)
        if (market is None or candidate_version is None or source_version is None
                or not market.owner.unique_sessions or not market.owner.exact_prices
                or not market.bars or self.limit <= 0):
            self.clear()
            return False
        end = len(market.bars) - 1
        evidence = current_evidence()
        namespace = evidence._namespace if evidence is not None else None
        if (self._owner is not market.owner or self._level != source_level or end < self._end
                or self._evidence_namespace != namespace):
            self.clear()
        self._owner, self._level, self._end = market.owner, source_level, end
        self._evidence_namespace = namespace
        if self._blocked:
            self._count('blocked_calls')
            return False
        candidate_common = self._candidate_input.changed_from(candidate_version)
        source_common = self._source_input.changed_from(source_version)
        # 完全未变的输入也满足“下一项不存在”的读取收据。真实追加只保留
        # old length 的共同前缀，必须重新检查曾经读到末尾的 lookahead。
        infinity = 2 ** 63
        candidate_bound = (infinity if candidate_common == len(candidate_version)
                           == len(self._candidate_input.rows) else candidate_common)
        source_bound = (infinity if source_common == len(source_version)
                        == len(self._source_input.rows) else source_common)
        changed_known = infinity
        if source_common < len(self._source_input.rows):
            changed_known = self._source_input.rows[source_common][2]
        if source_common < len(source_version):
            known = self._known(source_version[source_common], market)
            changed_known = min(changed_known, known if known is not None else -1)
        admitted, candidate_bytes = self._admit_version(
            candidate_version, self._candidate_input, market, candidates=True)
        source_admitted, source_bytes = self._admit_version(
            source_version, self._source_input, market, candidates=False)
        if not admitted or not source_admitted:
            # Do not partially revalidate a changed dependency against the old
            # checkpoint. The unchanged full publisher is the fallback.
            self._disable('input_rejections')
            return False
        self._input_bytes = candidate_bytes + source_bytes
        changed = self._dependencies.first(source_bound, candidate_bound, changed_known)
        if changed is not None:
            self._count('input_rewinds')
            self._count('invalidated_candidates', len(self._frames) - changed)
            self._drop_from(changed)
        if self.memory_bytes > self.limit:
            self._disable('budget_rejections')
            return False
        return True

    @staticmethod
    def rejected_forever(origin: Mapping[str, object], market: _MarketPrefixIndex) -> bool:
        """A missing ordinary proof is final only after the real origin broke."""
        return _PublicationCache.failed_at(origin, market) is not None

    @staticmethod
    def failed_at(origin: Mapping[str, object], market: _MarketPrefixIndex) -> int | None:
        index, price = origin.get('index'), origin.get('value')
        if (type(index) is not int or not 0 <= index < len(market.bars)
                or type(price) not in (int, float) or not isfinite(cast(float, price))
                or type(price) is int and abs(price) > 2 ** 53):
            return None
        return market.first_extreme(index + 1, len(market.bars), cast(float, price), high=False)

    def resume(self, source: Sequence[Mapping[str, object]], market: _MarketPrefixIndex,
               qualify: Callable[[int, Mapping[str, object] | None], Mapping[str, object] | None]) -> None:
        """所有未决查询都重查；只出现真实结论变化时撤销其后的发布状态。"""
        changed: int | None = None
        for query in tuple(self._pending.values()):
            if changed is not None and query.offset >= changed:
                continue
            self._count('pending_rechecks')
            proof = qualify(query.position, query.key)
            if proof is not None:
                changed = query.offset if changed is None else min(changed, query.offset)
                continue
            failed = self.failed_at(source[query.position], market)
            if failed is not None:
                self._dependencies.horizon(query.offset, failed)
                self._pending.pop(id(query), None)
                self._count('final_rejections')
        if changed is not None:
            self._count('qualification_rewinds')
            self._drop_from(changed)
        self._count('reused_candidates', len(self._frames))

    def begin(self, offset: int) -> None:
        self._offset = offset
        self._reads = (0, offset + 1, -1)
        self._queries = []

    def source_read(self, position: int) -> None:
        self._reads = (max(self._reads[0], position + 1), self._reads[1], self._reads[2])

    def candidate_read(self, position: int) -> None:
        self._reads = (self._reads[0], max(self._reads[1], position + 1), self._reads[2])

    def fixed_query(self, cutoff: int) -> None:
        self._reads = (self._reads[0], self._reads[1], max(self._reads[2], cutoff))

    def lookup(self, reference: Mapping[str, object], *, candidates: bool) -> None:
        rows = self._candidate_input.rows if candidates else self._source_input.rows
        index, ordinal = reference.get('index'), reference.get('ordinal', 0)
        if type(index) is not int or type(ordinal) is not int:
            # 非坐标查找可能调用通用相等语义；它读取整个集合及缺失末端。
            position = len(rows)
        else:
            order = index, ordinal
            left, right = 0, len(rows)
            while left < right:
                middle = (left + right) // 2
                if rows[middle][:2] < order:
                    left = middle + 1
                else:
                    right = middle
            position = left
        if candidates:
            self.candidate_read(position)
        else:
            self.source_read(position)

    def query(self, position: int, key: Mapping[str, object] | None,
              proof: Mapping[str, object] | None, source: Sequence[Mapping[str, object]],
              market: _MarketPrefixIndex) -> None:
        if self._blocked:
            return
        self.source_read(position)
        if proof is not None:
            trigger = proof.get('confirmed_by')
            cutoff = trigger.get('index') if isinstance(trigger, Mapping) else None
            if type(cutoff) is not int:
                self._disable('unsupported_receipts')
                return
            self.fixed_query(cutoff)
            return
        failed = self.failed_at(source[position], market)
        if failed is not None:
            self.fixed_query(failed)
            return
        self._queries.append(_PendingPublicationQuery(self._offset, position, key))
        if self.memory_bytes + len(self._queries) * 256 > self.limit:
            self._disable('budget_rejections')

    def _new_graph(self, roots: Sequence[object], limit: int) -> tuple[tuple[int, ...], int] | str | None:
        pending = list(roots)
        introduced: set[int] = set()
        size = 0
        owner = current_evidence()
        owned_objects = owner._objects if owner is not None else None
        while pending:
            value = pending.pop()
            identity = id(value)
            if identity in self._seen or identity in introduced:
                continue
            introduced.add(identity)
            kind = type(value)
            if kind not in (dict, tuple, list, FrozenPrefixList, _SequenceNode,
                            _PendingPublicationQuery, str, bytes, int, float, bool, type(None)):
                # 未知对象尚未经过内部冻结准入，拒绝时也不能调用其 __sizeof__。
                return 'unknown'
            # 同一个 ID 整数由 seen 与首次引入元组共享；集合真实容量另计，
            # 元组槽计8字节，其固定外壳由每帧预留覆盖。
            size += getsizeof(value) + getsizeof(identity) + 8
            owned = (owned_objects.get(identity) if owned_objects is not None
                     and kind not in _UNINDEXED_SCALAR_TYPES else None)
            if owned is not None and owned[0] is value:
                pass
            elif kind is dict:
                pending.extend(cast(dict[object, object], value).keys())
                pending.extend(cast(dict[object, object], value).values())
            elif kind in (tuple, list):
                pending.extend(cast(Sequence[object], value))
            elif kind is FrozenPrefixList:
                fields = vars(value)
                size += getsizeof(fields)
                for name, child in fields.items():
                    pending.append(name)
                    if name == '__orig_class__':
                        size += getsizeof(child)
                    else:
                        pending.append(child)
            elif kind is _SequenceNode:
                node = cast(_SequenceNode, value)
                pending.extend((node.values, node.left, node.right, node.size))
            elif kind is _PendingPublicationQuery:
                query = cast(_PendingPublicationQuery, value)
                # pending 字典以另一次 id(query) 创建自己的整数键。
                size += getsizeof(identity)
                pending.extend((query.offset, query.position, query.key))
            if size > limit:
                return None
        return tuple(introduced), size

    def save(self, checkpoint: _PublicationCheckpoint | None) -> None:
        if self._blocked or checkpoint is None or checkpoint is self.checkpoint:
            return
        if checkpoint.offset != len(self._frames) + 1:
            self._disable('layout_rejections')
            return
        queries = tuple(self._queries)
        roots = (checkpoint.result, checkpoint.pressure, checkpoint.pending_origin,
                 checkpoint.pending_down_origin, checkpoint.last_low, checkpoint.active,
                 checkpoint.extreme, queries)
        added = self._new_graph(roots, self.limit - self.memory_bytes - 2048)
        if added is None or isinstance(added, str):
            if isinstance(added, str):
                names = [name for name in self.stats if name.startswith('unsupported_type:')]
                self._count('unsupported_type:' + (added if len(names) < 16 else 'other'))
            self._disable('budget_rejections' if added is None else 'unsupported_graph_rejections')
            return
        introduced, size = added
        frame = _PublicationFrame(checkpoint, queries, introduced, size + 1024)
        self._frames.append(frame)
        self._dependencies.set(self._offset, self._reads)
        self._seen.update(introduced)
        self._checkpoint_bytes += frame.charged
        for query in queries:
            self._pending[id(query)] = query
        self.checkpoint = checkpoint
        self._queries = []
        if self.memory_bytes > self.limit:
            self._disable('budget_rejections')
        else:
            self._count('captures')
            self._count('admitted_bytes', frame.charged)
