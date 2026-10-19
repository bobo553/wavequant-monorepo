"""2月2日报价来自用户；此前行情为合成输入，不代表真实2月1日。"""

from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from wavequant.application.analytics.backtest import run_portfolio
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar, Signal
from wavequant.domain.strategies.two_t_resistance import (
    HoldingResistanceHistory, VOLUME_SHADOW_MIDPOINT_CLEAR, holding_resistance_history,
)


def sample():
    rows = [(4.70, 4.80, 4.60, 4.75, 50_000_000),
            (4.75, 4.90, 4.70, 4.85, 60_000_000),
            (5.50, 5.50, 4.90, 4.95, 100_000_000),
            (4.95, 5.50, 4.95, 5.08, 133_375_424)]
    return [Bar(datetime(2024, 1, 30) + timedelta(days=i), 'sh.600825', *row)
            for i, row in enumerate(rows)]


def test_user_bullish_upper_shadow_clears_without_breakout_or_target():
    bars = sample()
    risk = holding_resistance_history(bars, [])[3]
    assert risk['reason'] == VOLUME_SHADOW_MIDPOINT_CLEAR
    assert risk['exit_fraction'] == 1.0
    assert risk['execution_model'] == 'same_day_close'
    assert risk['upper_shadow'] == .42 and risk['candle_body'] == .13
    assert risk['bearish_body_midpoint'] == 5.225
    assert risk['previous_volume_bearish'] is True
    assert bars[3].close > bars[3].open and bars[3].high == bars[2].high


@pytest.mark.parametrize('case', ['equal_shadow', 'equal_midpoint', 'above_midpoint',
                                 'equal_volume', 'previous_not_volume', 'previous_bullish'])
def test_strict_boundaries_and_previous_session_requirement(case):
    bars = sample()
    if case == 'equal_shadow':
        bars[3] = replace(bars[3], high=5.21)
    elif case in ('equal_midpoint', 'above_midpoint'):
        bars[3] = replace(bars[3], close=5.225 if case == 'equal_midpoint' else 5.23, high=5.80)
    elif case == 'equal_volume':
        bars[3] = replace(bars[3], volume=100_000_000)
    elif case == 'previous_not_volume':
        bars[2] = replace(bars[2], volume=60_000_000)
    else:
        bars[2] = replace(bars[2], open=4.90)
    assert holding_resistance_history(bars, []).get(3, {}).get('reason') != VOLUME_SHADOW_MIDPOINT_CLEAR


def test_previous_volume_can_compare_older_bearish_and_window_restart_preserves_context():
    bars = sample()
    bars[0] = replace(bars[0], open=4.78, close=4.75, volume=40_000_000)
    bars[2] = replace(bars[2], volume=50_000_000)
    expected = holding_resistance_history(bars, [])[3]
    assert expected['reason'] == VOLUME_SHADOW_MIDPOINT_CLEAR
    stream = HoldingResistanceHistory(bars, start_index=3)
    assert stream.advance(3, [])[3] == expected


def test_stream_matches_each_prefix_and_other_symbol_uses_same_rule():
    bars = [replace(bar, symbol='sz.000001') for bar in sample()]
    stream = HoldingResistanceHistory(bars)
    for index in range(len(bars)):
        assert stream.advance(index, []) == holding_resistance_history(bars[:index + 1], [])
    assert stream.history[3]['reason'] == VOLUME_SHADOW_MIDPOINT_CLEAR


def test_account_clears_at_close_not_tomorrow():
    bars = sample()
    signal = Signal(bars[1].timestamp, bars[1].symbol, 1, 'LONG', bars[1].close,
                    1, 'fixture', bars[1].timestamp, 0, None, 'fixture', 20)
    config = StrategyConfig(entry_at_close=True, wave_exhaustion_exit=True, exit_on_target=False,
        max_position_weight=1, risk_fraction=1, max_participation=1, max_hold_bars=100,
        slippage_bps_per_side=0, net_reward_risk_filter=False)
    result = run_portfolio({bars[0].symbol: bars}, [signal], config)
    sells = [row for row in result.orders if row['side'] == 'SELL' and row['status'] == 'filled']
    assert len(sells) == 1
    assert sells[0]['reason'] == VOLUME_SHADOW_MIDPOINT_CLEAR
    assert sells[0]['timestamp'].startswith('2024-02-02')
    assert sells[0]['price'] == 5.08 and sells[0]['remaining_quantity'] == 0
    assert sells[0]['execution_model'] == 'same_day_close'
