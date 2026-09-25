"""Intraday replays may reuse only completed daily hierarchy work."""

from dataclasses import replace
from importlib import import_module

from wavequant.domain.strategies.integrated_strategy import generate_system_signals

from .test_completed_wave_recovery import config
from .test_wave_continuation import sample


def test_minute_prefix_reuses_prior_hierarchy_without_changing_current_evidence(monkeypatch):
    module = import_module("wavequant.domain.strategies.chart_entry_history")
    bars = sample()[0][:120]
    original = module.reversal_trends
    calls = []

    def counted(drawing, prefix):
        calls.append(len(prefix))
        return original(drawing, prefix)

    monkeypatch.setattr(module, "reversal_trends", counted)
    cache = {}
    first = [*bars[:-1], replace(bars[-1], high=bars[-1].high + 0.1)]
    first_sink = {}
    module.chart_entry_history(first, audit=(), shallow_candidate_sink=first_sink, prefix_cache=cache)
    initial_reductions = len(calls)
    assert initial_reductions > 1

    # A later five-minute observation may change today's high and low. The
    # cached path must still calculate today's step and agree with full replay.
    second = [*bars[:-1], replace(bars[-1], high=bars[-1].high + 0.5, low=bars[-1].low - 0.2)]
    calls.clear()
    cached_sink = {}
    cached = module.chart_entry_history(second, audit=(), shallow_candidate_sink=cached_sink, prefix_cache=cache)
    cached_reductions = len(calls)
    calls.clear()
    full_sink = {}
    full = module.chart_entry_history(second, audit=(), shallow_candidate_sink=full_sink)

    assert cached == full
    assert cached_sink == full_sink
    assert cached_reductions <= 1
    assert len(calls) >= initial_reductions

    # A completed replay must not mutate the checkpoint used by the next
    # intraday observation of the same session.
    third = [*bars[:-1], replace(bars[-1], high=bars[-1].high + 0.7, low=bars[-1].low - 0.3)]
    third_sink = {}
    assert module.chart_entry_history(third, shallow_candidate_sink=third_sink, prefix_cache=cache) == (
        module.chart_entry_history(third, shallow_candidate_sink={}))


def test_prior_bar_change_invalidates_hierarchy_checkpoint(monkeypatch):
    module = import_module("wavequant.domain.strategies.chart_entry_history")
    bars = sample()[0][:80]
    original = module.reversal_trends
    calls = []

    def counted(drawing, prefix):
        calls.append(len(prefix))
        return original(drawing, prefix)

    monkeypatch.setattr(module, "reversal_trends", counted)
    cache = {}
    module.chart_entry_history(bars, prefix_cache=cache)
    baseline = len(calls)
    assert baseline > 1

    changed = list(bars)
    changed[-2] = replace(changed[-2], high=changed[-2].high + 0.3)
    calls.clear()
    cached = module.chart_entry_history(changed, prefix_cache=cache)
    assert len(calls) > 1
    assert cached == module.chart_entry_history(changed)


def test_hierarchical_reducer_reuses_only_prior_days(monkeypatch):
    module = import_module("wavequant.domain.strategies.hierarchical_entry")
    bars = sample()[0][:120]
    original = module._wave_reversals
    calls = []

    def counted(points):
        calls.append(len(points))
        return original(points)

    monkeypatch.setattr(module, "_wave_reversals", counted)
    cache = {}
    first = [*bars[:-1], replace(bars[-1], high=bars[-1].high + 0.1)]
    module.hierarchical_history(first, prefix_cache=cache)
    initial_reductions = len(calls)
    assert initial_reductions >= len(bars)

    second = [*bars[:-1], replace(bars[-1], high=bars[-1].high + 0.5, low=bars[-1].low - 0.2)]
    calls.clear()
    cached = module.hierarchical_history(second, prefix_cache=cache)
    cached_reductions = len(calls)
    calls.clear()
    full = module.hierarchical_history(second)
    assert cached == full
    assert cached_reductions == 1
    assert len(calls) == initial_reductions


def test_lecture_pivots_reuse_only_prior_days_and_rebuild_episode_limits(monkeypatch):
    module = import_module("wavequant.domain.strategies.lecture_strategy")
    bars = sample()[0][:120]
    original = module.ReversalPoint
    calls = []

    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "ReversalPoint", counted)
    cache = {}
    first = [*bars[:-1], replace(bars[-1], high=bars[-1].high + 0.1)]
    module.lecture_pivot_history(first, prefix_cache=cache)
    initial_points = len(calls)
    assert initial_points > 1

    second = [*bars[:-1], replace(bars[-1], high=bars[-1].high + 0.5, low=bars[-1].low - 0.2)]
    calls.clear()
    cached = module.lecture_pivot_history(second, prefix_cache=cache)
    cached_points = len(calls)
    calls.clear()
    full = module.lecture_pivot_history(second)
    assert cached == full
    assert cached_points < len(calls)


