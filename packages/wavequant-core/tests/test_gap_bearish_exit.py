"""固定桂林行情验证量基准；账户用显式入场隔离退出，不替代完整买点回放。"""

from dataclasses import replace
from datetime import datetime, timedelta
import json
from pathlib import Path

import pytest

import wavequant.application.analytics.backtest as backtest_module
from wavequant.application.analytics.backtest import AccountDay, BacktestResult, portfolio_days
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar, Signal
from wavequant.domain.strategies.gap_bearish_exit import GAP_VOLUME_BEARISH_CLEAR, observe_gap_bearish_exit
from wavequant.domain.strategies.staged_exit import StagedExitState, observe_volume_down_exit


def guilin() -> list[Bar]:
    data = json.loads((Path(__file__).parent / 'fixtures/guilin_2022_hierarchy.json').read_text(encoding='utf-8'))
    return [Bar(datetime.fromisoformat(day), data['symbol'], opened, high, low, close, volume)
            for day, opened, high, low, close, volume in data['bars']]


def sample() -> list[Bar]:
    # 合成前日宽幅阳线：当天无需破前低、前日无需三倍量。
    return [
        Bar(datetime(2022, 6, 29), 'sz.000978', 9, 9, 8, 8, 100),
        Bar(datetime(2022, 6, 30), 'sz.000978', 9.5, 11, 8, 10.5, 200),
        Bar(datetime(2022, 7, 1), 'sz.000978', 10, 10, 9.8, 9.8, 150),
    ]


def test_guilin_july1_uses_june29_bearish_volume_despite_shrinking_from_yesterday() -> None:
    bars = guilin()
    index = next(index for index, bar in enumerate(bars) if bar.timestamp == datetime(2022, 7, 1))
    bar = bars[index]
    assert tuple(round(value, 2) for value in (bar.open, bar.high, bar.low, bar.close)) == (7.97, 7.97, 7.35, 7.35)
    assert bar.volume == 49_050_202
    proof = observe_volume_down_exit(bars[:index + 1], index, StagedExitState())
    assert proof is not None and proof['reason'] == GAP_VOLUME_BEARISH_CLEAR
    assert proof['exit_fraction'] == 1 and 'exit_target_fraction' not in proof
    assert proof['execution_model'] == 'same_day_close'
    assert proof['decision_timestamp'] == '2022-07-01T15:00:00'
    assert proof['volume_reference_date'] == '2022-06-29'
    assert proof['volume_reference'] == 48_887_332
    assert proof['volume_reference_basis'] == 'last_bearish'
    assert proof['previous_volume'] == 69_479_611
    assert bar.volume < proof['previous_volume'] and bar.volume > proof['volume_reference']
    assert observe_volume_down_exit(bars, index, StagedExitState(volume_reduction_target=.7)) == proof


def test_exact_two_percent_and_half_range_clear_without_previous_triple_or_low_break() -> None:
    bars = sample()
    bars[-1] = replace(bars[-1], high=10.1, low=9.7)
    proof = observe_gap_bearish_exit(bars, 2)
    assert proof is not None and proof['exit_fraction'] == 1
    assert proof['bearish_body_fraction'] == pytest.approx(.02)
    assert proof['bearish_body_range_fraction'] == pytest.approx(.5)
    assert bars[-1].close > bars[-2].low
    assert observe_volume_down_exit(bars, 2, StagedExitState()) == proof


