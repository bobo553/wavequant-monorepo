"""十倍量封板豁免及紧邻下一交易日收低全清；前后序行情为合成。"""

from dataclasses import replace
from datetime import datetime

import pytest

from wavequant.application.analytics.backtest import run_portfolio
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar, Signal
from wavequant.domain.strategies.staged_exit import (
    EXTREME_VOLUME_LIMIT_NEXT_CLEAR, EXTREME_VOLUME_CLEAR,
    StagedExitState, observe_volume_down_exit,
)


def scenario() -> list[Bar]:
    # 9/3使用用户显示OHLC/量及原始收盘12.21；前后日期报价显式合成。
    factor = 17.01 / 12.21
    rows = [
        ('2026-09-01', 15.0, 15.2, 14.9, 15.1, 4_000_000),
        ('2026-09-02', 15.2, 15.5, 15.1, 15.46, 5_800_000),
        ('2026-09-03', 17.01, 17.01, 16.67, 17.01, 58_420_300),
        ('2026-09-04', 16.8, 17.0, 16.6, 16.9, 4_000_000),
    ]
    return [Bar(datetime.fromisoformat(day), 'sh.601086', opened, high, low, close, volume,
                adjustment_factor=factor, raw_is_st=False, raw_trading_active=True)
            for day, opened, high, low, close, volume in rows]


def test_user_limit_quote_holds_then_lower_bullish_close_clears() -> None:
    bars = scenario()
    state = StagedExitState()
    assert observe_volume_down_exit(bars, 2, state) is None
    decision = observe_volume_down_exit(bars, 3, state)
    assert decision is not None and decision['reason'] == EXTREME_VOLUME_LIMIT_NEXT_CLEAR
    assert decision['exit_fraction'] == 1.0
    assert decision['execution_model'] == 'same_day_close'
    assert decision['warning_date'] == '2026-09-03'
    assert round(decision['warning_volume_multiple'], 2) == 10.07
    assert bars[3].close > bars[3].open  # 次日阳线但收盘比涨停日低也清仓。
    assert decision == observe_volume_down_exit(bars[:4], 3, StagedExitState())


@pytest.mark.parametrize('close', [17.01, 17.02])
def test_equal_or_higher_next_close_does_not_clear_or_extend_warning(close: float) -> None:
    bars = scenario()
    bars[3] = replace(bars[3], close=close, high=17.1)
    state = StagedExitState()
    assert observe_volume_down_exit(bars, 2, state) is None
    decision = observe_volume_down_exit(bars, 3, state)
    assert decision is None or decision['reason'] != EXTREME_VOLUME_LIMIT_NEXT_CLEAR
    bars.append(replace(bars[3], timestamp=datetime(2026, 9, 7), close=16.9, volume=3_000_000))
    decision = observe_volume_down_exit(bars, 4, state)
    assert decision is None or decision['reason'] != EXTREME_VOLUME_LIMIT_NEXT_CLEAR


@pytest.mark.parametrize('case', ['exact_ten', 'zero_baseline', 'open_board', 'changed_factor'])
def test_warning_requires_prior_extreme_volume_and_valid_sealed_limit(case: str) -> None:
    bars = scenario()
    if case == 'exact_ten':
        bars[2] = replace(bars[2], volume=58_000_000)
    elif case == 'zero_baseline':
        bars[1] = replace(bars[1], volume=0)
    elif case == 'open_board':
        bars[2] = replace(bars[2], close=16.99)
    else:
        bars[1] = replace(bars[1], adjustment_factor=1.0)
    decision = observe_volume_down_exit(bars, 3, StagedExitState())
    assert decision is None or decision['reason'] != EXTREME_VOLUME_LIMIT_NEXT_CLEAR
    if case in ('open_board', 'changed_factor'):
        decision = observe_volume_down_exit(bars, 2, StagedExitState())
        assert decision is not None and decision['reason'] == EXTREME_VOLUME_CLEAR


