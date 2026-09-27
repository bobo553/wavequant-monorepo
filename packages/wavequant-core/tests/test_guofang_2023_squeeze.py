"""Real-market regression for the March 2023 Guofang N and squeeze sequence."""

import json
from datetime import datetime
from pathlib import Path

from wavequant.application.analytics.backtest import run_portfolio
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals


def fixture():
    raw = json.loads((Path(__file__).parent / 'fixtures/guofang_2023_squeeze.json').read_text(encoding='utf-8'))
    bars = [Bar(datetime.fromisoformat(day), raw['symbol'], *values) for day, *values in raw['bars']]
    dates = {bar.timestamp.date().isoformat(): index for index, bar in enumerate(bars)}
    return bars, dates


def strategy():
    return SystemStrategy(
        pivot_mode='lecture_causal', entry_policy='hierarchical_two_buy_points',
        buy_point_definition='whole_flip_wave_v3', preflight_reward_risk=False,
        strict_n_attack_quality=False, first_pullback_threshold=None,
        mature_shallow_ratio=1 / 3, minimum_rvol=1.0,
    )


def test_march_17_n_march_21_squeeze_and_no_march_23_rebuy():
    bars, dates = fixture()
    result = generate_system_signals(bars, strategy())
    events = {day: [row for row in result.audit if row['bar_index'] == dates[day]]
              for day in ('2023-03-17', '2023-03-21', '2023-03-23')}
    assert any(row['event'] == 'n_completed' and row['direction'] == 'up'
               for row in events['2023-03-17'])
    squeeze = next(row for row in events['2023-03-21']
                   if row['event'] == 'hierarchical_n_squeeze_confirmed')
    assert squeeze['trend_key_date'] == '2023-03-02'
    assert squeeze['trend_pullback_date'] == '2023-03-14'
    assert squeeze['trend_key_high'] == bars[dates['2023-03-02']].high
    longs = [signal for signal in result.signals if signal.side == 'LONG'
             and dates['2023-03-17'] <= signal.bar_index <= dates['2023-03-23']]
    assert [(signal.bar_index, signal.reason) for signal in longs] == [
        (dates['2023-03-21'], 'system_transition_squeeze')]
    assert next(row for row in events['2023-03-21'] if row['event'] == 'long_signal')[
        'squeeze_confirmation'] == 'hierarchical_n_last_fall_high_squeeze'
    prefix = generate_system_signals(bars[:dates['2023-03-21'] + 1], strategy())
    assert [signal for signal in prefix.signals if signal.side == 'LONG'
            and signal.bar_index == dates['2023-03-21']] == longs


def test_march_21_squeeze_signal_still_obeys_execution_reward_risk():
    bars, dates = fixture()
    signal = next(signal for signal in generate_system_signals(bars, strategy()).signals
                  if signal.side == 'LONG' and signal.bar_index == dates['2023-03-21'])
    config = StrategyConfig(entry_at_close=True, risk_fraction=0.1,
                            max_position_weight=0.8, max_participation=1)
    result = run_portfolio({bars[0].symbol: bars}, [signal], config)
    buy = next(order for order in result.orders if order['side'] == 'BUY')
    assert buy['timestamp'][:10] == '2023-03-21'
    assert buy['status'] == 'cancelled'
    assert buy['reason'] == 'insufficient_net_reward_risk'
    assert buy['net_reward_risk'] < buy['required_reward_risk']


def test_march_21_squeeze_buy_fills_when_reward_risk_filter_is_off():
    bars, dates = fixture()
    signals = [signal for signal in generate_system_signals(bars, strategy()).signals
               if signal.side == 'LONG' and dates['2023-03-17'] <= signal.bar_index <= dates['2023-03-23']]
    config = StrategyConfig(entry_at_close=True, trend_flip_adverse_exit=True,
                            exit_on_target=False, risk_fraction=0.1, max_position_weight=0.8,
                            max_participation=1, net_reward_risk_filter=False)
    result = run_portfolio({bars[0].symbol: bars}, signals, config)
    buys = [order for order in result.orders if order['side'] == 'BUY']
    assert [(order['timestamp'][:10], order['status']) for order in buys] == [('2023-03-21', 'filled')]
    assert buys[0]['net_reward_risk_filter'] is False
    assert buys[0]['net_reward_risk'] < buys[0]['required_reward_risk']
