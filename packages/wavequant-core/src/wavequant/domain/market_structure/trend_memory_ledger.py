"""Bounded ownership accounting for private immutable checkpoint blocks."""

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from sys import getsizeof


@dataclass(slots=True)
class _BlockCharge:
    owner: object
    children: tuple[object, ...]
    size: int
    references: int = 1


class _BlockBudgetExceeded(Exception):
    pass


class _ImmutableBlockLedger:
    """Retain shared blocks once; mutable roots are measured afresh.

    The caller guarantees that a block and its non-block descendants never
    mutate while retained. Sharing within a block is deduplicated; sharing
    payload between distinct blocks is conservatively charged twice. Block
    edges themselves are reference counted, including multiple live snapshots.
    """

    def __init__(self, children: Callable[[object], Iterable[object]],
                 is_block: Callable[[object], bool], *, limit: int) -> None:
        self._children = children
        self._is_block = is_block
        self._limit = limit
        self._blocks: dict[int, _BlockCharge] = {}
        self._roots: dict[int, object] = {}
        self._bytes = 0

    @property
    def memory_used(self) -> int:
        if not self._blocks:
            return 0
        return (self._bytes + getsizeof(self._blocks) + getsizeof(self._roots)
                + len(self._roots) * getsizeof(2 ** 64) + 4096)

    def clear(self) -> None:
        self._blocks.clear()
        self._roots.clear()
        self._bytes = 0

    def _scan(self, values: Iterable[object]) -> tuple[int, dict[int, object]]:
        pending = list(values)
        seen: set[int] = set()
        blocks: dict[int, object] = {}
        size = 0
        while pending:
            value = pending.pop()
            identity = id(value)
            if identity in seen:
                continue
            seen.add(identity)
            if self._is_block(value):
                blocks[identity] = value
            else:
                size += getsizeof(value)
                if size > self._limit:
                    raise _BlockBudgetExceeded
                pending.extend(self._children(value))
        return size, blocks

    def _acquire(self, owner: object, retained_before: int) -> None:
        pending = [owner]
        while pending:
            value = pending.pop()
            identity = id(value)
            saved = self._blocks.get(identity)
            if saved is not None:
                saved.references += 1
                continue
            payload, children = self._scan(self._children(value))
            edges = tuple(children.values())
            charge = _BlockCharge(value, edges, 0)
            # Entry, identity key, reference/size integers and edge storage
            # belong to this ledger, not to the domain payload.
            charge.size = (getsizeof(value) + payload + getsizeof(charge)
                           + getsizeof(edges) + getsizeof(identity) + 2 * getsizeof(2 ** 64))
            self._blocks[identity] = charge
            self._bytes += charge.size
            if self.memory_used - retained_before > self._limit:
                raise _BlockBudgetExceeded
            pending.extend(edges)

    def _release(self, owner: object) -> None:
        pending = [owner]
        while pending:
            value = pending.pop()
            saved = self._blocks[id(value)]
            saved.references -= 1
            if saved.references:
                continue
            del self._blocks[id(value)]
            self._bytes -= saved.size
            pending.extend(saved.children)

    def measure(self, roots: Sequence[object]) -> int:
        try:
            mutable_size, blocks = self._scan(roots)
            # Acquire first to preserve shared children. Bound newly acquired
            # charges separately; obsolete roots do not belong to the final
            # retained budget. No domain payload is copied during this transfer.
            retained_before = self.memory_used
            for identity, owner in blocks.items():
                if identity not in self._roots:
                    self._acquire(owner, retained_before)
            for identity, owner in self._roots.items():
                if identity not in blocks:
                    self._release(owner)
            self._roots = blocks
            if not self._blocks:
                self.clear()
            total = mutable_size + self.memory_used
            if total > self._limit:
                raise _BlockBudgetExceeded
            return total
        except _BlockBudgetExceeded:
            self.clear()
            return self._limit + 1
        except Exception:
            self.clear()
            raise
