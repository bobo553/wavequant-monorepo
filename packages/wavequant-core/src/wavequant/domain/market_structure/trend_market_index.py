"""只读行情的首个严格突破索引，查询显式限定真实前缀和半开区间。"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from fractions import Fraction
from math import inf, isfinite
from types import MappingProxyType
from zoneinfo import ZoneInfo

from ..models.model import Bar
from .hierarchical_confirmation import session_date
from .trend_prefix_view import _PrefixView


def market_prefix(bars: Sequence[Bar], stop: int) -> Sequence[Bar]:
    """私有 frozen tuple 的窗口只移动边界；外部可变序列保留原切片。"""
    if type(bars) is tuple:
        return _PrefixView(bars, range(len(bars))[:stop])
    return bars[:stop]


def is_owned_market_prefix(bars: Sequence[Bar], owner: tuple[Bar, ...]) -> bool:
    return (type(bars) is _PrefixView and bars.source is owner
            and bars.positions == range(len(bars)) and len(bars) <= len(owner))


class _MarketRangeIndex:
    """一次构建 O(n) 极值树；查询不会返回 stop 及其后的行情。

    极值查询 O(log n)。收盘交叉使用已存在的相邻收盘区间剪枝，最坏仍为
    O(n)，但不再逐根重读所有已经不可能跨过目标价的交易日。
    """

    def __init__(self, bars: tuple[Bar, ...]) -> None:
        if type(bars) is not tuple:
            raise ValueError('a market index requires an immutable Bar tuple')
        self.bars = bars
        size = 1
        while size < len(bars):
            size *= 2
        self.size = size
        self.low = [inf] * (2 * size)
        self.high = [-inf] * (2 * size)
        self.close = [inf] * (2 * size)
        self.close_high = [-inf] * (2 * size)
        self.up_previous = [inf] * (2 * size)
        self.up_close = [-inf] * (2 * size)
        self.down_previous = [-inf] * (2 * size)
        self.down_close = [inf] * (2 * size)
        positions: dict[str, list[int]] = {}
        session_dates: list[str] = []
        for index, bar in enumerate(bars):
            day = session_date(bar)
            session_dates.append(day)
            positions.setdefault(day, []).append(index)
            cursor = size + index
            self.low[cursor], self.high[cursor] = float(bar.low), float(bar.high)
            self.close[cursor] = float(bar.close)
            self.close_high[cursor] = float(bar.close)
            if index:
                previous, closing = float(bars[index - 1].close), float(bar.close)
                if previous < closing:
                    self.up_previous[cursor], self.up_close[cursor] = previous, closing
                if previous > closing:
                    self.down_previous[cursor], self.down_close[cursor] = previous, closing
        for cursor in range(size - 1, 0, -1):
            left, right = 2 * cursor, 2 * cursor + 1
            for values in (self.low, self.close, self.up_previous, self.down_close):
                values[cursor] = min(values[left], values[right])
            for values in (self.high, self.close_high, self.up_close, self.down_previous):
                values[cursor] = max(values[left], values[right])
        self.positions: Mapping[str, tuple[int, ...]] = MappingProxyType(
            {key: tuple(value) for key, value in positions.items()})
        self.unique_sessions = all(len(value) == 1 for value in self.positions.values())
        self.session_dates = tuple(session_dates)
        self.ordered_sessions = all(left <= right for left, right in zip(session_dates, session_dates[1:]))
        self.exact_prices = all(type(price) is float or float(price) == price
                                for bar in bars for price in (bar.low, bar.high, bar.close))
        self.confirmation_reuse: object | None = None
        self.reference_reuse: object | None = None

    @classmethod
    def build(cls, bars: tuple[Bar, ...]) -> '_MarketRangeIndex | None':
        # 非标准价格或未来日期的异常不能因建索引提前出现，沿用原逐日路径。
        try:
            if any(type(bar) is not Bar or type(bar.timestamp) is not datetime
                    or (bar.timestamp.tzinfo is not None
                        and type(bar.timestamp.tzinfo) not in (timezone, ZoneInfo))
                    or any(type(price) not in (int, float) or not isfinite(price)
                    for price in (bar.low, bar.high, bar.close)) for bar in bars):
                return None
            return cls(bars)
        except (ValueError, TypeError, AttributeError, OverflowError):
            return None

    def first_extreme(self, start: int, stop: int, price: float, *, high: bool) -> int | None:
        values = self.high if high else self.low
        return self._first_extreme(values, start, stop, price, high=high)

    def first_close_extreme(self, start: int, stop: int, price: float, *, high: bool) -> int | None:
        """首个严格收盘越位，不额外要求前收盘位于阈值另一侧。"""
        return self._first_extreme(self.close_high if high else self.close, start, stop, price, high=high)

    def _first_extreme(self, values: list[float], start: int, stop: int, price: float, *, high: bool) -> int | None:
        stack = [(1, 0, self.size)]
        while stack:
            node, left, right = stack.pop()
            if right <= start or left >= stop or (values[node] <= price if high else values[node] >= price):
                continue
            if right - left == 1:
                return left
            middle = (left + right) // 2
            stack.append((2 * node + 1, middle, right))
            stack.append((2 * node, left, middle))
        return None

    def first_close_turn(self, start: int, stop: int, price: float, *, up: bool) -> int | None:
        previous = self.up_previous if up else self.down_previous
        closing = self.up_close if up else self.down_close
        stack = [(1, 0, self.size)]
        while stack:
            node, left, right = stack.pop()
            blocked = (previous[node] > price or closing[node] <= price if up
                       else previous[node] < price or closing[node] >= price)
            if right <= start or left >= stop or blocked:
                continue
            if right - left == 1:
                return left
            middle = (left + right) // 2
            stack.append((2 * node + 1, middle, right))
            stack.append((2 * node, left, middle))
        return None

    def supports_price(self, price: object) -> bool:
        """仅对可精确表示的有限普通价格使用区间剪枝。"""
        try:
            return (self.exact_prices and isinstance(price, (int, float)) and type(price) in (int, float)
                    and isfinite(price) and float(price) == price)
        except (TypeError, ValueError, OverflowError):
            return False

    def _range_bounds(self, start: int, stop: int) -> None:
        if type(start) is not int or type(stop) is not int or not 0 <= start <= stop <= len(self.bars):
            raise ValueError('market ranges require a real half-open interval')

    def extreme_position(self, start: int, stop: int, *, high: bool) -> int:
        """返回窗口内极值的最早位置，保持 min/max 的原始等值顺序。"""
        return self._extreme_position(start, stop, self.high if high else self.low, high=high)

    def lowest_close_position(self, start: int, stop: int) -> int:
        """仅在当时观察窗口中寻找最早最低收盘，不能借后来收盘修订回调。"""
        return self._extreme_position(start, stop, self.close, high=False)

    def _extreme_position(self, start: int, stop: int, values: list[float], *, high: bool) -> int:
        self._range_bounds(start, stop)
        if start == stop:
            raise ValueError('an extreme requires a nonempty market interval')
        left, right = self.size + start, self.size + stop
        extreme = -inf if high else inf
        choose = max if high else min
        while left < right:
            if left % 2:
                extreme = choose(extreme, values[left])
                left += 1
            if right % 2:
                right -= 1
                extreme = choose(extreme, values[right])
            left //= 2
            right //= 2
        stack = [(1, 0, self.size)]
        while stack:
            node, left, right = stack.pop()
            if (right <= start or left >= stop
                    or (values[node] < extreme if high else values[node] > extreme)):
                continue
            if right - left == 1:
                return left
            middle = (left + right) // 2
            stack.append((2 * node + 1, middle, right))
            stack.append((2 * node, left, middle))
        raise ValueError('a market extreme must belong to its requested interval')

    def first_close_below(self, start: int, stop: int, floor: Fraction) -> int | None:
        """严格收盘失守；保留十进制 Fraction 边界，不将防线舍入成 float。"""
        self._range_bounds(start, stop)
        stack = [(1, 0, self.size)]
        while stack:
            node, left, right = stack.pop()
            if right <= start or left >= stop:
                continue
            minimum = self.close[node]
            if minimum == inf or Fraction(str(minimum)) >= floor:
                continue
            if right - left == 1:
                return left
            middle = (left + right) // 2
            stack.append((2 * node + 1, middle, right))
            stack.append((2 * node, left, middle))
        return None


@dataclass(frozen=True, slots=True)
class _MarketPrefixIndex:
    owner: _MarketRangeIndex
    bars: Sequence[Bar]

    def __post_init__(self) -> None:
        if is_owned_market_prefix(self.bars, self.owner.bars):
            return
        if (type(self.bars) is not tuple or len(self.bars) > len(self.owner.bars)
                or any(bar is not self.owner.bars[index] for index, bar in enumerate(self.bars))):
            raise ValueError('a market index prefix must contain the original Bar objects')

    def matches(self, bars: Sequence[Bar] | None) -> bool:
        return bars is self.bars

    def _bounds(self, start: int, stop: int) -> None:
        if type(start) is not int or type(stop) is not int or not 0 <= start <= stop <= len(self.bars):
            raise ValueError('market index queries require a real half-open prefix interval')

    def first_extreme(self, start: int, stop: int, price: float, *, high: bool) -> int | None:
        self._bounds(start, stop)
        return self.owner.first_extreme(start, stop, price, high=high)

    def first_close_turn(self, start: int, stop: int, price: float, *, up: bool) -> int | None:
        self._bounds(start, stop)
        return self.owner.first_close_turn(start, stop, price, up=up)

    def first_close_extreme(self, start: int, stop: int, price: float, *, high: bool) -> int | None:
        self._bounds(start, stop)
        return self.owner.first_close_extreme(start, stop, price, high=high)
