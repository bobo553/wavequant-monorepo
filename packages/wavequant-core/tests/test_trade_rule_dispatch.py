"""Entry eligibility cannot silence a later strategy risk exit."""

from unittest.mock import Mock

from wavequant.domain.strategies.backtest_analysis import BacktestAnalysis
from wavequant.domain.strategies.eligible_polyline_n import EligibleNSource
from wavequant.domain.strategies.integrated_strategy import (
    _generate_system_signals,
    generate_system_signals,
    pivot_history,
)

from .test_integrated_strategy import config, fixture


def prepared_history(*, buy_days=None, scan_days=None, blocked_day=None):
    bars = fixture()
    pivots = pivot_history(bars, config())
    sources = []
    for known_at, points in pivots[0].items():
        for offset in range(len(points) - 2):
            vertices = (points[offset], points[offset + 1], points[offset + 2])
            sources.append(EligibleNSource(vertices, known_at, 'fixture', 0,
                                           vertices[0].point.index, vertices[0].point.ordinal))
    if blocked_day is not None:
        pivots[3].add(blocked_day)
    return Mock(
        spec=BacktestAnalysis,
        pivots=pivots,
        trading_intervals=(),
        buy_days=buy_days if buy_days is not None else (True,) * len(bars),
        scan_days=scan_days if scan_days is not None else (True,) * len(bars),
        n_sources=sources,
        n_observations={},
        regimes={},
        projections={},
    )


def test_open_window_preserves_existing_entry_and_exit_signals():
    expected = generate_system_signals(fixture(), config())
    actual = _generate_system_signals(fixture(), config(), _analysis=prepared_history())
    assert actual.signals == expected.signals
    assert any(signal.side == 'LONG' for signal in actual.signals)


def test_entry_permission_gates_buy_without_consuming_candidate():
    buy_days = tuple(i != 9 for i in range(len(fixture())))
    result = _generate_system_signals(fixture(), config(),
                                     _analysis=prepared_history(buy_days=buy_days))
    assert not any(signal.side == 'LONG' and signal.bar_index == 9 for signal in result.signals)
    assert result.counts['buy_interval_skipped_days'] >= 1


def test_closed_window_still_emits_exit_after_an_entry_signal():
    active = tuple(i <= 9 for i in range(len(fixture())))
    result = _generate_system_signals(fixture(), config(), _analysis=prepared_history(
        buy_days=active, scan_days=active, blocked_day=11))
    assert any(signal.side == 'LONG' and signal.bar_index == 9 for signal in result.signals)
    assert any(signal.side == 'EXIT' and signal.bar_index == 11 for signal in result.signals)
    assert not any(signal.side == 'LONG' and signal.bar_index > 9 for signal in result.signals)


def test_exit_wins_when_entry_and_exit_are_available_on_same_day():
    result = _generate_system_signals(fixture(), config(),
                                     _analysis=prepared_history(blocked_day=9))
    assert any(signal.side == 'EXIT' and signal.bar_index == 9 for signal in result.signals)
    assert not any(signal.side == 'LONG' and signal.bar_index == 9 for signal in result.signals)


def test_no_eligible_window_skips_signals_before_any_possible_position():
    inactive = (False,) * len(fixture())
    result = _generate_system_signals(fixture(), config(), _analysis=prepared_history(
        buy_days=inactive, scan_days=inactive, blocked_day=9))
    assert result.signals == []
    assert result.counts['interval_skipped_days'] == len(fixture())
