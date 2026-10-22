"""请求内的固化前缀与变化尾部；检查点共享块树和索引树，历史保持只读。

这些容器只接收领域状态拥有的不可变记录，不用于公共可变输入。序列的
固化位置由各级当前关键位决定；索引按确认时刻封存版本，旧版本看不到后来
追加的条目。更早修订仍能恢复原值，不能以固化为由丢弃真实修订。
"""

from collections.abc import Iterable, Iterator, MutableMapping, MutableSequence
from dataclasses import dataclass
from sys import getsizeof
from typing import Generic, TypeVar, cast, overload


T = TypeVar('T')
K = TypeVar('K')
V = TypeVar('V')


@dataclass(frozen=True, slots=True)
class _SequenceNode:
    values: tuple[object, ...] = ()
    left: '_SequenceNode | None' = None
    right: '_SequenceNode | None' = None
    size: int = 0


def _sequence_node(values: tuple[object, ...] = (), left: _SequenceNode | None = None,
                   right: _SequenceNode | None = None) -> _SequenceNode:
    return _SequenceNode(values, left, right, 128 + getsizeof(values)
                         + (left.size if left else 0) + (right.size if right else 0))


def _replace_block(node: _SequenceNode | None, capacity: int, position: int,
                   values: tuple[object, ...]) -> _SequenceNode:
    if capacity == 1:
        return _sequence_node(values)
    half = capacity // 2
    left, right = (node.left, node.right) if node is not None else (None, None)
    if position < half:
        left = _replace_block(left, half, position, values)
    else:
        right = _replace_block(right, half, position - half, values)
    return _sequence_node(left=left, right=right)


def _block(node: _SequenceNode | None, capacity: int, position: int) -> tuple[object, ...]:
    while capacity > 1 and node is not None:
        capacity //= 2
        if position < capacity:
            node = node.left
        else:
            node, position = node.right, position - capacity
    return node.values if node is not None else ()


def _trim(node: _SequenceNode | None, capacity: int, blocks: int) -> _SequenceNode | None:
    if blocks <= 0:
        return None
    if blocks >= capacity or node is None:
        return node
    half = capacity // 2
    return _sequence_node(left=_trim(node.left, half, blocks),
                          right=_trim(node.right, half, blocks - half))


