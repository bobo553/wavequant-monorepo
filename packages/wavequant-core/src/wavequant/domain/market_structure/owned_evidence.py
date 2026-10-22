"""请求拥有的只读证据图；历史行引用旧节点，公共读取独立恢复整图。"""

from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from io import BytesIO
from math import isfinite
import pickle
from sys import getsizeof
from typing import cast
from uuid import uuid4

from .trend_point_version import PointVersion
from .trend_frozen_state import FrozenPrefixList
from .owned_evidence_records import OwnedEvidenceRecords
from .finite_source_receipts import FiniteSourceReceipts


_MAGIC = b'WQ-owned-evidence-1\x00'
_current: ContextVar['OwnedEvidence | None'] = ContextVar('owned_trend_evidence', default=None)


class _EvidenceWriter(pickle.Pickler):
    def __init__(self, stream: BytesIO, owner: 'OwnedEvidence', *, root: object | None = None) -> None:
        super().__init__(stream, protocol=5)
        self.owner, self.root = owner, root

    def persistent_id(self, value: object) -> int | None:
        return None if value is self.root else self.owner.reference(value)

    def reducer_override(self, value: object) -> object:
        # 与普通签名入口一致，在未知对象的 __reduce__ 之前拒绝。
        from .trend_evidence_reuse import _UncacheableGeometry

        if value is list:
            return NotImplemented
        if type(value) in (PointVersion, CertifiedSource):
            return list, (), None, iter(value)
        raise _UncacheableGeometry


class _EvidenceReader(pickle.Unpickler):
    def __init__(self, payload: bytes, owner: 'OwnedEvidence', memo: dict[int, object], copies: dict[int, object]) -> None:
        super().__init__(BytesIO(payload))
        self.owner, self.references = owner, memo
        self.copies = copies

    def persistent_load(self, reference: object) -> object:
        if type(reference) is not int or not 0 <= reference < len(self.owner._nodes):
            raise ValueError('evidence reference must belong to its request')
        if reference not in self.references:
            # 每次公开读取独立复制；一次读取共用 memo 保留所有节点间的别名。
            self.references[reference] = deepcopy(self.owner._nodes[reference], self.copies)
        return self.references[reference]


class _SignatureWriter(_EvidenceWriter):
    """外壳按内容比较，字符串身份不影响来源版本；子树仍绑定原只读节点。"""

    def persistent_id(self, value: object) -> int | None:
        if type(value) is str:
            return None
        return super().persistent_id(value)


