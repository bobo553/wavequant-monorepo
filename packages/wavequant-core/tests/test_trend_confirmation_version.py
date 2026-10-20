"""Version reuse must retain exact date-bound confirmation inputs."""

from datetime import datetime, timedelta

import pytest

from wavequant.domain.market_structure import trend_confirmation_version as version_module
from wavequant.domain.market_structure.trend_confirmation import prepare_confirmation_context
from wavequant.domain.market_structure.trend_confirmation_version import _ConfirmationVersionCache
from wavequant.domain.market_structure.trend_market_index import _MarketPrefixIndex, _MarketRangeIndex, market_prefix
from wavequant.domain.market_structure.trend_point_version import PointVersion
from wavequant.domain.models.model import Bar


def _owner():
    return _MarketRangeIndex(tuple(
        Bar(datetime(2026, 1, 1) + timedelta(days=i), 'VERSION', 9., 12., 5., 10., 100)
        for i in range(12)))


def _point(index, *, clock='integer', known=None):
    day = (datetime(2026, 1, 1) + timedelta(days=index)).date().isoformat()
    return dict(index=index, ordinal=0, kind='H' if index % 2 == 0 else 'L',
                value=10. if index % 2 == 0 else 6., time=day,
                available_at=known if known is not None else day if clock == 'date' else index)


def _context(cache, owner, source, stop):
    bars = market_prefix(owner.bars, stop)
    market = _MarketPrefixIndex(owner, bars)
    actual = cache.prepare(source, bars, market)
    expected = prepare_confirmation_context(source, bars, stop - 1, _market_index=market)
    assert actual is not None
    assert list(actual.references) == list(expected.references)
    assert list(actual._prefix_visibility) == list(expected._prefix_visibility)
    assert list(actual._proof_prefixes) == list(expected._proof_prefixes)
    assert list(actual._reference_stops) == list(expected._reference_stops)
    assert actual.ordered == expected.ordered
    assert actual.end_index == expected.end_index
    assert actual.bars_identity == expected.bars_identity
    assert actual.source_identity == expected.source_identity
    # Historical certificate projection must use the original clock binding.
    projected = prepare_confirmation_context(source, bars, 1, parsed_context=actual, _market_index=market)
    cold = prepare_confirmation_context(source, bars, 1, _market_index=market)
    assert list(projected.references) == list(cold.references)
    return actual


@pytest.mark.parametrize('clock', ('integer', 'date'))
def test_unchanged_prefix_is_not_parsed_again_and_revisions_preserve_old_context(monkeypatch, clock):
    cache, owner = _ConfirmationVersionCache(), _owner()
    source = PointVersion(_point(i, clock=clock) for i in range(4)).seal()
    saved = _context(cache, owner, source, 5)
    saved_refs, saved_hashes = list(saved.references), list(saved._proof_prefixes)
    parsed = []
    original = version_module._reference

    def track(point, sessions, end):
        parsed.append(point['index'])
        return original(point, sessions, end)

    monkeypatch.setattr(version_module, '_reference', track)
    _context(cache, owner, source, 6)
    assert parsed == []
    appended = PointVersion([_point(4, clock=clock)], previous=source, start=4).seal()
    _context(cache, owner, appended, 7)
    assert parsed == [4]
    parsed.clear()
    revised = PointVersion([dict(appended[3], value=7.)], previous=appended, start=3).seal()
    _context(cache, owner, revised, 8)
    assert parsed == [3]
    truncated = PointVersion(previous=revised, start=2).seal()
    _context(cache, owner, truncated, 9)
    assert list(saved.references) == saved_refs
    assert list(saved._proof_prefixes) == saved_hashes
    assert 0 < cache.memory_used <= cache.limit


def test_rewind_and_replacement_market_reparse_current_prefix(monkeypatch):
    cache, owner = _ConfirmationVersionCache(), _owner()
    source = PointVersion(_point(i) for i in range(2)).seal()
    _context(cache, owner, source, 8)
    parsed = []
    original = version_module._reference

    def track(point, sessions, end):
        parsed.append(point['index'])
        return original(point, sessions, end)

    monkeypatch.setattr(version_module, '_reference', track)
    _context(cache, owner, source, 3)
    assert parsed == [0, 1]
    parsed.clear()
    _context(cache, _owner(), source, 4)
    assert parsed == [0, 1]