@pytest.mark.parametrize('case', [
    'equal_open', 'higher_open', 'small_body', 'long_wick', 'doji', 'bullish',
    'equal_volume', 'less_volume', 'zero_references', 'nearest_bearish_only',
    'different_symbol', 'same_time', 'factor_change', 'inactive', 'nan_volume', 'infinite_price',
])
def test_missing_condition_does_not_trigger_new_rule(case: str) -> None:
    bars = sample()
    if case in ('equal_open', 'higher_open'):
        bars[-2] = replace(bars[-2], close=10 if case == 'equal_open' else 9.9)
    elif case == 'small_body':
        bars[-1] = replace(bars[-1], close=9.8001)
    elif case == 'long_wick':
        bars[-1] = replace(bars[-1], high=10.201)
    elif case in ('doji', 'bullish'):
        bars[-1] = replace(bars[-1], close=10 if case == 'doji' else 10.1, high=10.1)
    elif case in ('equal_volume', 'less_volume'):
        bars[-1] = replace(bars[-1], volume=100 if case == 'equal_volume' else 99)
    elif case == 'zero_references':
        bars[:2] = [replace(bar, volume=0) for bar in bars[:2]]
    elif case == 'nearest_bearish_only':
        bars[0] = replace(bars[0], volume=160)
        bars.insert(0, replace(bars[0], timestamp=datetime(2022, 6, 28), volume=50))
    elif case == 'different_symbol':
        bars[-1] = replace(bars[-1], symbol='sh.600000')
    elif case == 'same_time':
        bars[-1] = replace(bars[-1], timestamp=bars[-2].timestamp)
    elif case == 'factor_change':
        bars[-1] = replace(bars[-1], adjustment_factor=2)
    elif case == 'inactive':
        bars[-1] = replace(bars[-1], raw_trading_active=False)
    elif case == 'nan_volume':
        bars[-1] = replace(bars[-1], volume=float('nan'))
    else:
        bars[-1] = replace(bars[-1], high=float('inf'))
    assert observe_gap_bearish_exit(bars, len(bars) - 1) is None


def test_previous_volume_alone_and_latest_bearish_missing_or_invalid() -> None:
    bars = sample()[-2:]
    bars[-1] = replace(bars[-1], volume=201)
    proof = observe_gap_bearish_exit(bars, 1)
    assert proof is not None and proof['volume_reference_basis'] == 'previous_session'
    bars.insert(0, replace(sample()[0], volume=0))
    assert observe_gap_bearish_exit(bars, 2) == proof


@pytest.mark.parametrize('index', [-1, 0, 3])
def test_invalid_index_is_not_a_decision(index: int) -> None:
    assert observe_gap_bearish_exit(sample(), index) is None


def test_future_stock_date_and_price_scale_do_not_change_the_rule() -> None:
    bars = sample()
    proof = observe_gap_bearish_exit(bars, 2)
    future = replace(bars[-1], timestamp=datetime(2022, 7, 4), volume=1000)
    assert observe_gap_bearish_exit([*bars, future], 2) == proof
    transformed = [replace(bar, symbol='sh.600000', timestamp=bar.timestamp + timedelta(days=365),
        open=bar.open * 10, high=bar.high * 10, low=bar.low * 10, close=bar.close * 10) for bar in bars]
    changed = observe_gap_bearish_exit(transformed, 2)
    assert changed is not None and changed['reason'] == GAP_VOLUME_BEARISH_CLEAR


