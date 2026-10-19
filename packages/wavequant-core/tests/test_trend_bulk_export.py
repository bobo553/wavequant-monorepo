"""Bulk transport preserves independent evidence and legacy audit boundaries."""

from collections.abc import Iterator, Mapping
from copy import deepcopy
from datetime import datetime, timedelta
import json
from typing import Never, SupportsIndex, cast

import pytest

from wavequant.domain.market_structure import trend_judgement as judgement
from wavequant.domain.market_structure import trend_lines
from wavequant.domain.models.model import Bar


def _points() -> tuple[judgement._FrozenPoint, ...]:
    return tuple(judgement._frozen_point(dict(index=index, ordinal=0, kind='H', value=12.0,
        available_at=index+1, label=f'H{index}', audit={'标记': [index, True, '引用'],
        'evidence': {'price': 12, 'ratio': 1.0}})) for index in range(5))


def test_bulk_decode_uses_one_parse_but_keeps_every_point_and_export_independent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    points = _points()
    real_loads = json.loads
    expected = [real_loads(cast(str, point.json_template)) for point in points]
    documents: list[str] = []

    def loads(document: str) -> object:
        documents.append(document)
        return real_loads(document)

    monkeypatch.setattr(judgement.json, 'loads', loads)
    exported = judgement.mutable_points(points)
    assert exported == expected and len(documents) == 1
    assert all(list(actual) == list(prior) for actual, prior in zip(exported, expected))
    audit = cast(dict[str, object], exported[0]['audit'])
    cast(list[object], audit['标记']).append('modified')
    assert exported[1] == expected[1]
    assert judgement.mutable_points(points) == expected
    assert type(cast(dict[str, object], audit['evidence'])['price']) is int
    assert type(cast(dict[str, object], audit['evidence'])['ratio']) is float


def test_batch_buffer_limit_falls_back_without_changing_values_or_types(monkeypatch: pytest.MonkeyPatch) -> None:
    points = _points()
    expected = judgement.mutable_points(points)
    real_loads = json.loads
    calls: list[str] = []

    def loads(document: str) -> object:
        calls.append(document)
        return real_loads(document)

    monkeypatch.setattr(judgement, '_MUTABLE_POINT_BATCH_CHARACTERS', 1)
    monkeypatch.setattr(judgement.json, 'loads', loads)
    assert judgement.mutable_points(points) == expected
    assert len(calls) == len(points)


