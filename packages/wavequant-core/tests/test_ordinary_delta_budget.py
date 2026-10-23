"""已付来源证明仅借用；发布账本的新外壳仍完整计入普通趋势容量。"""

from collections.abc import Mapping, Sequence
from random import Random

import pytest

from wavequant.domain.market_structure import trend_judgement as judgement
from wavequant.domain.market_structure.trend_judgement import (
    TrendJudgement, _EventRows, _FrozenPoint, _JudgementLedger, _OwnedLedgerChanges,
    _OwnedTrendJudgement, _TrendJudgementState, _finalize_judgement, _visible_source,
    judge_trend, mutable_points,
)


def _source(level: int = 1, count: int = 30, *, proof_size: int | None = None) -> list[dict[str, object]]:
    random = Random(31)
    proof = {'shared_observations': list(range(proof_size))} if proof_size is not None else None
    return [dict(index=index, ordinal=0, kind='H' if index % 2 else 'L',
        value=random.randint(30, 50) if index % 2 else random.randint(5, 25),
        time=str(index), available_at=index + 1, trend_level=level - 1, state='confirmed',
        audit=proof if proof is not None else {'source': [index]}) for index in range(count)]


def _owned(source: Sequence[Mapping[str, object]], state: _TrendJudgementState) -> TrendJudgement:
    return judge_trend(source, trend_level=state.trend_level, _append_state=state,
                       _owned_source=True, _certified_standard=True, _owned_result=True)


def _assert_cold(source: Sequence[Mapping[str, object]], state: _TrendJudgementState) -> TrendJudgement:
    actual = _owned(source, state)
    expected = judge_trend(source, trend_level=state.trend_level)
    assert type(actual) is _OwnedTrendJudgement
    assert mutable_points(actual.points) == mutable_points(expected.points)
    assert mutable_points(actual.candidates) == mutable_points(expected.candidates)
    assert state._ordinary is not None
    assert state._owned_bytes() <= state.memory_used <= state._limit
    return actual


def _prepare(state: _TrendJudgementState, source: Sequence[Mapping[str, object]]) -> tuple[
    list[dict[str, object]], list[dict[str, object]], _EventRows, tuple[int, ...],
]:
    admission = [False]
    visible, positions = _visible_source(source, state.trend_level - 1, None,
                                         _owned_source=True, _owned_admission=admission)
    assert admission == [True]
    ordinary, merged, events = state.calculate(visible, positions, _certified_standard=True)
    assert state._ordinary is not None and state._upgrade is not None
    assert state.memory_used >= state._owned_bytes()
    return ordinary, merged, events, positions


def test_publishing_shells_does_not_revisit_or_charge_the_shared_proof_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observations: list[tuple[int, int]] = []
    for proof_size in (10, 10000):
        state = _TrendJudgementState(1, memory_bytes=256 * 1024 * 1024)
        prepared = _prepare(state, _source(proof_size=proof_size))
        payload_before, walks_before = state._payload_upper_bytes, state.deep_budget_walks
        original_sizeof = judgement.getsizeof
        measured: set[int] = set()
        calls = 0

        def reject_old_proof(value: object) -> int:
            nonlocal calls
            # 也拒绝 calculate 产生的证明副本，不能只保护原始输入的 identity。
            assert not (type(value) is dict and 'shared_observations' in value), (
                'publishing shallow ledger shells walked an already paid proof')
            measured.add(id(value))
            calls += 1
            return original_sizeof(value)

        with monkeypatch.context() as patch:
            patch.setattr(judgement, 'getsizeof', reject_old_proof)
            result = state.finalize(*prepared, _owned_result=True)
        assert result.candidates and state._ledger is not None
        assert state.deep_budget_walks == walks_before
        assert state.memory_used >= state._owned_bytes()
        assert all(type(point) is dict for point in result.candidates)
        # 顶层索引另计容量；每个新桶及候选/几何壳不能随证明短路一起遗漏。
        for entry in state._ledger.buckets.values():
            shells: list[object] = [entry, vars(entry), entry.ordinary, entry.events,
                                   entry.base, entry.merged, entry.candidates, *entry.candidates]
            shells.extend(entry.merged)
            if entry.point is not None:
                shells.append(entry.point)
            assert all(id(shell) in measured for shell in shells)
        observations.append((calls, state._payload_upper_bytes - payload_before))
    small, large = observations
    assert small[0] == large[0]
    assert 0 < large[1] <= small[1] + 1024


@pytest.mark.parametrize('level', (1, 2, 3))
def test_owned_shell_budget_survives_tail_flip_and_prefix_revision(level: int) -> None:
    source = _source(level)
    state = _TrendJudgementState(level, _tail_reuse=True, memory_bytes=32 * 1024 * 1024)
    for end in range(1, 13):
        _assert_cold(source[:end], state)
    state.remember_source_flip()
    assert state._flip_checkpoint_ledger is not None
    frozen = _assert_cold(source[:18], state)
    frozen_points, frozen_candidates = mutable_points(frozen.points), mutable_points(frozen.candidates)
    _assert_cold(source[:19], state)
    assert state._checkpoint_ledger is not None
    before_tail = state.tail_restores
    revised = [dict(point) for point in source[:19]]
    revised[-1]['audit'] = {'tail_revision': [True]}
    _assert_cold(revised, state)
    assert state.tail_restores == before_tail + 1

    revised = [dict(point) for point in source[:19]]
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