def account(bars: list[Bar], *, enabled: bool = True, wave_enabled: bool = False,
            pressure_enabled: bool = False, entry_index: int | None = None) -> BacktestResult:
    entry_index = len(bars) - 2 if entry_index is None else entry_index
    entry = bars[entry_index]
    signal = Signal(entry.timestamp, entry.symbol, entry_index, 'LONG', entry.close, 5,
                    'fixture', entry.timestamp, 0, None, 'fixture', 50)
    config = StrategyConfig(volume_down_exit=enabled, wave_exhaustion_exit=wave_enabled,
        pressure_adverse_exit=pressure_enabled,
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
def test_real_quotes_account_clears_at_close_or_defers_when_not_sellable(sellable: bool) -> None:
    bars = [bar for bar in guilin() if datetime(2022, 6, 29) <= bar.timestamp <= datetime(2022, 7, 1)]
    # 夹具只有OHLCV；成交权限显式注入，不将行情文件当作涨跌停排队成交证据。
    bars[-1] = replace(bars[-1], sellable=sellable, close_sellable=sellable)
    result = account(bars)
    buys = [order for order in result.orders if order['side'] == 'BUY' and order['status'] == 'filled']
    assert len(buys) == 1
    sells = [order for order in result.orders if order['side'] == 'SELL']
    if sellable:
        filled = [order for order in sells if order['status'] == 'filled']
        assert len(filled) == 1 and filled[0]['reason'] == GAP_VOLUME_BEARISH_CLEAR
        assert filled[0]['quantity'] == buys[0]['quantity'] and filled[0]['remaining_quantity'] == 0
        assert filled[0]['price'] == bars[-1].close
        assert filled[0]['execution_timestamp'] == '2022-07-01T15:00:00'
    else:
        assert not any(order['status'] == 'filled' for order in sells)
        assert any(order['reason'] == 'not_sellable' and order['exit_fraction'] == 1 for order in sells)


@pytest.mark.parametrize('fraction', [.7, 1.0])
def test_account_prioritizes_new_clear_over_partial_wave_exit_and_preserves_existing_full_clear(
    monkeypatch: pytest.MonkeyPatch, fraction: float,
) -> None:
    def wave_exit(*_args: object, **_kwargs: object) -> dict[str, object]:
        return dict(reason='fixture_wave_exit', exit_fraction=fraction, execution_model='same_day_close')

    monkeypatch.setattr(backtest_module, 'observe_wave_exhaustion', wave_exit)
    sells = [order for order in account(sample(), wave_enabled=True).orders
             if order['side'] == 'SELL' and order['status'] == 'filled']
    assert len(sells) == 1 and sells[0]['remaining_quantity'] == 0
    assert sells[0]['reason'] == ('fixture_wave_exit' if fraction == 1 else GAP_VOLUME_BEARISH_CLEAR)


def test_volume_exit_switch_is_respected() -> None:
    assert not any(order['side'] == 'SELL' for order in account(sample(), enabled=False).orders)


@pytest.mark.parametrize('fraction', [.7, 1.0])
def test_account_pressure_partial_cannot_override_clear_but_full_clear_keeps_priority(
    monkeypatch: pytest.MonkeyPatch, fraction: float,
) -> None:
    class PressureStream:
        def __init__(self, bars: list[Bar], _config: StrategyConfig, *, start_index: int = 0) -> None:
            self.start_index = start_index
            self.last_index = len(bars) - 1

        def advance(self, index: int, _context: list[int | None]) -> tuple[dict, dict, dict]:
            if index != self.last_index:
                return {}, {}, {}
            return {index: dict(reason='fixture_pressure_exit', exit_fraction=fraction,
                                execution_model='same_day_close')}, {}, {}

        def close(self) -> None:
            pass

    monkeypatch.setattr(backtest_module, 'PressureExitStream', PressureStream)
    sells = [order for order in account(sample(), pressure_enabled=True).orders
             if order['side'] == 'SELL' and order['status'] == 'filled']
    assert len(sells) == 1 and sells[0]['remaining_quantity'] == 0
    assert sells[0]['reason'] == ('fixture_pressure_exit' if fraction == 1 else GAP_VOLUME_BEARISH_CLEAR)


def test_previously_reduced_holding_clears_every_remaining_share(monkeypatch: pytest.MonkeyPatch) -> None:
    bars = [replace(bar, volume=bar.volume * 10_000) for bar in sample()]

    def wave_exit(_bars: list[Bar], index: int, *_args: object, **_kwargs: object) -> dict[str, object] | None:
        if index == 1:
            return dict(reason='fixture_previous_reduce', exit_fraction=.7, execution_model='same_day_close')
        return None

    monkeypatch.setattr(backtest_module, 'observe_wave_exhaustion', wave_exit)
    filled = [order for order in account(bars, wave_enabled=True, entry_index=0).orders
              if order['status'] == 'filled']
    buy, partial, clear = filled
    assert partial['reason'] == 'fixture_previous_reduce' and partial['remaining_quantity'] > 0
    assert clear['reason'] == GAP_VOLUME_BEARISH_CLEAR
    assert clear['quantity'] == partial['remaining_quantity'] and clear['remaining_quantity'] == 0
    assert partial['quantity'] + clear['quantity'] == buy['quantity']
