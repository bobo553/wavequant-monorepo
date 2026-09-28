"""A third resisted break of a known falling high can clear an open holding."""

import json
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

from wavequant.application.analytics.backtest import run_portfolio
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.hierarchical_entry import hierarchical_history
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals
from wavequant.domain.strategies.strategy_profiles import whole_wave_profile
from wavequant.domain.strategies.trend_flip_exit import trend_flip_exit_history


def guofang_bars():
    raw = json.loads((Path(__file__).parent / 'fixtures/guofang_2026_consolidation.json').read_text(encoding='utf-8'))
    bars = [Bar(datetime.fromisoformat(day), raw['symbol'], *values) for day, *values in raw['bars']]
    dates = {bar.timestamp.date().isoformat(): index for index, bar in enumerate(bars)}
    return bars, dates


def risk_on(bars, index):
    return trend_flip_exit_history(bars, hierarchical_history(bars)[0]).get(index)


def test_guofang_third_resisted_break_and_bearish_outside_clears():
    bars, dates = guofang_bars()
    first, second, third = (dates[day] for day in ('2026-07-15', '2026-07-16', '2026-07-17'))
    key = bars[dates['2026-06-24']].high
    assert all(bars[i].high > key and bars[i].close < key for i in (first, second, third))
    assert bars[third].high > bars[second].high
    assert bars[third].close < bars[second].low
    assert bars[third].volume > bars[dates['2026-07-13']].volume
    assert risk_on(bars, second) is None
    decision = risk_on(bars, third)
    assert decision['reason'] == 'trend_last_fall_high_third_resistance_clear'
    assert decision['exit_fraction'] == 1.0
    assert decision['trend_key_date'] == '2026-06-24'
    assert decision['bearish_reference_date'] == '2026-07-13'
    assert decision['trend_resistance_dates'] == ['2026-07-15', '2026-07-16', '2026-07-17']
    assert decision == risk_on(bars[:third + 1], third)
    shifted = [replace(bar, symbol='example', timestamp=bar.timestamp + timedelta(days=365))
               for bar in bars[:third + 1]]
    assert risk_on(shifted, third)['reason'] == decision['reason']


def test_third_resistance_clear_requires_each_price_and_volume_condition():
    bars, dates = guofang_bars()
    first, second, third = (dates[day] for day in ('2026-07-15', '2026-07-16', '2026-07-17'))
    key = bars[dates['2026-06-24']].high
    reference_volume = bars[dates['2026-07-13']].volume
    cases = (
        (first, {'high': key}),
        (second, {'high': key}),
        (second, {'close': key + 0.01}),
        (third, {'high': bars[second].high}),
        (third, {'low': bars[second].low, 'close': bars[second].low}),
        (third, {'close': bars[second].low}),
        (third, {'volume': reference_volume}),
    )
    for index, changes in cases:
        changed = list(bars)
        changed[index] = replace(changed[index], **changes)
        assert (risk_on(changed[:third + 1], third) or {}).get('reason') != (
            'trend_last_fall_high_third_resistance_clear'), (index, changes)


def test_july_15_buy_is_preserved_and_july_17_holding_fully_clears():
    bars, dates = guofang_bars()
    bars = bars[:dates['2026-07-17'] + 1]
    profile = whole_wave_profile({'scenarios': {'base': {'execution': {}}}})
    assert 'last_fall_high_third_resistance_bearish_outside_volume_clear' in profile['definition']['exits']
    signals = generate_system_signals(bars, SystemStrategy(**profile['strategy'])).signals
    execution = replace(StrategyConfig(**profile['scenarios']['base']['execution']),
                        net_reward_risk_filter=False, staged_exit_intraday=False)
    result = run_portfolio({bars[0].symbol: bars}, signals, execution)
    orders = [order for order in result.orders if '2026-07-15' <= order['timestamp'][:10] <= '2026-07-17']
    assert [(order['timestamp'][:10], order['side'], order['status'], order['reason']) for order in orders] == [
        ('2026-07-15', 'BUY', 'filled', 'system_transition_squeeze'),
        ('2026-07-17', 'SELL', 'filled', 'trend_last_fall_high_third_resistance_clear'),
    ]
    assert orders[-1]['remaining_quantity'] == 0
