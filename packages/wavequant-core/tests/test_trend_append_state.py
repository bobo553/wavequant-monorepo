"""只追加状态与每个真实前缀的完整批量判断保持一致。"""

from collections.abc import Sequence
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from random import Random
import pickle
from typing import Never, cast

import pytest

from wavequant.domain.market_structure import trend_judgement as judgement_module
from wavequant.domain.market_structure.trend_levels import (
    _SourceTurn, _TrendReversalState, _source_turns, trend_reversals,
)
from wavequant.domain.market_structure.trend_level_upgrades import _TrendUpgradeState, promote_trend_key_breaks
from wavequant.domain.market_structure.trend_judgement import (
    _OwnedPointAdmission, _TrendJudgementState, _frozen_point_scope, _owned_mutable_points,
    _owned_point_admission, judge_trend, mutable_points,
)
from wavequant.domain.market_structure.trend_lines import _TrendJudgementCache


def _points(prices: Sequence[int], *, level: int, clock: str) -> list[dict[str, object]]:
    start = datetime(2020, 1, 1)
    points: list[dict[str, object]] = []
    for index, price in enumerate(prices):
        time = (start + timedelta(days=index)).date().isoformat() if clock == 'date' else str(index)
        known: int | str = index + 1 if clock == 'integer' else (
            (start + timedelta(days=index + 1)).date().isoformat() if clock == 'date' else str(index + 1))
        points.append(dict(index=index, ordinal=0, kind='L' if index % 2 == 0 else 'H', value=price,
                           time=time, available_at=known, trend_level=level, state='confirmed',
                           label=f'actual-{index}', audit=dict(order=[index, 'source'])))
    return points


@pytest.mark.parametrize('level', (1, 2, 3))
@pytest.mark.parametrize('clock', ('integer', 'numeric', 'date'))
def test_private_source_transfer_matches_complete_cold_proof_at_every_real_prefix(level: int, clock: str) -> None:
    source = _points((20, 30, 10, 20, 14, 26, 18, 35, 28, 45, 38, 42, 32, 36, 24, 29, 26, 50),
                     level=level - 1, clock=clock)
    original = deepcopy(source)
    cache = _TrendJudgementCache()
    for end in range(len(source) + 1):
        # 每个来源属于本次私有工作集；转移后只读取它，不向任何公共输出借出。
        transferred = deepcopy(source[:end])
        observed = cache.judge(transferred, path='owned', trend_level=level, _owned_source=True)
        expected = judge_trend(source[:end], trend_level=level)
        assert mutable_points(observed.points) == mutable_points(expected.points)
        assert mutable_points(observed.candidates) == mutable_points(expected.candidates)
        assert transferred == source[:end]
    assert source == original


def test_source_transfer_owns_nested_evidence_while_default_copies_remain_independent() -> None:
    private = _points((10, 30, 20, 40), level=0, clock='integer')
    owned_state, public_state = _TrendJudgementState(1), _TrendJudgementState(1)
    owned = judge_trend(private, _append_state=owned_state, _owned_source=True)
    public = judge_trend(private, _append_state=public_state)
    assert mutable_points(owned.candidates) == mutable_points(public.candidates)
    assert owned_state._ordinary is not None and public_state._ordinary is not None
    assert owned_state._ordinary.turns[0].source['audit'] is private[0]['audit']
    assert public_state._ordinary.turns[0].source['audit'] is not private[0]['audit']
    assert owned_state.memory_used >= owned_state._owned_bytes()


@pytest.mark.parametrize('level', (1, 2, 3))
@pytest.mark.parametrize('clock', ('integer', 'numeric', 'date'))
def test_bounded_tail_restore_matches_cold_after_every_endpoint_and_audit_revision(level: int, clock: str) -> None:
    source = _points((20, 30, 10, 20, 14, 26, 18, 35, 28, 45, 38, 42, 32, 36, 24, 29, 26, 50),
                     level=level - 1, clock=clock)
    state = _TrendJudgementState(level, _tail_reuse=True)
    for end in range(1, len(source) + 1):
        current = deepcopy(source[:end])
        judge_trend(current, trend_level=level, _append_state=state)
        for change in range(3):
            current[-1]['value'] = cast(int, source[end-1]['value']) + (change+1) * (1 if end % 2 == 0 else -1)
            current[-1]['audit'] = dict(revised_tail=change, order=[end, 'new'])
            actual = judge_trend(current, trend_level=level, _append_state=state)
            expected = judge_trend(current, trend_level=level)
            assert mutable_points(actual.points) == mutable_points(expected.points)
            assert mutable_points(actual.candidates) == mutable_points(expected.candidates)
            assert state.memory_used >= state._owned_bytes()
            assert state.memory_used <= state._limit
        source[end-1] = deepcopy(current[-1])
    assert state.tail_restores > 0


