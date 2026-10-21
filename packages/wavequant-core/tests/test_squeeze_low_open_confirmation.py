"""低开放量中大阳轧空确认不套用独立突破的倍量门槛。"""

from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path

import pytest

from wavequant.application.analytics.backtest import run_portfolio
from wavequant.domain.market_state.candle_strength import bullish_breakout_rejection
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.attack_quality import squeeze_buy_rejection
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals


def frozen_input() -> tuple[list[Bar], SystemStrategy]:
    raw = json.loads((Path(__file__).parent / 'fixtures/guofang_2024_squeeze_confirmation.json').read_text('utf-8'))
    bars = [Bar(**dict(row, timestamp=datetime.fromisoformat(row['timestamp']))) for row in raw['bars']]
    return bars, SystemStrategy(**raw['strategy'])


def test_actual_confirmation_candle_needs_increased_volume_not_double_volume() -> None:
    bars, _ = frozen_input()
    previous, current = bars[-2:]
    assert current.timestamp.date().isoformat() == '2024-11-29'
    assert current.open < previous.close and current.close > previous.high
    assert previous.volume < current.volume < 2 * previous.volume
    assert bullish_breakout_rejection(current, previous) == 'breakout_low_open'
    assert squeeze_buy_rejection(current, previous, bars[-4]) is None


@pytest.mark.parametrize('change', ['equal_volume', 'below_volume', 'equal_high', 'below_high', 'weak_body', 'future'])
def test_low_open_exception_keeps_volume_shape_and_price_boundaries(change: str) -> None:
    previous = Bar(datetime(2024, 11, 28), 'sh.600000', 10, 10.5, 9.8, 10.2, 100)
    current = Bar(datetime(2024, 11, 29), previous.symbol, 10, 11, 9.9, 11, 148)
    if change == 'equal_volume':
        current = replace(current, volume=100)
    elif change == 'below_volume':
        current = replace(current, volume=99)
    elif change == 'equal_high':
        current = replace(current, close=previous.high)
    elif change == 'below_high':
        current = replace(current, close=previous.high - .01)
    elif change == 'weak_body':
        current = replace(current, low=8)
    else:
        previous = replace(previous, timestamp=current.timestamp)
    assert squeeze_buy_rejection(current, previous) is not None


def test_full_history_emits_and_executes_on_squeeze_confirmation_close() -> None:
    # 保留2018起真实资格历史和成交权限，不注入正N、趋势或买入信号。
    bars, strategy = frozen_input()
    result = generate_system_signals(bars, strategy)
    n_index = len(bars) - 3
    assert bars[n_index].timestamp.date().isoformat() == '2024-11-27'
    assert any(row['event'] == 'n_completed' and row['bar_index'] == n_index
               and row.get('direction') == 'up' for row in result.audit)
    assert any(row['event'] == 'regime_confirmation' and row['bar_index'] == len(bars) - 1
               and row.get('attack') == n_index and row.get('regime') == '轧空' for row in result.audit)
    entries = [signal for signal in result.signals if signal.side == 'LONG'
               and signal.timestamp == bars[-1].timestamp]
    assert entries, '11/29已有正N轧空确认和放量中大阳，应在当天生成买入'
    account = run_portfolio({bars[0].symbol: bars}, entries, StrategyConfig(
        entry_at_close=True, nonflat_limit_close_fill=True, net_reward_risk_filter=False,
        slippage_bps_per_side=0))
    filled = [order for order in account.orders if order['side'] == 'BUY' and order['status'] == 'filled']
    assert len(filled) == 1
    assert filled[0]['execution_timestamp'] == '2024-11-29T15:00:00'
    assert filled[0]['price'] == bars[-1].close
    assert filled[0]['fill_assumption'] == 'nonflat_limit_close_without_queue_verification'
