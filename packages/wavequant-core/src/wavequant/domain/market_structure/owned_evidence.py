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
        self._closed = False

    def reference(self, value: object) -> int | None:
        entry = self._objects.get(id(value))
        return entry[1] if entry is not None and entry[0] is value else None

    @property
    def memory_used(self) -> int:
        return self._used

    @property
    def admission_remaining(self) -> int:
        return max(0, self._limit - self._used)

    def finite(self, value: object) -> bool:
        """等价于标准 JSON 的有限值准入，已拥有的节点不再遍历。"""
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
        """移交不可变导出模板；只编码此前没有移交的节点。"""
        if self._closed:
            raise ValueError('owned evidence is closed')
        if self._used >= self._limit or self.reference(value) is not None or not self.finite(value):
            return
        # 新根可能持有尚未准入的整个子树，不能只按外壳大小计费。
        pending_values = [value]
        seen: set[int] = set()
        charged = 0
        while pending_values:
            item = pending_values.pop()
            if self.reference(item) is not None or id(item) in seen:
                continue
            seen.add(id(item))
            charged += getsizeof(item) + 384
            if type(item) in (PointVersion, CertifiedSource):
                charged += cast(PointVersion, item).storage_bytes
            if type(item) is CertifiedSource:
                charged += item.signature_bytes + item.keys.storage_bytes + item._costs.storage_bytes
            if self._used + charged > self._limit:
                return
            if type(item) is dict:
                pending_values.extend(child for pair in cast(dict[object, object], item).items() for child in pair)
            elif type(item) in (list, tuple, PointVersion, CertifiedSource):
                pending_values.extend(cast(list[object] | tuple[object, ...], item))
        self._used += charged
        pending: list[tuple[object, bool]] = [(value, False)]
        while pending:
            item, leaving = pending.pop()
            kind = type(item)
            if self.reference(item) is not None or kind not in (dict, list, tuple, str, PointVersion, CertifiedSource):
                continue
            if not leaving:
                pending.append((item, True))
                if kind is dict:
                    pending.extend((child, False) for pair in cast(dict[object, object], item).items() for child in pair)
                elif kind in (list, tuple, PointVersion, CertifiedSource):
                    pending.extend((child, False) for child in cast(list[object] | tuple[object, ...], item))
                continue
            reference = len(self._nodes)
            self._nodes.append(item)
            self._objects[id(item)] = item, reference

    def close(self) -> None:
        self._objects.clear()
        self._nodes.clear()
        self._used = 0
        self._closed = True


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
