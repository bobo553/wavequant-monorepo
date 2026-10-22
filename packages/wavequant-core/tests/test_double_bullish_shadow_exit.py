"""复用真实桂林OHLCV，账户显式入场仅验证退出，不替代完整策略回放。"""

from dataclasses import replace
from datetime import datetime, timedelta
import json
from pathlib import Path

import pytest

import wavequant.application.analytics.backtest as backtest_module
from wavequant.application.analytics.backtest import AccountDay, BacktestResult, portfolio_days
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar, Signal
from wavequant.domain.strategies.double_bullish_shadow_exit import (
    DOUBLE_VOLUME_BULLISH_SHADOW_CLEAR, DoubleVolumeBullishShadowExit,
)
from wavequant.domain.strategies.staged_exit import StagedExitState, observe_volume_down_exit


def sample() -> list[Bar]:
    rows = [(10.2, 10.3, 9.9, 10.0, 100),
            (10.0, 11.2, 9.0, 10.5, 200),
            (10.0, 10.6, 9.9, 10.2, 300),
            (10.4, 10.5, 10.0, 10.1, 150)]
    return [Bar(datetime(2022, 11, 10) + timedelta(days=index), 'sz.000978', *row)
            for index, row in enumerate(rows)]


def guilin() -> list[Bar]:
    data = json.loads((Path(__file__).parent / 'fixtures/guilin_2022_hierarchy.json').read_text(encoding='utf-8'))
    return [Bar(datetime.fromisoformat(day), data['symbol'], opened, high, low, close, volume)
            for day, opened, high, low, close, volume in data['bars']]


def test_real_nov16_clears_below_nov11_close_using_nov8_bearish_volume() -> None:
    bars = guilin()
    index = next(index for index, bar in enumerate(bars) if bar.timestamp == datetime(2022, 11, 16))
    warning = next(bar for bar in bars if bar.timestamp == datetime(2022, 11, 11))
    assert tuple(round(value, 2) for value in (warning.open, warning.high, warning.low, warning.close)) == (
        7.51, 8.13, 7.26, 7.77)
    bar = bars[index]
    assert tuple(round(value, 2) for value in (bar.open, bar.high, bar.low, bar.close)) == (7.82, 8.01, 7.58, 7.62)
    proof = observe_volume_down_exit(bars[:index + 1], index, StagedExitState())
    assert proof is not None and proof['reason'] == DOUBLE_VOLUME_BULLISH_SHADOW_CLEAR
    assert proof['exit_fraction'] == 1.0 and 'exit_target_fraction' not in proof
    assert proof['execution_model'] == 'same_day_close'
    assert proof['decision_timestamp'] == '2022-11-16T15:00:00'
    assert proof['warning_date'] == '2022-11-11' and proof['warning_close'] == warning.close
    assert proof['warning_volume'] == 47_513_845 and proof['warning_baseline_volume'] == 21_085_732
    assert proof['volume_reference_basis'] == 'last_bearish'
    assert proof['volume_reference_date'] == '2022-11-08' and proof['volume_reference'] == 10_260_062
    assert proof['observed_volume'] == 22_500_013 and proof['previous_volume'] == 25_306_858
    assert warning.high - warning.close > warning.close - warning.open
    assert warning.high - warning.close < (warning.high - warning.low) / 2
    assert bar.low > warning.low and bar.open > bars[index - 1].close
    assert observe_volume_down_exit(bars, index, StagedExitState(volume_reduction_target=.7)) == proof


def test_exact_double_volume_warns_without_reduction_and_later_bearish_clears() -> None:
    bars = sample()
    tracker = DoubleVolumeBullishShadowExit()
    assert tracker.advance(bars, 1) is None
    assert tracker.advance(bars, 2) is None
    proof = tracker.advance(bars, 3)
    assert proof is not None and proof['reason'] == DOUBLE_VOLUME_BULLISH_SHADOW_CLEAR
    assert proof['warning_volume_multiple'] == 2 and proof['volume_multiple'] == 1.5
    assert bars[3].low > bars[1].low and bars[3].open > bars[2].close