class OwnedEvidence:
    """只接收生产者不再修改的标准有限对象，不认证外部可变快照。

    对象表强引用已发布节点，防止 id 重用；预算不足时拒绝新节点，原有引用
    仍有效。历史行写在请求临时流中，池只保留预算内的共享节点，不保存每日
    完整快照。调用方必须
    关闭本对象，并且只能在外壳副本上标注，不能修改已移交的证据节点。
    """

    def __init__(self, *, memory_bytes: int = 24 * 1024 * 1024) -> None:
        if type(memory_bytes) is not int or memory_bytes < 0:
            raise ValueError('evidence memory_bytes must be a nonnegative integer')
        self._limit = memory_bytes
        self._namespace = uuid4().bytes
        self._used = 0
        self._objects: dict[int, tuple[object, int]] = {}
        self._nodes: list[object] = []
        self._records: OwnedEvidenceRecords | None = None
        self._record_cache_reserved = 0
        # 生产者到历史根的完整接线仍在隔离测量；旧调用默认不进入实验通路。
        self.record_history_enabled = False
        self._closed = False

    def record_store(self) -> OwnedEvidenceRecords:
        """实验通道从原池划拨读缓存和追加页，两个拥有者不重复使用预算。"""
        if self._closed:
            raise ValueError('owned evidence is closed')
        if self._records is None:
            reserved = min(1024 * 1024, self.admission_remaining) if self.record_history_enabled else 0
            records = OwnedEvidenceRecords(cache_bytes=reserved,
                page_bytes=64 * 1024 if reserved >= 256 * 1024 else 0)
            self._records, self._record_cache_reserved = records, reserved
        return self._records

    def reference(self, value: object) -> int | None:
        entry = self._objects.get(id(value))
        return entry[1] if entry is not None and entry[0] is value else None

    @property
    def memory_used(self) -> int:
        # 缓存可随读取重新填满，clear_cache只清当前内容，不释放已划拨额度。
        # 按整笔预留计费，避免旧池占用缓存暂时未使用的容量。
        return self._used + self._record_cache_reserved

    @property
    def admission_remaining(self) -> int:
        return max(0, self._limit - self.memory_used)

    def finite(self, value: object, *, _receipts: FiniteSourceReceipts | None = None) -> bool:
        """等价于标准 JSON 的有限值准入，已拥有的节点不再遍历。"""
        receipts = _receipts if type(_receipts) is FiniteSourceReceipts and _receipts.owner is self else None
        pending: list[tuple[object, bool]] = [(value, False)]
        active: set[int] = set()
        checked: set[int] = set()
        while pending:
            item, leaving = pending.pop()
            kind = type(item)
            if leaving:
                active.remove(id(item))
                checked.add(id(item))
                continue
            if self.reference(item) is not None or id(item) in checked:
                continue
            if (receipts is not None and kind in (dict, list, tuple, PointVersion, CertifiedSource)
                    and receipts.knows(item, self)):
                receipts.stats['root_hits' if item is value else 'subtree_skips'] += 1
                continue
            if kind in (str, int, bool, type(None)):
                continue
            if kind is float:
                if not isfinite(cast(float, item)):
                    return False
                continue
            if kind in (PointVersion, CertifiedSource) and not cast(PointVersion, item)._sealed:
                return False
            if kind not in (dict, list, tuple, PointVersion, CertifiedSource) or id(item) in active:
                return False
            active.add(id(item))
            pending.append((item, True))
            if kind is dict:
                mapping = cast(dict[object, object], item)
                if any(type(key) not in (str, int, float, bool, type(None))
                       or type(key) is float and not isfinite(key) for key in mapping):
                    return False
                pending.extend((child, False) for pair in mapping.items() for child in pair)
            else:
                pending.extend((child, False) for child in cast(list[object] | tuple[object, ...], item))
        return True

    def dumps(self, value: object, *, root: object | None = None,
              writer: Callable[[BytesIO], pickle.Pickler] | None = None) -> bytes:
        if self._closed:
            raise ValueError('owned evidence is closed')
        stream = BytesIO()
        encoder = writer(stream) if writer is not None else _EvidenceWriter(stream, self, root=root)
        encoder.dump(value)
        return _MAGIC + self._namespace + stream.getvalue()

    def loads(self, payload: bytes, *, references: dict[int, object] | None = None,
              copies: dict[int, object] | None = None) -> object:
        if not payload.startswith(_MAGIC):
            return pickle.loads(payload)
        if self._closed:
            raise ValueError('owned evidence is closed')
        header = len(_MAGIC) + len(self._namespace)
        if payload[len(_MAGIC):header] != self._namespace:
            raise ValueError('evidence payload must belong to its request')
        return _EvidenceReader(payload[header:], self,
            references if references is not None else {}, copies if copies is not None else {}).load()

    def publish(self, value: object) -> None:
        """单遍验证并计费新图；整图准入成功后才公开任何节点引用。"""
        if self._closed:
            raise ValueError('owned evidence is closed')
        if not self.admission_remaining or self.reference(value) is not None:
            return
        # 新根会持有完整子图，不能只收外壳费用。验证、计费、后序登记计划
        # 共用一次遍历；额度不足立即拒绝，避免拒绝前仍扫描整段历史证明。
        pending: list[tuple[object, bool]] = [(value, False)]
        active: set[int] = set()
        seen: set[int] = set()
        admitted: list[object] = []
        charged = 0
        while pending:
            item, leaving = pending.pop()
            identity = id(item)
            if leaving:
                active.remove(identity)
                seen.add(identity)
                admitted.append(item)
                continue
            if self.reference(item) is not None or identity in seen:
                continue
            kind = type(item)
            if kind is float and not isfinite(cast(float, item)):
                return
            if kind in (PointVersion, CertifiedSource) and not cast(PointVersion, item)._sealed:
                return
            if kind not in (dict, list, tuple, str, int, float, bool, type(None), PointVersion, CertifiedSource):
                return
            if identity in active:
                return
            # 只有进入对象表的节点才分配引用索引；数值/布尔/None 由其父容器
            # 持有，只计自身大小。给每个标量再收索引费用会提前耗尽共享池。
            indexed = kind in (dict, list, tuple, str, PointVersion, CertifiedSource)
            charged += getsizeof(item) + (384 if indexed else 0)
            if kind in (PointVersion, CertifiedSource):
                charged += cast(PointVersion, item).storage_bytes
            if kind is CertifiedSource:
                source = cast(CertifiedSource, item)
                charged += source.signature_bytes + source.keys.storage_bytes + source._costs.storage_bytes
                charged += len(source._costs) * getsizeof(source.signature_bytes)
            if charged > self.admission_remaining:
                return
            if kind in (str, int, float, bool, type(None)):
                seen.add(identity)
                if indexed:
                    admitted.append(item)
                continue
            active.add(identity)
            pending.append((item, True))
            if kind is dict:
                mapping = cast(dict[object, object], item)
                if any(type(key) not in (str, int, float, bool, type(None))
                       or type(key) is float and not isfinite(key) for key in mapping):
                    return
                pending.extend((child, False) for pair in mapping.items() for child in pair)
            else:
                pending.extend((child, False) for child in cast(Sequence[object], item))
        self._used += charged
        for item in admitted:
            reference = len(self._nodes)
            self._nodes.append(item)
            self._objects[id(item)] = item, reference

    def close(self) -> None:
        self._objects.clear()
        self._nodes.clear()
        self._used = 0
        self._closed = True
        try:
            if self._records is not None:
                self._records.close()
        finally:
            self._record_cache_reserved = 0


