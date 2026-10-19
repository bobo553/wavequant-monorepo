"""突破前高后放量阴线子线全清，区分完整包含关系与未来破低。"""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
import json
from pathlib import Path

import pytest

from wavequant.application.analytics.backtest import run_portfolio
from wavequant.domain.market_state.volume_candle import is_bearish_inside_child, is_volume_bearish
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar, Signal
from wavequant.domain.strategies.breakout_shadow_exit import (
    BREAKOUT_CHILD_CLEAR, BREAKOUT_SHADOW_REDUCE, breakout_shadow_days,
)
from wavequant.domain.strategies.staged_exit import StagedExitState, observe_bearish_child_pattern_exit
from wavequant.domain.strategies.trend_flip_exit import TrendFlipExitStream, trend_flip_exit_history


def sample(level=2):
    rows = [(9.8, 10, 9.5, 9.9, 80), (9.3, 9.5, 9.0, 9.1, 50),
            (9.3, 9.9, 9.2, 9.7, 100), (9.8, 10.5, 9.7, 10.4, 200),
            (10.3, 10.4, 9.7, 9.9, 150)]
    bars = [Bar(datetime(2024, 1, 1) + timedelta(days=i), 'sh.600825', *row)
            for i, row in enumerate(rows)]
    high = dict(kind='H', index=0, value=10, available_at=1, state='confirmed')
    history = {index: {level: [dict(high)]} for index in range(1, len(bars))}
    return bars, history


def risks(bars, history):
    result = {}
    for _, result in breakout_shadow_days(bars, history):
        pass
    return result


@pytest.mark.parametrize('level', [0, 1, 2, 3])
@pytest.mark.parametrize('reference', ['previous', 'last_bearish'])
def test_volume_bearish_child_clears_at_its_close_without_waiting_for_a_low_break(level, reference):
    bars, history = sample(level)
    if reference == 'previous':
        bars[1] = replace(bars[1], volume=400)
        bars[4] = replace(bars[4], volume=250)
    proof = risks(bars, history)[4]
    assert proof['reason'] == BREAKOUT_CHILD_CLEAR and proof['exit_fraction'] == 1.0
    assert 'exit_target_fraction' not in proof
    assert proof['execution_model'] == 'same_day_close'
    assert proof['decision_timestamp'].endswith('15:00:00')
    assert proof['breakout_attack_index'] == 3 and proof['breakout_key_index'] == 0
    assert proof['breakout_resistance_phase'] == 'response'
    assert proof['child_low'] == proof['mother_low']
    assert proof['mother_index'] == 3 and proof['child_index'] == 4
    assert proof['trend_level'] == level
    assert trend_flip_exit_history(bars, history)[4]['exit_fraction'] == 1.0


@pytest.mark.parametrize('case', ['bullish_child', 'doji_child', 'doji_mother', 'high_outside',
    'low_outside', 'identical_range', 'equal_volume', 'future_key', 'developing_key', 'late_child'])
def test_breakout_child_clear_requires_every_pattern_volume_and_clock_condition(case):
    bars, history = sample()
    if case == 'bullish_child':
        bars[4] = replace(bars[4], open=9.8, close=10.3)
    elif case == 'doji_child':
        bars[4] = replace(bars[4], close=bars[4].open)
    elif case == 'doji_mother':
        bars[3] = replace(bars[3], close=bars[3].open)
    elif case == 'high_outside':
        bars[4] = replace(bars[4], high=10.6)
    elif case == 'low_outside':
        bars[4] = replace(bars[4], low=9.6)
    elif case == 'identical_range':
        bars[4] = replace(bars[4], high=bars[3].high, low=bars[3].low)
    elif case == 'equal_volume':
        bars[1] = replace(bars[1], volume=150)
        bars[3] = replace(bars[3], volume=150)
    elif case in ('future_key', 'developing_key'):
        for row in history.values():
            row[2][0].update({'available_at': 3} if case == 'future_key' else {'state': 'developing'})
    elif case == 'late_child':
        bars.append(replace(bars[4], timestamp=bars[4].timestamp + timedelta(days=1)))
        bars[4] = replace(bars[3], timestamp=bars[4].timestamp)
        history[5] = deepcopy(history[4])
    assert not any(proof['reason'] == BREAKOUT_CHILD_CLEAR for proof in risks(bars, history).values())


