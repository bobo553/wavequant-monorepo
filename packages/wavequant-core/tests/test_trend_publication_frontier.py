"""Publication frontier preserves full replay while skipping settled candidates."""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from wavequant.domain.market_structure import trend_publication as publication
from wavequant.domain.market_structure.trend_lines import _TrendPermissionCache
from wavequant.domain.market_structure.trend_market_index import (
    _MarketPrefixIndex, _MarketRangeIndex, market_prefix,
)
from wavequant.domain.market_structure.trend_point_version import PointVersion
from wavequant.domain.market_structure.trend_publication_cache import _PublicationCache
from wavequant.domain.models.model import Bar


def _bars():
    prices = ((10, 8, 9), (8, 5, 6), (9, 6, 8), (11, 7, 10.5),
              (12, 8, 11), (13, 9, 12), (14, 10, 13))
    return tuple(Bar(datetime(2026, 1, 1) + timedelta(days=i), 'FRONTIER',
                     (high + low) / 2, high, low, close, 100)
                 for i, (high, low, close) in enumerate(prices))


def _point(index, kind, value, known=None):
    return dict(index=index, ordinal=0, kind=kind, value=value,
                time=(datetime(2026, 1, 1) + timedelta(days=index)).date().isoformat(),
                available_at=index if known is None else known, state='confirmed', label=kind)


def _versions(level=1):
    source = PointVersion([_point(0, 'H', 10.), _point(1, 'L', 5.)]).seal()
    field = 'source_turn_position' if level == 1 else f'source_level{level - 1}_position'
    candidates = PointVersion(dict(point, **{field: i}, confirmation_rule='structure')
                              for i, point in enumerate(source)).seal()
    return source, candidates


def _publish(cache, owner, stop, source, candidates, *, level=1, enabled=False):
    market = market_prefix(owner.bars, stop)
    index = _MarketPrefixIndex(owner, market)
    return publication.publish_uptrends(
        candidates, source, market, source_level=level - 1,
        _market_index=index, _publication_cache=cache,
        n_target_trend_confirmation_enabled=enabled,
    )


@pytest.mark.parametrize('level', (1, 2, 3))
def test_pending_origin_is_revisited_until_price_confirms_then_prefix_is_frozen(level):
    bars = _bars()
    owner = _MarketRangeIndex(bars)
    source, candidates = _versions(level)
    cache = _PublicationCache()
    previous = []
    for stop in range(2, len(bars) + 1):
        actual = _publish(cache, owner, stop, source, candidates, level=level)
        expected = _publish(None, owner, stop, source, candidates, level=level)
        assert actual == expected
        previous.append(deepcopy(actual))
        assert cache.checkpoint is not None
        assert cache.checkpoint.offset == (1 if stop < 4 else 2)
    assert [point['kind'] for point in previous[0]] == ['H']
    assert previous[2][-1]['trend_confirmation']['available_at'] == 3
    assert previous[0] == [dict(candidates[0])]


def test_settled_candidates_do_not_reenter_qualification_or_full_iteration(monkeypatch):
    owner = _MarketRangeIndex(_bars())
    source, candidates = _versions()
    cache = _PublicationCache()
    expected = _publish(cache, owner, 4, source, candidates)
    tails = []
    original = publication._candidate_tail

    def tail(values, start):
        tails.append(start)
        yield from original(values, start)

    def forbidden(*args, **kwargs):
        raise AssertionError('settled publication prefix was reprocessed')

    monkeypatch.setattr(publication, '_candidate_tail', tail)
    monkeypatch.setattr(publication, 'qualify_uptrend', forbidden)
    monkeypatch.setattr(publication, 'qualify_downtrend', forbidden)
    monkeypatch.setattr(PointVersion, '__iter__', forbidden)
    lookups = (publication._OriginLookup([]), publication._OriginLookup([]))
    market = market_prefix(owner.bars, 5)
    actual = publication.publish_uptrends(
        candidates, source, market, source_level=0, _market_index=_MarketPrefixIndex(owner, market),
        _origin_lookups=lookups, _publication_cache=cache,
    )
    assert actual == expected
    assert tails == [len(candidates)]