def test_unknown_future_date_is_not_retained_as_permanently_missing():
    owner, cache = _owner(), _ConfirmationVersionCache()
    source = PointVersion([_point(0), _point(1, known=5)]).seal()
    bars = market_prefix(owner.bars, 3)
    assert cache.prepare(source, bars, _MarketPrefixIndex(owner, bars)) is None
    assert cache.source is None and cache.memory_used == 0
    _context(cache, owner, source, 7)


@pytest.mark.parametrize('case', ('mutable', 'unsealed', 'unordered', 'budget', 'duplicate'))
def test_unsupported_inputs_release_cache_and_fall_back(case):
    owner = _owner()
    cache = _ConfirmationVersionCache(memory_bytes=32 if case == 'budget' else 2 * 1024 * 1024)
    points = [_point(0), _point(1)]
    if case == 'unordered':
        points.reverse()
    source = points if case == 'mutable' else PointVersion(points)
    if case not in ('mutable', 'unsealed'):
        source.seal()
    if case == 'duplicate':
        owner = _MarketRangeIndex((owner.bars[0], owner.bars[0], *owner.bars[2:]))
    bars = market_prefix(owner.bars, 6)
    assert cache.prepare(source, bars, _MarketPrefixIndex(owner, bars)) is None
    assert cache.source is None and cache.memory_used == 0


def test_rejected_version_and_direct_appends_do_not_retry_sizing(monkeypatch):
    owner, cache = _owner(), _ConfirmationVersionCache(memory_bytes=32)
    source = PointVersion([_point(0), _point(1)]).seal()
    bars = market_prefix(owner.bars, 4)
    assert cache.prepare(source, bars, _MarketPrefixIndex(owner, bars)) is None
    assert cache._rejected is not None

    def forbidden(*args, **kwargs):
        raise AssertionError('unchanged rejected source was measured or parsed again')

    monkeypatch.setattr(version_module, '_builtin_size', forbidden)
    monkeypatch.setattr(version_module, '_reference', forbidden)
    for stop in (4, 5, 6):
        bars = market_prefix(owner.bars, stop)
        assert cache.prepare(source, bars, _MarketPrefixIndex(owner, bars)) is None
    for index in (2, 3):
        source = PointVersion([_point(index)], previous=source, start=len(source)).seal()
        bars = market_prefix(owner.bars, 7 + index)
        assert cache.prepare(source, bars, _MarketPrefixIndex(owner, bars)) is None
    assert cache.source is None and cache.memory_used == 0


@pytest.mark.parametrize('change', ('revision', 'truncate', 'market', 'rewind', 'budget', 'clear', 'unrelated'))
def test_rejection_retries_when_its_dependencies_change(monkeypatch, change):
    owner, cache = _owner(), _ConfirmationVersionCache(memory_bytes=32)
    source = PointVersion([_point(0), _point(1), _point(2)]).seal()
    bars = market_prefix(owner.bars, 6)
    assert cache.prepare(source, bars, _MarketPrefixIndex(owner, bars)) is None
    if change == 'revision':
        source = PointVersion([dict(_point(0), value=8.)], previous=source, start=0).seal()
    elif change == 'truncate':
        source = PointVersion(previous=source, start=2).seal()
    elif change == 'market':
        owner = _owner()
    elif change == 'budget':
        cache.set_limit(2 * 1024 * 1024)
    elif change == 'clear':
        cache.clear()
    elif change == 'unrelated':
        source = PointVersion([_point(0), _point(1)]).seal()
    calls = []
    original = version_module._builtin_size

    def track(point, limit):
        calls.append(point)
        return original(point, limit)

    monkeypatch.setattr(version_module, '_builtin_size', track)
    bars = market_prefix(owner.bars, 4 if change == 'rewind' else 7)
    actual = cache.prepare(source, bars, _MarketPrefixIndex(owner, bars))
    assert calls
    if change == 'budget':
        assert actual is not None
        assert cache._rejected is None
    else:
        assert actual is None


