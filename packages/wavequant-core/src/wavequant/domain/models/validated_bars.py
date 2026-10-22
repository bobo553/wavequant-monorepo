"""Provide an immutable, once-validated bar sequence for domain observers."""
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .model import Bar


@dataclass(frozen=True, init=False)
class ValidatedBars(Sequence):
    _values: tuple
    def __init__(self, bars: Sequence[Bar]) -> None:
        from ..market_state.wave_strength import validate_prefix
        values = tuple(bars)
        # Only frozen model Bars qualify, not mutable duck-typed records.
        if any(type(b) is not Bar for b in values):
            raise ValueError('immutable Bar records required')
        if values:
            validate_prefix(values, symbol=values[0].symbol, end=len(values)-1)
        object.__setattr__(self, '_values', values)

    @classmethod
    def _from_validated(cls, values: tuple[Bar, ...]) -> 'ValidatedBars':
        """只供已验证范围的内部窗口使用，不重新扫描其不可变行情。"""
        result = object.__new__(cls)
        object.__setattr__(result, '_values', values)
        return result

    def __add__(self, other):
        # General concatenation does NOT inherit the validation certificate.
        return self._values + tuple(other)

    def __len__(self):
        return len(self._values)

    def __getitem__(self, key):
        result = self._values[key]
        if not isinstance(key, slice):
            return result
        # Negative-step slices lose ascending time order and must be validated.
        if key.step is not None and key.step < 0:
            return ValidatedBars(result)
        child = object.__new__(ValidatedBars)
        object.__setattr__(child, '_values', result)
        return child


class _ValidatedBarWindows:
    """请求所有者的已认证区间；只验证实际消费窗口的空隙和连接。

    不缓存异常或调用方的可变输入。相邻但未验证连接的区间保持分开，
    未来行情和当前候选窗口外的坏行情不会使当前观察提前失败。
    """

    def __init__(self, source: tuple[Bar, ...]) -> None:
        self.source = source
        self._ranges: list[tuple[int, int]] = []

    def _certify(self, start: int, stop: int) -> bool:
        values = self.source[start:stop]
        if any(type(bar) is not Bar or type(bar.timestamp) is not datetime
               or type(bar.symbol) is not str
               or (bar.timestamp.tzinfo is not None
                   and type(bar.timestamp.tzinfo) not in (timezone, ZoneInfo))
               or any(type(price) not in (int, float) for price in
                      (bar.open, bar.high, bar.low, bar.close)) for bar in values):
            return False
        try:
            ValidatedBars(values)
        except (ValueError, TypeError, AttributeError, OverflowError):
            return False
        return True

    def window(self, start: int, stop: int) -> ValidatedBars | None:
        if (type(start) is not int or type(stop) is not int
                or not 0 <= start <= stop <= len(self.source)):
            raise ValueError('validated windows require a real half-open market interval')
        if start == stop:
            return ValidatedBars._from_validated(())
        cursor = start
        for left, right in self._ranges:
            if right <= start:
                continue
            if left >= stop:
                break
            if cursor < left or cursor == left and cursor > start:
                if not self._certify(max(start, cursor - 1), min(stop, left + 1)):
                    return None
            cursor = max(cursor, min(stop, right))
        if cursor < stop and not self._certify(max(start, cursor - 1), stop):
            return None
        # 只有整个请求成功后才保存认证，不把部分成功当作整段可信。
        merged: list[tuple[int, int]] = []
        left, right = start, stop
        for old_start, old_stop in self._ranges:
            if old_stop <= left:
                merged.append((old_start, old_stop))
            elif old_start >= right:
                merged.append((left, right))
                left, right = old_start, old_stop
            else:
                left, right = min(left, old_start), max(right, old_stop)
        merged.append((left, right))
        self._ranges = merged
        return ValidatedBars._from_validated(self.source[start:stop])
