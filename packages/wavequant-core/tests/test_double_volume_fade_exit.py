"""星网真实K线直接识别10/15前高；账户测试不注入任何趋势高点。"""

import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest

from wavequant.application.analytics.backtest import AccountDay, BacktestResult, portfolio_days
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar, Signal
from wavequant.domain.strategies.double_volume_fade_exit import (
    DOUBLE_VOLUME_BULLISH_FADE_REDUCE, observe_double_volume_fade,
)


def sample() -> list[Bar]:
    raw = json.loads((Path(__file__).parent / 'fixtures/xingwang_2019_dual_squeeze.json').read_text())
    return [Bar(datetime.fromisoformat(row[0]), raw['symbol'], *row[1:])
            for row in raw['bars'] if '2020-10-09' <= row[0] <= '2020-10-22']


def test_real_candles_identify_october15_high_without_trend_history() -> None:
    bars = sample()
    proof = observe_double_volume_fade(bars, 8)
    assert proof is not None
    assert proof['reason'] == DOUBLE_VOLUME_BULLISH_FADE_REDUCE
    assert proof['exit_fraction'] == proof['exit_target_fraction'] == .9
    assert proof['execution_model'] == 'same_day_close'
    assert proof['decision_timestamp'] == '2020-10-21T15:00:00'
    assert proof['bullish_date'] == '2020-10-20'
    assert proof['breakout_key_date'] == '2020-10-15'
    assert proof['breakout_key_high'] == bars[4].high
    assert proof['breakout_key_known_index'] == 5
    assert proof['double_volume_reference'] == 8_149_185
    assert proof['double_volume_ratio'] == pytest.approx(17_130_728 / 8_149_185)
    assert bars[8].volume < bars[7].volume
    assert observe_double_volume_fade(bars, 9) is None


@pytest.mark.parametrize('case', [
    'below_double', 'zero_reference', 'bullish_doji', 'bullish_bearish',
    'response_doji', 'response_bullish', 'equal_close', 'higher_close',
    'no_breakout', 'equal_high', 'no_pullback', 'already_broken',
    'bullish_factor_change', 'response_factor_change', 'inactive_bullish',
    'inactive_response', 'different_symbol',
])
def test_breakout_is_required_alongside_strict_price_and_volume_boundaries(case: str) -> None:
    bars = sample()
    if case == 'below_double':
        bars[7] = replace(bars[7], volume=bars[6].volume * 2 - 1)
    elif case == 'zero_reference':
        bars[6] = replace(bars[6], volume=0)
    elif case in ('bullish_doji', 'bullish_bearish'):
        bars[7] = replace(bars[7], open=bars[7].close + (0.1 if case == 'bullish_bearish' else 0))
    elif case in ('response_doji', 'response_bullish'):
        bars[8] = replace(bars[8], open=bars[8].close - (0.1 if case == 'response_bullish' else 0))
    elif case in ('equal_close', 'higher_close'):
        bars[8] = replace(bars[8], close=bars[7].close + (0.005 if case == 'higher_close' else 0))
    elif case in ('no_breakout', 'equal_high'):
        bars[4] = replace(bars[4], high=bars[7].high + (.1 if case == 'no_breakout' else 0))
    elif case == 'no_pullback':
        bars = [replace(bar, high=29 + index * .01) for index, bar in enumerate(bars)]
    elif case == 'already_broken':
        bars[6] = replace(bars[6], high=bars[7].high + .1)
    elif case in ('bullish_factor_change', 'response_factor_change'):
        index = 7 if case == 'bullish_factor_change' else 8
        bars[index] = replace(bars[index], adjustment_factor=2)
    elif case in ('inactive_bullish', 'inactive_response'):
        index = 7 if case == 'inactive_bullish' else 8
        bars[index] = replace(bars[index], raw_trading_active=False)
    else:
        bars[8] = replace(bars[8], symbol='sz.000001')
    assert observe_double_volume_fade(bars, 8) is None


