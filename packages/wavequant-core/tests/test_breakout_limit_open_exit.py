"""用户两日真实报价；中间行情和高点可知性为显式合成输入，不代表完整实股回放。"""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime

import pytest

from wavequant.application.analytics.backtest import run_portfolio
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar, Signal
from wavequant.domain.strategies.breakout_shadow_exit import (
    BREAKOUT_GAP_BEARISH_CLEAR, BREAKOUT_LIMIT_OPEN_REDUCE,
    BREAKOUT_SHADOW_CLEAR, breakout_shadow_days,
)
from wavequant.domain.strategies.trend_flip_exit import TrendFlipExitStream, trend_flip_exit_history


def sample(level: int = 1) -> tuple[list[Bar], dict[int, dict[int, list[dict[str, object]]]]]:
    rows = [
        ('2022-02-24', 3.93, 4.22, 3.90, 4.07, 53_669_707),
        ('2022-02-25', 4.00, 4.10, 3.95, 4.05, 200_000_000),
        ('2022-03-17', 4.05, 4.20, 4.00, 4.10, 200_000_000),
        ('2022-03-18', 4.20, 4.45, 4.15, 4.45, 200_000_000),
        ('2022-03-21', 5.34, 5.34, 4.78, 5.07, 194_599_622),
    ]
    bars = [Bar(datetime.fromisoformat(day), 'sz.300163', opened, high, low, close, volume)
            for day, opened, high, low, close, volume in rows]
    high: dict[str, object] = dict(kind='H', index=0, value=4.22, available_at=1, state='confirmed')
    history = {index: {level: [dict(high)]} for index in range(1, len(bars))}
    return bars, history


def risks(bars: list[Bar], history: dict[int, dict[int, list[dict[str, object]]]]) -> dict[int, dict[str, object]]:
    result: dict[int, dict[str, object]] = {}
    for _, result in breakout_shadow_days(bars, history):
        pass
    return result


@pytest.mark.parametrize('level', [0, 1, 2, 3])
def test_supplied_no_shadow_gap_bearish_reduces_against_frozen_high_volume(level: int) -> None:
    bars, history = sample(level)
    proof = risks(bars, history)[4]
    assert proof['reason'] == BREAKOUT_LIMIT_OPEN_REDUCE
    assert proof['exit_fraction'] == proof['exit_target_fraction'] == .9
    assert proof['execution_model'] == 'same_day_close'
    assert proof['decision_timestamp'] == '2022-03-21T15:00:00'
    assert proof['breakout_key_date'] == '2022-02-24'
    assert proof['breakout_key_high'] == 4.22
    assert proof['breakout_resistance_phase'] == 'response'
    assert proof['breakout_double_volume_source'] == 'breakout_key'
    assert proof['breakout_double_volume_reference'] == 53_669_707
    assert proof['breakout_double_volume_ratio'] == pytest.approx(194_599_622 / 53_669_707)
    assert proof['breakout_limit_up_raw'] == 5.34
    assert proof['breakout_upper_shadow'] == 0
    assert bars[4].close > bars[3].close and bars[4].close < bars[4].open


@pytest.mark.parametrize('case', ['below_double', 'no_limit_touch', 'sealed', 'doji',
                                  'future_key', 'developing_key', 'no_key', 'st', 'inactive',
                                  'factor_change', 'late_response'])
def test_rule_keeps_volume_limit_and_causal_boundaries(case: str) -> None:
    bars, history = sample()
    if case == 'below_double':
        bars[4] = replace(bars[4], volume=2 * bars[0].volume - 1)
    elif case == 'no_limit_touch':
        bars[4] = replace(bars[4], open=5.33, high=5.33)
    elif case == 'sealed':
        bars[4] = replace(bars[4], close=5.34)
    elif case == 'doji':
        bars[4] = replace(bars[4], open=5.07)
    elif case == 'future_key':
        for row in history.values():
            row[1][0]['available_at'] = 3
    elif case == 'developing_key':
        for row in history.values():
            row[1][0]['state'] = 'developing'
    elif case == 'no_key':
        history = {}
    elif case == 'st':
        bars[4] = replace(bars[4], raw_is_st=True)
    elif case == 'inactive':
        bars[4] = replace(bars[4], raw_trading_active=False)
    elif case == 'factor_change':
        bars[4] = replace(bars[4], adjustment_factor=2)
    else:
        bars.insert(4, replace(bars[3], timestamp=datetime(2022, 3, 19)))
        history[5] = deepcopy(history[4])
    assert not any(proof['reason'] == BREAKOUT_LIMIT_OPEN_REDUCE for proof in risks(bars, history).values())


