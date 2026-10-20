"""冻结国芳行情验证买入门禁；合成翻多背景单独验证三级共用确认，不冒充全历史回放。"""

from dataclasses import replace
from datetime import datetime, timedelta
import json
from pathlib import Path

import pytest

from wavequant.application.analytics.backtest import run_portfolio
from wavequant.application.analytics.intraday_entry import (
    _qualified_gap_entries, _qualified_rising_close_entries, resolve_consolidation_entries,
)
from wavequant.domain.market_state.candle_strength import (
    QUALIFIED_FOLLOW_THROUGH_CLOSE, QUALIFIED_VOLUME_BREAKOUT_CLOSE, volume_gap_follow_through,
)
from wavequant.domain.market_structure.bull_alternation_confirmation import (
    confirm_bull_alternation, confirm_volume_breakout_alternation,
)
from wavequant.domain.market_structure.polyline import LinePoint, PointKind, ReversalPoint
from wavequant.domain.market_structure.polyline_lifecycle import PolylineBuyGate
from wavequant.domain.market_structure.trend_lifecycle import TrendLifecycle, _Pivot
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar, Signal
from wavequant.domain.strategies.integrated_strategy import SystemResult, pivot_history
from wavequant.domain.strategies.trading_intervals import confirmed_trading_intervals
from tests.test_qualified_rising_close_entry import sample


@pytest.fixture(scope='module')
def frozen_market():
    raw = json.loads((Path(__file__).parent / 'fixtures/guofang_2025_combined_a.json').read_text('utf-8'))
    bars = [Bar(datetime.fromisoformat(row[0]), raw['symbol'],
                open=row[1], high=row[2], low=row[3], close=row[4], volume=row[5],
                adjustment_factor=row[6]) for row in raw['bars']]
    dates = {bar.timestamp.date().isoformat(): index for index, bar in enumerate(bars)}
    strategy = sample()[2]
    pivots = pivot_history(bars, strategy)
    gate = PolylineBuyGate()
    for index, bar in enumerate(bars):
        gate.advance(index, bar, pivots[0][index], pivots[1][index])
    return bars, dates, pivots, gate


@pytest.mark.parametrize('level', (1, 2, 3))
@pytest.mark.parametrize('date', ('2025-02-27', '2025-03-14'))
def test_local_double_volume_below_january9_cannot_bypass_the_existing_gate(frozen_market, level, date):
    bars, dates, _, gate = frozen_market
    index, key = dates[date], dates['2025-01-09']
    _, generated, strategy, window = sample(level)
    rejection = gate.restrictions[index]
    assert rejection['polyline_key_index'] == key
    assert rejection['polyline_key_price'] == bars[key].high
    assert bars[index].high < bars[key].high
    assert window.start_low_price is not None
    generated.signals.append(Signal(bars[index].timestamp, bars[index].symbol, index, 'LONG',
        bars[index].close, window.start_low_price, 'stale_direct_entry',
        bars[index].timestamp.replace(hour=15), 0, None, 'fixture'))
    resolved, _ = _qualified_rising_close_entries(bars, generated, strategy, only_index=index,
                                                 intervals=[window], polyline_gate=gate)
    assert not resolved.signals
    assert any(row.get('reason') == 'polyline_last_fall_high_not_broken' for row in resolved.audit)


@pytest.mark.parametrize('level', (1, 2, 3))
def test_april_breakout_and_next_session_follow_through_use_close_execution(frozen_market, level):
    bars, dates, _, gate = frozen_market
    _, generated, strategy, window = sample(level)
    breakout, following = dates['2025-04-03'], dates['2025-04-07']
    assert following == breakout + 1
    assert gate.breakouts[breakout]['polyline_key_index'] == dates['2025-01-09']
    first, _ = _qualified_rising_close_entries(bars, generated, strategy, only_index=breakout,
                                              intervals=[window], polyline_gate=gate)
    second, _ = _qualified_rising_close_entries(bars, first, strategy, only_index=following,
                                               intervals=[window], polyline_gate=gate)
    assert [(signal.bar_index, signal.reason) for signal in second.signals] == [
        (breakout, QUALIFIED_VOLUME_BREAKOUT_CLOSE), (following, QUALIFIED_FOLLOW_THROUGH_CLOSE),
    ]
    assert all(signal.trigger_timestamp.hour == 15 for signal in second.signals)
    assert bars[following].open == bars[following].close
    assert bars[following].low > bars[breakout].high
    resolved, executions, _, _ = _qualified_gap_entries(bars, second, strategy, None,
        only_index=following, intervals=[window], polyline_gate=gate)
    assert resolved.signals == second.signals and not executions
    # 仅账户时机用合成成交许可，原冻结行情的涨停权限不据此宣称可成交。
    tradable = [replace(bar, close_buyable=True) for bar in bars]
    account = run_portfolio({bars[0].symbol: tradable}, [second.signals[-1]],
        StrategyConfig(entry_at_close=False, net_reward_risk_filter=False, slippage_bps_per_side=0))
    filled = [order for order in account.orders if order['side'] == 'BUY' and order['status'] == 'filled']
    assert len(filled) == 1 and filled[0]['timestamp'].startswith('2025-04-07')
    assert filled[0]['execution_model'] == 'same_day_close'


