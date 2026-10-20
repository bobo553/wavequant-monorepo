"""Resolve qualified-session gaps and defended-N minute entries."""

from collections.abc import Sequence
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Callable, TypedDict

from wavequant.domain.market_state.volume_candle import volume_bearish_entry_rejection
from wavequant.domain.market_state.candle_strength import (
    QUALIFIED_CLOSE_ENTRY_REASONS, QUALIFIED_FOLLOW_THROUGH_CLOSE, QUALIFIED_RISING_CLOSE,
    QUALIFIED_VOLUME_BREAKOUT_CLOSE,
    double_volume_bullish_breakout, volume_gap_follow_through, volume_high_open_rising,
)
from wavequant.domain.models.model import Bar, Signal
from wavequant.domain.strategies.integrated_strategy import (
    SystemResult,
    SystemStrategy,
    generate_system_signals,
    pivot_history,
)
from wavequant.domain.strategies.n_consolidation import consolidation_gap
from wavequant.domain.strategies.trading_intervals import TradingInterval
from wavequant.domain.strategies.hierarchical_entry import EntryContext
from wavequant.domain.strategies.backtest_history_reuse import BacktestHistoryReuse, MinuteTradingScope
from wavequant.domain.strategies.wave_continuation import (
    wave_confirmation_is_new,
    wave_confirmation_state,
    wave_gap_entry,
    wave_pullback_context,
)
from wavequant.domain.market_structure.wave_projection import WaveProjectionSetup
from wavequant.domain.market_structure.polyline import PointKind
from wavequant.domain.market_structure.polyline_lifecycle import PolylineBuyGate
from wavequant.infrastructure.market_data.akshare_history import MinuteCoverageError
from wavequant.infrastructure.market_data.data import opening_permissions
from wavequant.infrastructure.market_data.minute import MinuteBar

if TYPE_CHECKING:
    from wavequant.domain.strategies.backtest_analysis import BacktestAnalysis


class EntryExecution(TypedDict):
    signal: Signal
    price: float
    buyable: bool
    timing: dict[str, object]
    remaining_low: float
    remaining_high: float


def _minute_buy_permissions(
    bar: Bar, previous: Bar, following: MinuteBar, *,
    observed_low: float, observed_high: float, observed_volume: float,
) -> tuple[bool, bool]:
    """分钟买点共用价格权限；非一字事实只取决策时已观察的量价。

    第二个返回值不是成交许可，账户仍须显式启用非一字涨停成交假设。
    """
    permission = dict(date=bar.timestamp.date().isoformat(),
        isST='1' if bar.raw_is_st else '0',
        tradestatus='1' if bar.raw_trading_active is not False and following.volume > 0 else '0',
        preclose=str(previous.close / bar.adjustment_factor))
    buyable, _ = opening_permissions(dict(permission, open=str(following.open)), bar.symbol)
    below_limit, _ = opening_permissions(
        dict(permission, open=str(observed_low / bar.adjustment_factor)), bar.symbol)
    previous_tick_buyable, _ = opening_permissions(
        dict(permission, open=str(Decimal(str(following.open)) - Decimal('0.01'))), bar.symbol)
    observed_nonflat = (not buyable and previous_tick_buyable and below_limit
        and observed_high > observed_low and observed_volume > 0)
    return buyable, observed_nonflat


def _entry_polyline_gate(
    bars: list[Bar], strategy: SystemStrategy, *, prepared_pivots: tuple | None = None,
    prepared_gate: PolylineBuyGate | None = None,
) -> PolylineBuyGate:
    """逐日执行复用策略门禁；独立解析只从同一因果折线历史补建一次。"""
    if prepared_gate is not None and prepared_gate.index == len(bars) - 1:
        return prepared_gate
    gate = PolylineBuyGate()
    if strategy.buy_point_definition != 'whole_flip_wave_v3':
        return gate
    snapshots, epochs, _, _ = prepared_pivots if prepared_pivots is not None else pivot_history(bars, strategy)
    for index, bar in enumerate(bars):
        gate.advance(index, bar, snapshots.get(index, ()), epochs.get(index, 0))
    return gate