def test_tail_checkpoint_rejects_an_earlier_full_source_revision() -> None:
    source = _points((10, 30, 20, 40, 15, 35), level=0, clock='integer')
    state = _TrendJudgementState(1, _tail_reuse=True)
    for end in range(1, len(source)+1):
        judge_trend(source[:end], _append_state=state)
    before = state.tail_restores
    source[0]['audit'] = dict(earlier_revision=True)
    actual = judge_trend(source, _append_state=state)
    expected = judge_trend(source)
    assert mutable_points(actual.candidates) == mutable_points(expected.candidates)
    assert state.tail_restores == before




@pytest.mark.parametrize('value', (float('nan'), float('inf'), -float('inf')))
def test_source_transfer_nonfinite_audit_remains_current_and_cold(value: float) -> None:
    source = _points((10, 30, 20, 40, 15, 35), level=0, clock='integer')
    for point in source:
        point['audit'] = dict(metadata=[value])
    state = _TrendJudgementState(1)
    observed = judge_trend(source, _append_state=state, _owned_source=True)
    assert state._ordinary is None and state.memory_used == 0
    assert observed.candidates
    for judged_point in observed.candidates:
        audit = cast(dict[str, object], mutable_points([judged_point])[0]['audit'])
        assert cast(list[object], audit['metadata'])[0] is value


def test_source_transfer_unknown_ignored_audit_keeps_original_copy_failure() -> None:
    events: list[str] = []

    class Audit:
        def __deepcopy__(self, memo: dict[int, object]) -> object:
            events.append('copy')
            raise RuntimeError('ignored audit copy')

        def __reduce__(self) -> Never:
            raise AssertionError('unknown serialization hook')

    source = _points((10, 30, 20, 40), level=0, clock='integer')
    source.append(dict(state='seed', trend_level=0, audit=Audit()))
    for owned in (False, True):
        with pytest.raises(RuntimeError, match='ignored audit copy'):
            judge_trend(source, _owned_source=owned)
    assert events == ['copy', 'copy']


@pytest.mark.parametrize('field,value', (('value', True), ('available_at', 1.0), ('trend_level', False), ('ordinal', 0.0)))
def test_source_transfer_never_skips_invalid_type_validation(field: str, value: object) -> None:
    source = _points((10, 30, 20, 40), level=0, clock='integer')
    state = _TrendJudgementState(1)
    judge_trend(source, _append_state=state, _owned_source=True)
    revised = deepcopy(source)
    revised[0][field] = value
    with pytest.raises(ValueError):
        judge_trend(revised, _append_state=state, _owned_source=True)
    assert state._ordinary is None and state.memory_used == 0


def test_owned_admission_reuses_full_proof_only_within_its_stage_and_thread() -> None:
    source = _points((10, 30, 20, 40), level=0, clock='integer')
    with _frozen_point_scope():
        admission = _owned_point_admission.get()
        assert admission is not None
        observed = _owned_mutable_points(source, True)
        assert observed[0]['audit'] is source[0]['audit']
        assert admission._misses == len(source) and admission._hits == 0
        repeated = _owned_mutable_points(deepcopy(source), True)
        assert repeated == observed
        assert admission._hits == len(source)
        current_memory = admission.memory_used
        with ThreadPoolExecutor(max_workers=1) as executor:
            assert executor.submit(admission.certify, source[0]).result() is False
        assert admission.memory_used == current_memory
        with _frozen_point_scope():
            nested = _owned_point_admission.get()
            assert nested is not None and nested is not admission
            _owned_mutable_points(source, True)
        assert nested._closed and nested.memory_used == 0
        assert _owned_point_admission.get() is admission
    assert admission._closed and admission.memory_used == 0
    assert _owned_point_admission.get() is None


