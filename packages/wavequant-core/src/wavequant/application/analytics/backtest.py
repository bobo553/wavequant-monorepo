"""Run shared-cash research with explicit daily and verified minute execution."""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Callable

from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar, Signal, Trade
from wavequant.domain.strategies.wave_exhaustion_exit import observe_wave_exhaustion
from wavequant.domain.strategies.pressure_exit import pressure_exit_history
from wavequant.domain.strategies.staged_exit import (
    StagedExitState, observe_intraday_staged_exit, observe_staged_exit, observe_inverse_resistance_exit, observe_volume_down_exit,
)
from wavequant.infrastructure.market_data.minute import MinuteBar
from wavequant.infrastructure.market_data.akshare_history import MinuteCoverageError


@dataclass
class BacktestResult:
    trades: list[Trade]
    metrics: dict
    equity: list[dict] = field(default_factory=list)
    orders: list[dict] = field(default_factory=list)
    open_positions: list[dict] = field(default_factory=list)
    minute_fallbacks: list[dict] = field(default_factory=list)


@dataclass
class _Position:
    entry_index: int
    entry_time: datetime
    entry_price: float
    quantity: float
    entry_fee: float
    stop: float
    initial_stop: float
    target: float
    reason: str
    staged_exit: StagedExitState = field(default_factory=StagedExitState)
    initial_quantity: float = 0.0
    initial_entry_fee: float = 0.0
    exit_notional: float = 0.0
    exit_fees: float = 0.0
    realized_pnl: float = 0.0
    wave_events: list[dict] = field(default_factory=list)
    wave_reduced: bool = False


def transaction_fee(notional: float, when: datetime, sell: bool, config: StrategyConfig) -> float:
    fee = max(config.minimum_commission, notional * config.commission_bps_per_side / 10000)
    if config.a_share_taxes:
        fee += notional * (0.00002 if when.date().isoformat() < '2022-04-29' else 0.00001)
        if sell:
            fee += notional * (0.001 if when.date().isoformat() < '2023-08-28' else 0.0005)
    return fee


def equity_metrics(curve: list[dict], capital: float) -> dict:
    daily = {r['timestamp'][:10]: r for r in curve}
    values = [capital] + [r['equity'] for r in daily.values()]
    returns = [b / a - 1 for a, b in zip(values, values[1:])]
    peak, drawdown = capital, 0.0
    for value in values:
        peak = max(peak, value)
        drawdown = min(drawdown, value / peak - 1)
    vol = statistics.stdev(returns) if len(returns) > 1 else 0.0
    ratio = values[-1] / capital
    return dict(total_return=ratio - 1,
                annualized_return=ratio ** (252 / len(returns)) - 1 if returns else 0,
                max_drawdown=drawdown,
                sharpe=statistics.mean(returns) / vol * math.sqrt(252) if vol else None,
                annualized_volatility=vol * math.sqrt(252),
                average_exposure=statistics.mean(r['exposure'] for r in daily.values()) if daily else 0,
                final_equity=values[-1], sessions=len(returns))


def summarize_trades(trades: list[Trade]) -> dict:
    gains = sum(max(0, t.pnl) for t in trades)
    losses = -sum(min(0, t.pnl) for t in trades)
    return dict(trades=len(trades), win_rate=sum(t.pnl > 0 for t in trades) / len(trades) if trades else 0,
                closed_pnl=sum(t.pnl for t in trades),
                average_net_return=statistics.mean(t.net_return for t in trades) if trades else 0,
                profit_factor=gains / losses if losses else None)


def run_backtest(bars: list[Bar], signals: list[Signal], config: StrategyConfig) -> BacktestResult:
    return run_portfolio({bars[0].symbol: bars} if bars else {}, signals, config)


