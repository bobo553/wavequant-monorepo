"""不可变输入的因果前缀视图；建立窗口不复制此前全部行情或证据。"""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Generic, TypeVar, overload


_Item = TypeVar('_Item')


@dataclass(frozen=True, slots=True, eq=False)
class _PrefixView(Sequence[_Item], Generic[_Item]):
    """只供内部不可变快照使用，切片继续借用同一底层数组。"""

    source: Sequence[_Item]
    positions: range

    def __len__(self) -> int:
        return len(self.positions)

    @overload
    def __getitem__(self, index: int) -> _Item: ...

    @overload
    def __getitem__(self, index: slice) -> '_PrefixView[_Item]': ...

    def __getitem__(self, index: int | slice) -> '_Item | _PrefixView[_Item]':
        if isinstance(index, slice):
            return _PrefixView(self.source, self.positions[index])
        return self.source[self.positions[index]]

    def __iter__(self) -> Iterator[_Item]:
        return (self.source[index] for index in self.positions)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Sequence):
            return False
        return len(self) == len(other) and all(left == right for left, right in zip(self, other))


@dataclass(frozen=True, slots=True, eq=False)
class _VisibleReferences(Sequence[_Item | None], Generic[_Item]):
    """保留来源位置编号，截止日右侧读取为 None，不分配等长填充数组。"""

    source: Sequence[_Item | None]
    stop: int

    def __len__(self) -> int:
        return len(self.source)

    @overload
    def __getitem__(self, index: int) -> _Item | None: ...

    @overload
    def __getitem__(self, index: slice) -> _PrefixView[_Item | None]: ...

    def __getitem__(self, index: int | slice) -> '_Item | None | _PrefixView[_Item | None]':
        if isinstance(index, slice):
            return _PrefixView(self, range(len(self))[index])
        position = index if index >= 0 else len(self) + index
        if not 0 <= position < len(self):
            raise IndexError('reference position outside source')
        return self.source[position] if position < self.stop else None

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Sequence):
            return False
        return len(self) == len(other) and all(left == right for left, right in zip(self, other))
