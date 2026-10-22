"""未决资格与候选状态分离；每次与原发布循环核对完整证据和首知日。"""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from wavequant.domain.market_structure import trend_publication as publication
from wavequant.domain.market_structure.trend_market_index import (
    _MarketPrefixIndex, _MarketRangeIndex, market_prefix,
)
from wavequant.domain.market_structure.trend_point_version import PointVersion
from wavequant.domain.market_structure.trend_publication_cache import _PublicationCache
from wavequant.domain.models.model import Bar


def _point(index: int, kind: str, value: float, *, known: int | None = None) -> dict[str, object]:
    return dict(index=index, ordinal=0, kind=kind, value=value, label=kind,
                time=(datetime(2026, 1, 1) + timedelta(days=index)).date().isoformat(),
                available_at=index if known is None else known, state='confirmed')


def _inputs(level: int = 1):
    prices = ((20., 10., 15.), (10., 5., 6.), (18., 8., 10.), (10., 6., 7.),
              (17., 7., 10.), (19., 8., 18.5), (19.5, 9., 19.), (21., 10., 20.5))
    owner = _MarketRangeIndex(tuple(
        Bar(datetime(2026, 1, 1) + timedelta(days=index), 'DEPENDENCIES',
            (high + low) / 2, high, low, close, 100.)
        for index, (high, low, close) in enumerate(prices)))
    source = PointVersion([_point(0, 'H', 20.), _point(1, 'L', 5.),
                           _point(2, 'H', 18.), _point(3, 'L', 6.)]).seal()
    field = 'source_turn_position' if level == 1 else f'source_level{level - 1}_position'
    candidates = PointVersion(dict(point, **{field: index}, confirmation_rule='structure')
                              for index, point in enumerate(source)).seal()
    return owner, source, candidates


def _publish(cache, owner, stop, source, candidates, *, level=1, readonly=True, enabled=False):
    market = market_prefix(owner.bars, stop)
    return publication.publish_uptrends(
        candidates, source, market, source_level=level - 1,
        _market_index=_MarketPrefixIndex(owner, market), _publication_cache=cache,
        _readonly_result=readonly, n_target_trend_confirmation_enabled=enabled)


@pytest.mark.parametrize('level', (1, 2, 3))
def test_all_unresolved_queries_are_rechecked_without_replaying_unchanged_candidates(level):
    owner, source, candidates = _inputs(level)
    cache = _PublicationCache()
    first = _publish(cache, owner, 4, source, candidates, level=level)
    assert first == _publish(None, owner, 4, source, candidates, level=level)
    assert cache.checkpoint.offset == len(candidates)
    frozen = deepcopy(first)
    queried = []
    qualify = publication.qualify_uptrend

    def record(source, origin, position, *args, **kwargs):
        queried.append(position)
        return qualify(source, origin, position, *args, **kwargs)

    expected = _publish(None, owner, 5, source, candidates, level=level)
    with patch.object(publication, 'qualify_uptrend', side_effect=record), patch.object(
        publication._PublishedTrendState, 'annotate', side_effect=AssertionError('old candidate replayed'),
    ):
        actual = _publish(cache, owner, 5, source, candidates, level=level)
    assert actual == expected
    assert queried == [1, 3]
    assert cache.stats['captures'] == len(candidates)
    assert first == frozen

    # 后一个 L 先突破自己的 18 压力；最早 L 的 20 压力仍未突破。
    for stop in (6, 7, 8):
        actual = _publish(cache, owner, stop, source, candidates, level=level)
        assert actual == _publish(None, owner, stop, source, candidates, level=level)
    assert cache.stats['qualification_rewinds'] >= 2
    assert first == frozen


