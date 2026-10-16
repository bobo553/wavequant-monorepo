"""Closed historical preparation is reusable without changing strategy facts."""

from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from wavequant.domain.models.model import Bar
from wavequant.domain.market_structure.polyline import LinePoint, PointKind, ReversalPoint
from wavequant.domain.strategies.inverse_n_entry import inverse_n_low_entry_risk
from wavequant.domain.strategies.n_consolidation import consolidation_gap
from wavequant.domain.strategies.strategy_history_cache import (
    StrategyHistoryCache, first_positive_n_invalidation,
)


def market() -> tuple[Bar, ...]:
    return tuple(
        Bar(datetime(2020, 1, 1) + timedelta(days=index), 'test', low + 1, low + 2, low, low + 1.5, 100)
        for index, low in enumerate((8.0, 9.0, 10.0, 9.0, 11.0, 7.0))
    )


@pytest.mark.parametrize('defense', (7.0, 8.5, 9.5, 10.5))
def test_invalidation_matches_cold_results_through_append_and_rewind(defense: float) -> None:
    bars = market()
    cache = StrategyHistoryCache(bars)
    for end in (2, 3, 4, 5, 6, 6, 4, 3, 6):
        prefix = bars[:end]
        view = cache.bind(prefix)
        assert view is not None
        assert view.first_positive_n_invalidation(origin=0, attack=1, defense=defense) == (
            first_positive_n_invalidation(prefix, origin=0, attack=1, defense=defense)
        )


def test_changed_parameters_keep_independent_invalidation_results() -> None:
    bars = market()
    view = StrategyHistoryCache(bars).bind(bars)
    assert view is not None
    assert view.first_positive_n_invalidation(origin=0, attack=1, defense=8.5) == 5
    assert view.first_positive_n_invalidation(origin=0, attack=1, defense=10.5) == 2
    assert view.first_positive_n_invalidation(origin=0, attack=1, defense=8.5) == 5


def test_partial_last_candle_cannot_contaminate_later_sealed_observations() -> None:
    bars = market()
    cache = StrategyHistoryCache(bars)
    # Warm through a later failure, then revisit an earlier partial last candle.
    full = cache.bind(bars)
    assert full is not None
    assert full.first_positive_n_invalidation(origin=0, attack=1, defense=8.5) == 5
    partial = (*bars[:3], replace(bars[3], low=7.5))
    view = cache.bind(partial)
    assert view is not None
    assert view.first_positive_n_invalidation(origin=0, attack=1, defense=8.5) == 3
    closed = cache.bind(bars[:4])
    assert closed is not None
    assert closed.first_positive_n_invalidation(origin=0, attack=1, defense=8.5) is None


def test_revised_older_bar_rejects_binding_and_keeps_original_cache_valid() -> None:
    bars = market()
    cache = StrategyHistoryCache(bars)
    original = cache.bind(bars)
    assert original is not None
    assert original.first_positive_n_invalidation(origin=0, attack=1, defense=8.5) == 5
    revised = (*bars[:2], replace(bars[2], low=7.5), *bars[3:])
    assert cache.bind(revised) is None
    assert first_positive_n_invalidation(revised, origin=0, attack=1, defense=8.5) == 2
    assert original.first_positive_n_invalidation(origin=0, attack=1, defense=8.5) == 5


def test_none_is_a_cached_result_and_changed_inputs_are_recomputed() -> None:
    bars = market()
    cache = StrategyHistoryCache(bars)
    view = cache.bind(bars[:4])
    assert view is not None
    calls = []

    def observe() -> None:
        calls.append(1)
        return None

    assert view.observe('rule', 2, ('first',), observe) is None
    later = cache.bind(bars)
    assert later is not None
    assert later.observe('rule', 2, ('first',), observe) is None
    assert calls == [1]
    assert later.observe('rule', 2, ('revised',), observe) is None
    assert calls == [1, 1]


