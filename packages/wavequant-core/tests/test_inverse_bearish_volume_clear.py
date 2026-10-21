"""冻结襄阳行情的倒 N 执行边界；信号显式注入，不冒充完整结构回放。"""

from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path

import pytest

from wavequant.application.analytics.backtest import run_portfolio
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar, Signal


def sample() -> list[Bar]:
    data = json.loads((Path(__file__).parent / 'fixtures/xiangyang_2025_trend_break.json').read_text())
    return [Bar(datetime.fromisoformat(row[0]), data['symbol'], *row[1:6])
            for row in data['rows'] if '2022-09-15' <= row[0] <= '2022-09-23']


def signals(bars: list[Bar]) -> list[Signal]:
    buy = Signal(bars[3].timestamp, bars[3].symbol, 3, 'LONG', bars[3].close,
                 5.0, 'fixture', bars[3].timestamp, 0, None, 'fixture', 20)
    inverse = replace(buy, timestamp=bars[-1].timestamp, bar_index=len(bars) - 1,
                      side='EXIT', reason='inverse_n_risk_exit', reference_price=bars[-1].close)
    return [buy, inverse]


def config() -> StrategyConfig:
    return StrategyConfig(entry_at_close=True, exit_on_target=False, inverse_n_close_reduce=True,
        volume_inverse_n_clear=True, max_hold_bars=100, max_participation=1,
        slippage_bps_per_side=0, net_reward_risk_filter=False)


def test_xiangyang_lower_body_gap_fill_clears_all_at_close() -> None:
    bars = sample()
    current, previous, bearish = bars[-1], bars[-2], bars[0]
    assert (current.open, current.high, current.low, current.close) == (7.25, 7.37, 6.94, 6.97)
    assert current.volume == 34_885_667
    assert bearish.volume < current.volume < previous.volume
    assert current.close < current.open < min(previous.open, previous.close)
    assert bars[3].low > bars[2].high == 6.95 > current.low
    result = run_portfolio({current.symbol: bars}, signals(bars), config())
    buy, sell = [row for row in result.orders if row['status'] == 'filled']
    assert sell['reason'] == 'volume_inverse_n_clear'
    assert sell['quantity'] == buy['quantity'] and sell['remaining_quantity'] == 0
    assert sell['price'] == 6.97
    assert sell['execution_timestamp'] == '2022-09-23T15:00:00'
    assert sell['bearish_reference_date'] == '2022-09-15'
    assert sell['bearish_reference_volume'] == 18_416_582
    assert not result.open_positions


@pytest.mark.parametrize('case', ['equal_volume', 'lower_volume', 'bullish', 'no_inverse', 'disabled'])
def test_last_bearish_extension_requires_volume_bearish_and_confirmed_inverse(case: str) -> None:
    bars = sample()
    if case in ('equal_volume', 'lower_volume'):
        bars[-1] = replace(bars[-1], volume=bars[0].volume - (case == 'lower_volume'))
    elif case == 'bullish':
        bars[-1] = replace(bars[-1], open=6.96)
    selected = signals(bars)
    if case == 'no_inverse':
        selected = selected[:1]
    options = replace(config(), volume_inverse_n_clear=False) if case == 'disabled' else config()
    result = run_portfolio({bars[0].symbol: bars}, selected, options)
    assert not any(row['reason'] == 'volume_inverse_n_clear' for row in result.orders)


def test_later_bearish_volume_does_not_replace_known_reference() -> None:
    bars = sample()
    future = replace(bars[-1], timestamp=datetime(2022, 9, 26), volume=100_000_000)
    result = run_portfolio({bars[0].symbol: [*bars, future]}, signals(bars), config())
    sell = next(row for row in result.orders if row['side'] == 'SELL' and row['status'] == 'filled')
    assert sell['reason'] == 'volume_inverse_n_clear'
    assert sell['execution_timestamp'] == '2022-09-23T15:00:00'
    assert sell['bearish_reference_date'] == '2022-09-15'
