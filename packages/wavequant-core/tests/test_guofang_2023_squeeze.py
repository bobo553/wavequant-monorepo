"""Real-market regression for the March 2023 Guofang N and squeeze sequence."""

import json
from datetime import datetime, timedelta
from pathlib import Path

from wavequant.application.analytics.backtest import run_portfolio
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar, Signal
from wavequant.domain.strategies.hierarchical_entry import hierarchical_history
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals
from wavequant.domain.strategies.trend_flip_exit import trend_flip_exit_history


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


def test_level_one_breakout_lower_close_clears_held_position():
    bars, dates = fixture()
    attack = dates['2023-03-21']
    risks = trend_flip_exit_history(
        bars, hierarchical_history(bars)[0], positive_n_attacks=(attack,))
    clear = risks[dates['2023-03-22']]
    assert clear['reason'] == 'trend_last_fall_high_lower_close_clear'
    assert clear['trend_level'] == 1
    assert clear['trend_key_date'] == '2023-03-02'
    signal = Signal(bars[attack].timestamp, bars[0].symbol, attack, 'LONG',
                    bars[attack].close, bars[dates['2023-03-14']].low,
                    'fixture', bars[attack].timestamp, 0, None, 'fixture', 10)
    config = StrategyConfig(entry_at_close=True, trend_flip_adverse_exit=True,
                            exit_on_target=False, risk_fraction=0.1,
                            max_position_weight=0.8, max_participation=1)
    result = run_portfolio({bars[0].symbol: bars}, [signal], config,
                           positive_n_bars={bars[0].symbol: {attack: attack}})
    fills = [order for order in result.orders if order['status'] == 'filled']
    assert [(order['side'], order['timestamp'][:10]) for order in fills] == [
        ('BUY', '2023-03-21'), ('SELL', '2023-03-22')]
    assert fills[1]['reason'] == 'trend_last_fall_high_lower_close_clear'
    assert fills[1]['remaining_quantity'] == 0


def test_adverse_candle_reduces_then_first_lower_close_clears():
    rows = [(9.7, 10, 9.6, 9.8), (9.8, 9.9, 9.7, 9.9),
            (9.95, 10.4, 9.9, 10), (10.2, 10.3, 10, 10.1),
            (10.1, 10.2, 9.9, 10)]
    bars = [Bar(datetime(2023, 3, 17) + timedelta(days=index), 'TEST', *row, 1000)
            for index, row in enumerate(rows)]
    high = dict(index=0, kind='H', value=10, available_at=0)
    history = {index: {1: [high]} for index in range(len(bars))}
    risks = trend_flip_exit_history(bars, history, positive_n_attacks=(2,))
    assert risks[3]['reason'] == 'trend_last_fall_high_adverse_reduce'
    assert risks[3]['exit_fraction'] == 0.8
    assert {'higher_open_bearish_body', 'bearish_body'} <= set(risks[3]['trend_adverse_patterns'])
    assert risks[4]['reason'] == 'trend_last_fall_high_lower_close_clear'
    assert risks[4]['trend_warning_index'] == 3