def _reject_polyline_entry(
    bars: list[Bar], index: int, gate: PolylineBuyGate, signals: list[Signal], audit: list[dict[str, object]],
) -> bool:
    rejection = gate.restrictions.get(index)
    if rejection is None:
        return False
    signals[:] = [row for row in signals if not (row.bar_index == index and row.side == 'LONG')]
    audit[:] = [row for row in audit if not (row['bar_index'] == index
        and (row['event'] in ('long_signal', 'long_transition_evidence')
             or row.get('candidate_channel') == 'global_polyline_last_fall_high'))]
    audit.append(dict(event='entry_rejected', bar_index=index, symbol=bars[index].symbol,
        timestamp=bars[index].timestamp.isoformat(), candidate_channel='global_polyline_last_fall_high',
        **rejection))
    return True


def _qualified_gap_entries(
    bars: list[Bar], generated: SystemResult, strategy: SystemStrategy,
    minute_loader: Callable[[Bar], list[MinuteBar]] | None, *, only_index: int | None,
    intervals: Sequence[TradingInterval] | None,
    polyline_gate: PolylineBuyGate | None = None,
) -> tuple[SystemResult, dict[tuple[str, int], EntryExecution], list[dict[str, object]], set[int]]:
    """消费现有资格区间；用户指定确认日整天可买，缺分钟则开盘模拟。

    不重算资格或要求 N；按用户约定先做全天阴线过滤。分钟定价只消费已完成的观察棒，
    资金、仓位、止损、目标和价格限制继续交给同一账户撮合入口。
    """
    from wavequant.domain.strategies.trading_intervals import confirmed_trading_intervals

    executions: dict[tuple[str, int], EntryExecution] = {}
    fallbacks: list[dict[str, object]] = []
    gap_days: set[int] = set()
    if strategy.buy_point_definition != 'whole_flip_wave_v3':
        return generated, executions, fallbacks, gap_days
    if intervals is None:
        intervals = confirmed_trading_intervals(generated.audit, len(bars), strategy.buy_interval_level)
    gate = polyline_gate if polyline_gate is not None else _entry_polyline_gate(bars, strategy)
    signals, audit = list(generated.signals), list(generated.audit)
    for i in ([only_index] if only_index is not None else range(1, len(bars))):
        if i <= 0 or bars[i].open <= bars[i - 1].high:
            continue
        active = [window for window in intervals if window.start_index <= i
            and (window.end_index is None or i < window.end_index)]
        window = max(active, key=lambda value: value.start_index, default=None)
        if window is None:
            continue
        bar = bars[i]
        confirmation = next((row for row in reversed(audit)
            if row.get('event') == 'trend_lifecycle_state' and row.get('state') == '空多交替'
            and row.get('trend_level') == strategy.buy_interval_level
            and row.get('source_path') == window.source_path and row['bar_index'] == window.start_index), None)
        if confirmation is None or window.start_low_price is None:
            continue
        if _reject_polyline_entry(bars, i, gate, signals, audit):
            gap_days.add(i)
            continue
        # 当日首次突破只有收盘证据；后续守稳收盘信号也不能被开盘模拟覆盖。
        if any(row.bar_index == i and row.reason in QUALIFIED_CLOSE_ENTRY_REASONS for row in signals):
            continue
        gap_days.add(i)
        rejection = volume_bearish_entry_rejection(bars, i)
        if rejection is not None:
            signals = [row for row in signals if not (row.bar_index == i and row.side == 'LONG')]
            audit = [row for row in audit if not (row['bar_index'] == i
                and row['event'] in ('long_signal', 'long_transition_evidence'))]
            audit.append(dict(event='entry_rejected', bar_index=i, symbol=bar.symbol,
                timestamp=bar.timestamp.isoformat(), reason=rejection,
                candidate_channel='qualified_gap_open',
                candle_filter_model='full_session_candle_filter',
                candle_filter_known_at=bar.timestamp.replace(hour=15).isoformat()))
            continue
        fallback = None
        try:
            minute = minute_loader(bar) if minute_loader is not None else []
        except MinuteCoverageError as exc:
            minute = []
            fallback = exc.coverage
        timing: dict[str, object] = dict(qualification_model='confirmation_day_full_session',
            candle_filter_model='full_session_candle_filter',
            candle_filter_known_at=bar.timestamp.replace(hour=15).isoformat(),
            qualification_index=window.start_index, trend_level=strategy.buy_interval_level,
            source_path=window.source_path, gap_open=bar.open, previous_high=bars[i - 1].high)
        if not minute:
            decision = execution_at = bar.timestamp.replace(hour=9, minute=30)
            price, buyable = bar.open, bar.buyable
            remaining_low, remaining_high = bar.low, bar.high
            timing.update(execution_model='same_day_open', decision_source='qualified_gap_open_no_minutes',
                minute_fallback=True, coverage=fallback)
            fallbacks.append(dict(symbol=bar.symbol, date=bar.timestamp.date().isoformat(),
                purpose='qualified_gap_entry', reason='minute_history_missing',
                execution_model='same_day_open', coverage=fallback))
        else:
            # 高开高走：至观察时未跌破开盘，完整五分钟收盘严格高于开盘。
            observed_low = float('inf')
            observed_high = observed_volume = 0.0
            for offset, observed in enumerate(minute[:-1]):
                observed_low = min(observed_low, observed.low * bar.adjustment_factor)
                observed_high = max(observed_high, observed.high * bar.adjustment_factor)
                observed_volume += observed.volume
                if observed_low < bar.open:
                    break
                if observed.close * bar.adjustment_factor <= bar.open:
                    continue
                following = minute[offset + 1]
                decision = observed.timestamp
                execution_at = following.timestamp - timedelta(minutes=5)
                price = following.open * bar.adjustment_factor
                buyable, observed_nonflat = _minute_buy_permissions(bar, bars[i - 1], following,
                    observed_low=observed_low, observed_high=observed_high, observed_volume=observed_volume)
                remaining_low = min(row.low for row in minute[offset + 1:]) * bar.adjustment_factor
                remaining_high = max(row.high for row in minute[offset + 1:]) * bar.adjustment_factor
                timing.update(execution_model='intraday_5m_next_open', decision_source='qualified_gap_rising_minute',
                    observed_low=observed_low, observed_high=observed_high, observed_volume=observed_volume,
                    observed_close=observed.close * bar.adjustment_factor, minute_next_open_raw=following.open,
                    observed_nonflat_limit_buyable=observed_nonflat)
                break
            else:
                continue
            if observed_low < bar.open:
                continue
        timing.update(decision_timestamp=decision.isoformat(), execution_timestamp=execution_at.isoformat())
        signal = Signal(bar.timestamp, bar.symbol, i, 'LONG', price, window.start_low_price,
            '资格区间跳空买入', decision, 0, None, '跳空高开', confirmation['breakout_level'],
            strategy.minimum_reward_risk)
        executions[bar.symbol, i] = dict(signal=signal, price=price, buyable=buyable, timing=timing,
            remaining_low=remaining_low, remaining_high=remaining_high)
        signals = [row for row in signals if not (row.bar_index == i and row.side == 'LONG')] + [signal]
        audit = [row for row in audit if not (row['bar_index'] == i
            and row['event'] in ('long_signal', 'long_transition_evidence'))]
        for event in ('long_signal', 'long_transition_evidence'):
            audit.append(dict(event=event, bar_index=i, timestamp=bar.timestamp.isoformat(), symbol=bar.symbol,
                buy_point_type='qualified_gap_open', stop=signal.invalidation_price, target=signal.target_price, **timing))
    return SystemResult(sorted(signals, key=lambda row: (row.timestamp, row.side)), audit,
        dict(generated.counts, long_signals=sum(row.side == 'LONG' for row in signals))), executions, fallbacks, gap_days


