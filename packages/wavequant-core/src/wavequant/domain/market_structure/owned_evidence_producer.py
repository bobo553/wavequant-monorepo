"""生产时同步保存证据引用，原生点字典不携带内部协议字段。"""

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from sys import getsizeof
from typing import cast

from .owned_evidence_records import OwnedEvidenceRecords, RecordRef, Scalar


@dataclass(frozen=True, slots=True, eq=False)
class PointRecord:
    """当前点的并行载体；字段子图由内部生产者承诺冻结，不反向扫描历史图。"""

    value: dict[str, object]
    reference: RecordRef
    fields: tuple[tuple[str, RecordRef], ...]
    owner: OwnedEvidenceRecords
    _values: tuple[object, ...] = field(init=False, repr=False)
    _keys: tuple[RecordRef, ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if type(self.value) is not dict or any(type(key) is not str for key in self.value):
            raise TypeError('point records require an exact dictionary with string keys')
        refs = self.owner.dictionary_refs(self.reference)
        if len(refs) != len(self.fields) or len(refs) != len(self.value):
            raise ValueError('point record fields must match its dictionary root')
        for (key, _), (name, ref), (_, stored) in zip(self.value.items(), self.fields, refs):
            if key is not name or ref != stored and not self.owner.same_reference(ref, stored):
                raise ValueError('point record fields must retain their original keys and references')
        object.__setattr__(self, '_values', tuple(self.value.values()))
        object.__setattr__(self, '_keys', tuple(key for key, _ in refs))

    @classmethod
    def _from_producer(cls, value: dict[str, object], reference: RecordRef,
                       refs: tuple[tuple[RecordRef, RecordRef], ...],
                       owner: OwnedEvidenceRecords) -> 'PointRecord':
        """移交刚写完或从同一根解析的字段引用；不把私有收据重新读回验证。

        调用方必须已经完成该根事务，或刚通过 dictionary_refs 解析该根。
        外部传入字段仍走公开构造器，不能用此入口认证任意 RecordRef。
        """
        if type(value) is not dict or any(type(key) is not str for key in value):
            raise TypeError('point records require an exact dictionary with string keys')
        if len(refs) != len(value):
            raise ValueError('point record fields must match its dictionary root')
        result = object.__new__(cls)
        object.__setattr__(result, 'value', value)
        object.__setattr__(result, 'reference', reference)
        object.__setattr__(result, 'fields', tuple((key, pair[1]) for key, pair in zip(value, refs)))
        object.__setattr__(result, 'owner', owner)
        object.__setattr__(result, '_values', tuple(value.values()))
        object.__setattr__(result, '_keys', tuple(key for key, _ in refs))
        return result

    def matches(self, point: object) -> bool:
        """最终浅标注可能改字段；仅同一外壳及同序字段身份可沿用整个根。"""
        return (point is self.value and len(self.value) == len(self.fields)
                and all(key is name and value is original
                        for (key, value), (name, _), original
                        in zip(self.value.items(), self.fields, self._values)))

    def field_reference(self, name: str) -> RecordRef | None:
        return next((reference for key, reference in self.fields if key == name), None)


class ProducerRecordScope:
    """一次核心调用的显式身份桥；退出释放全部对象索引，不跨日保留身份表。

    inputs 只能来自同 owner 已冻结的内部结果。只登记点和直接字段根；
    新证明允许扫描一次新 builtin 图，遇到已有字段引用立即停止。业务代码
    不得原地改已登记的字段子图；点外壳更新后必须重新 record_point。
    """

    def __init__(self, owner: OwnedEvidenceRecords, *, memory_bytes: int = 8 * 1024 * 1024) -> None:
        if type(memory_bytes) is not int or memory_bytes < 0:
            raise ValueError('producer graph memory_bytes must be nonnegative')
        self.owner = owner
        self.memory_bytes = memory_bytes
        self._points: dict[int, PointRecord] = {}
        self._bindings: dict[int, tuple[object, RecordRef]] = {}
        self._binding_bytes: dict[int, int] = {}
        self._clone_bytes: dict[int, int] = {}
        self._outputs: dict[int, None] = {}
        self.stats = dict(record_point_requests=0, ensure_record_requests=0, ensure_value_requests=0,
                          captures=0, new_nodes=0, known_root_reuses=0, field_decodes=0,
                          capture_node_visits=0, container_expansions=0, known_node_skips=0)
        # 计数也占原作用域额度，不建立第二份业务对象索引或扩大预算。
        self.memory_used = 2048 + getsizeof(self.stats) + sum(
            getsizeof(key) + getsizeof(value) + 32 for key, value in self.stats.items())
        self.peak_bytes = self.memory_used
        self._closed = False

    def _require_open(self) -> None:
        if self._closed:
            raise ValueError('producer record scope is closed')

    @property
    def outputs(self) -> tuple[PointRecord, ...]:
        return tuple(record for identity in self._outputs
                     if (record := self._points.get(identity)) is not None and record.matches(record.value))

    def tracks(self, point: object) -> bool:
        record = self._points.get(id(point))
        return record is not None and record.value is point

    def lookup(self, point: object) -> PointRecord | None:
        self._require_open()
        record = self._points.get(id(point))
        return record if record is not None and record.matches(point) else None

    def bind(self, record: PointRecord) -> None:
        self._require_open()
        if type(record) is not PointRecord or record.owner is not self.owner or not record.matches(record.value):
            raise ValueError('producer input must be an unchanged point from this record owner')
        bindings = {id(record.value): (record.value, record.reference)}
        for (key, reference), key_ref, value in zip(record.fields, record._keys, record._values):
            if id(key) not in self._bindings:
                bindings.setdefault(id(key), (key, key_ref))
            if id(value) not in self._bindings:
                bindings.setdefault(id(value), (value, reference))
        self._commit(bindings, record=record)

    @staticmethod
    def _record_bytes(record: PointRecord) -> int:
        # value 图属于当前生产者；这里只计并行载体及各字段引用元数据。
        return (getsizeof(record) + getsizeof(record.fields) + getsizeof(record._keys)
                + getsizeof(record._values) + 512 + len(record.fields) * 256)

    def _commit(self, bindings: dict[int, tuple[object, RecordRef]], *,
                record: PointRecord | None = None, output: bool = False) -> None:
        # 固定每项余量覆盖两个身份表/输出集合的槽、ID、元组和RecordRef。
        # 所有bind入口共享本次调用预算，不把每个_capture的局部上限当总上限。
        charges: dict[int, int] = {}
        for identity, (value, _) in bindings.items():
            if type(value) not in (dict, list, tuple, type(None), bool, int, float, str, bytes):
                raise TypeError('producer bindings require exact builtin values')
            charges[identity] = 768 + getsizeof(value)
        added = sum(size - self._binding_bytes.get(identity, 0) for identity, size in charges.items())
        if record is not None:
            previous = self._points.get(id(record.value))
            added += self._record_bytes(record) - (self._record_bytes(previous) if previous is not None else 0)
        if self.memory_used + added > self.memory_bytes:
            raise ValueError('producer record bindings exceed their scoped memory limit')
        self._bindings.update(bindings)
        self._binding_bytes.update(charges)
        if record is not None:
            self._points[id(record.value)] = record
            if output:
                self._outputs.pop(id(record.value), None)
                self._outputs[id(record.value)] = None
        self.memory_used += added
        self.peak_bytes = max(self.peak_bytes, self.memory_used)

    def _bind_value(self, value: object, reference: RecordRef) -> None:
        previous = self._bindings.get(id(value))
        if previous is None or previous[0] is not value:
            self._commit({id(value): (value, reference)})

    def bind_clone(self, value: object, source: object, reference: RecordRef) -> None:
        """绑定一次实际 deepcopy 的结果；不可变值沿用原引用，容器进入新域。"""
        self._require_open()
        if reference.namespace != self.owner.namespace:
            raise ValueError('producer clone must belong to its record owner')
        added: dict[int, int] = {}
        if value is not source:
            # 只计本次真正 deepcopy 产生的工作图，不读取旧磁盘证明。根引用
            # 会保活整个新复制域，不能只收根字典费用而遗漏其嵌套容器。
            pending = [value]
            charged = 0
            while pending:
                child = pending.pop()
                identity = id(child)
                if identity in added or identity in self._clone_bytes or identity in self._bindings:
                    continue
                kind = type(child)
                if kind not in (dict, list, tuple, type(None), bool, int, float, str, bytes):
                    raise TypeError('producer clones require exact builtin values')
                size = 384 + getsizeof(child)
                added[identity] = size
                charged += size
                if self.memory_used + charged + 768 + getsizeof(value) > self.memory_bytes:
                    raise ValueError('producer clone graph exceeds its scoped memory limit')
                if kind is dict:
                    pending.extend(item for pair in cast(dict[object, object], child).items() for item in pair)
                elif kind in (list, tuple):
                    pending.extend(cast(list[object] | tuple[object, ...], child))
        clone = reference if value is source else self.owner.clone(reference)
        self._bind_value(value, clone)
        self._clone_bytes.update(added)
        self.memory_used += sum(added.values())
        self.peak_bytes = max(self.peak_bytes, self.memory_used)

    def _reference(self, value: object) -> RecordRef | None:
        record = self._points.get(id(value))
        if record is not None and record.value is value:
            return record.reference if record.matches(value) else None
        binding = self._bindings.get(id(value))
        return binding[1] if binding is not None and binding[0] is value else None

    def reference_of(self, value: object) -> RecordRef | None:
        """仅查询本作用域已有证明；可变点壳仍须匹配原字段身份。"""
        self._require_open()
        return self._reference(value)

    def ensure_value(self, value: object) -> RecordRef:
        """新字段只捕获一次；既有字段连接原引用，不先记录半成品点。"""
        self._require_open()
        self.stats['ensure_value_requests'] += 1
        known = self._reference(value)
        if known is not None:
            self.stats['known_root_reuses'] += 1
            return known
        tracked = self._points.get(id(value))
        if tracked is not None and tracked.value is value:
            # 已登记的可变点壳变更后，不能留下旧快照遮住刚写的新绑定。
            # 它原本就是严格点类型，仍沿同一字段身份校验更新其载体。
            return self.ensure_record(cast(dict[str, object], value)).reference
        reference, bindings, _ = self._capture(value)
        self._commit(bindings)
        return reference

    def _capture(self, root: object) -> tuple[
        RecordRef, dict[int, tuple[object, RecordRef]], tuple[tuple[RecordRef, RecordRef], ...] | None,
    ]:
        # memo 只覆盖本次新图。已有引用从不展开，因此缓存淘汰不触发旧图重写。
        known = self._reference(root)
        if known is not None:
            self.stats['known_root_reuses'] += 1
            return known, {}, None
        memo: dict[int, tuple[object, RecordRef]] = {}
        active: set[int] = set()
        pending: list[tuple[object, tuple[object, ...] | None]] = [(root, None)]
        charged = 0
        root_pairs: tuple[tuple[RecordRef, RecordRef], ...] | None = None
        self.stats['captures'] += 1

        def reference(item: object) -> RecordRef:
            entry = memo.get(id(item))
            if entry is not None and entry[0] is item:
                return entry[1]
            known = self._reference(item)
            if known is None:
                raise ValueError('producer graph contains an unfinished child')
            return known

        try:
            available = max(0, self.memory_bytes - self.memory_used)
            with self.owner._new_graph_writer(memory_bytes=min(66 * 1024, available // 4)) as writer:
                while pending:
                    value, children = pending.pop()
                    identity = id(value)
                    if identity in memo or self._reference(value) is not None:
                        self.stats['known_node_skips'] += 1
                        continue
                    kind = type(value)
                    if children is None:
                        if kind not in (dict, list, tuple, type(None), bool, int, float, str, bytes):
                            raise TypeError('producer evidence requires exact builtin values')
                        if identity in active:
                            raise ValueError('producer evidence cannot contain a cycle')
                        size = (len(cast(dict[object, object] | list[object] | tuple[object, ...], value))
                                if kind in (dict, list, tuple) else 0)
                        charged += 1536 + getsizeof(value) * 4 + size * (256 if kind is dict else 128)
                        if self.memory_used + charged + writer.memory_used > self.memory_bytes:
                            raise ValueError('new producer evidence exceeds its scoped memory limit')
                        self.stats['capture_node_visits'] += 1
                        if kind is dict:
                            mapping = cast(dict[object, object], value)
                            if any(type(key) not in (type(None), bool, int, float, str, bytes) for key in mapping):
                                raise TypeError('producer evidence keys require builtin scalars')
                            children = tuple(child for item in mapping.items() for child in item)
                            self.stats['container_expansions'] += 1
                        elif kind in (list, tuple):
                            children = tuple(cast(list[object] | tuple[object, ...], value))
                            self.stats['container_expansions'] += 1
                        else:
                            memo[identity] = value, writer.write_scalar(cast(Scalar, value))
                            continue
                        active.add(identity)
                        pending.append((value, children))
                        pending.extend((child, None) for child in reversed(children))
                    else:
                        references = [reference(child) for child in children]
                        if kind is dict:
                            pairs = tuple(zip(references[::2], references[1::2]))
                            stored = writer.write_dict(pairs)
                            if value is root:
                                root_pairs = pairs
                        elif kind is list:
                            stored = writer.write_list(references)
                        else:
                            stored = writer.write_tuple(references)
                        active.remove(identity)
                        memo[identity] = value, stored
                result = reference(root)
            # 退出写者事务之后才交出根和字段证明，异常不会发布未提交引用。
            self.stats['new_nodes'] += len(memo)
            return result, memo, root_pairs
        except BaseException:
            # 新图批次失败时存储回滚全部新增记录，本次身份桥也不能发布残根。
            memo.clear()
            raise
        finally:
            active.clear()
            pending.clear()

    def record_point(self, point: dict[str, object]) -> PointRecord:
        """兼容入口；已认证根与 ensure_record 共用实际复用路径。"""
        self.stats['record_point_requests'] += 1
        return self._ensure_record(point)

    def ensure_record(self, point: dict[str, object]) -> PointRecord:
        """返回仍有效的原载体；命中时不捕获、不写入、不解析字段引用。"""
        self.stats['ensure_record_requests'] += 1
        return self._ensure_record(point)

    def _ensure_record(self, point: dict[str, object]) -> PointRecord:
        self._require_open()
        if type(point) is not dict or any(type(key) is not str for key in point):
            raise TypeError('producer points require an exact dictionary with string keys')
        previous = self.lookup(point)
        if previous is not None:
            self.stats['known_root_reuses'] += 1
            return previous
        reference, new_bindings, refs = self._capture(point)
        if refs is None:
            # ensure_value/bind_clone 可以先登记裸 dict 根；首次需要点载体
            # 时只解析这一层。正常新点直接消费写者交回的引用，无读回。
            refs = self.owner.dictionary_refs(reference)
            self.stats['field_decodes'] += 1
        record = PointRecord._from_producer(point, reference, refs, self.owner)
        bindings = dict(new_bindings)
        bindings[id(point)] = point, reference
        for (key, field_ref), key_ref, value in zip(record.fields, record._keys, record._values):
            bindings.setdefault(id(key), (key, key_ref))
            bindings.setdefault(id(value), (value, field_ref))
        self._commit(bindings, record=record, output=True)
        return record

    def close(self) -> None:
        self._closed = True
        self._points.clear()
        self._bindings.clear()
        self._binding_bytes.clear()
        self._clone_bytes.clear()
        self._outputs.clear()
        self.memory_used = 0


_CURRENT: ContextVar[ProducerRecordScope | None] = ContextVar('owned_evidence_producer', default=None)


def current_producer_records() -> ProducerRecordScope | None:
    return _CURRENT.get()


@contextmanager
def producer_record_scope(owner: OwnedEvidenceRecords, *, inputs: Iterable[PointRecord] = (),
                          memory_bytes: int = 8 * 1024 * 1024) -> Iterator[ProducerRecordScope]:
    scope = ProducerRecordScope(owner, memory_bytes=memory_bytes)
    token = None
    try:
        for record in inputs:
            scope.bind(record)
        token = _CURRENT.set(scope)
        yield scope
    finally:
        if token is not None:
            _CURRENT.reset(token)
        scope.close()