class FrozenPrefixList(MutableSequence[T]):
    """前缀为 32 项叶块的持久树；尾部修订/截短只复制树路径。"""

    def __init__(self, values: Iterable[T] = ()) -> None:
        self._archive: _SequenceNode | None = None
        self._capacity = 1
        self._stop = 0
        self._tail = list(values)

    @property
    def frozen_count(self) -> int:
        return self._stop

    @property
    def storage_bytes(self) -> int:
        return getsizeof(vars(self)) + getsizeof(self._tail) + (self._archive.size if self._archive else 0)

    def copy(self) -> 'FrozenPrefixList[T]':
        result = FrozenPrefixList[T]()
        result._archive, result._capacity, result._stop = self._archive, self._capacity, self._stop
        result._tail = self._tail.copy()
        return result

    def freeze(self, stop: int) -> None:
        if not self._stop <= stop <= len(self):
            raise ValueError('frozen prefix must advance within the current sequence')
        consumed = 0
        while self._stop < stop:
            position, offset = divmod(self._stop, 32)
            count = min(32 - offset, stop - self._stop)
            prefix = _block(self._archive, self._capacity, position)[:offset]
            self._write(position, (*prefix, *self._tail[consumed:consumed + count]))
            self._stop += count
            consumed += count
        if consumed:
            del self._tail[:consumed]

    def _write(self, position: int, values: tuple[object, ...]) -> None:
        while position >= self._capacity:
            self._archive = _sequence_node(left=self._archive)
            self._capacity *= 2
        self._archive = _replace_block(self._archive, self._capacity, position, values)

    def _truncate(self, stop: int) -> None:
        if stop >= self._stop:
            del self._tail[stop - self._stop:]
            return
        self._tail = []
        self._stop = stop
        self._archive = _trim(self._archive, self._capacity, (stop + 31) // 32)
        while self._capacity > 1 and stop <= self._capacity // 2 * 32:
            self._archive = self._archive.left if self._archive else None
            self._capacity //= 2
        if stop % 32:
            position = stop // 32
            self._write(position, _block(self._archive, self._capacity, position)[:stop % 32])

    def _detach(self, start: int) -> None:
        if start < self._stop:
            suffix = self[start:]
            self._truncate(start)
            self._tail = suffix

    def __len__(self) -> int:
        return self._stop + len(self._tail)

    def __iter__(self) -> Iterator[T]:
        return self.iter_range()

    def iter_range(self, start: int = 0, stop: int | None = None) -> Iterator[T]:
        """按块访问指定区间；跳过前缀，提前终止也不物化未访问的后缀。"""
        start, end, _ = slice(start, stop).indices(len(self))
        frozen_end = min(end, self._stop)
        pending = [(self._archive, self._capacity, 0)]
        while pending:
            node, capacity, block_start = pending.pop()
            left = block_start * 32
            if node is None or left >= frozen_end or left + capacity * 32 <= start:
                continue
            if capacity == 1:
                yield from cast(tuple[T, ...], node.values[max(0, start - left):min(32, frozen_end - left)])
            else:
                half = capacity // 2
                pending.extend(((node.right, half, block_start + half), (node.left, half, block_start)))
        for position in range(max(start, self._stop), end):
            yield self._tail[position - self._stop]

    def shared_prefix(self, other: 'FrozenPrefixList[T]') -> int:
        """按共享树块跳过旧前缀；只在不同叶块和可变尾部比较记录身份。"""
        stop = min(self._stop, other._stop)
        left, right = self._archive, other._archive
        capacity, other_capacity = self._capacity, other._capacity
        while capacity > other_capacity:
            left = left.left if left is not None else None
            capacity //= 2
        while other_capacity > capacity:
            right = right.left if right is not None else None
            other_capacity //= 2
        pending = [(left, right, capacity, 0)]
        while pending:
            left, right, width, start = pending.pop()
            if start >= stop or left is right:
                continue
            if left is None or right is None:
                return start
            if width == 1:
                for offset in range(min(32, stop - start)):
                    if left.values[offset] is not right.values[offset]:
                        return start + offset
            else:
                half = width // 2
                pending.append((left.right, right.right, half, start + half * 32))
                pending.append((left.left, right.left, half, start))
        end = min(len(self), len(other))
        for position in range(stop, end):
            if self[position] is not other[position]:
                return position
        return end

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, (list, tuple, FrozenPrefixList)):
            return False
        return len(self) == len(other) and all(left == right for left, right in zip(self, other))

    @overload
    def __getitem__(self, index: int) -> T: ...

    @overload
    def __getitem__(self, index: slice) -> list[T]: ...

    def __getitem__(self, index: int | slice) -> T | list[T]:
        if isinstance(index, slice):
            start, stop, step = index.indices(len(self))
            if step != 1:
                return [self[position] for position in range(start, stop, step)]
            return list(self.iter_range(start, stop))
        position = index + len(self) if index < 0 else index
        if position < 0 or position >= len(self):
            raise IndexError('state sequence index out of range')
        if position >= self._stop:
            return self._tail[position - self._stop]
        return cast(T, _block(self._archive, self._capacity, position // 32)[position % 32])

    @overload
    def __setitem__(self, index: int, value: T) -> None: ...

    @overload
    def __setitem__(self, index: slice, value: Iterable[T]) -> None: ...

    def __setitem__(self, index: int | slice, value: T | Iterable[T]) -> None:
        if isinstance(index, slice):
            start, stop, step = index.indices(len(self))
            if step != 1:
                positions = list(range(start, stop, step))
                values = list(cast(Iterable[T], value))
                if len(positions) != len(values):
                    raise ValueError('extended slice size mismatch')
                for position, item in zip(positions, values):
                    self[position] = item
                return
            self._detach(start)
            self._tail[start - self._stop:max(start, stop) - self._stop] = cast(Iterable[T], value)
            return
        position = index + len(self) if index < 0 else index
        self[position]
        if position >= self._stop:
            self._tail[position - self._stop] = cast(T, value)
        else:
            block_position, offset = divmod(position, 32)
            block_values = list(_block(self._archive, self._capacity, block_position))
            block_values[offset] = value
            self._write(block_position, tuple(block_values))

    def __delitem__(self, index: int | slice) -> None:
        if isinstance(index, slice):
            start, stop, step = index.indices(len(self))
            if step != 1:
                for position in sorted(range(start, stop, step), reverse=True):
                    del self[position]
                return
            if start >= stop:
                return
            if stop == len(self):
                self._truncate(start)
                return
            self._detach(start)
            del self._tail[start - self._stop:stop - self._stop]
            return
        position = index + len(self) if index < 0 else index
        self[position]
        self._detach(position)
        del self._tail[position - self._stop]

    def insert(self, index: int, value: T) -> None:
        position = max(0, min(index if index >= 0 else index + len(self), len(self)))
        self._detach(position)
        self._tail.insert(position - self._stop, value)

    def append(self, value: T) -> None:
        self._tail.append(value)

    def extend(self, values: Iterable[T]) -> None:
        self._tail.extend(values)


@dataclass(frozen=True, slots=True)
class _IndexNode:
    key_hash: int
    bit: int
    entries: tuple[tuple[object, object], ...]
    left: '_IndexNode | None' = None
    right: '_IndexNode | None' = None
    size: int = 0


def _leaf(key_hash: int, entries: tuple[tuple[object, object], ...]) -> _IndexNode:
    return _IndexNode(key_hash, -1, entries, size=192 + getsizeof(entries) + sum(getsizeof(pair) for pair in entries))


def _branch(bit: int, left: _IndexNode, right: _IndexNode) -> _IndexNode:
    return _IndexNode(left.key_hash, bit, (), left, right, 192 + left.size + right.size)


def _node_children(node: _SequenceNode | _IndexNode) -> tuple[object, ...]:
    if type(node) is _SequenceNode:
        return node.values, node.left, node.right, node.size
    return node.entries, node.left, node.right, node.key_hash, node.bit, node.size


def _index_set(node: _IndexNode | None, key_hash: int, key: object, value: object) -> _IndexNode:
    if node is None:
        return _leaf(key_hash, ((key, value),))
    differing = (node.key_hash ^ key_hash).bit_length() - 1
    if differing > node.bit:
        added = _leaf(key_hash, ((key, value),))
        return _branch(differing, node, added) if key_hash & (1 << differing) else _branch(differing, added, node)
    if node.bit < 0:
        entries = list(node.entries)
        for position, (existing, _) in enumerate(entries):
            if existing == key:
                entries[position] = existing, value
                break
        else:
            entries.append((key, value))
        return _leaf(key_hash, tuple(entries))
    assert node.left is not None and node.right is not None
    if key_hash & (1 << node.bit):
        return _branch(node.bit, node.left, _index_set(node.right, key_hash, key, value))
    return _branch(node.bit, _index_set(node.left, key_hash, key, value), node.right)


class _IndexArchive(Generic[K, V]):
    def __init__(self, root: _IndexNode | None = None) -> None:
        self.root = root


class FrozenPrefixMap(MutableMapping[K, V]):
    """压缩哈希树只复制变更路径，旧检查点分叉不枚举历史索引。

    每层消耗一个不同的哈希位，深度至多 64；碰撞桶按完整键比较。
    持久键序列保留插入顺序。节点不引用父版本，检查点释放即可回收旧节点。
    """

    def __init__(self, values: Iterable[tuple[K, V]] = ()) -> None:
        self._archive = _IndexArchive[K, V]()
        self._tail: dict[K, V] = {}
        self._keys = FrozenPrefixList[K]()
        for key, value in values:
            self[key] = value

    @property
    def storage_bytes(self) -> int:
        root = self._archive.root
        return getsizeof(vars(self)) + getsizeof(self._tail) + self._keys.storage_bytes + (root.size if root else 0)

    def copy(self) -> 'FrozenPrefixMap[K, V]':
        result = FrozenPrefixMap[K, V]()
        result._archive = self._archive
        result._tail, result._keys = self._tail.copy(), self._keys.copy()
        return result

    def freeze(self) -> None:
        root = self._archive.root
        for key, value in self._tail.items():
            root = _index_set(root, hash(key) & ((1 << 64) - 1), key, value)
        if self._tail:
            self._archive = _IndexArchive(root)
            self._tail = {}
        self._keys.freeze(len(self._keys))

    def __getitem__(self, key: K) -> V:
        if key in self._tail:
            return self._tail[key]
        key_hash = hash(key) & ((1 << 64) - 1)
        node = self._archive.root
        while node is not None and node.bit >= 0:
            node = node.right if key_hash & (1 << node.bit) else node.left
        if node is not None and node.key_hash == key_hash:
            for existing, value in node.entries:
                if existing == key:
                    return cast(V, value)
        raise KeyError(key)

    def __setitem__(self, key: K, value: V) -> None:
        if key not in self:
            self._keys.append(key)
        self._tail[key] = value

    def __delitem__(self, key: K) -> None:
        # 生产路径的索引只追加／覆盖。明确删除时保留 MutableMapping 语义，
        # 不引入会隐藏未来分支的共享 tombstone。
        values = dict(self.items())
        del values[key]
        self.__init__(values.items())

    def __iter__(self) -> Iterator[K]:
        return iter(self._keys)

    def __len__(self) -> int:
        return len(self._keys)
