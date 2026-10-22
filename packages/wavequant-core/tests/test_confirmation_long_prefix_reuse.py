"""长前缀在原缓存额度内逐日复用，修订与历史投影保留冷计算语义。"""

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from sys import getsizeof

import pytest

from wavequant.domain.market_structure import trend_confirmation as confirmation
from wavequant.domain.market_structure.trend_market_index import _MarketPrefixIndex, _MarketRangeIndex
from wavequant.domain.market_structure.trend_point_version import PointVersion
from wavequant.domain.market_structure.trend_publication import _PublishedTrendState
from wavequant.domain.market_structure.trend_publication_cache import _PublicationCache
from wavequant.domain.models.model import Bar


START = datetime(2016, 1, 1)
PREFIX_LENGTH = 2125


def _bars(count: int) -> tuple[Bar, ...]:
    return tuple(Bar(START + timedelta(days=index), 'sz.test', 9., 10., 8., 9., 100)
                 for index in range(count))


def _point(index: int, *, date_clock: bool) -> dict[str, object]:
    day = (START + timedelta(days=index)).date().isoformat()
    return dict(index=index, ordinal=0, time=day, kind='H' if index % 2 == 0 else 'L',
                value=10. if index % 2 == 0 else 8., available_at=day if date_clock else index,
                label=f'point {index}', state='confirmed')


def _state(
    source: Sequence[Mapping[str, object]], bars: tuple[Bar, ...], owner: _MarketRangeIndex,
    end: int, channel: _PublicationCache,
) -> _PublishedTrendState:
    prefix = bars[:end + 1]
    return _PublishedTrendState(source, prefix, 0, _market_index=_MarketPrefixIndex(owner, prefix),
                                _reference_channel=channel)


def _cold_context(
    source: Sequence[Mapping[str, object]], bars: tuple[Bar, ...], owner: _MarketRangeIndex, end: int,
) -> confirmation.ConfirmationContext:
    prefix = bars[:end + 1]
    return confirmation.prepare_confirmation_context(source, prefix, end,
        _market_index=_MarketPrefixIndex(owner, prefix))


def _same(actual: confirmation.ConfirmationContext, expected: confirmation.ConfirmationContext) -> None:
    assert list(actual.references) == list(expected.references)
    assert actual.ordered == expected.ordered
    assert list(actual._prefix_visibility or ()) == list(expected._prefix_visibility or ())
    assert list(actual._proof_prefixes or ()) == list(expected._proof_prefixes or ())
    assert list(actual._reference_stops or ()) == list(expected._reference_stops or ())
    assert actual._visible_stop == expected._visible_stop


@pytest.fixture
def parsed_references(monkeypatch: pytest.MonkeyPatch) -> list[Mapping[str, object]]:
    parsed: list[Mapping[str, object]] = []
    original = confirmation._ReferenceParsingCache.parse

    def counted(
        self: confirmation._ReferenceParsingCache, raw: Mapping[str, object],
        sessions: Mapping[str, int], end_index: int, *, _retain: bool = True,
    ) -> confirmation._Reference | None:
        parsed.append(raw)
        return original(self, raw, sessions, end_index, _retain=_retain)

    monkeypatch.setattr(confirmation._ReferenceParsingCache, 'parse', counted)
    return parsed


@pytest.mark.parametrize('date_clock', (False, True))
def test_long_prefix_with_other_channels_only_parses_daily_append_for_twenty_sessions(
    date_clock: bool, parsed_references: list[Mapping[str, object]],
) -> None:
    bars = _bars(PREFIX_LENGTH + 20)
    owner, cold = _MarketRangeIndex.build(bars), _MarketRangeIndex.build(bars)
    assert owner is not None and cold is not None
    # 冷对照完全绕过解析缓存，不能污染下面的工作量计数。
    cold.reference_reuse = object()
    channels = [_PublicationCache() for _ in range(3)]
    sources = [PointVersion(_point(index, date_clock=date_clock) for index in range(count)).seal()
               for count in (PREFIX_LENGTH, 300, 50)]
    initial = [_state(source, bars, owner, PREFIX_LENGTH - 1, channel).confirmation_context(PREFIX_LENGTH - 1)
               for source, channel in zip(sources, channels)]
    assert len(parsed_references) == PREFIX_LENGTH + 300 + 50
    original_references = list(initial[0].references)
    original_digests = list(initial[0]._proof_prefixes or ())
    for source, context in zip(sources, initial):
        _same(context, _cold_context(source, bars, cold, PREFIX_LENGTH - 1))
    parsed_references.clear()

    for day in range(20):
        end = PREFIX_LENGTH + day
        previous = sources[0]
        sources[0] = PointVersion([_point(end, date_clock=date_clock)],
                                 previous=previous, start=len(previous)).seal()
        before = len(parsed_references)
        contexts = [_state(source, bars, owner, end, channel).confirmation_context(end)
                    for source, channel in zip(sources, channels)]
        assert parsed_references[before:] == [sources[0][-1]]
        for source, context in zip(sources, contexts):
            _same(context, _cold_context(source, bars, cold, end))
        cache = owner.reference_reuse
        assert isinstance(cache, confirmation._ReferenceParsingCache)
        assert all(channel in cache.versions for channel in channels)
        assert not cache.rejections
        assert not cache.entries and cache.memory_bytes == 0
        assert (cache.version_bytes + cache.memory_bytes + 2 * getsizeof(cache.entries)
                <= confirmation._REFERENCE_DATA_BYTES)

    assert len(parsed_references) == 20
    assert list(initial[0].references) == original_references
    assert list(initial[0]._proof_prefixes or ()) == original_digests


