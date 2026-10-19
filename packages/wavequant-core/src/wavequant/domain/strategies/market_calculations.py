"""可按需调用的请求级折线、趋势线及六态计算，共享本次历史与事件缓存。"""

from collections.abc import Callable, Iterator, Mapping, Sequence
from bisect import bisect_left
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from time import monotonic
from typing import TYPE_CHECKING, Literal, cast

if TYPE_CHECKING:
    from ..market_structure.trend_lines import _TrendPermissionCache
    from .hierarchy_stream import HierarchyHistoryStream

from ..market_structure.polyline_lifecycle import PolylineLifecycleSource, polyline_lifecycle_rows
from ..market_structure.trend_history_changes import TrendChangeRows
from ..market_structure.trend_market_index import market_prefix
from ..market_structure.trend_lifecycle import TrendLifecycle
from ..market_structure.bull_alternation_confirmation import AlternationPolicy
from ..models.model import Bar
from .chart_drawing_history import ChartDrawingFrame, ChartDrawingHistory
from .market_timing import MarketTiming
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
        self._shared_hierarchy: 'HierarchyHistoryStream | None' = None
        self._shared_buy_level: int | None = None
        self._shared_permission_days: dict[int, tuple[dict[str, object], ...]] = {}
        self._live_lifecycle_days: dict[int, TrendLifecycle] = {}
        self.timing = MarketTiming()

    def _require_open(self) -> None:
        if self._frames.closed:
            raise RuntimeError('market calculations are closed outside their resource scope')

    def _permission_cache_for(self, bars: Sequence[Bar]) -> '_TrendPermissionCache':
        """同一请求复用有界许可模板；分钟尾行情修订使用独立市场索引。"""
        from ..market_structure.trend_lines import _TrendPermissionCache

        self._require_open()
        if self._strategy_permission_cache is None:
            self._strategy_permission_cache = _TrendPermissionCache(self._bars)
        if type(bars) is tuple and bars is self._bars:
            return self._strategy_permission_cache
        if self._strategy_permission_cache.covers(bars):
            return self._strategy_permission_cache
        return _TrendPermissionCache(bars)

    def prepare_shared_hierarchy(
        self, *, n_enabled: bool = False, buy_level: int | None = None,
    ) -> 'HierarchyHistoryStream':
        """在首个交易日前绑定共同日期历史；策略适配器不再计算另一套趋势。"""
        from .hierarchy_stream import HierarchyHistoryStream

        self._require_open()
        if buy_level is not None and (type(buy_level) is not int or buy_level not in (1, 2, 3)):
            raise ValueError('buy interval level must be 1, 2 or 3')
        if self._shared_hierarchy is not None:
            if self._shared_hierarchy.n_enabled != n_enabled or self._shared_buy_level != buy_level:
                raise ValueError('shared hierarchy confirmation mode cannot change within a request')
            return self._shared_hierarchy
        if self._published_index >= 0:
            raise ValueError('shared hierarchy must be bound before the first market day')
        self._shared_hierarchy = HierarchyHistoryStream(self._bars, n_enabled=n_enabled, shared=True)
        self._shared_buy_level = buy_level
        return self._shared_hierarchy

    def _shared_permissions_at(self, index: int, *, n_enabled: bool) -> tuple[dict[str, object], ...] | None:
        """只借用已真实生成的日期视图；分钟修订和未缓存旧日走各自因果输入。"""
        self._require_open()
        if self._shared_hierarchy is None or self._shared_hierarchy.n_enabled != n_enabled:
            return None
        return self._shared_permission_days.get(index)

    def _publish_shared_permissions(self, day: MarketDay) -> tuple[dict[str, object], ...]:
        from ..market_structure.trend_lines import attach_trend_permissions, high_low_points_from_strokes

        assert self._shared_hierarchy is not None
        market = market_prefix(self._bars, day.frame.index + 1)
        cache = self._permission_cache_for(market)
        n_enabled = self._shared_hierarchy.n_enabled
        hierarchy_only = (not n_enabled and self._shared_buy_level is not None
                          and not day.permits_buy(self._shared_buy_level))
        views: list[dict[str, object]] = []
        source: list[dict[str, object]] | None = None
        for number, prepared in enumerate(day.levels, 1):
            lifecycle = self._lifecycles.get(number)
            pending = lifecycle.pending_bull_paths(number) if lifecycle is not None else frozenset()
            view = attach_trend_permissions(prepared, market,
                n_target_trend_confirmation_enabled=n_enabled,
                qualified_source_points=source, _permission_cache=cache, _owned_candidates=True,
                _n_confirmation_paths=pending, _hierarchy_only=hierarchy_only)
            views.append(view)
            if n_enabled and number < 3:
                source = high_low_points_from_strokes(view['qualified_strokes'])
        published = tuple(views)
        # 窗口结束日或旧日若需要交易附件，读取方按原日期重新构造完整视图。
        if not hierarchy_only:
            self._shared_permission_days[day.frame.index] = published
        # 完整证据树只保留当前/前日，供同日交易与末日检查点重放借用。
        # 累积策略历史另存轻量日期/价格字段，不把大树复制到每个历史日。
        for index in tuple(self._shared_permission_days):
            if index < day.frame.index - 1:
                del self._shared_permission_days[index]
        # 交易附件保留上面的完整视图；层级历史只接收自身的价格/知识时钟投影。
        # 有效交易区间内也保持同一投影版本，避免切换模式重新物化冻结前缀。
        from ..market_structure.trend_point_version import PointVersion

        projected_views: list[dict[str, object]] = []
        for number, (prepared, view) in enumerate(zip(day.levels, published, strict=True), 1):
            cutoffs = cast(Mapping[str, int | None], prepared['_path_market_end_indices'])
            strokes: list[dict[str, object]] = []
            for stroke in cast(Sequence[Mapping[str, object]], view['qualified_strokes']):
                path = cast(str, stroke['source_path'])
                cutoff = cutoffs[path]
                if cutoff is not None and day.frame.index >= cutoff:
                    continue
                points = cast(Sequence[Mapping[str, object]], stroke['points'])
                projected = (points if type(points) is PointVersion else
                             cache.hierarchy_projection(number, path, points))
                strokes.append(dict(source_path=path, points=projected))
            projected_views.append(dict(trend_level=number, qualified_strokes=strokes))
        return tuple(projected_views)

    def _close_shared_history(self) -> None:
        if self._shared_hierarchy is not None:
            self._shared_hierarchy.close()
        self._shared_permission_days.clear()
        self._live_lifecycle_days.clear()

    def _live_lifecycle_at(self, index: int) -> TrendLifecycle | None:
        """借用当前/前日的只读状态；需要续算的消费者必须先 copy。"""
        self._require_open()
        return self._live_lifecycle_days.get(index) if self.reading_live_day else None

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
            # 升级证明在三级之间收敛；首次请求统一完成，后续级别只读同一份结果。
            self._cascade(3)
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
                           frame: ChartDrawingFrame | None = None,
                           source_events: Sequence[Mapping[str, object]] = ()) -> list[dict[str, object]]:
        """趋势入口内部在当天更新六态；不重新遍历已处理行情。"""
        self._require_full_history()
        lifecycle = self._lifecycles.get(level)
        if lifecycle is None:
            lifecycle = self._lifecycles[level] = TrendLifecycle()
        events = lifecycle.advance((row,), self._bars, index, self._dates,
            polyline=frame.raw if frame is not None else (),
            polyline_path=f'lecture-{frame.epoch}' if frame is not None else None,
            confirmation_policy=self.confirmation_policy, source_events=source_events,
            # 折线适配器也只替换版本，新点的观察在发布前完成；旧点不再改写。
            _owned_structure=True,
            _market_index=(self._permission_cache_for(self._bars)._market_index
                           if type(self._bars) is tuple else None))
        self._daily_events[level] = events
        if level in self._eligibility:
            for event in events:
                changed = self._eligibility[level].observe(event)
                if changed is not None:
                    event['buy_eligible'] = changed
            events.extend(self._eligibility[level].close_on_confirmation_gap_fill(self._bars, index))
            events.extend(self._eligibility[level].close_on_reversal_low_break(row, self._bars, index, self._dates))
        # 资格关闭与本级结构失效分开；下一日继续寻找新确认，旧证明不能直接重开。
        for event in events:
            if (event.get('buy_eligible') is False and isinstance(event.get('source_path'), str)
                    and not any(window.source_path == event['source_path']
                                for window in self._eligibility[level].active_intervals)):
                lifecycle.resume_qualification_search(level, cast(str, event['source_path']), index, self._bars)
        self._pending_events.setdefault(level, []).extend(events)
        return events

    def _publish_day(self, frame: ChartDrawingFrame, levels: Sequence[Mapping[str, object]]) -> None:
        """当日三级及资格全部推进后交付，不再次读取已经写入的历史流。"""
        if self._on_day is None and self._shared_hierarchy is None:
            return
        if len(levels) != 3 or frame.index != self._published_index + 1:
            raise ValueError('market day requires three levels and consecutive dates')
        day = MarketDay(frame, tuple(levels),
            tuple(event for level in (1, 2, 3) for event in self._daily_events.get(level, ())),
            (self._eligibility[1].active, self._eligibility[2].active, self._eligibility[3].active))
        if self._on_day is not None:
            snapshot_started = monotonic()
            self._live_lifecycle_days[frame.index] = TrendLifecycle.combine(
                tuple(self._lifecycles[number] for number in (0, 1, 2, 3) if number in self._lifecycles))
            for index in tuple(self._live_lifecycle_days):
                if index < frame.index - 1:
                    del self._live_lifecycle_days[index]
            self.timing.publication += monotonic() - snapshot_started
        if self._shared_hierarchy is not None:
            permissions_started = monotonic()
            self._shared_hierarchy.advance_market_day(day, self._publish_shared_permissions(day))
            self.timing.permissions += monotonic() - permissions_started
        if self._on_day is not None:
            self._on_day(day)
        self._published_index = frame.index

    def trading_intervals(self, level: int) -> tuple[TradingInterval, ...]:
        """借用本级确认过程已经生成的交易区间，不重算逐日资格。"""
        if type(level) is not int or level not in (1, 2, 3):
            raise ValueError('trading eligibility level must be 1, 2 or 3')
        self._lifecycle_event_rows(level)
        return self._eligibility[level].intervals

    def _seal_lifecycle(self, level: int) -> None:
        self._lifecycle_events[level] = tuple(self._pending_events.pop(level, ()))
        self._lifecycles.pop(level, None)

    def _save_lifecycle_checkpoint(self, index: int) -> None:
        if self._checkpoints is None:
            return
        snapshots = [self._lifecycles[level].checkpoint() for level in (0, 1, 2, 3)
                     if level in self._lifecycles]
        version = snapshots[0][0]
        rows = tuple(row for snapshot in snapshots for row in cast(tuple[object, ...], snapshot[2]))
        self._checkpoints.write_change(index, (version, index, rows))

    def _calculate_lifecycle(self, level: int) -> None:
        self._require_full_history()
        if level in self._lifecycle_events:
            return
        if level == 1 and 0 not in self._lifecycle_events:
            self._calculate_lifecycle(0)
        source_by_day: dict[int, list[Mapping[str, object]]] = {}
        for event in self._lifecycle_events.get(level - 1, ()):
            source_by_day.setdefault(cast(int, event['bar_index']), []).append(event)
        if level == 0:
            # 折线状态和行情上下文来自同一行，不能同时打开该历史的两个顺序读者。
            source = PolylineLifecycleSource(self._dates, _owned_tail=True)
            frames = self._frames.iter_rows(shared=True)
            try:
                for frame in frames:
                    self._advance_lifecycle(0, source.advance(frame), frame.index, frame=frame)
            finally:
                frames.close()
            self._seal_lifecycle(0)
            return
        rows = self._lifecycle_rows(level)
        frames = self._frames.iter_rows(shared=True)
        try:
            for (index, row), frame in zip(rows, frames, strict=True):
                self._advance_lifecycle(level, row, index, frame=frame,
                                        source_events=source_by_day.get(index, ()))
        finally:
            rows.close()
            frames.close()
        self._seal_lifecycle(level)

    def _lifecycle_event_rows(self, level: CalculationLevel) -> Sequence[dict[str, object]]:
        """内部只读账本；日期顺序与 advance 的连续行情时钟一致。"""
        self._require_full_history()
        number = normalize_calculation_level(level)
        if self.reading_live_day and number in self._pending_events:
            return self._pending_events[number]
        if number not in self._lifecycle_events:
            raise RuntimeError('calculate the requested trend before reading its lifecycle events')
        return self._lifecycle_events[number]

    def _owned_lifecycle_events(self, level: CalculationLevel) -> tuple[dict[str, object], ...]:
        """只借用已完成的内部事件，读取不会触发计算。"""
        return tuple(self._lifecycle_event_rows(level))

    def _lifecycle_events_since(self, level: CalculationLevel, index: int) -> tuple[dict[str, object], ...]:
        """只取待续算日期的事件，避免每日复制和筛选整个历史账本。"""
        if type(index) is not int or not 0 <= index <= len(self._bars):
            raise ValueError('lifecycle event date must be within the market history')
        rows = self._lifecycle_event_rows(level)
        start = bisect_left(rows, index, key=lambda event: cast(int, event['bar_index']))
        return tuple(rows[start:])

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
