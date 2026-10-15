"""完整点的有界跨日复用保留知识前缀、公开复制和请求生命周期。"""

import asyncio
from collections.abc import Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import copy_context
from copy import deepcopy
from datetime import datetime, timedelta
import importlib
from typing import Never, SupportsIndex, cast

import pytest

from wavequant.domain.market_structure import trend_judgement as judgement
from wavequant.domain.market_structure.trend_judgement import (
    _FrozenPoint, _FrozenPointCache, _frozen_point, _frozen_point_cache, _frozen_point_scope,
    judge_trend, mutable_points,
)
from wavequant.domain.models.model import Bar


def _point() -> dict[str, object]:
    return dict(index=3, ordinal=0, kind='H', value=12.0, time='2024-01-04', available_at=5,
                label='H1', source_level=0, trend_level=1,
                proof=dict(origin=dict(index=0, available_at=1), children=[dict(index=2, value=10.0)]))


def _source() -> list[dict[str, object]]:
    values = (20, 30, 10, 20, 14, 26, 18, 35, 28, 45, 38, 42, 32, 36, 24, 29, 26, 50)
    return [dict(index=index, ordinal=0, kind='L' if index % 2 == 0 else 'H', value=value,
                 time=str(index), available_at=index+1, state='confirmed', trend_level=0, label=f'source-{index}')
            for index, value in enumerate(values)]


def test_whole_point_hits_keep_readonly_data_and_independent_nested_exports() -> None:
    point = _point()
    with _frozen_point_scope() as cache:
        first = _frozen_point(point)
        assert _frozen_point(deepcopy(point)) is first
        point['available_at'] = 99
        assert first['available_at'] == 5
        with pytest.raises(TypeError):
            cast(dict[str, object], first.data)['available_at'] = 2
        first_export, second_export = mutable_points((first, first))
        proof = cast(dict[str, object], first_export['proof'])
        cast(dict[str, object], proof['origin'])['available_at'] = 100
        assert cast(Mapping[str, object], cast(Mapping[str, object], second_export['proof'])['origin'])['available_at'] == 1
        assert mutable_points((first,))[0] == second_export
        assert cache._hits == 1
    assert cache.memory_used == 0 and cache._closed


def test_borrowed_request_cache_reuses_points_across_days_and_restores_outer_scope() -> None:
    request_cache = _FrozenPointCache()
    point = _point()
    try:
        with _frozen_point_scope() as outer:
            with _frozen_point_scope(cache=request_cache):
                first = _frozen_point(point)
            assert _frozen_point_cache.get() is outer
            assert not request_cache._closed
            with _frozen_point_scope(cache=request_cache):
                assert _frozen_point(deepcopy(point)) is first
                revised = dict(point, available_at=6)
                assert _frozen_point(revised) is not first
            assert request_cache._hits == 1
            with pytest.raises(RuntimeError, match='interrupted day'):
                with _frozen_point_scope(cache=request_cache):
                    raise RuntimeError('interrupted day')
            assert _frozen_point_cache.get() is outer
            assert not request_cache._closed
    finally:
        request_cache.close()
    assert request_cache.memory_used == 0 and request_cache._closed








@pytest.mark.parametrize(('field', 'value'), [
    ('available_at', 6), ('available_at', '5'), ('value', 12), ('ordinal', False),
    ('label', 'H2'), ('source_level', False), ('trend_level', 1.0),
    ('proof', {'origin': {'index': 0, 'available_at': 2}, 'children': [{'index': 2, 'value': 10.0}]}),
])
def test_full_typed_clock_label_source_and_proof_changes_do_not_share_snapshot(field: str, value: object) -> None:
    original = _point()
    changed = deepcopy(original)
    changed[field] = value
    with _frozen_point_scope():
        first = _frozen_point(original)
        second = _frozen_point(changed)
        assert first is not second
        assert type(mutable_points((second,))[0][field]) is type(value)