@pytest.mark.parametrize('case', [
    'below_double', 'zero_baseline', 'warning_doji', 'warning_bearish', 'equal_shadow', 'short_shadow',
    'response_doji', 'response_bullish', 'equal_close', 'higher_close', 'equal_volume', 'lower_volume',
    'different_symbol', 'unordered', 'factor_change', 'inactive', 'invalid_volume', 'invalid_price',
])
def test_missing_conditions_do_not_clear(case: str) -> None:
    bars = sample()
    if case == 'below_double':
        bars[1] = replace(bars[1], volume=199.99)
    elif case == 'zero_baseline':
        bars[0] = replace(bars[0], volume=0)
    elif case in ('warning_doji', 'warning_bearish'):
        bars[1] = replace(bars[1], open=10.5 if case == 'warning_doji' else 10.6)
    elif case in ('equal_shadow', 'short_shadow'):
        bars[1] = replace(bars[1], high=11 if case == 'equal_shadow' else 10.99)
    elif case in ('response_doji', 'response_bullish'):
        bars[3] = replace(bars[3], open=10.1 if case == 'response_doji' else 10.0)
    elif case in ('equal_close', 'higher_close'):
        bars[3] = replace(bars[3], open=10.7, high=10.8, close=10.5 if case == 'equal_close' else 10.51)
    elif case in ('equal_volume', 'lower_volume'):
        bars[3] = replace(bars[3], volume=100 if case == 'equal_volume' else 99)
    elif case == 'different_symbol':
        bars[2] = replace(bars[2], symbol='sh.600000')
    elif case == 'unordered':
        bars[3] = replace(bars[3], timestamp=bars[2].timestamp)
    elif case == 'factor_change':
        bars[3] = replace(bars[3], adjustment_factor=2)
    elif case == 'inactive':
        bars[1] = replace(bars[1], raw_trading_active=False)
    elif case == 'invalid_volume':
        bars[1] = replace(bars[1], volume=float('nan'))
    else:
        bars[1] = replace(bars[1], high=float('inf'))
    assert DoubleVolumeBullishShadowExit().advance(bars, 3) is None


def test_previous_session_volume_alone_suffices() -> None:
    bars = sample()
    bars[0] = replace(bars[0], open=9.9)
    bars[3] = replace(bars[3], volume=301)
    proof = DoubleVolumeBullishShadowExit().advance(bars, 3)
    assert proof is not None and proof['volume_reference_basis'] == 'previous_session'


def test_latest_bearish_is_used_instead_of_an_older_smaller_reference() -> None:
    bars = sample()
    bars[2] = replace(bars[2], open=10.3)
    assert DoubleVolumeBullishShadowExit().advance(bars, 3) is None


def test_new_warning_replaces_old_close_and_a_trigger_is_consumed_once() -> None:
    bars = sample()
    bars[2] = replace(bars[2], open=9.5, high=11, low=9, close=10, volume=400)
    tracker = DoubleVolumeBullishShadowExit()
    assert tracker.advance(bars, 3) is None  # 低于旧10.5但高于新10.0。
    bars.append(replace(bars[-1], timestamp=bars[-1].timestamp + timedelta(days=1), low=9.7, close=9.8, volume=151))
    proof = tracker.advance(bars, 4)
    assert proof is not None and proof['warning_date'] == bars[2].timestamp.date().isoformat()
    bars.append(replace(bars[-1], timestamp=bars[-1].timestamp + timedelta(days=1), low=9.6, close=9.7, volume=152))
    assert tracker.advance(bars, 5) is None


def test_prefix_stream_rewind_and_output_copy_are_causal() -> None:
    bars = sample()
    tracker = DoubleVolumeBullishShadowExit()
    for index in range(len(bars)):
        assert tracker.advance(bars, index) == DoubleVolumeBullishShadowExit().advance(bars[:index + 1], index)
    proof = tracker.advance(bars, 3)
    assert proof is not None
    proof['exit_fraction'] = .7
    repeated = tracker.advance(bars, 3)
    assert repeated is not None and repeated['exit_fraction'] == 1
    assert tracker.advance(bars, 1) is None
    assert tracker.advance(bars, 3) == repeated
    future = replace(bars[-1], timestamp=bars[-1].timestamp + timedelta(days=1), volume=1000)
    assert DoubleVolumeBullishShadowExit().advance([*bars, future], 3) == repeated
    shifted = [replace(bar, symbol='sh.600000', timestamp=bar.timestamp + timedelta(days=365)) for bar in bars]
    other = DoubleVolumeBullishShadowExit().advance(shifted, 3)
    assert other is not None and other['reason'] == DOUBLE_VOLUME_BULLISH_SHADOW_CLEAR


