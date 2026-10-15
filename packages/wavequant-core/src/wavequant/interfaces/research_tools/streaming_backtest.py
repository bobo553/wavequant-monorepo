"""One daily boundary for structure, signals, minute execution and the account."""

from collections.abc import Callable, Sequence
from dataclasses import asdict

from wavequant.application.analytics.backtest import AccountDay, BacktestResult, portfolio_days
from wavequant.application.analytics.intraday_entry import resolve_consolidation_entries
from wavequant.domain.market_structure.calculation_steps import calculation_steps
from wavequant.domain.market_structure.calculation_plan import resolve_calculation_plan
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar, Trade
from wavequant.domain.strategies.backtest_analysis import BacktestAnalysis
from wavequant.domain.strategies.daily_strategy import daily_signals
from wavequant.domain.strategies.integrated_strategy import DailySignalState, SystemResult, SystemStrategy
from wavequant.domain.strategies.market_calculations import MarketDay
from wavequant.domain.strategies.market_preview import publish_market_preview
from wavequant.infrastructure.market_data.minute import MinuteBar

from .stock_backtest import signal_execution_facts, single_stock_result


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
        nonlocal current_day, current_result, account
        current_day = day
        index = day.frame.index
        prefix = bars[:index + 1]
        executions: dict = {}
        if account_config.consolidation_entry_intraday and analysis.scan_days[-1]:
            resolved, executions, daily_fallbacks = resolve_consolidation_entries(prefix, generated, config,
                minute_loader, daily_fallback=account_config.missing_minute_daily_fallback,
                only_index=index, session_cache=minute_cache,
                prepared_pivots=analysis.pivots, prepared_contexts=state.permissions)
            generated.signals = resolved.signals
            generated.audit = resolved.audit
            generated.counts = resolved.counts
            state.signals[:] = resolved.signals
            state.has_buy_signal = state.has_buy_signal or any(signal.side == 'LONG' for signal in resolved.signals)
            state.audit[:] = [row for row in state.audit if not (row['bar_index'] == index
                and row['event'] in ('long_signal', 'long_transition_evidence', 'exit_signal'))]
            state.audit.extend(row for row in resolved.audit if row['bar_index'] == index
                and row['event'] in ('long_signal', 'long_transition_evidence', 'exit_signal'))
            fallbacks.extend(daily_fallbacks)
        current_result = generated
        n_bars, wave_events = signal_execution_facts(prefix, generated)
        try:
            runner.send(AccountDay(index,
                [signal for signal in generated.signals if signal.bar_index == index],
                n_bars, wave_events, state.hierarchy, executions))
        except StopIteration as completed:
            if index != len(bars) - 1:
                raise RuntimeError('account completed before the market') from completed
            account = completed.value

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