def test_new_permanent_rejections_stop_rechecking_without_replaying_suffix():
    owner, source, candidates = _inputs()
    bars = tuple(replace(bar, low=4.) if index == 4 else bar for index, bar in enumerate(owner.bars))
    owner = _MarketRangeIndex(bars)
    cache = _PublicationCache()
    _publish(cache, owner, 4, source, candidates)
    expected = _publish(None, owner, 5, source, candidates)
    with patch.object(publication._PublishedTrendState, 'annotate',
                      side_effect=AssertionError('unchanged failure replayed old candidates')):
        assert _publish(cache, owner, 5, source, candidates) == expected
    assert cache.stats['final_rejections'] == 2
    assert not cache._pending
    with patch.object(publication, 'qualify_uptrend', side_effect=AssertionError('final failure rechecked')):
        assert _publish(cache, owner, 8, source, candidates) == expected


@pytest.mark.parametrize('revision', ('label', 'knowledge', 'state', 'truncate'))
def test_source_tail_revision_restores_only_the_affected_candidate_prefix(revision):
    owner, source, candidates = _inputs()
    cache = _PublicationCache()
    first = _publish(cache, owner, 4, source, candidates)
    saved = deepcopy(first)
    if revision == 'truncate':
        revised = PointVersion(previous=source, start=3).seal()
        with pytest.raises(ValueError, match='actual source origin'):
            _publish(cache, owner, 5, revised, candidates)
        with pytest.raises(ValueError, match='actual source origin'):
            _publish(None, owner, 5, revised, candidates)
    else:
        change = ({'label': 'revised-low'} if revision == 'label' else
                  {'available_at': 4} if revision == 'knowledge' else {'state': 'developing'})
        revised = PointVersion([dict(source[3], **change)], previous=source, start=3).seal()
        assert _publish(cache, owner, 5, revised, candidates) == _publish(None, owner, 5, revised, candidates)
        assert cache.stats['invalidated_candidates'] == 1
    assert first == saved


def test_later_source_fact_does_not_invalidate_an_earlier_first_certificate():
    owner, source, candidates = _inputs()
    cache = _PublicationCache()
    _publish(cache, owner, 6, source, candidates)
    captures = cache.stats['captures']
    revised = PointVersion([_point(6, 'H', 19.5)], previous=source, start=len(source)).seal()
    assert _publish(cache, owner, 7, revised, candidates) == _publish(None, owner, 7, revised, candidates)
    assert cache.stats['captures'] == captures


@pytest.mark.parametrize('revision', ('visible_append', 'backdated_state', 'candidate_lookahead', 'candidate_retract'))
def test_retroactive_dependency_changes_replay_the_original_rule_loop(revision):
    owner, source, candidates = _inputs()
    cache = _PublicationCache()
    _publish(cache, owner, 8, source, candidates)
    if revision == 'visible_append':
        source = PointVersion([_point(4, 'H', 17., known=5)], previous=source, start=4).seal()
    elif revision == 'backdated_state':
        source = PointVersion([dict(source[2], state='developing'), source[3]], previous=source, start=2).seal()
    elif revision == 'candidate_lookahead':
        candidates = PointVersion([dict(candidates[2], label='lookahead-key'), candidates[3]],
                                  previous=candidates, start=2).seal()
    else:
        candidates = PointVersion(previous=candidates, start=2).seal()
    assert _publish(cache, owner, 8, source, candidates) == _publish(None, owner, 8, source, candidates)
    assert cache.stats['input_rewinds'] >= 1


def test_exhausted_lookahead_detects_new_candidate_even_when_prior_candidate_is_unchanged():
    owner, source, candidates = _inputs()
    source = PointVersion(previous=source, start=2).seal()
    candidates = PointVersion(previous=candidates, start=2).seal()
    cache = _PublicationCache()
    _publish(cache, owner, 8, source, candidates)
    new_source = PointVersion([_point(2, 'H', 18.), _point(3, 'L', 6.)], previous=source, start=2).seal()
    new_candidates = PointVersion([
        dict(new_source[2], source_turn_position=2, confirmation_rule='structure'),
        dict(new_source[3], source_turn_position=3, confirmation_rule='structure'),
    ], previous=candidates, start=2).seal()
    assert _publish(cache, owner, 8, new_source, new_candidates) == _publish(
        None, owner, 8, new_source, new_candidates)
    assert cache.stats['input_rewinds'] >= 1


