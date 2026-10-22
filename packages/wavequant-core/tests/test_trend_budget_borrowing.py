"""冻结点由独立缓存持有时只借用；拒绝、关闭和缓存切换恢复原预算边界。"""

from collections.abc import Callable, Sequence
from weakref import ref

import pytest

from wavequant.domain.market_structure import trend_judgement as judgement
from wavequant.domain.market_structure.trend_judgement import (
    TrendJudgement, _FormalKeyBreakState, _FrozenPointCache, _TrendJudgementState,
    _frozen_point_scope, judge_trend, mutable_points,
)


def _measure(kind: str) -> Callable[[Sequence[object]], int]:
    if kind == 'ordinary':
        return _TrendJudgementState(1)._owned_value_bytes
    return _FormalKeyBreakState._value_bytes


@pytest.mark.parametrize('kind', ('ordinary', 'formal'))
def test_cache_owned_point_is_borrowed_and_refused_points_keep_full_cost(
    monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    cache = _FrozenPointCache(memory_bytes=64 * 1024)
    source = {'index': 1, 'proof': {'observations': list(range(128))}}
    with _frozen_point_scope(cache=cache):
        point = cache.intern(source)
        assert point._budget_owner is cache._budget_token
        measure = _measure(kind)
        original = judgement.get_referents

        def forbid_proof(value: object) -> list[object]:
            assert value is not point.data and value is not point['proof']
            return original(value)

        with monkeypatch.context() as scoped:
            scoped.setattr(judgement, 'get_referents', forbid_proof)
            assert measure([point, point]) == 8
        rejected = cache.intern({'index': 2, 'proof': 'large-proof-' * 16384})
        assert rejected._budget_owner is None
        assert measure([rejected]) > 8
        assert cache.intern(source) is point
        assert point._budget_owner.alive
        assert cache.memory_used <= cache._limit
        cache.close()
        assert not point._budget_owner.alive
        assert measure([point]) > 8


def test_point_token_does_not_keep_the_cache_alive() -> None:
    cache = _FrozenPointCache()
    weak_cache = ref(cache)
    with _frozen_point_scope(cache=cache):
        point = cache.intern({'index': 1, 'proof': [1, 2, 3]})
    cache.close()
    del cache
    assert weak_cache() is None
    assert point._budget_owner is not None and not point._budget_owner.alive
    assert mutable_points([point]) == [{'index': 1, 'proof': [1, 2, 3]}]


def test_cache_switch_invalidates_ordinary_and_formal_borrowed_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prices = (20., 30., 10., 20., 14., 26., 18., 35.)
    source = [dict(index=index, ordinal=0, kind='H' if index % 2 else 'L', value=price,
        time=str(index), available_at=index + 1, trend_level=0, state='confirmed')
        for index, price in enumerate(prices)]
    first, second = _FrozenPointCache(), _FrozenPointCache()
    with _frozen_point_scope(cache=first):
        ordinary = _TrendJudgementState(1)
        formal = _FormalKeyBreakState(2)
        expected = judge_trend(source, _append_state=ordinary)
        formal.calculate([], TrendJudgement(2, 1, (), ()))
        assert ordinary._frozen_budget_owner is first._budget_token
        assert formal._frozen_budget_owner is first._budget_token
    formal_clears = 0
    original_clear = _FormalKeyBreakState.clear

    def clear(self: _FormalKeyBreakState) -> None:
        nonlocal formal_clears
        formal_clears += 1
        original_clear(self)

    monkeypatch.setattr(_FormalKeyBreakState, 'clear', clear)
    cold_before = ordinary.cold_runs
    with _frozen_point_scope(cache=second):
        actual = judge_trend(source, _append_state=ordinary)
        formal.calculate([], TrendJudgement(2, 1, (), ()))
        assert ordinary.cold_runs == cold_before + 1
        assert formal_clears == 1
        assert ordinary._frozen_budget_owner is second._budget_token
        assert formal._frozen_budget_owner is second._budget_token
        assert mutable_points(actual.points) == mutable_points(expected.points)
        assert mutable_points(actual.candidates) == mutable_points(expected.candidates)
    first.close()
    second.close()