def _qualified_rising_close_entries(
    bars: list[Bar], generated: SystemResult, strategy: SystemStrategy, *,
    only_index: int | None, intervals: Sequence[TradingInterval] | None,
    polyline_gate: PolylineBuyGate | None = None,
) -> tuple[SystemResult, set[int]]:
    """已有资格内高开强阳或倍量突破在收盘入场；严格跳空仍沿原分钟通道。"""
    from wavequant.domain.strategies.trading_intervals import confirmed_trading_intervals

    days: set[int] = set()
    if strategy.buy_point_definition != 'whole_flip_wave_v3':
        return generated, days
    if intervals is None:
        intervals = confirmed_trading_intervals(generated.audit, len(bars), strategy.buy_interval_level)
    gate = polyline_gate if polyline_gate is not None else _entry_polyline_gate(bars, strategy)
    signals, audit = list(generated.signals), list(generated.audit)
    for i in ([only_index] if only_index is not None else range(1, len(bars))):
        if i <= 0:
            continue
        follow_through = (i - 1 in gate.breakouts and gate.source_epochs.get(i) == gate.source_epochs.get(i - 1)
                          and volume_gap_follow_through(bars[i], bars[i - 1]))
        if bars[i].open > bars[i - 1].high and i not in gate.breakouts and not follow_through:
            continue
        breakout = double_volume_bullish_breakout(bars[i], bars[i - 1])
        if not breakout and not follow_through and not volume_high_open_rising(bars[i], bars[i - 1]):
            continue
        window = max((w for w in intervals if w.trend_level == strategy.buy_interval_level
            and w.start_index <= i and (w.end_index is None or i < w.end_index)),
            key=lambda w: w.start_index, default=None)
        if window is None or window.start_low_price is None:
            continue
        confirmation = next((row for row in reversed(audit)
            if row.get('event') == 'trend_lifecycle_state' and row.get('state') == '空多交替'
            and row.get('trend_level') == strategy.buy_interval_level
            and row.get('source_path') == window.source_path and row['bar_index'] == window.start_index), None)
        if confirmation is None:
            continue
        if _reject_polyline_entry(bars, i, gate, signals, audit):
            days.add(i)
            continue
        bar = bars[i]
        decision = bar.timestamp.replace(hour=15, minute=0)
        reason = (QUALIFIED_FOLLOW_THROUGH_CLOSE if follow_through else
                  QUALIFIED_VOLUME_BREAKOUT_CLOSE if breakout else QUALIFIED_RISING_CLOSE)
        structural_breakout = gate.breakouts.get(i - 1 if follow_through else i, {})
        signal = Signal(bar.timestamp, bar.symbol, i, 'LONG', bar.close, window.start_low_price,
            reason, decision, 0, bar.volume / bars[i - 1].volume if breakout else None,
            '突破次日放量跳空守稳' if follow_through else
            '倍量中大阳高位收盘突破' if breakout else '放量高开高走',
            confirmation['breakout_level'], strategy.minimum_reward_risk)
        days.add(i)
        signals = [row for row in signals if not (row.bar_index == i and row.side == 'LONG')] + [signal]
        audit = [row for row in audit if not (row['bar_index'] == i
            and row['event'] in ('long_signal', 'long_transition_evidence'))]
        for event in ('long_signal', 'long_transition_evidence'):
            audit.append(dict(event=event, bar_index=i, timestamp=bar.timestamp.isoformat(), symbol=bar.symbol,
                buy_point_type=reason, stop=signal.invalidation_price, target=signal.target_price,
                execution_model='same_day_close', decision_source='daily_close',
                decision_timestamp=decision.isoformat(), qualification_index=window.start_index,
                trend_level=strategy.buy_interval_level, source_path=window.source_path,
                observed_open=bar.open, observed_high=bar.high, observed_low=bar.low,
                observed_close=bar.close, observed_volume=bar.volume,
                previous_close=bars[i - 1].close, previous_high=bars[i - 1].high,
                previous_volume=bars[i - 1].volume,
                volume_multiple=bar.volume / bars[i - 1].volume,
                breakout_reference=structural_breakout.get('polyline_key_price', bars[i - 1].high)
                    if breakout or follow_through else None,
                breakout_reference_index=structural_breakout.get('polyline_key_index', i - 1)
                    if breakout or follow_through else None,
                polyline_breakout_evidence=dict(structural_breakout)))
    return SystemResult(sorted(signals, key=lambda row: (row.timestamp, row.side)), audit,
        dict(generated.counts, long_signals=sum(row.side == 'LONG' for row in signals))), days