def test_large_history_is_exported_in_bounded_batches_instead_of_one_parse_per_point(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    points = _points()
    expected = judgement.mutable_points(points)
    templates = [cast(str, point.json_template) for point in points]
    limit = 2 + sum(len(template)+1 for template in templates[:2])
    real_loads = json.loads
    documents: list[str] = []

    def loads(document: str) -> object:
        documents.append(document)
        return real_loads(document)

    monkeypatch.setattr(judgement, '_MUTABLE_POINT_BATCH_CHARACTERS', limit)
    monkeypatch.setattr(judgement.json, 'loads', loads)
    assert judgement.mutable_points(points) == expected
    assert len(documents) == 3 and all(len(document) <= limit for document in documents)


def test_nonstandard_sequence_keeps_its_original_iteration_boundary() -> None:
    calls: list[str] = []

    class Points(list[Mapping[str, object]]):
        def __iter__(self) -> Iterator[Mapping[str, object]]:
            calls.append('iterate')
            return super().__iter__()

    points = _points()
    assert judgement.mutable_points(Points(points)) == judgement.mutable_points(points)
    assert calls == ['iterate']


def test_mixed_unknown_audit_keeps_original_copy_count_and_avoids_encoding_hooks() -> None:
    calls: list[str] = []

    class Audit:
        def __deepcopy__(self, memo: dict[int, object]) -> 'Audit':
            calls.append('copy')
            return Audit()

        def __reduce_ex__(self, protocol: SupportsIndex) -> Never:
            raise AssertionError('unknown audit was encoded')

    frozen = _points()[0]
    source: tuple[Mapping[str, object], ...] = (frozen, {'audit': Audit()}, frozen)
    exported = judgement.mutable_points(source)
    assert calls == ['copy']
    assert exported[0] == exported[2]
    assert exported[0] is not exported[2]
    assert exported[0]['audit'] is not exported[2]['audit']


def _market() -> list[Bar]:
    return [Bar(datetime(2026, 1, 1)+timedelta(days=index), 'CACHE', 11., 12., 10., 11., 100.)
            for index in range(6)]


def _cache_payload() -> tuple[list[dict[str, object]], list[dict[str, object]],
                              list[dict[str, object]], list[dict[str, object]]]:
    shared = {'values': [1, 1.0, True], 'tags': ('closed', 'own')}
    return ([{'kind': 'H', 'proof': shared}], [{'kind': 'L', 'proof': shared}], [], [])


def test_closed_permission_bytes_preserve_internal_aliases_and_detach_each_read() -> None:
    bars = _market()
    cache = trend_lines._TrendPermissionCache(bars)
    prefix = cache.prefix(bars)
    assert prefix is not None
    source = [{'index': 0, 'kind': 'L', 'value': 10., 'available_at': 1}]
    payload = _cache_payload()
    cache.put((1, 'closed'), source, source, None, prefix, 4, False, payload)
    first = cache.get((1, 'closed'), source, source, None, prefix, 4, False)
    second = cache.get((1, 'closed'), source, source, None, prefix, 4, False)
    assert first == second == payload and first is not None and second is not None
    assert type(first[0][0]['proof']) is dict
    assert first[0][0]['proof'] is first[1][0]['proof']
    assert first[0][0]['proof'] is not second[0][0]['proof']
    proof = cast(dict[str, object], first[0][0]['proof'])
    assert type(proof['tags']) is tuple
    original_proof = cast(dict[str, object], payload[0][0]['proof'])
    assert proof['tags'] is original_proof['tags']
    assert proof['tags'] is cast(dict[str, object], second[0][0]['proof'])['tags']
    for original_value, restored_value in zip(cast(list[object], original_proof['values']),
                                             cast(list[object], proof['values'])):
        assert restored_value is original_value
    cast(list[object], proof['values']).append('modified')
    assert second == payload
    assert cache.get((1, 'closed'), source, source, None, prefix, 4, False) == payload
    assert cache.get((1, 'closed'), source, source, None, prefix, 4, True) is None
    revised = [dict(source[0], available_at=2)]
    assert cache.get((1, 'closed'), revised, source, None, prefix, 4, False) is None
    assert cache.get((1, 'closed'), source, source, None, prefix, 5, False) is None


def test_closed_permission_budget_refuses_only_storage_and_never_changes_input() -> None:
    bars = _market()
    cache = trend_lines._TrendPermissionCache(bars, memory_bytes=1)
    prefix = cache.prefix(bars)
    assert prefix is not None
    source = [{'index': 0, 'kind': 'L', 'value': 10., 'available_at': 1}]
    payload = _cache_payload()
    expected = deepcopy(payload)
    cache.put((1, 'closed'), source, source, None, prefix, 4, False, payload)
    assert cache.get((1, 'closed'), source, source, None, prefix, 4, False) is None
    assert payload == expected and cache._payload_bytes == 0


@pytest.mark.parametrize('invalid', (0, -1, True, 1.0))
def test_closed_permission_budget_rejects_invalid_configuration(invalid: object) -> None:
    with pytest.raises(ValueError, match='positive integer'):
        trend_lines._TrendPermissionCache(_market(), memory_bytes=cast(int, invalid))


def test_identical_complete_source_does_not_repeat_per_point_certification(monkeypatch: pytest.MonkeyPatch) -> None:
    values = (20, 30, 10, 20, 14, 26, 18, 35, 28, 45, 38, 42, 32, 36, 24, 29, 26, 50)
    source = [dict(index=index, ordinal=0, kind='L' if index % 2 == 0 else 'H', value=value,
                   time=str(index), available_at=index+1, state='confirmed', trend_level=0)
              for index, value in enumerate(values)]
    cache = trend_lines._TrendJudgementCache()
    first = cache.judge(source, path='source', trend_level=1)
    calls: list[int] = []
    original = cache._finite.known_points

    def certify(points: list[Mapping[str, object]]) -> bool:
        calls.append(len(points))
        return original(points)

    monkeypatch.setattr(cache._finite, 'known_points', certify)
    assert cache.judge(deepcopy(source), path='source', trend_level=1) is first
    assert cache.judge(deepcopy(source), path='source', trend_level=1, asof=len(source)+2) is first
    assert calls == []
    source[-1]['available_at'] = len(source)+1
    assert cache.judge(source, path='source', trend_level=1) == judgement.judge_trend(source, trend_level=1)
    assert calls == [len(source)]
    assert cache.judge(source, path='source', trend_level=1, asof=len(source)-1) == judgement.judge_trend(
        source, trend_level=1, asof=len(source)-1)
