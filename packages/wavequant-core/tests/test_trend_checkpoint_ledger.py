"""尾部与翻向检查点恢复同一份发布账本，只重整真正变化的候选后缀。"""

from collections.abc import Mapping, Sequence
from copy import deepcopy
from random import Random

import pytest

from wavequant.domain.market_structure import trend_judgement
from wavequant.domain.market_structure.trend_judgement import (
    TrendJudgement, _JudgementLedger, _TrendJudgementState, judge_trend, mutable_points,
)
from wavequant.domain.market_structure.trend_level_upgrades import _TrendUpgradeState
from wavequant.domain.market_structure.trend_levels import _TrendReversalState
from wavequant.domain.market_structure.trend_options import ordinary_trend_completion


def _source(level: int, mirror: bool, count: int = 30) -> list[dict[str, object]]:
    random = Random(31)
    values = [random.randint(30, 50) if index % 2 else random.randint(5, 25) for index in range(count)]
    return [dict(index=index, ordinal=0, time=str(index), available_at=index+1,
        kind=('H' if mirror else 'L') if index % 2 == 0 else ('L' if mirror else 'H'),
        value=100-value if mirror else value, state='confirmed', trend_level=level-1,
        audit={'source': [index]}) for index, value in enumerate(values)]


def _assert_cold(source: Sequence[Mapping[str, object]], state: _TrendJudgementState) -> TrendJudgement:
    actual = judge_trend(source, trend_level=state.trend_level, _append_state=state)
    expected = judge_trend(source, trend_level=state.trend_level)
    assert mutable_points(actual.points) == mutable_points(expected.points)
    assert mutable_points(actual.candidates) == mutable_points(expected.candidates)
    if state._ordinary is not None:
        assert state._owned_bytes() <= state.memory_used <= state._limit
    return actual


@pytest.mark.parametrize('level', (1, 2, 3))
@pytest.mark.parametrize('mirror', (False, True))
@pytest.mark.parametrize('ordinary', (False, True))
def test_tail_and_flip_ledger_restore_preserves_cold_evidence_and_published_snapshots(
    level: int, mirror: bool, ordinary: bool,
) -> None:
    source = _source(level, mirror)
    state = _TrendJudgementState(level, _tail_reuse=True, ordinary_trend_completion_enabled=ordinary)
    with ordinary_trend_completion(ordinary):
        for end in range(1, 13):
            _assert_cold(source[:end], state)
        state.remember_source_flip()
        assert state._flip_checkpoint_ledger is not None
        frozen = _assert_cold(source[:18], state)
        frozen_points, frozen_candidates = mutable_points(frozen.points), mutable_points(frozen.candidates)
        _assert_cold(source[:19], state)
        checkpoint = state._checkpoint_ledger
        assert checkpoint is not None and state._ledger is not None
        assert checkpoint is not state._ledger
        assert checkpoint.buckets is not state._ledger.buckets
        for order in checkpoint.buckets.keys() & state._ledger.buckets.keys():
            saved, current = checkpoint.buckets[order], state._ledger.buckets[order]
            if saved is not current:
                assert saved.ordinary is not current.ordinary
                assert saved.events is not current.events
                assert saved.merged is not current.merged
        saved_buckets = {order: (deepcopy(bucket.ordinary), deepcopy(bucket.events), deepcopy(bucket.merged),
                                mutable_points(bucket.candidates))
                         for order, bucket in checkpoint.buckets.items()}
        for revision in range(3):
            revised = deepcopy(source[:19])
            revised[-1]['audit'] = {'tail_revision': [revision]}
            _assert_cold(revised, state)
        assert {order: (bucket.ordinary, bucket.events, bucket.merged, mutable_points(bucket.candidates))
                for order, bucket in checkpoint.buckets.items()} == saved_buckets
        assert state.tail_restores >= 3
        revised = deepcopy(source[:19])
        revised[12]['audit'] = {'after_flip': [True]}
        before_flip = state.flip_restores
        _assert_cold(revised, state)
        assert state.flip_restores == before_flip + 1
        revised[0]['audit'] = {'before_flip': [True]}
        before_cold = state.cold_runs
        _assert_cold(revised, state)
        assert state.cold_runs == before_cold + 1
        assert mutable_points(frozen.points) == frozen_points
        assert mutable_points(frozen.candidates) == frozen_candidates