@pytest.mark.parametrize('fits', (False, True))
def test_shell_upper_bound_recalibrates_and_rejects_only_a_true_overflow(fits: bool) -> None:
    state = _TrendJudgementState(1, memory_bytes=32 * 1024 * 1024)
    prepared = _prepare(state, _source(proof_size=128))
    first = state.finalize(*prepared, _owned_result=True)
    old_points, old_candidates = mutable_points(first.points), mutable_points(first.candidates)
    exact = state._owned_bytes()
    state._limit = exact + 1024 if fits else exact - 4096
    assert state._limit > 0
    # 重复追加产生保守上界时，应先精确校准，不能靠漏计维持命中。
    state._payload_upper_bytes += state._limit
    before_walks, before_rejections = state.deep_budget_walks, state.budget_rejections
    actual = state.finalize(*prepared, _owned_result=True)
    assert state.deep_budget_walks == before_walks + 1
    assert mutable_points(actual.points) == old_points
    assert mutable_points(actual.candidates) == old_candidates
    assert mutable_points(first.points) == old_points
    assert mutable_points(first.candidates) == old_candidates
    if fits:
        assert state._ledger is not None and state._ordinary is not None
        assert state._owned_bytes() <= state.memory_used <= state._limit
        assert state.budget_rejections == before_rejections
    else:
        assert state._ledger is None and state._ordinary is None
        assert state.memory_used == 0
        assert state.budget_rejections == before_rejections + 1


def test_public_mode_switch_keeps_the_recursive_frozen_point_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _TrendJudgementState(1, memory_bytes=32 * 1024 * 1024)
    prepared = _prepare(state, _source())
    private = state.finalize(*prepared, _owned_result=True)
    retained = mutable_points(private.candidates)
    measured: list[Sequence[object]] = []
    original_measure = state._owned_value_bytes

    def record_measure(roots: Sequence[object], *, _incremental: bool = False) -> int:
        if _incremental:
            measured.append(roots)
        return original_measure(roots, _incremental=_incremental)

    with monkeypatch.context() as patch:
        patch.setattr(state, '_owned_value_bytes', record_measure)
        public = state.finalize(*prepared)
    assert measured and all(type(roots) is list for roots in measured)
    assert type(public) is TrendJudgement
    assert public.candidates and all(type(point) is _FrozenPoint for point in public.candidates)
    assert mutable_points(public.candidates) == retained
    assert state.memory_used >= state._owned_bytes()
    restored = state.finalize(*prepared, _owned_result=True)
    assert type(restored) is _OwnedTrendJudgement
    assert mutable_points(restored.candidates) == retained
    assert mutable_points(private.candidates) == retained
    assert state.memory_used >= state._owned_bytes()


def test_external_events_cannot_borrow_the_calculated_graph_certificate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _TrendJudgementState(1, memory_bytes=32 * 1024 * 1024)
    ordinary, merged, events, positions = _prepare(state, _source())
    assert events and state._upgrade is not None
    external_events = [dict(event) for event in events]
    external_proof = {'external_observations': list(range(2048))}
    external_events[-1]['audit'] = external_proof
    expected = _finalize_judgement(ordinary, state._upgrade.snapshot(), external_events,
                                  positions, 1, _owned_result=True)
    original_sizeof = judgement.getsizeof
    saw_external_proof = False

    def record_external_proof(value: object) -> int:
        nonlocal saw_external_proof
        saw_external_proof |= value is external_proof
        return original_sizeof(value)

    with monkeypatch.context() as patch:
        patch.setattr(judgement, 'getsizeof', record_external_proof)
        actual = state.finalize(ordinary, merged, external_events, positions, _owned_result=True)
    assert saw_external_proof
    assert mutable_points(actual.points) == mutable_points(expected.points)
    assert mutable_points(actual.candidates) == mutable_points(expected.candidates)
    assert state.memory_used >= state._owned_bytes()


def test_formal_reconciliation_cannot_issue_an_ordinary_shell_certificate() -> None:
    state = _TrendJudgementState(2, memory_bytes=32 * 1024 * 1024)
    prepared = _prepare(state, _source(2))
    result = state.finalize(*prepared, _owned_result=True)
    assert result.candidates and state._upgrade is not None
    ledger = _JudgementLedger(_owned_result=True)
    reconciled, changed = ledger.reconcile_formal((), result.candidates, 0, (), result.points, 0,
                                                 state._upgrade, state._events, prepared[3], 2)
    assert type(changed) is list and not isinstance(changed, _OwnedLedgerChanges)
    assert changed and type(reconciled) is _OwnedTrendJudgement