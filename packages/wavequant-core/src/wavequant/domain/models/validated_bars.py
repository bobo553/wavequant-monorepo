"""Provide an immutable, once-validated bar sequence for domain observers."""
from bisect import bisect_right
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


class ValidatedBarWindows:
    """固定行情的已验证区间；重叠观察窗口只检查新行情和接缝。

    不预验未来行情，也不按可变列表身份缓存。区间只保存两个边界，返回
    值继续使用 ValidatedBars 的只读切片证书，不保存每次观察的窗口副本。
    """

    def __init__(self, bars: tuple[Bar, ...]) -> None:
        self._bars = bars
        self._ranges: list[tuple[int, int]] = []
        self._cacheable = type(bars) is tuple and all(
            type(bar) is Bar and type(bar.timestamp) is datetime
            and (bar.timestamp.tzinfo is None or type(bar.timestamp.tzinfo) in (timezone, ZoneInfo))
            and type(bar.symbol) is str
            and all(type(getattr(bar, name)) in (int, float) for name in ('open', 'high', 'low', 'close'))
            for bar in bars)
        self.validated_bars = 0
        self.reused_windows = 0

    def window(self, start: int, stop: int) -> Sequence[Bar]:
        if (type(start) is not int or type(stop) is not int
                or not 0 <= start <= stop <= len(self._bars)):
            raise ValueError('validated windows require a real half-open interval')
        values = self._bars[start:stop]
        if not self._cacheable or start == stop:
            return values
        position = bisect_right(self._ranges, (start, len(self._bars) + 1)) - 1
        if position >= 0 and self._ranges[position][1] >= stop:
            self.reused_windows += 1
            result = object.__new__(ValidatedBars)
            object.__setattr__(result, '_values', values)
            return result
        cursor = start
        for left, right in self._ranges:
            if right <= start:
                continue
            if left >= stop:
                break
            if left > cursor:
                # 包含两侧最多各一根旧行情，以原校验器验证跨区间的相邻关系。
                begin, end = max(start, cursor - 1), min(stop, left + 1)
                ValidatedBars(self._bars[begin:end])
                self.validated_bars += end - begin
            elif left == cursor and cursor > start:
                ValidatedBars(self._bars[cursor - 1:cursor + 1])
                self.validated_bars += 2
            cursor = max(cursor, right)
            if cursor >= stop:
                break
        if cursor < stop:
            begin = max(start, cursor - 1)
            ValidatedBars(self._bars[begin:stop])
            self.validated_bars += stop - begin
        # 只有共享行情的区间才能直接合并；仅相邻的区间尚未验证接缝。
        merged: list[tuple[int, int]] = []
        lower, upper = start, stop
        for left, right in self._ranges:
            if right <= lower:
                merged.append((left, right))
            elif upper <= left:
                merged.append((lower, upper))
                lower, upper = left, right
            else:
                lower, upper = min(lower, left), max(upper, right)
        merged.append((lower, upper))
        self._ranges = merged
        result = object.__new__(ValidatedBars)
        object.__setattr__(result, '_values', values)
        return result
