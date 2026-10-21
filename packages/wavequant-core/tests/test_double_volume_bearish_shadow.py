"""用户11/15报价配合显式合成前序行情验证倍量长上影阴线减仓。"""

import math
from dataclasses import replace
from datetime import datetime

import pytest

from wavequant.application.analytics.backtest import run_portfolio
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar, Signal
from wavequant.domain.strategies.staged_exit import (
    DOUBLE_VOLUME_BEARISH_SHADOW_REDUCE, EXTREME_VOLUME_REDUCE,
    StagedExitState, observe_volume_down_exit,
)


def scenario() -> list[Bar]:
    # 仅11/15来自用户显示报价；11/13、11/14合成，2.51为显示舍入值。
    rows = [
        ('2023-11-13', 23.7, 23.9, 23.5, 23.8, 7_000_000),
        ('2023-11-14', 24.0, 24.3, 23.5, 24.22, 8_217_000),
        ('2023-11-15', 24.22, 25.30, 23.97, 24.13, 20_624_900),
    ]
    return [Bar(datetime.fromisoformat(day), 'sz.003029', opened, high, low, close, volume)
            for day, opened, high, low, close, volume in rows]


def test_user_quote_requests_seventy_percent_at_close() -> None:
    bars = scenario()
    decision = observe_volume_down_exit(bars, 2, StagedExitState())
    assert decision is not None and decision['reason'] == DOUBLE_VOLUME_BEARISH_SHADOW_REDUCE
    assert decision['exit_target_fraction'] == .7
    assert decision['execution_model'] == 'same_day_close'
    assert round(decision['volume_multiple'], 2) == 2.51
    assert decision['upper_shadow'] == pytest.approx(1.08)
    assert decision['body_size'] == pytest.approx(.09)


@pytest.mark.parametrize('case,expected', [
    ('exact_double', True), ('below_double', False), ('zero_previous', False),
    ('doji', False), ('bullish', False), ('half_shadow', True), ('short_shadow', False),
])
def test_volume_color_and_long_shadow_boundaries(case: str, expected: bool) -> None:
    bars = scenario()
    if case == 'exact_double':
        bars[2] = replace(bars[2], volume=2 * bars[1].volume)
    elif case == 'below_double':
        bars[2] = replace(bars[2], volume=2 * bars[1].volume - 1)
    elif case == 'zero_previous':
        bars[1] = replace(bars[1], volume=0)
    elif case == 'doji':
        bars[2] = replace(bars[2], close=24.22)
    elif case == 'bullish':
        bars[2] = replace(bars[2], close=24.23)
    elif case == 'half_shadow':
        bars[2] = replace(bars[2], high=24.47)
    else:
        bars[2] = replace(bars[2], high=24.46)
    decision = observe_volume_down_exit(bars, 2, StagedExitState())
    assert (decision is not None and decision['reason'] == DOUBLE_VOLUME_BEARISH_SHADOW_REDUCE) is expected


def test_tenfold_and_existing_full_clear_win() -> None:
    bars = scenario()
    bars[2] = replace(bars[2], volume=bars[1].volume * 11)
    decision = observe_volume_down_exit(bars, 2, StagedExitState())
    assert decision is not None and decision['reason'] == EXTREME_VOLUME_REDUCE
    assert decision['exit_target_fraction'] == .9
    bars = scenario()
    state = StagedExitState(volume_trigger_index=1, volume_support_index=0, volume_reduction_target=.7)
    bars[2] = replace(bars[2], low=23.4)
    decision = observe_volume_down_exit(bars, 2, state)
    assert decision is not None and decision['reason'] == 'volume_down_support_break_clear'
    assert decision['exit_fraction'] == 1.0


def test_account_reduces_seventy_percent_without_future_data() -> None:
    bars = scenario()
    entry = bars[1]
    signal = Signal(entry.timestamp, entry.symbol, 1, 'LONG', entry.close, 20.0,
                    'system_wave_push_gap', entry.timestamp, 0, None, 'fixture', 50.0)
    config = StrategyConfig(volume_down_exit=True, entry_at_close=True, exit_on_target=False,
                            max_hold_bars=100, slippage_bps_per_side=0, max_participation=1)
    result = run_portfolio({entry.symbol: bars}, [signal], config)
    filled = [order for order in result.orders if order['status'] == 'filled']
    assert [order['reason'] for order in filled] == ['system_wave_push_gap', DOUBLE_VOLUME_BEARISH_SHADOW_REDUCE]
    bought, sold = filled
    assert sold['quantity'] == math.floor(bought['quantity'] * .7 / 100 + 1e-9) * 100
    assert sold['remaining_quantity'] == bought['quantity'] - sold['quantity']
    assert sold['execution_timestamp'] == '2023-11-15T15:00:00'
    prefix = run_portfolio({entry.symbol: bars[:2]}, [signal], config)
    assert prefix.orders == [order for order in result.orders if order['timestamp'][:10] < '2023-11-15']
    decision = observe_volume_down_exit(bars, 2, StagedExitState())
    future = bars + [replace(bars[-1], timestamp=datetime(2023, 11, 16), volume=1)]
    assert observe_volume_down_exit(future, 2, StagedExitState()) == decision
