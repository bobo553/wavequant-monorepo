"""突破当笔/次笔的跳空大阴线全清；结构可知日作为明确边界输入。"""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
import json
from pathlib import Path

import pytest

from wavequant.application.analytics.backtest import run_portfolio
from wavequant.domain.market_state.volume_candle import is_volume_bearish
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar, Signal
from wavequant.domain.strategies.breakout_shadow_exit import BREAKOUT_GAP_BEARISH_CLEAR, breakout_shadow_days
from wavequant.domain.strategies.trend_flip_exit import TrendFlipExitStream, trend_flip_exit_history


def sample(level=2, response=False):
    rows = [(9.8, 10, 9.5, 9.9, 80), (9.3, 9.7, 9.2, 9.6, 90),
            (9.5, 9.9, 9.4, 9.8, 100), (10.5, 10.8, 9.7, 9.8, 110)]
    if response:
        rows[3] = (9.8, 10.6, 9.7, 10.4, 120)
        rows.append((9.6, 9.7, 8.8, 8.9, 150))
    bars = [Bar(datetime(2024, 1, 1) + timedelta(days=i), 'sh.600825', *prices)
            for i, prices in enumerate(rows)]
    high = dict(kind='H', index=0, value=10, available_at=1, state='confirmed')
    history = {i: {level: [dict(high)]} for i in range(1, len(bars))}
    return bars, history


def risks(bars, history):
    result = {}
    for _, result in breakout_shadow_days(bars, history):
        pass
    return result


@pytest.mark.parametrize('level', [0, 1, 2, 3])
@pytest.mark.parametrize('response', [False, True])
def test_gap_bearish_attack_or_response_clears_without_preceding_limit_ups(level, response):
    bars, history = sample(level, response)
    index = len(bars) - 1
    proof = risks(bars, history)[index]
    assert proof['reason'] == BREAKOUT_GAP_BEARISH_CLEAR
    assert proof['exit_fraction'] == 1.0 and 'exit_target_fraction' not in proof
    assert proof['execution_model'] == 'same_day_close'
    assert proof['decision_timestamp'].endswith('15:00:00')
    assert proof['breakout_attack_index'] == 3
    assert proof['breakout_key_index'] == 0
    assert proof['breakout_resistance_phase'] == ('response' if response else 'attack')
    assert proof['breakout_gap_direction'] == ('down' if response else 'up')
    if response:
        assert bars[index].high < proof['breakout_key_high']
    result = trend_flip_exit_history(bars, history)
    assert result[index]['exit_fraction'] == 1.0
    assert result[index]['execution_model'] == 'same_day_close'


@pytest.mark.parametrize('case', ['gap_touch', 'small_body', 'thin_body', 'equal_volume',
                                  'future_key', 'developing_key', 'touch_key', 'late_response'])
def test_breakout_gap_clear_keeps_strict_causal_and_candle_boundaries(case):
    bars, history = sample(response=True)
    if case == 'gap_touch':
        bars[4] = replace(bars[4], open=bars[3].low)
    elif case == 'small_body':
        bars[4] = replace(bars[4], close=9.2)
    elif case == 'thin_body':
        bars[4] = replace(bars[4], low=8.0)
    elif case == 'equal_volume':
        bars[4] = replace(bars[4], volume=bars[3].volume)
    elif case in ('future_key', 'developing_key'):
        for row in history.values():
            row[2][0].update({'available_at': 3} if case == 'future_key' else {'state': 'developing'})
    elif case == 'touch_key':
        bars[3] = replace(bars[3], high=10, close=9.9)
    elif case == 'late_response':
        delayed = replace(bars[4], timestamp=bars[4].timestamp + timedelta(days=1))
        bars[4] = replace(bars[3], timestamp=bars[4].timestamp)
        bars.append(delayed)
        history[5] = deepcopy(history[4])
    assert not any(row['reason'] == BREAKOUT_GAP_BEARISH_CLEAR for row in risks(bars, history).values())


def test_breakout_gap_clear_prefix_and_stream_use_the_same_frozen_key():
    bars, history = sample(response=True)
    stream = TrendFlipExitStream(bars)
    try:
        for index in range(len(bars)):
            assert stream.advance(index, history) == trend_flip_exit_history(bars[:index + 1], history)
        changed = deepcopy(history)
        for row in changed.values():
            row[2][0]['value'] = 11
        assert stream.advance(len(bars) - 1, changed) == trend_flip_exit_history(bars, changed)
        assert not stream.advance(len(bars) - 1, changed)
    finally:
        stream.close()


