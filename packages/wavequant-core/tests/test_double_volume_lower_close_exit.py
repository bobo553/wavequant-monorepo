"""复用真实海洋王报价；账户显式入场只隔离退出行为，不替代完整买点回放。"""

from dataclasses import replace
from datetime import datetime, timedelta
import json
from pathlib import Path

import pytest

import wavequant.application.analytics.backtest as backtest_module
from wavequant.application.analytics.backtest import AccountDay, BacktestResult, portfolio_days
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar, Signal
from wavequant.domain.strategies.double_volume_lower_close_exit import (
    DOUBLE_VOLUME_NEXT_LOWER_CLOSE_CLEAR, observe_double_volume_lower_close_exit,
)
from wavequant.domain.strategies.staged_exit import StagedExitState, observe_volume_down_exit


def haiyangwang() -> list[Bar]:
    raw = json.loads((Path(__file__).parent / 'fixtures/haiyangwang_2021_positive_n.json').read_text('utf-8'))
    return [Bar(**dict(row, timestamp=datetime.fromisoformat(row['timestamp'])))
            for row in raw['bars'] if '2021-11-30' <= row['timestamp'][:10] <= '2021-12-02']


def sample() -> list[Bar]:
    # 无前高突破、无长上影、平开小实体；当日只比前日多一股。
    rows = [(10, 12, 9, 10, 100_000), (10, 11.5, 9, 11.5, 200_000),
            (11.5, 11.5, 11.4, 11.4, 200_001)]
    return [Bar(datetime(2024, 1, 1) + timedelta(days=i), 'TEST', *row)
            for i, row in enumerate(rows)]


def test_real_dec2_expanding_but_not_double_volume_clears_remaining_position() -> None:
    bars = haiyangwang()
    assert tuple(round(v, 2) for v in (bars[2].open, bars[2].high, bars[2].low, bars[2].close)) == (
        24.45, 24.47, 22.87, 23.49)
    assert bars[0].volume == 1_419_085 and bars[1].volume == 3_498_200 and bars[2].volume == 4_680_071
    proof = observe_volume_down_exit(bars, 2, StagedExitState())
    assert proof is not None and proof['reason'] == DOUBLE_VOLUME_NEXT_LOWER_CLOSE_CLEAR
    assert proof['previous_volume_multiple'] == pytest.approx(3_498_200 / 1_419_085)
    assert 1 < proof['response_volume_multiple'] < 2
    assert proof['previous_date'] == '2021-12-01'
    assert proof['double_volume_reference_date'] == '2021-11-30'
    assert proof['exit_fraction'] == 1 and 'exit_target_fraction' not in proof
    assert proof['execution_model'] == 'same_day_close'
    assert proof['decision_timestamp'] == '2021-12-02T15:00:00'


@pytest.mark.parametrize('previous_open,current_open', [(10, 11.5), (11.5, 11.4), (12, 11.3)])
def test_exact_double_and_lower_close_do_not_require_either_candle_to_be_bullish_or_bearish(
    previous_open: float, current_open: float,
) -> None:
    bars = sample()
    bars[1] = replace(bars[1], open=previous_open, high=max(previous_open, bars[1].high))
    bars[2] = replace(bars[2], open=current_open, low=min(current_open, bars[2].low))
    proof = observe_double_volume_lower_close_exit(bars, 2)
    assert proof is not None and proof['exit_fraction'] == 1
    assert bars[2].low > bars[1].low


@pytest.mark.parametrize('case', [
    'not_double', 'equal_volume', 'shrinking', 'equal_close', 'higher_close',
    'zero_reference', 'zero_previous', 'zero_current', 'nan_volume', 'infinite_volume', 'nan_close',
    'wrong_symbol', 'same_time', 'factor_change', 'inactive_reference', 'inactive_previous', 'inactive_current',
])
def test_missing_condition_or_incomparable_sessions_do_not_trigger(case: str) -> None:
    bars = sample()
    if case == 'not_double':
        bars[1] = replace(bars[1], volume=199_999)
    elif case in ('equal_volume', 'shrinking'):
        bars[2] = replace(bars[2], volume=200_000 if case == 'equal_volume' else 199_999)
    elif case in ('equal_close', 'higher_close'):
        bars[2] = replace(bars[2], close=11.5 if case == 'equal_close' else 11.6, high=11.6)
    elif case.startswith('zero_'):
        index = {'zero_reference': 0, 'zero_previous': 1, 'zero_current': 2}[case]
        bars[index] = replace(bars[index], volume=0)
    elif case in ('nan_volume', 'infinite_volume'):
        bars[2] = replace(bars[2], volume=float('nan') if case == 'nan_volume' else float('inf'))
    elif case == 'nan_close':
        bars[2] = replace(bars[2], close=float('nan'))
    elif case == 'wrong_symbol':
        bars[0] = replace(bars[0], symbol='OTHER')
    elif case == 'same_time':
        bars[2] = replace(bars[2], timestamp=bars[1].timestamp)
    elif case == 'factor_change':
        bars[2] = replace(bars[2], adjustment_factor=2)
    else:
        index = {'inactive_reference': 0, 'inactive_previous': 1, 'inactive_current': 2}[case]
        bars[index] = replace(bars[index], raw_trading_active=False)
    assert observe_double_volume_lower_close_exit(bars, 2) is None


