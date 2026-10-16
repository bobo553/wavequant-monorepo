"""可按需调用的请求级折线、趋势线及六态计算，共享本次历史与事件缓存。"""

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, cast

if TYPE_CHECKING:
    from ..market_structure.trend_lines import _TrendPermissionCache

from ..market_structure.polyline_lifecycle import polyline_lifecycle_rows
from ..market_structure.trend_history_changes import TrendChangeRows
from ..market_structure.trend_lifecycle import TrendLifecycle
from ..market_structure.bull_alternation_confirmation import AlternationPolicy
from ..models.model import Bar
from .chart_drawing_history import ChartDrawingFrame, ChartDrawingHistory
from .trading_intervals import TradingEligibility, TradingInterval


CalculationLevel = int | Literal['折线', 'polyline']


@dataclass(frozen=True, slots=True)
class MarketDay:
    """当天联动完成后借用的证据；消费者不能修改或跨日保留嵌套容器。"""

    frame: ChartDrawingFrame
    levels: tuple[Mapping[str, object], ...]
    lifecycle_events: tuple[Mapping[str, object], ...]
    buy_eligible: tuple[bool, bool, bool]

    def permits_buy(self, level: int) -> bool:
        if type(level) is not int or level not in (1, 2, 3):
            raise ValueError('buy interval level must be 1, 2 or 3')
        return self.buy_eligible[level - 1]


def normalize_calculation_level(level: object) -> int:
    """把折线别名归一为 0；未知值、bool 和非整数级别显式拒绝。"""
    if isinstance(level, str) and level in ('折线', 'polyline'):
        return 0
    if type(level) is int and level in (0, 1, 2, 3):
        return level
    raise ValueError('calculation level must be 折线, polyline, 0, 1, 2 or 3')