def test_owned_admission_full_types_and_current_nonfinite_values_cannot_hit_old_certificate() -> None:
    source = dict(audit=[1, 1.0, True, -0.0, {'evidence': ['original']}])
    admission = _OwnedPointAdmission()
    assert admission.certify(source)
    assert admission.certify(deepcopy(source)) and admission._hits == 1
    for changed in ([1.0, 1.0, True, -0.0, {'evidence': ['original']}],
                    [1, 1, True, -0.0, {'evidence': ['original']}],
                    [1, 1.0, 1, -0.0, {'evidence': ['original']}],
                    [1, 1.0, True, 0.0, {'evidence': ['original']}],
                    [1, 1.0, True, -0.0, {'evidence': ['revised']}]):
        assert admission.certify(dict(audit=changed))
        assert admission._hits == 1
    count = len(admission._points)
    for value in (float('nan'), float('inf'), -float('inf')):
        assert admission.certify(dict(audit=[value])) is False
    assert len(admission._points) == count
    assert admission.certify(dict(audit=(1, 2))) is False
    assert admission.certify(dict(audit={1: 'nonstring'})) is False


def test_owned_admission_charges_real_byte_objects_and_dict_capacity_and_refuses_new_versions() -> None:
    from sys import getsizeof

    admission = _OwnedPointAdmission(memory_bytes=6000)
    first: bytes | None = None
    for index in range(100):
        assert admission.certify(dict(audit=['proof', str(index) * 7]))
        if first is None and admission._points:
            first = next(iter(admission._points))
        actual = getsizeof(admission._points) + sum(getsizeof(key) for key in admission._points)
        if admission._points:
            assert actual < admission.memory_used <= admission._limit
        else:
            assert admission.memory_used == 0
    assert admission._refusals > 0 and first is not None and first in admission._points
    assert admission.certify(dict(audit=['proof', '0' * 7]))
    assert admission._hits > 0
    tiny = _OwnedPointAdmission(memory_bytes=1)
    assert tiny.certify(dict(audit=['valid']))
    assert tiny.memory_used == 0 and tiny._refusals == 1


def test_owned_admission_scope_closes_on_copy_failure() -> None:
    with pytest.raises(ValueError, match='own stage failure'):
        with _frozen_point_scope():
            admission = _owned_point_admission.get()
            assert admission is not None
            assert admission.certify(dict(audit=['valid']))
            raise ValueError('own stage failure')
    assert admission is not None and admission._closed and admission.memory_used == 0
    assert _owned_point_admission.get() is None


@pytest.mark.parametrize('level', (1, 2, 3))
@pytest.mark.parametrize('clock', ('integer', 'numeric', 'date'))
@pytest.mark.parametrize('seed', range(12))
def test_every_appended_prefix_matches_batch_ordinary_owned_geometry_and_events(level: int, clock: str, seed: int) -> None:
    random = Random(seed)
    source = _points([random.randint(5, 50) for _ in range(32)], level=level - 1, clock=clock)
    original = deepcopy(source)
    ordinary_state = _TrendReversalState(level)
    upgrade_state: _TrendUpgradeState | None = None
    events: list[dict[str, object]] = []
    previous: list[_SourceTurn] = []
    previous_regular: list[_SourceTurn] = []
    appended_count = 0
    for end in range(len(source) + 1):
        prefix = source[:end]
        turns = _source_turns(prefix, level - 1)
        ordinary = ordinary_state.extend(turns[len(previous):])
        assert ordinary == trend_reversals(prefix, trend_level=level)
        regular = _source_turns(ordinary, level, retain_geometry=True, accept_direction_facts=True)
        if upgrade_state is not None and upgrade_state.append(turns[len(previous):], regular[len(previous_regular):]):
            appended_count += 1
        else:
            events = []
            upgrade_state = _TrendUpgradeState(turns, regular, trend_level=level, event_sink=events)
        observed = upgrade_state.advance()
        expected_events: list[dict[str, object]] = []
        expected = promote_trend_key_breaks(prefix, ordinary, trend_level=level, event_sink=expected_events)
        assert observed == expected
        assert events == expected_events
        previous, previous_regular = turns, regular
    assert appended_count > 20
    assert source == original