def resolve_consolidation_entries(
    bars: list[Bar],
    generated: SystemResult,
    strategy: SystemStrategy,
    minute_loader: Callable[[Bar], list[MinuteBar]] | None,
    *,
    daily_fallback: bool,
    qualified_gap_only: bool = False,
    stage: Callable[[str], None] | None = None,
    history_reuse: BacktestHistoryReuse | None = None,
    only_index: int | None = None,
    session_cache: dict | None = None,
    prepared_pivots: tuple | None = None,
    prepared_polyline_gate: PolylineBuyGate | None = None,
    prepared_contexts: dict[int, tuple[EntryContext, ...]] | None = None,
    prepared_analysis: 'BacktestAnalysis | None' = None,
    trading_window_start: int | None = None,
) -> tuple[SystemResult, dict[tuple[str, int], EntryExecution], list[dict[str, object]]]:
    """Resolve qualified gaps first; keep the existing N minute path for other sessions."""
    if history_reuse is not None:
        history_reuse.require_input(bars, strategy)
    if prepared_analysis is not None:
        if (prepared_analysis.bars != tuple(bars) or only_index is None or only_index != len(bars) - 1
                or trading_window_start is None):
            raise ValueError('prepared minute replay requires the current daily prefix and window')
    gate = _entry_polyline_gate(bars, strategy, prepared_pivots=prepared_pivots,
                                prepared_gate=prepared_polyline_gate)
    generated, rising_days = _qualified_rising_close_entries(
        bars, generated, strategy, only_index=only_index,
        intervals=prepared_analysis.trading_intervals if prepared_analysis is not None else None,
        polyline_gate=gate)
    generated, gap_executions, gap_fallbacks, gap_days = _qualified_gap_entries(
        bars, generated, strategy, minute_loader, only_index=only_index,
        intervals=prepared_analysis.trading_intervals if prepared_analysis is not None else None,
        polyline_gate=gate)
    if qualified_gap_only or only_index is not None and only_index in (gap_days | rising_days):
        return generated, gap_executions, gap_fallbacks
    minute_scope = None
    if prepared_analysis is not None:
        assert only_index is not None and trading_window_start is not None
        minute_scope = MinuteTradingScope.from_analysis(prepared_analysis,
            index=only_index, window_start=trading_window_start)
        # A close-confirmed new qualification cannot authorize earlier minutes.
        if minute_scope is None:
            return generated, gap_executions, gap_fallbacks
    signals = list(generated.signals)
    audit = list(generated.audit)
    executions: dict[tuple[str, int], EntryExecution] = gap_executions
    fallbacks: list[dict[str, object]] = gap_fallbacks
    candidates = [
        e for e in audit if e["event"] == "n_completed" and e.get("direction") == "up" and e.get("n_level", 0) >= 1
        and (minute_scope is None or minute_scope.window_start <= e.get('known_at', e['bar_index']) < minute_scope.index)
    ]
    wave_candidates = [
        (
            e["bar_index"],
            WaveProjectionSetup(**{key: e[key] for key in WaveProjectionSetup.__dataclass_fields__}),
            e.get("wave_epoch"),
        )
        for e in audit
        if e["event"] == "wave_continuation_ready"
        and (minute_scope is None or minute_scope.window_start <= e['bar_index'] < minute_scope.index)
    ]
    snapshots, epochs = {}, {}
    if wave_candidates:
        pivots = prepared_pivots if prepared_pivots is not None else (
            history_reuse.pivot(bars, strategy) if history_reuse is not None else None)
        if pivots is None:
            pivots = pivot_history(bars, strategy)
            if history_reuse is not None:
                history_reuse.save_pivot(bars, strategy, pivots)
        snapshots, epochs = pivots[:2]
    wave_context_history: dict[int, tuple[EntryContext, ...]] = {}
    if prepared_contexts is not None:
        wave_context_history = prepared_contexts
    elif wave_candidates and strategy.buy_point_definition == "whole_flip_wave_v3":
        from wavequant.domain.strategies.chart_entry_history import chart_entry_history

        if history_reuse is not None and history_reuse.hierarchy(
                bars, n_target_trend_confirmation_enabled=strategy.n_target_trend_confirmation_enabled) is None:
            from wavequant.domain.strategies.hierarchical_entry import hierarchical_history

            daily_levels = hierarchical_history(
                bars, decline_sink={}, _checkpoint_reuse=history_reuse,
                n_target_trend_confirmation_enabled=strategy.n_target_trend_confirmation_enabled)
            history_reuse.save_hierarchy(bars, strategy, daily_levels)

        contexts = history_reuse.chart(bars, strategy, audit) if history_reuse is not None else None
        if contexts is None and history_reuse is not None:
            contexts = history_reuse.refresh_chart(bars, strategy, audit)
        if contexts is None:
            contexts = (chart_entry_history(bars, audit=audit,
                n_target_trend_confirmation_enabled=strategy.n_target_trend_confirmation_enabled)
                if history_reuse is None else chart_entry_history(bars, audit=audit,
                    n_target_trend_confirmation_enabled=strategy.n_target_trend_confirmation_enabled,
                    shallow_candidate_sink={} if strategy.shallow_base_breakout_enabled else None,
                    combined_candidate_sink={} if strategy.combined_a_entry_enabled else None,
                    secondary_pullback_sink={} if strategy.secondary_pullback_entry_enabled else None,
                    _history_reuse=history_reuse))
            if history_reuse is not None:
                history_reuse.save_chart(bars, strategy, audit, contexts)
        wave_context_history = contexts[0]
    daily_waves = {e["bar_index"]: e for e in audit if e["event"] == "long_signal" and e.get("wave_entry_path")}
    consumed_waves: dict[tuple[int, int, int], tuple[str, float]] = (
        session_cache.setdefault('consumed_waves', {}) if session_cache is not None else {})
    # Every minute prefix takes all earlier bars from this fixed full-history
    # input. The strategy may therefore reuse observations ending before the
    # current session across later sessions as well as later minutes.
    chart_history_cache: dict = (session_cache.setdefault('chart', {"source_bars": tuple(bars)})
        if session_cache is not None else {"source_bars": tuple(bars)})
    if stage is not None: stage('核验分钟入场')
    for i, bar in enumerate(bars):
        if i in gap_days or i in rising_days:
            continue
        if only_index is not None and i != only_index:
            continue
        rejection = volume_bearish_entry_rejection(bars, i)
        if rejection is not None:
            signals = [row for row in signals if not (row.bar_index == i and row.side == 'LONG')]
            audit = [row for row in audit if not (row['bar_index'] == i
                and row['event'] in ('long_signal', 'long_transition_evidence'))]
            audit.append(dict(event='entry_rejected', bar_index=i, symbol=bar.symbol,
                timestamp=bar.timestamp.isoformat(), reason=rejection,
                candidate_channel='global_minute_entry',
                candle_filter_model='full_session_candle_filter',
                candle_filter_known_at=bar.timestamp.replace(hour=15).isoformat()))
            continue
        preceding_daily = daily_waves.get(i - 1)
        if preceding_daily is not None:
            key = (preceding_daily["attack"], preceding_daily["wave_a_high_index"], preceding_daily["wave_b_low_index"])
            if wave_confirmation_is_new(preceding_daily, consumed_waves.get(key)):
                consumed_waves[key] = wave_confirmation_state(preceding_daily)
        current_daily = daily_waves.get(i)
        current_key = (
            (
                (
                    int(current_daily["attack"]),
                    int(current_daily["wave_a_high_index"]),
                    int(current_daily["wave_b_low_index"]),
                )
            )
            if current_daily is not None
            else None
        )
        if (
            current_daily is not None
            and current_key is not None
            and not wave_confirmation_is_new(current_daily, consumed_waves.get(current_key))
        ):
            # A prior minute body may be followed by one higher gap, while the
            # same phase remains owned by its first available confirmation.
            signals = [s for s in signals if not (s.bar_index == i and s.reason == "system_wave_push_gap")]
            audit = [
                e
                for e in audit
                if not (
                    e["bar_index"] == i
                    and e["event"] in ("long_signal", "long_transition_evidence")
                    and e.get("attack") == current_daily["attack"]
                )
            ]
        # Eligibility uses prior sessions and today's open, never today's final range/volume.
        eligible = [
            e
            for e in candidates
            if max(e["bar_index"], e.get("known_at", e["bar_index"])) < i
            and e["bar_index"] < i - 2
            and bar.open > bars[i - 1].high
            and min(b.low for b in bars[e["bar_index"] + 1 : i]) >= e["defense"]
            and bars[i - 1].close <= bars[e["bar_index"]].high
            and bars[i - 2].close <= bars[e["bar_index"]].high
        ]
        wave_eligible = []
        for known, setup, epoch in wave_candidates:
            # Match the global strategy's structural episode boundary, using
            # yesterday only: today's final candle must not invalidate a minute entry.
            epoch = epochs.get(setup.attack_index) if epoch is None else epoch
            if known >= i or bar.open < setup.defense or epoch != epochs.get(i - 1):
                continue
            if wave_context_history:
                from wavequant.domain.strategies.whole_wave_entry import wave_entry_contexts

                # A defended price box cannot request replay after its entry
                # context has retired. Today's partial bar still owns decisions.
                current = wave_context_history.get(i - 1, ())
                live = {context.episode for context in current}
                available = wave_entry_contexts(
                    current, wave_context_history.get(setup.attack_index, ()),
                    attack=setup.attack_index, asof=i - 1, allow_confirming_n=True,
                )
                if not any(context.episode in live for context in available):
                    continue
            context = wave_pullback_context(bars, setup, i)
            if context is not None and bar.open <= bars[i - 1].high:
                highs = [
                    p
                    for p in snapshots.get(i - 1, ())
                    if p.point.kind == PointKind.HIGH
                    and int(context["wave_a_high_index"]) < p.point.index < i
                    and p.confirmed_index < i
                ]
                resistance = max(highs, key=lambda p: (p.point.index, p.confirmed_index), default=None)
                # Final cumulative volume/high are upper bounds on every prefix.
                # They can prove a trigger impossible, never confirm its timing.
                if (
                    (
                        bar.volume <= bars[i - 1].volume
                        and (context["wave_a_class"] != "ordinary" or strategy.volume_filter)
                    )
                    or bar.high < bar.open * (1.0 if context["wave_a_class"] == "ordinary" else 1.03)
                    or resistance is None
                    or bar.high <= resistance.point.price
                ):
                    continue
            if context is not None:
                key = (setup.attack_index, int(context["wave_a_high_index"]), int(context["wave_b_low_index"]))
                previous = consumed_waves.get(key)
                if previous is None or (
                    previous[0] == "body" and bar.open > bars[i - 1].high and bar.high > previous[1]
                ):
                    wave_eligible.append(setup)
        if not eligible and not wave_eligible:
            continue
        if stage is not None: stage(f'核验分钟入场 · {bar.timestamp.date().isoformat()}')
        try:
            minute = minute_loader(bar) if minute_loader is not None else None
            if minute is None:
                raise MinuteCoverageError(bar.timestamp.date().isoformat(), None, None, "未配置")
        except MinuteCoverageError as exc:
            if not daily_fallback:
                raise
            fallbacks.append(
                dict(
                    symbol=bar.symbol,
                    date=bar.timestamp.date().isoformat(),
                    reason=exc.coverage.get("reason", "minute_history_missing"),
                    coverage=exc.coverage,
                    purpose="wave_continuation_entry" if wave_eligible else "consolidation_entry",
                    execution_model="same_day_close",
                )
            )
            continue
        high, low, volume = 0.0, float("inf"), 0.0
        attempted: set[tuple[float, float, float, float]] = set()
        for offset, observed in enumerate(minute[:-1]):
            high = max(high, observed.high * bar.adjustment_factor)
            low = min(low, observed.low * bar.adjustment_factor)
            volume += observed.volume
            partial = replace(bar, high=high, low=low, close=observed.close * bar.adjustment_factor, volume=volume)
            prefix = bars[:i] + [partial]
            consolidation_confirmed = any(
                consolidation_gap(prefix, attack=e["bar_index"], now=i, defense=e["defense"]) for e in eligible
            )
            wave_confirmations = [
                wave_observation
                for setup in wave_eligible
                if (wave_observation := wave_gap_entry(prefix, setup, i, pivots=snapshots.get(i - 1, ()))) is not None
            ]
            if not consolidation_confirmed and not wave_confirmations:
                continue
            # Ordinary-A price geometry does not imply the optional volume
            # gate passed. Do not replay years of structure for every under-volume
            # minute. Only a verified strong-A gap price-break may waive volume.
            if (
                strategy.buy_point_definition == "whole_flip_wave_v3"
                and strategy.volume_filter
                and volume <= bars[i - 1].volume
                and not any(
                    p.get("wave_gap_trigger") in ("breakout", "breakout_and_volume") for p in wave_confirmations
                )
            ):
                continue
            volume_state = (
                float(volume > bars[i - 1].volume) if strategy.buy_point_definition == "whole_flip_wave_v3" else volume
            )
            observation = (high, low, partial.close, volume_state)
            if observation in attempted:
                continue
            attempted.add(observation)
            if stage is not None:
                stage(f'核验分钟入场 · {bar.timestamp.date().isoformat()} · {offset + 1}/{len(minute) - 1}')
            replay = (
                generate_system_signals(prefix, strategy, chart_history_cache=chart_history_cache,
                    _minute_history_reuse=history_reuse, _minute_scope=minute_scope,
                    _minute_analysis=prepared_analysis)
                if strategy.buy_point_definition == "whole_flip_wave_v3"
                else generate_system_signals(prefix, strategy, _minute_scope=minute_scope)
            )
            proof = next(
                (
                    e
                    for e in replay.audit
                    if e["bar_index"] == i
                    and e["event"] == "long_signal"
                    and e.get("squeeze_confirmation")
                    in ("defended_n_consolidation_gap", "two_t_wave_push_gap", "one_p_wave_rebound")
                ),
                None,
            )
            if proof is None:
                continue
            wave_key = (
                (proof["attack"], proof["wave_a_high_index"], proof["wave_b_low_index"])
                if proof.get("wave_entry_path")
                else None
            )
            if wave_key is not None and not wave_confirmation_is_new(proof, consumed_waves.get(wave_key)):
                continue
            if wave_key is not None:
                consumed_waves[wave_key] = wave_confirmation_state(proof)
            signal = next(s for s in replay.signals if s.bar_index == i and s.side == "LONG")
            following = minute[offset + 1]
            execution_at = following.timestamp - timedelta(minutes=5)
            buyable, observed_nonflat = _minute_buy_permissions(bar, bars[i - 1], following,
                observed_low=low, observed_high=high, observed_volume=volume)
            timing: dict[str, object] = dict(
                execution_model="intraday_5m_next_open",
                candle_filter_model="full_session_candle_filter",
                candle_filter_known_at=bar.timestamp.replace(hour=15).isoformat(),
                decision_source="completed_five_minute_bar",
                decision_timestamp=observed.timestamp.isoformat(),
                execution_timestamp=execution_at.isoformat(),
                observed_volume=volume,
                previous_volume=bars[i - 1].volume,
                observed_high=high,
                observed_low=low,
                observed_close=partial.close,
                minute_next_open_raw=following.open,
                observed_nonflat_limit_buyable=observed_nonflat,
            )
            executions[bar.symbol, i] = dict(
                signal=signal,
                price=following.open * bar.adjustment_factor,
                buyable=bool(buyable),
                timing=timing,
                remaining_low=min(m.low for m in minute[offset + 1 :]) * bar.adjustment_factor,
                remaining_high=max(m.high for m in minute[offset + 1 :]) * bar.adjustment_factor,
            )
            signals = [s for s in signals if not (s.bar_index == i and s.side == "LONG")] + [signal]
            audit = [
                e
                for e in audit
                if not (e["bar_index"] == i and e["event"] in ("long_signal", "long_transition_evidence"))
            ]
            audit.extend(
                {**e, **timing}
                for e in replay.audit
                if e["bar_index"] == i and e["event"] in ("long_signal", "long_transition_evidence")
            )
            break
    counts = dict(generated.counts, long_signals=sum(s.side == "LONG" for s in signals))
    return (
        SystemResult(
            sorted(signals, key=lambda s: (s.timestamp, s.side)),
            sorted(audit, key=lambda e: (e["bar_index"], e["event"])),
            counts,
        ),
        executions,
        fallbacks,
    )