class MarketCalculations:
    """一次行情计算的函数对象；几何按依赖补齐，同级六态只生成一次。

    几何历史由外层资源作用域持有，返回的历史对象也只在该作用域内有效。
    六态公开结果各自拥有可修改副本，不把调用方变更写回共享事件缓存。
    本对象用于同一请求内的顺序调用，不跨请求或线程共享可变回放状态。
    """

    def __init__(
        self, bars: Sequence[Bar], frames: ChartDrawingHistory,
        levels: list[TrendChangeRows[dict[str, object]]], *,
        draw: Callable[[], None], trend: Callable[[int], None],
        cascade: Callable[[int], None] | None = None,
        on_day: Callable[[MarketDay], None] | None = None,
    ) -> None:
        self._bars = bars
        self._frames = frames
        self._levels = levels
        self._draw = draw
        self._trend = trend
        self._cascade = cascade
        self._on_day = on_day
        self._published_index = -1
        self.reading_live_day = False
        self._daily_events: dict[int, list[dict[str, object]]] = {}
        self._drawn = False
        self._dates = {bar.timestamp.date().isoformat(): index for index, bar in enumerate(bars)}
        self._lifecycle_events: dict[int, tuple[dict[str, object], ...]] = {}
        self._lifecycles: dict[int, TrendLifecycle] = {}
        self.confirmation_policy = AlternationPolicy()
        self._pending_events: dict[int, list[dict[str, object]]] = {}
        self._eligibility = {level: TradingEligibility(level) for level in (1, 2, 3)}
        self._checkpoints: TrendChangeRows[tuple[object, ...]] | None = None
        self._chart_prefix_owner = object()
        self._strategy_permission_cache: '_TrendPermissionCache | None' = None

    def _require_open(self) -> None:
        if self._frames.closed:
            raise RuntimeError('market calculations are closed outside their resource scope')

    def _permission_cache_for(self, bars: Sequence[Bar]) -> '_TrendPermissionCache':
        """同一请求复用有界许可模板；分钟尾行情修订使用独立市场索引。"""
        from ..market_structure.trend_lines import _TrendPermissionCache

        self._require_open()
        if self._strategy_permission_cache is None:
            self._strategy_permission_cache = _TrendPermissionCache(self._bars)
        if self._strategy_permission_cache.covers(bars):
            return self._strategy_permission_cache
        return _TrendPermissionCache(bars)

    @property
    def frames(self) -> ChartDrawingHistory:
        """借用本次折线历史；访问本属性不会提前计算折线。"""
        self._require_open()
        return self._frames

    @property
    def levels(self) -> tuple[TrendChangeRows[dict[str, object]], ...]:
        """已完成各级历史的只读容器快照，不暴露内部可变列表。"""
        self._require_open()
        return tuple(self._levels)

    def calculate_polyline(self) -> ChartDrawingHistory:
        """通过原有折线计算入口生成历史；本次请求再次调用时复用结果。"""
        self._require_open()
        if not self._drawn:
            self._draw()
            self._require_open()
            self._drawn = True
        return self._frames

    def calculate_trend_line(self, level: int) -> TrendChangeRows[dict[str, object]]:
        """同轮推进所需层级的趋势及六态；返回时两种结果均已保存。"""
        self._require_open()
        if type(level) is not int or level not in (1, 2, 3):
            raise ValueError('trend level must be 1, 2 or 3; use calculate_polyline() for the polyline')
        if self._cascade is not None:
            self._cascade(level)
            self._drawn = True
            if self._levels[level - 1].closed:
                raise RuntimeError('trend calculation history has been closed')
            return self._levels[level - 1]
        self.calculate_polyline()
        while len(self._levels) < level:
            next_level = len(self._levels) + 1
            self._trend(next_level)
            self._require_open()
            if len(self._levels) != next_level:
                raise RuntimeError('trend calculation must append exactly its immediate next level')
            self._calculate_lifecycle(next_level)
        history = self._levels[level - 1]
        if history.closed:
            raise RuntimeError('trend calculation history has been closed')
        return history

    def _require_full_history(self) -> None:
        self._require_open()
        if self._frames.start != 0 or self._frames.row_count != len(self._bars):
            raise ValueError('lifecycle calculation requires complete history starting at market index 0')

    def _advance_lifecycle(self, level: int, row: Mapping[str, object], index: int, *,
                           frame: ChartDrawingFrame | None = None) -> list[dict[str, object]]:
        """趋势入口内部在当天更新六态；不重新遍历已处理行情。"""
        self._require_full_history()
        lifecycle = self._lifecycles.get(level)
        if lifecycle is None:
            lifecycle = self._lifecycles[level] = TrendLifecycle()
        events = lifecycle.advance((row,), self._bars, index, self._dates,
            polyline=frame.raw if frame is not None else (),
            polyline_path=f'lecture-{frame.epoch}' if frame is not None else None,
            confirmation_policy=self.confirmation_policy)
        self._daily_events[level] = events
        if level in self._eligibility:
            for event in events:
                changed = self._eligibility[level].observe(event)
                if changed is not None:
                    event['buy_eligible'] = changed
            events.extend(self._eligibility[level].close_on_confirmation_gap_fill(self._bars, index))
            events.extend(self._eligibility[level].close_on_reversal_low_break(row, self._bars, index, self._dates))
        self._pending_events.setdefault(level, []).extend(events)
        return events

    def _publish_day(self, frame: ChartDrawingFrame, levels: Sequence[Mapping[str, object]]) -> None:
        """当日三级及资格全部推进后交付，不再次读取已经写入的历史流。"""
        if self._on_day is None:
            return
        if len(levels) != 3 or frame.index != self._published_index + 1:
            raise ValueError('market day requires three levels and consecutive dates')
        self._on_day(MarketDay(frame, tuple(levels),
            tuple(event for level in (1, 2, 3) for event in self._daily_events.get(level, ())),
            (self._eligibility[1].active, self._eligibility[2].active, self._eligibility[3].active)))
        self._published_index = frame.index

    def trading_intervals(self, level: int) -> tuple[TradingInterval, ...]:
        """借用本级确认过程已经生成的交易区间，不重算逐日资格。"""
        if type(level) is not int or level not in (1, 2, 3):
            raise ValueError('trading eligibility level must be 1, 2 or 3')
        self._owned_lifecycle_events(level)
        return self._eligibility[level].intervals

    def _seal_lifecycle(self, level: int) -> None:
        self._lifecycle_events[level] = tuple(self._pending_events.pop(level, ()))
        self._lifecycles.pop(level, None)

    def _save_lifecycle_checkpoint(self, index: int) -> None:
        if self._checkpoints is None:
            return
        snapshots = [self._lifecycles[level].checkpoint() for level in (1, 2, 3)]
        version = snapshots[0][0]
        rows = tuple(row for snapshot in snapshots for row in cast(tuple[object, ...], snapshot[2]))
        self._checkpoints.write_change(index, (version, index, rows))

    def _calculate_lifecycle(self, level: int) -> None:
        self._require_full_history()
        if level in self._lifecycle_events:
            return
        rows = self._lifecycle_rows(level)
        frames = self._frames.iter_rows(shared=True)
        try:
            for (index, row), frame in zip(rows, frames, strict=True):
                self._advance_lifecycle(level, row, index, frame=frame)
        finally:
            rows.close()
            frames.close()
        self._seal_lifecycle(level)

    def _owned_lifecycle_events(self, level: CalculationLevel) -> tuple[dict[str, object], ...]:
        """只借用已完成的内部事件，读取不会触发计算或扫描历史。"""
        self._require_full_history()
        number = normalize_calculation_level(level)
        if self.reading_live_day and number in self._pending_events:
            return tuple(self._pending_events[number])
        if number not in self._lifecycle_events:
            raise RuntimeError('calculate the requested trend before reading its lifecycle events')
        return self._lifecycle_events[number]

    def _lifecycle_rows(self, level: int) -> Iterator[tuple[int, dict[str, object]]]:
        if level == 0:
            frames = self._frames.iter_rows(shared=True)
            try:
                yield from polyline_lifecycle_rows(frames, self._dates)
            finally:
                frames.close()
        else:
            rows = self._levels[level - 1].iter_rows(shared=True)
            try:
                yield from enumerate(rows)
            finally:
                rows.close()

    def history_events(self, level: CalculationLevel) -> tuple[dict[str, object], ...]:
        """返回该级全部六态、候选及撤销事件的独立副本，保留真实首次可知日。"""
        self._require_full_history()
        number = normalize_calculation_level(level)
        if number == 0 and number not in self._lifecycle_events:
            self.calculate_polyline()
            self._calculate_lifecycle(0)
        return deepcopy(self._owned_lifecycle_events(level))


@contextmanager
def market_calculations(
    bars: Sequence[Bar], *, stage: Callable[[str], None] | None = None,
    on_day: Callable[[MarketDay], None] | None = None,
) -> Iterator[MarketCalculations]:
    """建立完整行情的按需计算作用域；退出时释放折线及趋势临时历史。"""
    from .chart_entry_history import _chart_calculation_scope

    with _chart_calculation_scope(
        bars, resume_start=0, closed=(), previous_epoch=None, previous_raw=(), _stage=stage, _on_day=on_day,
    ) as calculations:
        yield calculations