@pytest.mark.parametrize('date_clock', (False, True))
def test_long_prefix_revision_reparses_real_suffix_and_truncation_preserves_old_snapshot(
    date_clock: bool, parsed_references: list[Mapping[str, object]],
) -> None:
    bars = _bars(PREFIX_LENGTH + 3)
    owner, cold = _MarketRangeIndex.build(bars), _MarketRangeIndex.build(bars)
    assert owner is not None and cold is not None
    cold.reference_reuse = object()
    channel = _PublicationCache()
    source = PointVersion(_point(index, date_clock=date_clock) for index in range(PREFIX_LENGTH)).seal()
    old = _state(source, bars, owner, PREFIX_LENGTH - 1, channel).confirmation_context(PREFIX_LENGTH - 1)
    old_references, old_digests = list(old.references), list(old._proof_prefixes or ())
    parsed_references.clear()
    revision_start = 1025
    suffix = [dict(point) for point in source.iter_range(revision_start)]
    suffix[0]['label'] = 'revised old point'
    revised = PointVersion(suffix, previous=source, start=revision_start).seal()

    updated = _state(revised, bars, owner, PREFIX_LENGTH, channel).confirmation_context(PREFIX_LENGTH)

    assert parsed_references == list(revised.iter_range(revision_start))
    _same(updated, _cold_context(revised, bars, cold, PREFIX_LENGTH))
    assert updated.references[revision_start] is not None
    assert updated._proof_prefixes is not None
    assert list(updated._proof_prefixes[:revision_start + 1]) == old_digests[:revision_start + 1]
    assert updated._proof_prefixes[-1] != old_digests[-1]
    assert list(old.references) == old_references
    assert list(old._proof_prefixes or ()) == old_digests
    _same(old, _cold_context(source, bars, cold, PREFIX_LENGTH - 1))
    parsed_references.clear()
    truncate_at = 2060
    truncated = PointVersion(previous=revised, start=truncate_at).seal()

    shortened = _state(truncated, bars, owner, PREFIX_LENGTH + 1, channel).confirmation_context(PREFIX_LENGTH + 1)

    assert parsed_references == []
    assert len(shortened.references) == truncate_at
    _same(shortened, _cold_context(truncated, bars, cold, PREFIX_LENGTH + 1))
    restored = PointVersion([_point(truncate_at, date_clock=date_clock)],
                            previous=truncated, start=truncate_at).seal()
    latest = _state(restored, bars, owner, PREFIX_LENGTH + 2, channel).confirmation_context(PREFIX_LENGTH + 2)
    assert parsed_references == [restored[-1]]
    _same(latest, _cold_context(restored, bars, cold, PREFIX_LENGTH + 2))
    assert len(shortened.references) == truncate_at
    _same(updated, _cold_context(revised, bars, cold, PREFIX_LENGTH))
    assert list(old.references) == old_references
    assert list(old._proof_prefixes or ()) == old_digests


@pytest.mark.parametrize('date_clock', (False, True))
def test_long_prefix_historical_cutoff_hides_future_knowledge_without_mutating_latest(
    date_clock: bool, parsed_references: list[Mapping[str, object]],
) -> None:
    full_end = PREFIX_LENGTH + 5
    cutoff = PREFIX_LENGTH + 2
    bars = _bars(full_end + 1)
    owner, cold = _MarketRangeIndex.build(bars), _MarketRangeIndex.build(bars)
    assert owner is not None and cold is not None
    cold.reference_reuse = object()
    source = PointVersion(_point(index, date_clock=date_clock) for index in range(PREFIX_LENGTH)).seal()
    future = _point(PREFIX_LENGTH, date_clock=date_clock)
    future['available_at'] = (START + timedelta(days=full_end)).date().isoformat() if date_clock else full_end
    latest_source = PointVersion([future], previous=source, start=len(source)).seal()
    channel = _PublicationCache()
    state = _state(latest_source, bars, owner, full_end, channel)
    latest = state.confirmation_context(full_end)
    latest_references = list(latest.references)
    latest_digests = list(latest._proof_prefixes or ())
    _same(latest, _cold_context(latest_source, bars, cold, full_end))
    parsed_references.clear()

    for historical_end in (cutoff, PREFIX_LENGTH - 7, cutoff):
        projected = state.confirmation_context(historical_end)
        expected = _cold_context(latest_source, bars, cold, historical_end)
        assert list(projected.references) == list(expected.references)
        assert projected.ordered == expected.ordered
        assert projected.references[-1] is None
        assert all(point is None or point.known is not None and point.known <= historical_end
                   for point in projected.references)
        assert confirmation._SuccessfulConfirmations._prefix(projected, historical_end) == (
            confirmation._SuccessfulConfirmations._prefix(expected, historical_end))

    assert parsed_references == []
    assert state.confirmation_context(full_end) is latest
    assert latest.references[-1] is not None
    assert list(latest.references) == latest_references
    assert list(latest._proof_prefixes or ()) == latest_digests
