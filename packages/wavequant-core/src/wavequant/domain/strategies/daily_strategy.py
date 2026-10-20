"""Causal daily coordination; market and decision state share one advancing date."""

from wavequant.domain.market_structure.trend_options import with_strategy_trend_options

from collections.abc import Callable, Sequence
from dataclasses import replace
from time import monotonic
from typing import cast

from ..models.model import Bar
from ..market_structure.polyline import ReversalPoint
from ..market_structure.calculation_steps import calculation_steps
from ..market_structure.calculation_plan import resolve_calculation_plan
from ..models.validated_bars import ValidatedBars
from .backtest_analysis import BacktestAnalysis, prepare_backtest_analysis
from .backtest_history_reuse import BacktestHistoryReuse, HierarchyHistory
from .integrated_strategy import DailySignalState, SystemResult, SystemStrategy
from . import integrated_strategy as policy
from .market_calculations import MarketDay
from ..market_structure.a_wave import AWaveHistoryStream
from .daily_risk_histories import DailyRiskHistories
from .wave_projection_stream import WaveProjectionHistory
from ..market_structure.bottom_n_targets import BottomNTargetHistoryStream
from ..market_structure.positive_n_reformation import PositiveNReformationHistory
from .secondary_resistance import SecondaryResistanceStream


