"""缓存拒绝不能造成同次双重解析，也不能丢掉可容纳的稳定前缀。"""

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from sys import getsizeof
from weakref import ref

import pytest

from wavequant.domain.market_structure import trend_confirmation as confirmation
from wavequant.domain.market_structure.trend_market_index import _MarketPrefixIndex, _MarketRangeIndex
from wavequant.domain.market_structure.trend_point_version import PointVersion
from wavequant.domain.models.model import Bar


START = datetime(2024, 1, 1)


class _Channel:
    pass


def _bars(count: int) -> tuple[Bar, ...]:
    return tuple(Bar(START + timedelta(days=index), 'sz.test', 9., 10., 8., 9., 100)
                 for index in range(count))


def _point(index: int, *, date_clock: bool = False) -> dict[str, object]:
    day = (START + timedelta(days=index)).date().isoformat()
    return dict(index=index, ordinal=0, time=day, kind='H' if index % 2 == 0 else 'L',
                value=10. if index % 2 == 0 else 8., available_at=day if date_clock else index,
                label=f'point {index}', state='confirmed')


def _context(source: Sequence[Mapping[str, object]], bars: tuple[Bar, ...], owner: _MarketRangeIndex,
             end: int, channel: object | None = None) -> confirmation.ConfirmationContext:
    prefix = bars[:end + 1]
    return confirmation.prepare_confirmation_context(source, prefix, end,
        _market_index=_MarketPrefixIndex(owner, prefix), _reference_channel=channel)


def _same(actual: confirmation.ConfirmationContext, expected: confirmation.ConfirmationContext) -> None:
    assert list(actual.references) == list(expected.references)
    assert actual.ordered == expected.ordered
    for name in ('_prefix_visibility', '_proof_prefixes', '_reference_stops'):
        left, right = getattr(actual, name), getattr(expected, name)
        assert (list(left) if left is not None else None) == (list(right) if right is not None else None)
    assert actual._visible_stop == expected._visible_stop


