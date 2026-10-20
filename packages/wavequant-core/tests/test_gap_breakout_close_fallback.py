"""用户当日量价配合合成前日、资格和分钟，检验通道选择而非实股历史成交。"""

from dataclasses import replace
from datetime import datetime

import pytest

from tests.test_qualified_gap_entry import sample
from wavequant.application.analytics.intraday_entry import resolve_consolidation_entries
from wavequant.domain.market_state.candle_strength import QUALIFIED_VOLUME_BREAKOUT_CLOSE
from wavequant.domain.market_structure.polyline_lifecycle import PolylineBuyGate
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.backtest_analysis import BacktestAnalysis
from wavequant.domain.strategies.chart_drawing_history import ChartDrawingHistory
from wavequant.domain.strategies.trading_intervals import TradingInterval
from wavequant.infrastructure.market_data.minute import MinuteBar


def scenario(level: int = 2):
    bars, generated, strategy = sample(level)
    bars[:] = [replace(bar, timestamp=datetime(2026, 8, 24 + index), symbol='sh.601086')
               for index, bar in enumerate(bars)]
    # 前日与资格为合成输入，不能由用户提供的单日数据推定实际倍量和历史资格。
    bars[-2] = Bar(datetime(2026, 8, 27), 'sh.601086', 10.5, 10.6, 10.3, 10.56, 8_000_000)
    bars[-1] = Bar(datetime(2026, 8, 28), 'sh.601086', 10.78, 11.62, 10.75, 11.62, 17_997_179)
    window = TradingInterval(level, 'trend', 4, 1, start_low_index=2, start_low_price=9)
    gate = PolylineBuyGate()
    gate.index = 4
    return bars, generated, strategy, window, gate


def resolve_case(case, minute=None):
    bars, generated, strategy, window, gate = case
    with ChartDrawingHistory(start=0, count=len(bars), closed=()) as frames:
        analysis = BacktestAnalysis(tuple(bars), frames, (), ({}, {}, {}, set()),
            trading_intervals=(window,))
        return resolve_consolidation_entries(bars, generated, strategy, minute,
            daily_fallback=False, qualified_gap_only=True, only_index=4,
            prepared_polyline_gate=gate, trading_window_start=4, prepared_analysis=analysis)


def rising_minutes(bar: Bar) -> list[MinuteBar]:
    return [
        MinuteBar(bar.timestamp.replace(hour=9, minute=35), 10.78, 10.9, 10.75, 10.86, 100_000),
        MinuteBar(bar.timestamp.replace(hour=9, minute=40), 10.87, 11, 10.85, 10.95, 120_000),
    ]


@pytest.mark.parametrize('level', (1, 2, 3))
def test_gap_double_volume_without_minutes_keeps_close_not_open_simulation(level: int) -> None:
    case = scenario(level)
    result, executions, fallbacks = resolve_case(case)
    assert not executions and not fallbacks
    assert len(result.signals) == 1
    signal = result.signals[0]
    assert signal.reason == QUALIFIED_VOLUME_BREAKOUT_CLOSE
    assert signal.reference_price == 11.62
    assert signal.trigger_timestamp == datetime(2026, 8, 28, 15)


@pytest.mark.parametrize('level', (1, 2, 3))
def test_unfilled_gap_with_small_opening_pullback_prefers_observed_morning_breakout(level: int) -> None:
    case = scenario(level)
    result, executions, fallbacks = resolve_case(case, lambda bar: rising_minutes(bar))
    assert not fallbacks and len(result.signals) == 1
    entry = executions['sh.601086', 4]
    assert entry['price'] == 10.87
    assert entry['signal'] == result.signals[0]
    assert entry['timing']['observed_low'] == 10.75
    assert entry['timing']['decision_timestamp'].endswith('09:35:00')
    assert sum(row['event'] == 'long_signal' for row in result.audit) == 1
    assert all(row.get('execution_model') != 'same_day_close'
               for row in result.audit if row['event'] in ('long_signal', 'long_transition_evidence'))


@pytest.mark.parametrize('case_name', ('gap_filled', 'no_rise', 'no_next_minute'))
def test_no_morning_entry_retains_independent_close_breakout(case_name: str) -> None:
    case = scenario()
    minute = rising_minutes(case[0][-1])
    if case_name == 'gap_filled':
        minute[0] = replace(minute[0], low=10.6)
    elif case_name == 'no_rise':
        minute[0] = replace(minute[0], close=10.78)
    else:
        minute = minute[:1]
    result, executions, fallbacks = resolve_case(case, lambda _: minute)
    assert not executions and not fallbacks
    assert len(result.signals) == 1
    assert result.signals[0].reason == QUALIFIED_VOLUME_BREAKOUT_CLOSE
    assert result.signals[0].trigger_timestamp.hour == 15


def test_non_double_volume_does_not_gain_close_fallback() -> None:
    case = scenario()
    case[0][-1] = replace(case[0][-1], volume=15_999_999)
    minute = rising_minutes(case[0][-1])
    minute[0] = replace(minute[0], low=10.6)
    result, executions, _ = resolve_case(case, lambda _: minute)
    assert not result.signals and not executions


@pytest.mark.parametrize('blocked', ('closed', 'wrong_level', 'no_confirmation', 'polyline'))
def test_common_guards_still_reject_both_routes(blocked: str) -> None:
    bars, generated, strategy, window, gate = scenario()
    if blocked == 'closed':
        window = replace(window, end_index=4)
    elif blocked == 'wrong_level':
        window = replace(window, trend_level=1)
    elif blocked == 'no_confirmation':
        generated.audit.clear()
    else:
        gate.restrictions[4] = dict(reason='polyline_last_fall_high_not_broken', polyline_key_price=12)
    result, executions, _ = resolve_case((bars, generated, strategy, window, gate),
        lambda _: pytest.fail('rejected entries must not request minutes'))
    assert not result.signals and not executions


def test_first_daily_structural_release_cannot_be_backdated_to_morning() -> None:
    case = scenario()
    case[-1].breakouts[4] = dict(polyline_key_index=2, polyline_key_price=11.2,
        polyline_key_known_index=3, polyline_breakout_index=4, source_path='lecture-0')
    result, executions, _ = resolve_case(case,
        lambda _: pytest.fail('close-only structural evidence cannot authorize morning'))
    assert not executions
    assert result.signals[0].reason == QUALIFIED_VOLUME_BREAKOUT_CLOSE
    assert result.signals[0].trigger_timestamp.hour == 15
