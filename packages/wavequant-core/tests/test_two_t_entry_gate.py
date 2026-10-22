"""桂林冻结行情 + 显式 N/资格事实；不冒充完整趋势识别与在线账户回放。"""

from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path

import pytest

from wavequant.application.analytics.backtest import run_portfolio
from wavequant.application.analytics.intraday_entry import _qualified_gap_entries, _qualified_rising_close_entries
from wavequant.domain.market_structure.n_shape import ValueDomain, project_n_targets
from wavequant.domain.market_structure.polyline_lifecycle import PolylineBuyGate
from wavequant.domain.market_structure.price_action import Direction
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar, Signal
from wavequant.domain.strategies.integrated_strategy import SystemResult, SystemStrategy
from wavequant.domain.strategies.trading_intervals import TradingInterval
from wavequant.domain.strategies.two_t_entry_gate import (
    TWO_T_ENTRY_REACHED, TwoTEntryTarget, entry_target_from_events, two_t_entry_rejection,
)
from wavequant.infrastructure.market_data.minute import MinuteBar
from wavequant.interfaces.research_tools.signal_execution_stream import SignalExecutionFactsStream


def guilin() -> tuple[list[Bar], TwoTEntryTarget, dict[str, object]]:
    raw = json.loads((Path(__file__).parent / 'fixtures/guilin_2021_record_high.json').read_text(encoding='utf-8'))
    bars = [Bar(datetime.fromisoformat(day), raw['symbol'], *values)
            for day, *values in raw['bars'] if day <= '2021-09-10']
    attack = next(i for i, bar in enumerate(bars) if str(bar.timestamp.date()) == '2021-08-16')
    # 用户指定 8/16 为测幅 N；固定锚点复用现有投影公式，资格另作显式输入。
    targets = project_n_targets(bars[0].low, bars[1].high, bars[3].low,
        box_anchor=bars[attack].high, direction=Direction.UP, domain=ValueDomain.PRICE)
    assert targets.one_p is not None and targets.two_t is not None
    source = TwoTEntryTarget(attack, attack, targets.one_p, targets.two_t, 'fixture-aug16')
    event: dict[str, object] = dict(event='n_completed', direction='up', bar_index=attack,
        known_at=attack, origin=0, neckline=1, pullback=3, defense=bars[3].low,
        n_id=source.source_id, target_eligible=True, target_primary=True,
        one_p=source.one_p, two_t=source.two_t)
    return bars, source, event


def rejection(bars: list[Bar], source: TwoTEntryTarget, *, high: float | None = None,
              close: float | None = None, at_close: bool = True) -> dict[str, object] | None:
    return two_t_entry_rejection(bars, len(bars) - 1, source,
        observed_high=bars[-1].high if high is None else high,
        observed_close=bars[-1].close if close is None else close, at_close=at_close)


def qualified(level: int = 2) -> tuple[list[Bar], TwoTEntryTarget, SystemResult, SystemStrategy, TradingInterval]:
    bars, source, event = guilin()
    window = TradingInterval(level, 'fixture', source.attack, 1, start_low_price=bars[3].low)
    confirmation = dict(event='trend_lifecycle_state', state='空多交替', bar_index=source.attack,
        trend_level=level, source_path='fixture', breakout_level=bars[1].high)
    strategy = SystemStrategy(pivot_mode='lecture_causal', entry_policy='hierarchical_two_buy_points',
        buy_point_definition='whole_flip_wave_v3', buy_interval_level=level)
    return bars, source, SystemResult([], [event, confirmation], {}), strategy, window


def test_guilin_reported_prices_and_first_two_t_touch_reject_even_with_close_below_target() -> None:
    bars, source, _ = guilin()
    assert tuple(round(value, 2) for value in (bars[-1].open, bars[-1].high, bars[-1].low, bars[-1].close)) == (
        6.49, 7.00, 6.46, 6.92)
    assert round(bars[source.attack].close, 2) == 5.20
    assert source.one_p < bars[-1].open < bars[-1].close < source.two_t <= bars[-1].high
    proof = rejection(bars, source)
    assert proof is not None and proof['reason'] == TWO_T_ENTRY_REACHED
    assert proof['target_source_date'] == '2021-08-16'
    assert proof['target_observed_price'] == bars[-1].high


@pytest.mark.parametrize('offset, blocked', [(-.000001, False), (0, True), (.000001, True)])
def test_touch_is_inclusive_and_close_need_not_reach(offset: float, blocked: bool) -> None:
    bars, source, _ = guilin()
    assert (rejection(bars, source, high=source.two_t + offset) is not None) is blocked


