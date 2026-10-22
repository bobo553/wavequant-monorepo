"""Advance market facts and hand each completed prefix to the daily consumer.

Prepared rows belong to one request and exact bar sequence. Strategy consumers
read the same dated rows, never slices of the final chart. Temporary streams are
bounded and are closed with the strategy even if calculation fails.
"""

from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from typing import TYPE_CHECKING

from ..market_structure.calculation_plan import resolve_calculation_plan
from ..market_structure.bull_alternation_confirmation import AlternationPolicy
from ..market_structure.calculation_steps import calculation_steps
from ..market_structure.n_shape import NObservation, NSetup
from ..market_structure.polyline import ReversalPoint
from ..market_structure.trend_history_changes import TrendChangeRows
from ..market_structure.trend_evidence_reuse import _completion_projection_scope
from ..market_structure.wave_projection import WaveProjectionEvent, WaveProjectionSetup
from ..market_state.market_regime import RegimeObservation
from ..models.model import Bar
from .trading_intervals import TradingInterval, interval_days
from .chart_drawing_history import ChartDrawingHistory
from .market_calculations import MarketCalculations, MarketDay
from .eligible_polyline_n import EligibleNSource, EligiblePolylineN
from .polyline_target_stream import PolylineTargetStream
from .structural_n_stream import StructuralNStream
from .market_preview import publish_market_preview

if TYPE_CHECKING:
    from .backtest_history_reuse import BacktestHistoryReuse
    from .hierarchy_stream import HierarchyHistoryStream
    from .integrated_strategy import SystemStrategy


@dataclass
class BacktestAnalysis:
    """Owned stage streams plus bounded memo tables, never a global history cache."""
    bars: tuple[Bar, ...]
    frames: ChartDrawingHistory
    levels: tuple[TrendChangeRows[dict[str, object]], ...]
    pivots: tuple[dict[int, tuple[ReversalPoint, ...]], dict[int, int], dict[int, int], set[int]]
    n_observations: dict[tuple[NSetup, int, int], NObservation] = field(default_factory=dict)
    regimes: dict[tuple[NSetup, int, int], RegimeObservation] = field(default_factory=dict)
    projections: dict[tuple[WaveProjectionSetup, int], tuple[WaveProjectionEvent, ...]] = field(default_factory=dict)
    lifecycle_events: list[dict[str, object]] = field(default_factory=list)
    trading_intervals: tuple[TradingInterval, ...] = ()
    buy_days: tuple[bool, ...] = ()
    scan_days: tuple[bool, ...] = ()
    calculations: MarketCalculations | None = None
    n_sources: list[EligibleNSource] = field(default_factory=list)
    # Exclusive closure dates bound trading observers without changing anchor clocks.
    n_window_ends: dict[tuple[str, int], int] = field(default_factory=dict)
    scoped_n_observations: dict[tuple[EligibleNSource, int], NObservation] = field(default_factory=dict)
    hierarchy: 'HierarchyHistoryStream | None' = None
    structural_n_events: list[dict[str, object]] = field(default_factory=list)