def test_appended_future_candidate_resumes_without_replaying_prior_candidates():
    owner = _MarketRangeIndex(_bars())
    source, candidates = _versions()
    cache = _PublicationCache()
    original = _publish(cache, owner, 4, source, candidates)
    new_point = _point(4, 'H', 12.)
    next_source = PointVersion([new_point], previous=source, start=2).seal()
    next_candidates = PointVersion(
        [dict(new_point, source_turn_position=2, confirmation_rule='structure')],
        previous=candidates, start=2,
    ).seal()
    expected = _publish(None, owner, 5, next_source, next_candidates)
    with patch.object(publication, 'qualify_uptrend', side_effect=AssertionError('old low requalified')):
        actual = _publish(cache, owner, 5, next_source, next_candidates)
    assert actual == expected
    assert [point['kind'] for point in actual] == ['H', 'L', 'H']
    actual[1]['trend_confirmation']['origin']['value'] = -100
    assert _publish(cache, owner, 6, next_source, next_candidates) == expected
    assert original[-1]['trend_confirmation']['origin']['value'] == 5.


@pytest.mark.parametrize('revision', ('source', 'candidate', 'truncate', 'backdated_append'))
def test_revised_dependencies_fall_back_to_exact_full_publication(revision):
    owner = _MarketRangeIndex(_bars())
    source, candidates = _versions()
    cache = _PublicationCache()
    _publish(cache, owner, 4, source, candidates)
    if revision == 'source':
        source = PointVersion([dict(source[1], available_at=3)], previous=source, start=1).seal()
    elif revision == 'candidate':
        candidates = PointVersion([dict(candidates[1], available_at=3)], previous=candidates, start=1).seal()
    elif revision == 'truncate':
        candidates = PointVersion(previous=candidates, start=1).seal()
    else:
        point = _point(2, 'H', 9., known=3)
        source = PointVersion([point], previous=source, start=2).seal()
        candidates = PointVersion([dict(point, source_turn_position=2, confirmation_rule='structure')],
                                  previous=candidates, start=2).seal()
    expected = _publish(None, owner, 5, source, candidates)
    assert _publish(cache, owner, 5, source, candidates) == expected
    assert cache.checkpoint is None


def test_origin_break_before_confirmation_is_a_terminal_rejection():
    bars = list(_bars())
    bars[2] = replace(bars[2], low=4., high=12.)
    owner = _MarketRangeIndex(tuple(bars))
    source, candidates = _versions()
    cache = _PublicationCache()
    expected = _publish(None, owner, 3, source, candidates)
    assert _publish(cache, owner, 3, source, candidates) == expected
    assert cache.checkpoint.offset == len(candidates)
    with patch.object(publication, 'qualify_uptrend', side_effect=AssertionError('dead origin requalified')):
        assert _publish(cache, owner, 7, source, candidates) == expected


def test_rewind_and_revised_market_cannot_reuse_future_or_other_market_proofs():
    bars = _bars()
    owner = _MarketRangeIndex(bars)
    source, candidates = _versions()
    cache = _PublicationCache()
    _publish(cache, owner, 5, source, candidates)
    assert _publish(cache, owner, 2, source, candidates) == _publish(None, owner, 2, source, candidates)
    revised = tuple(replace(bar, high=10., close=9.) if i == 3 else bar for i, bar in enumerate(bars))
    other = _MarketRangeIndex(revised)
    assert _publish(cache, other, 4, source, candidates) == _publish(None, other, 4, source, candidates)


def test_unknown_knowledge_and_budget_exhaustion_keep_original_behavior():
    owner = _MarketRangeIndex(_bars())
    source, candidates = _versions()
    unknown = PointVersion([dict(source[1], available_at=5)], previous=source, start=1).seal()
    for actual_source, cache in ((unknown, _PublicationCache()), (source, _PublicationCache(memory_bytes=1))):
        assert _publish(cache, owner, 4, actual_source, candidates) == _publish(
            None, owner, 4, actual_source, candidates)
        assert cache.checkpoint is None