def test_future_high_cannot_cancel_an_earlier_minute_and_prior_reached_source_is_not_permanently_blocked() -> None:
    bars, source, _ = guilin()
    assert rejection(bars, source, high=6.7, close=6.6, at_close=False) is None
    assert rejection(bars, source, high=source.two_t, close=6.8, at_close=False) is not None
    bars[-2] = replace(bars[-2], high=source.two_t)
    assert rejection(bars, source) is None


def test_first_knowledge_day_uses_close_and_is_unavailable_intraday() -> None:
    bars, source, _ = guilin()
    source = replace(source, known_at=len(bars) - 1)
    assert rejection(bars, source) is None
    assert rejection(bars, source, close=source.two_t) is not None
    assert rejection(bars, source, close=source.two_t, at_close=False) is None


@pytest.mark.parametrize('field, value', [('two_t', float('nan')), ('one_p', 0), ('two_t', 1)])
def test_incomplete_target_cannot_manufacture_rejection(field: str, value: float) -> None:
    bars, source, _ = guilin()
    invalid = replace(source, one_p=value) if field == 'one_p' else replace(source, two_t=value)
    assert rejection(bars, invalid) is None


def test_event_selection_ignores_future_child_and_preserves_retirement_clock() -> None:
    bars, source, event = guilin()
    index = len(bars) - 1
    future = dict(event, bar_index=index + 1, known_at=index + 1, n_id='future')
    child = dict(event, bar_index=index, target_eligible=False, n_id='child')
    assert entry_target_from_events([event, future, child], index, at_close=True) == source
    retired = dict(event='n_target_source_retired', bar_index=index, attack=source.attack, n_id=source.source_id)
    assert entry_target_from_events([event, retired], index, at_close=True) is None
    assert entry_target_from_events([event, retired], index, at_close=False) == source
    replacement = dict(event, bar_index=index - 1, known_at=index - 1, n_id='replacement')
    retired['n_id'] = 'replacement'
    assert entry_target_from_events([event, replacement, retired], index, at_close=True) is None


@pytest.mark.parametrize('level', [1, 2, 3])
def test_all_qualification_levels_reject_guilin_close_supplement(level: int) -> None:
    bars, _, generated, strategy, window = qualified(level)
    resolved, days = _qualified_rising_close_entries(bars, generated, strategy,
        only_index=len(bars) - 1, intervals=[window], polyline_gate=PolylineBuyGate())
    assert days == {len(bars) - 1}
    assert not resolved.signals
    assert any(row.get('reason') == TWO_T_ENTRY_REACHED for row in resolved.audit)
    # 同行情去除目标事实应仍走原买入规则，防止测试因其他门禁拒绝而虚假成功。
    unbound = SystemResult([], generated.audit[1:], {})
    control, _ = _qualified_rising_close_entries(bars, unbound, strategy,
        only_index=len(bars) - 1, intervals=[window], polyline_gate=PolylineBuyGate())
    assert any(signal.side == 'LONG' for signal in control.signals)


@pytest.mark.parametrize('early', [False, True])
def test_gap_minute_uses_only_observed_high(early: bool) -> None:
    bars, source, generated, strategy, window = qualified()
    bar = replace(bars[-1], open=6.6, low=6.6)
    bars[-1] = bar
    first_high = 6.8 if early else source.two_t
    minute = [MinuteBar(bar.timestamp.replace(hour=9, minute=35), 6.6, first_high, 6.6, 6.7, 1_000_000),
        MinuteBar(bar.timestamp.replace(hour=9, minute=40), 6.7, 7.0, 6.7, 6.9, 2_000_000)]
    result, executions, _, _ = _qualified_gap_entries(bars, generated, strategy, lambda _: minute,
        only_index=len(bars) - 1, intervals=[window], polyline_gate=PolylineBuyGate())
    assert bool(executions) is early
    assert bool(result.signals) is early
    if not early:
        assert any(row.get('reason') == TWO_T_ENTRY_REACHED for row in result.audit)