@pytest.mark.parametrize('delay', (0, -1))
def test_new_same_or_earlier_knowledge_candidate_refuses_without_mutating_state(delay: int) -> None:
    source = _points((10, 30, 20, 40), level=0, clock='integer')
    turns = _source_turns(source, 0)
    state = _TrendUpgradeState(turns, [], trend_level=1)
    expected = state.advance()
    candidate = dict(source[1], trend_level=1, available_at=4 + delay)
    regular = _source_turns([candidate], 1, retain_geometry=True, accept_direction_facts=True)
    before = (list(state.turns), list(state.regular), state.regular_cursor, state.processed_count,
              dict(state.positions), list(state.orders))
    assert state.append([], regular) is False
    assert (state.turns, state.regular, state.regular_cursor, state.processed_count, state.positions, state.orders) == before
    assert state.advance() == expected


def test_pending_future_candidate_refuses_append_and_matches_batch_after_rebuild() -> None:
    source = _points((10, 30, 20, 40, 15, 35), level=0, clock='integer')
    candidate = dict(source[1], trend_level=1, available_at=6)
    regular = _source_turns([candidate], 1, retain_geometry=True, accept_direction_facts=True)
    turns = _source_turns(source[:4], 0)
    state = _TrendUpgradeState(turns, regular, trend_level=1)
    assert state.advance() == promote_trend_key_breaks(source[:4], [candidate], trend_level=1)
    assert state.append(_source_turns(source[4:], 0), []) is False
    assert _TrendUpgradeState(_source_turns(source, 0), regular, trend_level=1).advance() == (
        promote_trend_key_breaks(source, [candidate], trend_level=1))