def test_revised_tail_updates_a_small_ledger_suffix_instead_of_republishing_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source(1, False, 256)
    state = _TrendJudgementState(1, _tail_reuse=True, memory_bytes=32 * 1024 * 1024)
    _assert_cold(source[:-1], state)
    _assert_cold(source, state)
    updates: list[tuple[bool, int, int]] = []
    original = _JudgementLedger.update

    def counted(ledger: _JudgementLedger, ordinary: _TrendReversalState, upgrade: _TrendUpgradeState,
                events: list[dict[str, object]], positions: Sequence[int], *, initial: bool,
                level: int) -> tuple[TrendJudgement, list[object]]:
        result, changed = original(ledger, ordinary, upgrade, events, positions, initial=initial, level=level)
        updates.append((initial, len(changed), len(ledger.buckets)))
        return result, changed

    monkeypatch.setattr(trend_judgement._JudgementLedger, 'update', counted)
    for revision in range(4):
        source[-1]['audit'] = {'tail_revision': [revision]}
        _assert_cold(source, state)
    assert len(updates) == 4
    assert all(not initial for initial, _, _ in updates)
    assert all(changed < buckets for _, changed, buckets in updates)


def test_checkpoint_ledger_is_included_in_the_original_heap_budget() -> None:
    source = _source(1, False, 40)
    state = _TrendJudgementState(1, _tail_reuse=True)
    _assert_cold(source[:-1], state)
    _assert_cold(source, state)
    assert state._checkpoint_ledger is not None
    # 以真实包含账本检查点的对象图大小设门槛，不能用关闭计费保持缓存。
    restricted = _TrendJudgementState(1, _tail_reuse=True, memory_bytes=state._owned_bytes() - 1)
    for end in (len(source)-1, len(source)):
        _assert_cold(source[:end], restricted)
    if restricted._ordinary is not None:
        assert restricted._owned_bytes() <= restricted._limit
    restricted.clear()
    assert restricted._checkpoint_ledger is None
    assert restricted._flip_checkpoint_ledger is None
    assert restricted.memory_used == 0


@pytest.mark.parametrize('level', (1, 2, 3))
@pytest.mark.parametrize('mirror', (False, True))
def test_flip_checkpoint_budget_uses_a_safe_upper_bound_without_walking_history(
    level: int, mirror: bool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source(level, mirror, 96)
    state = _TrendJudgementState(level, _tail_reuse=True, memory_bytes=32 * 1024 * 1024)
    frozen = _assert_cold(source[:48], state)
    frozen_points, frozen_candidates = mutable_points(frozen.points), mutable_points(frozen.candidates)
    before_walks = state.deep_budget_walks

    def unexpected_full_walk() -> int:
        raise AssertionError('an admitted shallow flip snapshot must not walk historical evidence')

    with monkeypatch.context() as patch:
        patch.setattr(state, '_owned_bytes', unexpected_full_walk)
        state.remember_source_flip()
    assert state.deep_budget_walks == before_walks
    assert state._flip_checkpoint_ledger is not None
    assert state._owned_bytes() <= state.memory_used <= state._limit
    _assert_cold(source[:72], state)
    revised = deepcopy(source[:72])
    revised[48]['audit'] = {'after_flip': [True]}
    before_restores = state.flip_restores
    _assert_cold(revised, state)
    assert state.flip_restores == before_restores + 1
    assert mutable_points(frozen.points) == frozen_points
    assert mutable_points(frozen.candidates) == frozen_candidates


def test_flip_checkpoint_over_budget_is_dropped_after_exact_calibration() -> None:
    source = _source(1, False, 64)
    measure = _TrendJudgementState(1, _tail_reuse=True)
    _assert_cold(source, measure)
    current_bytes = measure._owned_bytes()
    measure.remember_source_flip()
    flip_bytes = measure._owned_bytes()
    assert flip_bytes > current_bytes
    state = _TrendJudgementState(1, _tail_reuse=True,
        memory_bytes=current_bytes + (flip_bytes - current_bytes) // 2)
    frozen = _assert_cold(source, state)
    before_walks = state.deep_budget_walks
    state.remember_source_flip()
    assert state.deep_budget_walks == before_walks + 2
    assert state._flip_checkpoint is None and state._flip_checkpoint_ledger is None
    assert state._ordinary is not None and state._ledger is not None
    assert state._owned_bytes() <= state.memory_used <= state._limit
    assert _assert_cold(source, state) == frozen


def test_flip_budget_clears_if_even_the_current_state_exceeds_the_limit() -> None:
    source = _source(1, False, 32)
    state = _TrendJudgementState(1, _tail_reuse=True)
    _assert_cold(source, state)
    state._limit = 1
    before_rejections = state.budget_rejections
    state.remember_source_flip()
    assert state.budget_rejections == before_rejections + 1
    assert state._ordinary is None and state._ledger is None
    assert state._flip_checkpoint is None and state._flip_checkpoint_ledger is None
    assert state.memory_used == 0
    _assert_cold(source, state)
