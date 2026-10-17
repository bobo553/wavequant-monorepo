"""Request-owned event reducers with explicit correction handling."""

from collections.abc import Mapping, Sequence
from copy import deepcopy

from ..models.model import Bar


class EventHistoryStream:
    """Advance sealed candles once; replay only when already-consumed facts change.

    The source is an owned tuple of frozen Bars, so a caller cannot silently edit
    a certified candle. A different market source requires a new stream. Event
    certificates contain owned values, not list lengths or mutable references.
    Late, removed, reordered and edited historical facts all invalidate the
    reducer. Normal new-date facts may be inserted anywhere in the input list.
    """

    def __init__(self, bars: Sequence[Bar], *, start_index: int = 0) -> None:
        self.bars = tuple(bars)
        if any(type(bar) is not Bar for bar in self.bars):
            raise ValueError('event histories require immutable Bar records')
        if type(start_index) is not int or not 0 <= start_index <= len(self.bars):
            raise ValueError('invalid event history window start')
        self.start_index = start_index
        self.index = start_index - 1
        self.processed_days = 0
        self.history: dict[int, dict[str, object]] = {}
        self.events: tuple[dict[str, object], ...] = ()
        self.dated: dict[int, list[dict[str, object]]] = {}
        self._reset_state()

    def _accepts(self, event: Mapping[str, object]) -> bool:
        return True

    def _effective_index(self, event: Mapping[str, object]) -> int:
        index = event['bar_index']
        assert type(index) is int
        return index

    def _reset_state(self) -> None:
        raise NotImplementedError

    def _advance_day(self, index: int) -> None:
        raise NotImplementedError

    def advance(
        self, index: int, events: Sequence[Mapping[str, object]],
    ) -> dict[int, dict[str, object]]:
        if type(index) is not int or index < self.index or index >= len(self.bars):
            raise ValueError('event history requires a nondecreasing source date')
        values = tuple(deepcopy(dict(event)) for event in events
            if self._accepts(event) and type(known := event.get('bar_index')) is int
            and self.start_index <= known <= index)
        consumed = tuple(event for event in self.events if self._effective_index(event) <= self.index)
        revised = tuple(event for event in values if self._effective_index(event) <= self.index)
        if consumed != revised:
            self.index = self.start_index - 1
            self.history.clear()
            self._reset_state()
        self.events = values
        self.dated = {}
        for event in values:
            known = event['bar_index']
            assert type(known) is int
            self.dated.setdefault(known, []).append(event)
        while self.index < index:
            self.index += 1
            self._advance_day(self.index)
            self.processed_days += 1
        return self.history
