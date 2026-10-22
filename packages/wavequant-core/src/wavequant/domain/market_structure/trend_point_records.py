"""点版本仅保留磁盘序列根；并行载体按需浅读取，不缓存全历史身份表。"""

from collections.abc import Iterator, Sequence
from sys import getsizeof
from typing import TYPE_CHECKING, overload

from .evidence_record_sequence import EvidenceSequence, derive_evidence_sequence, iter_point_refs, point_ref_at
from .owned_evidence_producer import PointRecord, ProducerRecordScope
from .owned_evidence_records import OwnedEvidenceRecords, RecordRef

if TYPE_CHECKING:
    from .trend_point_version import PointVersion


class PointRecordView(Sequence[PointRecord]):
    """当前版本已认证前缀的惰性视图；返回载体只由本次消费者持有。"""

    __slots__ = ('_version', '_store', '_sequence', '_count')

    def __init__(self, version: Sequence[dict[str, object]], store: OwnedEvidenceRecords,
                 sequence: EvidenceSequence, count: int) -> None:
        self._version, self._store, self._sequence, self._count = version, store, sequence, count

    def __len__(self) -> int:
        return self._count

    def _point(self, position: int, reference: RecordRef) -> PointRecord:
        value = self._version[position]
        refs = self._store.dictionary_refs(reference)
        fields = tuple((key, pair[1]) for key, pair in zip(value, refs))
        return PointRecord(value, reference, fields, self._store)

    @overload
    def __getitem__(self, index: int) -> PointRecord: ...

    @overload
    def __getitem__(self, index: slice) -> tuple[PointRecord, ...]: ...

    def __getitem__(self, index: int | slice) -> PointRecord | tuple[PointRecord, ...]:
        if isinstance(index, slice):
            start, stop, step = index.indices(self._count)
            if step == 1:
                return tuple(self.iter_range(start, stop))
            return tuple(self[position] for position in range(start, stop, step))
        position = index + self._count if index < 0 else index
        if not 0 <= position < self._count:
            raise IndexError('point record index out of range')
        return self._point(position, point_ref_at(self._store, self._sequence, position))

    def __iter__(self) -> Iterator[PointRecord]:
        return self.iter_range()

    def iter_range(self, start: int = 0, stop: int | None = None) -> Iterator[PointRecord]:
        start, end, _ = slice(start, stop).indices(self._count)
        if start >= end:
            return
        for position, reference in enumerate(iter_point_refs(self._store, self._sequence, start=start, stop=end), start):
            yield self._point(position, reference)


def point_record_metadata_bytes(version: 'PointVersion') -> int:
    """当前根的常量元数据；点value图与store的既有预算仍由原拥有者计费。"""
    sequence = version._record_sequence
    if sequence is None:
        return 0
    size = getsizeof(sequence) + getsizeof(version._record_prefix)
    seen: set[int] = set()
    for value in (sequence.reference, sequence.root, sequence.capacity, sequence.length,
                  sequence.reference.namespace, sequence.reference.offset,
                  sequence.root.namespace, sequence.root.offset):
        if id(value) not in seen:
            seen.add(id(value))
            size += getsizeof(value)
    # 三个PointVersion属性槽、store借用引用及视图的固定外壳预留；没有
    # 持久PointRecord/字段引用表，调用端把这个数加入原版本拥有者预算。
    return size + 512


def record_point_version(version: 'PointVersion', scope: ProducerRecordScope | None = None) -> EvidenceSequence | None:
    """显式最终封存；只写未认证尾部及树路径，记录失败必须由调用方处理。

    未知公共序列保持原路径。合法内部版本的写入、容量或类型错误明确
    传播，不能悄悄把无记录前缀变成全历史重新编码的后备路径。
    """
    from .owned_evidence import CertifiedSource
    from .trend_point_version import PointVersion

    if scope is None or type(version) not in (PointVersion, CertifiedSource):
        return None
    if not version._sealed:
        raise ValueError('point records require a finally sealed point version')
    scope._require_open()
    previous = version._record_sequence
    if previous is not None and version._record_store is not scope.owner:
        raise ValueError('point record prefixes cannot cross request stores')
    start = version._record_prefix if previous is not None else 0
    if previous is not None and start == len(version) == previous.length:
        scope.owner._validate_ref(previous.reference)
        return previous
    references: list[RecordRef] = []
    for point in version.iter_range(start):
        record = scope.record_point(point)
        if record.value is not point or record.owner is not scope.owner:
            raise ValueError('recorded point must retain its exact version dictionary')
        references.append(record.reference)
    sequence = derive_evidence_sequence(scope.owner, references, previous=previous, start=start)
    # 只在完整新根提交后替换常量描述符；失败仍保留此前可用的前缀证明。
    version._record_sequence, version._record_store = sequence, scope.owner
    version._record_prefix = len(version)
    return sequence