def test_gap_without_minutes_uses_open_not_final_high_and_gap_over_target_is_blocked() -> None:
    bars, source, generated, strategy, window = qualified()
    bars[-1] = replace(bars[-1], open=6.6, low=6.6)
    _, executions, _, _ = _qualified_gap_entries(bars, generated, strategy, None,
        only_index=len(bars) - 1, intervals=[window], polyline_gate=PolylineBuyGate())
    assert executions
    bars[-1] = replace(bars[-1], open=source.two_t, high=7.1, close=7.05)
    result, executions, _, _ = _qualified_gap_entries(bars, generated, strategy, None,
        only_index=len(bars) - 1, intervals=[window], polyline_gate=PolylineBuyGate())
    assert not executions and not result.signals
    assert any(row.get('reason') == TWO_T_ENTRY_REACHED for row in result.audit)


def test_execution_facts_preserve_live_target_and_account_rejects_close_buy() -> None:
    bars, _, event = guilin()
    index, bar = len(bars) - 1, bars[-1]
    _, facts = SignalExecutionFactsStream(bars).advance(index, [event])
    assert facts == [], 'entry targets must not alter the existing wave-exit event contract'
    signal = Signal(bar.timestamp, bar.symbol, index, 'LONG', bar.close, 4.7,
        'fixture', bar.timestamp, 0, None, 'fixture', 10)
    config = StrategyConfig(entry_at_close=True, net_reward_risk_filter=False, slippage_bps_per_side=0)
    account = run_portfolio({bar.symbol: bars}, [signal], config, entry_target_events={bar.symbol: [event]})
    assert any(row['side'] == 'BUY' and row['status'] == 'cancelled'
               and row['reason'] == TWO_T_ENTRY_REACHED for row in account.orders)
    assert not any(row['side'] == 'BUY' and row['status'] == 'filled' for row in account.orders)


@pytest.mark.parametrize('execution_price, blocked', [(6.7, False), (7.0, True)])
def test_account_minute_match_does_not_use_later_daily_high(execution_price: float, blocked: bool) -> None:
    bars, _, event = guilin()
    index, bar = len(bars) - 1, bars[-1]
    signal = Signal(bar.timestamp, bar.symbol, index, 'LONG', 6.6, 4.7,
        'fixture', bar.timestamp.replace(hour=9, minute=35), 0, None, 'fixture', 10)
    entry = dict(signal=signal, price=execution_price, buyable=True, remaining_low=6.6, remaining_high=7.0,
        timing=dict(execution_model='intraday_5m_next_open', observed_high=6.8, observed_close=6.6,
            decision_timestamp=bar.timestamp.replace(hour=9, minute=35).isoformat(),
            execution_timestamp=bar.timestamp.replace(hour=9, minute=35).isoformat()))
    account = run_portfolio({bar.symbol: bars}, [signal],
        StrategyConfig(net_reward_risk_filter=False, slippage_bps_per_side=0),
        entry_target_events={bar.symbol: [event]}, entry_executions={(bar.symbol, index): entry})
    assert any(row['reason'] == TWO_T_ENTRY_REACHED for row in account.orders) is blocked
    assert any(row['side'] == 'BUY' and row['status'] == 'filled' for row in account.orders) is not blocked


def test_other_symbols_and_price_scales_share_the_rule() -> None:
    bars, source, _ = guilin()
    scaled = [replace(bar, symbol='TEST', open=bar.open * 10, high=bar.high * 10,
                      low=bar.low * 10, close=bar.close * 10) for bar in bars]
    proof = rejection(scaled, replace(source, one_p=source.one_p * 10, two_t=source.two_t * 10))
    assert proof is not None and proof['reason'] == TWO_T_ENTRY_REACHED


def test_first_two_t_touch_blocks_add_on_without_creating_an_exit() -> None:
    bars, _, event = guilin()
    index = len(bars) - 1
    prior, bar = bars[-2:]
    initial = Signal(prior.timestamp, prior.symbol, index - 1, 'LONG', prior.close, 4.7,
        'fixture', prior.timestamp, 0, None, 'fixture', 10)
    addition = replace(initial, timestamp=bar.timestamp, bar_index=index,
        reference_price=bar.close, trigger_timestamp=bar.timestamp)
    account = run_portfolio({bar.symbol: bars}, [initial, addition],
        StrategyConfig(entry_at_close=True, allow_add_on=True, net_reward_risk_filter=False,
            slippage_bps_per_side=0, exit_on_target=False), entry_target_events={bar.symbol: [event]})
    assert len([row for row in account.orders if row['side'] == 'BUY' and row['status'] == 'filled']) == 1
    assert any(row['reason'] == TWO_T_ENTRY_REACHED for row in account.orders)
    assert not any(row['side'] == 'SELL' for row in account.orders)
