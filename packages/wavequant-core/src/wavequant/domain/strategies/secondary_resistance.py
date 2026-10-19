"""A lower-level squeeze cannot resolve a fresh resisted secondary breakout."""

from collections.abc import Sequence
from copy import deepcopy

from ..models.model import Bar
from ..market_structure.observation_window import observation_window_end
from ..market_structure.price_action import Direction, ShadowPolicy, observe_resistance
from .history_cursor import HistoryCursor


def _secondary_resistance_days(bars, history, keys, *, include_resolved, start_index=0, end_index=None):
    end = observation_window_end(len(bars), start_index, end_index)
    blocked = {}
    current_key = None
    attack = None
    resisted = False
    record = 0.0
    defense = 0.0
    resolved_at = None
    resolved_high = 0.0
    tracked_level = None
    chart_key = None
    chart_known = False
    first = max(1, start_index)
    for i in range(first, end + 1):
        yield i, blocked
        if i == first and start_index:
            # The last known key is geometry, not a pre-window resistance episode.
            previous_key = max((day for day in keys if day < i), default=None)
            if previous_key is not None:
                chart_key, chart_known = keys[previous_key], True
        elif i - 1 in keys:
            chart_key, chart_known = keys[i - 1], True
        highs = [p for p in history.get(i - 1, {}).get(2, ()) if p["kind"] == "H"]
        if chart_known:
            # Supplement the still-known formal pressure; a newer, lower source
            # high must not silently lower that unresolved structural hurdle.
            candidates = highs[-1:] + ([chart_key] if chart_key is not None else [])
            highs = [max(candidates, key=lambda p: p["value"])] if candidates else []
        if attack is not None and resisted and tracked_level is not None:
            # Promoting the resistance rally's own high must not erase the
            # unresolved breakout that produced it.
            highs = [tracked_level]
        if not highs:
            current_key, attack, resisted = None, None, False
            resolved_at = None
            continue
        level = highs[-1]
        key = (level["index"], level["value"])
        if key != current_key:
            current_key, attack, resisted = key, None, False
            resolved_at = None
            tracked_level = level
        bar, prev = bars[i], bars[i - 1]
        upper = bar.high - max(bar.open, bar.close)
        # A dominant upper wick can express supply despite a long lower wick
        # diluting its fraction of the entire range.
        resistance = observe_resistance(
            prev, bar, attack_direction=Direction.UP, shadow_policy=ShadowPolicy(0.5)
        ).detected is True or (upper > abs(bar.close - bar.open) and upper >= (bar.high - bar.low) / 3 and upper > 0)
        if attack is None and prev.high <= level["value"] < bar.high:
            attack, record, defense = i, bar.high, min(bar.low, prev.close)
            resisted = resistance
        if attack is None:
            continue
        if i == attack + 1:
            resisted |= resistance
        if resisted:
            confirmed = (
                i >= attack + 2
                and not resistance
                and bar.close > bar.open
                and bar.close > record
                and bar.low >= defense
            )
            if not confirmed:
                blocked[i] = dict(
                    secondary_high_date=bars[level["index"]].timestamp.date().isoformat(),
                    secondary_high=level["value"],
                    secondary_attack_date=bars[attack].timestamp.date().isoformat(),
                    secondary_resistance_high=record,
                    secondary_defense=defense,
                    secondary_current_resistance=resistance,
                )
            else:
                resisted = False
                resolved_at = i
                resolved_high = record
        if include_resolved and resolved_at is not None:
            blocked[i] = dict(
                secondary_resistance_resolved=True,
                secondary_high_date=bars[level["index"]].timestamp.date().isoformat(),
                secondary_high=level["value"],
                secondary_attack_date=bars[attack].timestamp.date().isoformat(),
                secondary_resolution_date=bars[resolved_at].timestamp.date().isoformat(),
                secondary_resistance_high=resolved_high,
                secondary_defense=defense,
                secondary_confirmation_close=bars[resolved_at].close,
            )
        record = max(record, bar.high)
        if bar.low < defense:
            attack, resisted = None, False
            resolved_at = None
    yield end + 1, blocked


