"""用户提供两根新华传媒K线；中间／次日行情和已确认高点为合成边界输入。"""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime

import pytest

from wavequant.application.analytics.backtest import run_portfolio
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar, Signal
from wavequant.domain.strategies.breakout_shadow_exit import (
    BREAKOUT_SHADOW_CLEAR, BREAKOUT_SHADOW_REDUCE, breakout_shadow_days,
)
from wavequant.domain.strategies.trend_flip_exit import TrendFlipExitStream, trend_flip_exit_history


def sample(level=1, symbol='sh.600825'):
    rows = [
        ('2023-12-18', 5.63, 5.63, 5.28, 5.32, 69_475_932),
        ('2023-12-19', 5.05, 5.30, 5.00, 5.18, 50_000_000),
        ('2024-01-08', 5.05, 5.30, 5.00, 5.18, 200_000_000),
        ('2024-01-09', 5.44, 5.69, 5.16, 5.25, 155_973_716),
        ('2024-01-10', 5.10, 5.80, 5.00, 5.70, 10_000_000),
    ]
    bars = [Bar(datetime.fromisoformat(day), symbol, *values) for day, *values in rows]
    high = dict(kind='H', index=0, value=5.63, available_at=1)
    history = {i: {level: [high]} for i in range(1, len(bars))}
    return bars, history


def risks(bars, history):
    result = {}
    for _, result in breakout_shadow_days(bars, history):
        pass
    return result


@pytest.mark.parametrize('level', [0, 1, 2, 3])
@pytest.mark.parametrize('symbol', ['sh.600825', 'sz.000001'])
def test_attack_reduces_ninety_and_only_next_open_clears(level, symbol):
    bars, history = sample(level, symbol)
    found = risks(bars, history)
    warning, clear = found[3], found[4]
    assert warning['reason'] == BREAKOUT_SHADOW_REDUCE
    assert warning['exit_target_fraction'] == .9
    assert warning['breakout_key_date'] == '2023-12-18'
    assert warning['breakout_key_high'] == 5.63
    assert warning['breakout_upper_shadow'] == .25
    assert warning['breakout_body'] == .19
    assert warning['bearish_reference_volume'] == 69_475_932
    assert warning['observed_volume'] < warning['previous_volume']
    assert warning['breakout_resistance_phase'] == 'attack'
    assert clear['reason'] == BREAKOUT_SHADOW_CLEAR
    assert clear['execution_model'] == 'next_open'
    assert clear['decision_timestamp'].endswith('09:30:00')
    assert 'exit_target_fraction' not in clear
    assert not {'observed_high', 'observed_low', 'observed_close', 'observed_volume'} & clear.keys()
    # 次日高收、缩量不改变开盘已成立的清仓决定。
    bars[4] = replace(bars[4], high=5.3, low=4.5, close=4.8, volume=900_000_000)
    assert risks(bars, history)[4] == clear


@pytest.mark.parametrize('case', ['touch', 'equal_shadow', 'bullish', 'doji', 'equal_volume',
                                 'future_key', 'missing_key', 'developing_key'])
def test_strict_boundaries_reject_without_a_known_breakout(case):
    bars, history = sample()
    if case == 'touch':
        bars[3] = replace(bars[3], high=5.63)
    elif case == 'equal_shadow':
        bars[3] = replace(bars[3], open=5.47)
    elif case == 'bullish':
        bars[3] = replace(bars[3], open=5.20)
    elif case == 'doji':
        bars[3] = replace(bars[3], open=5.25)
    elif case == 'equal_volume':
        bars[2] = replace(bars[2], volume=69_475_932)
        bars[3] = replace(bars[3], volume=69_475_932)
    elif case == 'future_key':
        for row in history.values():
            row[1][0]['available_at'] = 3
    elif case == 'missing_key':
        history = {}
    else:
        for row in history.values():
            row[1][0]['state'] = 'developing'
    assert 3 not in risks(bars, history)