def test_exact_double_and_tiny_response_volume_do_not_require_small_body() -> None:
    bars = sample()
    bars[7] = replace(bars[7], volume=bars[6].volume * 2)
    bars[8] = replace(bars[8], volume=1, low=27, close=27)
    proof = observe_double_volume_fade(bars, 8)
    assert proof is not None and proof['double_volume_ratio'] == 2


def test_prefix_and_future_tail_do_not_change_breakout_evidence() -> None:
    bars = sample()
    expected = observe_double_volume_fade(bars, 8)
    assert observe_double_volume_fade(bars[:9], 8) == expected
    bars[9] = replace(bars[9], open=20, high=40, low=19, close=39, volume=100_000_000)
    assert observe_double_volume_fade(bars, 8) == expected
    bars[8] = replace(bars[8], open=bars[8].close)
    assert observe_double_volume_fade(bars, 8) is None


def account(bars: list[Bar], capital: int = 32_000, *, enabled: bool = True,
            stop: float = 20, start_index: int = 0) -> BacktestResult:
    signal = Signal(bars[7].timestamp, bars[7].symbol, 7, 'LONG', bars[7].close,
                    stop, 'fixture', bars[7].timestamp, 0, None, 'fixture', 60)
    config = StrategyConfig(trend_flip_adverse_exit=False, volume_down_exit=enabled,
        entry_at_close=True, exit_on_target=False, initial_capital=capital,
        max_position_weight=1, risk_fraction=1, max_participation=1, max_hold_bars=100,
        commission_bps_per_side=0, slippage_bps_per_side=0, minimum_commission=0,
        a_share_taxes=False, net_reward_risk_filter=False)
    runner = portfolio_days({bars[0].symbol: bars}, [], config, live=True)
    result = None
    try:
        next(runner)
        for index in range(len(bars)):
            day = AccountDay(index, [signal] if index == 7 else [], {}, [], {}, {},
                             trading_window_start=start_index if index >= start_index else 0)
            try:
                runner.send(day)
            except StopIteration as completed:
                result = completed.value
    finally:
        runner.close()
    assert isinstance(result, BacktestResult)
    return result


@pytest.mark.parametrize('capital', [32_000, 320_000])
@pytest.mark.parametrize('start_index', [0, 8])
def test_account_reduces_on_october21_without_any_formal_high(capital: int, start_index: int) -> None:
    bars = sample()
    result = account(bars, capital, start_index=start_index)
    buy, sell = [order for order in result.orders if order['status'] == 'filled']
    assert sell['reason'] == DOUBLE_VOLUME_BULLISH_FADE_REDUCE
    assert buy['quantity'] * .9 <= sell['quantity'] <= buy['quantity']
    assert sell['remaining_quantity'] <= buy['quantity'] * .1
    assert sell['price'] == bars[8].close
    assert sell['execution_timestamp'] == '2020-10-21T15:00:00'


def test_full_stop_wins_over_partial_reduction() -> None:
    bars = sample()
    result = account(bars, stop=28.5)
    filled = [order for order in result.orders if order['status'] == 'filled']
    assert filled[-1]['reason'] == 'structural_stop_observed'
    assert filled[-1]['remaining_quantity'] == 0
    assert not any(order['reason'] == DOUBLE_VOLUME_BULLISH_FADE_REDUCE for order in filled)


def test_locked_response_does_not_fabricate_close_fill() -> None:
    bars = sample()
    bars[8] = replace(bars[8], sellable=False, close_sellable=False)
    result = account(bars)
    assert any(order['side'] == 'SELL' and order['status'] == 'deferred'
               and order['reason'] == 'not_sellable' for order in result.orders)
    assert not any(order['side'] == 'SELL' and order['status'] == 'filled'
                   and order['timestamp'].startswith('2020-10-21') for order in result.orders)


def test_disabled_volume_risk_retains_previous_account_configuration() -> None:
    result = account(sample(), enabled=False)
    assert not any(order['side'] == 'SELL' for order in result.orders)
