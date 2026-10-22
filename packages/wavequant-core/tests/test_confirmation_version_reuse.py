"""冻结来源版本只重解析变化后缀，旧日证据和摘要必须保持原语义。"""

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta

import pytest

from wavequant.domain.market_structure import trend_confirmation as confirmation
from wavequant.domain.market_structure.owned_evidence import CertifiedSource, owned_evidence_scope
from wavequant.domain.market_structure.trend_market_index import _MarketPrefixIndex, _MarketRangeIndex
from wavequant.domain.market_structure.trend_point_version import PointVersion
from wavequant.domain.market_structure.trend_publication import _PublishedTrendState
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


def _context(
    source: Sequence[Mapping[str, object]], bars: tuple[Bar, ...], owner: _MarketRangeIndex,
    end: int, channel: object | None = None,
) -> confirmation.ConfirmationContext:
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
def test_append_and_unchanged_days_only_parse_new_references(
    date_clock: bool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    bars = _bars(80)
    owner, cold = _MarketRangeIndex.build(bars), _MarketRangeIndex.build(bars)
    assert owner is not None and cold is not None
    cold.reference_reuse = object()
    channel = _Channel()
    source = PointVersion(_point(index, date_clock=date_clock) for index in range(64)).seal()
    original = confirmation._ReferenceParsingCache.parse
    parsed: list[Mapping[str, object]] = []

    def counted(self: confirmation._ReferenceParsingCache, raw: Mapping[str, object],
                sessions: Mapping[str, int], end: int, *, _retain: bool = True) -> confirmation._Reference | None:
        parsed.append(raw)
        return original(self, raw, sessions, end, _retain=_retain)

    monkeypatch.setattr(confirmation._ReferenceParsingCache, 'parse', counted)
    first = _context(source, bars, owner, 63, channel)
    _same(first, _context(source, bars, cold, 63))
    assert len(parsed) == 64
    parsed.clear()
    unchanged = _context(source, bars, owner, 64, channel)
    assert parsed == [] and unchanged.references is first.references
    appended = PointVersion([_point(65, date_clock=date_clock)], previous=source, start=64).seal()
    latest = _context(appended, bars, owner, 65, channel)
    assert parsed == [appended[-1]]
    _same(latest, _context(appended, bars, cold, 65))
    assert len(first.references) == 64 and first.references[-1] is not None


@pytest.mark.parametrize('date_clock', (False, True))
def test_versioned_suffix_skips_content_lookup_and_admission_but_public_input_still_validates(
    date_clock: bool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    bars = _bars(8)
    owner = _MarketRangeIndex.build(bars)
    assert owner is not None
    cache, channel = confirmation._ReferenceParsingCache(), _Channel()
    market = _MarketPrefixIndex(owner, bars)
    sessions = confirmation._PrefixSessions(owner.positions, 7)
    signature = confirmation._geometry_signature
    encoded: list[object] = []

    def counted(value: object) -> bytes | None:
        encoded.append(value)
        return signature(value)

    monkeypatch.setattr(confirmation, '_geometry_signature', counted)
    source = PointVersion(_point(index, date_clock=date_clock) for index in range(4)).seal()
    first = cache.version(channel, source, market, sessions, 7)
    assert first is not None
    proofs = [value for value in encoded if type(value) is dict]
    assert len(proofs) == 4 and all('state' not in value for value in proofs)
    assert cache.entries == {} and cache.memory_bytes == 0
    encoded.clear()
    appended = PointVersion([_point(4, date_clock=date_clock)], previous=source, start=4).seal()
    following = cache.version(channel, appended, market, sessions, 7)
    assert following is not None and following.references[-1] is not None
    latest = following.references[-1]
    assert latest is not None
    assert [value for value in encoded if type(value) is dict] == [dict(latest.raw)]
    assert latest._proof_signature == signature(dict(latest.raw))
    assert cache.entries == {} and cache.memory_bytes == 0

    # 公共可变点仍先生成完整内容键；改写同一个字典不能借用版本资格。
    encoded.clear()
    raw = _point(0, date_clock=date_clock)
    assert cache.parse(raw, sessions, 7) is not None
    assert any(type(value) is dict and 'state' in value for value in encoded)
    assert len(cache.entries) == 1
    raw['index'] = True
    assert cache.parse(raw, sessions, 7) is None


@pytest.mark.parametrize('start', (0, 31, 32, 33, 63, 64))
def test_revision_and_truncation_preserve_every_digest_and_old_snapshot(start: int) -> None:
    bars = _bars(72)
    owner, cold = _MarketRangeIndex.build(bars), _MarketRangeIndex.build(bars)
    assert owner is not None and cold is not None
    cold.reference_reuse = object()
    channel = _Channel()
    source = PointVersion(_point(index) for index in range(66)).seal()
    old = _context(source, bars, owner, 66, channel)
    old_digests = list(old._proof_prefixes or ())
    suffix = [dict(point) for point in source.iter_range(start)]
    suffix[0]['label'] = 'revised'
    if len(suffix) > 1:
        suffix[1]['state'] = 'developing'
    revised = PointVersion(suffix, previous=source, start=start).seal()
    _same(_context(revised, bars, owner, 67, channel), _context(revised, bars, cold, 67))
    assert list(old._proof_prefixes or ()) == old_digests
    _same(old, _context(source, bars, cold, 66))
    truncated = PointVersion(previous=revised, start=start).seal()
    _same(_context(truncated, bars, owner, 68, channel), _context(truncated, bars, cold, 68))
    restored = PointVersion([_point(start)], previous=truncated, start=start).seal()
    _same(_context(restored, bars, owner, 69, channel), _context(restored, bars, cold, 69))


@pytest.mark.parametrize('date_clock', (False, True))
def test_future_knowledge_becomes_visible_without_mutating_source(date_clock: bool) -> None:
    bars = _bars(8)
    owner, cold = _MarketRangeIndex.build(bars), _MarketRangeIndex.build(bars)
    assert owner is not None and cold is not None
    cold.reference_reuse = object()
    channel = _Channel()
    future = _point(1, date_clock=date_clock)
    future['available_at'] = '2024-01-06' if date_clock else 5
    source = PointVersion([_point(0, date_clock=date_clock), future]).seal()
    early = _context(source, bars, owner, 3, channel)
    assert early.references[-1] is None
    cache = owner.reference_reuse
    assert isinstance(cache, confirmation._ReferenceParsingCache) and channel not in cache.versions
    visible = _context(source, bars, owner, 5, channel)
    _same(visible, _context(source, bars, cold, 5))
    assert visible.references[-1] is not None and channel in cache.versions
    _same(_context(source, bars, owner, 3, channel), _context(source, bars, cold, 3))
    assert early.references[-1] is None
    prefix = bars[:6]
    market = _MarketPrefixIndex(owner, prefix)
    state = _PublishedTrendState(source, prefix, 0, _market_index=market, _reference_channel=channel)
    projected, expected = state.confirmation_context(3), _context(source, bars, cold, 3)
    assert list(projected.references) == list(expected.references) and projected.ordered == expected.ordered
    assert confirmation._SuccessfulConfirmations._prefix(projected, 3) == (
        confirmation._SuccessfulConfirmations._prefix(expected, 3))


@pytest.mark.parametrize('public_list', (False, True))
def test_public_or_unsealed_input_keeps_original_validation(public_list: bool) -> None:
    bars = _bars(4)
    owner = _MarketRangeIndex.build(bars)
    assert owner is not None
    channel = _Channel()
    source = [_point(0)] if public_list else PointVersion([_point(0)])
    before = _context(source, bars, owner, 1, channel)
    source[0]['label'] = 'changed'
    after = _context(source, bars, owner, 2, channel)
    assert before.references[0] != after.references[0]
    cache = owner.reference_reuse
    assert isinstance(cache, confirmation._ReferenceParsingCache) and channel not in cache.versions


def test_unordered_or_unknown_fields_fall_back_to_complete_parsing() -> None:
    bars = _bars(4)
    owner, cold = _MarketRangeIndex.build(bars), _MarketRangeIndex.build(bars)
    assert owner is not None and cold is not None
    cold.reference_reuse = object()
    channel = _Channel()
    for values in ([_point(1), _point(0)], [dict(_point(0), label=['unknown'])]):
        source = PointVersion(values).seal()
        _same(_context(source, bars, owner, 3, channel), _context(source, bars, cold, 3))
        cache = owner.reference_reuse
        assert isinstance(cache, confirmation._ReferenceParsingCache) and channel not in cache.versions


def test_budget_exhaustion_changes_storage_only() -> None:
    bars = _bars(4)
    owner, cold = _MarketRangeIndex.build(bars), _MarketRangeIndex.build(bars)
    assert owner is not None and cold is not None
    cold.reference_reuse = object()
    channel = _Channel()
    source = PointVersion([dict(_point(0), label='x' * (4 * 1024 * 1024))]).seal()
    _same(_context(source, bars, owner, 3, channel), _context(source, bars, cold, 3))
    cache = owner.reference_reuse
    assert isinstance(cache, confirmation._ReferenceParsingCache) and channel not in cache.versions


def test_rejected_version_drops_checkpoint_and_skips_proven_oversized_descendants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bars = _bars(20)
    owner = _MarketRangeIndex.build(bars)
    assert owner is not None
    cache, channel = confirmation._ReferenceParsingCache(), _Channel()
    market = _MarketPrefixIndex(owner, bars)
    sessions = confirmation._PrefixSessions(owner.positions, len(bars) - 1)
    source = PointVersion(_point(index) for index in range(4)).seal()
    accepted = cache.version(channel, source, market, sessions, 19)
    assert accepted is not None
    monkeypatch.setattr(confirmation, '_REFERENCE_DATA_BYTES', accepted.size + 8000)
    source = PointVersion([dict(_point(4), label='oversized' * 4096)], previous=source, start=4).seal()
    assert cache.version(channel, source, market, sessions, 19) is None
    assert channel not in cache.versions and channel in cache.rejections

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError('a proven oversized version must be rejected before parsing')

    monkeypatch.setattr(cache, 'parse', forbidden)
    assert cache.version(channel, source, market, sessions, 19) is None
    for index in range(5, 10):
        source = PointVersion([_point(index)], previous=source, start=len(source)).seal()
        assert cache.version(channel, source, market, sessions, 19) is None
        rejected = cache.rejections[channel]
        assert rejected.token is source.token and rejected.length == len(source)
        assert channel not in cache.versions


@pytest.mark.parametrize('revision', ('replace', 'truncate', 'date'))
def test_rejected_version_retries_after_its_required_storage_can_shrink(
    revision: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    bars = _bars(20)
    owner = _MarketRangeIndex.build(bars)
    assert owner is not None
    cache, channel = confirmation._ReferenceParsingCache(), _Channel()
    market = _MarketPrefixIndex(owner, bars)
    sessions = confirmation._PrefixSessions(owner.positions, 19)
    source = PointVersion(_point(index) for index in range(4)).seal()
    accepted = cache.version(channel, source, market, sessions, 19)
    assert accepted is not None
    monkeypatch.setattr(confirmation, '_REFERENCE_DATA_BYTES', accepted.size + 8000)
    source = PointVersion([dict(_point(4), label='oversized' * 4096)], previous=source, start=4).seal()
    assert cache.version(channel, source, market, sessions, 19) is None
    if revision == 'replace':
        source = PointVersion([_point(4)], previous=source, start=4).seal()
    elif revision == 'truncate':
        source = PointVersion(previous=source, start=2).seal()
    else:
        # 回退不能沿用较晚日的拒绝下界；未来点仍交由原日期门禁拒绝版本。
        assert cache.version(channel, source, market, confirmation._PrefixSessions(owner.positions, 3), 3) is None
        assert channel not in cache.rejections
        return
    restored = cache.version(channel, source, market, sessions, 19)
    assert restored is not None and channel not in cache.rejections
    assert len(restored.references) == len(source)


def test_rejected_version_retries_when_another_channel_releases_capacity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bars = _bars(20)
    owner = _MarketRangeIndex.build(bars)
    assert owner is not None
    cache, channel, other = confirmation._ReferenceParsingCache(), _Channel(), _Channel()
    market = _MarketPrefixIndex(owner, bars)
    sessions = confirmation._PrefixSessions(owner.positions, 19)
    source = PointVersion(_point(index) for index in range(4)).seal()
    accepted = cache.version(channel, source, market, sessions, 19)
    occupied = cache.version(other, PointVersion(_point(index) for index in range(10)).seal(), market, sessions, 19)
    assert accepted is not None and occupied is not None
    monkeypatch.setattr(confirmation, '_REFERENCE_DATA_BYTES', accepted.size + occupied.size + 2048)
    source = PointVersion([dict(_point(4), label='larger' * 512)], previous=source, start=4).seal()
    assert cache.version(channel, source, market, sessions, 19) is None
    assert channel in cache.rejections and channel not in cache.versions
    cache.versions.pop(other)
    assert cache.version(channel, source, market, sessions, 19) is not None
    assert channel not in cache.rejections


def test_rejection_metadata_is_bounded_without_retaining_source_graphs(monkeypatch: pytest.MonkeyPatch) -> None:
    bars = _bars(2)
    owner = _MarketRangeIndex.build(bars)
    assert owner is not None
    cache = confirmation._ReferenceParsingCache()
    market = _MarketPrefixIndex(owner, bars)
    sessions = confirmation._PrefixSessions(owner.positions, 1)
    monkeypatch.setattr(confirmation, '_REFERENCE_DATA_BYTES', 1024)
    channels = [_Channel() for _ in range(confirmation._REJECTION_LIMIT + 5)]
    for channel in channels:
        assert cache.version(channel, PointVersion([_point(0)]).seal(), market, sessions, 1) is None
    assert len(cache.rejections) == confirmation._REJECTION_LIMIT and not cache.versions
    assert all(rejected.source() is None for rejected in cache.rejections.values())


def test_certified_source_requires_current_owner_and_same_market() -> None:
    bars = _bars(4)
    owner, other = _MarketRangeIndex.build(bars), _MarketRangeIndex.build(bars)
    assert owner is not None and other is not None
    channel = _Channel()
    with owned_evidence_scope() as evidence:
        source = CertifiedSource([_point(0)], [b'point'], evidence, None)
        context = _context(source, bars, owner, 2, channel)
        cache = owner.reference_reuse
        assert isinstance(cache, confirmation._ReferenceParsingCache) and channel in cache.versions
        assert context.session_indices is not None
        assert cache.version(channel, source, _MarketPrefixIndex(other, bars), context.session_indices, 3) is None
        with owned_evidence_scope():
            assert cache.version(channel, source, _MarketPrefixIndex(owner, bars), context.session_indices, 3) is None


def test_repeated_market_session_keeps_exact_cutoff_projection() -> None:
    bars = tuple(Bar(START + timedelta(hours=index), 'sz.test', 9., 10., 8., 9., 100)
                 for index in range(4))
    owner = _MarketRangeIndex.build(bars)
    assert owner is not None and not owner.unique_sessions
    source = PointVersion([_point(0, date_clock=True)]).seal()
    channel = _Channel()
    early, late = _context(source, bars, owner, 1, channel), _context(source, bars, owner, 3, channel)
    assert early.references[0] is not None and early.references[0].known == 1
    assert late.references[0] is not None and late.references[0].known == 3
    assert owner.reference_reuse is None


def test_n_enabled_path_never_uses_version_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    bars = _bars(4)
    owner = _MarketRangeIndex.build(bars)
    assert owner is not None
    source = PointVersion([_point(0)]).seal()
    channel = _Channel()

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError('N confirmation must retain its own prefix preparation')

    monkeypatch.setattr(confirmation._ReferenceParsingCache, 'version', forbidden)
    state = _PublishedTrendState(source, bars, 0, n_target_trend_confirmation_enabled=True,
                                _market_index=_MarketPrefixIndex(owner, bars), _reference_channel=channel)
    assert state.confirmation_context(3).n_target_trend_confirmation_enabled