@pytest.mark.parametrize('value', [b'audit', 1+2j, float('inf'), {(1, 2)}])
def test_non_template_evidence_keeps_original_fresh_freeze_and_export(value: object) -> None:
    point = dict(_point(), audit=value)
    with _frozen_point_scope() as cache:
        first, second = _frozen_point(point), _frozen_point(point)
        assert first is not second and first.json_template is None and second.json_template is None
        assert mutable_points((first,))[0]['audit'] == value
        assert cache.memory_used == 0 and cache._fallbacks == 2


def test_unknown_audit_hooks_keep_original_copy_count_and_never_enter_signature_or_size() -> None:
    hooks: list[str] = []

    class Audit:
        def __deepcopy__(self, memo: dict[int, object]) -> 'Audit':
            hooks.append('copy')
            return Audit()

        def __reduce__(self) -> Never:
            raise AssertionError('cache invoked reduce')

        def __reduce_ex__(self, protocol: SupportsIndex) -> Never:
            raise AssertionError('cache invoked reduce_ex')

        def __getstate__(self) -> object:
            raise AssertionError('cache invoked getstate')

        def __sizeof__(self) -> int:
            raise AssertionError('cache invoked custom size')

    point = dict(_point(), audit=Audit())
    with _frozen_point_scope() as cache:
        first, second = _frozen_point(point), _frozen_point(point)
        assert first['audit'] is not second['audit']
        assert hooks == ['copy', 'copy'] and cache.memory_used == 0


def test_cache_uses_bounded_owned_graph_admission_and_large_point_fallback() -> None:
    cache = _FrozenPointCache(memory_bytes=6000)
    for index in range(40):
        result = cache.intern(dict(_point(), index=index, audit='x'*512))
        assert result['index'] == index and cache.memory_used <= 6000
    assert cache._evictions > 0 and cache._peak_bytes <= 6000
    before = cache.memory_used
    oversized = cache.intern(dict(_point(), audit='x'*10000))
    assert oversized['audit'] == 'x'*10000 and cache.memory_used == before
    cache.close()
    assert cache.memory_used == 0
    assert cache.intern(_point()) is not cache.intern(_point())


def test_repeated_window_larger_than_capacity_keeps_stable_point_hits() -> None:
    cache = _FrozenPointCache(memory_bytes=6000)
    source = [dict(_point(), index=index, audit='x'*512) for index in range(20)]
    original = [mutable_points((cache.intern(point),))[0] for point in source]
    for _ in range(3):
        hits = cache._hits
        assert [mutable_points((cache.intern(point),))[0] for point in source] == original
        assert cache._hits > hits and cache.memory_used <= 6000
    cache.close()


@pytest.mark.parametrize('capacity', [0, -1, True, 1.0, '8'])
def test_invalid_capacity_is_rejected(capacity: object) -> None:
    with pytest.raises(ValueError, match='positive integer'):
        _FrozenPointCache(memory_bytes=cast(int, capacity))


def test_nested_scopes_restore_owner_and_exception_cleans_entries() -> None:
    assert _frozen_point_cache.get() is None
    with _frozen_point_scope() as outer:
        old = _frozen_point(_point())
        with pytest.raises(RuntimeError, match='stop'):
            with _frozen_point_scope() as inner:
                assert _frozen_point(_point()) is not old
                raise RuntimeError('stop')
        assert inner._closed and inner.memory_used == 0
        assert _frozen_point_cache.get() is outer and _frozen_point(_point()) is old
    assert _frozen_point_cache.get() is None and outer._closed


def test_copied_thread_context_cannot_borrow_or_mutate_origin_cache() -> None:
    with _frozen_point_scope() as owner:
        old = _frozen_point(_point())
        context = copy_context()
        with ThreadPoolExecutor(max_workers=1) as executor:
            foreign = executor.submit(context.run, _frozen_point, _point()).result()
        assert foreign is not old and owner._hits == 0
        assert _frozen_point(_point()) is old
    assert context.run(_frozen_point, _point()) is not old


