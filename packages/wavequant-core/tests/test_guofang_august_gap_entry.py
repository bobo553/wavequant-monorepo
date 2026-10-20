"""冻结完整同源前缀验证最后折线门槛；账户子例保留真实涨停权限。"""

from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path

import pytest

from wavequant.application.analytics.backtest import run_portfolio
from wavequant.application.analytics.intraday_entry import _qualified_rising_close_entries
from wavequant.application.analytics.trade_evidence import enrich_ledger
from wavequant.domain.market_structure.polyline_lifecycle import PolylineBuyGate
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.integrated_strategy import SystemResult, SystemStrategy, pivot_history
from wavequant.domain.strategies.trading_intervals import confirmed_trading_intervals


@pytest.fixture(scope='module')
def frozen_market():
    raw = json.loads((Path(__file__).parent / 'fixtures/guofang_2026_august_gap.json').read_text('utf-8'))
    bars = [Bar(**dict(row, timestamp=datetime.fromisoformat(row['timestamp']))) for row in raw['bars']]
    strategy = SystemStrategy(**raw['strategy'])
    dates = {bar.timestamp.date().isoformat(): index for index, bar in enumerate(bars)}
    pivots = pivot_history(bars, strategy)
    gate = PolylineBuyGate()
    for index, bar in enumerate(bars):
        gate.advance(index, bar, pivots[0][index], pivots[1][index])
    generated = SystemResult([], [raw['qualification']], {})
    windows = confirmed_trading_intervals(generated.audit, len(bars), strategy.buy_interval_level)
    resolved, _ = _qualified_rising_close_entries(bars, generated, strategy, only_index=len(bars)-1,
        intervals=windows, polyline_gate=gate)
    return bars, dates, gate, resolved, StrategyConfig(**raw['execution'])


def test_last_fall_high_is_june24_and_old_cycle_is_not_accumulated(frozen_market) -> None:
    bars, dates, gate, resolved, _ = frozen_market
    key, known = dates['2026-06-24'], dates['2026-06-30']
    assert gate.restrictions[known]['polyline_key_index'] == key
    assert gate.restrictions[known]['polyline_key_price'] == bars[key].high
    assert bars[key].high == pytest.approx(10.239033074495085)
    assert any(row['polyline_key_index'] == key for row in gate.breakouts.values())
    assert dates['2026-08-28'] not in gate.restrictions
    assert len(resolved.signals) == 1
    assert resolved.signals[0].reason == 'qualified_double_volume_breakout_close'
    assert resolved.signals[0].rvol == pytest.approx(2.21073837952044)


def test_broken_qualification_high_is_evidence_not_a_profit_target(frozen_market) -> None:
    bars, _, _, resolved, config = frozen_market
    signal = replace(resolved.signals[0], bar_index=1)
    proof = next(row for row in resolved.audit if row['event'] == 'long_transition_evidence')
    assert proof['qualification_breakout_level'] < signal.reference_price
    assert proof['target_policy'] == 'account_r_multiple'
    assert signal.target_price is None
    # 独立账户子例不代表全历史账户，真实完整回放另留诊断证据。
    result = run_portfolio({bars[-1].symbol: bars[-2:]}, [signal], config, minute_loader=lambda _: [])
    buy = next(row for row in result.orders if row['side'] == 'BUY')
    assert buy['status'] == 'filled'
    assert buy['execution_timestamp'] == '2026-08-28T15:00:00'
    assert buy['price'] == bars[-1].close
    assert buy['target_price'] == pytest.approx(
        buy['price'] + config.take_profit_r * (buy['price'] - signal.invalidation_price))
    assert buy['fill_assumption'] == 'nonflat_limit_close_without_queue_verification'
    assert bars[-1].close_buyable is False and bars[-1].nonflat_close_buyable is True
    enrich_ledger(bars, result, resolved, dict(entry_policy='hierarchical_two_buy_points',
        buy_point_definition='whole_flip_wave_v3'))
    assert all(row['passed'] is not False for row in buy['entry_conditions'])
    assert buy['entry_conditions'][1]['name'] == '收盘量价确认'


@pytest.mark.parametrize('guard', ('limit_permission', 'net_reward_risk'))
def test_real_close_entry_does_not_bypass_account_guards(frozen_market, guard: str) -> None:
    bars, _, _, resolved, config = frozen_market
    signal = replace(resolved.signals[0], bar_index=1)
    if guard == 'limit_permission':
        config = replace(config, nonflat_limit_close_fill=False)
        expected = 'not_buyable_at_close'
    else:
        signal = replace(signal, minimum_reward_risk=3)
        config = replace(config, net_reward_risk_filter=True)
        expected = 'insufficient_net_reward_risk'
    result = run_portfolio({bars[-1].symbol: bars[-2:]}, [signal], config, minute_loader=lambda _: [])
    buy = next(row for row in result.orders if row['side'] == 'BUY')
    assert buy['status'] == 'cancelled' and buy['reason'] == expected


def test_other_strategy_cannot_omit_required_structural_target(frozen_market) -> None:
    bars, _, _, resolved, config = frozen_market
    signal = replace(resolved.signals[0], bar_index=1, reason='unrelated_structural_entry')
    with pytest.raises(ValueError, match='invalid signal target/reward-risk'):
        run_portfolio({bars[-1].symbol: bars[-2:]}, [signal], config, minute_loader=lambda _: [])