def test_closed_qualification_still_blocks_follow_through(frozen_market):
    bars, dates, _, gate = frozen_market
    _, generated, strategy, window = sample()
    index = dates['2025-04-07']
    resolved, days = _qualified_rising_close_entries(bars, generated, strategy, only_index=index,
        intervals=[replace(window, end_index=index)], polyline_gate=gate)
    assert not resolved.signals and not days


@pytest.mark.parametrize('change', ('equal_volume', 'fill_gap', 'bearish', 'upper_shadow'))
def test_follow_through_requires_all_current_session_conditions(frozen_market, change):
    bars, dates, _, _ = frozen_market
    previous, current = bars[dates['2025-04-03']], bars[dates['2025-04-07']]
    assert volume_gap_follow_through(current, previous)
    if change == 'equal_volume':
        current = replace(current, volume=previous.volume)
    elif change == 'fill_gap':
        current = replace(current, low=previous.high)
    elif change == 'bearish':
        current = replace(current, close=current.open - .01)
    else:
        current = replace(current, high=current.high + 1)
    assert not volume_gap_follow_through(current, previous)


def confirmation_case():
    prices = [(10, 11, 9, 10, 100), (6, 7, 5, 6, 100), (11, 12, 10, 11, 100),
              (9, 9.5, 8, 9, 100), (9.5, 10, 9, 9.6, 100), (8, 8.5, 7, 8, 100),
              (8, 8.8, 8, 8.7, 100), (8, 8.4, 7.5, 8.2, 100), (8.2, 8.6, 8, 8.4, 100),
              (8.3, 10.2, 8.3, 10.2, 300)]
    bars = [Bar(datetime(2025, 1, 1) + timedelta(days=index), 'TEST', *row)
            for index, row in enumerate(prices)]
    origin, high, broken = _Pivot(1, 0, 'L', 5, 1), _Pivot(2, 0, 'H', 12, 2), _Pivot(0, 0, 'H', 11, 0)
    polyline = (high, _Pivot(3, 0, 'L', 8, 4), _Pivot(4, 0, 'H', 10, 5),
                _Pivot(5, 0, 'L', 7, 6), _Pivot(6, 0, 'H', 8.8, 7), _Pivot(7, 0, 'L', 7.5, 8))
    return bars, origin, high, broken, polyline


@pytest.mark.parametrize('level', (1, 2, 3))
def test_shared_confirmation_opens_only_on_the_strong_structural_breakout(level):
    bars, origin, high, broken, polyline = confirmation_case()
    common = dict(origin=origin, high=high, broken=broken, points=(broken, origin, high),
                  polyline=polyline, level=level, path='fixture', after_index=7)
    assert confirm_bull_alternation(bars[:9], 8, **common) is None
    proof = confirm_bull_alternation(bars, 9, **common)
    assert proof is not None and proof.low.index == 5
    assert proof.rule == 'volume_bullish_polyline_last_fall_high_breakout'
    assert proof.evidence is not None
    assert proof.evidence['polyline_key_index'] == 4
    assert proof.evidence['polyline_key_price'] == 10
    assert proof.evidence['candidate_index'] == 9


@pytest.mark.parametrize('case', ('below_key', 'equal_key', 'future_key', 'broken_low', 'broken_origin', 'weak_volume'))
def test_structural_confirmation_rejects_missing_independent_evidence(case):
    bars, origin, high, _, polyline = confirmation_case()
    if case == 'below_key':
        bars[9] = replace(bars[9], high=9.9, close=9.9)
    elif case == 'equal_key':
        bars[9] = replace(bars[9], close=10)
    elif case == 'future_key':
        polyline = tuple(replace(point, known=10) if point.index == 4 else point for point in polyline)
    elif case == 'broken_low':
        bars[8] = replace(bars[8], low=6.9)
    elif case == 'broken_origin':
        bars[8] = replace(bars[8], low=4.9)
    else:
        bars[9] = replace(bars[9], volume=199)
    assert confirm_volume_breakout_alternation(bars, 9, origin=origin, high=high, polyline=polyline) is None


def test_strict_gap_below_structural_key_is_rejected_before_minute_loading():
    bars, _, _, _, polyline = confirmation_case()
    bars[9] = replace(bars[9], open=9, low=9, high=9.7, close=9.6)
    gate = PolylineBuyGate()
    for index, bar in enumerate(bars):
        gate.advance(index, bar, tuple(ReversalPoint(
            LinePoint(point.index, point.ordinal, PointKind(point.kind), point.price), point.known, 'fixture')
            for point in polyline if point.known <= index), 0)
    _, generated, strategy, window = sample()

    def forbidden_loader(bar):
        raise AssertionError('an unbroken structural key cannot authorize minute execution')

    resolved, executions, _, days = _qualified_gap_entries(bars, generated, strategy, forbidden_loader,
        only_index=9, intervals=[window], polyline_gate=gate)
    assert not resolved.signals and not executions and days == {9}
    rejection = next(row for row in resolved.audit if row['event'] == 'entry_rejected')
    assert rejection['polyline_key_price'] == 10


