"""Causal daily coordination; market and decision state share one advancing date."""

from collections.abc import Callable, Sequence
from time import monotonic

from ..models.model import Bar
from ..market_structure.polyline import ReversalPoint
from ..market_structure.calculation_steps import calculation_steps
from ..market_structure.calculation_plan import resolve_calculation_plan
from ..models.validated_bars import ValidatedBars
from .backtest_analysis import BacktestAnalysis, prepare_backtest_analysis
from .integrated_strategy import DailySignalState, SystemResult, SystemStrategy
from . import integrated_strategy as policy
from .market_calculations import MarketDay
from .hierarchy_stream import HierarchyHistoryStream


def daily_signals(
    bars: Sequence[Bar], config: SystemStrategy, *,
    on_day: Callable[[MarketDay, BacktestAnalysis, SystemResult, DailySignalState], None] | None = None,
    progress: Callable[[int], None] | None = None,
    stage: Callable[[str], None] | None = None,
    minor_points: Sequence[ReversalPoint] | None = None,
) -> SystemResult:
    """Run decisions once per close, using only the market prefix available then.

    Existing shape observers retain their rule semantics and bounded prefix caches.
    They are not permitted to consume future candles or replay trading decisions.
    """
    market = ValidatedBars(bars)
    state = DailySignalState()
    if config.buy_point_definition == 'whole_flip_wave_v3' and config.pivot_mode == 'lecture_causal':
        state.hierarchy_stream = HierarchyHistoryStream(market,
            n_enabled=config.n_target_trend_confirmation_enabled)
    cache: dict = {'source_bars': tuple(market)}
    result = SystemResult([], [], {})
    last_percent = -1
    last_finished = monotonic()
    last_report = 0.0
    market_seconds = 0.0
    decision_seconds = 0.0
    account_seconds = 0.0
    steps = calculation_steps(market)
    if steps is not None:
        steps.set_plan(resolve_calculation_plan())
        if on_day is None:
            steps.begin(24)

    def advance(day: MarketDay, analysis: BacktestAnalysis) -> None:
        nonlocal result, last_percent, last_finished, last_report
        nonlocal market_seconds, decision_seconds, account_seconds
        index = day.frame.index
        started = monotonic()
        market_seconds += started - last_finished

        def report_phase(message: str) -> None:
            nonlocal last_report
            now = monotonic()
            if now - last_report < 1:
                return
            if stage is not None:
                stage(f'同日联动：{message} · 已处理{index}/{len(market)}个交易日 · '
                    f'正在计算{market[index].timestamp.date().isoformat()}')
            if progress is not None:
                progress(index * 100 // len(market))
            last_report = now

        if analysis.scan_days[-1] or state.has_buy_signal:
            report_phase('计算交易确认与 B/S')
            result = policy._generate_system_signals(analysis.bars, config, minor_points=minor_points,
                chart_history_cache=cache, _analysis=analysis, _daily=state, stage=report_phase)
        else:
            events = [dict(row,
                timestamp=market[int(row['bar_index'])].timestamp.isoformat(), symbol=market[0].symbol)
                for row in day.lifecycle_events]
            result = SystemResult(list(result.signals), [*result.audit, *events],
                dict(result.counts, rows=index + 1, trading_interval_count=len(analysis.trading_intervals),
                    interval_skipped_days=result.counts.get('interval_skipped_days', 0) + 1,
                    buy_interval_level=config.buy_interval_level,
                    long_signals=sum(signal.side == 'LONG' for signal in state.signals)))
        decision_seconds += monotonic() - started
        account_started = monotonic()
        report_phase('处理分钟成交、账户与图表快照')
        # The account consumer must finish before progress or chart data is published.
        if on_day is not None:
            on_day(day, analysis, result, state)
        account_seconds += monotonic() - account_started
        if steps is not None and on_day is None:
            steps.append(24, dict(bar_index=index,
                signals=[signal for signal in result.signals if signal.bar_index == index]))
        percent = (index + 1) * 100 // len(market)
        if percent != last_percent or monotonic() - last_report >= 1:
            if stage is not None:
                stage(f'同日联动：折线 → 一级 → 二级 → 三级 → B/S及账户 · '
                    f'已处理{index + 1}/{len(market)}个交易日 · 截至{market[index].timestamp.date().isoformat()} · '
                    f'累计耗时：折线趋势 {market_seconds:.1f}秒，'
                    f'交易结构确认 {state.hierarchy_seconds:.1f}秒，'
                    f'买卖规则 {max(0.0, decision_seconds - state.hierarchy_seconds):.1f}秒，'
                    f'账户/分钟/快照 {account_seconds:.1f}秒')
            if progress is not None:
                progress(percent)
            last_percent = percent
            last_report = monotonic()
        last_finished = monotonic()

    try:
        with prepare_backtest_analysis(market, config, on_day=advance):
            pass
    finally:
        if state.hierarchy_stream is not None:
            state.hierarchy_stream.close()
    if steps is not None:
        # Persist the final derived wave facts; this serializes existing results without replaying decisions.
        for row in result.audit:
            if row['event'].startswith(('n_', 'wave_projection_', 'wave_n_target_')):
                steps.append(2, row)
        steps.complete(2)
        if on_day is None:
            steps.append(24, dict(signals=result.signals, audit=result.audit, counts=result.counts))
            steps.complete(24)
    return result
