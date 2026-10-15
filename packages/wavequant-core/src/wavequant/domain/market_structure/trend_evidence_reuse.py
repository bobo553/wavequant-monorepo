"""同次趋势计算的有界证据复用，不保存路径判断或交易资格。"""

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from io import BytesIO
import pickle
from sys import getsizeof
from typing import Never, ParamSpec, TypeVar


class _UncacheableGeometry(Exception):
    """未知审计对象不能触发自定义序列化钩子。"""


class _GeometryPickler(pickle.Pickler):
    def reducer_override(self, value: object) -> Never:
        # CPython 直接编码 exact builtin；其他对象在其 reduce 钩子之前拒绝。
        raise _UncacheableGeometry


def _geometry_signature(value: object) -> bytes | None:
    """编码内存中的标准证据，保留类型、字段顺序和别名关系。"""
    output = BytesIO()
    try:
        _GeometryPickler(output, protocol=5).dump(value)
    except _UncacheableGeometry:
        return None
    return output.getvalue()


@dataclass(frozen=True, slots=True)
class _CompletionProjection:
    signature: bytes
    # None 是开放／无身份来源；其他记录只描述原字段间的时钟投影。
    fields: tuple[tuple[str, str], ...] | None
    memory_bytes: int


class _CompletionProjectionCache:
    """每个来源坐标仅保存最近全文版本及其标量投影关系。"""

    def __init__(self, *, memory_bytes: int = 16 * 1024 * 1024) -> None:
        if type(memory_bytes) is not int or memory_bytes <= 0:
            raise ValueError('completion memory_bytes must be a positive integer')
        self._memory_bytes = memory_bytes
        self._memory_used = 0
        self._points: dict[tuple[object, ...], _CompletionProjection] = {}

    def project(self, raw: Mapping[str, object],
                validate: Callable[[Mapping[str, object]], dict[str, object] | None]) -> dict[str, object] | None:
        if type(raw) is not dict:
            return validate(raw)
        slot = tuple(raw.get(field) for field in ('trend_level', 'source_path', 'index', 'ordinal', 'kind'))
        if any(type(value) not in (int, str, type(None)) for value in slot):
            return validate(raw)
        signature = _geometry_signature(raw)
        if signature is None:
            return validate(raw)
        previous = self._points.get(slot)
        if previous is not None and previous.signature == signature:
            if previous.fields is None:
                return None
            cached_result: dict[str, object] = dict(raw)
            for target, source in previous.fields:
                cached_result[target] = raw[source]
            return cached_result
        result = validate(raw)
        fields: list[tuple[str, str]] | None = [] if result is not None else None
        if result is not None and fields is not None:
            for target in ('available_at', 'source_geometry_available_at'):
                if target not in result:
                    continue
                # 投影返回 _later_moment 的原输入。重放当日字段引用，不能把
                # 上次字符串的 identity 带入当天字典而改变后续签名的别名结构。
                origin_field = next((field for field in
                    ('available_at', 'wave_completed_at', 'source_geometry_available_at')
                    if field in raw and result[target] is raw[field]), None)
                if origin_field is None or type(result[target]) not in (int, str, bool, float, bytes, type(None)):
                    return result
                fields.append((target, origin_field))
        if previous is not None:
            self._memory_used -= previous.memory_bytes
            del self._points[slot]
        projection = tuple(fields) if fields is not None else None
        added = (getsizeof(signature) + getsizeof(slot) + sum(getsizeof(value) for value in slot)
                 + getsizeof(projection) + 256)
        if self._memory_used + added <= self._memory_bytes:
            self._points[slot] = _CompletionProjection(signature, projection, added)
            self._memory_used += added
        return result


_completion_cache: ContextVar[_CompletionProjectionCache | None] = ContextVar('trend_completion_cache', default=None)


@contextmanager
def _completion_projection_scope(*, memory_bytes: int = 16 * 1024 * 1024) -> Iterator[_CompletionProjectionCache]:
    cache = _CompletionProjectionCache(memory_bytes=memory_bytes)
    token = _completion_cache.set(cache)
    try:
        yield cache
    finally:
        _completion_cache.reset(token)


_Parameters = ParamSpec('_Parameters')
_Result = TypeVar('_Result')


def _cache_completion_projections(function: Callable[_Parameters, _Result]) -> Callable[_Parameters, _Result]:
    """历史计算拥有自己的 scope；嵌套调用与异常不泄漏缓存给其他请求。"""
    @wraps(function)
    def scoped(*args: _Parameters.args, **kwargs: _Parameters.kwargs) -> _Result:
        with _completion_projection_scope():
            return function(*args, **kwargs)
    return scoped