def test_whole_wave_signal_replay_matches_uncached_prefix(monkeypatch):
    bars = sample()[0][:120]
    cache = {}
    first = [*bars[:-1], replace(bars[-1], high=bars[-1].high + 0.1)]
    second = [*bars[:-1], replace(bars[-1], high=bars[-1].high + 0.5, low=bars[-1].low - 0.2)]
    strategy = config()
    module = import_module("wavequant.domain.strategies.integrated_strategy")
    original = module.observe_n
    calls = []
    original_structure = module.observe_structure
    structure_calls = []
    folded_module = import_module("wavequant.domain.strategies.folded_n")
    original_folded = folded_module.folded_positive_n_candidates
    folded_calls = []

    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    def counted_folded(*args, **kwargs):
        folded_calls.append(1)
        return original_folded(*args, **kwargs)

    def counted_structure(*args, **kwargs):
        structure_calls.append(1)
        return original_structure(*args, **kwargs)

    monkeypatch.setattr(module, "observe_n", counted)
    monkeypatch.setattr(module, "observe_structure", counted_structure)
    monkeypatch.setattr(folded_module, "folded_positive_n_candidates", counted_folded)

    generate_system_signals(first, strategy, chart_history_cache=cache)
    calls.clear()
    folded_calls.clear()
    structure_calls.clear()
    cached = generate_system_signals(second, strategy, chart_history_cache=cache)
    cached_observations = len(calls)
    cached_folded = len(folded_calls)
    cached_structures = len(structure_calls)
    calls.clear()
    folded_calls.clear()
    structure_calls.clear()
    full = generate_system_signals(second, strategy)
    assert cached == full
    assert cached_observations < len(calls)
    assert cached_folded == 1
    assert len(folded_calls) == len(bars)
    assert cached_structures < len(structure_calls)


def test_fixed_source_reuses_completed_observations_across_sessions():
    bars = sample()[0][:120]
    strategy = config()
    cache = {"source_bars": tuple(bars)}
    prior_session = [*bars[:118], replace(bars[118], high=bars[118].high + 0.1)]
    generate_system_signals(prior_session, strategy, chart_history_cache=cache)
    observations = cache["candidate_observations"]

    next_session = [*bars[:-1], replace(bars[-1], high=bars[-1].high + 0.2)]
    cached = generate_system_signals(next_session, strategy, chart_history_cache=cache)
    assert cache["candidate_scope"][0] == "source"
    assert cache["candidate_observations"] is observations
    assert cached == generate_system_signals(next_session, strategy)

    # A revised prior candle cancels the source certificate before lookup.
    changed = list(next_session)
    changed[-2] = replace(changed[-2], high=changed[-2].high + 0.3)
    assert generate_system_signals(changed, strategy, chart_history_cache=cache) == generate_system_signals(
        changed, strategy)
    assert cache["candidate_scope"][0] == "day"


def test_hierarchy_checkpoints_advance_one_completed_session(monkeypatch):
    bars = sample()[0][:120]
    prior_session = [*bars[:118], replace(bars[118], high=bars[118].high + 0.1)]
    next_session = [*bars[:-1], replace(bars[-1], high=bars[-1].high + 0.2)]

    chart = import_module("wavequant.domain.strategies.chart_entry_history")
    chart_cache = {}
    prior_sink = {}
    chart.chart_entry_history(prior_session, shallow_candidate_sink=prior_sink, prefix_cache=chart_cache)
    chart_calls = []
    original_reversals = chart.reversal_trends

    def counted_reversals(drawing, prefix):
        chart_calls.append(len(prefix))
        return original_reversals(drawing, prefix)

    monkeypatch.setattr(chart, "reversal_trends", counted_reversals)
    cached_sink = {}
    cached_chart = chart.chart_entry_history(next_session, shallow_candidate_sink=cached_sink, prefix_cache=chart_cache)
    cached_reductions = len(chart_calls)
    chart_calls.clear()
    full_sink = {}
    assert cached_chart == chart.chart_entry_history(next_session, shallow_candidate_sink=full_sink)
    assert cached_sink == full_sink
    assert cached_reductions <= 2
    assert len(chart_calls) > cached_reductions

    hierarchy = import_module("wavequant.domain.strategies.hierarchical_entry")
    hierarchy_cache = {}
    hierarchy.hierarchical_history(prior_session, prefix_cache=hierarchy_cache)
    original_wave = hierarchy._wave_reversals
    wave_calls = []

    def counted_wave(points):
        wave_calls.append(1)
        return original_wave(points)

    monkeypatch.setattr(hierarchy, "_wave_reversals", counted_wave)
    cached_hierarchy = hierarchy.hierarchical_history(next_session, prefix_cache=hierarchy_cache)
    assert len(wave_calls) == 2
    assert cached_hierarchy == hierarchy.hierarchical_history(next_session)

    pivot = import_module("wavequant.domain.strategies.lecture_strategy")
    pivot_cache = {}
    pivot.lecture_pivot_history(prior_session, prefix_cache=pivot_cache)
    cached_pivots = pivot.lecture_pivot_history(next_session, prefix_cache=pivot_cache)
    assert cached_pivots == pivot.lecture_pivot_history(next_session)
