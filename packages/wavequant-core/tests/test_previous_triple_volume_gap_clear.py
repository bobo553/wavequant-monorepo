"""用户当日报价配合显式合成前序行情，隔离巨量次日清仓规则。"""

from dataclasses import replace
from datetime import datetime

import pytest

from wavequant.application.analytics.backtest import run_portfolio
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar, Signal
from wavequant.domain.strategies.staged_exit import StagedExitState, observe_volume_down_exit


REASON = 'volume_previous_triple_gap_bearish_clear'


def scenario() -> list[Bar]:
    # 仅1/26来自用户显示报价；前序价格与成交量为合成条件，不冒充实股历史。
    rows = [
        ('2022-01-21', 26.0, 26.1, 25.7, 25.8, 8_000_000),
        ('2022-01-24', 26.0, 27.1, 25.9, 27.0, 9_000_000),
        ('2022-01-25', 27.0, 27.5, 26.9, 27.41, 28_600_000),
        ('2022-01-26', 27.19, 27.20, 24.67, 24.67, 12_301_700),
    ]
    return [Bar(datetime.fromisoformat(day), 'sz.003029', opened, high, low, close, volume)
            for day, opened, high, low, close, volume in rows]


def test_lower_than_yesterday_volume_can_clear_against_last_bearish() -> None:
    bars = scenario()
    decision = observe_volume_down_exit(bars, 3, StagedExitState())
    assert decision is not None
    assert decision['reason'] == REASON
    assert decision['exit_fraction'] == 1.0
    assert decision['execution_model'] == 'same_day_close'
    assert decision['previous_volume_multiple'] > 3
    assert decision['observed_volume'] < decision['previous_volume']
    assert decision['observed_volume'] > decision['bearish_reference_volume']
    assert decision['bearish_reference_date'] == '2022-01-21'


@pytest.mark.parametrize('case', [
    'exact_three', 'below_three', 'zero_baseline', 'equal_open', 'higher_open',
    'close_equal_low', 'wick_only', 'small_body', 'equal_bearish_volume',
    'smaller_bearish_volume', 'previous_is_bearish', 'short_history',
])
def test_every_condition_is_required(case: str) -> None:
    bars = scenario()
    if case == 'exact_three':
        bars[2] = replace(bars[2], volume=27_000_000)
    elif case == 'below_three':
        bars[2] = replace(bars[2], volume=26_999_999)
    elif case == 'zero_baseline':
        bars[1] = replace(bars[1], volume=0)
    elif case in ('equal_open', 'higher_open'):
        bars[3] = replace(bars[3], open=27.41 if case == 'equal_open' else 27.42, high=27.5)
    elif case == 'close_equal_low':
        bars[2] = replace(bars[2], low=24.67)
    elif case == 'wick_only':
        bars[2] = replace(bars[2], low=24.6)
        bars[3] = replace(bars[3], low=24.5)
    elif case == 'small_body':
        bars[3] = replace(bars[3], open=25.0)
    elif case in ('equal_bearish_volume', 'smaller_bearish_volume'):
        bars[3] = replace(bars[3], volume=8_000_000 if case == 'equal_bearish_volume' else 7_999_999)
    elif case == 'previous_is_bearish':
        bars[2] = replace(bars[2], open=27.5)
    else:
        bars = bars[-2:]
    decision = observe_volume_down_exit(bars, len(bars) - 1, StagedExitState())
    assert decision is None or decision['reason'] != REASON


def test_previous_session_volume_path_and_exact_five_percent_body() -> None:
    bars = scenario()
    bars[3] = replace(bars[3], open=26.0, close=24.7, volume=30_000_000)
    decision = observe_volume_down_exit(bars, 3, StagedExitState())
    assert decision is not None and decision['reason'] == REASON
    assert decision['bearish_body_fraction'] == pytest.approx(.05)


def test_clear_does_not_require_a_previous_reduction_and_ignores_future() -> None:
    bars = scenario()
    expected = observe_volume_down_exit(bars, 3, StagedExitState())
    bars.append(replace(bars[-1], timestamp=datetime(2022, 1, 27), volume=100_000_000))
    assert observe_volume_down_exit(bars, 3, StagedExitState(volume_reduction_target=.7)) == expected
    state = StagedExitState()
    for index in range(3):
        decision = observe_volume_down_exit(bars[:index + 1], index, state)
        assert decision is None or decision['reason'] != REASON
    assert observe_volume_down_exit(bars[:4], 3, state) == expected


@pytest.mark.parametrize('sellable', [True, False])
def test_account_requests_all_remaining_shares_at_close_and_keeps_sellability(sellable: bool) -> None:
    bars = scenario()
    bars[-1] = replace(bars[-1], sellable=sellable, close_sellable=sellable)
    entry = bars[1]
    signal = Signal(entry.timestamp, entry.symbol, 1, 'LONG', entry.close, 20.0,
                    'system_wave_push_gap', entry.timestamp, 0, None, 'fixture', 50.0)
    config = StrategyConfig(volume_down_exit=True, entry_at_close=True, exit_on_target=False,
                            max_hold_bars=100, slippage_bps_per_side=0, max_participation=1)
    result = run_portfolio({entry.symbol: bars}, [signal], config)
    buys = [order for order in result.orders if order['side'] == 'BUY' and order['status'] == 'filled']
    assert len(buys) == 1
    sells = [order for order in result.orders if order['side'] == 'SELL']
    if sellable:
        clears = [order for order in sells if order['status'] == 'filled' and order['reason'] == REASON]
        assert len(clears) == 1
        assert clears[0]['quantity'] == buys[0]['quantity']
        assert clears[0]['remaining_quantity'] == 0
        assert clears[0]['execution_timestamp'] == '2022-01-26T15:00:00'
    else:
        assert not any(order['status'] == 'filled' for order in sells)
        assert any(order['reason'] == 'not_sellable' and order['exit_fraction'] == 1.0 for order in sells)
    prefix = run_portfolio({entry.symbol: bars[:3]}, [signal], config)
    assert prefix.orders == [order for order in result.orders if order['timestamp'][:10] < '2022-01-26']
