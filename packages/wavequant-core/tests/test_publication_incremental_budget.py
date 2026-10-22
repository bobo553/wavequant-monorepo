"""发布缓存只拥有标量输入索引，旧证明和已失效版本不被预算账本保留。"""

import gc
from datetime import datetime, timedelta
from weakref import ref

import pytest

from wavequant.domain.market_structure import trend_publication as publication
from wavequant.domain.market_structure import trend_publication_cache as budget
from wavequant.domain.market_structure.trend_frozen_state import FrozenPrefixList
from wavequant.domain.market_structure.trend_market_index import (
    _MarketPrefixIndex, _MarketRangeIndex, market_prefix,
)
from wavequant.domain.market_structure.trend_point_version import PointVersion
from wavequant.domain.models.model import Bar


def _owner() -> _MarketRangeIndex:
    prices = ((10., 8., 9.), (8., 5., 6.), (9., 6., 8.), (11., 7., 10.5),
              (12., 8., 11.), (13., 9., 12.), (14., 10., 13.))
    return _MarketRangeIndex(tuple(
        Bar(datetime(2026, 1, 1) + timedelta(days=index), 'BUDGET',
            (high + low) / 2, high, low, close, 100.)
        for index, (high, low, close) in enumerate(prices)))


def _point(index: int, kind: str, value: float, known: int | None = None) -> dict[str, object]:
    return dict(index=index, ordinal=0, kind=kind, value=value,
                time=(datetime(2026, 1, 1) + timedelta(days=index)).date().isoformat(),
                available_at=index if known is None else known, state='confirmed', label=kind)


def _versions(level: int = 1) -> tuple[PointVersion, PointVersion]:
    source = PointVersion([_point(0, 'H', 10.), _point(1, 'L', 5.)]).seal()
    field = 'source_turn_position' if level == 1 else f'source_level{level - 1}_position'
    candidates = PointVersion(dict(point, **{field: index}, confirmation_rule='structure')
                              for index, point in enumerate(source)).seal()
    return source, candidates


def _market(owner: _MarketRangeIndex, stop: int) -> _MarketPrefixIndex:
    return _MarketPrefixIndex(owner, market_prefix(owner.bars, stop))


def _publish(cache: budget._PublicationCache | None, owner: _MarketRangeIndex, stop: int,
             source: PointVersion, candidates: PointVersion, level: int = 1) -> list[dict[str, object]]:
    index = _market(owner, stop)
    return publication.publish_uptrends(candidates, source, index.bars, source_level=level - 1,
        _market_index=index, _publication_cache=cache, n_target_trend_confirmation_enabled=False)


def test_borrowed_input_proofs_do_not_consume_publication_owned_budget() -> None:
    owner = _owner()
    source, candidates = _versions()
    large = {'audit': b'x' * 131_072}
    source = PointVersion(dict(point, proof=large) for point in source).seal()
    candidates = PointVersion(dict(point, proof=large) for point in candidates).seal()
    cache = budget._PublicationCache(memory_bytes=16_384)

    assert cache.admit(candidates, source, _market(owner, 4), 0)
    assert cache._input_bytes < cache.limit
    source_ref, candidates_ref = ref(source), ref(candidates)
    del source, candidates
    gc.collect()
    assert source_ref() is None and candidates_ref() is None
    assert cache._source is None and cache._candidates is None


@pytest.mark.parametrize('level', (1, 2, 3))
def test_collected_parent_version_retains_causal_append_frontier(
    monkeypatch: pytest.MonkeyPatch, level: int,
) -> None:
    owner = _owner()
    source, candidates = _versions(level)
    cache = budget._PublicationCache()
    _publish(cache, owner, 4, source, candidates, level)
    assert cache.checkpoint is not None and cache.checkpoint.offset == 2
    prefix_ids = {id(point) for version in (source, candidates) for point in version}
    parent_refs = ref(source), ref(candidates)
    point = _point(4, 'H', 12.)
    field = 'source_turn_position' if level == 1 else f'source_level{level - 1}_position'
    source = PointVersion([point], previous=source, start=2).seal()
    candidates = PointVersion([dict(point, **{field: 2}, confirmation_rule='structure')],
                              previous=candidates, start=2).seal()
    gc.collect()
    assert all(parent() is None for parent in parent_refs)
    expected = _publish(None, owner, 5, source, candidates, level)
    known = budget._PublicationCache._known

    def only_new_points(point: dict[str, object], market: _MarketPrefixIndex) -> int | None:
        assert id(point) not in prefix_ids, 'unchanged input prefix was validated again'
        return known(point, market)

    monkeypatch.setattr(budget._PublicationCache, '_known', staticmethod(only_new_points))
    assert _publish(cache, owner, 5, source, candidates, level) == expected


def test_backdated_append_invalidates_checkpoint_after_parent_collection() -> None:
    owner = _owner()
    source, candidates = _versions()
    cache = budget._PublicationCache()
    _publish(cache, owner, 4, source, candidates)
    assert cache.checkpoint is not None
    point = _point(2, 'H', 9., known=3)
    parents = ref(source), ref(candidates)
    source = PointVersion([point], previous=source, start=2).seal()
    candidates = PointVersion([dict(point, source_turn_position=2, confirmation_rule='structure')],
                              previous=candidates, start=2).seal()
    gc.collect()
    assert all(parent() is None for parent in parents)
    assert _publish(cache, owner, 5, source, candidates) == _publish(None, owner, 5, source, candidates)
    assert cache.checkpoint is None


def test_lost_nonparent_version_requires_full_scalar_validation() -> None:
    owner = _owner()
    source, candidates = _versions()
    cache = budget._PublicationCache()
    _publish(cache, owner, 4, source, candidates)
    parent_ref = ref(source)
    intermediate = PointVersion(previous=source, start=len(source)).seal()
    source = PointVersion(previous=intermediate, start=len(intermediate)).seal()
    del intermediate
    gc.collect()
    assert parent_ref() is None
    # No retained source tree establishes an unchanged prefix across the gap.
    assert _publish(cache, owner, 5, source, candidates) == _publish(None, owner, 5, source, candidates)
    assert cache.checkpoint is None


def test_owned_checkpoint_proofs_still_obey_original_memory_limit() -> None:
    owner = _owner()
    source, candidates = _versions()
    cache = budget._PublicationCache(memory_bytes=16_384)
    assert cache.admit(candidates, source, _market(owner, 4), 0)
    rows = FrozenPrefixList([dict(source[0], proof={'audit': b'x' * 131_072})])
    rows.freeze(len(rows))
    checkpoint = budget._PublicationCheckpoint(1, 3, rows, None, None, None, None, None, None)
    cache.save(checkpoint)
    assert cache.checkpoint is None
    assert cache._input_bytes == 0


def test_scalar_metadata_and_rewind_remain_bounded() -> None:
    owner = _owner()
    source, candidates = _versions()
    cache = budget._PublicationCache(memory_bytes=1)
    assert not cache.admit(candidates, source, _market(owner, 4), 0)
    assert cache._source is None and cache.checkpoint is None
    cache = budget._PublicationCache()
    _publish(cache, owner, 5, source, candidates)
    assert _publish(cache, owner, 2, source, candidates) == _publish(None, owner, 2, source, candidates)
