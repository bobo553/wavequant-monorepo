from copy import deepcopy
from dataclasses import replace
import pytest

from wavequant.domain.strategies import hierarchy_stream as module
from wavequant.domain.strategies import chart_entry_history as chart_module
from wavequant.domain.strategies import combined_a_entry
from wavequant.domain.strategies.backtest_analysis import prepare_backtest_analysis
from wavequant.domain.strategies.chart_drawing_history import ChartDrawingFrame
from wavequant.domain.strategies.hierarchy_stream import HierarchyHistoryStream
from wavequant.domain.strategies.integrated_strategy import SystemStrategy
from wavequant.domain.strategies.market_calculations import MarketDay
from wavequant.domain.market_structure.trend_lines import _TrendJudgementCache
from .test_lecture_strategy import history
from .test_staged_trend_pipeline import _multiple_path_bars


def _day(index, *, epoch=0, point=None, cutoff=None):
    levels, permissions = [], []
    for level in (1, 2, 3):
        points = [] if point is None else [dict(point)]
        stroke = dict(id=f'level-{level}', source_path='source', points=points)
        levels.append(dict(trend_level=level, source_level=level - 1,
            strokes=[stroke], candidate_strokes=[], _path_market_end_indices={'source': cutoff}))
        permissions.append(dict(qualified_strokes=[stroke]))
    return MarketDay(ChartDrawingFrame(index, epoch, [], {'strokes': []}),
        tuple(levels), (), (False, False, False)), permissions


def test_shared_adapter_preserves_actual_knowledge_without_any_trend_recalculation(monkeypatch):
    bars = history(18, 6)
    stream = HierarchyHistoryStream(bars, shared=True)

    def forbidden(*args, **kwargs):
        raise AssertionError('shared market results must not start another trend chain')

    monkeypatch.setattr(_TrendJudgementCache, 'judge', forbidden)
    monkeypatch.setattr(module, 'qualify_trend_structure', forbidden)
    point = dict(index=0, ordinal=0, kind='H', value=10,
                 available_at=bars[2].timestamp.date().isoformat())
    for index in range(3):
        day, permissions = _day(index, point=point)
        stream.advance_market_day(day, permissions)
    assert stream.history[0][1] == stream.history[1][1] == ()
    assert stream.history[2][1][0]['available_at'] == 2
    published = deepcopy(stream.history)
    day, permissions = _day(3, point=dict(point, value=11))
    stream.advance_market_day(day, permissions)
    assert stream.history[3][1][0]['available_at'] == 3
    assert {index: row for index, row in stream.history.items() if index < 3} == published
    day, permissions = _day(4, epoch=4, point=point, cutoff=4)
    stream.advance_market_day(day, permissions)
    assert all(points == () for points in stream.history[4].values())
    with pytest.raises(ValueError, match='second trend'):
        stream.advance(5, 4, [])
    stream.close()


def test_shared_geometry_does_not_manufacture_strategy_permission():
    bars = history(18, 3)
    stream = HierarchyHistoryStream(bars, shared=True)
    day, permissions = _day(0, point=dict(index=0, kind='H', value=10, available_at=0))
    for permission in permissions:
        permission['qualified_strokes'] = []
    stream.advance_market_day(day, permissions)
    assert all(points == () for points in stream.history[0].values())
    stream.close()


def test_daily_analysis_borrows_one_shared_hierarchy_and_releases_it_on_failure(monkeypatch):
    bars = _multiple_path_bars(count=8)
    config = SystemStrategy(buy_point_definition='whole_flip_wave_v3', pivot_mode='lecture_causal',
        strict_n_attack_quality=False, preflight_reward_risk=False, volume_filter=False)
    owners = []

    def forbidden(*args, **kwargs):
        raise AssertionError('daily strategy cannot build an independent hierarchy')

    monkeypatch.setattr(HierarchyHistoryStream, 'advance', forbidden)

    def consume(day, analysis):
        stream = analysis.hierarchy
        assert stream is not None and stream.index == day.frame.index
        owners.append(stream)
        assert set(stream.history) == set(range(day.frame.index + 1))
        if day.frame.index == 10:
            raise RuntimeError('consumer failed')

    with pytest.raises(RuntimeError, match='consumer failed'):
        with prepare_backtest_analysis(bars, config, on_day=consume):
            pass
    assert owners and all(owner is owners[0] for owner in owners)
    assert owners[0]._closed


def test_first_eligible_day_uses_shared_permissions_without_prior_day_trade_warmup(monkeypatch):
    bars = _multiple_path_bars(count=8)
    config = SystemStrategy(buy_point_definition='whole_flip_wave_v3', pivot_mode='lecture_causal',
        strict_n_attack_quality=False, preflight_reward_risk=False, volume_filter=False)
    calculated = []

    def forbidden(*args, **kwargs):
        raise AssertionError('same-day chart must borrow the shared permission result')

    def combined(bars, drawing, first, second, third, dates, index, audit, **kwargs):
        calculated.append(index)
        return ()

    monkeypatch.setattr(chart_module, 'attach_trend_permissions', forbidden)
    monkeypatch.setattr(combined_a_entry, 'combined_a_contexts_from_geometry', combined)

    def consume(day, analysis):
        if day.frame.index != 10:
            return
        current = replace(analysis, buy_days=(False,) * day.frame.index + (True,))
        sink = {}
        chart_module.chart_entry_history(current.bars, _prepared_history=current,
            combined_candidate_sink=sink)
        assert set(sink) == {10}

    with prepare_backtest_analysis(bars, config, on_day=consume):
        pass
    assert calculated == [10]


def test_explicit_minute_eligibility_works_without_a_full_daily_analysis(monkeypatch):
    bars = _multiple_path_bars(count=4)

    def forbidden(*args, **kwargs):
        raise AssertionError('ineligible minute history cannot prepare trading permission shapes')

    monkeypatch.setattr(chart_module, 'attach_trend_permissions', forbidden)
    monkeypatch.setattr(combined_a_entry, 'combined_a_contexts_from_geometry', forbidden)
    sink = {}
    rows, _events = chart_module.chart_entry_history(bars, _entry_days=(False,) * len(bars),
        combined_candidate_sink=sink)
    assert rows and all(value == () for value in rows.values())
    assert sink == {}