def test_exact_double_and_adjusted_prices_keep_raw_limit_evidence() -> None:
    bars, history = sample()
    bars[4] = replace(bars[4], volume=2 * bars[0].volume)
    bars = [replace(bar, open=bar.open * 2, high=bar.high * 2, low=bar.low * 2,
                    close=bar.close * 2, adjustment_factor=2) for bar in bars]
    for row in history.values():
        row[1][0]['value'] = 8.44
    proof = risks(bars, history)[4]
    assert proof['reason'] == BREAKOUT_LIMIT_OPEN_REDUCE
    assert proof['breakout_double_volume_ratio'] == 2
    assert proof['breakout_limit_up_raw'] == 5.34


@pytest.mark.parametrize('source', ['previous', 'last_bearish'])
def test_other_known_volume_references_are_available(source: str) -> None:
    bars, history = sample()
    bars[0] = replace(bars[0], volume=200_000_000)
    reference = 3 if source == 'previous' else 1
    bars[reference] = replace(bars[reference], volume=90_000_000,
                              open=4.08 if source == 'last_bearish' else bars[reference].open)
    proof = risks(bars, history)[4]
    assert proof['reason'] == BREAKOUT_LIMIT_OPEN_REDUCE
    assert proof['breakout_double_volume_source'] == source


def test_attack_day_and_full_clear_precedence() -> None:
    bars, history = sample()
    # 合成更高关键位，使3/21成为突破当笔。
    bars[0] = replace(bars[0], high=4.50)
    for row in history.values():
        row[1][0]['value'] = 4.50
    assert risks(bars, history)[4]['breakout_resistance_phase'] == 'attack'
    bars[3] = replace(bars[3], volume=90_000_000)
    bars[4] = replace(bars[4], low=4.80)
    proof = risks(bars, history)[4]
    assert proof['reason'] == BREAKOUT_GAP_BEARISH_CLEAR
    assert proof['exit_fraction'] == 1


def test_prefix_stream_and_next_open_use_only_known_evidence() -> None:
    bars, history = sample()
    bars.append(replace(bars[-1], timestamp=datetime(2022, 3, 22), open=5.00,
                        high=5.80, low=4.90, close=5.70))
    history[5] = deepcopy(history[4])
    stream = TrendFlipExitStream(bars)
    try:
        for index in range(len(bars)):
            assert stream.advance(index, history) == trend_flip_exit_history(bars[:index + 1], history)
    finally:
        stream.close()
    proof = risks(bars, history)[5]
    assert proof['reason'] == BREAKOUT_SHADOW_CLEAR
    assert proof['execution_model'] == 'next_open'
    assert not {'observed_high', 'observed_low', 'observed_close', 'observed_volume'} & proof.keys()


@pytest.mark.parametrize('capital', [4_510, 45_510])
def test_account_rounds_up_to_at_least_ninety_percent(monkeypatch: pytest.MonkeyPatch, capital: int) -> None:
    from wavequant.domain.strategies import hierarchical_entry

    bars, history = sample()
    monkeypatch.setattr(hierarchical_entry, 'hierarchical_history', lambda *args, **kwargs: (history, {}))
    signal = Signal(bars[2].timestamp, bars[2].symbol, 2, 'LONG', bars[2].close,
                    1.0, 'fixture', bars[2].timestamp, 0, None, 'fixture', 20)
    config = StrategyConfig(trend_flip_adverse_exit=True, entry_at_close=True, exit_on_target=False,
        initial_capital=capital, max_position_weight=1, risk_fraction=1, max_participation=1,
        max_hold_bars=100, commission_bps_per_side=0, slippage_bps_per_side=0,
        minimum_commission=0, a_share_taxes=False, net_reward_risk_filter=False)
    result = run_portfolio({bars[0].symbol: bars}, [signal], config)
    filled = [row for row in result.orders if row['status'] == 'filled']
    buy, sell = filled
    assert sell['reason'] == BREAKOUT_LIMIT_OPEN_REDUCE
    assert buy['quantity'] * .9 <= sell['quantity'] <= buy['quantity']
    assert sell['remaining_quantity'] <= buy['quantity'] * .1
    assert sell['price'] == 5.07
    assert sell['execution_timestamp'] == '2022-03-21T15:00:00'
