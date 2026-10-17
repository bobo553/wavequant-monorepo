"""Share request-owned target risk reducers between decisions and the account."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from ..models.model import Bar
from .five_top_entry import FiveTopEntryHistory
from .ten_full_entry import RetracementAnchor, TenFullEntryHistory
from .two_t_resistance import TwoTResistanceHistory


@dataclass(frozen=True)
class DailyRiskHistory:
    target_resistance: dict[int, dict[str, object]]
    five_top_entry: dict[int, dict[str, object]]
    ten_full_entry: dict[int, dict[str, object]]


class DailyRiskHistories:
    """Keep one source and one reducer for each distinct rule configuration."""

    def __init__(
        self, bars: Sequence[Bar], *, breakout_window: int, retracement_ratio: float,
        anchor: RetracementAnchor, timed_half: bool = False, start_index: int = 0,
    ) -> None:
        self.bars = tuple(bars)
        self.start_index = start_index
        self.five_top = FiveTopEntryHistory(self.bars, start_index=start_index)
        self.ten_full = TenFullEntryHistory(self.bars, breakout_window=breakout_window,
            retracement_ratio=retracement_ratio, anchor=anchor, timed_half=timed_half, start_index=start_index)
        self.two_t_streams: dict[tuple[str, float], TwoTResistanceHistory] = {}

    def two_t(
        self, index: int, events: Sequence[Mapping[str, object]], *, reduction_fraction: float = .8,
        channel: str = 'strategy',
    ) -> dict[int, dict[str, object]]:
        if channel not in ('strategy', 'account'):
            raise ValueError('unknown target risk fact channel')
        key = channel, reduction_fraction
        stream = self.two_t_streams.get(key)
        if stream is None:
            stream = TwoTResistanceHistory(self.bars, reduction_fraction=reduction_fraction,
                start_index=self.start_index)
            self.two_t_streams[key] = stream
        return stream.advance(index, events)

    def advance(self, index: int, events: Sequence[Mapping[str, object]]) -> DailyRiskHistory:
        projections = sorted((event for event in events
            if str(event.get('event', '')).startswith('wave_projection_')),
            key=lambda event: cast(int, event['bar_index']))
        return DailyRiskHistory(
            self.two_t(index, events),
            self.five_top.advance(index, projections),
            self.ten_full.advance(index, projections),
        )
