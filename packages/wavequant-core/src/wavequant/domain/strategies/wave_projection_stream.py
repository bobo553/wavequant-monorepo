"""Request-owned continuations of the canonical wave target reducer."""

from collections.abc import Sequence
from typing import Literal

from ..market_structure.observation_window import observation_window_end
from ..market_structure.wave_projection import WaveProjectionEvent, WaveProjectionSetup, _wave_projection_days
from ..models.model import Bar
from ..models.validated_bars import ValidatedBars
from .history_cursor import HistoryCursor


class WaveProjectionHistory:
    """资格窗口内独立推进目标；重新开窗必须新建实例，不继承空档的触达。"""

    def __init__(self, bars: Sequence[Bar], *, start_index: int = 0, end_index: int | None = None) -> None:
        self._bars = ValidatedBars(bars)
        self.start_index = start_index
        self.end_index = observation_window_end(len(self._bars), start_index, end_index)
        self._streams: dict[
            tuple[WaveProjectionSetup, str, str | None, int], tuple[int, HistoryCursor[list[WaveProjectionEvent]]]
        ] = {}

    def advance(
        self, setup: WaveProjectionSetup, end: int, *,
        target_policy: Literal['named_stages', 'nearest_box'] = 'named_stages',
        source_identity: str | None = None,
        measurement_start: int | None = None,
    ) -> tuple[WaveProjectionEvent, ...]:
        if type(end) is not int or not self.start_index <= end <= self.end_index:
            raise ValueError('wave projection requires a date inside its observation window')
        if target_policy not in ('named_stages', 'nearest_box'):
            raise ValueError('explicit supported target policy required')
        if source_identity is not None and not isinstance(source_identity, str):
            raise ValueError('wave projection source identity must be a string')
        if measurement_start is not None and (type(measurement_start) is not int
                or not 0 <= measurement_start <= self.end_index):
            raise ValueError('wave projection requires an available observation start')
        start = max(self.start_index, measurement_start if measurement_start is not None else self.start_index)
        if setup.attack_index > end or start > end:
            return ()
        key = setup, target_policy, source_identity, start
        previous = self._streams.pop(key, None)
        if previous is not None and end < previous[0]:
            previous[1].close()
            previous = None
        cursor = (previous[1] if previous is not None else
                  HistoryCursor(_wave_projection_days(self._bars, setup, target_policy=target_policy,
                      start_index=start, end_index=self.end_index), []))
        self._streams[key] = end, cursor
        # The request's finite candidate set owns these continuations. An LRU
        # smaller than that set would replay every candidate on every close.
        return tuple(cursor.advance(end))

    def close(self) -> None:
        for _, cursor in self._streams.values():
            cursor.close()
        self._streams.clear()
