"""N sources from the selected, already confirmed alternation's polyline suffix."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from ..market_structure.polyline import ReversalPoint
from .folded_n import folded_inverse_n_candidates, folded_positive_n_candidates
from .trading_intervals import TradingEligibility


@dataclass(frozen=True, slots=True)
class EligibleNSource:
    points: tuple[ReversalPoint, ReversalPoint, ReversalPoint]
    known_at: int
    source_path: str
    window_start: int
    low_index: int
    low_ordinal: int

    @property
    def scope(self) -> tuple[str, int]:
        """同一组三点在重开窗口中是新的交易观察，不延续已关闭窗口。"""
        return self.source_path, self.window_start


class EligiblePolylineN:
    """Keep window clocks separate from vertex dates; never invent a low vertex.

    An opening window can expose earlier vertices, but their N becomes a
    strategy input only at this close. Subsequent changes revisit the affected
    triples, preserving the original confirmation dates and same-bar order.
    V3 opts into bounded folded envelopes; both observers consume the same
    source rows, while sealed legacy profiles retain adjacent triples only.
    """

    def __init__(self, level: int, *, folded_window: int | None = None) -> None:
        if folded_window is not None and (type(folded_window) is not int or folded_window <= 0):
            raise ValueError('folded N window must be a positive integer')
        self.eligibility = TradingEligibility(level)
        self.folded_window = folded_window
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
            triples = [suffix[end - 2:end + 1] for end in range(max(2, common), len(suffix))]
            if self.folded_window is not None and suffix != prior:
                # 内部小反弹不能替换仍有效的外层颈线。只组合本窗口已确认的
                # 原始折线点，不生成趋势顶点，也不回填窗口开放前的知识日。
                earliest = max(window.start_low_index, now - self.folded_window)
                triples.extend(refs for refs, _ in folded_positive_n_candidates(suffix, earliest=earliest))
                triples.extend(refs for refs, _ in folded_inverse_n_candidates(suffix, earliest=earliest))
            for triple in triples:
                if triple in seen:
                    continue
                seen.add(triple)
                result.append(EligibleNSource((triple[0], triple[1], triple[2]), now,
                    window.source_path, window.start_index, window.start_low_index, window.start_low_ordinal))
            self._previous[key] = suffix
        self._previous = {key: value for key, value in self._previous.items() if key in live}
        self._seen = {key: value for key, value in self._seen.items() if key in live}
        self._last = now
        return tuple(result)
