"""请求内只读借用的有界解码缓存；不改变公共可变导出契约。"""

from collections import OrderedDict
from collections.abc import Hashable
from sys import getsizeof
from typing import Generic, TypeVar, cast


_Value = TypeVar('_Value')


def _builtin_size(value: object, limit: int) -> int | None:
    """只接纳标准容器，按对象身份计费；未知对象不调用大小或遍历钩子。"""
    pending = [value]
    seen: set[int] = set()
    total = 0
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        kind = type(current)
        if kind not in (dict, list, tuple, str, bytes, int, float, bool, type(None)):
            return None
        seen.add(id(current))
        total += getsizeof(current)
        if total > limit:
            return None
        if kind is dict:
            mapping = cast(dict[object, object], current)
            pending.extend(mapping.keys())
            pending.extend(mapping.values())
        elif kind in (list, tuple):
            pending.extend(cast(list[object] | tuple[object, ...], current))
    return total


class _ReadonlySnapshotCache(Generic[_Value]):
    """同请求、同线程的私有借用；调用方不得修改或公开缓存容器。

    token 必须是来源的不可变完整版本，不得用可变对象 id 或日期代替。
    仅限已确认只读的内部消费者，公开导出必须重新解码。容量包括键、
    token、值及固定管理余量；超大行不缓存，避免逐日历史无限驻留。
    """

    def __init__(self, *, memory_bytes: int, max_entries: int, evict: bool = True) -> None:
        if type(memory_bytes) is not int or memory_bytes < 0:
            raise ValueError('readonly cache memory_bytes must be a nonnegative integer')
        if type(max_entries) is not int or max_entries < 1:
            raise ValueError('readonly cache max_entries must be a positive integer')
        self._limit = memory_bytes
        self._max_entries = max_entries
        self._evict = evict
        self._entries: OrderedDict[Hashable, tuple[object, _Value, int]] = OrderedDict()
        self._bytes = 0

    @property
    def memory_used(self) -> int:
        return self._bytes + 4096 if self._entries else 0

    def get(self, key: Hashable, token: object) -> tuple[_Value] | None:
        saved = self._entries.get(key)
        if saved is None or saved[0] != token:
            return None
        self._entries.move_to_end(key)
        return (saved[1],)

    def put(self, key: Hashable, token: object, value: _Value) -> None:
        previous = self._entries.pop(key, None)
        if previous is not None:
            self._bytes -= previous[2]
        size = _builtin_size((key, token, value), self._limit - 4096 - 512)
        if size is None:
            return
        charged = size + 512
        # 顺序扫描完整前缀时，拒绝新项可避免 LRU 每日把所有旧热点逐个挤出。
        if not self._evict and (len(self._entries) >= self._max_entries
                or self._bytes + charged + 4096 > self._limit):
            return
        while self._entries and (len(self._entries) >= self._max_entries
                or self._bytes + charged + 4096 > self._limit):
            _, removed = self._entries.popitem(last=False)
            self._bytes -= removed[2]
        self._entries[key] = token, value, charged
        self._bytes += charged

    def clear(self) -> None:
        self._entries.clear()
        self._bytes = 0
