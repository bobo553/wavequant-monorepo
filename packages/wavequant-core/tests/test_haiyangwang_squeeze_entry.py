"""海洋王同源日线：轧空当日确认资格并买入，倒 N 身份继续隔离。"""

from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path

import pytest

from wavequant.application.analytics.backtest import run_portfolio
from wavequant.domain.market_structure.squeeze_alternation import squeeze_alternations
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals


def frozen_input() -> tuple[list[Bar], SystemStrategy]:
    raw = json.loads((Path(__file__).parent / 'fixtures/haiyangwang_2021_squeeze.json').read_text('utf-8'))
    bars = [Bar(**dict(row, timestamp=datetime.fromisoformat(row['timestamp']))) for row in raw['bars']]
    return bars, SystemStrategy(**raw['strategy'])


@pytest.mark.parametrize('with_next_day', [False, True])
def test_daily_backtest_keeps_inverse_n_without_positive_identity(with_next_day: bool) -> None:
    bars, strategy = frozen_input()
    if not with_next_day:
        bars = bars[:-1]
    result = generate_system_signals(bars, strategy)
    completed = [row for row in result.audit if row['event'] == 'n_completed']
    assert any(row['direction'] == 'down' for row in completed)
    assert any(row['direction'] == 'up' for row in completed)
    positive_attacks = {row['bar_index'] for row in completed if row['direction'] == 'up'}
    for row in result.audit:
        if row['event'] == 'squeeze_round_changed' and row.get('squeeze_owner_attack') is not None:
            assert row['squeeze_owner_attack'] in positive_attacks


@pytest.mark.parametrize('with_next_day', [False, True])
def test_same_n_opens_qualification_and_enters_on_may31_close(with_next_day: bool) -> None:
    # 完整保留 2018 起 AkShare / 新浪行情、复权因子和成交权限；不注入趋势或买点。
    bars, strategy = frozen_input()
    dates = {bar.timestamp.date().isoformat(): index for index, bar in enumerate(bars)}
    attack, confirmation = dates['2021-05-26'], dates['2021-05-31']
    if not with_next_day:
        bars = bars[:confirmation + 1]
    result = generate_system_signals(bars, strategy)
    assert any(row['event'] == 'n_completed' and row['bar_index'] == attack
               and row['known_at'] <= confirmation and row.get('direction') == 'up' for row in result.audit)
    qualification = next(row for row in result.audit
        if row['event'] == 'trend_lifecycle_state' and row.get('state') == '空多交替'
        and row['trend_level'] == 2 and row['bar_index'] == confirmation)
    proof = qualification['confirmation_evidence']
    assert proof['attack'] == attack
    assert proof['duration_confirmation'] == 'volume_bullish_n_squeeze'
    assert proof['bar_index'] == proof['candidate_index'] == confirmation
    assert any(row['event'] == 'trading_interval_opened' and row['trend_level'] == 2
               and row['bar_index'] == confirmation for row in result.audit)
    assert any(row['event'] == 'n_completed' and row['bar_index'] == attack
               and row.get('qualification_window_start') == confirmation for row in result.audit)
    assert bars[confirmation - 1].volume < bars[confirmation].volume < 2 * bars[confirmation - 1].volume
    entries = [signal for signal in result.signals if signal.side == 'LONG' and signal.bar_index >= attack]
    assert entries and entries[0].bar_index == confirmation
    emissions = [row['bar_index'] for row in result.audit if row['event'] == 'squeeze_round_changed'
                 and row.get('reason') == 'first_n_squeeze_emitted' and row['squeeze_owner_attack'] == attack]
    assert emissions == [confirmation]
    assert not any(row['event'] == 'trading_interval_opened' and row['trend_level'] == 2
                   and attack <= row['bar_index'] < confirmation for row in result.audit)
    account = run_portfolio({bars[0].symbol: bars}, [entries[0]], StrategyConfig(
        entry_at_close=True, nonflat_limit_close_fill=True, net_reward_risk_filter=False,
        slippage_bps_per_side=0))
    bought = next(order for order in account.orders if order['side'] == 'BUY' and order['status'] == 'filled')
    assert bought['execution_timestamp'] == '2021-05-31T15:00:00'
    assert bought['price'] == bars[confirmation].close


@pytest.mark.parametrize('asof', ['2021-05-26', '2021-05-28'])
def test_positive_n_does_not_open_qualification_before_squeeze_close(asof: str) -> None:
    bars, strategy = frozen_input()
    bars = [bar for bar in bars if bar.timestamp.date().isoformat() <= asof]
    attack = next(index for index, bar in enumerate(bars) if bar.timestamp.date().isoformat() == '2021-05-26')
    result = generate_system_signals(bars, strategy)
    assert not any(row['event'] == 'trading_interval_opened' and row['trend_level'] == 2
                   and row['bar_index'] >= attack for row in result.audit)
    assert not any(signal.side == 'LONG' and signal.bar_index >= attack for signal in result.signals)


@pytest.mark.parametrize('level', [1, 2, 3])
@pytest.mark.parametrize('scale', [1.0, 10.0])
def test_short_pullback_accepts_volume_bullish_squeeze_for_every_level(level: int, scale: float) -> None:
    bars, _ = frozen_input()
    dates = {bar.timestamp.date().isoformat(): index for index, bar in enumerate(bars)}
    origin, high, low = dates['2021-02-09'], dates['2021-05-07'], dates['2021-05-20']
    attack, now = dates['2021-05-26'], dates['2021-05-31']
    bars = [replace(bar, symbol='sh.600000', **{
        key: getattr(bar, key) * scale for key in ('open', 'high', 'low', 'close')}) for bar in bars[:now + 1]]
    audit = [dict(event='n_completed', direction='up', bar_index=attack, known_at=attack,
                  origin=low, neckline=low, pullback=dates['2021-05-24']),
             dict(event='regime_confirmation', regime='轧空', bar_index=now, attack=attack)]
    anchors = {attack: [dict(origin=dict(index=origin, value=bars[origin].low),
        high=dict(index=high, value=bars[high].high), key=dict(index=origin, value=10 * scale),
        known_index=high + 1, source_path='same-causal-path', trend_level=level)]}
    assert bars[now].close < bars[high].high
    events = squeeze_alternations(bars, audit, anchors)
    confirmed = next(event for event in events if event['event'] == 'squeeze_alternation_confirmed')
    assert confirmed['bar_index'] == now and confirmed['trend_level'] == level
    assert confirmed['duration_confirmation'] == 'volume_bullish_n_squeeze'
    assert not squeeze_alternations(bars[:-1], audit, anchors)
    for weak in (replace(bars[now], volume=0), replace(bars[now], close=bars[now].open),
                 replace(bars[now], low=bars[low].low - .01)):
        assert not squeeze_alternations([*bars[:-1], weak], audit, anchors)
    for event in ('squeeze_resumption_observed', 'n_consolidation_gap_confirmed'):
        assert not squeeze_alternations(bars, [audit[0], dict(audit[1], event=event)], anchors)
