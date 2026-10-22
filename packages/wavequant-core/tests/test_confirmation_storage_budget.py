"""确认版本按保留的标量计费，容量不足只影响复用。"""

from dataclasses import replace
from datetime import datetime, timedelta
from fractions import Fraction
from gc import collect
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


def _bars() -> tuple[Bar, ...]:
    return tuple(Bar(START + timedelta(days=index), 'sz.test', 9., 10., 8., 9., 100)
                 for index in range(2))


def _point(*, label: str = 'point', state: str = 'confirmed') -> dict[str, object]:
    return dict(index=0, ordinal=0, time='2024-01-01', kind='H', value=10.,
                available_at=0, label=label, state=state)


def test_dense_invalid_references_fit_without_a_valid_point_minimum(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bars = _bars()
    owner, cold = _MarketRangeIndex.build(bars), _MarketRangeIndex.build(bars)
    assert owner is not None and cold is not None
    cold.reference_reuse = object()
    monkeypatch.setattr(confirmation, '_REFERENCE_DATA_BYTES', 640 * 1024)
    channel = _Channel()
    source = PointVersion(_point(state='developing') for _ in range(2048)).seal()

    actual = confirmation.prepare_confirmation_context(source, bars, 1,
        _market_index=_MarketPrefixIndex(owner, bars), _reference_channel=channel)
    expected = confirmation.prepare_confirmation_context(source, bars, 1,
        _market_index=_MarketPrefixIndex(cold, bars))

    cache = owner.reference_reuse
    assert isinstance(cache, confirmation._ReferenceParsingCache)
    assert channel in cache.versions and channel not in cache.rejections
    assert len(actual.references) == len(source)
    assert all(point is None for point in actual.references)
    assert list(actual.references) == list(expected.references)
    assert actual.ordered == expected.ordered
    assert list(actual._proof_prefixes or ()) == list(expected._proof_prefixes or ())
    assert list(actual._prefix_visibility or ()) == list(expected._prefix_visibility or ())
    assert list(actual._reference_stops or ()) == list(expected._reference_stops or ())
    assert cache.version_bytes + cache.memory_bytes + 2 * getsizeof(cache.entries) <= 640 * 1024


@pytest.mark.parametrize('character,count', (('x', 4 * 1024 * 1024), ('\U0001f9ed', 1024 * 1024)))
def test_oversized_scalar_rejection_recovers_after_a_short_revision(character: str, count: int) -> None:
    bars = _bars()
    owner, cold = _MarketRangeIndex.build(bars), _MarketRangeIndex.build(bars)
    assert owner is not None and cold is not None
    cold.reference_reuse = object()
    cache, channel = confirmation._ReferenceParsingCache(), _Channel()
    owner.reference_reuse = cache
    market = _MarketPrefixIndex(owner, bars)
    sessions = confirmation._PrefixSessions(owner.positions, 1)
    source = PointVersion([_point(label=character * count)]).seal()

    assert cache.version(channel, source, market, sessions, 1) is None
    assert channel not in cache.versions and channel in cache.rejections
    assert cache.entries == {} and cache.memory_bytes == 0
    assert cache.version_bytes <= confirmation._REFERENCE_DATA_BYTES

    revised = PointVersion([_point(label='short')], previous=source, start=0).seal()
    actual = confirmation.prepare_confirmation_context(revised, bars, 1,
        _market_index=market, _reference_channel=channel)
    expected = confirmation.prepare_confirmation_context(revised, bars, 1,
        _market_index=_MarketPrefixIndex(cold, bars))

    assert channel in cache.versions and channel not in cache.rejections
    assert list(actual.references) == list(expected.references)
    assert list(actual._proof_prefixes or ()) == list(expected._proof_prefixes or ())
    assert cache.version_bytes + 2 * getsizeof(cache.entries) <= confirmation._REFERENCE_DATA_BYTES


@pytest.mark.parametrize('field', ('numerator', 'denominator', 'known'))
def test_reference_charge_includes_large_derived_integer_payloads(field: str) -> None:
    point = confirmation._ReferenceParsingCache().parse(_point(), {'2024-01-01': 0}, 0, _retain=False)
    assert point is not None
    large = 2 ** 32768 + 1
    # 只构造计费对象，不扩大价格或行情位置的业务输入范围。
    if field == 'numerator':
        expanded = replace(point, value=Fraction(large, 1))
    elif field == 'denominator':
        expanded = replace(point, value=Fraction(1, large))
    else:
        expanded = replace(point, known=large)

    assert confirmation._reference_storage_bytes(expanded) - confirmation._reference_storage_bytes(point) > 4096


class _AuditGraph:
    def __init__(self) -> None:
        self.records = [{'proof': str(index)} for index in range(256)]

    def __deepcopy__(self, memo: dict[int, object]) -> '_AuditGraph':
        raise AssertionError('reference caching must not copy unrelated audit evidence')


def test_cached_reference_does_not_retain_or_copy_source_evidence_graph() -> None:
    bars = _bars()
    owner = _MarketRangeIndex.build(bars)
    assert owner is not None
    cache, channel = confirmation._ReferenceParsingCache(), _Channel()
    market = _MarketPrefixIndex(owner, bars)
    sessions = confirmation._PrefixSessions(owner.positions, 1)
    evidence = _AuditGraph()
    evidence_ref = ref(evidence)
    raw = _point()
    raw['evidence'] = evidence
    source = PointVersion([raw]).seal()
    source_ref = ref(source)

    version = cache.version(channel, source, market, sessions, 1)
    assert version is not None
    point = version.references[0]
    assert point is not None and 'evidence' not in point.raw
    assert dict(point.raw) == {key: value for key, value in _point().items() if key != 'state'}
    del source, raw, evidence
    collect()

    assert source_ref() is None and evidence_ref() is None
    assert channel in cache.versions
    assert cache.entries == {} and cache.memory_bytes == 0
    assert cache.version_bytes + 2 * getsizeof(cache.entries) <= confirmation._REFERENCE_DATA_BYTES
