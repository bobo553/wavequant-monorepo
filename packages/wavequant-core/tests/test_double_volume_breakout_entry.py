"""国芳冻结量价与合成资格分开验证，不冒充全历史资格及涨停成交复现。"""

from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path

import pytest

from tests.test_qualified_rising_close_entry import sample
from wavequant.application.analytics.backtest import run_portfolio
from wavequant.application.analytics.intraday_entry import _qualified_rising_close_entries
from wavequant.domain.market_state.candle_strength import (
    QUALIFIED_VOLUME_BREAKOUT_CLOSE, double_volume_bullish_breakout,
)
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar


def guofang_bars() -> list[Bar]:
    raw = json.loads((Path(__file__).parent / 'fixtures/guofang_2025_five_top_low_open.json').read_text('utf-8'))
    return [Bar(**dict(row, timestamp=datetime.fromisoformat(row['timestamp'])))
            for row in raw['bars'] if row['timestamp'][:10] in ('2025-04-02', '2025-04-03')]


@pytest.mark.parametrize('level', (1, 2, 3))
def test_april3_low_open_double_volume_generates_close_buy_with_existing_qualification(level: int) -> None:
    bars = guofang_bars()
    _, generated, strategy, window = sample(level)
    assert bars[1].open < bars[0].close
    assert bars[1].volume == 20_135_200
    assert bars[1].volume >= 2 * bars[0].volume
    assert bars[1].close > bars[0].high
    resolved, days = _qualified_rising_close_entries(bars, generated, strategy, only_index=1, intervals=[window])
    assert days == {1} and len(resolved.signals) == 1
    signal = resolved.signals[0]
    assert signal.reason == QUALIFIED_VOLUME_BREAKOUT_CLOSE
    assert signal.trigger_timestamp == bars[1].timestamp.replace(hour=15, minute=0)
    assert signal.reference_price == bars[1].close
    proof = next(event for event in resolved.audit if event['event'] == 'long_transition_evidence')
    assert proof['breakout_reference'] == bars[0].high
    assert proof['volume_multiple'] == pytest.approx(bars[1].volume / bars[0].volume)
    # 单独授予成交权限，只检验新通道的收盘时机，不宣称实盘涨停一定成交。
    tradable = [replace(bar, close_buyable=True) for bar in bars]
    result = run_portfolio({bars[0].symbol: tradable}, resolved.signals,
        StrategyConfig(entry_at_close=False, slippage_bps_per_side=0, net_reward_risk_filter=False))
    buys = [order for order in result.orders if order['side'] == 'BUY' and order['status'] == 'filled']
    assert len(buys) == 1 and buys[0]['timestamp'].startswith('2025-04-03')
    assert buys[0]['execution_model'] == 'same_day_close' and buys[0]['price'] == bars[1].close


@pytest.mark.parametrize('case', ('missing', 'closed', 'future', 'wrong_level'))
def test_breakout_does_not_invent_qualification(case: str) -> None:
    bars = guofang_bars()
    _, generated, strategy, window = sample()
    if case == 'missing':
        generated.audit.clear()
    elif case == 'closed':
        window = replace(window, end_index=1)
    elif case == 'future':
        window = replace(window, start_index=2)
    else:
        window = replace(window, trend_level=1)
    resolved, days = _qualified_rising_close_entries(bars, generated, strategy, only_index=1, intervals=[window])
    assert not resolved.signals and not days


@pytest.mark.parametrize('change', ('volume', 'zero_volume', 'shadow', 'no_break', 'weak_body', 'symbol', 'future'))
def test_each_breakout_condition_is_required(change: str) -> None:
    previous = Bar(datetime(2025, 4, 2), 'TEST', 10, 10.5, 9.8, 10, 100)
    current = Bar(datetime(2025, 4, 3), 'TEST', 9.9, 10.8, 9.8, 10.7, 200)
    assert double_volume_bullish_breakout(current, previous)
    if change == 'volume':
        current = replace(current, volume=199)
    elif change == 'zero_volume':
        previous = replace(previous, volume=0)
    elif change == 'shadow':
        current = replace(current, high=12)
    elif change == 'no_break':
        previous = replace(previous, high=current.close)
    elif change == 'weak_body':
        current = replace(current, open=10.6)
    elif change == 'symbol':
        previous = replace(previous, symbol='OTHER')
    else:
        previous = replace(previous, timestamp=current.timestamp)
    assert not double_volume_bullish_breakout(current, previous)


def test_exact_double_volume_medium_body_and_high_close_boundaries() -> None:
    previous = Bar(datetime(2025, 4, 2), 'TEST', 10.01, 10.1, 9.9, 10.01, 100)
    current = Bar(datetime(2025, 4, 3), 'TEST', 10, 10.25, 10, 10.2, 200)
    assert double_volume_bullish_breakout(current, previous)
    assert not double_volume_bullish_breakout(replace(current, high=10.251), previous)


def test_close_permission_still_blocks_actual_fill() -> None:
    bars = guofang_bars()
    bars[1] = replace(bars[1], close_buyable=False, nonflat_close_buyable=False)
    _, generated, strategy, window = sample()
    resolved, _ = _qualified_rising_close_entries(bars, generated, strategy, only_index=1, intervals=[window])
    result = run_portfolio({bars[0].symbol: bars}, resolved.signals, StrategyConfig(entry_at_close=False))
    assert not any(order['side'] == 'BUY' and order['status'] == 'filled' for order in result.orders)


def test_repeated_resolution_has_one_signal_and_one_evidence_pair() -> None:
    bars = guofang_bars()
    _, generated, strategy, window = sample()
    first, _ = _qualified_rising_close_entries(bars, generated, strategy, only_index=1, intervals=[window])
    second, _ = _qualified_rising_close_entries(bars, first, strategy, only_index=1, intervals=[window])
    assert second == first
    assert len(second.signals) == 1
    assert sum(event['event'] == 'long_signal' for event in second.audit) == 1
    assert sum(event['event'] == 'long_transition_evidence' for event in second.audit) == 1