def test_previous_shadow_reduction_does_not_cancel_next_child_clear():
    bars, history = sample()
    bars[3] = replace(bars[3], open=10.2, high=11, low=9.7, close=9.9)
    bars[4] = replace(bars[4], open=10.1, high=10.5, low=9.8, close=10.0, volume=250)
    found = risks(bars, history)
    assert found[3]['reason'] == BREAKOUT_SHADOW_REDUCE
    assert found[4]['reason'] == BREAKOUT_CHILD_CLEAR
    assert found[4]['exit_fraction'] == 1.0


def test_child_uses_frozen_breakout_key_and_prefix_stream_matches_batch():
    bars, history = sample()
    history[3] = {2: [dict(kind='H', index=3, value=11, available_at=3)]}
    assert risks(bars, history)[4]['breakout_key_index'] == 0
    stream = TrendFlipExitStream(bars)
    try:
        for index in range(len(bars)):
            assert stream.advance(index, history) == trend_flip_exit_history(bars[:index + 1], history)
    finally:
        stream.close()


def test_inside_child_without_known_breakout_preserves_the_existing_wait_for_low_break():
    bars, _ = sample()
    assert is_bearish_inside_child(bars[3], bars[4])
    assert not risks(bars, {})
    state = StagedExitState()
    assert observe_bearish_child_pattern_exit(bars, 4, state) is None
    assert state.bearish_child_pattern_warning_index == 4


def test_account_sells_all_shares_on_the_child_close(monkeypatch):
    from wavequant.domain.strategies import hierarchical_entry

    bars, history = sample()
    bars = [replace(bar, volume=bar.volume * 1_000_000) for bar in bars]
    monkeypatch.setattr(hierarchical_entry, 'hierarchical_history', lambda *args, **kwargs: (history, {}))
    signal = Signal(bars[2].timestamp, bars[2].symbol, 2, 'LONG', bars[2].close,
                    1.0, 'fixture', bars[2].timestamp, 0, None, 'fixture', 20)
    config = StrategyConfig(trend_flip_adverse_exit=True, entry_at_close=True, exit_on_target=False,
        initial_capital=100_000, max_position_weight=1, risk_fraction=1, max_participation=1,
        max_hold_bars=100, commission_bps_per_side=0, slippage_bps_per_side=0,
        minimum_commission=0, a_share_taxes=False, net_reward_risk_filter=False)
    result = run_portfolio({bars[0].symbol: bars}, [signal], config)
    filled = [order for order in result.orders if order['status'] == 'filled']
    assert len(filled) == 2
    buy, sell = filled
    assert sell['reason'] == BREAKOUT_CHILD_CLEAR
    assert sell['quantity'] == buy['quantity'] and sell['remaining_quantity'] == 0
    assert sell['price'] == bars[4].close
    assert sell['execution_timestamp'].endswith('15:00:00')



def test_xinhua_frozen_child_is_volume_bearish_but_supplied_old_high_is_only_touched():
    fixture = json.loads((Path(__file__).parent / 'fixtures/xinhua_2024_alternation.json').read_text())
    wanted = {'2022-04-20', '2022-05-26', '2022-05-27', '2022-05-30',
              '2022-05-31', '2022-06-01', '2022-06-02'}
    bars = [Bar(datetime.fromisoformat(row[0]), 'sh.600825', *row[1:7])
            for row in fixture['bars'] if row[0][:10] in wanted]
    old_high, previous_bearish, mother, child = bars[0], bars[1], bars[-2], bars[-1]
    assert mother.high == old_high.high
    assert mother.close < old_high.high
    assert is_bearish_inside_child(mother, child)
    assert child.volume < mother.volume and child.volume > previous_bearish.volume
    assert is_volume_bearish(child, mother, previous_bearish)
    # 用户确认收盘或最高严格高于末跌高；相等不算，不降低旧 key 迎合案例。
    high = dict(kind='H', index=0, value=old_high.high, available_at=1, state='confirmed')
    history = {index: {2: [high]} for index in range(1, len(bars))}
    assert not any(proof['reason'] == BREAKOUT_CHILD_CLEAR for proof in risks(bars, history).values())



@pytest.mark.parametrize('level', [0, 1, 2, 3])
@pytest.mark.parametrize('closed_above', [False, True])
def test_high_break_is_enough_even_when_attack_close_does_not_clear_the_key(level, closed_above):
    bars, history = sample(level)
    bars[3] = replace(bars[3], close=10.4 if closed_above else 9.9)
    bars[4] = replace(bars[4], open=10.3, high=10.4, low=9.7, close=9.9)
    proof = risks(bars, history)[4]
    assert bars[3].high > proof['breakout_key_high']
    assert (bars[3].close > proof['breakout_key_high']) is closed_above
    assert proof['reason'] == BREAKOUT_CHILD_CLEAR
    assert proof['exit_fraction'] == 1.0
