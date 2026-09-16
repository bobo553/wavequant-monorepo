"""Run the shared-cash, close-observed/next-open daily research use case."""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime

from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar, Signal, Trade


@dataclass
class BacktestResult:
    trades: list[Trade]
    metrics: dict
    equity: list[dict] = field(default_factory=list)
    orders: list[dict] = field(default_factory=list)
    open_positions: list[dict] = field(default_factory=list)


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
                  start: str | None = None, end: str | None = None) -> BacktestResult:
    """Orders use only prior bars plus the next opening price. No terminal forced sale.

    Volume capacity is a prior-bar estimate, not an auction fill guarantee. Adjusted
    units approximate reinvested corporate actions, not a cash dividend ledger.
    Stops/targets observed in OHLC request next-open exits; they are NOT stop fills.
    """
    config.validate()
    calendar: dict[datetime, dict[str, tuple[int, Bar]]] = {}
    for symbol, bars in grouped.items():
        for i, bar in enumerate(bars):
            if bar.symbol != symbol or (i and bar.timestamp <= bars[i-1].timestamp):
                raise ValueError('bars must have matching symbols and strictly increasing timestamps')
            day = bar.timestamp.date().isoformat()
            if (start is None or day >= start) and (end is None or day <= end):
                calendar.setdefault(bar.timestamp, {})[symbol] = (i, bar)
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

    for tick, when in enumerate(sorted(calendar)):
        current = calendar[when]
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
            pos = positions[symbol]
            reason = ('T+1' if not config.allow_same_day_exit and when.date() == pos.entry_time.date()
                      else 'not_sellable' if not bar.sellable else
                      'liquidity_capacity' if pos.quantity > capacity(symbol, i, bar) + 1e-8 else '')
            if reason:
                log(when, symbol, 'SELL', 'deferred', reason)
                continue
            price = bar.open * (1-slip)
            notional = price * pos.quantity
            fee = transaction_fee(notional, when, True, config)
            cost = pos.entry_price * pos.quantity + pos.entry_fee
            pnl = notional - fee - cost
            trades.append(Trade(symbol, pos.entry_time, when, pos.entry_price, price,
                                pos.initial_stop, pos.quantity, price/pos.entry_price-1,
                                pnl/cost, i-pos.entry_index, pos.reason, pending_exit[symbol],
                                pnl, fee+pos.entry_fee))
            cash += notional-fee
            total_fees += fee
            turnover += notional
            log(when, symbol, 'SELL', 'filled', pending_exit[symbol], quantity=pos.quantity, price=price, fee=fee)
            del positions[symbol], pending_exit[symbol]
            exit_evidence.pop(symbol, None)
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
            price = bar.open * (1+slip)
            reason = ('position_limit' if len(positions) >= config.max_positions else
                      'already_held' if symbol in positions else
                      'not_buyable' if not bar.buyable else
                      'invalidated_at_open' if bar.open <= signal.invalidation_price else
                      'entry_gap' if bar.open / signal.reference_price-1 > config.max_entry_gap else '')
            if reason:
                log(when, symbol, 'BUY', 'cancelled', reason, signal_timestamp=signal.timestamp.isoformat())
                continue
            equity = cash + sum(p.quantity*marks[s] for s, p in positions.items())
            risk = price-signal.invalidation_price
            target = signal.target_price if signal.target_price is not None else price+config.take_profit_r*risk
            detail = dict(signal_timestamp=signal.timestamp.isoformat(), reference_price=signal.reference_price,
                price=price, stop_price=signal.invalidation_price, target_price=target,
                gross_reward_risk=(target-price)/risk, required_reward_risk=signal.minimum_reward_risk,
                adjustment_factor=bar.adjustment_factor, equity_at_open=equity,
                cash_at_open=cash, risk_budget=equity*config.risk_fraction,
                raw_lot_size=config.lot_size)
            if target <= price:
                log(when, symbol, 'BUY', 'cancelled', 'target_exhausted_at_open', **detail)
                continue
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
                continue
            # Evaluate reward/risk at the actual slipped opening price and sized
            # quantity, including round-trip fees and assumed exit slippage.
            reward = (target*(1-slip)-price)*quantity-fee-transaction_fee(
                target*(1-slip)*quantity, when, True, config)
            loss = (price-signal.invalidation_price*(1-slip))*quantity+fee+transaction_fee(
                signal.invalidation_price*(1-slip)*quantity, when, True, config)
            detail.update(quantity=quantity, raw_shares=quantity*bar.adjustment_factor,
                fee=fee, expected_net_reward=reward, expected_net_loss=loss, net_reward_risk=reward/loss)
            if signal.minimum_reward_risk and reward/loss < signal.minimum_reward_risk:
                log(when, symbol, 'BUY', 'cancelled', 'insufficient_net_reward_risk', **detail)
                continue
            cash -= price*quantity+fee
            total_fees += fee
            turnover += price*quantity
            positions[symbol] = _Position(i, when, price, quantity, fee,
                                          signal.invalidation_price, signal.invalidation_price,
                                          target, signal.reason)
            log(when, symbol, 'BUY', 'filled', signal.reason, **detail)
        # Old stop applies to today's bar. Newly observed trailing levels apply tomorrow.
        for symbol, pos in positions.items():
            if symbol not in current or symbol in pending_exit:
                continue
            i, bar = current[symbol]
            if bar.low <= pos.stop:
                pending_exit[symbol] = 'structural_stop_observed'
            elif bar.high >= pos.target:
                pending_exit[symbol] = 'target_observed'
            elif i-pos.entry_index >= config.max_hold_bars:
                pending_exit[symbol] = 'time_exit'
            if symbol in pending_exit:
                exit_evidence[symbol] = dict(signal_timestamp=when.isoformat(),
                    decision_reason=pending_exit[symbol], stop_price=pos.stop, target_price=pos.target,
                    observed_low=bar.low, observed_high=bar.high, observed_close=bar.close,
                    bars_held=i-pos.entry_index, decision_source='close_observed_risk_control')
        for signal in signal_map.get(when, []):
            symbol = signal.symbol
            if signal.side == 'EXIT':
                pending_entry.pop(symbol, None)
                if symbol in positions:
                    if symbol not in pending_exit:
                        pending_exit[symbol] = signal.reason
                        exit_evidence[symbol] = dict(signal_timestamp=signal.timestamp.isoformat(),
                            decision_reason=signal.reason, stop_price=positions[symbol].stop,
                            target_price=positions[symbol].target, observed_close=signal.reference_price,
                            decision_source='strategy_exit_signal')
            elif symbol in positions:
                pos = positions[symbol]
                if pos.stop < signal.invalidation_price < current[symbol][1].close:
                    pos.stop = signal.invalidation_price
            else:
                pending_entry[symbol] = (signal, tick)
        for symbol, (_, bar) in current.items():
            marks[symbol] = bar.close
        market_value = sum(p.quantity*marks[s] for s, p in positions.items())
        equity = cash+market_value
        if cash < -1e-6 or equity <= 0:
            raise ArithmeticError('portfolio cash/equity invariant failed')
        curve.append(dict(timestamp=when.isoformat(), cash=cash, market_value=market_value,
                          equity=equity, exposure=market_value/equity, positions=len(positions)))
    opened = [dict(symbol=s, entry_time=p.entry_time.isoformat(), entry_price=p.entry_price,
                   quantity=p.quantity, mark=marks[s], mark_time=mark_times[s],
                   unrealized_pnl=p.quantity*(marks[s]-p.entry_price)-p.entry_fee,
                   pending_exit=pending_exit.get(s)) for s, p in sorted(positions.items())]
    metrics = equity_metrics(curve, config.initial_capital)
    metrics.update(summarize_trades(trades))
    metrics.update(fees=total_fees, turnover_notional=turnover, open_positions=len(opened),
                   cancelled_orders=sum(o['status']=='cancelled' for o in orders),
                   deferred_exits=sum(o['status']=='deferred' for o in orders),
                   unexecuted_end_signals=len(pending_entry))
    entries = sum(o['side']=='BUY' and o['status']=='filled' for o in orders)
    metrics.update(entry_fills=entries, evidence_status=(
        'no_entry_fills' if not entries else 'open_positions_only' if not trades else 'closed_trades_observed'))
    return BacktestResult(trades, metrics, curve, orders, opened)
