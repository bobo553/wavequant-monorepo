"""8月4日使用用户报价；前后交易日为合成输入，不作为实股运行证据。"""
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from wavequant.application.analytics.backtest import run_portfolio
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar, Signal
from wavequant.domain.market_state.volume_candle import (
    VOLUME_SHADOW_NEXT_WEAK_CLEAR, volume_upper_shadow_rejection,
    volume_shadow_next_decline, volume_shadow_next_open_eligible,
)
from wavequant.domain.strategies.two_t_resistance import holding_resistance_history


def sample():
    rows = [(14.0, 14.4, 13.9, 14.2, 3_000_000),
            (14.31, 15.01, 14.25, 14.37, 6_430_488),
            (14.2, 14.3, 14.0, 14.1, 3_000_000)]
    return [Bar(datetime(2021, 8, 3) + timedelta(days=i), 'sz.300562', *row)
            for i, row in enumerate(rows)]


@pytest.mark.parametrize('close', [14.37, 14.31, 14.28])
def test_volume_shadow_blocks_bullish_doji_and_bearish(close):
    bars = sample()
    assert volume_upper_shadow_rejection(replace(bars[1], close=close), bars[0])


def test_volume_and_shape_boundaries():
    bars = sample()
    assert volume_upper_shadow_rejection(replace(bars[1], volume=bars[0].volume), bars[0]) is None
    assert volume_upper_shadow_rejection(replace(bars[1], close=14.95), bars[0]) is None
    assert volume_upper_shadow_rejection(bars[1], None) is None
    assert volume_upper_shadow_rejection(bars[1], replace(bars[0], symbol='other')) is None


def test_next_open_eligibility_does_not_use_later_low():
    bars = sample()
    flat = replace(bars[2], low=14.2, close=14.2)
    assert volume_shadow_next_open_eligible([*bars[:2], flat], 2)
    assert volume_shadow_next_decline(bars, 2, flat) is None
    observed = replace(flat, low=14.19, close=14.21)
    risk = volume_shadow_next_decline(bars, 2, observed)
    assert risk and risk['exit_fraction'] == 1 and risk['observed_low'] == 14.19
    assert risk['reason'] == VOLUME_SHADOW_NEXT_WEAK_CLEAR
    assert holding_resistance_history(bars, [])[2]['reason'] == VOLUME_SHADOW_NEXT_WEAK_CLEAR


def test_equal_open_and_warning_expiry_do_not_clear():
    bars = sample()
    assert volume_shadow_next_decline([*bars[:2], replace(bars[2], open=14.37, high=14.4)], 2) is None
    bars.append(replace(bars[2], timestamp=bars[2].timestamp + timedelta(days=1), open=14.05))
    assert volume_shadow_next_decline(bars, 3) is None


def test_early_entry_survives_final_shadow_then_next_day_clears():
    bars = sample()
    signal = Signal(bars[0].timestamp, bars[0].symbol, 0, 'LONG', bars[0].close,
                    1, 'fixture', bars[0].timestamp, 0, None, 'fixture', 30)
    config = StrategyConfig(entry_at_close=False, exit_on_target=False,
        max_position_weight=1, risk_fraction=1, max_participation=1, max_hold_bars=100,
        slippage_bps_per_side=0, net_reward_risk_filter=False)
    result = run_portfolio({bars[0].symbol: bars}, [signal], config)
    buys = [row for row in result.orders if row['side'] == 'BUY' and row['status'] == 'filled']
    sells = [row for row in result.orders if row['side'] == 'SELL' and row['status'] == 'filled']
    assert len(buys) == 1 and buys[0]['timestamp'].startswith('2021-08-04')
    assert len(sells) == 1 and sells[0]['timestamp'].startswith('2021-08-05')
    assert sells[0]['reason'] == VOLUME_SHADOW_NEXT_WEAK_CLEAR
    assert sells[0]['remaining_quantity'] == 0
    assert sells[0]['execution_model'] == 'same_day_close'
