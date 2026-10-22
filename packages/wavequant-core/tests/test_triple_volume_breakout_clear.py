"""海洋王两日报价来自用户；此前高点/基准量显式合成，不代表完整实股历史。"""

from dataclasses import replace
from datetime import datetime

import pytest

import wavequant.application.analytics.backtest as backtest_module
from wavequant.application.analytics.backtest import AccountDay, BacktestResult, portfolio_days
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar, Signal
from wavequant.domain.strategies.double_volume_fade_exit import (
    DOUBLE_VOLUME_BULLISH_FADE_REDUCE, TRIPLE_VOLUME_BREAKOUT_DOUBLE_BEARISH_CLEAR, observe_double_volume_fade,
)


def sample() -> list[Bar]:
    rows = [
        ('2022-02-23', 18.0, 18.8, 17.9, 18.5, 800_000),
        ('2022-02-24', 18.5, 19.5, 18.3, 19.0, 900_000),
        ('2022-02-25', 18.2, 18.8, 18.0, 18.41, 914_000),
        ('2022-02-28', 18.53, 20.25, 18.15, 20.25, 3_746_900),
        ('2022-03-01', 21.60, 21.60, 20.32, 20.87, 10_013_900),
    ]
    return [Bar(datetime.fromisoformat(day), 'sz.002724', opened, high, low, close, volume)
            for day, opened, high, low, close, volume in rows]


def test_user_quotes_clear_on_bearish_close_despite_positive_daily_return() -> None:
    bars = sample()
    proof = observe_double_volume_fade(bars, 4)
    assert proof is not None
    assert bars[4].close > bars[3].close and bars[4].close < bars[4].open
    assert proof['reason'] == TRIPLE_VOLUME_BREAKOUT_DOUBLE_BEARISH_CLEAR
    assert proof['exit_fraction'] == 1.0
    assert 'exit_target_fraction' not in proof
    assert proof['execution_model'] == 'same_day_close'
    assert proof['decision_timestamp'] == '2022-03-01T15:00:00'
    assert proof['bullish_date'] == '2022-02-28'
    assert proof['breakout_key_date'] == '2022-02-24'
    assert proof['breakout_key_high'] == 19.5
    assert proof['double_volume_reference_date'] == '2022-02-25'
    assert proof['double_volume_ratio'] == pytest.approx(3_746_900 / 914_000)
    assert proof['response_volume_ratio'] == pytest.approx(10_013_900 / 3_746_900)


@pytest.mark.parametrize('close', [19.9, 20.25, 20.87])
def test_exact_three_and_two_multiples_clear_above_equal_or_below_previous_close(close: float) -> None:
    bars = sample()
    bars[2] = replace(bars[2], volume=100.1)
    bars[3] = replace(bars[3], volume=300.3)
    bars[4] = replace(bars[4], volume=600.6, close=close, low=min(20.32, close))
    proof = observe_double_volume_fade(bars, 4)
    assert proof is not None and proof['reason'] == TRIPLE_VOLUME_BREAKOUT_DOUBLE_BEARISH_CLEAR
    assert proof['exit_fraction'] == 1.0


