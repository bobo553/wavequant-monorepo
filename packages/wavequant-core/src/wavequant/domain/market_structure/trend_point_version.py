"""内部点版本共享不可变前缀；公开序列化仍恢复普通数组。"""

from collections.abc import Iterable, Iterator, Sequence
from itertools import repeat
from operator import is_not
from typing import TYPE_CHECKING, SupportsIndex, cast, overload

from .trend_frozen_state import FrozenPrefixList

if TYPE_CHECKING:
    from .evidence_record_sequence import EvidenceSequence
    from .owned_evidence_producer import PointRecord, ProducerRecordScope
    from .owned_evidence_records import OwnedEvidenceRecords


class PointVersion(FrozenPrefixList[dict[str, object]]):
    """仅由拥有点外壳的生产者构建；发布后不得修改点或嵌套证据。

    parent 只保存版本令牌，不保留祖先对象链。回退/修订通过共享块树分叉，
    最早变化位置覆盖点字段与知识时钟；消费者可直接借用未变前缀。
    """

    def __init__(self, values: Iterable[dict[str, object]] = (), *,
                 previous: 'PointVersion | None' = None, start: int = 0) -> None:
        if not 0 <= start <= (len(previous) if previous is not None else 0):
            raise ValueError('point version requires a valid retained prefix')
        super().__init__()
        self.token = object()
        self.parent = previous.token if previous is not None else None
        self.changed_from = start
        self._sealed = False
        self._record_sequence: EvidenceSequence | None = None
        self._record_store: OwnedEvidenceRecords | None = None
        self._record_prefix = 0
        if previous is not None:
            previous.seal()
            self._archive, self._capacity, self._stop = previous._archive, previous._capacity, previous._stop
            self._truncate(start)
            self._record_sequence, self._record_store = previous._record_sequence, previous._record_store
            self._record_prefix = min(start, previous._record_prefix)
        self.extend(values)

    @property
    def point_records(self) -> Sequence['PointRecord']:
        """已认证前缀的惰性载体；不在版本上保存视图或逐点身份表。"""
        from .trend_point_records import PointRecordView

        if not self._sealed or self._record_sequence is None or self._record_store is None:
            return ()
        return PointRecordView(self, self._record_store, self._record_sequence, self._record_prefix)

    @property
    def evidence_sequence(self) -> 'EvidenceSequence | None':
        sequence = self._record_sequence
        return (sequence if self._sealed and sequence is not None
                and self._record_prefix == len(self) == sequence.length else None)

    @property
    def record_store(self) -> 'OwnedEvidenceRecords | None':
        return self._record_store if self.evidence_sequence is not None else None

    @property
    def record_metadata_bytes(self) -> int:
        from .trend_point_records import point_record_metadata_bytes

        return point_record_metadata_bytes(self)

    def _invalidate_records(self, index: int | slice) -> None:
        if isinstance(index, slice):
            start, stop, step = index.indices(len(self))
            positions = range(start, stop, step)
            start = min(positions[0], positions[-1]) if positions else start
        else:
            start = index if index >= 0 else len(self) + index
        self._record_prefix = min(self._record_prefix, max(0, start))

    def seal(self) -> 'PointVersion':
        if not self._sealed:
            self.freeze(len(self))
            self._sealed = True
        return self

    def _writeable(self) -> None:
        if self._sealed:
            raise TypeError('published point versions are immutable')

    @overload
    def __setitem__(self, index: int, value: dict[str, object]) -> None: ...

    @overload
    def __setitem__(self, index: slice, value: Iterable[dict[str, object]]) -> None: ...

    def __setitem__(self, index: int | slice,
                    value: dict[str, object] | Iterable[dict[str, object]]) -> None:
        self._writeable()
        self._invalidate_records(index)
        start = index.indices(len(self))[0] if isinstance(index, slice) else index if index >= 0 else len(self) + index
        self.changed_from = min(self.changed_from, max(0, start))
        if isinstance(index, slice):
            values = list(cast(Iterable[dict[str, object]], value))
            for item in values:
                self._validate(item)
            super().__setitem__(index, values)
        else:
            item = cast(dict[str, object], value)
            self._validate(item)
            super().__setitem__(index, item)

    def __delitem__(self, index: int | slice) -> None:
        self._writeable()
        self._invalidate_records(index)
        start = index.indices(len(self))[0] if isinstance(index, slice) else index if index >= 0 else len(self) + index
        self.changed_from = min(self.changed_from, max(0, start))
        super().__delitem__(index)

    @staticmethod
    def _validate(value: dict[str, object]) -> None:
        if type(value) is not dict or any(map(is_not, map(type, value), repeat(str))):
            raise ValueError('owned points require dictionaries with string keys')

    def append(self, value: dict[str, object]) -> None:
        self._writeable()
        self._validate(value)
        super().append(value)

    def extend(self, values: Iterable[dict[str, object]]) -> None:
        self._writeable()
        for value in values:
            self.append(value)

    def insert(self, index: int, value: dict[str, object]) -> None:
        self._writeable()
        self._record_prefix = min(self._record_prefix, max(0, index))
        self.changed_from = min(self.changed_from, max(0, index))
        self._validate(value)
        super().insert(index, value)

    def __reduce_ex__(self, protocol: SupportsIndex) -> tuple[
        type[list[dict[str, object]]], tuple[()], None, Iterator[dict[str, object]],
    ]:
        # 公开 pickle/deepcopy 边界物化；历史分块编码可直接读取变化后缀。
        return list, (), None, iter(self)

    def __eq__(self, other: object) -> bool:
        if self is other:
            return True
        if not isinstance(other, (list, tuple, FrozenPrefixList)):
            return False
        values = cast(list[object], other)
        return len(self) == len(values) and all(left == right for left, right in zip(self, values))

    def changed_since(self, previous: 'PointVersion | None') -> int:
        if previous is self:
            return len(self)
        if previous is None:
            return 0
        if self.parent is previous.token:
            return self.changed_from
        # 跨过中间版本时按共享块定位变化，不能重新遍历整段冻结前缀。
        return self.shared_prefix(previous)


def record_point_version(version: PointVersion, scope: 'ProducerRecordScope | None' = None) -> 'EvidenceSequence | None':
    """内部生产者在最终 seal 后显式连接并行证据根。"""
    from .trend_point_records import record_point_version as record

    return record(version, scope)