def test_rule_is_immediate_next_session_prefix_causal_and_not_stock_specific() -> None:
    bars = sample()
    proof = observe_double_volume_lower_close_exit(bars, 2)
    assert proof is not None
    future = replace(bars[2], timestamp=datetime(2024, 1, 4), close=11.3, low=11.3, volume=300_000)
    assert observe_double_volume_lower_close_exit([*bars, future], 2) == proof
    assert observe_double_volume_lower_close_exit([*bars, future], 3) is None
    for index in (-1, 0, 1, 3):
        assert observe_double_volume_lower_close_exit(bars, index) is None
    moved = [replace(bar, symbol='sz.000978', timestamp=bar.timestamp + timedelta(days=365)) for bar in bars]
    changed = observe_double_volume_lower_close_exit(moved, 2)
    assert changed is not None and changed['reason'] == proof['reason']


def account(bars: list[Bar], *, enabled: bool = True, entry_index: int = 1,
            wave_enabled: bool = False, pressure_enabled: bool = False) -> BacktestResult:
    entry = bars[entry_index]
    signal = Signal(entry.timestamp, entry.symbol, entry_index, 'LONG', entry.close, 5,
                    'fixture', entry.timestamp, 0, None, 'fixture', 60)
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
def test_real_quotes_account_clears_at_dec2_close_or_keeps_unfilled_full_order(sellable: bool) -> None:
    bars = haiyangwang()
    bars[-1] = replace(bars[-1], sellable=sellable, close_sellable=sellable,
                       nonflat_close_sellable=sellable)
    result = account(bars)
    buys = [order for order in result.orders if order['side'] == 'BUY' and order['status'] == 'filled']
    sells = [order for order in result.orders if order['side'] == 'SELL']
    assert len(buys) == 1
    if sellable:
        filled = [order for order in sells if order['status'] == 'filled']
        assert len(filled) == 1 and filled[0]['reason'] == DOUBLE_VOLUME_NEXT_LOWER_CLOSE_CLEAR
        assert filled[0]['quantity'] == buys[0]['quantity'] and filled[0]['remaining_quantity'] == 0
        assert filled[0]['price'] == bars[-1].close
        assert filled[0]['execution_timestamp'] == '2021-12-02T15:00:00'
    else:
        assert not any(order['status'] == 'filled' for order in sells)
        assert any(order['reason'] == 'not_sellable' and order['exit_fraction'] == 1 for order in sells)


@pytest.mark.parametrize('fraction', [.7, .9, 1.0])
def test_full_clear_overrides_partial_wave_exits_and_preserves_existing_full_exit(
    monkeypatch: pytest.MonkeyPatch, fraction: float,
) -> None:
    def wave_exit(*_args: object, **_kwargs: object) -> dict[str, object]:
        return dict(reason='fixture_wave_exit', exit_fraction=fraction, execution_model='same_day_close')

    monkeypatch.setattr(backtest_module, 'observe_wave_exhaustion', wave_exit)
    sells = [order for order in account(sample(), wave_enabled=True).orders
             if order['side'] == 'SELL' and order['status'] == 'filled']
    assert len(sells) == 1 and sells[0]['remaining_quantity'] == 0
    assert sells[0]['reason'] == ('fixture_wave_exit' if fraction == 1 else DOUBLE_VOLUME_NEXT_LOWER_CLOSE_CLEAR)


def test_previously_reduced_holding_clears_remaining_shares(monkeypatch: pytest.MonkeyPatch) -> None:
    def prior_partial(_bars: list[Bar], index: int, *_args: object, **_kwargs: object) -> dict[str, object] | None:
        if index == 1:
            return dict(reason='fixture_prior_reduce', exit_fraction=.7, execution_model='same_day_close')
        return None

    monkeypatch.setattr(backtest_module, 'observe_wave_exhaustion', prior_partial)
    buy, partial, clear = [order for order in account(sample(), entry_index=0, wave_enabled=True).orders
                           if order['status'] == 'filled']
    assert partial['remaining_quantity'] > 0
    assert clear['reason'] == DOUBLE_VOLUME_NEXT_LOWER_CLOSE_CLEAR
    assert clear['quantity'] == partial['remaining_quantity'] and clear['remaining_quantity'] == 0
    assert partial['quantity'] + clear['quantity'] == buy['quantity']


@pytest.mark.parametrize('fraction', [.7, 1.0])
def test_pressure_partial_cannot_hide_clear_but_existing_full_clear_keeps_priority(
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
    assert sells[0]['reason'] == ('fixture_pressure_exit' if fraction == 1 else DOUBLE_VOLUME_NEXT_LOWER_CLOSE_CLEAR)


def test_volume_exit_switch_and_t_plus_one_remain_respected() -> None:
    assert not any(order['side'] == 'SELL' for order in account(sample(), enabled=False).orders)
    assert not any(order['side'] == 'SELL' and order['status'] == 'filled'
                   for order in account(sample(), entry_index=2).orders)