@pytest.mark.parametrize('response', [False, True])
def test_account_clears_every_share_at_the_trigger_close(monkeypatch, response):
    from wavequant.domain.strategies import hierarchical_entry

    bars, history = sample(response=response)
    # 用足够成交量避免成交参与率影响规则；保留量比。
    bars = [replace(bar, volume=bar.volume * 1_000_000) for bar in bars]
    monkeypatch.setattr(hierarchical_entry, 'hierarchical_history', lambda *args, **kwargs: (history, {}))
    signal = Signal(bars[2].timestamp, bars[2].symbol, 2, 'LONG', bars[2].close,
                    1.0, 'fixture', bars[2].timestamp, 0, None, 'fixture', 20)
    config = StrategyConfig(trend_flip_adverse_exit=True, entry_at_close=True, exit_on_target=False,
        initial_capital=100_000, max_position_weight=1, risk_fraction=1, max_participation=1,
        max_hold_bars=100, commission_bps_per_side=0, slippage_bps_per_side=0,
        minimum_commission=0, a_share_taxes=False, net_reward_risk_filter=False)
    result = run_portfolio({bars[0].symbol: bars}, [signal], config)
    filled = [row for row in result.orders if row['status'] == 'filled']
    assert len(filled) == 2
    buy, sell = filled
    assert sell['reason'] == BREAKOUT_GAP_BEARISH_CLEAR
    assert sell['quantity'] == buy['quantity'] and sell['remaining_quantity'] == 0
    assert sell['price'] == bars[-1].close
    assert sell['execution_timestamp'].endswith('15:00:00')


def test_xinhua_frozen_quotes_clear_on_attack_when_the_old_high_is_already_known():
    # 行情来自冻结实股；旧 H 的可知性明确作为输入，不能冒充完整趋势管线验收。
    fixture = json.loads((Path(__file__).parent / 'fixtures/xinhua_2024_alternation.json').read_text())
    wanted = {'2022-01-05', '2023-02-14', '2023-02-15', '2023-02-16', '2023-02-17'}
    bars = [Bar(datetime.fromisoformat(row[0]), 'sh.600825', *row[1:7])
            for row in fixture['bars'] if row[0][:10] in wanted]
    high = dict(kind='H', index=0, value=bars[0].high, available_at=1, state='confirmed')
    history = {index: {2: [high]} for index in range(1, len(bars))}
    proof = risks(bars, history)[3]
    assert proof['breakout_key_date'] == '2022-01-05'
    assert proof['breakout_attack_date'] == '2023-02-16'
    assert proof['reason'] == BREAKOUT_GAP_BEARISH_CLEAR
    assert proof['exit_fraction'] == 1.0
    assert bars[4].open < bars[3].low
    assert bars[4].volume < bars[3].volume
    assert is_volume_bearish(bars[3], bars[2], None)
    # 2/16 自身是阴线，因此 2/17 的昨日和最近阴线均指向它。
    assert bars[3].close < bars[3].open
    assert not is_volume_bearish(bars[4], bars[3], bars[3])



@pytest.mark.parametrize('level', [0, 1, 2, 3])
@pytest.mark.parametrize('reference,previous_volume,bearish_volume,current_volume,expected', [
    ('previous', 200, 300, 250, True),
    ('last_bearish', 200, 100, 150, True),
    ('neither', 200, 160, 150, False),
    ('equal', 150, 150, 150, False),
])
def test_response_volume_compares_with_yesterday_or_last_bearish_not_pre_attack(
    level, reference, previous_volume, bearish_volume, current_volume, expected,
):
    bars, history = sample(level=level, response=True)
    bars[1] = replace(bars[1], open=9.6, close=9.3, volume=bearish_volume)
    # 突破前量 100 不作为固定基准；次笔必须使用其自己的昨日/最近阴线。
    bars[2] = replace(bars[2], volume=100)
    bars[3] = replace(bars[3], volume=previous_volume)
    bars[4] = replace(bars[4], volume=current_volume)
    found = risks(bars, history).get(4)
    assert (found is not None) is expected, reference
    if expected:
        assert found['reason'] == BREAKOUT_GAP_BEARISH_CLEAR
        assert found['previous_volume'] == previous_volume
        assert found['bearish_reference_volume'] == bearish_volume
        assert found['exit_fraction'] == 1.0


def test_bearish_attack_becomes_the_response_volume_reference():
    bars, history = sample(response=True)
    # 当笔为小阴线，既不全清也不触发长上影减仓，但应更新最近阴线量。
    bars[3] = replace(bars[3], open=10.5, high=10.6, close=10.2, volume=200)
    bars[4] = replace(bars[4], volume=150)
    found = risks(bars, history)
    assert 3 not in found and 4 not in found
