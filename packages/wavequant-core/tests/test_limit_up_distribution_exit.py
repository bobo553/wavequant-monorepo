"""Synthetic lead-in isolates the user-supplied August 1/2 body boundary."""

from dataclasses import replace
from datetime import datetime

import pytest

from wavequant.application.analytics.backtest import run_portfolio
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar, Signal
from wavequant.domain.strategies.limit_up_distribution_exit import (
    LIMIT_UP_DISTRIBUTION_CLEAR, observe_limit_up_distribution_exit,
)


def sample():
    # 前四根为合成前置行情，不冒充该股票实际历史。
    rows = [
        ('2022-07-25', 6.95, 7.05, 6.90, 7.00, 100_000_000),
        ('2022-07-26', 7.20, 7.70, 7.10, 7.70, 80_000_000),
        ('2022-07-27', 8.00, 8.47, 7.90, 8.47, 40_000_000),
        ('2022-07-29', 8.50, 9.68, 8.40, 9.68, 30_000_000),
        ('2022-08-01', 9.68, 10.65, 9.30, 10.65, 123_141_952),
        ('2022-08-02', 10.18, 10.66, 9.80, 9.91, 107_655_028),
    ]
    return [Bar(datetime.fromisoformat(day), 'sz.000678', *values) for day, *values in rows]


def test_single_heavy_rise_after_non_heavy_day_and_near_midpoint_open_can_clear():
    bars = sample()
    risk = observe_limit_up_distribution_exit(bars, 5)
    assert risk is not None
    assert risk['reason'] == LIMIT_UP_DISTRIBUTION_CLEAR
    assert risk['distribution_body_midpoint'] == 10.165
    assert risk['distribution_open_ceiling'] == 10.1844
    assert risk['distribution_quiet_dates'] == ['2022-07-26', '2022-07-27']
    assert risk['distribution_turnover_dates'] == ['2022-08-01']
    assert bars[3].volume < bars[2].volume, 'no consecutive heavy-volume requirement'
    assert bars[5].volume < bars[4].volume, 'weakness need not create another volume record'
    assert risk['exit_fraction'] == 1
    assert risk['execution_model'] == 'same_day_close'


def test_local_xiangyang_daily_sequence_preserves_july28_nonshrinking_limit_up():
    # 来自本地 TDX 原始日线的固定片段；用例未运行，不代表完成账户回测。
    rows = [
        ('2022-07-19', 5.08, 5.57, 5.03, 5.45, 33044829),
        ('2022-07-20', 5.40, 5.58, 5.27, 5.48, 17061800),
        ('2022-07-21', 5.45, 5.62, 5.30, 5.46, 18063000),
        ('2022-07-22', 5.46, 6.01, 5.40, 6.01, 32823803),
        ('2022-07-25', 6.22, 6.61, 6.19, 6.61, 25335722),
        ('2022-07-26', 7.00, 7.27, 6.96, 7.27, 19074994),
        ('2022-07-27', 8.00, 8.00, 8.00, 8.00, 3649241),
        ('2022-07-28', 8.80, 8.80, 8.80, 8.80, 5098423),
        ('2022-07-29', 9.68, 9.68, 8.80, 9.68, 137379465),
        ('2022-08-01', 9.68, 10.65, 9.30, 10.65, 123141952),
        ('2022-08-02', 10.18, 10.66, 9.80, 9.91, 107655028),
    ]
    bars = [Bar(datetime.fromisoformat(day), 'sz.000678', *values) for day, *values in rows]
    risk = observe_limit_up_distribution_exit(bars, len(bars) - 1)
    assert risk is not None
    assert risk['distribution_quiet_dates'] == ['2022-07-26', '2022-07-27']
    assert risk['distribution_turnover_dates'] == ['2022-08-01']
    assert risk['distribution_volume_mean'] == (19074994 + 3649241) / 2
    assert risk['observed_close'] == 9.91
    assert all(observe_limit_up_distribution_exit(bars, i) is None for i in range(len(bars) - 1))


@pytest.mark.parametrize('change', ['no_shrink', 'not_sealed', 'not_heavy', 'flat_midpoint',
                                   'open_too_high', 'bullish_response', 'factor_change', 'broken_quiet_low'])
def test_incomplete_or_invalid_sequence_does_not_create_an_exit(change):
    bars = sample()
    if change == 'no_shrink':
        bars[2] = replace(bars[2], volume=bars[1].volume)
    elif change == 'not_sealed':
        bars[2] = replace(bars[2], close=8.46)
    elif change == 'not_heavy':
        bars[4] = replace(bars[4], volume=119_999_999)
    elif change == 'flat_midpoint':
        bars[5] = replace(bars[5], close=10.165)
    elif change == 'open_too_high':
        bars[5] = replace(bars[5], open=10.19)
    elif change == 'bullish_response':
        bars[5] = replace(bars[5], open=9.85)
    elif change == 'factor_change':
        bars[3] = replace(bars[3], adjustment_factor=1.01)
    else:
        bars[3] = replace(bars[3], low=7.0)
    assert observe_limit_up_distribution_exit(bars, 5) is None


def test_only_completed_prefix_matters_and_bullish_volume_day_is_not_itself_an_exit():
    bars = sample()
    assert all(observe_limit_up_distribution_exit(bars, i) is None for i in range(5))
    future = replace(bars[-1], timestamp=datetime(2022, 8, 3), close=20, high=20, volume=1)
    assert observe_limit_up_distribution_exit(bars + [future], 5) == observe_limit_up_distribution_exit(bars, 5)
    assert observe_limit_up_distribution_exit([], 0) is None


@pytest.mark.parametrize('signal_only', [True, False])
def test_signal_and_held_position_paths_clear_at_same_close_and_keep_evidence(signal_only):
    bars = sample()
    buy = Signal(bars[0].timestamp, bars[0].symbol, 0, 'LONG', 7, 6,
                 'fixture', bars[0].timestamp, 0, None, 'fixture', 20)
    signals = [buy]
    if signal_only:
        signals.append(replace(buy, timestamp=bars[5].timestamp, bar_index=5, side='EXIT',
                               reason=LIMIT_UP_DISTRIBUTION_CLEAR, reference_price=9.91))
    config = StrategyConfig(entry_at_close=True, exit_on_target=False, volume_down_exit=not signal_only,
                            max_hold_bars=100, max_participation=1, slippage_bps_per_side=0)
    account = run_portfolio({bars[0].symbol: bars}, signals, config)
    sell = next(order for order in account.orders if order['side'] == 'SELL' and order['status'] == 'filled')
    assert sell['reason'] == LIMIT_UP_DISTRIBUTION_CLEAR
    assert sell['timestamp'] == bars[5].timestamp.isoformat()
    assert sell['price'] == bars[5].close
    assert sell['remaining_quantity'] == 0
    assert sell['distribution_body_midpoint'] == 10.165
    assert sell['execution_model'] == 'same_day_close'
