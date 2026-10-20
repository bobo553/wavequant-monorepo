"""One daily boundary for structure, signals, minute execution and the account."""

from wavequant.domain.market_structure.trend_options import with_strategy_trend_options

from collections.abc import Callable, Sequence
from dataclasses import asdict

from wavequant.application.analytics.backtest import AccountDay, BacktestResult, portfolio_days
from wavequant.application.analytics.intraday_entry import resolve_consolidation_entries
from wavequant.domain.market_structure.calculation_steps import calculation_steps
from wavequant.domain.market_structure.calculation_plan import resolve_calculation_plan
from wavequant.domain.market_state.candle_strength import double_volume_bullish_breakout
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar, Trade
from wavequant.domain.strategies.backtest_analysis import BacktestAnalysis
from wavequant.domain.strategies.daily_strategy import daily_signals
from wavequant.domain.strategies.integrated_strategy import DailySignalState, SystemResult, SystemStrategy
from wavequant.domain.strategies.market_calculations import MarketDay
from wavequant.domain.strategies.market_preview import publish_market_preview
from wavequant.infrastructure.market_data.minute import MinuteBar

from .stock_backtest import single_stock_result
from .signal_execution_stream import SignalExecutionFactsStream


@with_strategy_trend_options("strategy")
def streaming_stock_result(
    bars: list[Bar], strategy: dict, execution: dict, *,
    minute_loader: Callable[[Bar], list[MinuteBar]] | None = None,
    progress: Callable[[int, str], None] | None = None,
) -> tuple[SystemResult, dict]:
    config = SystemStrategy(**strategy)
    config.validate()
    account_config = StrategyConfig(**execution)
    account_config.validate()
    if not bars or len({bar.symbol for bar in bars}) != 1:
        raise ValueError('the daily coordinator requires one nonempty security')
    current_day: MarketDay | None = None
    current_result = SystemResult([], [], {})
    account: BacktestResult | None = None
    fallbacks: list[dict] = []
    minute_cache: dict = {'chart': {'source_bars': tuple(bars)}}
    execution_facts: SignalExecutionFactsStream | None = None
    steps = calculation_steps(bars)
    if steps is not None:
        steps.set_plan(resolve_calculation_plan())
        steps.begin(24)

    def publish(index: int, orders: Sequence[dict], trades: Sequence[Trade], equity: Sequence[dict]) -> None:
        if current_day is None or current_day.frame.index != index:
            raise RuntimeError('account and market must publish the same completed day')
        if steps is not None:
            steps.append(24, dict(bar_index=index,
                signals=[asdict(signal) for signal in current_result.signals if signal.bar_index == index],
                orders=[row for row in orders if row['timestamp'][:10] == bars[index].timestamp.date().isoformat()],
                equity=equity[-1]))
        publish_market_preview(bars, current_day, dict(
            signals=current_result.signals, audit=current_result.audit,
            orders=orders, trades=trades, equity=equity,
            backtest=dict(status='running', strategy=strategy, execution=execution,
                          initial_capital=account_config.initial_capital),
        ))

    runner = portfolio_days({bars[0].symbol: bars}, [], account_config, minute_loader=minute_loader,
        live=True, on_day=publish,
        n_target_trend_confirmation_enabled=config.n_target_trend_confirmation_enabled)

    def consume(day: MarketDay, analysis: BacktestAnalysis, generated: SystemResult, state: DailySignalState) -> None:
        nonlocal current_day, current_result, account, execution_facts
        current_day = day
        index = day.frame.index
        prefix = bars[:index + 1]
        executions: dict = {}
        eligible = bool(analysis.buy_days[-1])

        def minute_stage(message: str) -> None:
            if progress is not None:
                progress(min(index * 100 // len(bars), 99), message)

        # 收盘突破可低开，不能让分钟通道的高开预筛选屏蔽它。
        if eligible and (account_config.consolidation_entry_intraday
                or index > 0 and (prefix[-1].open > prefix[-2].close
                    or double_volume_bullish_breakout(prefix[-1], prefix[-2]))):
            resolved, executions, daily_fallbacks = resolve_consolidation_entries(prefix, generated, config,
                minute_loader, daily_fallback=account_config.missing_minute_daily_fallback,
                qualified_gap_only=not account_config.consolidation_entry_intraday,
                only_index=index, session_cache=minute_cache, stage=minute_stage,
                prepared_pivots=analysis.pivots, prepared_contexts=state.permissions,
                prepared_polyline_gate=state.polyline_buy_gate,
                prepared_analysis=analysis, trading_window_start=state.window_start)
            generated.signals = resolved.signals
            generated.audit = resolved.audit
            generated.counts = resolved.counts
            state.signals[:] = resolved.signals
            state.has_buy_signal = state.has_buy_signal or any(signal.side == 'LONG' for signal in resolved.signals)
            def resolved_entry_row(row: dict[str, object]) -> bool:
                return row['bar_index'] == index and (row['event'] in (
                    'long_signal', 'long_transition_evidence', 'exit_signal')
                    or row.get('candidate_channel') == 'global_polyline_last_fall_high')

            state.audit[:] = [row for row in state.audit if not resolved_entry_row(row)]
            state.audit.extend(row for row in resolved.audit if resolved_entry_row(row))
            # 同日替换可能长度不变，旧排序缓存仍会保存被拒绝/替换的买点。
            state._ordered_signals.clear()
            state._ordered_decisions.clear()
            fallbacks.extend(daily_fallbacks)
        current_result = generated
        if eligible:
            if execution_facts is None or execution_facts.start_index != state.window_start:
                execution_facts = SignalExecutionFactsStream(bars, start_index=state.window_start)
            n_bars, wave_events = execution_facts.advance(index, generated.audit)
            n_bars = execution_facts.active_positive_n_bars
        else:
            execution_facts = None
            n_bars, wave_events = {}, []
        account_day = AccountDay(index,
            [signal for signal in generated.signals if signal.bar_index == index],
            n_bars, wave_events, state.hierarchy, executions,
            hierarchy_owner=state.hierarchy_stream, risk_histories=state.risk_histories,
            trading_eligible=eligible, trading_window_start=state.window_start,
            positive_n_authoritative=True,
            eligibility_closed=(not eligible and len(analysis.buy_days) > 1 and analysis.buy_days[-2]))
        try:
            runner.send(account_day)
        except StopIteration as completed:
            if index != len(bars) - 1:
                raise RuntimeError('account completed before the market') from completed
            account = completed.value
        state.account_risk_required = account_day.risk_required

    current_stage = '同日联动：准备折线、趋势、交易与账户'

    def advance(percent: int) -> None:
        if progress is not None:
            progress(min(percent, 99), current_stage)

    def stage(message: str) -> None:
        nonlocal current_stage
        current_stage = message

    try:
        next(runner)
        generated = daily_signals(bars, config, on_day=consume, progress=advance, stage=stage)
        if account is None:
            raise RuntimeError('the account did not complete the full market history')
        result = single_stock_result(bars, strategy, execution, generated,
            account_result=account, entry_fallbacks_override=fallbacks)
        if steps is not None:
            steps.append(24, result)
            steps.complete(24)
        return generated, result
    finally:
        runner.close()