@with_strategy_trend_options("config")
def daily_signals(
    bars: Sequence[Bar], config: SystemStrategy, *,
    on_day: Callable[[MarketDay, BacktestAnalysis, SystemResult, DailySignalState], None] | None = None,
    progress: Callable[[int], None] | None = None,
    stage: Callable[[str], None] | None = None,
    minor_points: Sequence[ReversalPoint] | None = None,
    history_reuse: BacktestHistoryReuse | None = None,
) -> SystemResult:
    """Run decisions once per close, using only the market prefix available then.

    Existing shape observers retain their rule semantics and bounded prefix caches.
    They are not permitted to consume future candles or replay trading decisions.
    """
    market = ValidatedBars(bars)
    if history_reuse is not None:
        history_reuse.require_input(market, config)
    requested_hierarchy: dict[int, dict[int, tuple[dict[str, object], ...]]] = {}
    state = DailySignalState()
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
        if (history_reuse is not None and config.n_target_trend_confirmation_enabled
                and analysis.calculations is not None and analysis.hierarchy is not None):
            # 旧完整历史保留发布证书的确认日；共享日线历史另有首次观察日。
            # 从当天已有许可投影五字段，不能把两种知识时钟混作同一缓存。
            views = analysis.calculations._shared_permissions_at(index, n_enabled=True)
            if views is None:
                raise RuntimeError('requested N history requires the existing same-day permissions')
            dated: dict[int, tuple[dict[str, object], ...]] = {}
            for level, (prepared, view) in enumerate(zip(day.levels, views, strict=True), 1):
                points = analysis.hierarchy._active_points(prepared, view, 'qualified_strokes', index)
                dated[level] = tuple(dict(index=point['index'], ordinal=point.get('ordinal', 0),
                    kind=point['kind'], value=point['value'],
                    available_at=analysis.hierarchy._known_index(point.get('available_at')))
                    for point in points)
            requested_hierarchy[index] = dated
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

        state.hierarchy_stream = analysis.hierarchy
        state.hierarchy = analysis.hierarchy.history if analysis.hierarchy is not None else {}
        eligible = bool(analysis.buy_days[-1])
        scopes = frozenset((interval.source_path, interval.start_index)
            for interval in analysis.trading_intervals
            if interval.start_index <= index and (interval.end_index is None or index < interval.end_index))
        replaced_window = bool(eligible and state.window_scopes and not state.window_scopes.intersection(scopes))
        ended_window = state.trading_active and (not eligible or replaced_window)
        departed_scopes = state.window_scopes - scopes
        closed_targets: list[dict[str, object]] = []
        if state.trading_active and (ended_window or departed_scopes):
            # 关闭窗口后只保存已发布事实；下一窗口不得补算空档或复用旧目标状态。
            departing_rows = [row for row in state.preparation_audit
                if not str(row.get('event', '')).startswith(('trading_interval_', 'trend_'))
                and (ended_window or (row.get('source_path'), row.get('qualification_window_start')) in departed_scopes)]
            state.archived_preparation.extend(departing_rows)
            retired = set()
            for row in departing_rows:
                attack = row.get('bar_index')
                scope = (row.get('n_id'), attack, row.get('window_start'), row.get('source_path'))
                if (row.get('event') == 'n_completed' and row.get('direction') == 'up'
                        and scope not in retired):
                    retired.add(scope)
                    closed_targets.append(dict(event='n_target_source_retired', bar_index=index,
                        timestamp=market[index].timestamp.isoformat(), symbol=market[index].symbol,
                        attack=attack, n_id=row.get('n_id'), source_path=row.get('source_path'),
                        qualification_window_start=row.get('qualification_window_start'),
                        reason='trading_eligibility_closed', window_start=state.window_start))
            state.archived_preparation.extend(closed_targets)
            state.preparation_audit = ([] if ended_window else [row for row in state.preparation_audit
                if (row.get('source_path'), row.get('qualification_window_start')) not in departed_scopes])
        if ended_window:
            if state.projection_history is not None:
                state.projection_history.close()
            if state.secondary_resistance_history is not None:
                state.secondary_resistance_history.close()
            state.permissions = {}
        if eligible and (not state.trading_active or replaced_window):
            state.window_start = index
            state.a_wave_stream = AWaveHistoryStream(market, start_index=index)
            state.risk_histories = DailyRiskHistories(market,
                breakout_window=config.ten_full_breakout_window,
                retracement_ratio=config.ten_full_retracement_ratio,
                anchor=config.ten_full_retracement_anchor,
                timed_half=config.ten_full_timed_half_retracement, start_index=index)
            state.projection_history = WaveProjectionHistory(market, start_index=index)
            state.bottom_target_history = BottomNTargetHistoryStream(market, start_index=index)
            state.reformed_target_history = BottomNTargetHistoryStream(market, start_index=index)
            state.reformation_history = PositiveNReformationHistory(market, start_index=index)
            state.secondary_resistance_history = SecondaryResistanceStream(
                market, include_resolved=True, start_index=index,
            )
            state.bottom_hits.clear()
            state.c_equal_events.clear()
            state.emitted_attacks.clear()
            state.emitted_nested.clear()
            state.emitted_nested_owners.clear()
            state.canonical_n_owners.clear()
            state.emitted_waves.clear()
            state.emitted_secondary_reclaims.clear()
            state.emitted_secondary_pullbacks.clear()
            # 图表检查点持有已发布的因果历史；换交易窗口仍由其完整来源、
            # 资格前缀及攻击证据校验续接，不能随窗口内形态缓存退回历史起点。
            chart_checkpoint = cache.get('chart')
            cache.clear()
            cache['source_bars'] = tuple(market)
            if chart_checkpoint is not None:
                cache['chart'] = chart_checkpoint
        state.trading_active = eligible
        state.window_scopes = scopes
        if eligible:
            report_phase('计算资格区间内交易确认与 B/S')
            result = policy._generate_system_signals(analysis.bars, config, minor_points=minor_points,
                chart_history_cache=cache, _analysis=analysis, _daily=state, stage=report_phase)
        else:
            events = [dict(row,
                timestamp=market[cast(int, row['bar_index'])].timestamp.isoformat(), symbol=market[0].symbol)
                for row in day.lifecycle_events]
            for interval in analysis.trading_intervals:
                if interval.end_index == index:
                    events.append(dict(event='trading_interval_closed', bar_index=index,
                        timestamp=market[index].timestamp.isoformat(), symbol=market[index].symbol,
                        trend_level=interval.trend_level, source_path=interval.source_path,
                        start_index=interval.start_index, flip_index=interval.end_flip_index,
                        reason=interval.end_reason, reversal_low_index=interval.end_key_index,
                        reversal_low_price=interval.end_key_price))
            result = SystemResult(list(result.signals), [*result.audit, *events, *closed_targets],
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
                    f'累计耗时：行情计算 {market_seconds:.1f}秒，'
                    f'交易结构确认 {state.hierarchy_seconds:.1f}秒，'
                    f'买卖规则 {max(0.0, decision_seconds - state.hierarchy_seconds):.1f}秒，'
                    f'账户/分钟/快照 {account_seconds:.1f}秒；'
                    f'{analysis.calculations.timing.progress_text() + "；" if analysis.calculations is not None else ""}'
                    f'最近一日 {monotonic() - last_finished:.3f}秒，'
                    f'平均每交易日 {(market_seconds + decision_seconds + account_seconds) / (index + 1):.3f}秒')
            if progress is not None:
                progress(percent)
            last_percent = percent
            last_report = monotonic()
        last_finished = monotonic()

    try:
        with prepare_backtest_analysis(market, config, on_day=advance) as analysis:
            if history_reuse is not None:
                # 显式完整历史消费者只在收尾捕获一次；日线默认路径不增加
                # 每日复制。读取已有逐日几何，不能重新运行三级趋势判定。
                history_reuse.save_pivot(market, config, analysis.pivots)
                if analysis.hierarchy is not None:
                    stream = analysis.hierarchy
                    levels = (requested_hierarchy if config.n_target_trend_confirmation_enabled else
                        {day: {level: tuple(dict(point) for point in points)
                               for level, points in row.items()}
                         for day, row in stream.history.items()})
                    history_reuse.save_hierarchy(market, config,
                        cast(HierarchyHistory, (levels, stream.epochs)))
                from .chart_entry_history import chart_entry_history

                complete = replace(analysis, buy_days=(True,) * len(market))
                chart = chart_entry_history(market, audit=result.audit,
                    _prepared_history=complete, _history_reuse=history_reuse,
                    n_target_trend_confirmation_enabled=config.n_target_trend_confirmation_enabled)
                history_reuse.save_chart(market, config, result.audit, chart)
    finally:
        if state.projection_history is not None:
            state.projection_history.close()
        if state.secondary_resistance_history is not None:
            state.secondary_resistance_history.close()
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