@pytest.mark.parametrize('case', [
    'below_triple', 'below_double', 'zero_reference', 'zero_response', 'nan_response', 'infinite_response',
    'bullish_doji', 'bullish_bearish', 'bullish_not_rising', 'response_doji', 'response_bullish',
    'equal_breakout', 'no_breakout', 'infinite_attack_high', 'no_prior_peak', 'already_broken',
    'different_symbol', 'unordered_time', 'inactive_bullish', 'inactive_response', 'factor_change',
])
def test_clear_requires_both_volumes_bullish_breakout_and_next_bearish_candle(case: str) -> None:
    bars = sample()
    if case == 'below_triple':
        bars[3] = replace(bars[3], volume=3 * bars[2].volume - 1)
    elif case == 'below_double':
        bars[4] = replace(bars[4], volume=2 * bars[3].volume - 1)
    elif case == 'zero_reference':
        bars[2] = replace(bars[2], volume=0)
    elif case in ('zero_response', 'nan_response', 'infinite_response'):
        volume = {'zero_response': 0.0, 'nan_response': float('nan'), 'infinite_response': float('inf')}[case]
        bars[4] = replace(bars[4], volume=volume)
    elif case in ('bullish_doji', 'bullish_bearish'):
        bars[3] = replace(bars[3], open=bars[3].close + (0.1 if case == 'bullish_bearish' else 0))
    elif case == 'bullish_not_rising':
        bars[3] = replace(bars[3], open=18.0, close=18.4)
    elif case in ('response_doji', 'response_bullish'):
        bars[4] = replace(bars[4], open=bars[4].close - (0.1 if case == 'response_bullish' else 0))
    elif case in ('equal_breakout', 'no_breakout'):
        bars[1] = replace(bars[1], high=bars[3].high + (0.1 if case == 'no_breakout' else 0))
    elif case == 'infinite_attack_high':
        bars[3] = replace(bars[3], high=float('inf'))
    elif case == 'no_prior_peak':
        bars[1] = replace(bars[1], high=18.8, close=18.6)
    elif case == 'already_broken':
        bars[2] = replace(bars[2], high=20.3)
    elif case == 'different_symbol':
        bars[4] = replace(bars[4], symbol='sz.000001')
    elif case == 'unordered_time':
        bars[4] = replace(bars[4], timestamp=bars[3].timestamp)
    elif case in ('inactive_bullish', 'inactive_response'):
        index = 3 if case == 'inactive_bullish' else 4
        bars[index] = replace(bars[index], raw_trading_active=False)
    else:
        bars[4] = replace(bars[4], adjustment_factor=2)
    assert observe_double_volume_fade(bars, 4) is None


@pytest.mark.parametrize('index', [-1, 0, 3, 5])
def test_insufficient_or_out_of_range_history_does_not_clear(index: int) -> None:
    assert observe_double_volume_fade(sample(), index) is None


def test_breakout_evidence_is_prefix_causal_and_not_specific_to_stock_or_date() -> None:
    bars = sample()
    expected = observe_double_volume_fade(bars, 4)
    future = replace(bars[4], timestamp=datetime(2022, 3, 2), open=21.5, high=30, close=21,
                     volume=30_000_000)
    assert observe_double_volume_fade([*bars, future], 4) == expected
    assert observe_double_volume_fade([*bars, future], 5) is None
    other = [replace(bar, symbol='sh.600000', timestamp=bar.timestamp.replace(year=2023)) for bar in bars]
    proof = observe_double_volume_fade(other, 4)
    assert proof is not None and proof['reason'] == TRIPLE_VOLUME_BREAKOUT_DOUBLE_BEARISH_CLEAR


@pytest.mark.parametrize('missing_condition', ['triple_volume', 'response_double_volume'])
def test_lower_close_still_uses_existing_ninety_percent_rule_when_full_clear_is_not_met(
    missing_condition: str,
) -> None:
    bars = sample()
    bars[4] = replace(bars[4], low=19.8, close=19.9)
    if missing_condition == 'triple_volume':
        bars[3] = replace(bars[3], volume=2 * bars[2].volume)
    else:
        bars[4] = replace(bars[4], volume=2 * bars[3].volume - 1)
    proof = observe_double_volume_fade(bars, 4)
    assert proof is not None and proof['reason'] == DOUBLE_VOLUME_BULLISH_FADE_REDUCE
    assert proof['exit_fraction'] == proof['exit_target_fraction'] == .9


def account(bars: list[Bar], *, enabled: bool = True, wave_enabled: bool = False,
            entry_index: int = 3) -> BacktestResult:
    entry = bars[entry_index]
    signal = Signal(entry.timestamp, entry.symbol, entry_index, 'LONG', entry.close,
                    17, 'fixture', entry.timestamp, 0, None, 'fixture', 60)
    config = StrategyConfig(trend_flip_adverse_exit=False, volume_down_exit=enabled,
        wave_exhaustion_exit=wave_enabled, entry_at_close=True, exit_on_target=False,
        initial_capital=32_000, max_position_weight=1, risk_fraction=1, max_participation=1,
        max_hold_bars=100, commission_bps_per_side=0, slippage_bps_per_side=0,
        minimum_commission=0, a_share_taxes=False, net_reward_risk_filter=False)
    runner = portfolio_days({bars[0].symbol: bars}, [], config, live=True)
    result = None
    try:
        next(runner)
        for index in range(len(bars)):
            try:
                runner.send(AccountDay(index, [signal] if index == entry_index else [], {}, [], {}, {}))
            except StopIteration as completed:
                result = completed.value
    finally:
        runner.close()
    assert isinstance(result, BacktestResult)
    return result


