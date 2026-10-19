"""参考点标量复用不改变公开字段、证据编码或当天的知识边界。"""

from collections.abc import Mapping
from datetime import datetime, timedelta
from types import MappingProxyType

import pytest

from wavequant.domain.market_structure import trend_confirmation as confirmation
from wavequant.domain.market_structure.trend_evidence_reuse import _geometry_signature
from wavequant.domain.market_structure.trend_market_index import _MarketPrefixIndex, _MarketRangeIndex
from wavequant.domain.models.model import Bar


START = datetime(2024, 1, 1)


def _bars(count: int) -> tuple[Bar, ...]:
    return tuple(Bar(START + timedelta(days=index), 'sz.test', 9., 10., 8., 9., 100)
                 for index in range(count))


def _point(index: int, *, date_clock: bool = False) -> dict[str, object]:
    day = (START + timedelta(days=index)).date().isoformat()
    return dict(index=index, ordinal=0, time=day, kind='H' if index % 2 == 0 else 'L',
                value=10. if index % 2 == 0 else 8., available_at=day if date_clock else index,
                label=f'point {index}', state='confirmed')


@pytest.mark.parametrize('history', (64, 512))
@pytest.mark.parametrize('date_clock', (False, True))
def test_unchanged_reference_proofs_are_not_reencoded_each_day(
    history: int, date_clock: bool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    bars = _bars(history + 16)
    owner = _MarketRangeIndex.build(bars)
    assert owner is not None
    source = [_point(index, date_clock=date_clock) for index in range(history)]
    prefix = bars[:history]
    confirmation.prepare_confirmation_context(source, prefix, history - 1,
        _market_index=_MarketPrefixIndex(owner, prefix))
    encoded: list[object] = []

    def counted(value: object) -> bytes | None:
        encoded.append(value)
        return _geometry_signature(value)

    monkeypatch.setattr(confirmation, '_geometry_signature', counted)
    for end in range(history, history + 8):
        prefix = bars[:end + 1]
        context = confirmation.prepare_confirmation_context(source, prefix, end,
            _market_index=_MarketPrefixIndex(owner, prefix))
        assert context.ordered and context._proof_prefixes is not None
    # 当天输入仍逐点完整认证；已冻结的公开证明不再被第二次编码。
    assert len(encoded) == 8 * history
    assert all(isinstance(value, dict) and value.get('state') == 'confirmed' for value in encoded)
    encoded.clear()
    source.append(_point(history + 7, date_clock=date_clock))
    context = confirmation.prepare_confirmation_context(source, prefix, history + 7,
        _market_index=_MarketPrefixIndex(owner, prefix))
    latest = context.references[-1]
    assert latest is not None and len(encoded) == history + 2
    proofs = [value for value in encoded if isinstance(value, dict) and 'state' not in value]
    assert proofs == [dict(latest.raw)]


@pytest.mark.parametrize('field,initial,revised', (
    ('value', 10, 10.), ('label', 1, True), ('label', 1, 1.), ('label', 0., -0.),
    ('label', None, 'changed'), ('available_at', 0, '2024-01-01'),
    ('state', 'confirmed', 'developing'), ('display_only', False, True),
    ('index', 0, True), ('ordinal', 0, True), ('kind', 'H', 'K'), ('value', 10., float('inf')),
))
def test_old_point_revisions_keep_exact_types_and_rejections(
    field: str, initial: object, revised: object,
) -> None:
    cache = confirmation._ReferenceParsingCache()
    sessions = {'2024-01-01': 0}
    raw = _point(0)
    raw[field] = initial
    assert cache.parse(raw, sessions, 0) == confirmation._reference(raw, sessions, 0)
    raw[field] = revised
    actual = cache.parse(raw, sessions, 0)
    expected = confirmation._reference(raw, sessions, 0)
    assert actual == expected
    if actual is not None and expected is not None:
        assert _geometry_signature(dict(actual.raw)) == _geometry_signature(dict(expected.raw))
        assert actual._proof_signature == _geometry_signature(dict(expected.raw))


@pytest.mark.parametrize('field,value', (('ordinal', 0), ('label', None), ('state', 'confirmed'),
                                       ('display_only', False)))
def test_missing_and_explicit_optional_fields_remain_distinct(field: str, value: object) -> None:
    cache = confirmation._ReferenceParsingCache()
    raw = _point(0)
    raw.pop(field, None)
    sessions = {'2024-01-01': 0}
    first = cache.parse(raw, sessions, 0)
    raw[field] = value
    second = cache.parse(raw, sessions, 0)
    assert first is not None and second is not None
    assert len(cache.entries) == 2
    expected = confirmation._reference(raw, sessions, 0)
    assert expected is not None and second._proof_signature == _geometry_signature(dict(expected.raw))
    assert second.raw == expected.raw


def test_equal_strings_preserve_aliases_without_binding_the_key_to_object_ids() -> None:
    cache = confirmation._ReferenceParsingCache()
    raw = _point(0, date_clock=True)
    shared = cache.parse(raw, {'2024-01-01': 0}, 0)
    assert shared is not None and shared.raw['time'] is shared.raw['available_at']
    raw['available_at'] = bytearray(b'2024-01-01').decode()
    assert raw['time'] is not raw['available_at']
    separate = cache.parse(raw, {'2024-01-01': 0}, 0)
    assert separate is not None and separate.raw['time'] is not separate.raw['available_at']
    assert shared._proof_signature != separate._proof_signature
    raw['time'] = bytearray(b'2024-01-01').decode()
    raw['available_at'] = bytearray(b'2024-01-01').decode()
    assert cache.parse(raw, {'2024-01-01': 0}, 0) is separate
    assert len(cache.entries) == 2


class _UnknownString(str):
    pass


@pytest.mark.parametrize('unknown', (_UnknownString('label'), {'nested': 'label'}, ['label']))
def test_unknown_field_types_keep_the_uncached_validation_path(unknown: object) -> None:
    cache = confirmation._ReferenceParsingCache()
    raw = _point(0)
    raw['label'] = unknown
    sessions = {'2024-01-01': 0}
    actual = cache.parse(raw, sessions, 0)
    expected = confirmation._reference(raw, sessions, 0)
    assert actual == expected and actual is not None and actual._proof_signature is None
    assert cache.entries == {}
    assert cache.parse(MappingProxyType(raw), sessions, 0) == expected


@pytest.mark.parametrize('date_clock', (False, True))
def test_each_real_prefix_and_source_revision_matches_uncached_proofs(date_clock: bool) -> None:
    prices = ((10., 8., 9.), (7., 5., 6.), (9., 6., 8.), (11., 8., 10.5), (10.8, 9., 10.2),
              (9.5, 8., 9.), (10.5, 8.5, 10.), (12., 9., 11.5), (11.8, 9.5, 11.3))
    bars = tuple(Bar(START + timedelta(days=index), 'sz.test', (high + low) / 2, high, low, close, 100)
                 for index, (high, low, close) in enumerate(prices))
    owner, cold_owner = _MarketRangeIndex.build(bars), _MarketRangeIndex.build(bars)
    assert owner is not None and cold_owner is not None
    # 保留原独立解析与摘要生成路径，用于比较全部证明前缀及实际资格。
    cold_owner.reference_reuse = object()
    source: list[dict[str, object]] = []
    for index, kind, known in ((0, 'H', 0), (1, 'L', 2), (3, 'H', 4), (5, 'L', 6)):
        point = _point(index, date_clock=date_clock)
        point.update(kind=kind, value=bars[index].high if kind == 'H' else bars[index].low,
                     available_at=bars[known].timestamp.date().isoformat() if date_clock else known)
        source.append(point)
    for revised in (False, True):
        if revised:
            source[2].update(value=10.5, label='revised',
                             available_at='2024-01-08' if date_clock else 7)
        for end in (8, 0, 2, 6, 4, 7, 8):
            prefix = bars[:end + 1]
            actual = confirmation.prepare_confirmation_context(source, prefix, end,
                _market_index=_MarketPrefixIndex(owner, prefix))
            expected = confirmation.prepare_confirmation_context(source, prefix, end,
                _market_index=_MarketPrefixIndex(cold_owner, prefix))
            assert actual.references == expected.references and actual.ordered == expected.ordered
            assert actual._proof_prefixes == expected._proof_prefixes
            proof = confirmation.qualify_uptrend(source, source[1], 1, None, prefix, end, context=actual)
            assert proof == confirmation.qualify_uptrend(source, source[1], 1, None, prefix, end,
                                                         context=expected)
            if not revised and end >= 7:
                assert proof is not None
                trigger = proof['confirmed_by']
                assert isinstance(trigger, Mapping) and trigger['index'] == 7


def test_full_cache_budget_changes_storage_only() -> None:
    cache = confirmation._ReferenceParsingCache()
    cache.memory_bytes = 4 * 1024 * 1024
    raw = _point(0)
    sessions = {'2024-01-01': 0}
    assert cache.parse(raw, sessions, 0) == confirmation._reference(raw, sessions, 0)
    assert cache.entries == {}