@contextmanager
def prepare_backtest_analysis(
    bars: Sequence[Bar], config: 'SystemStrategy', *,
    reuse: 'BacktestHistoryReuse | None' = None,
    stage: Callable[[str], None] | None = None,
    progress: Callable[[int], None] | None = None,
    on_day: Callable[[MarketDay, BacktestAnalysis], None] | None = None,
) -> Iterator[BacktestAnalysis]:
    from .chart_entry_history import _staged_chart_frames
    from .lecture_strategy import LecturePivotState

    market = tuple(bars)
    plan = resolve_calculation_plan()
    steps = calculation_steps(market)
    if steps is not None:
        steps.set_plan(plan)
    analysis: BacktestAnalysis | None = None
    calculations: MarketCalculations | None = None
    pivot_state = LecturePivotState()
    n_sources = EligiblePolylineN(config.buy_interval_level,
        folded_window=(config.structure_window
            if config.buy_point_definition == 'whole_flip_wave_v3' and config.pivot_mode == 'lecture_causal'
            else None))
    targets = PolylineTargetStream(bars, config,
        publish=(lambda row: steps.append(2, row)) if steps is not None else None)
    structures = StructuralNStream(bars, pattern_ttl=config.pattern_ttl)
    structural_sources = EligiblePolylineN(config.buy_interval_level,
        folded_window=config.structure_window, structural=True)
    last_progress = -1

    def report(number: int, message: str) -> None:
        if stage is not None:
            stage(message)

    def after_drawing(frames: ChartDrawingHistory) -> None:
        nonlocal analysis
        if analysis is None:
            raise RuntimeError('market analysis must be bound before drawing')

    def require_analysis() -> BacktestAnalysis:
        if analysis is None:
            raise RuntimeError('market analysis requires a completed drawing stage')
        return analysis

    def calculate_n_targets() -> None:
        current = require_analysis()
        if steps is not None:
            steps.begin(2)
        report(2, '保存随折线更新的 N 目标')
        # Episode endings are metadata, never permission to inspect a future bar.
        end = len(market) - 1
        for i in range(end, -1, -1):
            if i in pivot_state.blocked:
                end = i - 1
            current.pivots[2][i] = end
        targets.finish()
        current.n_observations = targets.observations
        current.scoped_n_observations = targets.scoped_observations
        if steps is not None:
            steps.complete(2)

    def prepare_strategy_inputs() -> None:
        current = require_analysis()
        report(24, '读取所选级别已确认资格，在有效区间内计算 B/S')
        if steps is not None:
            steps.begin(24)
        if current.calculations is None:
            raise RuntimeError('trading eligibility requires prepared trend confirmations')
        current.trading_intervals = current.calculations.trading_intervals(config.buy_interval_level)
        current.buy_days = interval_days(current.trading_intervals, len(market))
        current.scan_days = interval_days(current.trading_intervals, len(market), include_end=True)
        if steps is not None:
            steps.append(24, dict(event='selected_level_trading_intervals', trend_level=config.buy_interval_level,
                intervals=[asdict(interval) for interval in current.trading_intervals]))

    analysis_actions: dict[int, Callable[[], None]] = {2: calculate_n_targets, 24: prepare_strategy_inputs}

    def bind_calculations(current: MarketCalculations) -> None:
        nonlocal calculations, analysis
        calculations = current
        current.confirmation_policy = AlternationPolicy(config.structure_window, config.pattern_ttl,
            config.volume_lookback, config.shadow_fraction, config.strict_n_attack_quality,
            config.squeeze_pullback_entries)
        analysis = BacktestAnalysis(market, current.frames, (),
            (pivot_state.snapshots, pivot_state.epochs, {}, pivot_state.blocked), calculations=current,
            hierarchy=(current.prepare_shared_hierarchy(n_enabled=config.n_target_trend_confirmation_enabled,
                buy_level=config.buy_interval_level)
                if config.buy_point_definition == 'whole_flip_wave_v3' and config.pivot_mode == 'lecture_causal' else None))

    def advance_market_day(day: MarketDay) -> None:
        nonlocal last_progress
        current = require_analysis()
        frame = day.frame
        points = pivot_state.advance(frame.index, frame.epoch, frame.raw)
        sources = n_sources.advance(frame.index, points, day.lifecycle_events)
        structure_sources = structural_sources.advance(frame.index, points, day.lifecycle_events)
        eligible = day.permits_buy(config.buy_interval_level)
        active_scopes = {(window.source_path, window.start_index)
                         for window in n_sources.eligibility.active_intervals}
        structures.advance(frame.index, structure_sources, active_scopes=active_scopes,
                           interrupted=frame.index in pivot_state.blocked)
        current.structural_n_events = structures.events
        for window in n_sources.eligibility.intervals:
            if window.end_index is not None:
                current.n_window_ends[window.source_path, window.start_index] = window.end_index
        current.n_sources.extend(sources)
        targets.advance(frame.index, sources, interrupted=frame.index in pivot_state.blocked,
                        buy_eligible=eligible, active_scopes=active_scopes)
        current.pivots[2][frame.index] = len(market) - 1
        if frame.index in pivot_state.blocked:
            for prior in range(frame.index - 1, -1, -1):
                if prior in pivot_state.blocked:
                    break
                current.pivots[2][prior] = frame.index - 1
        if on_day is None:
            publish_market_preview(bars, day)
        else:
            assert calculations is not None
            count = frame.index + 1
            previous_eligible = current.buy_days[-1] if current.buy_days else False
            current.buy_days += (eligible,)
            current.scan_days += (eligible or previous_eligible,)
            current.lifecycle_events.extend(dict(event) for event in day.lifecycle_events)
            histories = calculations.levels
            current.frames.read_limit = count
            for history in histories:
                history.read_limit = count
            if calculations._checkpoints is not None:
                calculations._checkpoints.read_limit = count
            calculations.reading_live_day = True
            try:
                prefix = replace(current, bars=market[:count], levels=histories,
                    n_observations=targets.current_observations(),
                    scoped_n_observations=targets.current_scoped_observations(),
                    trading_intervals=calculations.trading_intervals(config.buy_interval_level))
                on_day(day, prefix)
            finally:
                calculations.reading_live_day = False
                current.frames.read_limit = None
                for history in histories:
                    history.read_limit = None
                if calculations._checkpoints is not None:
                    calculations._checkpoints.read_limit = None
        percent = (frame.index + 1) * 100 // len(market)
        if progress is not None and percent != last_progress:
            progress(percent)
            last_progress = percent

    def run_analysis_step(
        number: int, _frames: ChartDrawingHistory, levels: tuple[TrendChangeRows[dict[str, object]], ...],
    ) -> None:
        current = require_analysis()
        current.levels = levels
        action = analysis_actions.get(number)
        if action is None:
            raise ValueError(f'unsupported market analysis step: {number}')
        action()

    with _completion_projection_scope(), _staged_chart_frames(
        market, resume_start=0, closed=(), previous_epoch=None, previous_raw=(),
        _stage=stage, _after_drawing=after_drawing,
        _plan=plan, _run_step=run_analysis_step, _on_ready=bind_calculations,
        _on_day=advance_market_day,
    ) as (frames, levels):
        if analysis is None or calculations is None:
            raise RuntimeError('market analysis requires a completed drawing stage')
        analysis.levels = levels
        # 公开事件序列继续按级别、原始观察顺序交付，不能泄漏任务调度次序。
        analysis.lifecycle_events = [event for level in (1, 2, 3)
                                     for event in calculations._owned_lifecycle_events(level)]
        # 批量兼容调用在此读取区间；同日消费者已经在每个日期处理过资格。
        if on_day is None:
            analysis_actions[24]()
        yield analysis