def test_changed_polyline_epoch_cannot_borrow_yesterdays_breakout():
    bars, _, _, _, polyline = confirmation_case()
    bars.append(Bar(bars[-1].timestamp + timedelta(days=1), 'TEST', 11, 11.4, 10.8, 11.4, 400))
    gate = PolylineBuyGate()
    for index, bar in enumerate(bars):
        points = tuple(ReversalPoint(
            LinePoint(point.index, point.ordinal, PointKind(point.kind), point.price), point.known, 'fixture')
            for point in polyline if point.known <= index)
        gate.advance(index, bar, points if index < 10 else (), 0 if index < 10 else 10)
    assert 9 in gate.breakouts
    _, generated, strategy, window = sample()
    resolved, days = _qualified_rising_close_entries(bars, generated, strategy, only_index=10,
                                                     intervals=[window], polyline_gate=gate)
    assert not resolved.signals and not days


@pytest.mark.parametrize('level', (1, 2, 3))
def test_lifecycle_to_qualification_to_resolver_uses_the_same_confirmation_day(level):
    bars, origin, high, broken, polyline = confirmation_case()
    dates = {bar.timestamp.date().isoformat(): index for index, bar in enumerate(bars)}

    def raw(point):
        return dict(index=point.index, ordinal=point.ordinal, kind=point.kind, value=point.price,
                    available_at=point.known, time=bars[point.index].timestamp.date().isoformat(), state='confirmed')

    high_raw = dict(raw(high), observations=[dict(title='翻空为多', available_at=high.known,
                                                confirmed_low=raw(origin), key=raw(broken))])
    row = dict(trend_level=level, strokes=[dict(id='fixture', points=[raw(broken), raw(origin), high_raw])])
    lifecycle, events, gate = TrendLifecycle(), [], PolylineBuyGate()
    for index in range(len(bars)):
        visible = tuple(point for point in polyline if point.known <= index)
        # 原始端点用于正式峰谷投影，末尾发展点不参与确认。
        line = [raw(origin), *(raw(point) for point in visible)]
        if visible:
            line.append(dict(index=index, ordinal=1, kind='H', value=bars[index].high,
                             available_at=index, time=bars[index].timestamp.date().isoformat(), state='developing'))
        events.extend(lifecycle.advance((row,), bars, index, dates, polyline=line))
        gate.advance(index, bars[index], tuple(ReversalPoint(
            LinePoint(point.index, point.ordinal, PointKind(point.kind), point.price), point.known, 'fixture')
            for point in visible), 0)
    windows = confirmed_trading_intervals(events, len(bars), level)
    assert [(window.start_index, window.start_low_index) for window in windows] == [(9, 5)]
    assert TrendLifecycle.from_checkpoint(lifecycle.checkpoint()).checkpoint() == lifecycle.checkpoint()
    strategy = replace(sample(level)[2], pivot_mode='lecture_causal')
    generated = SystemResult([], events, {})
    resolved, executions, _ = resolve_consolidation_entries(bars, generated, strategy, None,
        daily_fallback=False, qualified_gap_only=True, only_index=9, prepared_polyline_gate=gate)
    assert len(resolved.signals) == 1 and resolved.signals[0].bar_index == 9
    assert resolved.signals[0].reason == QUALIFIED_VOLUME_BREAKOUT_CLOSE
    assert not executions


def test_structural_breakout_without_close_setup_keeps_qualified_gap_route():
    bars, _, _, _, polyline = confirmation_case()
    bars[9] = replace(bars[9], open=10.1, high=10.3, low=10.1, close=10.3, volume=100)
    gate = PolylineBuyGate()
    for index, bar in enumerate(bars):
        gate.advance(index, bar, tuple(ReversalPoint(
            LinePoint(point.index, point.ordinal, PointKind(point.kind), point.price), point.known, 'fixture')
            for point in polyline if point.known <= index), 0)
    assert 9 in gate.breakouts
    _, generated, strategy, window = sample()
    resolved, days = _qualified_rising_close_entries(bars, generated, strategy, only_index=9,
                                                     intervals=[window], polyline_gate=gate)
    assert not resolved.signals and not days
    resolved, executions, _, _ = _qualified_gap_entries(bars, resolved, strategy, None,
        only_index=9, intervals=[window], polyline_gate=gate)
    assert len(resolved.signals) == 1
    assert executions['TEST', 9]['timing']['execution_model'] == 'same_day_open'
