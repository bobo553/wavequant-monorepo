"""冻结先锋新材行情的等实体反包；完整趋势资格回放另行验证。"""

from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path

import pytest

from wavequant.application.analytics.backtest import run_portfolio
from wavequant.domain.market_state.volume_candle import bearish_body_engulfs, volume_bearish_entry_rejection
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar, Signal
from wavequant.domain.strategies.two_t_resistance import (
    BODY_VOLUME_CLEAR, HoldingResistanceHistory, holding_resistance_history,
)


def sample() -> list[Bar]:
    data = json.loads((Path(__file__).parent / 'fixtures/xianfeng_2026_strong_squeeze.json')
                      .read_text(encoding='utf-8'))
    return [Bar(datetime.fromisoformat(row[0]), data['symbol'], *row[1:6])
            for row in data['bars'] if '2021-08-19' <= row[0] <= '2021-08-24']


def test_frozen_equal_body_uses_last_bearish_volume_despite_lower_daily_volume() -> None:
    bars = sample()
    previous, current = bars[-2:]
    assert (previous.open, previous.close) == (4.22, 4.31)
    assert (current.open, current.close, current.volume) == (4.31, 4.22, 25_759_884)
    assert current.volume < previous.volume
    assert bearish_body_engulfs(current, previous)
    proof = holding_resistance_history(bars, [])[3]
    assert proof['reason'] == BODY_VOLUME_CLEAR and proof['exit_fraction'] == 1
    assert proof['volume_above_previous'] is False
    assert proof['volume_above_last_bearish'] is True
    assert proof['bearish_reference_date'] == '2021-08-19'
    assert proof['bearish_reference_volume'] == 23_596_117
    assert proof['execution_model'] == 'same_day_close'
    assert volume_bearish_entry_rejection(bars, 3) == 'volume_bearish_body_engulfing_buy_blocked'


@pytest.mark.parametrize('opened,closed,expected', [(4.31, 4.22, True), (4.32, 4.22, True),
                                                   (4.31, 4.21, True), (4.30, 4.22, False),
                                                   (4.31, 4.23, False), (4.22, 4.31, False)])
def test_equal_edges_are_inclusive_but_partial_body_overlap_is_not(
    opened: float, closed: float, expected: bool,
) -> None:
    bars = sample()
    current = replace(bars[-1], open=opened, close=closed)
    assert bearish_body_engulfs(current, bars[-2]) is expected
    assert (3 in holding_resistance_history([*bars[:-1], current], [])) is expected


@pytest.mark.parametrize('volume', [23_596_117, 23_596_116, 0])
def test_equal_body_does_not_bypass_positive_strict_volume_threshold(volume: float) -> None:
    bars = sample()
    bars[-1] = replace(bars[-1], volume=volume)
    assert 3 not in holding_resistance_history(bars, [])


def test_doji_still_uses_its_separate_low_break_rule() -> None:
    bars = sample()
    previous = replace(bars[-2], open=4.31)
    assert not bearish_body_engulfs(bars[-1], previous)
    assert 3 not in holding_resistance_history([*bars[:2], previous, bars[-1]], [])


def test_equal_body_stream_and_new_window_match_completed_prefix() -> None:
    bars = sample()
    stream = HoldingResistanceHistory(bars)
    for index in range(len(bars)):
        assert stream.advance(index, []) == holding_resistance_history(bars[:index + 1], [])
    window = HoldingResistanceHistory(bars, start_index=3)
    assert window.advance(3, []) == {3: holding_resistance_history(bars, [])[3]}


def test_account_clears_on_august_24_close_and_blocks_reentry() -> None:
    bars = sample()
    buy = Signal(bars[1].timestamp, bars[1].symbol, 1, 'LONG', bars[1].close,
                 3.0, 'fixture', bars[1].timestamp, 0, None, 'fixture', 10)
    repeat = replace(buy, bar_index=3, timestamp=bars[3].timestamp, reference_price=4.22)
    config = StrategyConfig(entry_at_close=True, allow_add_on=True, exit_on_target=False,
        wave_exhaustion_exit=True, max_hold_bars=100, max_participation=1, slippage_bps_per_side=0,
        net_reward_risk_filter=False)
    result = run_portfolio({bars[0].symbol: bars}, [buy, repeat], config)
    buys = [row for row in result.orders if row['side'] == 'BUY' and row['status'] == 'filled']
    sells = [row for row in result.orders if row['side'] == 'SELL' and row['status'] == 'filled']
    assert len(buys) == len(sells) == 1
    assert sells[0]['reason'] == BODY_VOLUME_CLEAR
    assert sells[0]['price'] == 4.22
    assert sells[0]['quantity'] == buys[0]['quantity']
    assert sells[0]['remaining_quantity'] == 0
    assert sells[0]['execution_timestamp'] == '2021-08-24T15:00:00'
    assert not result.open_positions