@pytest.mark.parametrize('date_clock', (False, True))
def test_over_budget_context_parses_once_and_retains_only_affordable_prefix(
    date_clock: bool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    bars = _bars(48)
    owner, cold = _MarketRangeIndex.build(bars), _MarketRangeIndex.build(bars)
    assert owner is not None and cold is not None
    cold.reference_reuse = object()
    cache, channel = confirmation._ReferenceParsingCache(), _Channel()
    owner.reference_reuse = cache
    monkeypatch.setattr(confirmation, '_REFERENCE_DATA_BYTES', 40_000)
    parsed: list[Mapping[str, object]] = []
    original = cache.parse

    def counted(raw: Mapping[str, object], sessions: Mapping[str, int], end: int,
                *, _versioned: bool = False) -> confirmation._Reference | None:
        parsed.append(raw)
        return original(raw, sessions, end, _versioned=_versioned)

    monkeypatch.setattr(cache, 'parse', counted)
    source = PointVersion(_point(index, date_clock=date_clock) for index in range(32)).seal()
    initial = _context(source, bars, owner, 32, channel)
    _same(initial, _context(source, bars, cold, 32))
    assert len(parsed) == len(source)
    saved = cache.versions[channel]
    retained = len(saved.references)
    assert 0 < retained < len(source)
    assert saved.references is not initial.references
    assert cache.version_bytes + cache.memory_bytes + 2 * getsizeof(cache.entries) <= 40_000
    full_references = ref(initial.references)
    del initial
    assert full_references() is None

    parsed.clear()
    unchanged = _context(source, bars, owner, 33, channel)
    assert parsed == list(source.iter_range(retained))
    _same(unchanged, _context(source, bars, cold, 33))
    retained = len(cache.versions[channel].references)
    parsed.clear()
    appended = PointVersion([_point(34, date_clock=date_clock)], previous=source, start=len(source)).seal()
    _same(_context(appended, bars, owner, 34, channel), _context(appended, bars, cold, 34))
    assert parsed == list(appended.iter_range(retained))
    assert cache.version_bytes + cache.memory_bytes + 2 * getsizeof(cache.entries) <= 40_000


@pytest.mark.parametrize('revision', ('label', 'clock', 'alias', 'truncate', 'branch'))
def test_retained_prefix_invalidates_from_the_real_revision(
    revision: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    bars = _bars(48)
    owner, cold = _MarketRangeIndex.build(bars), _MarketRangeIndex.build(bars)
    assert owner is not None and cold is not None
    cold.reference_reuse = object()
    channel = _Channel()
    monkeypatch.setattr(confirmation, '_REFERENCE_DATA_BYTES', 40_000)
    source = PointVersion(_point(index, date_clock=True) for index in range(32)).seal()
    old = _context(source, bars, owner, 32, channel)
    old_prefixes = list(old._proof_prefixes or ())
    suffix = [dict(point) for point in source.iter_range(1)]
    if revision == 'label':
        suffix[0]['label'] = 'revised at the same coordinate'
    elif revision == 'clock':
        suffix[0]['available_at'] = '2024-01-03'
    elif revision == 'alias':
        suffix[0]['available_at'] = bytearray(str(suffix[0]['time']).encode()).decode()
        assert suffix[0]['time'] is not suffix[0]['available_at']
    elif revision == 'truncate':
        suffix = []
    else:
        intermediate = PointVersion([_point(33)], previous=source, start=len(source)).seal()
        _context(intermediate, bars, owner, 33, channel)
        suffix[0]['value'] = 8.5
    revised = PointVersion(suffix, previous=source, start=1).seal()
    actual = _context(revised, bars, owner, 34, channel)
    _same(actual, _context(revised, bars, cold, 34))
    assert list(old._proof_prefixes or ()) == old_prefixes
    _same(_context(revised, bars, owner, 20, channel), _context(revised, bars, cold, 20))


def test_public_mutable_source_is_revalidated_after_cached_internal_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bars = _bars(48)
    owner, cold = _MarketRangeIndex.build(bars), _MarketRangeIndex.build(bars)
    assert owner is not None and cold is not None
    cold.reference_reuse = object()
    channel = _Channel()
    monkeypatch.setattr(confirmation, '_REFERENCE_DATA_BYTES', 40_000)
    source = PointVersion(_point(index) for index in range(32)).seal()
    _context(source, bars, owner, 32, channel)
    mutable = [dict(point) for point in source]
    before = _context(mutable, bars, owner, 33, channel)
    mutable[0]['value'] = float('inf')
    after = _context(mutable, bars, owner, 34, channel)
    assert before.references[0] is not None and after.references[0] is None
    _same(after, _context(mutable, bars, cold, 34))


def test_multiple_channels_charge_the_actual_version_table_capacity(monkeypatch: pytest.MonkeyPatch) -> None:
    bars = _bars(48)
    owner = _MarketRangeIndex.build(bars)
    assert owner is not None
    monkeypatch.setattr(confirmation, '_REFERENCE_DATA_BYTES', 40_000)
    source = PointVersion(_point(index) for index in range(32)).seal()
    channels = [_Channel() for _ in range(20)]
    for channel in channels:
        result = _context(source, bars, owner, 40, channel)
        assert len(result.references) == 32
        cache = owner.reference_reuse
        assert isinstance(cache, confirmation._ReferenceParsingCache)
        assert cache.version_bytes + cache.memory_bytes + 2 * getsizeof(cache.entries) <= 40_000


def test_unchanged_affordable_prefix_does_not_rebuild_six_trees_each_day(monkeypatch: pytest.MonkeyPatch) -> None:
    bars = _bars(48)
    owner = _MarketRangeIndex.build(bars)
    assert owner is not None
    channel = _Channel()
    monkeypatch.setattr(confirmation, '_REFERENCE_DATA_BYTES', 40_000)
    source = PointVersion(_point(index) for index in range(32)).seal()
    _context(source, bars, owner, 32, channel)
    cache = owner.reference_reuse
    assert isinstance(cache, confirmation._ReferenceParsingCache)
    retained = cache.versions[channel]
    copied: list[int] = []
    original = cache._prefix_version

    def counted(version: confirmation._ConfirmationVersion, length: int) -> confirmation._ConfirmationVersion:
        copied.append(length)
        return original(version, length)

    monkeypatch.setattr(cache, '_prefix_version', counted)
    for end in (33, 34, 35):
        context = _context(source, bars, owner, end, channel)
        assert len(context.references) == len(source)
        assert cache.versions[channel].references is retained.references
    assert copied == []
    suffix = [dict(point) for point in source.iter_range(1)]
    suffix[0]['label'] = 'new label'
    revised = PointVersion(suffix, previous=source, start=1).seal()
    _context(revised, bars, owner, 36, channel)
    assert copied == [len(retained.references)]


def test_released_capacity_is_used_for_the_complete_version(monkeypatch: pytest.MonkeyPatch) -> None:
    bars = _bars(48)
    owner = _MarketRangeIndex.build(bars)
    assert owner is not None
    channel, other = _Channel(), _Channel()
    source = PointVersion(_point(index) for index in range(16)).seal()
    other_source = PointVersion(_point(index) for index in range(8)).seal()
    market = _MarketPrefixIndex(owner, bars)
    sessions = confirmation._PrefixSessions(owner.positions, 47)
    probe = confirmation._ReferenceParsingCache()
    full = probe.version(channel, source, market, sessions, 47)
    occupied = probe.version(other, other_source, market, sessions, 47)
    assert full is not None and occupied is not None
    limit = full.size + occupied.size - 2000
    monkeypatch.setattr(confirmation, '_REFERENCE_DATA_BYTES', limit)
    cache = confirmation._ReferenceParsingCache()
    owner.reference_reuse = cache
    assert cache.version(other, other_source, market, sessions, 47) is not None
    _context(source, bars, owner, 47, channel)
    assert 0 < len(cache.versions[channel].references) < len(source)
    cache.versions.pop(other)
    context = _context(source, bars, owner, 47, channel)
    assert cache.versions[channel].references is context.references
    assert len(context.references) == len(source)
    assert cache.version_bytes + cache.memory_bytes + 2 * getsizeof(cache.entries) <= limit
