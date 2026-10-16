"""Reuse closed strategy observations within one immutable market request."""

from collections import OrderedDict
from collections.abc import Callable, Hashable, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import TypeVar, cast

from ..models.model import Bar


_Result = TypeVar('_Result')


def first_positive_n_invalidation(
    bars: Sequence[Bar], *, origin: int, attack: int, defense: float,
) -> int | None:
    """Return the same first origin/defense breach used by the strategy audit."""
    for now in range(attack + 1, len(bars)):
        if bars[now].low < bars[origin].low or bars[now].low < defense:
            return now
    return None


@dataclass
class _InvalidationScan:
    through: int
    first: int | None = None


class StrategyHistoryCache:
    """Own bounded memo tables; a partial last candle never receives a certificate.

    Source Bars are frozen values. Each binding certifies the complete older
    prefix once; any changed older candle forces the caller's original path.
    No global cache or account/decision state is retained here.
    """

    def __init__(self, source: tuple[Bar, ...], *, capacity: int = 8192) -> None:
        if capacity < 1:
            raise ValueError('positive strategy history capacity required')
        if any(type(bar) is not Bar for bar in source):
            raise ValueError('immutable Bar records required')
        self.source = tuple(source)
        self.capacity = capacity
        self._observations: OrderedDict[tuple[Hashable, ...], object] = OrderedDict()
        self._invalidations: OrderedDict[tuple[int, int, float, float], _InvalidationScan] = OrderedDict()

    def bind(self, bars: Sequence[Bar]) -> 'StrategyHistoryView | None':
        values = tuple(bars)
        count = len(values)
        if not count or count > len(self.source):
            return None
        if values[:-1] != self.source[:count - 1]:
            return None
        last, original = values[-1], self.source[count - 1]
        if last.timestamp != original.timestamp or last.symbol != original.symbol:
            return None
        sealed = count - 1 if last == original else count - 2
        return StrategyHistoryView(self, values, sealed)

    def _remember(self, key: tuple[Hashable, ...], value: object) -> None:
        self._observations[key] = deepcopy(value)
        self._observations.move_to_end(key)
        if len(self._observations) > self.capacity:
            self._observations.popitem(last=False)


@dataclass(frozen=True)
class StrategyHistoryView:
    """Only the exact sealed prefix may reuse dated, fully keyed observations."""

    cache: StrategyHistoryCache
    bars: tuple[Bar, ...]
    sealed_through: int

    def observe(
        self, namespace: str, now: int, inputs: tuple[Hashable, ...], calculate: Callable[[], _Result],
    ) -> _Result:
        if not 0 <= now <= self.sealed_through:
            return calculate()
        key = (namespace, now, *inputs)
        if key in self.cache._observations:
            self.cache._observations.move_to_end(key)
            return cast(_Result, deepcopy(self.cache._observations[key]))
        result = calculate()
        self.cache._remember(key, result)
        return result

    def first_positive_n_invalidation(self, *, origin: int, attack: int, defense: float) -> int | None:
        origin_low = self.bars[origin].low
        key = (origin, attack, origin_low, defense)
        scan = self.cache._invalidations.get(key)
        if scan is None:
            scan = _InvalidationScan(attack)
            self.cache._invalidations[key] = scan
            if len(self.cache._invalidations) > self.cache.capacity:
                self.cache._invalidations.popitem(last=False)
        self.cache._invalidations.move_to_end(key)
        if scan.first is None:
            for now in range(scan.through + 1, self.sealed_through + 1):
                scan.through = now
                if self.bars[now].low < origin_low or self.bars[now].low < defense:
                    scan.first = now
                    break
        # A shorter replay cannot observe a breach cached from a later close.
        if scan.first is not None and scan.first <= self.sealed_through:
            return scan.first
        # An intraday replacement of the last candle is evaluated but never saved.
        last = len(self.bars) - 1
        if last > self.sealed_through and last > attack:
            if self.bars[last].low < origin_low or self.bars[last].low < defense:
                return last
        return None