def run_portfolio(grouped: dict[str, list[Bar]], signals: list[Signal], config: StrategyConfig,
                  start: str | None = None, end: str | None = None,
                  minute_loader: Callable[[Bar], list[MinuteBar]] | None = None,
                  positive_n_bars: dict[str, dict[int, int]] | None = None,
                  wave_events: dict[str, list[dict]] | None = None,
                  entry_executions: dict | None = None) -> BacktestResult:
    """Entries use explicit next-open or same-close simulation; exits retain their model.

    Volume capacity is a prior-bar estimate, not an auction fill guarantee. Adjusted
    units approximate reinvested corporate actions, not a cash dividend ledger.
    Stops (and targets when enabled) observed in OHLC request next-open exits;
    they are NOT stop fills. V3 measured milestones do not liquidate holdings.
    """
    config.validate()
    entry_executions = entry_executions or {}
    if config.staged_exit_intraday and minute_loader is None:
        raise ValueError('五分钟精确减仓需要完整分钟行情来源')
    calendar: dict[datetime, dict[str, tuple[int, Bar]]] = {}
    for symbol, bars in grouped.items():
        for i, bar in enumerate(bars):
            if bar.symbol != symbol or (i and bar.timestamp <= bars[i-1].timestamp):
                raise ValueError('bars must have matching symbols and strictly increasing timestamps')
            day = bar.timestamp.date().isoformat()
            if (start is None or day >= start) and (end is None or day <= end):
                calendar.setdefault(bar.timestamp, {})[symbol] = (i, bar)
    # Map availability to source candle; each prefix sees only then-confirmed N bars.
    n_context: dict[str, list[int | None]] = {}
    for symbol, history in grouped.items():
        known = (positive_n_bars or {}).get(symbol, {})
        for available, attack in known.items():
            if type(available) is not int or type(attack) is not int or not 0 <= attack <= available < len(history):
                raise ValueError('invalid positive N availability')
        active = None
        n_context[symbol] = []
        for j, candle in enumerate(history):
            if j in known:
                active = known[j]
            if active is not None:
                defense = min(history[active].low, history[active-1].close) if active else history[active].low
                if candle.low < defense or (j in known and any(b.low < defense for b in history[active:j])):
                    active = None
            n_context[symbol].append(active)
    pressure_risks = {symbol: pressure_exit_history(history, n_context[symbol], config)
                      if config.pressure_adverse_exit else {} for symbol, history in grouped.items()}
    trend_flip_risks = {symbol: {} for symbol in grouped}
    if config.trend_flip_adverse_exit:
        from wavequant.domain.strategies.hierarchical_entry import hierarchical_history
        from wavequant.domain.strategies.trend_flip_exit import trend_flip_exit_history
        trend_flip_risks = {symbol: trend_flip_exit_history(history, hierarchical_history(history)[0])
                            for symbol, history in grouped.items()}
    wave_lookup = {symbol: {} for symbol in grouped}
    for symbol, history in grouped.items():
        for event in (wave_events or {}).get(symbol, []):
            attack, known = event['attack'], event['bar_index']
            if type(attack) is not int or type(known) is not int or not 0 <= attack <= known < len(history):
                raise ValueError('invalid wave event availability')
            wave_lookup[symbol].setdefault(history[attack].timestamp, []).append(event)
    signal_map: dict[datetime, list[Signal]] = {}
    for signal in signals:
        bars = grouped.get(signal.symbol, [])
        if (signal.side not in ('LONG', 'EXIT') or signal.bar_index < 0 or signal.bar_index >= len(bars)
                or bars[signal.bar_index].timestamp != signal.timestamp
                or not math.isfinite(signal.invalidation_price) or signal.invalidation_price <= 0):
            raise ValueError('invalid or misaligned signal')
        if (not math.isfinite(signal.reference_price) or signal.reference_price <= 0
                or not math.isfinite(signal.minimum_reward_risk) or signal.minimum_reward_risk < 0
                or (signal.target_price is not None and
                    (not math.isfinite(signal.target_price) or signal.target_price <= 0))
                or (signal.minimum_reward_risk > 0 and signal.target_price is None)):
            raise ValueError('invalid signal target/reward-risk')
        signal_map.setdefault(signal.timestamp, []).append(signal)
    cash = config.initial_capital
    positions: dict[str, _Position] = {}
    pending_entry: dict[str, tuple[Signal, int]] = {}
    pending_exit: dict[str, str] = {}
    exit_evidence: dict[str, dict] = {}
    marks, mark_times = {}, {}
    trades, curve, orders = [], [], []
    minute_fallbacks = []
    total_fees = turnover = 0.0
    slip = config.slippage_bps_per_side / 10000

    def capacity(symbol: str, i: int, bar: Bar) -> float:
        history = grouped[symbol][max(0, i-config.liquidity_lookback):i]
        return (statistics.mean(b.volume for b in history) * config.max_participation
                / bar.adjustment_factor) if history else 0.0

    def log(when: datetime, symbol: str, side: str, status: str, reason: str, **extra: object) -> None:
        if side == 'SELL':
            extra = {**exit_evidence.get(symbol, {}), **extra}
        orders.append(dict(timestamp=when.isoformat(), symbol=symbol, side=side,
                           status=status, reason=reason, **extra))

    def execute_exit(symbol, i, bar, when, reference_price, execution_model):
        nonlocal cash, total_fees, turnover
        pos = positions[symbol]
        evidence = exit_evidence.get(symbol, {})
        fraction = evidence.get('exit_fraction', 1.0)
        lot = config.lot_size / bar.adjustment_factor
        quantity = pos.quantity if fraction == 1 else math.floor(pos.quantity * fraction / lot) * lot
        if 'exit_target_fraction' in evidence:
            desired = pos.initial_quantity * evidence['exit_target_fraction']
            already_sold = pos.initial_quantity - pos.quantity
            quantity = max(0.0, math.floor((desired - already_sold) / lot + 1e-9) * lot)
        # A holding smaller than two sale lots cannot be split as requested.
        if quantity <= 0:
            if 'exit_target_fraction' in evidence:
                log(when, symbol, 'SELL', 'deferred', 'reduction_below_one_lot')
                pending_exit.pop(symbol, None)
                exit_evidence.pop(symbol, None)
                return False
            quantity = pos.quantity
        reason = ('T+1' if not config.allow_same_day_exit and when.date() == pos.entry_time.date()
                  else 'not_sellable' if not bar.sellable else
                  'liquidity_capacity' if quantity > capacity(symbol, i, bar) + 1e-8 else '')
        if reason:
            log(when, symbol, 'SELL', 'deferred', reason)
            return False
        price = reference_price * (1-slip)
        notional = price * quantity
        fee = transaction_fee(notional, when, True, config)
        allocated_entry_fee = pos.entry_fee * quantity / pos.quantity
        cost = pos.entry_price * quantity + allocated_entry_fee
        pnl = notional - fee - cost
        remaining = max(0.0, pos.quantity - quantity)
        closed = remaining < 1e-8
        pos.exit_notional += notional
        pos.exit_fees += fee
        pos.realized_pnl += pnl
        entry_cost = pos.entry_price * pos.initial_quantity + pos.initial_entry_fee
        if closed:
            # A trade ends only when the original holding has been fully liquidated.
            average_exit = pos.exit_notional / pos.initial_quantity
            trades.append(Trade(symbol, pos.entry_time, when, pos.entry_price, average_exit,
                                pos.initial_stop, pos.initial_quantity, average_exit/pos.entry_price-1,
                                pos.realized_pnl/entry_cost, i-pos.entry_index, pos.reason, pending_exit[symbol],
                                pos.realized_pnl, pos.exit_fees+pos.initial_entry_fee))
        cash += notional-fee
        total_fees += fee
        turnover += notional
        log(when, symbol, 'SELL', 'filled', pending_exit[symbol], quantity=quantity, price=price, fee=fee,
            remaining_quantity=remaining, position_closed=closed,
            fill_pnl=pnl, position_pnl=pos.realized_pnl,
            position_entry_cost=entry_cost, position_net_return=pos.realized_pnl/entry_cost,
            position_quantity_before=pos.quantity, closed_position_fraction=quantity/pos.quantity,
            execution_model=execution_model)
        if closed:
            del positions[symbol]
        else:
            pos.quantity = remaining
            if pending_exit[symbol] in ('wave_volume_shadows_reduce', 'wave_gap_reversal_reduce'):
                pos.wave_reduced = True
            if pending_exit[symbol] == 'inverse_n_close_reduce_90':
                pos.staged_exit.inverse_index = evidence['inverse_observed_index']
            pos.entry_fee -= allocated_entry_fee
        del pending_exit[symbol]
        exit_evidence.pop(symbol, None)
        return True

    def execute_entry(symbol, i, bar, when, signal, execution_price, *, at_close=False, intraday=None):
        nonlocal cash, total_fees, turnover
        price = execution_price * (1+slip)
        buyable = bar.close_buyable if at_close and bar.close_buyable is not None else bar.buyable
        timing = dict(execution_model='same_day_close', decision_source='daily_close_simulation',
                      decision_timestamp=when.replace(hour=15).isoformat(),
                      execution_timestamp=when.replace(hour=15).isoformat()) if at_close else {}
        if intraday is not None:
            buyable, timing = intraday['buyable'], intraday['timing']
        if (at_close and intraday is None and not buyable and config.nonflat_limit_close_fill
                and bar.nonflat_close_buyable is True and bar.volume > 0 and bar.high > bar.low):
            buyable = True
            # The user-selected daily model assumes queue access at the close;
            # positive slippage cannot price a fill above the observed limit.
            price = execution_price
            timing = dict(timing, fill_assumption='nonflat_limit_close_without_queue_verification',
                          applied_slippage_bps=0.0)
        reason = ('position_limit' if len(positions) >= config.max_positions else
                  'already_held' if symbol in positions else
                  ('not_buyable_intraday' if intraday is not None else 'not_buyable_at_close' if at_close else 'not_buyable') if not buyable else
                  ('invalidated_at_close' if at_close else 'invalidated_at_open') if execution_price <= signal.invalidation_price else
                  'entry_gap' if execution_price / signal.reference_price-1 > config.max_entry_gap else '')
        if reason:
            log(when, symbol, 'BUY', 'cancelled', reason, signal_timestamp=signal.timestamp.isoformat(), **timing)
            return
        equity = cash + sum(p.quantity*marks[s] for s, p in positions.items())
        risk = price-signal.invalidation_price
        target = signal.target_price if signal.target_price is not None else price+config.take_profit_r*risk
        detail = dict(signal_timestamp=signal.timestamp.isoformat(), reference_price=signal.reference_price,
            price=price, stop_price=signal.invalidation_price, target_price=target,
            gross_reward_risk=(target-price)/risk, required_reward_risk=signal.minimum_reward_risk,
            net_reward_risk_filter=config.net_reward_risk_filter,
            adjustment_factor=bar.adjustment_factor, equity_at_open=equity,
            cash_at_open=cash, risk_budget=equity*config.risk_fraction,
            raw_lot_size=config.lot_size, **timing)
        if at_close or intraday is not None:
            detail['equity_at_execution'] = detail.pop('equity_at_open')
            detail['cash_at_execution'] = detail.pop('cash_at_open')
        if target <= price:
            log(when, symbol, 'BUY', 'cancelled', 'target_exhausted_at_close' if at_close else 'target_exhausted_at_open', **detail)
            return
        caps = dict(position_weight=equity*config.max_position_weight/price,
                    risk_budget=equity*config.risk_fraction/risk,
                    liquidity=capacity(symbol, i, bar), cash=cash/price)
        units = min(caps.values())
        lot = config.lot_size/bar.adjustment_factor
        detail.update({name+'_shares': value*bar.adjustment_factor for name,value in caps.items()})
        detail['limiting_constraint'] = min(caps, key=caps.get)
        detail['one_lot_notional'] = price*lot
        detail['one_lot_price_risk'] = risk*lot
        lots = math.floor(units/lot)
        quantity = max(0, lots)*lot
        minimum_quantity = config.minimum_entry_shares/bar.adjustment_factor
        fee = transaction_fee(price*quantity, when, False, config)
        while lots > 0 and price*quantity+fee > cash:
            lots -= 1
            quantity = lots*lot
            fee = transaction_fee(price*quantity, when, False, config)
        if lots <= 0 or quantity+1e-8 < minimum_quantity:
            constraint = min(caps, key=caps.get) if units < max(lot,minimum_quantity) else 'cash_after_fees'
            log(when, symbol, 'BUY', 'cancelled', constraint+'_below_one_lot', **detail)
            return
        # Evaluate reward/risk at the actual slipped execution price and sized
        # quantity, including round-trip fees and assumed exit slippage.
        reward = (target*(1-slip)-price)*quantity-fee-transaction_fee(
            target*(1-slip)*quantity, when, True, config)
        loss = (price-signal.invalidation_price*(1-slip))*quantity+fee+transaction_fee(
            signal.invalidation_price*(1-slip)*quantity, when, True, config)
        detail.update(quantity=quantity, raw_shares=quantity*bar.adjustment_factor,
            fee=fee, expected_net_reward=reward, expected_net_loss=loss, net_reward_risk=reward/loss)
        if config.net_reward_risk_filter and signal.minimum_reward_risk and reward/loss < signal.minimum_reward_risk:
            log(when, symbol, 'BUY', 'cancelled', 'insufficient_net_reward_risk', **detail)
            return
        # At execution price, buying exchanges cash for shares; only the fee reduces account equity.
        entry_position_value = price * quantity
        account_equity_after_fill = equity - fee
        detail.update(
            entry_position_value=entry_position_value,
            account_equity_after_fill=account_equity_after_fill,
            entry_position_weight=entry_position_value / account_equity_after_fill,
        )
        cash -= price*quantity+fee
        total_fees += fee
        turnover += price*quantity
        positions[symbol] = _Position(i, when, price, quantity, fee,
                                      signal.invalidation_price, signal.invalidation_price,
                                      target, signal.reason, initial_quantity=quantity, initial_entry_fee=fee,
                                      wave_events=wave_lookup[symbol].get(signal.trigger_timestamp, []))
        log(when, symbol, 'BUY', 'filled', signal.reason, **detail)

    for tick, when in enumerate(sorted(calendar)):
        current = calendar[when]
        daily_fallback = {}
        wave_clear_symbols = set()
        for symbol, (_, bar) in current.items():
            marks[symbol], mark_times[symbol] = bar.open, when.isoformat()
        # Free cash from eligible exits before sizing new entries at this open.
        for symbol in sorted(list(pending_exit)):
            if symbol not in positions:
                del pending_exit[symbol]
                continue
            if symbol not in current:
                continue
            i, bar = current[symbol]
            if exit_evidence.get(symbol, {}).get('execution_model') not in ('same_day_close', 'intraday_5m_next_open'):
                execute_exit(symbol, i, bar, when, bar.open, 'next_open')
        ranked = sorted(pending_entry, key=lambda s: (-(pending_entry[s][0].rvol or 0), s))
        for symbol in ranked:
            signal, created = pending_entry[symbol]
            if tick-created > config.entry_ttl_bars:
                log(when, symbol, 'BUY', 'cancelled', 'expired', signal_timestamp=signal.timestamp.isoformat())
                del pending_entry[symbol]
                continue
            if symbol not in current:
                continue
            del pending_entry[symbol]
            i, bar = current[symbol]
            execute_entry(symbol, i, bar, when, signal, bar.open)
        # Intraday entries precede daily observations and cannot use cash released at the close.
        for symbol, (i, bar) in sorted(current.items(), key=lambda item:
                entry_executions.get((item[0], item[1][0]), {}).get('timing', {}).get('execution_timestamp', '')):
            entry = entry_executions.get((symbol, i))
            if entry is not None:
                pending_entry.pop(symbol, None)
                marks[symbol] = entry['price']
                execute_entry(symbol, i, bar, when, entry['signal'], entry['price'], intraday=entry)
        # Minute decisions use only completed bars; the next interval's opening
        # price is a modeled fill, not a broker acknowledgement.
        if config.staged_exit_intraday:
            assert minute_loader is not None
            for symbol in sorted(list(positions)):
                if symbol not in current or symbol in pending_exit:
                    continue
                i, bar = current[symbol]
                pos = positions[symbol]
                if pos.entry_index == i and (symbol, i) in entry_executions:
                    continue  # A-share T+1: no same-session staged liquidation of this new holding.
                state = pos.staged_exit
                if state.recovered or state.exit_requested or state.reduction_target >= 0.65:
                    continue
                if state.support_index is None:
                    support = next((j for j in range(i - 2, 0, -1)
                                    if grouped[symbol][j].low < min(grouped[symbol][j - 1].low,
                                                                      grouped[symbol][j + 1].low)), None)
                    if support is None or bar.low >= grouped[symbol][support].low:
                        continue
                elif bar.low >= grouped[symbol][state.support_index].low:
                    continue
                try:
                    minute = minute_loader(bar)
                except MinuteCoverageError as exc:
                    if not config.missing_minute_daily_fallback:
                        raise
                    evidence = dict(symbol=symbol, date=bar.timestamp.date().isoformat(),
                                    reason=exc.coverage.get('reason', 'minute_history_missing'), coverage=exc.coverage,
                                    execution_model='same_day_close')
                    daily_fallback[symbol] = evidence
                    minute_fallbacks.append(evidence)
                    continue
                day_low = math.inf
                day_high = 0.0
                for offset, observed in enumerate(minute[:-1]):
                    day_low = min(day_low, observed.low * bar.adjustment_factor)
                    day_high = max(day_high, observed.high * bar.adjustment_factor)
                    if not time(14, 30) <= observed.timestamp.time() < time(15):
                        continue
                    price = observed.close * bar.adjustment_factor
                    previous_target = state.reduction_target
                    decision = observe_intraday_staged_exit(grouped[symbol], i, state, day_low, price)
                    if decision is None:
                        continue
                    following = minute[offset + 1]
                    pending_exit[symbol] = decision['reason']
                    exit_evidence[symbol] = dict(
                        signal_timestamp=observed.timestamp.isoformat(),
                        decision_timestamp=observed.timestamp.isoformat(),
                        **{key: value for key, value in decision.items() if key != 'reason'},
                        decision_reason=decision['reason'], stop_price=pos.stop, target_price=pos.target,
                        observed_low=day_low, observed_high=day_high, observed_close=price,
                        minute_next_open_raw=following.open,
                        decision_source='completed_five_minute_bar',
                        execution_model='intraday_5m_next_open',
                    )
                    filled = execute_exit(symbol, i, bar, observed.timestamp,
                                          following.open * bar.adjustment_factor, 'intraday_5m_next_open')
                    if not filled:
                        state.reduction_target = previous_target
                if exit_evidence.get(symbol, {}).get('execution_model') == 'intraday_5m_next_open':
                    pending_exit.pop(symbol, None)
                    exit_evidence.pop(symbol, None)
        # Old stop applies to today's bar. Newly observed trailing levels apply tomorrow.
        for symbol, pos in positions.items():
            if symbol not in current or (symbol in pending_exit and exit_evidence[symbol].get('exit_fraction', 1) == 1):
                continue
            i, bar = current[symbol]
            hard_reason = None
            entry = entry_executions.get((symbol, i)) if pos.entry_index == i else None
            risk_low = entry['remaining_low'] if entry else bar.low
            risk_high = entry['remaining_high'] if entry else bar.high
            if risk_low <= pos.stop:
                hard_reason = 'structural_stop_observed'
            elif config.exit_on_target and risk_high >= pos.target:
                hard_reason = 'target_observed'
            elif i-pos.entry_index >= config.max_hold_bars:
                hard_reason = 'time_exit'
            inverse_failure = (observe_inverse_resistance_exit(grouped[symbol], i, pos.staged_exit)
                               if config.inverse_n_close_reduce else None)
            volume_exit = (observe_volume_down_exit(grouped[symbol], i, pos.staged_exit,
                               positive_n_index=n_context[symbol][i] if config.small_n_reduction else None,
                               small_body_max_fraction=config.small_body_max_fraction,
                               small_body_lookback=config.small_body_lookback)
                           if config.volume_down_exit else None)
            # Evaluate close-known N risk only after intraday execution. It may
            # supersede daily partial orders, never erase earlier minute fills.
            volume_inverse = (config.volume_inverse_n_clear and i > 0
                and bar.volume > grouped[symbol][i - 1].volume
                and any(signal.symbol == symbol and signal.side == 'EXIT'
                        and 'inverse_n_risk_exit' in signal.reason.split('|')
                        for signal in signal_map.get(when, [])))
            wave_exit = (observe_wave_exhaustion(grouped[symbol], i, pos.wave_events, config,
                                                reduced=pos.wave_reduced) if config.wave_exhaustion_exit else None)
            pressure = trend_flip_risks[symbol].get(i) or pressure_risks[symbol].get(i)
            if wave_exit is not None and wave_exit['exit_fraction'] == 1:
                wave_clear_symbols.add(symbol)
                pending_exit[symbol] = wave_exit['reason']
                exit_evidence[symbol] = dict(signal_timestamp=when.isoformat(),
                    **{key: value for key, value in wave_exit.items() if key != 'reason'},
                    decision_reason=wave_exit['reason'], decision_source='wave_exhaustion_risk')
            elif pressure is not None:
                pending_exit[symbol] = pressure['reason']
                exit_evidence[symbol] = dict(signal_timestamp=when.isoformat(),
                    **{key: value for key, value in pressure.items() if key != 'reason'},
                    decision_reason=pressure['reason'], decision_source='overhead_pressure_risk',
                    **({'minute_fallback': daily_fallback[symbol]} if symbol in daily_fallback else {}))
            elif volume_inverse:
                pending_exit[symbol] = 'volume_inverse_n_clear'
                exit_evidence[symbol] = dict(signal_timestamp=when.isoformat(),
                    decision_reason='volume_inverse_n_clear', decision_source='strategy_exit_signal',
                    execution_model='same_day_close', exit_fraction=1.0,
                    observed_low=bar.low, observed_close=bar.close,
                    observed_volume=bar.volume, previous_volume=grouped[symbol][i - 1].volume,
                    **({'minute_fallback': daily_fallback[symbol]} if symbol in daily_fallback else {}))
            elif volume_exit is not None and volume_exit['exit_fraction'] == 1 and inverse_failure is None:
                pending_exit[symbol] = volume_exit['reason']
                exit_evidence[symbol] = dict(signal_timestamp=when.isoformat(),
                    **{key: value for key, value in volume_exit.items() if key != 'reason'},
                    decision_reason=volume_exit['reason'], decision_source='volume_down_exit',
                    observed_low=bar.low, observed_close=bar.close,
                    **({'minute_fallback': daily_fallback[symbol]} if symbol in daily_fallback
                       and volume_exit['execution_model'] == 'same_day_close' else {}))
            elif hard_reason:
                pending_exit[symbol] = hard_reason
                exit_evidence[symbol] = dict(signal_timestamp=when.isoformat(),
                    decision_reason=pending_exit[symbol], stop_price=pos.stop, target_price=pos.target,
                    observed_low=bar.low, observed_high=bar.high, observed_close=bar.close,
                    bars_held=i-pos.entry_index, decision_source='close_observed_risk_control')
            elif inverse_failure is not None:
                pending_exit[symbol] = inverse_failure['reason']
                exit_evidence[symbol] = dict(signal_timestamp=when.isoformat(),
                    **{key: value for key, value in inverse_failure.items() if key != 'reason'},
                    decision_reason=inverse_failure['reason'], decision_source='inverse_n_resistance_failure',
                    observed_close=bar.close)
            elif wave_exit is not None:
                pending_exit[symbol] = wave_exit['reason']
                exit_evidence[symbol] = dict(signal_timestamp=when.isoformat(),
                    **{key: value for key, value in wave_exit.items() if key != 'reason'},
                    decision_reason=wave_exit['reason'], decision_source='wave_exhaustion_risk')
            elif volume_exit is not None:
                pending_exit[symbol] = volume_exit['reason']
                exit_evidence[symbol] = dict(signal_timestamp=when.isoformat(),
                    **{key: value for key, value in volume_exit.items() if key != 'reason'},
                    decision_reason=volume_exit['reason'], decision_source='volume_down_exit',
                    observed_low=bar.low, observed_close=bar.close)
            elif config.staged_exit_enabled:
                decision = observe_staged_exit(grouped[symbol], i, pos.staged_exit, config.initial_reduction_fraction,
                    tiered=symbol in daily_fallback or (config.staged_exit_same_day and not config.staged_exit_intraday)) if (
                        symbol in daily_fallback or not config.staged_exit_intraday or pos.staged_exit.breakdown_index is not None) else None
                if decision is not None:
                    pending_exit[symbol] = decision['reason']
                    exit_evidence[symbol] = dict(signal_timestamp=when.isoformat(),
                        **{key: value for key, value in decision.items() if key != 'reason'},
                        decision_reason=decision['reason'], stop_price=pos.stop, target_price=pos.target,
                        observed_low=bar.low, observed_high=bar.high, observed_close=bar.close,
                        decision_source='staged_support_break',
                        execution_model='same_day_close' if config.staged_exit_same_day or symbol in daily_fallback else 'next_open',
                        **({'minute_fallback': daily_fallback[symbol]} if symbol in daily_fallback else {}))
        # Daily close matching is explicit; it does not reconstruct a 14:30 quote.
        for symbol in sorted(list(pending_exit)):
            if symbol in positions and symbol in current and exit_evidence.get(symbol, {}).get('execution_model') == 'same_day_close':
                i, bar = current[symbol]
                execute_exit(symbol, i, bar, when, bar.close, 'same_day_close')
        for signal in signal_map.get(when, []):
            symbol = signal.symbol
            if signal.side == 'LONG' and (symbol, signal.bar_index) in entry_executions:
                continue
            if signal.side == 'EXIT':
                pending_entry.pop(symbol, None)
                if symbol in positions:
                    if symbol not in pending_exit or exit_evidence[symbol].get('exit_fraction', 1) < 1:
                        pos = positions[symbol]
                        inverse_reduction = ((config.inverse_n_close_reduce or (config.inverse_n_after_reduction
                            and pos.staged_exit.breakdown_index is not None
                            and pos.quantity < pos.initial_quantity - 1e-8))
                            and signal.reason == 'inverse_n_risk_exit')
                        if inverse_reduction:
                            lot = config.lot_size / current[symbol][1].adjustment_factor
                            remaining_to_sell = pos.initial_quantity * 0.9 - (pos.initial_quantity - pos.quantity)
                            if math.floor(remaining_to_sell / lot + 1e-9) <= 0:
                                continue
                        pending_exit[symbol] = (('inverse_n_close_reduce_90' if config.inverse_n_close_reduce
                                                else 'inverse_n_after_reduction_90') if inverse_reduction else signal.reason)
                        exit_evidence[symbol] = dict(signal_timestamp=signal.timestamp.isoformat(),
                            decision_reason=signal.reason, stop_price=positions[symbol].stop,
                            target_price=positions[symbol].target, observed_close=signal.reference_price,
                            decision_source='strategy_exit_signal',
                            **(dict(exit_fraction=0.9, exit_target_fraction=0.9,
                                    original_signal_reason=signal.reason) if inverse_reduction else {}))
                        if inverse_reduction and config.inverse_n_close_reduce:
                            i, bar = current[symbol]
                            exit_evidence[symbol]['execution_model'] = 'same_day_close'
                            exit_evidence[symbol]['inverse_observed_index'] = signal.bar_index
                            execute_exit(symbol, i, bar, when, bar.close, 'same_day_close')
            elif symbol in positions:
                pos = positions[symbol]
                if pos.stop < signal.invalidation_price < current[symbol][1].close:
                    pos.stop = signal.invalidation_price
            elif (current[symbol][0] not in pressure_risks[symbol]
                  and current[symbol][0] not in trend_flip_risks[symbol] and symbol not in wave_clear_symbols):
                pending_entry[symbol] = (signal, tick)
        for symbol, (_, bar) in current.items():
            marks[symbol] = bar.close
        if config.entry_at_close:
            # Match only after all close-time exits, using close marks for every
            # holding. Today's earlier extrema cannot stop out a new close fill.
            for symbol in sorted(list(pending_entry), key=lambda s: (-(pending_entry[s][0].rvol or 0), s)):
                signal, created = pending_entry[symbol]
                if created != tick or symbol not in current:
                    continue
                del pending_entry[symbol]
                i, bar = current[symbol]
                if any(s.symbol == symbol and s.side == 'EXIT' for s in signal_map.get(when, [])):
                    log(when, symbol, 'BUY', 'cancelled', 'same_day_exit_priority',
                        signal_timestamp=signal.timestamp.isoformat(), execution_model='same_day_close')
                    continue
                execute_entry(symbol, i, bar, when, signal, bar.close, at_close=True)
        market_value = sum(p.quantity*marks[s] for s, p in positions.items())
        equity = cash+market_value
        if cash < -1e-6 or equity <= 0:
            raise ArithmeticError('portfolio cash/equity invariant failed')
        curve.append(dict(timestamp=when.isoformat(), cash=cash, market_value=market_value,
                          equity=equity, exposure=market_value/equity, positions=len(positions)))
    opened = [dict(symbol=s, entry_time=p.entry_time.isoformat(), entry_price=p.entry_price,
                   quantity=p.quantity, mark=marks[s], mark_time=mark_times[s],
                   unrealized_pnl=p.quantity*(marks[s]-p.entry_price)-p.entry_fee,
                   realized_pnl=p.realized_pnl,
                   entry_cost=p.entry_price*p.initial_quantity+p.initial_entry_fee,
                   total_pnl=p.realized_pnl+p.quantity*(marks[s]-p.entry_price)-p.entry_fee,
                   net_return=(p.realized_pnl+p.quantity*(marks[s]-p.entry_price)-p.entry_fee)
                       /(p.entry_price*p.initial_quantity+p.initial_entry_fee),
                   pending_exit=pending_exit.get(s)) for s, p in sorted(positions.items())]
    metrics = equity_metrics(curve, config.initial_capital)
    metrics.update(summarize_trades(trades))
    metrics['realized_pnl'] = sum(t.pnl for t in trades) + sum(p.realized_pnl for p in positions.values())
    metrics['unrealized_pnl'] = sum(p['unrealized_pnl'] for p in opened)
    metrics['total_pnl'] = metrics['realized_pnl'] + metrics['unrealized_pnl']
    metrics.update(fees=total_fees, turnover_notional=turnover, open_positions=len(opened),
                   cancelled_orders=sum(o['status']=='cancelled' for o in orders),
                   deferred_exits=sum(o['status']=='deferred' for o in orders),
                   unexecuted_end_signals=len(pending_entry))
    entries = sum(o['side']=='BUY' and o['status']=='filled' for o in orders)
    metrics.update(entry_fills=entries, evidence_status=(
        'no_entry_fills' if not entries else 'open_positions_only' if not trades else 'closed_trades_observed'))
    return BacktestResult(trades, metrics, curve, orders, opened, minute_fallbacks)