def test_equal_extreme_and_same_index_ordinal_keep_original_first_source_position() -> None:
    source = _points((10, 30, 20, 40, 20, 40, 15, 35, 10, 30), level=0, clock='integer')
    for index, point in enumerate(source):
        point.update(index=index // 2, ordinal=index % 2, time=str(index // 2), available_at=index // 2 + 1)
    state = _TrendReversalState(1)
    for end in range(len(source) + 1):
        assert state.extend(_source_turns(source[:end], 0)[-1:] if end else []) == (
            trend_reversals(source[:end], trend_level=1))


@pytest.mark.parametrize('level', (True, 1.0, 0, 4))
def test_state_levels_preserve_strict_batch_rejection(level: object) -> None:
    with pytest.raises(ValueError, match='trend_level'):
        _TrendReversalState(cast(int, level))
    with pytest.raises(ValueError, match='trend_level'):
        _TrendUpgradeState([], [], trend_level=cast(int, level))


@pytest.mark.parametrize('level', (1, 2, 3))
@pytest.mark.parametrize('clock', ('integer', 'numeric', 'date'))
@pytest.mark.parametrize('seed', range(6))
def test_cached_judgement_matches_every_cold_prefix_with_all_proofs(level: int, clock: str, seed: int) -> None:
    random = Random(seed)
    source = _points([random.randint(30, 50) if index % 2 else random.randint(5, 25)
                      for index in range(32)], level=level - 1, clock=clock)
    cache = _TrendJudgementCache()
    for end in range(len(source) + 1):
        prefix = deepcopy(source[:end])
        observed = cache.judge(prefix, path='complete', trend_level=level)
        expected = judge_trend(prefix, trend_level=level)
        assert mutable_points(observed.points) == mutable_points(expected.points)
        assert mutable_points(observed.candidates) == mutable_points(expected.candidates)
        state = cache._states[(level, 'complete')]
        assert state.memory_used >= state._owned_bytes()
        assert state.memory_used <= state._limit
    state = cache._states[(level, 'complete')]
    assert state.append_runs > 20
    assert state.cold_runs == 1


def test_cached_judgement_rebuilds_for_same_value_type_source_revision_and_position_change() -> None:
    source = _points((20, 30, 10, 20, 14, 26, 18, 35, 28, 45, 38, 42, 32, 36, 24, 29, 26, 50),
                     level=0, clock='integer')
    cache = _TrendJudgementCache()
    cache.judge(source[:12], path='actual', trend_level=1)
    cache.judge(source, path='actual', trend_level=1)
    state = cache._states[(1, 'actual')]
    assert state.append_runs == 1
    for field, value in (('value', 10.0), ('label', 'revised-label'), ('available_at', 4)):
        revised = deepcopy(source)
        revised[2][field] = value
        observed = cache.judge(revised, path='actual', trend_level=1)
        expected = judge_trend(revised)
        assert mutable_points(observed.points) == mutable_points(expected.points)
        assert mutable_points(observed.candidates) == mutable_points(expected.candidates)
    source.insert(0, dict(source[0], state='seed'))
    observed = cache.judge(source, path='actual', trend_level=1)
    assert mutable_points(observed.candidates) == mutable_points(judge_trend(source).candidates)
    assert state.cold_runs == 5


def test_source_flip_checkpoint_replays_revised_suffix_and_rejects_changed_prefix() -> None:
    source = _points((20, 30, 10, 20, 14, 26, 18, 35, 28, 45, 38, 42, 32, 36, 24, 29, 26, 50),
                     level=0, clock='integer')
    cache = _TrendJudgementCache(tail_reuse=True)
    cache.judge(source[:8], path='source', trend_level=1)
    cache.remember_source_flip(1, 'source')
    state = cache._states[(1, 'source')]
    assert state._flip_checkpoint is not None
    cache.judge(source[:12], path='source', trend_level=1)
    cache.judge(source, path='source', trend_level=1)
    revised = deepcopy(source)
    revised[10]['label'] = 'revised after flip'
    observed = cache.judge(revised, path='source', trend_level=1)
    expected = judge_trend(revised)
    assert mutable_points(observed.points) == mutable_points(expected.points)
    assert mutable_points(observed.candidates) == mutable_points(expected.candidates)
    assert state.flip_restores == 1
    assert state.memory_used <= state._limit

    cold_runs = state.cold_runs
    revised[2]['label'] = 'changed before flip'
    observed = cache.judge(revised, path='source', trend_level=1)
    expected = judge_trend(revised)
    assert mutable_points(observed.points) == mutable_points(expected.points)
    assert mutable_points(observed.candidates) == mutable_points(expected.candidates)
    assert state.flip_restores == 1
    assert state.cold_runs == cold_runs + 1


def test_cached_same_knowledge_facts_rebuild_instead_of_appending_to_consumed_attacks() -> None:
    source = _points((20, 30, 10, 20, 14, 26, 18, 35, 28, 45, 38, 42, 32, 36, 24, 29, 26, 50),
                     level=0, clock='integer')
    for point in source:
        point['available_at'] = len(source)
    cache = _TrendJudgementCache()
    for end in range(len(source) + 1):
        observed = cache.judge(source[:end], path='late', trend_level=1)
        expected = judge_trend(source[:end])
        assert mutable_points(observed.points) == mutable_points(expected.points)
        assert mutable_points(observed.candidates) == mutable_points(expected.candidates)
    assert cache._states[(1, 'late')].cold_runs > 2


def test_judgement_state_over_budget_and_cross_thread_fall_back_without_retained_state() -> None:
    source = _points((20, 30, 10, 20, 14, 26, 18, 35, 28, 45, 38, 42), level=0, clock='integer')
    expected = judge_trend(source)
    tiny = _TrendJudgementState(1, memory_bytes=1)
    observed = judge_trend(source, _append_state=tiny)
    assert mutable_points(observed.candidates) == mutable_points(expected.candidates)
    assert tiny._ordinary is None and tiny._upgrade is None and tiny._source_keys == ()
    state = _TrendJudgementState(1)
    with ThreadPoolExecutor(max_workers=1) as executor:
        observed = executor.submit(judge_trend, source, _append_state=state).result()
    assert mutable_points(observed.points) == mutable_points(expected.points)
    assert mutable_points(observed.candidates) == mutable_points(expected.candidates)
    assert state._ordinary is None and state.cold_runs == state.append_runs == 0


def test_same_index_multiple_ordinals_preserve_full_batch_source_proofs_in_cached_judgement() -> None:
    random = Random(19)
    source = _points([random.randint(5, 10) for _ in range(40)], level=0, clock='integer')
    for index, point in enumerate(source):
        point.update(index=index // 8, ordinal=index % 8, time=str(index // 8))
    cache = _TrendJudgementCache()
    for end in range(len(source) + 1):
        observed = cache.judge(source[:end], path='ordinals', trend_level=1)
        expected = judge_trend(source[:end])
        assert mutable_points(observed.points) == mutable_points(expected.points)
        assert mutable_points(observed.candidates) == mutable_points(expected.candidates)


@pytest.mark.parametrize('failure', ('regular', 'append', 'advance', 'finalize'))
def test_a_failed_append_discards_partial_state_and_recovers_with_full_cold_proofs(
    monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    source = _points((20, 30, 10, 20, 14, 26, 18, 35, 28, 45, 38, 42, 32, 36, 24, 29, 26, 50),
                     level=0, clock='integer')
    cache = _TrendJudgementCache()
    cache.judge(source[:12], path='actual', trend_level=1)
    state = cache._states[(1, 'actual')]

    def reject(*args: object, **kwargs: object) -> object:
        raise RuntimeError('injected append failure')

    with monkeypatch.context() as temporary:
        if failure == 'regular':
            original = judgement_module._source_turns

            def parse(points: Sequence[dict[str, object]], source_level: int, *, retain_geometry: bool = False,
                      accept_direction_facts: bool = False, _position_offset: int = 0) -> list[_SourceTurn]:
                if accept_direction_facts:
                    raise RuntimeError('injected append failure')
                return original(points, source_level, retain_geometry=retain_geometry,
                                accept_direction_facts=accept_direction_facts, _position_offset=_position_offset)

            temporary.setattr(judgement_module, '_source_turns', parse)
        elif failure == 'finalize':
            temporary.setattr(_TrendJudgementState, 'finalize', reject)
        else:
            temporary.setattr(_TrendUpgradeState, failure, reject)
        with pytest.raises(RuntimeError, match='injected append failure'):
            cache.judge(source, path='actual', trend_level=1)
    assert state._ordinary is None and state._upgrade is None and state._events == []
    assert (1, 'actual') not in cache._states and (1, 'actual') not in cache._paths
    observed = cache.judge(source, path='actual', trend_level=1)
    expected = judge_trend(source)
    assert mutable_points(observed.points) == mutable_points(expected.points)
    assert mutable_points(observed.candidates) == mutable_points(expected.candidates)


@pytest.mark.parametrize('value', (float('nan'), float('inf'), -float('inf')))
def test_private_append_state_preserves_current_nonfinite_leaf_identity_and_keeps_no_state(value: float) -> None:
    source = _points((20, 30, 10, 20, 14, 26, 18, 35, 28, 45, 38, 42), level=0, clock='integer')
    state = _TrendJudgementState(1)
    judge_trend(source, _append_state=state)
    source[2]['audit'] = dict(nonfinite=value)
    observed = judge_trend(source, _append_state=state)
    assert cast(dict[str, object], observed.points[0]['audit'])['nonfinite'] is value
    assert state._ordinary is None and state._source_keys == ()


def test_judgement_heap_budget_charges_python_containers_and_keys_not_only_pickle_bytes() -> None:
    source = _points((20, 30, 10, 20, 14, 26, 18, 35, 28, 45, 38, 42), level=0, clock='integer')
    source[2]['audit'] = dict(many_small_containers=[dict(item=index) for index in range(200)])
    state = _TrendJudgementState(1)
    judge_trend(source, _append_state=state)
    assert state.memory_used >= state._owned_bytes()
    assert state.memory_used > sum(map(len, state._source_keys))
    restricted = _TrendJudgementState(1, memory_bytes=state._owned_bytes() - 1)
    observed = judge_trend(source, _append_state=restricted)
    assert mutable_points(observed.candidates) == mutable_points(judge_trend(source).candidates)
    assert restricted.memory_used == 0 and restricted._ordinary is None
    assert restricted.peak_observed_bytes > restricted._limit


def test_append_ownership_upper_bound_covers_tree_growth_replaced_keys_and_proof_aliases() -> None:
    random = Random(31)
    source = _points([random.randint(30, 50) if index % 2 else random.randint(5, 25)
                      for index in range(130)], level=0, clock='integer')
    audit: dict[str, object] = dict(shared=[dict(proof=index) for index in range(40)])
    for point in source:
        point['audit'] = audit
    state = _TrendJudgementState(1)
    previous_source_keys: tuple[bytes, ...] = ()
    for end in range(1, len(source) + 1):
        observed = judge_trend(source[:end], _append_state=state)
        expected = judge_trend(source[:end])
        assert mutable_points(observed.points) == mutable_points(expected.points)
        assert mutable_points(observed.candidates) == mutable_points(expected.candidates)
        assert state.memory_used >= state._owned_bytes()
        assert state.memory_used <= state._limit
        assert all(old is new for old, new in zip(previous_source_keys, state._source_keys))
        previous_source_keys = state._source_keys
    assert state.append_runs == 129
    assert state.deep_budget_walks == state.cold_runs == 1
    assert state.budget_rejections == 0


def test_ownership_budget_rechecks_a_conservative_bound_before_rejecting_real_heap() -> None:
    source = _points((20, 30, 10, 20, 14, 26, 18, 35, 28, 45, 38, 42, 32, 36, 24, 29, 26, 50),
                     level=0, clock='integer')
    state = _TrendJudgementState(1)
    judge_trend(source[:12], _append_state=state)
    state._payload_upper_bytes = state._limit
    observed = judge_trend(source, _append_state=state)
    assert mutable_points(observed.candidates) == mutable_points(judge_trend(source).candidates)
    assert state.deep_budget_walks == 2 and state.budget_rejections == 0
    assert state.memory_used >= state._owned_bytes()


@pytest.mark.parametrize('clock', ('integer', 'numeric', 'date'))
def test_validated_visible_turns_keep_original_positions_across_ignored_gaps_and_late_insertion(clock: str) -> None:
    source = _points((20, 30, 10, 20, 14, 26, 18, 35, 28, 45, 38, 42, 32, 36, 24, 29, 26, 50),
                     level=0, clock=clock)
    source[0]['available_at'] = 40 if clock == 'integer' else ('40' if clock == 'numeric' else '2020-02-10')
    source.insert(0, dict(source[0], state='seed'))
    source.insert(4, dict(source[3], state='developing'))
    source.insert(8, dict(source[6], display_only=True))
    state = _TrendJudgementState(1)
    for index in (6, 13, 18, 40):
        cutoff: int | str = index if clock == 'integer' else (
            str(index) if clock == 'numeric' else (datetime(2020, 1, 1) + timedelta(days=index)).date().isoformat())
        observed = judge_trend(source, asof=cutoff, _append_state=state)
        expected = judge_trend(source, asof=cutoff)
        assert mutable_points(observed.points) == mutable_points(expected.points)
        assert mutable_points(observed.candidates) == mutable_points(expected.candidates)
        assert state.memory_used >= state._owned_bytes()
    assert state.cold_runs >= 2


@pytest.mark.parametrize('change', ('clock', 'price', 'level'))
def test_validated_turn_reuse_does_not_hide_invalid_source_revisions(change: str) -> None:
    source = _points((20, 30, 10, 20, 14, 26, 18, 35, 28, 45, 38, 42), level=0, clock='integer')
    state = _TrendJudgementState(1)
    judge_trend(source, _append_state=state)
    revised = deepcopy(source)
    if change == 'clock':
        revised[-1]['available_at'] = str(revised[-1]['available_at'])
    elif change == 'price':
        revised[2]['value'] = True
    else:
        revised[-1]['trend_level'] = False
    with pytest.raises(ValueError) as cold_error:
        judge_trend(revised)
    with pytest.raises(ValueError, match=str(cold_error.value)):
        judge_trend(revised, _append_state=state)
    assert state._ordinary is None and state._upgrade is None
    assert mutable_points(judge_trend(source, _append_state=state).candidates) == mutable_points(judge_trend(source).candidates)


def test_tail_checkpoint_copies_mutable_containers_without_serializing_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _points((20, 30, 10, 20, 14, 26, 18, 35, 28, 45, 38, 42), level=0, clock='integer')
    state = _TrendJudgementState(1, _tail_reuse=True)

    def reject_serialization(*args: object, **kwargs: object) -> Never:
        raise AssertionError('a tail checkpoint serialized the whole history')

    monkeypatch.setattr(pickle, 'dumps', reject_serialization)
    for end in range(1, len(source)+1):
        observed = judge_trend(source[:end], _append_state=state)
        assert mutable_points(observed.candidates) == mutable_points(judge_trend(source[:end]).candidates)
        if state._checkpoint is not None:
            saved_ordinary, saved_upgrade, saved_events = state._checkpoint[2:]
            assert state._ordinary is not None and state._upgrade is not None
            assert saved_ordinary.turns is not state._ordinary.turns
            assert saved_upgrade.keys_by_kind['H'].points is not state._upgrade.keys_by_kind['H'].points
            assert saved_events is not state._events and saved_upgrade.event_sink is saved_events
    revised = deepcopy(source)
    revised[-1]['audit'] = {'tail_revision': True}
    observed = judge_trend(revised, _append_state=state)
    expected = judge_trend(revised)
    assert mutable_points(observed.points) == mutable_points(expected.points)
    assert mutable_points(observed.candidates) == mutable_points(expected.candidates)
    assert state.tail_restores > 0 and state.memory_used >= state._owned_bytes()
