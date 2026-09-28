"""A close through yesterday's low with volume above the last bearish day clears."""

import json
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

from wavequant.application.analytics.backtest import run_portfolio
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals
from wavequant.domain.strategies.staged_exit import StagedExitState, observe_volume_down_exit
from wavequant.domain.strategies.strategy_profiles import whole_wave_profile


def guofang_bars():
    raw = json.loads((Path(__file__).parent / 'fixtures/guofang_2026_consolidation.json').read_text(encoding='utf-8'))
    bars = [Bar(datetime.fromisoformat(day), raw['symbol'], *values) for day, *values in raw['bars']]
    dates = {bar.timestamp.date().isoformat(): index for index, bar in enumerate(bars)}
    return bars, dates


def observe(bars, index):
    return observe_volume_down_exit(bars, index, StagedExitState())


def test_guofang_august_close_break_compares_the_previous_bearish_day():
    bars, dates = guofang_bars()
    index = dates['2026-08-13']
    previous, last_bearish = bars[index - 1], bars[dates['2026-08-06']]
    assert all(bars[j].close >= bars[j].open for j in range(dates['2026-08-07'], index))
    assert bars[index].close < previous.close and bars[index].close < previous.low
    assert bars[index].volume > last_bearish.volume
    decision = observe(bars[:index + 1], index)
    assert decision['reason'] == 'volume_previous_bearish_low_break_clear'
    assert decision['exit_fraction'] == 1.0
    assert decision['bearish_reference_date'] == '2026-08-06'
    assert decision['bearish_reference_volume'] == 12887189
    assert decision == observe(bars, index)
    shifted = [replace(bar, symbol='example', timestamp=bar.timestamp + timedelta(days=365))
               for bar in bars[:index + 1]]
    assert observe(shifted, index)['reason'] == decision['reason']


def test_previous_bearish_clear_requires_strict_close_and_volume_breaks():
    bars, dates = guofang_bars()
    index = dates['2026-08-13']
    cases = (
        {'close': bars[index - 1].low},
        {'volume': bars[dates['2026-08-06']].volume},
    )
    for changes in cases:
        changed = list(bars[:index + 1])
        changed[index] = replace(changed[index], **changes)
        assert (observe(changed, index) or {}).get('reason') != 'volume_previous_bearish_low_break_clear'
    changed = list(bars[:index + 1])
    changed[dates['2026-08-06']] = replace(changed[dates['2026-08-06']], close=bars[dates['2026-08-06']].open)
    assert observe(changed, index)['bearish_reference_date'] == '2026-08-05'


def test_august_10_buy_is_preserved_and_august_13_holding_is_fully_cleared():
    bars, dates = guofang_bars()
    bars = bars[:dates['2026-08-13'] + 1]
    profile = whole_wave_profile({'scenarios': {'base': {'execution': {}}}})
    assert 'previous_bearish_volume_previous_low_close_break_clear' in profile['definition']['exits']
    signals = generate_system_signals(bars, SystemStrategy(**profile['strategy'])).signals
    execution = replace(StrategyConfig(**profile['scenarios']['base']['execution']),
                        net_reward_risk_filter=False, staged_exit_intraday=False)
    result = run_portfolio({bars[0].symbol: bars}, signals, execution)
    orders = [order for order in result.orders if '2026-08-10' <= order['timestamp'][:10] <= '2026-08-13']
    assert [(order['timestamp'][:10], order['side'], order['status'], order['reason']) for order in orders] == [
        ('2026-08-10', 'BUY', 'filled', 'system_wave_push_gap'),
        ('2026-08-13', 'SELL', 'filled', 'volume_previous_bearish_low_break_clear'),
    ]
    assert orders[-1]['remaining_quantity'] == 0
