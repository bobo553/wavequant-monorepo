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
        identity = id(current)
        if identity in seen:
            continue
        kind = type(current)
        if kind not in (dict, list, tuple, str, bytes, int, float, bool, type(None)):
            return None
        seen.add(identity)
        total += getsizeof(current)
        if total > limit:
            return None
        if type(current) is dict:
            pending.extend(current)
            pending.extend(current.values())
        elif type(current) is list or type(current) is tuple:
            pending.extend(current)
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
        self._entries: OrderedDict[Hashable, tuple[object, _Value, int, bytes | None]] = OrderedDict()
        self._bytes = 0

    @property
    def memory_used(self) -> int:
        return self._bytes + 4096 if self._entries else 0

    def get(self, key: Hashable, token: object) -> tuple[_Value] | None:
        saved = self._entries.get(key)
        if saved is None or (saved[0] != token and (saved[3] is None or saved[3] != token)):
            return None
        self._entries.move_to_end(key)
        return (saved[1],)

    def rebind_identity(self, key: Hashable, signature: bytes, token: object) -> bool:
        """Attach an identity to an admitted signature without rescanning its value.

        A signature-only entry retains the same signature and gains one opaque
        token. Existing identity entries only replace a fixed-size token. On
        insufficient space, preserve the signature entry for future lookups.
        """
        if type(signature) is not bytes or type(token) is not object:
            raise ValueError('rebinding requires exact bytes and an opaque identity')
        saved = self._entries.get(key)
        if saved is None:
            return False
        previous, value, charged, fallback = saved
        if type(previous) is object and fallback is not None:
            if fallback != signature:
                return False
            extra = 0
            retained_signature = fallback
        elif type(previous) is bytes and fallback is None:
            if previous != signature:
                return False
            extra = getsizeof(token)
            retained_signature = previous
        else:
            return False
        if self._bytes + extra + 4096 > self._limit:
            return False
        self._entries[key] = token, value, charged + extra, retained_signature
        self._bytes += extra
        self._entries.move_to_end(key)
        return True

    def put(self, key: Hashable, token: object, value: _Value, *, _identity_token: bool = False,
            _fallback_token: bytes | None = None) -> None:
        if _identity_token and type(token) is not object:
            raise ValueError("identity tokens must be opaque objects")
        if _fallback_token is not None and (not _identity_token or type(_fallback_token) is not bytes):
            raise ValueError('fallback signatures require an identity token and exact bytes')
        previous = self._entries.pop(key, None)
        if previous is not None:
            self._bytes -= previous[2]
        if not self._evict and len(self._entries) >= self._max_entries:
            return
        remaining = self._limit - 4096 - 512 - (self._bytes if not self._evict else 0)
        if remaining <= 0:
            return
        token_bytes = getsizeof(token) if _identity_token else 0
        size = _builtin_size((key, None if _identity_token else token, value, _fallback_token), remaining - token_bytes)
        if size is not None:
            size += token_bytes
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
        self._entries[key] = token, value, charged, _fallback_token
        self._bytes += charged

    def clear(self) -> None:
        self._entries.clear()
        self._bytes = 0
