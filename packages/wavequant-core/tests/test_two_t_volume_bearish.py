"""二吐后的放量阴线与相对前收涨幅无关，卖出风险同时阻止买入。"""

from dataclasses import replace
from datetime import datetime, timedelta
import json
from pathlib import Path

import pytest

from wavequant.application.analytics.backtest import run_portfolio
from wavequant.domain.market_state.volume_candle import is_volume_bearish
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar, Signal
from wavequant.domain.strategies.two_t_resistance import (
    HoldingResistanceHistory, TWO_T_VOLUME_BEARISH_REDUCE, holding_resistance_history,
)


@pytest.mark.parametrize('yesterday,last_bearish,current,expected', [
    (100, 200, 150, True), (200, 100, 150, True), (150, 150, 150, False),
    (0, 100, 101, True), (100, 0, 101, True), (0, 0, 100, False),
])
def test_global_volume_bearish_uses_either_strict_positive_reference(
    yesterday: float, last_bearish: float, current: float, expected: bool,
) -> None:
    previous = Bar(datetime(2024, 9, 30), 'sh.601086', 5.9, 6.2, 5.6, 6.2, yesterday)
    bearish = replace(previous, timestamp=datetime(2024, 9, 20), open=6.3, volume=last_bearish)
    bar = Bar(datetime(2024, 10, 8), previous.symbol, 6.72, 6.82, 5.97, 6.52, current)
    assert is_volume_bearish(bar, previous, bearish) is expected
    assert not is_volume_bearish(replace(bar, close=bar.open), previous, bearish)
    assert not is_volume_bearish(replace(bar, close=6.8), previous, bearish)
    assert not is_volume_bearish(bar, None, replace(bearish, timestamp=bar.timestamp))


def sample() -> tuple[list[Bar], dict[str, object]]:
    rows = [(5.8, 5.9, 5.6, 5.7, 100), (5.7, 6.0, 5.6, 5.9, 150),
            (5.9, 6.2, 5.8, 6.2, 200), (6.72, 6.82, 5.97, 6.52, 180)]
    bars = [Bar(datetime(2024, 9, 24) + timedelta(days=index), 'sh.601086', *row)
            for index, row in enumerate(rows)]
    return bars, dict(event='wave_projection_ready', attack=1, origin_index=0, bar_index=3, two_t=6.5)


@pytest.mark.parametrize('event_name', ['wave_projection_ready', 'wave_n_target_reached'])
def test_target_reach_day_bearish_volume_reduces_even_with_positive_daily_return(event_name: str) -> None:
    bars, event = sample()
    if event_name == 'wave_n_target_reached':
        event = dict(event=event_name, attack=1, origin_index=0, bar_index=3,
                     reached_stage='two_t', reached_target=6.5)
    risk = holding_resistance_history(bars, [event])[3]
    assert risk['reason'] == TWO_T_VOLUME_BEARISH_REDUCE
    assert risk['exit_target_fraction'] == .8
    assert risk['execution_model'] == 'same_day_close'
    assert bars[3].close > bars[2].close
    assert bars[3].volume < bars[2].volume
    assert risk['bearish_reference_volume'] == 100
    assert holding_resistance_history(bars[:3], [event]) == {}
    stream = HoldingResistanceHistory(bars)
    stream.advance(2, [])
    assert stream.advance(3, [event]) == holding_resistance_history(bars, [event])


@pytest.mark.parametrize('invalid', ['no_target', 'future', 'not_reached', 'invalidated'])
def test_no_unconfirmed_or_invalidated_target_can_create_sell_risk(invalid: str) -> None:
    bars, event = sample()
    events = [event]
    if invalid == 'no_target':
        events = []
    elif invalid == 'future':
        event['bar_index'] = 4
    elif invalid == 'not_reached':
        event['two_t'] = 7.0
    else:
        events.append(dict(event='wave_projection_invalidated', attack=1, origin_index=0, bar_index=3))
    assert 3 not in holding_resistance_history(bars, events)


def test_october_8_frozen_candle_with_known_two_t_target_blocks_new_buy() -> None:
    raw = json.loads((Path(__file__).parent / 'fixtures/guofang_2025_five_top_low_open.json')
                     .read_text(encoding='utf-8'))
    bars = [Bar(**dict(row, timestamp=datetime.fromisoformat(row['timestamp'])))
            for row in raw['bars'] if '2024-09-18' <= row['timestamp'][:10] <= '2024-10-08']
    attack = next(index for index, bar in enumerate(bars) if str(bar.timestamp.date()) == '2024-09-24')
    now = len(bars) - 1
    # 显式已知目标用于隔离卖出判定；此例不伪称重新推导了该正 N 的测幅。
    event = dict(event='wave_projection_ready', attack=attack, origin_index=0, bar_index=now, two_t=6.5)
    risk = holding_resistance_history(bars, [event])[now]
    assert risk['reason'] == TWO_T_VOLUME_BEARISH_REDUCE
    assert risk['observed_volume'] == 43_775_910
    assert risk['previous_volume'] == 35_545_935
    assert risk['wave_n_date'] == '2024-09-24'
    buy = Signal(bars[now].timestamp, bars[now].symbol, now, 'LONG', bars[now].close, 5.0,
                 'fixture', bars[attack].timestamp, 0, None, 'fixture', 10.0)
    account = run_portfolio({bars[0].symbol: bars}, [buy], StrategyConfig(
        entry_at_close=True, wave_exhaustion_exit=True, max_participation=1),
        wave_events={bars[0].symbol: [event]})
    assert not account.open_positions
    assert not any(order['side'] == 'BUY' and order['status'] == 'filled' for order in account.orders)


def test_existing_position_reduces_at_close_instead_of_adding() -> None:
    bars, event = sample()
    bars = [replace(bar, volume=bar.volume * 100_000) for bar in bars]
    buy = Signal(bars[1].timestamp, bars[1].symbol, 1, 'LONG', bars[1].close, 4.0,
                 'fixture', bars[1].timestamp, 0, None, 'fixture', 10.0)
    repeat = replace(buy, timestamp=bars[3].timestamp, bar_index=3, reference_price=bars[3].close)
    config = StrategyConfig(entry_at_close=True, allow_add_on=True, wave_exhaustion_exit=True,
                            wave_exhaustion_reduction=.8, max_participation=1, max_hold_bars=100)
    account = run_portfolio({bars[0].symbol: bars}, [buy, repeat], config,
                            wave_events={bars[0].symbol: [event]})
    sells = [order for order in account.orders if order['side'] == 'SELL' and order['status'] == 'filled']
    assert len(sells) == 1
    assert sells[0]['reason'] == TWO_T_VOLUME_BEARISH_REDUCE
    assert sells[0]['execution_model'] == 'same_day_close'
    assert sells[0]['timestamp'] == bars[3].timestamp.isoformat()
    assert sells[0]['remaining_quantity'] > 0
    assert not any(order['side'] == 'BUY' and order['status'] == 'filled'
                   and order['timestamp'] == bars[3].timestamp.isoformat() for order in account.orders)
