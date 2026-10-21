"""十倍量严格边界、全清优先与九成累计撮合；前序行情显式合成。"""

import math
from dataclasses import replace
from datetime import datetime

import pytest

from wavequant.application.analytics.backtest import run_portfolio
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar, Signal
from wavequant.domain.strategies.staged_exit import (
    EXTREME_VOLUME_REDUCE, StagedExitState, observe_volume_down_exit,
)


def scenario() -> list[Bar]:
    # 1/25、1/26使用用户显示报价；1/21、1/24为合成数据，11.91是显示舍入值。
    rows = [
        ('2022-01-21', 24.8, 24.9, 24.4, 24.5, 1_000_000),
        ('2022-01-24', 24.5, 25.0, 24.4, 24.91, 2_397_000),
        ('2022-01-25', 26.19, 27.40, 25.66, 27.40, 28_549_200),
        ('2022-01-26', 27.19, 27.20, 24.67, 24.67, 12_301_700),
    ]
    return [Bar(datetime.fromisoformat(day), 'sz.003029', opened, high, low, close, volume)
            for day, opened, high, low, close, volume in rows]


def test_nonsealed_bullish_quote_reduces_ninety_percent() -> None:
    bars = scenario()
    # 新封板豁免下，九成减仓用例显式改为收盘开板。
    bars[2] = replace(bars[2], close=27.39)
    decision = observe_volume_down_exit(bars, 2, StagedExitState())
    assert decision is not None and decision['reason'] == EXTREME_VOLUME_REDUCE
    assert decision['exit_target_fraction'] == .9
    assert decision['execution_model'] == 'same_day_close'
    assert decision['trigger_volume'] == 28_549_200
    assert round(decision['volume_multiple'], 2) == 11.91
    assert decision == observe_volume_down_exit(bars[:3], 2, StagedExitState())


@pytest.mark.parametrize('previous_volume,current_volume,expected', [
    (100.0, 1000.0, False), (100.0, 999.0, False), (100.0, 1001.0, True),
    (.1, 1.0, False), (.1, 1.0001, True), (0.0, 1000.0, False),
])
def test_strict_tenfold_positive_previous_volume(previous_volume: float, current_volume: float,
                                                expected: bool) -> None:
    bars = scenario()[1:3]
    bars[0] = replace(bars[0], volume=previous_volume)
    bars[1] = replace(bars[1], volume=current_volume, close=27.39)
    decision = observe_volume_down_exit(bars, 1, StagedExitState())
    assert (decision is not None and decision['reason'] == EXTREME_VOLUME_REDUCE) is expected
    assert observe_volume_down_exit(bars[:1], 0, StagedExitState()) is None


@pytest.mark.parametrize('close', [27.39, 26.19, 26.0])
def test_candle_color_does_not_restrict_extreme_volume(close: float) -> None:
    bars = scenario()
    bars[2] = replace(bars[2], close=close)
    decision = observe_volume_down_exit(bars, 2, StagedExitState())
    assert decision is not None and decision['reason'] == EXTREME_VOLUME_REDUCE


def test_full_clear_wins_even_when_today_exceeds_tenfold() -> None:
    bars = scenario()
    bars[3] = replace(bars[3], volume=300_000_000)
    decision = observe_volume_down_exit(bars, 3, StagedExitState())
    assert decision is not None
    assert decision['reason'] == 'volume_previous_triple_gap_bearish_clear'
    assert decision['exit_fraction'] == 1.0


def test_account_reduces_at_close_then_clears_remaining_next_day() -> None:
    bars = scenario()
    bars[2] = replace(bars[2], close=27.39)
    entry = bars[1]
    signal = Signal(entry.timestamp, entry.symbol, 1, 'LONG', entry.close, 20.0,
                    'system_wave_push_gap', entry.timestamp, 0, None, 'fixture', 50.0)
    config = StrategyConfig(volume_down_exit=True, entry_at_close=True, exit_on_target=False,
                            max_hold_bars=100, slippage_bps_per_side=0, max_participation=1)
    result = run_portfolio({entry.symbol: bars}, [signal], config)
    filled = [order for order in result.orders if order['status'] == 'filled']
    assert [order['reason'] for order in filled] == [
        'system_wave_push_gap', EXTREME_VOLUME_REDUCE, 'volume_previous_triple_gap_bearish_clear',
    ]
    bought, reduced, cleared = filled
    assert reduced['quantity'] == min(bought['quantity'], math.ceil(bought['quantity'] * .9 / 100) * 100)
    assert reduced['execution_timestamp'] == '2022-01-25T15:00:00'
    assert cleared['quantity'] == reduced['remaining_quantity']
    assert cleared['remaining_quantity'] == 0
    assert cleared['execution_timestamp'] == '2022-01-26T15:00:00'
    prefix = run_portfolio({entry.symbol: bars[:3]}, [signal], config)
    assert prefix.orders == [order for order in result.orders if order['timestamp'][:10] < '2022-01-26']

    # 连续极端放量累计目标保持九成，不再卖剩余仓位的九成。
    repeated = bars[:3] + [replace(bars[3], open=28.0, high=29.0, low=28.0, close=29.0, volume=300_000_000)]
    repeated_result = run_portfolio({entry.symbol: repeated}, [signal], config)
    repeated_sells = [order for order in repeated_result.orders
                      if order['side'] == 'SELL' and order['status'] == 'filled']
    assert len(repeated_sells) == 1
    assert repeated_sells[0]['quantity'] == reduced['quantity']

    blocked = bars[:3]
    blocked[2] = replace(blocked[2], close_sellable=False, sellable=False)
    blocked_result = run_portfolio({entry.symbol: blocked}, [signal], config)
    assert not any(order['side'] == 'SELL' and order['status'] == 'filled' for order in blocked_result.orders)
    assert any(order['reason'] == 'not_sellable' and order['exit_target_fraction'] == .9
               for order in blocked_result.orders)


def test_jida_sealed_limit_quote_is_now_exempt_from_tenfold_reduction() -> None:
    bars = scenario()
    decision = observe_volume_down_exit(bars, 2, StagedExitState())
    assert decision is None or decision['reason'] != EXTREME_VOLUME_REDUCE
    decision = observe_volume_down_exit(bars, 3, StagedExitState())
    assert decision is not None and decision['exit_fraction'] == 1.0
