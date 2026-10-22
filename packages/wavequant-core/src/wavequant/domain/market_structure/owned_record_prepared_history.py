"""已认证点版本的历史行根；只编码当天外壳，不再扫描旧证明。"""

from collections.abc import Callable
from dataclasses import dataclass
from math import isfinite
from struct import Struct
from sys import getsizeof
from typing import cast

from .evidence_record_sequence import EvidenceSequence, domain_sequence, validate_evidence_sequence
from .owned_evidence import CertifiedSource
from .owned_evidence_producer import PointRecord
from .owned_evidence_records import OwnedEvidenceRecords, RecordRef
from .trend_point_version import PointVersion


_MAGIC = b'WQ-owned-record-prepared-1\x00'
_ROOT = Struct('>16sQ')
_FIELDS = ('source_strokes', 'candidate_strokes', 'structure_strokes', 'strokes', 'developing_strokes')
_COLUMNS = ('points', 'structural_legs')
_SCALARS = (type(None), bool, int, float, str, bytes)
_PointLookup = Callable[[object], PointRecord | None]


class _UnrecordedPrepared(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class RecordedPreparedPath:
    """闭合路径只保存磁盘根及外壳位置，没有历史点或祖先版本强引用。"""

    payload: bytes
    arrays: tuple[tuple[str, int, str, EvidenceSequence], ...]
    points: tuple[tuple[str, int, str, RecordRef], ...]
    read_domain: RecordRef | None = None

    @property
    def metadata_bytes(self) -> int:
        # 每个位置包含字符串/整数、tuple、sequence及其两个RecordRef的
        # 保守上界；重复引用也重复计费，避免依赖跨模板身份表去重。
        return (getsizeof(self) + getsizeof(self.payload) + getsizeof(self.arrays) + getsizeof(self.points) + 512
                + len(self.arrays) * 2048 + len(self.points) * 1024
                + sum(getsizeof(field) + getsizeof(index) + getsizeof(name)
                      for field, index, name, _ in (*self.arrays, *self.points)))


class OwnedRecordPreparedHistory:
    """借用请求记录存储，历史行只持有一个已提交的磁盘根。

    非空点数组必须在生产端封成完整 EvidenceSequence。该入口既不从
    普通列表补建版本，也不迭代 point_records；未知证明直接拒绝，不能
    把保存端的全图重新编码称为增量。point_lookup 只能查已有载体。

    整行一次 materialize 共享读取 memo；不同读取的可变结果相互隔离。
    没有历史对象身份表，也不依赖旧证据池或 PreparedGraphHistory 索引。
    """

    def __init__(self, store: OwnedEvidenceRecords, *, memory_bytes: int = 8 * 1024 * 1024) -> None:
        if type(memory_bytes) is not int or memory_bytes < 0:
            raise ValueError('prepared record memory_bytes must be nonnegative')
        self.store = store
        self.memory_bytes = memory_bytes
        self.last_rejection: str | None = None
        self._closed = False

    def _require_open(self) -> None:
        if self._closed:
            raise ValueError('owned record prepared history is closed')
        self.store._require_open()

    def _prepare(self, prepared: object, point_lookup: _PointLookup | None) -> tuple[
        dict[int, tuple[object, EvidenceSequence]], dict[int, tuple[object, RecordRef]],
    ]:
        if type(prepared) is not dict:
            raise _UnrecordedPrepared('prepared_root_not_builtin')
        row = cast(dict[object, object], prepared)
        if any(type(key) is not str for key in row) or any(field not in row for field in _FIELDS):
            raise _UnrecordedPrepared('prepared_root_shape')
        sequences: dict[int, tuple[object, EvidenceSequence]] = {}
        references: dict[int, tuple[object, RecordRef]] = {}
        charged: set[int] = set()
        memory = 0

        def charge(value: object, children: int = 0) -> None:
            nonlocal memory
            if id(value) in charged:
                return
            charged.add(id(value))
            memory += 1536 + 4 * getsizeof(value) + 128 * children
            if memory > self.memory_bytes:
                raise _UnrecordedPrepared('prepared_shell_memory_limit')

        def scalar(value: object) -> None:
            if type(value) not in _SCALARS or type(value) is float and not isfinite(cast(float, value)):
                raise _UnrecordedPrepared('prepared_metadata_not_scalar')
            charge(value)

        def metadata(value: object, *, flat_mapping: bool = False) -> None:
            kind = type(value)
            if kind in _SCALARS:
                scalar(value)
            elif kind in (list, tuple):
                children = cast(list[object] | tuple[object, ...], value)
                charge(value, len(children))
                for child in children:
                    scalar(child)
            elif kind is dict and flat_mapping:
                mapping = cast(dict[object, object], value)
                charge(value, len(mapping) * 2)
                for key, child in mapping.items():
                    if type(key) is not str:
                        raise _UnrecordedPrepared('prepared_metadata_key')
                    scalar(key)
                    scalar(child)
            elif kind is dict:
                # 例如 developing.confirmed_anchor：必须由生产者给出其独立
                # 外壳引用，不能扫描继承证明，也不能猜它等同于数组中的点。
                record = point_lookup(value) if point_lookup is not None else None
                if (type(record) is not PointRecord or record.owner is not self.store
                        or not record.matches(value)):
                    raise _UnrecordedPrepared('prepared_point_without_record')
                self.store._validate_ref(record.reference)
                references[id(value)] = value, record.reference
            else:
                raise _UnrecordedPrepared('prepared_metadata_unknown_type')

        def point_array(value: object) -> None:
            if type(value) is list and not value:
                charge(value)
                return
            if type(value) not in (PointVersion, CertifiedSource):
                raise _UnrecordedPrepared('prepared_points_without_version')
            if id(value) in sequences:
                return
            version = cast(PointVersion, value)
            sequence = version.evidence_sequence
            if version.record_store is not self.store or sequence is None:
                raise _UnrecordedPrepared('prepared_points_without_complete_root')
            if type(value) is CertifiedSource and cast(CertifiedSource, value).owner.record_store() is not self.store:
                raise _UnrecordedPrepared('prepared_source_foreign_owner')
            validate_evidence_sequence(self.store, sequence)
            if sequence.length != len(version):
                raise ValueError('prepared point sequence metadata does not match its recorded root')
            sequences[id(value)] = value, sequence

        charge(row, len(row) * 2)
        seen_rows: set[int] = set()
        for key, value in row.items():
            scalar(key)
            if key not in _FIELDS:
                metadata(value, flat_mapping=key == '_path_market_end_indices')
                continue
            if type(value) is not list:
                raise _UnrecordedPrepared('prepared_strokes_not_builtin')
            rows = cast(list[object], value)
            charge(value, len(rows))
            for stroke in rows:
                if type(stroke) is not dict:
                    raise _UnrecordedPrepared('prepared_stroke_not_builtin')
                if id(stroke) in seen_rows:
                    continue
                seen_rows.add(id(stroke))
                fields = cast(dict[object, object], stroke)
                if 'points' not in fields:
                    raise _UnrecordedPrepared('prepared_stroke_missing_points')
                charge(stroke, len(fields) * 2)
                for name, child in fields.items():
                    if type(name) is not str:
                        raise _UnrecordedPrepared('prepared_stroke_key')
                    scalar(name)
                    if name in _COLUMNS:
                        point_array(child)
                    else:
                        metadata(child)
        return sequences, references

    def capture(self, prepared: object, *, point_lookup: _PointLookup | None = None) -> bytes | None:
        """普通/未认证输入返回 None；I/O、损坏引用等真实错误直接传播。

        所有来源门禁先于写入。成功仅写固定大小序列外壳和当天新外壳图；
        失败不发布行根。这里没有基于容量改用完整 pickle 的后备路径。
        """
        self._require_open()
        self.last_rejection = None
        try:
            sequences, references = self._prepare(prepared, point_lookup)
        except _UnrecordedPrepared as error:
            self.last_rejection = str(error)
            return None
        # 两个不同数组可能共享同一持久树及原vector根，但公开读取时仍应
        # 是两个独立list。每个实际数组新写一个O(1)外壳，点引用保持共享。
        memo: dict[int, object] = {}

        def shell(value: object) -> object:
            reference = references.get(id(value))
            if reference is not None and reference[0] is value:
                return reference[1]
            kind = type(value)
            if kind in _SCALARS:
                return value
            if id(value) in memo:
                return memo[id(value)]
            if kind is dict:
                mapping = {key: shell(child) for key, child in cast(dict[object, object], value).items()}
                memo[id(value)] = mapping
                return mapping
            if kind is list:
                values = [shell(child) for child in cast(list[object], value)]
                memo[id(value)] = values
                return values
            if kind is tuple:
                items = tuple(shell(child) for child in cast(tuple[object, ...], value))
                memo[id(value)] = items
                return items
            raise ValueError('prepared shell changed after admission')

        try:
            for identity, (value, sequence) in sequences.items():
                reference = self.store._write_vector(sequence.root, sequence.capacity, sequence.length)
                references[identity] = value, reference
            root = self.store.capture_new(shell(prepared), memory_bytes=self.memory_bytes)
            return _MAGIC + _ROOT.pack(root.namespace, root.offset)
        finally:
            # 不保留当前行对象索引，更不保存祖先版本；磁盘引用由payload拥有。
            memo.clear()
            references.clear()
            sequences.clear()

    @staticmethod
    def is_payload(payload: object) -> bool:
        return type(payload) is bytes and cast(bytes, payload).startswith(_MAGIC)

    def _payload_reference(self, payload: bytes) -> RecordRef:
        self._require_open()
        if not self.is_payload(payload) or len(payload) != len(_MAGIC) + _ROOT.size:
            raise ValueError('invalid owned record prepared payload')
        namespace, offset = _ROOT.unpack(payload[len(_MAGIC):])
        reference = RecordRef(namespace, offset)
        self.store._validate_ref(reference)
        return reference

    def restore(self, payload: bytes) -> dict[str, object]:
        result = self.store.materialize(self._payload_reference(payload))
        if type(result) is not dict or any(type(key) is not str for key in cast(dict[object, object], result)):
            raise ValueError('owned record prepared payload must restore a dictionary')
        return cast(dict[str, object], result)

    def capture_path(self, source: dict[str, object], candidate: dict[str, object],
                     stroke: dict[str, object] | None, developing: dict[str, object] | None, *,
                     point_lookup: _PointLookup | None = None) -> RecordedPreparedPath | None:
        """只在生产端完成封存后调用；四外壳共用同一行根和读取域。"""
        candidates = [candidate]
        row: dict[str, object] = dict(source_strokes=[source], candidate_strokes=candidates,
            structure_strokes=candidates, strokes=[] if stroke is None else [stroke],
            developing_strokes=[] if developing is None else [developing])
        payload = self.capture(row, point_lookup=point_lookup)
        if payload is None:
            return None
        arrays: list[tuple[str, int, str, EvidenceSequence]] = []
        points: list[tuple[str, int, str, RecordRef]] = []
        for field in _FIELDS:
            for index, shell in enumerate(cast(list[dict[str, object]], row[field])):
                for name, value in shell.items():
                    if name in _COLUMNS and type(value) in (PointVersion, CertifiedSource):
                        sequence = cast(PointVersion, value).evidence_sequence
                        if sequence is None:
                            raise ValueError('recorded path changed after final admission')
                        arrays.append((field, index, name, sequence))
                    elif type(value) is dict:
                        record = point_lookup(value) if point_lookup is not None else None
                        if record is None or record.owner is not self.store or not record.matches(value):
                            raise ValueError('recorded path lost its explicit point carrier')
                        points.append((field, index, name, record.reference))
        return RecordedPreparedPath(payload, tuple(arrays), tuple(points))

    def restore_path(self, saved: RecordedPreparedPath) -> tuple[dict[str, object], RecordedPreparedPath]:
        """一次独立读取共用一个新域；跨数组、锚点的子引用沿用该读取域。

        新域只增加常量引用外壳，不重编码点或证明。借用缓存须连同返回的
        descriptor 保存该行；独立读取不得重新挂回原始的生产引用身份。
        """
        reference = self._payload_reference(saved.payload)
        domain = self.store.new_read_domain()
        root = self.store.domain_ref(reference, domain)
        sequences: dict[RecordRef, EvidenceSequence] = {}
        arrays: list[tuple[str, int, str, EvidenceSequence]] = []
        for field, index, name, sequence in saved.arrays:
            validate_evidence_sequence(self.store, sequence)
            scoped = sequences.get(sequence.reference)
            if scoped is None:
                scoped = domain_sequence(self.store, sequence, domain)
                sequences[sequence.reference] = scoped
            arrays.append((field, index, name, scoped))
        points = tuple((field, index, name, self.store.domain_ref(point, domain))
                       for field, index, name, point in saved.points)
        descriptor = RecordedPreparedPath(_MAGIC + _ROOT.pack(root.namespace, root.offset),
                                         tuple(arrays), points, domain)
        return self.restore(descriptor.payload), descriptor

    @staticmethod
    def _path_field(row: dict[str, object], field: str, index: int) -> dict[str, object]:
        rows = row.get(field)
        if type(rows) is not list or not 0 <= index < len(cast(list[object], rows)):
            raise ValueError('recorded path descriptor requires its saved stroke')
        result = cast(list[object], rows)[index]
        if type(result) is not dict:
            raise ValueError('recorded path descriptor requires a stroke dictionary')
        return cast(dict[str, object], result)

    def attach_path_versions(self, row: dict[str, object], saved: RecordedPreparedPath) -> int:
        """首次物化后重挂内部版本；返回原builtin图之外的保守常驻费用。"""
        self._require_open()
        if saved.read_domain is None:
            raise ValueError('recorded path versions require their independent read domain')
        versions: dict[int, tuple[list[dict[str, object]], PointVersion]] = {}
        charged = 0
        for field, index, name, sequence in saved.arrays:
            shell = self._path_field(row, field, index)
            value = shell.get(name)
            if type(value) is PointVersion:
                # 同一stroke在多个字段中出现，前一个位置已完成替换。
                version = cast(PointVersion, value)
                if version.record_store is not self.store or version.evidence_sequence != sequence:
                    raise ValueError('recorded path aliases require the same sequence root')
                continue
            if type(value) is not list or len(cast(list[object], value)) != sequence.length:
                raise ValueError('recorded path array must match its saved visible length')
            previous = versions.get(id(value))
            if previous is not None and previous[0] is value:
                version = previous[1]
                if version.evidence_sequence != sequence:
                    raise ValueError('recorded path array has conflicting sequence descriptors')
            else:
                values = cast(list[dict[str, object]], value)
                version = PointVersion(values).seal()
                version._record_store, version._record_sequence = self.store, sequence
                version._record_prefix = len(version)
                versions[id(value)] = values, version
                charged += (getsizeof(version) + version.storage_bytes + version.record_metadata_bytes
                            + getsizeof(version.token) + 1024)
            shell[name] = version
        return charged

    def path_point_records(self, row: dict[str, object], saved: RecordedPreparedPath) -> tuple[PointRecord, ...]:
        """只恢复少量独立外壳的carrier，不遍历版本中的点。"""
        if saved.read_domain is None:
            raise ValueError('recorded path point carriers require their independent read domain')
        result: list[PointRecord] = []
        seen: set[int] = set()
        for field, index, name, reference in saved.points:
            value = self._path_field(row, field, index).get(name)
            if type(value) is not dict:
                raise ValueError('recorded path point descriptor requires a dictionary')
            if id(value) in seen:
                continue
            seen.add(id(value))
            point = cast(dict[str, object], value)
            refs = self.store.dictionary_refs(reference)
            fields = tuple((key, pair[1]) for key, pair in zip(point, refs))
            result.append(PointRecord(point, reference, fields, self.store))
        return tuple(result)

    def close(self) -> None:
        """关闭适配器；记录流仍由请求 owner 统一关闭。"""
        self._closed = True
