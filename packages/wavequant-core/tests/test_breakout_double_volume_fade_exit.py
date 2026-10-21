"""真实六日行情配合显式已知高点输入；不代替完整趋势与账户实股回放。"""

import json
from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest

from wavequant.application.analytics.backtest import AccountDay, portfolio_days
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar, Signal
from wavequant.domain.strategies.breakout_shadow_exit import (
    BREAKOUT_CHILD_CLEAR, BREAKOUT_DOUBLE_VOLUME_FADE_REDUCE, breakout_double_volume_fade_days,
)
from wavequant.domain.strategies.trend_flip_exit import TrendFlipExitStream, trend_flip_exit_history


def sample(level: int = 1) -> tuple[list[Bar], dict[int, dict[int, list[dict[str, object]]]]]:
    raw = json.loads((Path(__file__).parent / 'fixtures/xingwang_2019_dual_squeeze.json').read_text())
    bars = [Bar(datetime.fromisoformat(row[0]), raw['symbol'], *row[1:])
            for row in raw['bars'] if '2020-10-15' <= row[0] <= '2020-10-22']
    high: dict[str, object] = dict(kind='H', index=0, value=bars[0].high,
                                  available_at=1, state='confirmed')
    return bars, {index: {level: [dict(high)]} for index in range(1, len(bars))}


def risks(bars: list[Bar], history: dict[int, dict[int, list[dict[str, object]]]],
          *, start_index: int = 0) -> dict[int, dict[str, object]]:
    result: dict[int, dict[str, object]] = {}
    for _, result in breakout_double_volume_fade_days(bars, history, start_index=start_index):
        pass
    return result


@pytest.mark.parametrize('level', [0, 1, 2, 3])
def test_xingwang_next_bearish_lower_close_reduces_without_response_volume(level: int) -> None:
    bars, history = sample(level)
    result = risks(bars, history)
    assert set(result) == {4}
    proof = result[4]
    assert proof['reason'] == BREAKOUT_DOUBLE_VOLUME_FADE_REDUCE
    assert proof['exit_fraction'] == proof['exit_target_fraction'] == .9
    assert proof['execution_model'] == 'same_day_close'
    assert proof['decision_timestamp'] == '2020-10-21T15:00:00'
    assert proof['breakout_attack_date'] == '2020-10-20'
    assert proof['breakout_attack_close'] == bars[3].close
    assert proof['breakout_key_high'] == bars[0].high
    assert proof['trend_level'] == level
    assert proof['breakout_double_volume_source'] == 'previous'
    assert proof['breakout_double_volume_reference'] == 8_149_185
    assert proof['breakout_double_volume_ratio'] == pytest.approx(17_130_728 / 8_149_185)
    assert bars[4].volume < bars[3].volume


@pytest.mark.parametrize('case', [
    'below_double', 'zero_reference', 'attack_doji', 'attack_bearish',
    'response_doji', 'response_bullish', 'equal_close', 'higher_close',
    'future_key', 'developing_key', 'display_key', 'no_key', 'touch_key',
    'already_broken', 'attack_factor_change', 'response_factor_change',
    'inactive_attack', 'inactive_response', 'different_symbol',
])
def test_strict_price_volume_and_known_high_boundaries(case: str) -> None:
    bars, history = sample()
    if case == 'below_double':
        bars[3] = replace(bars[3], volume=bars[2].volume * 2 - 1)
    elif case == 'zero_reference':
        bars[2] = replace(bars[2], volume=0)
    elif case in ('attack_doji', 'attack_bearish'):
        bars[3] = replace(bars[3], open=bars[3].close + (0.1 if case == 'attack_bearish' else 0))
    elif case in ('response_doji', 'response_bullish'):
        bars[4] = replace(bars[4], open=bars[4].close - (0.1 if case == 'response_bullish' else 0))
    elif case in ('equal_close', 'higher_close'):
        bars[4] = replace(bars[4], close=bars[3].close + (0.005 if case == 'higher_close' else 0))
    elif case in ('future_key', 'developing_key', 'display_key'):
        field, value = {'future_key': ('available_at', 3), 'developing_key': ('state', 'developing'),
                        'display_key': ('display_only', True)}[case]
        for levels in history.values():
            levels[1][0][field] = value
    elif case == 'no_key':
        history = {}
    elif case == 'touch_key':
        for levels in history.values():
            levels[1][0]['value'] = bars[3].high
    elif case == 'already_broken':
        bars[2] = replace(bars[2], high=bars[0].high + .1)
    elif case in ('attack_factor_change', 'response_factor_change'):
        index = 3 if case == 'attack_factor_change' else 4
        bars[index] = replace(bars[index], adjustment_factor=2)
    elif case in ('inactive_attack', 'inactive_response'):
        index = 3 if case == 'inactive_attack' else 4
        bars[index] = replace(bars[index], raw_trading_active=False)
    else:
        bars[4] = replace(bars[4], symbol='sz.000001')
    assert risks(bars, history) == {}