def test_n_confirmation_does_not_reuse_ordinary_frontier():
    owner = _MarketRangeIndex(_bars())
    source, candidates = _versions()
    cache = _PublicationCache()
    _publish(cache, owner, 4, source, candidates)
    expected = _publish(None, owner, 5, source, candidates, enabled=True)
    with patch.object(cache, 'admit', side_effect=AssertionError('ordinary frontier used for N')):
        assert _publish(cache, owner, 5, source, candidates, enabled=True) == expected


def test_cache_is_request_level_and_path_scoped():
    cache = _TrendPermissionCache(_bars())
    first = cache.publication(1, 'path-a')
    assert cache.publication(1, 'path-a') is first
    assert cache.publication(2, 'path-a') is not first
    assert cache.publication(1, 'path-b') is not first
    assert cache.publication(1, 'path-a') is not first


@pytest.mark.parametrize('level', (1, 2, 3))
def test_date_knowledge_uses_the_same_prefix_boundary(level):
    owner = _MarketRangeIndex(_bars())
    source, candidates = _versions(level)
    source = PointVersion(dict(point, available_at=owner.bars[point['available_at']].timestamp.date().isoformat())
                          for point in source).seal()
    candidates = PointVersion(dict(point, available_at=owner.bars[point['available_at']].timestamp.date().isoformat())
                              for point in candidates).seal()
    cache = _PublicationCache()
    for stop in range(2, len(owner.bars) + 1):
        assert _publish(cache, owner, stop, source, candidates, level=level) == _publish(
            None, owner, stop, source, candidates, level=level)
    assert cache.checkpoint.offset == 2


def test_old_suffix_revision_invalidates_prefix_that_could_have_looked_ahead():
    owner = _MarketRangeIndex(_bars())
    source, candidates = _versions()
    cache = _PublicationCache()
    _publish(cache, owner, 2, source, candidates)
    assert cache.checkpoint.offset == 1
    # The edit is after the saved offset but still inside its knowledge horizon.
    revised = PointVersion([dict(candidates[1], label='revised-low')],
                           previous=candidates, start=1).seal()
    assert _publish(cache, owner, 3, source, revised) == _publish(None, owner, 3, source, revised)
    assert cache.checkpoint is None


def test_publication_options_reach_the_owned_qualification_entry():
    from wavequant.domain.market_structure.trend_lines import _TrendCandidateCache, qualify_trend_structure

    owner = _MarketRangeIndex(_bars())
    source, candidates = _versions()
    candidate_cache = _TrendCandidateCache()
    cache = _PublicationCache()
    for stop in (4, 5):
        market = market_prefix(owner.bars, stop)
        index = _MarketPrefixIndex(owner, market)
        actual = qualify_trend_structure(
            candidates, source, market, trend_level=1, _market_index=index,
            _owned_candidates=True, _candidate_cache=candidate_cache, _publication_cache=cache,
        )
        expected = qualify_trend_structure(candidates, source, market, trend_level=1, _market_index=index)
        assert actual == expected
        assert cache.checkpoint is not None


def test_disabled_n_does_not_block_frontier_when_lower_level_permissions_are_supplied():
    owner = _MarketRangeIndex(_bars())
    source, candidates = _versions()
    cache = _PublicationCache()
    expected = _publish(None, owner, 4, source, candidates)
    for stop in (4, 5):
        market = market_prefix(owner.bars, stop)
        actual = publication.publish_uptrends(
            candidates, source, market, source_level=0, qualified_source=[],
            _market_index=_MarketPrefixIndex(owner, market), _publication_cache=cache,
        )
        assert actual == expected
        assert cache.checkpoint is not None
        assert cache.checkpoint.offset == len(candidates)