def test_interleaved_async_scopes_have_independent_owners() -> None:
    async def run() -> tuple[_FrozenPoint, _FrozenPoint]:
        proceed = asyncio.Event()
        ready = (asyncio.Event(), asyncio.Event())

        async def task(signal: asyncio.Event) -> _FrozenPoint:
            with _frozen_point_scope():
                old = _frozen_point(_point())
                signal.set()
                await proceed.wait()
                assert _frozen_point(_point()) is old
                return old

        tasks = [asyncio.create_task(task(signal)) for signal in ready]
        await ready[0].wait()
        await ready[1].wait()
        proceed.set()
        results = await asyncio.gather(*tasks)
        return results[0], results[1]

    first, second = asyncio.run(run())
    assert first is not second and _frozen_point_cache.get() is None


def test_warm_points_do_not_bypass_source_or_clock_rejection_and_future_prefix() -> None:
    source = _source()
    before = judge_trend(source, asof=17)
    after = judge_trend(source, asof=18)
    with _frozen_point_scope():
        assert judge_trend(source, asof=18) == after
        assert judge_trend(source, asof=17) == before
        assert all(cast(int, point['available_at']) <= 17 for point in judge_trend(source, asof=17).points)
        changed = deepcopy(source)
        changed[0]['trend_level'] = False
        with pytest.raises(ValueError):
            judge_trend(changed, asof=18)
        with pytest.raises(ValueError):
            judge_trend(source, asof=cast(int, 18.0))


@pytest.mark.parametrize('enabled', [False, True])
def test_history_stages_close_independent_caches_and_match_uncached_all_channels(
    monkeypatch: pytest.MonkeyPatch, enabled: bool,
) -> None:
    chart = importlib.import_module('wavequant.domain.strategies.chart_entry_history')
    hierarchy = importlib.import_module('wavequant.domain.strategies.hierarchical_entry')
    prices = (20, 30, 10, 20, 14, 26, 18, 35, 28, 45, 38, 42, 32, 36, 24, 29, 26, 50)
    bars = [Bar(datetime(2024, 1, 1)+timedelta(days=index), 'TEST', price, price+1, price-1, price, 100)
            for index, price in enumerate(prices)]

    @contextmanager
    def uncached() -> Iterator[None]:
        yield None

    def result() -> object:
        shallow: dict[object, object] = {}
        combined: dict[object, object] = {}
        pullback: dict[object, object] = {}
        declines: dict[object, object] = {}
        structure: dict[object, object] = {}
        return (chart.chart_entry_history(bars, shallow_candidate_sink=shallow, combined_candidate_sink=combined,
                                         secondary_pullback_sink=pullback,
                                         n_target_trend_confirmation_enabled=enabled), shallow, combined, pullback,
                hierarchy.hierarchical_history(bars, decline_sink=declines, structure_sink=structure,
                                               n_target_trend_confirmation_enabled=enabled), declines, structure)

    with monkeypatch.context() as patch:
        patch.setattr(chart, '_frozen_point_scope', uncached)
        patch.setattr(hierarchy, '_frozen_point_scope', uncached)
        expected = result()
    captured: list[_FrozenPointCache] = []

    @contextmanager
    def observed() -> Iterator[_FrozenPointCache]:
        with _frozen_point_scope() as cache:
            captured.append(cache)
            yield cache

    monkeypatch.setattr(chart, '_frozen_point_scope', observed)
    monkeypatch.setattr(hierarchy, '_frozen_point_scope', observed)
    assert result() == expected
    assert len(captured) == 6 and all(cache._closed and cache.memory_used == 0 for cache in captured)
    assert any(cache._hits > 0 for cache in captured)
    assert _frozen_point_cache.get() is None
