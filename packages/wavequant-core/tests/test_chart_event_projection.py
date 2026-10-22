"""内部审计保留交易用途，不再重复投影为图表规则圆点。"""

from copy import deepcopy

from wavequant.domain.strategies.integrated_strategy import SystemResult, SystemStrategy
from wavequant.interfaces.charts.visualization import ChartRepository
from tests.test_chart_target_windows import market, positive_n


def test_chart_events_skip_internal_audit_before_marker_field_conversion() -> None:
    bars = market()
    internal = [dict(event=event, bar_index=2, trend_level=level, timestamp=None)
                for level in (0, 1, 2, 3) for event in (
                    'trend_lifecycle_state', 'trend_lifecycle_anchor', 'trend_lifecycle_suspicion',
                    'trend_lifecycle_candidate', 'trend_lifecycle_candidate_expired',
                    'trend_lifecycle_invalidated', 'future_internal_event')]
    kept = [dict(event=event, bar_index=4, timestamp=bars[4].timestamp.isoformat()) for event in (
        'entry_rejected', 'long_signal', 'exit_signal', 'a_wave_confirmed', 'b_wave_updated',
        'c_wave_started', 'n_target_source_retired', 'trading_interval_opened', 'trading_interval_closed')]
    n = positive_n(bars)
    audit = [*internal, n, *kept]
    before = deepcopy(audit)
    theory = ChartRepository.render_theory(
        None, bars, SystemStrategy(), SystemResult([], audit, {}),
        bars[-1].timestamp.date().isoformat(), geometry={'tertiary_trends': {}},
    )
    assert [row['event'] for row in theory['events']] == ['n_completed', *(row['event'] for row in kept)]
    target = theory['events'][0]
    assert target['id'] == f"n:{n['n_id']}"
    assert len(target['shape']) == 4
    assert {row['stage'] for row in target['levels'] if 'stage' in row} == {'one_p', 'two_t'}
    assert audit == before