def test_one_price_limit_and_twenty_percent_board_are_supported() -> None:
    bars = scenario()
    bars[2] = replace(bars[2], low=17.01)
    assert observe_volume_down_exit(bars, 2, StagedExitState()) is None
    pair = [
        Bar(datetime(2026, 9, 2), 'sz.300001', 9.8, 10.0, 9.7, 10.0, 100.0),
        Bar(datetime(2026, 9, 3), 'sz.300001', 12.0, 12.0, 11.0, 12.0, 1001.0),
    ]
    assert observe_volume_down_exit(pair, 1, StagedExitState()) is None


@pytest.mark.parametrize('sellable', [True, False])
@pytest.mark.parametrize('opened', [16.8, 17.01, 17.1])
def test_account_lower_close_exit_regardless_of_open(sellable: bool, opened: float) -> None:
    bars = scenario()
    bars[3] = replace(bars[3], open=opened, high=17.1,
                      close_sellable=sellable, sellable=sellable)
    entry = bars[1]
    signal = Signal(entry.timestamp, entry.symbol, 1, 'LONG', entry.close, 12.0,
                    'system_wave_push_gap', entry.timestamp, 0, None, 'fixture', 30.0)
    config = StrategyConfig(volume_down_exit=True, entry_at_close=True, exit_on_target=False,
                            max_hold_bars=100, slippage_bps_per_side=0, max_participation=1)
    result = run_portfolio({entry.symbol: bars}, [signal], config)
    buys = [order for order in result.orders if order['side'] == 'BUY' and order['status'] == 'filled']
    assert len(buys) == 1
    sells = [order for order in result.orders if order['side'] == 'SELL']
    assert not any(order['timestamp'][:10] == '2026-09-03' for order in sells)
    if sellable:
        filled = [order for order in sells if order['status'] == 'filled']
        assert len(filled) == 1
        assert filled[0]['reason'] == EXTREME_VOLUME_LIMIT_NEXT_CLEAR
        assert filled[0]['quantity'] == buys[0]['quantity']
        assert filled[0]['remaining_quantity'] == 0
        assert filled[0]['execution_timestamp'] == '2026-09-04T15:00:00'
        assert filled[0]['price'] == pytest.approx(bars[3].close)
    else:
        assert not any(order['status'] == 'filled' for order in sells)
        assert any(order['reason'] == 'not_sellable' and order['exit_fraction'] == 1.0 for order in sells)
    prefix = run_portfolio({entry.symbol: bars[:3]}, [signal], config)
    assert prefix.orders == [order for order in result.orders if order['timestamp'][:10] < '2026-09-04']


@pytest.mark.parametrize('closed', [17.01, 17.1])
def test_lower_open_recovery_does_not_sell(closed: float) -> None:
    bars = scenario()
    bars[3] = replace(bars[3], open=16.8, high=17.1, close=closed)
    entry = bars[1]
    signal = Signal(entry.timestamp, entry.symbol, 1, 'LONG', entry.close, 12.0,
                    'system_wave_push_gap', entry.timestamp, 0, None, 'fixture', 30.0)
    config = StrategyConfig(volume_down_exit=True, entry_at_close=True, exit_on_target=False,
                            max_hold_bars=100, slippage_bps_per_side=0, max_participation=1)
    result = run_portfolio({entry.symbol: bars}, [signal], config)
    assert any(order['side'] == 'BUY' and order['status'] == 'filled' for order in result.orders)
    assert not any(order['side'] == 'SELL' for order in result.orders)


def test_late_seal_clears_same_day_and_cannot_arm_next_close_exception() -> None:
    bars = scenario()
    bars[2] = replace(bars[2], open=16.8)
    decision = observe_volume_down_exit(bars, 2, StagedExitState())
    assert decision is not None and decision['reason'] == EXTREME_VOLUME_CLEAR
    assert decision['exit_fraction'] == 1.0
    following = observe_volume_down_exit(bars, 3, StagedExitState())
    assert following is None or following['reason'] != EXTREME_VOLUME_LIMIT_NEXT_CLEAR