def secondary_resistance_history(
    bars: Sequence[Bar], history: dict, *, key_events=(), include_resolved=False
) -> dict[int, dict]:
    """Batch and live callers consume the same causal resistance reducer."""
    stream = SecondaryResistanceStream(bars, include_resolved=include_resolved)
    try:
        return deepcopy(stream.advance(len(bars) - 1, history, key_events=key_events))
    finally:
        stream.close()


class SecondaryResistanceStream:
    """Advance one market day; replay only revised evidence already consumed.

    Results are borrowed until the next advance. The hierarchy's own immutable
    past dates may skip comparison; public history and key events stay checked.
    """

    def __init__(
        self, bars: Sequence[Bar], *, include_resolved: bool = True,
        start_index: int = 0, end_index: int | None = None,
    ) -> None:
        self._bars = tuple(bars)
        self.start_index = start_index
        self.end_index = observation_window_end(len(self._bars), start_index, end_index)
        self._include_resolved = include_resolved
        self._history: dict[int, dict] = {}
        self._keys: dict[int, dict | None] = {}
        self._owner: object | None = None
        self._index = start_index - 1
        self._closed = False
        self._cursor = self._new_cursor()

    def _new_cursor(self) -> HistoryCursor[dict[int, dict]]:
        return HistoryCursor(_secondary_resistance_days(
            self._bars, self._history, self._keys, include_resolved=self._include_resolved,
            start_index=self.start_index, end_index=self.end_index if self._bars else None,
        ), {})

    @staticmethod
    def _row(levels: dict) -> dict:
        # These are the complete fields this reducer observes. Proof payloads
        # and other levels do not affect resistance and need no second copy.
        return {2: tuple({field: point[field] for field in ('kind', 'index', 'value')}
                         for point in levels.get(2, ()))}

    @staticmethod
    def _key_rows(events) -> dict[int, dict | None]:
        return {event['bar_index']: (
            None if event['key'] is None else
            {field: event['key'][field] for field in ('index', 'value')}
        ) for event in events if event['event'] == 'hierarchy_resistance_key'}

    def advance(
        self, index: int, history: dict[int, dict], key_events=(), *,
        _history_owner: object | None = None,
    ) -> dict[int, dict]:
        if self._closed:
            raise ValueError('secondary resistance stream is closed')
        minimum = self.start_index if self.start_index else -1
        if type(index) is not int or not minimum <= index <= self.end_index:
            raise ValueError('secondary resistance requires a date inside its observation window')
        from .hierarchy_stream import HierarchyHistoryStream

        owner = (_history_owner if isinstance(_history_owner, HierarchyHistoryStream)
                 and history is _history_owner.history else None)
        owned = owner is not None and owner is self._owner and index >= self._index
        changed = index < self._index
        first_snapshot = max(0, self.start_index - 1)
        for day in range(max(first_snapshot, self._index if owned else first_snapshot), index):
            row = self._row(history.get(day, {}))
            if day < self._index and self._history.get(day) != row:
                changed = True
            self._history[day] = row

        keys = self._key_rows(key_events)
        if self.start_index:
            previous_key = max((day for day in keys if day < self.start_index), default=None)
            keys = {day: key for day, key in keys.items()
                    if day >= self.start_index or day == previous_key}
        # Day i observes only day i-1. New future/current keys need no replay;
        # edits, removals and late arrivals for consumed days must restart it.
        consumed_keys = {day: key for day, key in self._keys.items() if day < self._index}
        revised_keys = {day: key for day, key in keys.items() if day < self._index}
        changed |= consumed_keys != revised_keys
        self._keys.clear()
        self._keys.update(keys)
        if changed:
            self._cursor.close()
            self._cursor = self._new_cursor()
        result = self._cursor.advance(index)
        self._index, self._owner = index, owner
        return result

    def close(self) -> None:
        self._cursor.close()
        self._history.clear()
        self._keys.clear()
        self._owner = None
        self._closed = True