def account(bars: list[Bar], *, enabled: bool = True, wave_enabled: bool = False,
            entry_index: int | None = None) -> BacktestResult:
    entry_index = len(bars) - 2 if entry_index is None else entry_index
    entry = bars[entry_index]
    signal = Signal(entry.timestamp, entry.symbol, entry_index, 'LONG', entry.close, 5,
                    'fixture', entry.timestamp, 0, None, 'fixture', 50)
    config = StrategyConfig(volume_down_exit=enabled, wave_exhaustion_exit=wave_enabled,
        trend_flip_adverse_exit=False, entry_at_close=True, exit_on_target=False,
        initial_capital=32_000, max_position_weight=1, risk_fraction=1, max_participation=1,
        max_hold_bars=100, commission_bps_per_side=0, slippage_bps_per_side=0,
        minimum_commission=0, a_share_taxes=False, net_reward_risk_filter=False)
    runner = portfolio_days({entry.symbol: bars}, [], config, live=True)
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


@pytest.mark.parametrize('sellable', [True, False])
def test_real_quotes_account_clears_nov16_at_close_or_defers(sellable: bool) -> None:
    bars = [bar for bar in guilin() if datetime(2022, 11, 8) <= bar.timestamp <= datetime(2022, 11, 16)]
    bars[-1] = replace(bars[-1], sellable=sellable, close_sellable=sellable)
    result = account(bars)
    buys = [order for order in result.orders if order['side'] == 'BUY' and order['status'] == 'filled']
    assert len(buys) == 1
    sells = [order for order in result.orders if order['side'] == 'SELL']
    if sellable:
        filled = [order for order in sells if order['status'] == 'filled']
        assert len(filled) == 1 and filled[0]['reason'] == DOUBLE_VOLUME_BULLISH_SHADOW_CLEAR
        assert filled[0]['quantity'] == buys[0]['quantity'] and filled[0]['remaining_quantity'] == 0
        assert filled[0]['price'] == bars[-1].close
        assert filled[0]['execution_timestamp'] == '2022-11-16T15:00:00'
    else:
        assert not any(order['status'] == 'filled' for order in sells)
        assert any(order['reason'] == 'not_sellable' and order['exit_fraction'] == 1 for order in sells)


@pytest.mark.parametrize('fraction', [.7, 1.0])
def test_new_full_clear_overrides_partial_wave_but_preserves_full_wave(
    monkeypatch: pytest.MonkeyPatch, fraction: float,
) -> None:
    def wave_exit(*_args: object, **_kwargs: object) -> dict[str, object]:
        return dict(reason='fixture_wave_exit', exit_fraction=fraction, execution_model='same_day_close')

    bars = [replace(bar, volume=bar.volume * 10_000) for bar in sample()]
    monkeypatch.setattr(backtest_module, 'observe_wave_exhaustion', wave_exit)
    sells = [order for order in account(bars, wave_enabled=True).orders
             if order['side'] == 'SELL' and order['status'] == 'filled']
    assert len(sells) == 1 and sells[0]['remaining_quantity'] == 0
    assert sells[0]['reason'] == ('fixture_wave_exit' if fraction == 1 else DOUBLE_VOLUME_BULLISH_SHADOW_CLEAR)


def test_remaining_position_clears_after_earlier_partial_reduction(monkeypatch: pytest.MonkeyPatch) -> None:
    bars = [replace(bar, volume=bar.volume * 10_000) for bar in sample()]

    def wave_exit(_bars: list[Bar], index: int, *_args: object, **_kwargs: object) -> dict[str, object] | None:
        if index == 2:
            return dict(reason='fixture_previous_reduce', exit_fraction=.7, execution_model='same_day_close')
        return None

    monkeypatch.setattr(backtest_module, 'observe_wave_exhaustion', wave_exit)
    buy, partial, clear = [order for order in account(bars, wave_enabled=True, entry_index=0).orders
                           if order['status'] == 'filled']
    assert partial['reason'] == 'fixture_previous_reduce' and partial['remaining_quantity'] > 0
    assert clear['reason'] == DOUBLE_VOLUME_BULLISH_SHADOW_CLEAR
    assert clear['quantity'] == partial['remaining_quantity'] and clear['remaining_quantity'] == 0
    assert partial['quantity'] + clear['quantity'] == buy['quantity']


def test_volume_exit_switch_and_state_isolation() -> None:
    bars = [replace(bar, volume=bar.volume * 10_000) for bar in sample()]
    assert not any(order['side'] == 'SELL' for order in account(bars, enabled=False).orders)
    first, second = StagedExitState(), StagedExitState()
    observe_volume_down_exit(bars, 1, first)
    assert second.double_bullish_shadow.warning is None
