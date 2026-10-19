"""Qualification ownership stays distinct while overlapping windows are open."""

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace

from wavequant.domain.models.model import Bar
from wavequant.domain.strategies import daily_strategy as module
from wavequant.domain.strategies.chart_drawing_history import ChartDrawingFrame
from wavequant.domain.strategies.integrated_strategy import SystemResult, SystemStrategy
from wavequant.domain.strategies.market_calculations import MarketDay
from wavequant.domain.strategies.trading_intervals import TradingInterval


def test_departing_overlap_archives_only_its_facts_and_reopening_gets_a_new_owner(monkeypatch):
    bars = [Bar(datetime(2024, 1, 1) + timedelta(days=index), 'TEST', 10, 12, 9, 11, 1000)
            for index in range(7)]
    intervals = (
        TradingInterval(2, 'A', 0, 0, end_index=2, end_reason='source_invalidated'),
        TradingInterval(2, 'B', 1, 1, end_index=4, end_reason='source_invalidated'),
        TradingInterval(2, 'A', 6, 6),
    )
    eligible_days = (True, True, True, True, False, False, True)
    snapshots = []
    generated = []

    def lifecycle(index):
        return dict(event='trend_lifecycle_state', bar_index=index, trend_level=2,
                    source_path='market', state='空翻多')

    @contextmanager
    def analysis_scope(market, config, *, on_day):
        for index in range(len(market)):
            known = tuple(replace(interval, end_index=None, end_reason=None)
                          if interval.end_index is not None and interval.end_index > index else interval
                          for interval in intervals if interval.start_index <= index)
            analysis = SimpleNamespace(bars=market[:index + 1], hierarchy=None,
                buy_days=eligible_days[:index + 1], trading_intervals=known)
            day = MarketDay(ChartDrawingFrame(index, 0, [], {'strokes': []}), (),
                            (lifecycle(index),), (False, eligible_days[index], False))
            on_day(day, analysis)
        yield

    def generate(market, config, *, _analysis, _daily, **kwargs):
        # The coordinator owns archival. This observer republishes only active
        # owners, as the real candidate filter and A-wave reducer do.
        index = len(market) - 1
        generated.append(index)
        rows = [lifecycle(now) for now in range(index + 1)]
        for interval in _analysis.trading_intervals:
            rows.append(dict(event='trading_interval_opened', bar_index=interval.start_index,
                             source_path=interval.source_path))
            if interval.end_index is not None:
                rows.append(dict(event='trading_interval_closed', bar_index=interval.end_index,
                                 source_path=interval.source_path, start_index=interval.start_index))
        for path, start in sorted(_daily.window_scopes):
            owner = dict(source_path=path, qualification_window_start=start,
                         window_start=_daily.window_start, n_id=f'{path}:{start}')
            rows.append(dict(event='n_completed', direction='up', bar_index=start,
                             target_eligible=True, **owner))
            rows.extend(dict(event='wave_projection_target_reached', bar_index=now, **owner)
                        for now in range(start, index + 1))
        _daily.preparation_audit = rows
        _daily.audit.append(dict(event='entry_rejected', bar_index=index, reason='fixture'))
        return SystemResult([], [*_daily.archived_preparation, *rows, *_daily.audit], {})

    def consume(day, analysis, result, state):
        snapshots.append(dict(index=day.frame.index, result=deepcopy(result.audit),
            archived=deepcopy(state.archived_preparation), preparation=deepcopy(state.preparation_audit),
            scopes=state.window_scopes, window_start=state.window_start,
            a_stream=state.a_wave_stream, projection=state.projection_history))

    monkeypatch.setattr(module, 'prepare_backtest_analysis', analysis_scope)
    monkeypatch.setattr(module.policy, '_generate_system_signals', generate)
    config = SystemStrategy(buy_point_definition='whole_flip_wave_v3',
        entry_policy='hierarchical_two_buy_points', pivot_mode='lecture_causal')
    result = module.daily_signals(bars, config, on_day=consume)

    assert generated == [0, 1, 2, 3, 6]
    assert snapshots[2]['scopes'] == frozenset({('B', 1)})
    assert snapshots[2]['window_start'] == snapshots[3]['window_start'] == 0
    assert snapshots[2]['a_stream'] is snapshots[1]['a_stream']
    assert snapshots[3]['projection'] is snapshots[1]['projection']
    assert all(row.get('source_path') != 'A' for row in snapshots[2]['preparation']
               if not row['event'].startswith(('trend_', 'trading_interval_')))
    assert snapshots[2]['archived'] == snapshots[3]['archived']
    assert all(not row['event'].startswith(('trend_', 'trading_interval_'))
               for row in snapshots[4]['archived'])

    old_a = [row for row in result.audit
             if row.get('source_path') == 'A' and row.get('qualification_window_start') == 0]
    old_b = [row for row in result.audit
             if row.get('source_path') == 'B' and row.get('qualification_window_start') == 1]
    assert [row['bar_index'] for row in old_a if row['event'] == 'wave_projection_target_reached'] == [0, 1]
    assert [row['bar_index'] for row in old_b if row['event'] == 'wave_projection_target_reached'] == [1, 2, 3]
    assert sum(row['event'] == 'n_completed' for row in old_a) == 1
    assert sum(row['event'] == 'n_completed' for row in old_b) == 1
    assert [row['bar_index'] for row in old_a if row['event'] == 'n_target_source_retired'] == [2]
    assert [row['bar_index'] for row in old_b if row['event'] == 'n_target_source_retired'] == [4]
    assert snapshots[6]['window_start'] == 6
    assert snapshots[6]['a_stream'] is not snapshots[3]['a_stream']
    assert snapshots[6]['projection'] is not snapshots[3]['projection']
    assert snapshots[6]['scopes'] == frozenset({('A', 6)})
    assert [row['n_id'] for row in result.audit if row['event'] == 'n_completed'] == ['A:0', 'B:1', 'A:6']
    assert [row['bar_index'] for row in result.audit if row['event'] == 'trend_lifecycle_state'] == list(range(7))
