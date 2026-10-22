"""标准冻结点一次计量，追加上界与完整图校准分别承担不同职责。"""

from collections.abc import Callable, Sequence
from decimal import Decimal
from typing import cast

import pytest

from wavequant.domain.market_structure import trend_judgement as judgement
from wavequant.domain.market_structure.owned_evidence import owned_evidence_scope
from wavequant.domain.market_structure.trend_judgement import (
    _FormalKeyBreakState, _FrozenPoint, _TrendJudgementState, _freeze_point_uncached,
    judge_trend, mutable_points,
)


def _measure(kind: str) -> Callable[[Sequence[object], bool], int]:
    if kind == 'ordinary':
        state = _TrendJudgementState(1)
        return lambda roots, incremental: state._owned_value_bytes(roots, _incremental=incremental)
    return lambda roots, incremental: _FormalKeyBreakState._value_bytes(roots, _incremental=incremental)


@pytest.mark.parametrize('kind', ('ordinary', 'formal'))
def test_appending_existing_frozen_point_does_not_revisit_its_proof(
    monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    source = {'index': 2, 'proof': {'observations': list(range(2048))}}
    point = _freeze_point_uncached(source)
    measure = _measure(kind)
    expected = measure([point], False)
    before = mutable_points([point])
    frozen_proof = point['proof']
    assert point._budget_upper_bytes is not None
    original_referents = judgement.get_referents

    def forbid_old_proof(value: object) -> list[object]:
        assert value is not frozen_proof, 'an append walked the frozen historical proof'
        return original_referents(value)

    with monkeypatch.context() as scoped:
        scoped.setattr(judgement, 'get_referents', forbid_old_proof)
        for _ in range(32):
            assert measure([point], True) >= expected
    assert mutable_points([point]) == before
    # 创建之后的外部输入修订不能改变既有版本或其尺寸证书。
    cast(dict[str, object], source['proof'])['observations'] = [99]
    assert mutable_points([point]) == before


@pytest.mark.parametrize('kind', ('ordinary', 'formal'))
def test_exact_calibration_still_deduplicates_aliases_between_frozen_points(kind: str) -> None:
    shared = {'observations': list(range(1024))}
    memo: dict[int, tuple[object, object]] = {}
    first = _freeze_point_uncached({'index': 1, 'proof': shared}, memo)
    second = _freeze_point_uncached({'index': 2, 'proof': shared}, memo)
    assert first['proof'] is second['proof']
    measure = _measure(kind)
    incremental = measure([first, second], True)
    exact = measure([first, second], False)
    separate = measure([first], False) + measure([second], False)

    assert incremental >= exact
    assert exact < separate
    assert measure([first, first], True) == measure([first], True)
    # 请求池折扣只会减少真实开销，独占上界不依赖某次请求的拥有权。
    with owned_evidence_scope(memory_bytes=4096):
        assert measure([first, second], True) == incremental
    assert measure([first, second], False) == exact


@pytest.mark.parametrize('kind', ('ordinary', 'formal'))
def test_nonstandard_audit_keeps_the_uncached_budget_path(
    monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    point = _freeze_point_uncached({'index': 1, 'audit': Decimal('1.25')})
    assert point.json_template is None
    assert point._budget_upper_bytes is None
    measure = _measure(kind)
    expected = measure([point], False)

    def reject_cached_budget(*args: object) -> int:
        raise AssertionError('nonstandard audit acquired an immutable size certificate')

    monkeypatch.setattr(judgement, '_frozen_budget_upper', reject_cached_budget)
    assert measure([point], True) == expected


@pytest.mark.parametrize('kind', ('ordinary', 'formal'))
def test_direct_frozen_point_wrapper_does_not_certify_a_mutable_mapping(kind: str) -> None:
    data: dict[str, object] = {'audit': [1]}
    point = _FrozenPoint(data, '{"audit":[1]}')
    measure = _measure(kind)
    before = measure([point], True)
    data['audit'] = list(range(1024))
    assert point._budget_upper_bytes is None
    assert measure([point], True) == measure([point], False)
    assert measure([point], True) > before


@pytest.mark.parametrize('level', (1, 2, 3))
def test_incremental_budget_preserves_full_judgements_and_the_memory_limit(level: int) -> None:
    prices = (20., 30., 10., 20., 14., 26., 18., 35., 13., 25., 16., 39.)
    source = [dict(index=index, ordinal=0, kind='H' if index % 2 else 'L', value=value,
        time=str(index), available_at=index + 1, trend_level=level - 1, state='confirmed',
        audit={'proof': [index]}) for index, value in enumerate(prices)]
    limit = 512 * 1024
    state = _TrendJudgementState(level, memory_bytes=limit, _tail_reuse=True)
    for stop in range(1, len(source) + 1):
        actual = judge_trend(source[:stop], trend_level=level, _append_state=state)
        expected = judge_trend(source[:stop], trend_level=level)
        assert mutable_points(actual.points) == mutable_points(expected.points)
        assert mutable_points(actual.candidates) == mutable_points(expected.candidates)
        assert state.memory_used <= limit
        if state.memory_used:
            assert state.memory_used >= state._owned_bytes()
