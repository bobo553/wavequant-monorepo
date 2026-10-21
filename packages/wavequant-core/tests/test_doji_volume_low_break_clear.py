"""先锋新材2026-09-23/24用户报价；只验证退出规则和撮合，不伪造趋势资格。"""

from dataclasses import replace
from datetime import datetime

import pytest

from wavequant.application.analytics.backtest import run_portfolio
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar, Signal
from wavequant.domain.strategies.two_t_resistance import (
    BODY_VOLUME_CLEAR, TWO_T_BODY_VOLUME_CLEAR, HoldingResistanceHistory,
    holding_resistance_history, two_t_resistance_history,
)


def sample() -> list[Bar]:
    return [
        Bar(datetime(2026, 9, 23), 'sz.300163', 8.09, 8.36, 7.97, 8.09, 31_480_400),
        Bar(datetime(2026, 9, 24), 'sz.300163', 8.15, 8.15, 7.51, 7.63, 44_175_200),
    ]


def test_supplied_doji_then_volume_bearish_close_break_clears_without_target() -> None:
    bars = sample()
    risk = holding_resistance_history(bars, [])[1]
    assert risk['reason'] == BODY_VOLUME_CLEAR
    assert risk['exit_fraction'] == 1.0
    assert risk['execution_model'] == 'same_day_close'
    assert risk['body_reversal_pattern'] == 'doji_body_cover_close_below_low'
    assert risk['previous_is_doji'] is True
    assert risk['close_below_previous_low'] is True
    assert risk['engulfed_date'] == '2026-09-23'
    assert risk['previous_low'] == 7.97
    assert risk['observed_close'] == 7.63
    assert risk['previous_volume'] == 31_480_400
    assert risk['observed_volume'] == 44_175_200
    assert risk['volume_above_previous'] is True
    assert risk['bearish_reference_date'] is None


@pytest.mark.parametrize('case', ['equal_low', 'above_low', 'open_below_body', 'equal_volume',
                                  'less_volume', 'zero_reference', 'bullish', 'doji',
                                  'different_symbol', 'same_timestamp'])
def test_doji_exception_requires_strict_low_break_and_body_coverage(case: str) -> None:
    bars = sample()
    if case == 'equal_low':
        bars[1] = replace(bars[1], close=7.97)
    elif case == 'above_low':
        bars[1] = replace(bars[1], close=8.00)
    elif case == 'open_below_body':
        bars[1] = replace(bars[1], open=8.08)
    elif case == 'equal_volume':
        bars[1] = replace(bars[1], volume=bars[0].volume)
    elif case == 'less_volume':
        bars[1] = replace(bars[1], volume=bars[0].volume - 1)
    elif case == 'zero_reference':
        bars[0] = replace(bars[0], volume=0)
    elif case == 'bullish':
        bars[1] = replace(bars[1], open=7.60)
    elif case == 'doji':
        bars[1] = replace(bars[1], open=7.63)
    elif case == 'different_symbol':
        bars[1] = replace(bars[1], symbol='sz.000001')
    else:
        bars[1] = replace(bars[1], timestamp=bars[0].timestamp)
    assert 1 not in holding_resistance_history(bars, [])


def test_equal_open_covers_doji_and_wick_engulfing_is_not_required() -> None:
    bars = sample()
    bars[1] = replace(bars[1], open=8.09)
    assert bars[1].high < bars[0].high
    assert holding_resistance_history(bars, [])[1]['exit_fraction'] == 1.0


def test_prior_bearish_volume_and_new_window_keep_the_same_close_known_result() -> None:
    bars = sample()
    bars[0] = replace(bars[0], volume=100_000_000)
    bars.insert(0, Bar(datetime(2026, 9, 22), 'sz.300163', 8.40, 8.50, 8.00, 8.10, 30_000_000))
    expected = holding_resistance_history(bars, [])[2]
    assert expected['volume_above_previous'] is False
    assert expected['volume_above_last_bearish'] is True
    stream = HoldingResistanceHistory(bars, start_index=2)
    assert stream.advance(2, []) == {2: expected}
    assert stream.advance(2, []) == {2: expected}


def test_cold_prefix_and_daily_stream_do_not_backfill_the_exit() -> None:
    bars = sample()
    stream = HoldingResistanceHistory(bars)
    assert stream.advance(0, []) == holding_resistance_history(bars[:1], []) == {}
    assert stream.advance(1, []) == holding_resistance_history(bars, [])
    # 合成未来反弹不能撤销已经成立的9/24清仓。
    future = replace(bars[1], timestamp=datetime(2026, 9, 25), open=7.70, high=9.00, close=8.90)
    assert holding_resistance_history([*bars, future], [])[1] == holding_resistance_history(bars, [])[1]


def test_two_t_consumer_shares_the_doji_break_rule() -> None:
    bars = sample()
    # 目标较早已达到，隔开次笔退出观察窗，单独覆盖共用实体风险分支。
    origin = Bar(datetime(2026, 9, 21), 'sz.300163', 7.90, 8.10, 7.80, 8.05, 60_000_000)
    following = replace(origin, timestamp=datetime(2026, 9, 22))
    bars = [origin, following, *bars]
    event = dict(event='wave_projection_ready', attack=0, origin_index=0, bar_index=0, two_t=8.00)
    risk = two_t_resistance_history(bars, [event])[3]
    assert risk['reason'] == TWO_T_BODY_VOLUME_CLEAR
    assert risk['exit_fraction'] == 1.0
    assert risk['body_reversal_pattern'] == 'doji_body_cover_close_below_low'


def test_account_fully_exits_at_september_24_close_and_does_not_reenter() -> None:
    bars = sample()
    # 9/23自身受放量长上影禁买约束；用合成9/21量与9/22买入建立此前持仓。
    bars = [Bar(datetime(2026, 9, 21), 'sz.300163', 8.00, 8.20, 7.95, 8.10, 30_000_000),
            Bar(datetime(2026, 9, 22), 'sz.300163', 8.00, 8.20, 7.95, 8.10, 30_000_000), *bars]
    buy = Signal(bars[1].timestamp, bars[1].symbol, 1, 'LONG', 8.10, 7.00,
                 'fixture', bars[1].timestamp, 0, None, 'fixture', 20.0)
    repeat = replace(buy, bar_index=3, timestamp=bars[3].timestamp, reference_price=7.63)
    config = StrategyConfig(entry_at_close=True, allow_add_on=True, exit_on_target=False,
        wave_exhaustion_exit=True, max_hold_bars=100, max_participation=1, slippage_bps_per_side=0)
    result = run_portfolio({bars[0].symbol: bars}, [buy, repeat], config)
    buys = [row for row in result.orders if row['side'] == 'BUY' and row['status'] == 'filled']
    sells = [row for row in result.orders if row['side'] == 'SELL' and row['status'] == 'filled']
    assert len(buys) == len(sells) == 1
    assert sells[0]['reason'] == BODY_VOLUME_CLEAR
    assert sells[0]['price'] == 7.63
    assert sells[0]['quantity'] == buys[0]['quantity']
    assert sells[0]['remaining_quantity'] == 0
    assert sells[0]['execution_model'] == 'same_day_close'
    assert sells[0]['execution_timestamp'] == '2026-09-24T15:00:00'
    assert not result.open_positions
