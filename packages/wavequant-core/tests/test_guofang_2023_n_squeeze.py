"""The March 2023 Guofang entry uses the March 17 N and its March 21 record close."""

import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from wavequant.application.analytics.backtest import run_portfolio
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals
from wavequant.domain.strategies.strategy_profiles import whole_wave_profile


def guofang_bars():
    path = Path(__file__).parent / 'fixtures/guofang_2023_n_squeeze.json'
    raw = json.loads(path.read_text(encoding='utf-8'))
    bars = [Bar(datetime.fromisoformat(day), raw['symbol'], *values) for day, *values in raw['bars']]
    dates = {bar.timestamp.date().isoformat(): index for index, bar in enumerate(bars)}
    return bars, dates


def v3_strategy():
    return SystemStrategy(
        pivot_mode='lecture_causal', entry_policy='hierarchical_two_buy_points',
        buy_point_definition='whole_flip_wave_v3', preflight_reward_risk=False,
        strict_n_attack_quality=False, first_pullback_threshold=None,
        mature_shallow_ratio=1 / 3, minimum_rvol=1.0,
    )


def test_march_17_n_confirms_squeeze_on_march_21_without_march_23_rebuy():
    bars, dates = guofang_bars()
    prior, confirmation = bars[dates['2023-03-20']], bars[dates['2023-03-21']]
    assert confirmation.open < prior.close
    assert confirmation.close > prior.high
    assert confirmation.volume > 2 * prior.volume
    result = generate_system_signals(bars, v3_strategy())
    attack = next(row for row in result.audit if row['event'] == 'n_completed'
                  and row['bar_index'] == dates['2023-03-17'] and row['direction'] == 'up')
    assert bars[attack['neckline']].timestamp.date().isoformat() == '2023-03-15'
    assert any(row['event'] == 'regime_confirmation' and row['attack'] == dates['2023-03-17']
               and row['bar_index'] == dates['2023-03-21'] for row in result.audit)
    longs = [signal for signal in result.signals if signal.side == 'LONG'
             and dates['2023-03-17'] <= signal.bar_index <= dates['2023-03-23']]
    assert [(signal.bar_index, signal.trigger_timestamp.date().isoformat()) for signal in longs] == [
        (dates['2023-03-21'], '2023-03-17')]
    prefix = generate_system_signals(bars[:dates['2023-03-21'] + 1], v3_strategy())
    assert [signal for signal in prefix.signals if signal.side == 'LONG'
            and signal.bar_index >= dates['2023-03-17']] == longs


def test_march_21_full_profile_fills_when_optional_net_reward_filter_is_off():
    bars, dates = guofang_bars()
    profile = whole_wave_profile({'scenarios': {'base': {'execution': {}}}})
    signals = generate_system_signals(bars, SystemStrategy(**profile['strategy'])).signals
    execution = replace(StrategyConfig(**profile['scenarios']['base']['execution']),
                        net_reward_risk_filter=False, staged_exit_intraday=False)
    result = run_portfolio({bars[0].symbol: bars}, signals, execution)
    buys = [order for order in result.orders if order['side'] == 'BUY']
    march_buys = [(order['timestamp'][:10], order['status']) for order in buys
                  if '2023-03-17' <= order['timestamp'][:10] <= '2023-03-23']
    assert march_buys == [('2023-03-21', 'filled')]