def test_unsealed_observation_never_reads_or_writes_closed_cache() -> None:
    bars = market()
    cache = StrategyHistoryCache(bars)
    closed = cache.bind(bars[:4])
    assert closed is not None
    assert closed.observe('rule', 3, (), lambda: bars[3].low) == 9.0
    partial = (*bars[:3], replace(bars[3], low=7.5))
    view = cache.bind(partial)
    assert view is not None
    assert view.observe('rule', 3, (), lambda: partial[3].low) == 7.5
    assert closed.observe('rule', 3, (), lambda: -1.0) == 9.0


def test_cached_evidence_cannot_be_mutated_by_a_consumer() -> None:
    bars = market()
    view = StrategyHistoryCache(bars).bind(bars)
    assert view is not None
    proof = view.observe('rule', 2, (), lambda: {'level': {'price': 10.0}})
    proof['level']['price'] = 0.0
    assert view.observe('rule', 2, (), lambda: {}) == {'level': {'price': 10.0}}


def test_eviction_preserves_cold_result_and_request_isolation() -> None:
    bars = market()
    view = StrategyHistoryCache(bars, capacity=1).bind(bars)
    assert view is not None
    assert view.observe('rule', 2, ('first',), lambda: None) is None
    assert view.observe('rule', 2, ('second',), lambda: 2) == 2
    assert view.observe('rule', 2, ('first',), lambda: 1) == 1
    other = StrategyHistoryCache(bars).bind(bars)
    assert other is not None
    assert other.observe('rule', 2, ('first',), lambda: 3) == 3


def test_inverse_rule_reuses_exact_vertices_but_recomputes_revised_confirmation() -> None:
    rows = ((11, 12, 10, 11.5), (10.5, 11, 8, 9), (9, 9.8, 8.2, 9.4),
            (9.5, 10, 8.5, 9.8), (9.7, 10, 8.4, 9.9), (9.9, 10.5, 7.9, 10.2))
    bars = tuple(Bar(datetime(2020, 1, 1) + timedelta(days=index), 'test', *row, 100)
                 for index, row in enumerate(rows))
    source = (*bars, replace(bars[-1], timestamp=datetime(2020, 1, 7), high=100))
    points = tuple(ReversalPoint(LinePoint(index, 0, kind, price), known, 'fixture')
                   for index, kind, price, known in ((0, PointKind.HIGH, 12, 1),
                                                    (1, PointKind.LOW, 8, 2),
                                                    (3, PointKind.HIGH, 10, 4)))
    cache = StrategyHistoryCache(source)
    cold = inverse_n_low_entry_risk(bars, 5, points)
    assert cold is not None
    for prefix in (bars, source, bars):
        view = cache.bind(prefix)
        assert view is not None
        assert view.observe('inverse_n_low_entry', 5, (points,),
            lambda: inverse_n_low_entry_risk(prefix, 5, points)) == cold
    revised = (*points[:2], replace(points[2], confirmed_index=6))
    assert view.observe('inverse_n_low_entry', 5, (revised,),
        lambda: inverse_n_low_entry_risk(bars, 5, revised)) is None


def test_consolidation_rule_cache_keeps_defense_and_unsealed_day_distinct() -> None:
    rows = ((9, 11, 9, 11, 100), (11, 12, 10, 11.5, 120), (10.7, 10.9, 10.4, 10.6, 60),
            (10.5, 10.8, 10.2, 10.7, 70), (11.1, 12, 11, 11.9, 150))
    bars = tuple(Bar(datetime(2020, 1, 1) + timedelta(days=index), 'test', *row)
                 for index, row in enumerate(rows))
    view = StrategyHistoryCache(bars).bind(bars)
    assert view is not None
    proof = consolidation_gap(bars, attack=0, now=4, defense=9)
    assert proof is not None
    assert view.observe('consolidation_gap', 4, (0, 9),
        lambda: consolidation_gap(bars, attack=0, now=4, defense=9)) == proof
    assert view.observe('consolidation_gap', 4, (0, 10.5),
        lambda: consolidation_gap(bars, attack=0, now=4, defense=10.5)) is None
    partial = (*bars[:-1], replace(bars[-1], volume=70))
    partial_view = view.cache.bind(partial)
    assert partial_view is not None
    assert partial_view.observe('consolidation_gap', 4, (0, 9),
        lambda: consolidation_gap(partial, attack=0, now=4, defense=9)) is None