def current_evidence() -> OwnedEvidence | None:
    return _current.get()


class CertifiedSource(PointVersion):
    """请求内来源和逐点证书共用持久前缀，变化只追加到独立版本。"""

    def __init__(self, points: Sequence[dict[str, object]], keys: Sequence[bytes],
                 owner: OwnedEvidence, previous: 'CertifiedSource | None', *,
                 _changed_from: int | None = None) -> None:
        if len(points) != len(keys):
            raise ValueError('each source point requires one certificate')
        changed = _changed_from if _changed_from is not None and previous is not None else 0
        if previous is not None and previous.owner is not owner:
            previous, changed = None, 0
        if not 0 <= changed <= min(len(keys), len(previous.keys) if previous is not None else 0):
            raise ValueError('certified source delta must cover the changed suffix')
        if previous is not None and _changed_from is None:
            while changed < min(len(previous.keys), len(keys)) and previous.keys[changed] == keys[changed]:
                changed += 1
        self._initialize(points[changed:], keys[changed:], owner, previous, changed)

    def _initialize(self, points: Iterable[dict[str, object]], keys: Iterable[bytes],
                    owner: OwnedEvidence, previous: 'CertifiedSource | None', start: int) -> None:
        super().__init__(points, previous=previous, start=start)
        self.owner = owner
        self.keys = previous.keys.copy() if previous is not None else FrozenPrefixList[bytes]()
        self._costs = previous._costs.copy() if previous is not None else FrozenPrefixList([0])
        del self.keys[start:]
        del self._costs[start + 1:]
        for key in keys:
            self.keys.append(key)
            self._costs.append(self._costs[-1] + getsizeof(key))
        if len(self.keys) != len(self):
            raise ValueError('each source point requires one certificate')
        self.keys.freeze(len(self.keys))
        self._costs.freeze(len(self._costs))
        self.signature_bytes = self._costs[-1]
        self.seal()

    @classmethod
    def from_tail(cls, points: Iterable[dict[str, object]], keys: Iterable[bytes],
                  owner: OwnedEvidence, previous: 'CertifiedSource | None', start: int) -> 'CertifiedSource':
        if previous is not None and previous.owner is not owner:
            raise ValueError('a source prefix belongs to one evidence owner')
        result = cls.__new__(cls)
        result._initialize(points, keys, owner, previous, start)
        return result


@contextmanager
def owned_evidence_scope(*, memory_bytes: int = 24 * 1024 * 1024) -> Iterator[OwnedEvidence]:
    owner = OwnedEvidence(memory_bytes=memory_bytes)
    token = _current.set(owner)
    try:
        yield owner
    finally:
        _current.reset(token)
        owner.close()


def owned_signature(value: object) -> bytes | None:
    from .trend_evidence_reuse import _geometry_signature, _UncacheableGeometry

    owner = current_evidence()
    if owner is None:
        return _geometry_signature(value)
    if type(value) is CertifiedSource:
        if value.owner is not owner:
            return None
        value = list(value)
    try:
        return owner.dumps(value, writer=lambda stream: _SignatureWriter(stream, owner, root=value))
    except _UncacheableGeometry:
        return None


def finite_evidence(value: object) -> bool:
    import json

    owner = current_evidence()
    if owner is not None:
        return owner.finite(value)
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError):
        return False
    return True
