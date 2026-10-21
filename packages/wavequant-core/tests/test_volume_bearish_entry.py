"""用户报价作为当棒回归；前棒是显式构造的规则边界，不冒充历史行情。"""

from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from wavequant.application.analytics.backtest import run_portfolio
from wavequant.domain.market_state.candle_strength import bullish_breakout_rejection
from wavequant.domain.market_state.volume_candle import (
    bearish_body_engulfs, volume_bearish_entry_rejection,
)
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar, Signal
from .test_qualified_gap_entry import sample, resolve, minutes


@pytest.mark.parametrize('date,ohlcv,previous_body,reason', [
    ('2023-08-28', (4.93, 4.93, 4.73, 4.74, 30649178), (4.70, 4.73),
     'volume_bearish_buy_blocked'),
    ('2023-12-18', (5.63, 5.63, 5.28, 5.32, 69475932), (5.40, 5.57),
     'volume_bearish_body_engulfing_buy_blocked'),
])
@pytest.mark.parametrize('symbol,scale', [('sh.600825', 1), ('sz.000001', 10)])
def test_supplied_bearish_quotes_block_close_and_next_open_orders(
    date, ohlcv, previous_body, reason, symbol, scale,
):
    current = Bar(datetime.fromisoformat(date), symbol,
        *(price * scale for price in ohlcv[:4]), ohlcv[4])
    opened, closed = (price * scale for price in previous_body)
    previous = Bar(current.timestamp - timedelta(days=1), symbol, opened,
        max(opened, closed), min(opened, closed), closed, current.volume / 2)
    bars = [previous, current]
    assert volume_bearish_entry_rejection(bars, 1) == reason
    assert bullish_breakout_rejection(current, previous) == reason
    signal = Signal(current.timestamp, symbol, 1, 'LONG', current.close,
        current.low / 2, 'test', previous.timestamp, 0, None, 'test', current.high * 2, 0)
    for at_close in (True, False):
        result = run_portfolio({symbol: bars}, [signal],
            StrategyConfig(entry_at_close=at_close, net_reward_risk_filter=False))
        assert not any(order['side'] == 'BUY' and order['status'] == 'filled' for order in result.orders)
        assert any(order['reason'] == reason for order in result.orders)


def test_latest_bearish_volume_and_body_edges_do_not_use_future_or_wicks():
    start = datetime(2023, 12, 14)
    bars = [Bar(start, 'sh.600825', 5.5, 5.6, 5.3, 5.4, 100),
        Bar(start + timedelta(days=1), 'sh.600825', 5.4, 6, 5, 5.57, 300),
        Bar(start + timedelta(days=4), 'sh.600825', 5.63, 5.63, 5.28, 5.32, 200)]
    assert volume_bearish_entry_rejection(bars, 2) == 'volume_bearish_body_engulfing_buy_blocked'
    assert bearish_body_engulfs(bars[2], bars[1])  # Previous wicks extend beyond today's body.
    assert bearish_body_engulfs(replace(bars[2], open=5.57, close=5.4), bars[1])
    assert volume_bearish_entry_rejection([*bars, replace(bars[2], volume=1)], 2) == volume_bearish_entry_rejection(bars, 2)
    assert volume_bearish_entry_rejection([*bars[:2], replace(bars[2], volume=100)], 2) is None
    assert volume_bearish_entry_rejection([*bars[:2], replace(bars[2], close=5.63)], 2) is None


@pytest.mark.parametrize('level', [1, 2, 3])
@pytest.mark.parametrize('has_minutes', [False, True])
@pytest.mark.parametrize('volume', [500000, 2000000])
def test_full_session_falling_gap_cannot_reenter_via_minutes_or_missing_minutes(level, has_minutes, volume):
    bars, generated, strategy = sample(level)
    minute = minutes(bars)
    bars[-1] = replace(bars[-1], low=9, close=10, volume=volume)
    loader = (lambda _: minute) if has_minutes else None
    result, executions, _ = resolve(bars, generated, strategy, loader)
    assert not result.signals and not executions
    assert any(row.get('candle_filter_model') == 'full_session_candle_filter' for row in result.audit)


def test_account_rejects_injected_minute_buy_and_add_on_on_falling_gap():
    bars, generated, strategy = sample()
    result, executions, _ = resolve(bars, generated, strategy, lambda _: minutes(bars))
    signal = result.signals[0]
    bars[-1] = replace(bars[-1], low=9, close=10, volume=2000000)
    earlier = replace(signal, timestamp=bars[3].timestamp, bar_index=3,
        trigger_timestamp=bars[2].timestamp, reference_price=bars[3].close)
    for signals in ([signal], [earlier, signal]):
        account = run_portfolio({signal.symbol: bars}, signals,
            StrategyConfig(entry_at_close=True, allow_add_on=True, net_reward_risk_filter=False),
            entry_executions=executions)
        orders = [row for row in account.orders if row['side'] == 'BUY'
            and row.get('signal_timestamp') == signal.timestamp.isoformat()]
        assert orders and all(row['status'] == 'cancelled' for row in orders)
        assert all(row['candle_filter_model'] == 'full_session_candle_filter' for row in orders)
