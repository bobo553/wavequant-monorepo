"""N sources from the selected, already confirmed alternation's polyline suffix."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from ..market_structure.polyline import ReversalPoint
from .trading_intervals import TradingEligibility


@dataclass(frozen=True, slots=True)
class EligibleNSource:
    points: tuple[ReversalPoint, ReversalPoint, ReversalPoint]
    known_at: int
    source_path: str
    window_start: int
    low_index: int
    low_ordinal: int


class EligiblePolylineN:
    """Keep window clocks separate from vertex dates; never invent a low vertex.

    An opening window can expose earlier vertices, but their N becomes a
    strategy input only at this close. Subsequent changes revisit the affected
    triples, preserving the original confirmation dates and same-bar order.
    """

    def __init__(self, level: int) -> None:
        self.eligibility = TradingEligibility(level)
        self._previous: dict[tuple[str, int], tuple[ReversalPoint, ...]] = {}
        self._seen: dict[tuple[str, int], set[tuple[ReversalPoint, ...]]] = {}
        self._last = -1

    def advance(self, now: int, points: Sequence[ReversalPoint],
                events: Sequence[Mapping[str, object]]) -> tuple[EligibleNSource, ...]:
        if type(now) is not int or now != self._last + 1:
            raise ValueError('eligible N sources require consecutive market dates')
        if any(point.confirmed_index > now for point in points):
            raise ValueError('N source includes an unknown polyline vertex')
        if any(event.get('bar_index') != now for event in events):
            raise ValueError('eligibility events must belong to this close')
        for event in events:
            self.eligibility.observe(event)
        result: list[EligibleNSource] = []
        live: set[tuple[str, int]] = set()
        emitted: set[tuple[ReversalPoint, ...]] = set()
        for window in self.eligibility.active_intervals:
            key = window.source_path, window.start_index
            live.add(key)
            if window.start_low_index is None:
                continue
            anchor = window.start_low_index, window.start_low_ordinal
            suffix = tuple(point for point in points
                           if (point.point.index, point.point.ordinal) >= anchor)
            prior = self._previous.get(key, ())
            common = 0
            while common < min(len(prior), len(suffix)) and prior[common] == suffix[common]:
                common += 1
            seen = self._seen.setdefault(key, set())
            for end in range(max(2, common), len(suffix)):
                triple = suffix[end - 2:end + 1]
                if triple in seen:
                    continue
                seen.add(triple)
                if triple in emitted:
                    continue
                emitted.add(triple)
                result.append(EligibleNSource((triple[0], triple[1], triple[2]), now,
                    window.source_path, window.start_index, window.start_low_index, window.start_low_ordinal))
            self._previous[key] = suffix
        self._previous = {key: value for key, value in self._previous.items() if key in live}
        self._seen = {key: value for key, value in self._seen.items() if key in live}
        self._last = now
        return tuple(result)