def test_previously_missing_continuation_source_is_a_real_dependency():
    prices = ((10., 8., 9.), (8., 5., 6.), (9., 6., 8.), (11., 7., 10.5),
              (12., 8., 11.), (13., 9., 12.), (14., 10., 13.))
    owner = _MarketRangeIndex(tuple(
        Bar(datetime(2026, 1, 1) + timedelta(days=index), 'MISSING',
            (high + low) / 2, high, low, close, 100.)
        for index, (high, low, close) in enumerate(prices)))
    source = PointVersion([_point(0, 'H', 10.), _point(1, 'L', 5.)]).seal()
    highs = [_point(4, 'H', 12.), _point(5, 'H', 13.)]
    candidates = PointVersion(dict(point, source_turn_position=index, confirmation_rule='structure')
                              for index, point in enumerate([*source, *highs])).seal()
    cache = _PublicationCache()
    first = _publish(cache, owner, 6, source, candidates)
    assert first == _publish(None, owner, 6, source, candidates)
    revised = PointVersion(highs, previous=source, start=2).seal()
    actual = _publish(cache, owner, 7, revised, candidates)
    assert actual == _publish(None, owner, 7, revised, candidates)
    assert first[-1]['index'] == 4 and actual[-1]['index'] == 5
    assert cache.stats['invalidated_candidates'] == 1


def test_budget_refusal_does_not_repeat_snapshot_admission_on_every_date():
    owner, source, candidates = _inputs()
    candidates = PointVersion(dict(point, audit=b'x' * 131_072) for point in candidates).seal()
    cache = _PublicationCache(memory_bytes=32_768)
    assert _publish(cache, owner, 4, source, candidates) == _publish(None, owner, 4, source, candidates)
    assert cache.checkpoint is None and cache._blocked
    with patch.object(cache, '_new_graph', side_effect=AssertionError('over-budget history scanned again')):
        for stop in (5, 6, 7, 8):
            assert _publish(cache, owner, stop, source, candidates) == _publish(None, owner, stop, source, candidates)
    assert cache.stats['budget_rejections'] == 1


def test_public_mutable_inputs_and_n_mode_keep_the_original_path():
    owner, source, candidates = _inputs()
    for readonly, enabled, mutable in ((False, False, False), (True, True, False), (True, False, True)):
        cache = _PublicationCache()
        actual_source = list(source) if mutable else source
        actual_candidates = list(candidates) if mutable else candidates
        actual = _publish(cache, owner, 6, actual_source, actual_candidates, readonly=readonly, enabled=enabled)
        assert actual == _publish(None, owner, 6, actual_source, actual_candidates, readonly=readonly, enabled=enabled)
        assert cache.checkpoint is None


def test_unknown_snapshot_values_are_rejected_without_invoking_size_or_class_hooks():
    class UnknownMeta(type):
        def __getattribute__(cls, name):
            if name == '__name__':
                raise AssertionError('unknown class hook invoked')
            return super().__getattribute__(name)

    class Unknown(metaclass=UnknownMeta):
        def __sizeof__(self):
            raise AssertionError('unknown size hook invoked')

        def __reduce_ex__(self, protocol):
            raise AssertionError('unknown reduce hook invoked')

        def __deepcopy__(self, memo):
            raise AssertionError('unknown deepcopy hook invoked')

    cache = _PublicationCache()
    assert cache._new_graph([{'audit': Unknown()}], cache.limit) == 'unknown'
    assert not cache._seen


@pytest.mark.parametrize('level', (1, 2, 3))
def test_date_clock_preserves_first_known_proofs_and_rewind(level):
    owner, source, candidates = _inputs(level)
    source = PointVersion(dict(point, available_at=owner.bars[point['available_at']].timestamp.date().isoformat())
                          for point in source).seal()
    candidates = PointVersion(dict(point, available_at=owner.bars[point['available_at']].timestamp.date().isoformat())
                              for point in candidates).seal()
    cache = _PublicationCache()
    for stop in (4, 5, 6, 8, 4, 7):
        assert _publish(cache, owner, stop, source, candidates, level=level) == _publish(
            None, owner, stop, source, candidates, level=level)
