"""Advance a causal history reducer without recreating its local state."""

from collections.abc import Iterator
from typing import Generic, TypeVar


_Value = TypeVar('_Value')


class HistoryCursor(Generic[_Value]):
    """A boundary is yielded before that date is read, including on continue paths.

    The reducer owns its output. Internal consumers borrow it only until the next
    advance; public exports must copy mutable values they retain or modify.
    """

    def __init__(self, rows: Iterator[tuple[int, _Value]], empty: _Value) -> None:
        self._rows = rows
        self._next = next(rows, None)
        self._value = empty
        self._index = -1

    def advance(self, index: int) -> _Value:
        if type(index) is not int or index < self._index:
            raise ValueError('history cursor requires nondecreasing dates')
        while self._next is not None and self._next[0] <= index:
            self._next = next(self._rows, None)
        if self._next is not None:
            self._value = self._next[1]
        self._index = index
        return self._value

    def close(self) -> None:
        close = getattr(self._rows, 'close', None)
        if close is not None:
            close()
        self._next = None