def test_account_clears_all_on_march1_close_without_formal_trend_high() -> None:
    result = account(sample())
    buy, sell = [order for order in result.orders if order['status'] == 'filled']
    assert sell['reason'] == TRIPLE_VOLUME_BREAKOUT_DOUBLE_BEARISH_CLEAR
    assert sell['quantity'] == buy['quantity'] and sell['remaining_quantity'] == 0
    assert sell['price'] == 20.87
    assert sell['execution_timestamp'] == '2022-03-01T15:00:00'


@pytest.mark.parametrize('risk_source', ['volume', 'wave'])
def test_full_clear_takes_priority_over_other_partial_exits(
    monkeypatch: pytest.MonkeyPatch, risk_source: str,
) -> None:
    def partial_exit(*_args: object, **_kwargs: object) -> dict[str, object]:
        return dict(reason='fixture_partial_reduce', exit_fraction=.9, execution_model='same_day_close')

    target = 'observe_volume_down_exit' if risk_source == 'volume' else 'observe_wave_exhaustion'
    monkeypatch.setattr(backtest_module, target, partial_exit)
    result = account(sample(), wave_enabled=risk_source == 'wave')
    sells = [order for order in result.orders if order['side'] == 'SELL' and order['status'] == 'filled']
    assert len(sells) == 1 and sells[0]['reason'] == TRIPLE_VOLUME_BREAKOUT_DOUBLE_BEARISH_CLEAR
    assert sells[0]['remaining_quantity'] == 0


def test_unavailable_close_defers_the_full_order_without_fabricating_a_fill() -> None:
    bars = sample()
    bars[4] = replace(bars[4], sellable=False, close_sellable=False)
    result = account(bars)
    assert any(order['side'] == 'SELL' and order['status'] == 'deferred'
               and order['reason'] == 'not_sellable' and order['exit_fraction'] == 1.0
               for order in result.orders)
    assert not any(order['side'] == 'SELL' and order['status'] == 'filled' for order in result.orders)


def test_previously_reduced_position_still_clears_every_remaining_share(monkeypatch: pytest.MonkeyPatch) -> None:
    def prior_partial(_bars: list[Bar], index: int, *_args: object, **_kwargs: object) -> dict[str, object] | None:
        if index == 3:
            return dict(reason='fixture_partial_reduce', exit_fraction=.9, execution_model='same_day_close')
        return None

    monkeypatch.setattr(backtest_module, 'observe_volume_down_exit', prior_partial)
    result = account(sample(), entry_index=2)
    buy, partial, clear = [order for order in result.orders if order['status'] == 'filled']
    assert partial['reason'] == 'fixture_partial_reduce' and partial['remaining_quantity'] > 0
    assert clear['reason'] == TRIPLE_VOLUME_BREAKOUT_DOUBLE_BEARISH_CLEAR
    assert clear['quantity'] == partial['remaining_quantity']
    assert partial['quantity'] + clear['quantity'] == buy['quantity']
    assert clear['remaining_quantity'] == 0


def test_existing_full_clear_keeps_its_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    def full_exit(*_args: object, **_kwargs: object) -> dict[str, object]:
        return dict(reason='fixture_existing_full_clear', exit_fraction=1.0, execution_model='same_day_close')

    monkeypatch.setattr(backtest_module, 'observe_volume_down_exit', full_exit)
    sells = [order for order in account(sample()).orders
             if order['side'] == 'SELL' and order['status'] == 'filled']
    assert len(sells) == 1 and sells[0]['reason'] == 'fixture_existing_full_clear'
    assert sells[0]['remaining_quantity'] == 0


def test_volume_exit_switch_remains_respected() -> None:
    assert not any(order['side'] == 'SELL' for order in account(sample(), enabled=False).orders)