def test_response_is_only_one_session_after_attack_and_no_twenty_day_minimum():
    bars, history = sample()
    bars[3] = replace(bars[3], open=5.40, close=5.65, volume=100_000_000)
    bars[4] = replace(bars[4], open=5.44, high=5.69, low=5.16, close=5.25, volume=155_973_716)
    warning = risks(bars, history)[4]
    assert warning['breakout_attack_index'] == 3
    assert warning['breakout_resistance_phase'] == 'response'
    assert warning['exit_target_fraction'] == .9
    later = [*bars[:4], replace(bars[3], timestamp=bars[4].timestamp),
             replace(bars[4], timestamp=datetime(2024, 1, 11))]
    history[5] = history[4]
    assert 5 not in risks(later, history)


@pytest.mark.parametrize('opened', [5.25, 5.26])
def test_equal_or_higher_next_open_expires_the_warning(opened):
    bars, history = sample()
    bars[4] = replace(bars[4], open=opened)
    bars.append(replace(bars[4], timestamp=datetime(2024, 1, 11), open=5.0))
    history[5] = history[4]
    assert not any(row['reason'] == BREAKOUT_SHADOW_CLEAR for row in risks(bars, history).values())


def test_prefix_stream_and_historical_key_correction_match_cold():
    bars, history = sample()
    expected = trend_flip_exit_history(bars, history)
    stream = TrendFlipExitStream(bars)
    try:
        for index in range(len(bars)):
            actual = deepcopy(stream.advance(index, history))
            assert actual == trend_flip_exit_history(bars[:index + 1], history)
            assert actual == {day: row for day, row in expected.items() if day <= index}
        revised = deepcopy(history)
        for row in revised.values():
            row[1][0]['value'] = 6.0
        assert stream.advance(4, revised) == trend_flip_exit_history(bars, revised)
        assert 3 not in stream.advance(4, revised)
    finally:
        stream.close()


def account(bars, history, monkeypatch):
    # 账户边界输入已确认结构；不伪造新的趋势确认算法来迎合两个真实报价。
    from wavequant.domain.strategies import hierarchical_entry

    monkeypatch.setattr(hierarchical_entry, 'hierarchical_history', lambda *args, **kwargs: (history, {}))
    signal = Signal(bars[2].timestamp, bars[2].symbol, 2, 'LONG', bars[2].close,
                    1.0, 'fixture', bars[2].timestamp, 0, None, 'fixture', 20)
    config = StrategyConfig(trend_flip_adverse_exit=True, entry_at_close=True, exit_on_target=False,
        initial_capital=100_000, max_position_weight=1, risk_fraction=1, max_participation=1,
        max_hold_bars=100, commission_bps_per_side=0, slippage_bps_per_side=0,
        minimum_commission=0, a_share_taxes=False, net_reward_risk_filter=False)
    return run_portfolio({bars[0].symbol: bars}, [signal], config)


def test_account_sells_ninety_at_warning_close_then_remaining_at_next_open(monkeypatch):
    bars, history = sample()
    result = account(bars, history, monkeypatch)
    buy, reduced, cleared = [row for row in result.orders if row['status'] == 'filled']
    assert reduced['reason'] == BREAKOUT_SHADOW_REDUCE
    assert reduced['price'] == 5.25
    assert reduced['exit_target_fraction'] == .9
    assert reduced['quantity'] <= buy['quantity'] * .9
    assert reduced['quantity'] > buy['quantity'] * .9 - 100
    assert cleared['reason'] == BREAKOUT_SHADOW_CLEAR
    assert cleared['price'] == 5.10
    assert cleared['execution_timestamp'].endswith('09:30:00')
    assert cleared['quantity'] == reduced['remaining_quantity']
    assert cleared['remaining_quantity'] == 0


def test_unsellable_warning_still_arms_next_open_and_unsellable_open_retries(monkeypatch):
    bars, history = sample()
    bars[3] = replace(bars[3], sellable=False, close_sellable=False)
    bars[4] = replace(bars[4], sellable=False)
    bars.append(replace(bars[4], timestamp=datetime(2024, 1, 11), open=5.4, sellable=True))
    history[5] = history[4]
    result = account(bars, history, monkeypatch)
    sells = [row for row in result.orders if row['side'] == 'SELL' and row['status'] == 'filled']
    assert len(sells) == 1
    assert sells[0]['reason'] == BREAKOUT_SHADOW_CLEAR
    assert sells[0]['execution_timestamp'] == '2024-01-11T09:30:00'
    assert sells[0]['remaining_quantity'] == 0