def test_shrinking_publication_budget_records_rejection_instead_of_retrying(monkeypatch):
    from wavequant.domain.market_structure.trend_publication_cache import _PublicationCache

    owner = _owner()
    publication = _PublicationCache()
    cache = publication.confirmations
    source = PointVersion([_point(0), _point(1)]).seal()
    _context(cache, owner, source, 4)
    publication._input_bytes = publication.limit - 32
    publication._fit_confirmation_budget()
    assert cache.limit == 32 and cache.source is None
    assert cache._rejected is not None

    def forbidden(*args, **kwargs):
        raise AssertionError('budget eviction caused immediate rebuild')

    with monkeypatch.context() as scoped:
        scoped.setattr(version_module, '_builtin_size', forbidden)
        bars = market_prefix(owner.bars, 5)
        assert cache.prepare(source, bars, _MarketPrefixIndex(owner, bars)) is None
    publication._input_bytes = 0
    publication._fit_confirmation_budget()
    _context(cache, owner, source, 6)


def test_rejection_metadata_does_not_retain_source_or_market():
    from weakref import ref

    owner, cache = _owner(), _ConfirmationVersionCache(memory_bytes=32)
    source = PointVersion([_point(0)]).seal()
    source_ref, owner_ref = ref(source), ref(owner)
    bars = market_prefix(owner.bars, 3)
    market = _MarketPrefixIndex(owner, bars)
    assert cache.prepare(source, bars, market) is None
    del source, market, owner
    assert source_ref() is None
    assert owner_ref() is None
    assert cache._rejected is not None


def test_repeated_cache_rejection_preserves_full_current_and_historical_contexts():
    from wavequant.domain.market_structure.trend_publication import _PublishedTrendState

    owner, cache = _owner(), _ConfirmationVersionCache(memory_bytes=32)
    source = PointVersion([_point(0), _point(1), _point(2)]).seal()
    for stop in (4, 5, 6):
        bars = market_prefix(owner.bars, stop)
        market = _MarketPrefixIndex(owner, bars)
        state = _PublishedTrendState(source, bars, 0, _market_index=market)
        state._context_cache = cache
        for cutoff in (stop - 1, 1):
            actual = state.confirmation_context(cutoff)
            expected = prepare_confirmation_context(source, bars, cutoff, _market_index=market)
            assert list(actual.references) == list(expected.references)
            assert actual.ordered == expected.ordered
            assert actual.end_index == expected.end_index
            stop_visible = expected._visible_stop
            assert list(actual._proof_prefixes)[:stop_visible + 1] == list(expected._proof_prefixes)[:stop_visible + 1]
    assert cache._rejected is not None


def test_clock_dependent_rejection_is_retried_when_future_point_becomes_known():
    owner, cache = _owner(), _ConfirmationVersionCache()
    source = PointVersion([_point(0), _point(1, known=5)]).seal()
    bars = market_prefix(owner.bars, 3)
    assert cache.prepare(source, bars, _MarketPrefixIndex(owner, bars)) is None
    assert cache._rejected is None
    _context(cache, owner, source, 7)


def test_nested_evidence_is_rejected_before_recursive_sizing(monkeypatch):
    owner, cache = _owner(), _ConfirmationVersionCache()
    point = dict(_point(0), evidence={'proof': ['large' * 10000]})
    source = PointVersion([point]).seal()

    def forbidden(*args, **kwargs):
        raise AssertionError('scalar parser cache traversed nested evidence')

    monkeypatch.setattr(version_module, '_builtin_size', forbidden)
    bars = market_prefix(owner.bars, 4)
    assert cache.prepare(source, bars, _MarketPrefixIndex(owner, bars)) is None
    assert cache.source is None and cache.memory_used == 0
    assert cache._rejected is not None
    appended = PointVersion([_point(1)], previous=source, start=1).seal()
    assert cache.prepare(appended, bars, _MarketPrefixIndex(owner, bars)) is None
    expected = prepare_confirmation_context(source, bars, 3, _market_index=_MarketPrefixIndex(owner, bars))
    assert len(expected.references) == 1
    assert source[0]['evidence'] is point['evidence']