def test_exact_double_needs_neither_response_volume_nor_small_body() -> None:
    bars, history = sample()
    bars[3] = replace(bars[3], volume=bars[2].volume * 2)
    bars[4] = replace(bars[4], volume=1, low=27, close=27)
    assert risks(bars, history)[4]['breakout_double_volume_ratio'] == 2


def test_next_session_only_and_no_borrowed_breakout_before_window() -> None:
    bars, history = sample()
    assert risks(bars, history, start_index=4) == {}
    bars[4] = replace(bars[4], open=bars[4].close)
    assert risks(bars, history) == {}


def test_frozen_high_and_future_tail_cannot_rewrite_decision() -> None:
    bars, history = sample()
    expected = deepcopy(risks(bars, history)[4])
    history[3] = {}
    bars[5] = replace(bars[5], open=20, high=40, low=19, close=39, volume=100_000_000)
    assert risks(bars, history)[4] == expected


def test_stream_matches_each_prefix_and_preserves_full_clear_priority() -> None:
    bars, history = sample()
    stream = TrendFlipExitStream(bars)
    try:
        for index in range(len(bars)):
            assert stream.advance(index, history) == trend_flip_exit_history(bars[:index + 1], history)
        assert stream.advance(5, history)[4]['reason'] == BREAKOUT_DOUBLE_VOLUME_FADE_REDUCE
        bars[4] = replace(bars[4], volume=bars[3].volume + 1)
        combined = trend_flip_exit_history(bars, history)
        assert combined[4]['reason'] == BREAKOUT_CHILD_CLEAR
        assert combined[4]['exit_fraction'] == 1
    finally:
        stream.close()


@pytest.mark.parametrize('capital', [32_000, 320_000])
def test_account_sells_at_next_session_close_and_rounds_up_ninety_percent(capital: int) -> None:
    bars, history = sample()
    signal = Signal(bars[3].timestamp, bars[3].symbol, 3, 'LONG', bars[3].close,
                    20.0, 'fixture', bars[3].timestamp, 0, None, 'fixture', 60)
    config = StrategyConfig(trend_flip_adverse_exit=True, entry_at_close=True, exit_on_target=False,
        initial_capital=capital, max_position_weight=1, risk_fraction=1, max_participation=1,
        max_hold_bars=100, commission_bps_per_side=0, slippage_bps_per_side=0,
        minimum_commission=0, a_share_taxes=False, net_reward_risk_filter=False)
    runner = portfolio_days({bars[0].symbol: bars}, [], config, live=True)
    result = None
    try:
        next(runner)
        for index in range(len(bars)):
            day = AccountDay(index, [signal] if index == 3 else [], {}, [],
                             {key: row for key, row in history.items() if key <= index}, {})
            try:
                runner.send(day)
            except StopIteration as completed:
                result = completed.value
    finally:
        runner.close()
    assert result is not None
    buy, sell = [order for order in result.orders if order['status'] == 'filled']
    assert sell['reason'] == BREAKOUT_DOUBLE_VOLUME_FADE_REDUCE
    assert buy['quantity'] * .9 <= sell['quantity'] <= buy['quantity']
    assert sell['remaining_quantity'] <= buy['quantity'] * .1
    assert sell['price'] == bars[4].close
    assert sell['execution_timestamp'] == '2020-10-21T15:00:00'
