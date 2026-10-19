"""行情取冻结国芳数据；资格、止损及目标为合成场景，不冒充实股全链路复现。"""
from dataclasses import replace
from datetime import datetime

import pytest

from wavequant.application.analytics.backtest import run_portfolio
from wavequant.application.analytics.intraday_entry import _qualified_rising_close_entries
from wavequant.domain.market_state.candle_strength import QUALIFIED_RISING_CLOSE, volume_high_open_rising
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.integrated_strategy import SystemResult, SystemStrategy
from wavequant.domain.strategies.trading_intervals import TradingInterval


def sample(level=2):
    rows = [
        ('2024-03-14', 5.8779099115938, 6.0999642860317875, 5.773413735387687, 5.851785867542272, 16213600),
        ('2024-03-15', 5.890971933619563, 6.439576858701652, 5.890971933619563, 6.439576858701652, 25079200),
        ('2024-03-18', 6.896747629603392, 7.079615937964087, 6.766127409345751, 7.079615937964087, 75191900),
    ]
    bars = [Bar(datetime.fromisoformat(day), 'sh.601086', *values) for day, *values in rows]
    event = dict(event='trend_lifecycle_state', state='空多交替', bar_index=0,
                 trend_level=level, source_path='fixture', breakout_level=20)
    strategy = SystemStrategy(pivot_mode='lecture_causal', entry_policy='hierarchical_two_buy_points',
                              buy_point_definition='whole_flip_wave_v3', buy_interval_level=level)
    window = TradingInterval(level, 'fixture', 0, 0, start_low_price=5)
    return bars, SystemResult([], [event], {}), strategy, window


@pytest.mark.parametrize('level', [1, 2, 3])
def test_high_open_is_relative_to_close_and_fills_on_march15(level):
    bars, generated, strategy, window = sample(level)
    assert bars[0].close < bars[1].open < bars[0].high
    resolved, days = _qualified_rising_close_entries(bars, generated, strategy, only_index=1, intervals=[window])
    assert days == {1} and resolved.signals[0].reason == QUALIFIED_RISING_CLOSE
    assert resolved.signals[0].trigger_timestamp.hour == 15
    result = run_portfolio({bars[0].symbol: bars}, resolved.signals,
        StrategyConfig(entry_at_close=False, slippage_bps_per_side=0, net_reward_risk_filter=False))
    buys = [o for o in result.orders if o['side'] == 'BUY' and o['status'] == 'filled']
    assert len(buys) == 1 and buys[0]['timestamp'].startswith('2024-03-15')
    assert buys[0]['execution_model'] == 'same_day_close' and buys[0]['price'] == bars[1].close


@pytest.mark.parametrize('change', ['equal_open', 'equal_volume', 'zero_reference', 'break_open', 'weak_body', 'upper_shadow'])
def test_strict_volume_open_and_strong_body_boundaries(change):
    bars, _, _, _ = sample()
    bar, previous = bars[1], bars[0]
    if change == 'equal_open': bar = replace(bar, open=previous.close, low=previous.close)
    elif change == 'equal_volume': bar = replace(bar, volume=previous.volume)
    elif change == 'zero_reference': previous = replace(previous, volume=0)
    elif change == 'break_open': bar = replace(bar, low=bar.open - .01)
    elif change == 'weak_body': bar = replace(bar, close=bar.open + .01)
    else: bar = replace(bar, high=8)
    assert not volume_high_open_rising(bar, previous)


@pytest.mark.parametrize('case', ['missing', 'closed', 'future', 'wrong_level'])
def test_no_new_qualification_is_invented(case):
    bars, generated, strategy, window = sample()
    if case == 'missing': generated.audit.clear()
    elif case == 'closed': window = replace(window, end_index=1)
    elif case == 'future': window = replace(window, start_index=2)
    else: window = replace(window, trend_level=1)
    resolved, days = _qualified_rising_close_entries(bars, generated, strategy, only_index=1, intervals=[window])
    assert not resolved.signals and not days


def test_close_permission_still_blocks_fill():
    bars, generated, strategy, window = sample()
    bars[1] = replace(bars[1], close_buyable=False)
    resolved, _ = _qualified_rising_close_entries(bars, generated, strategy, only_index=1, intervals=[window])
    result = run_portfolio({bars[0].symbol: bars}, resolved.signals, StrategyConfig(entry_at_close=False))
    assert not any(o['side'] == 'BUY' and o['status'] == 'filled' for o in result.orders)


def test_strict_gap_keeps_existing_channel():
    bars, generated, strategy, window = sample()
    bars[1] = replace(bars[1], open=6.2, low=6.2, high=6.8, close=6.8)
    resolved, days = _qualified_rising_close_entries(bars, generated, strategy, only_index=1, intervals=[window])
    assert not resolved.signals and not days
