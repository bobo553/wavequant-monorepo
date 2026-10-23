"""消费快照已付费的共享图不再遍历；撤销覆盖必须恢复当前状态的费用。"""

from collections.abc import Sequence
from copy import deepcopy
from typing import cast
from unittest.mock import patch

import pytest

from wavequant.domain.market_structure import trend_judgement
from wavequant.domain.market_structure.owned_evidence import (
    CertifiedSource, OwnedEvidence, owned_evidence_scope, owned_signature,
)
from wavequant.domain.market_structure.trend_judgement import (
    _FormalKeyBreakState, _OwnedTrendJudgement, mutable_points,
)


def _rows(level: int) -> list[dict[str, object]]:
    prices = (20, 30, 10, 20, 14, 26, 18, 35, 28, 45, 36, 42, 30, 36, 24, 31, 26, 50)
    audit = {'history': 'unchanged source evidence ' * 1000}
    rows: list[dict[str, object]] = [dict(
        index=index, ordinal=0, kind='H' if index % 2 else 'L', value=prices[index % len(prices)],
        time=str(index), available_at=index + 1, state='confirmed', trend_level=level - 1, audit=audit,
    ) for index in range(96)]
    rows[-3]['value'], rows[-2]['value'], rows[-1]['value'] = 30, 20, 50
    endpoint = rows[-1]
    rule = f'level{level - 2}_confirmed_high_breaks_known_level{level - 1}_last_fall_high'
    references = [{name: point[name] for name in ('index', 'ordinal', 'kind', 'value', 'time', 'available_at')}
                  for point in rows[-3:]]
    endpoint.update(
        source_level=level - 2, wave_completion_policy='whole_wave_v117', wave_identity_state='owned',
        wave_direction_before='up', wave_direction_after='up', confirmation_rule=rule,
        upgrade_evidence=dict(role='endpoint', direction='up', confirmation_rule=rule,
            available_at=endpoint['available_at'], broken_key=references[0], origin=references[1],
            confirmed_by=references[2]),
    )
    return rows


def _prepared(owner: OwnedEvidence, level: int = 2) -> tuple[
    CertifiedSource, _OwnedTrendJudgement, _FormalKeyBreakState,
]:
    rows = _rows(level)
    keys = [owned_signature(point) for point in rows]
    assert all(key is not None for key in keys)
    source = CertifiedSource(rows, cast(list[bytes], keys), owner, None)
    candidates = tuple(dict(source[index], trend_level=level, audit={'version': 0}) for index in (0, 1, 2, 70))
    base = _OwnedTrendJudgement(level, level - 1, (), candidates)
    state = _FormalKeyBreakState(level, memory_bytes=32 * 1024 * 1024)
    assert state.calculate(source, base, _certified_keys=source.keys) is not None
    assert state._consumption.frames
    return source, base, state


def test_covered_checkpoint_root_stops_traversal_but_uncovered_proof_is_charged() -> None:
    with owned_evidence_scope() as owner:
        _, _, state = _prepared(owner)
        cache = state._consumption
        shared = cache.frames[-1].runtime
        fresh = {'new proof': 'uncovered evidence ' * 1000}
        assert state._value_bytes([shared]) > 0
        expected = state._value_bytes([fresh], _covered_ids=cache._seen)
        assert expected > len(fresh['new proof'])
        with patch.object(trend_judgement, '_node_children', side_effect=AssertionError('rescanned a paid subtree')):
            assert state._value_bytes([shared], _covered_ids=cache._seen) == 0
            assert state._value_bytes([shared, fresh], _covered_ids=cache._seen) == expected
            assert state._value_bytes([shared, fresh], _incremental=True, _covered_ids=cache._seen) == expected


def test_shared_graph_fits_the_union_budget_without_disabling_valid_checkpoints() -> None:
    with owned_evidence_scope() as owner:
        _, _, state = _prepared(owner)
        cache = state._consumption
        frames = tuple(cache.frames)
        current = state._value_bytes(state._containers(), _covered_ids=cache._seen)
        duplicated = state._value_bytes(state._containers())
        assert current < duplicated
        union_budget = current + cache.retained_upper_bytes
        state._limit = union_budget
        state._retain(appended=False, added=())
        assert state.memory_used == union_budget
        assert len(cache.frames) == len(frames)
        assert all(actual is previous for actual, previous in zip(cache.frames, frames))
        assert not cache.stats.get('total_budget_rejections', 0)
        assert state._upgrade is not None


@pytest.mark.parametrize('withdrawal', ('drop', 'replace', 'disable', 'reset'))
def test_removed_coverage_transfers_charge_without_rewalking_an_append(withdrawal: str) -> None:
    with owned_evidence_scope() as owner:
        _, _, state = _prepared(owner)
        cache = state._consumption
        retired, frame_count = cache.retired_upper_bytes, len(cache.frames)
        charge = cache.frames[-1].charged if withdrawal == 'replace' else cache.memory_used
        payload = state._payload_upper_bytes
        assert state._upgrade is not None
        if withdrawal == 'drop':
            cache.discard_for_cold()
        elif withdrawal == 'replace':
            cache._drop_from(frame_count - 1)
            cache.capture(state._upgrade)
            assert len(cache.frames) == frame_count
        elif withdrawal == 'disable':
            cache.disable_for_budget()
        else:
            cache.reset()
        assert cache.retired_upper_bytes == retired + charge
        exact = state._value_bytes(state._containers(), _covered_ids=cache._seen) + cache.retained_upper_bytes
        with patch.object(state, '_value_bytes', wraps=state._value_bytes) as measure:
            state._retain(appended=True, added=())
            assert state._payload_upper_bytes == payload + charge
            assert state.memory_used >= exact
            state._retain(appended=True, added=())
            assert state._payload_upper_bytes == payload + charge
        assert all(call.kwargs.get('_incremental', False) for call in measure.call_args_list)
        assert state._retired_upper_bytes == cache.retired_upper_bytes
        assert state._upgrade is not None
        if withdrawal != 'replace':
            cache.reset()
            assert cache.retired_upper_bytes == retired + charge


def test_retired_charge_is_reconciled_only_when_the_conservative_budget_is_full() -> None:
    with owned_evidence_scope() as owner:
        _, _, state = _prepared(owner)
        cache = state._consumption
        cache.discard_for_cold()
        exact = state._value_bytes(state._containers(), _covered_ids=cache._seen) + cache.retained_upper_bytes
        state._limit = exact
        state._retain(appended=True, added=())
        assert state.memory_used == exact
        assert state._retired_upper_bytes == cache.retired_upper_bytes
        assert state._upgrade is not None


def test_old_formal_checkpoint_does_not_roll_back_the_retired_charge_counter() -> None:
    with owned_evidence_scope() as owner:
        _, _, state = _prepared(owner)
        saved = state._copy_checkpoint()
        cache = state._consumption
        prior_watermark = saved._retired_upper_bytes
        charge = cache.memory_used
        cache.discard_for_cold()
        restored = saved._copy_checkpoint()
        assert restored._consumption is cache
        assert restored._retired_upper_bytes == prior_watermark
        assert cache.retired_upper_bytes == prior_watermark + charge
        payload = restored._payload_upper_bytes
        with patch.object(restored, '_value_bytes', wraps=restored._value_bytes) as measure:
            restored._retain(appended=True, added=())
        assert all(call.kwargs.get('_incremental', False) for call in measure.call_args_list)
        assert restored._payload_upper_bytes == payload + charge
        assert restored._retired_upper_bytes == cache.retired_upper_bytes
        exact = restored._value_bytes(restored._containers(), _covered_ids=cache._seen) + cache.retained_upper_bytes
        assert restored.memory_used >= exact


@pytest.mark.parametrize('level', (2, 3))
def test_restored_shared_runtime_keeps_cold_results_and_old_snapshot_contents(level: int) -> None:
    with owned_evidence_scope() as owner:
        source, base, state = _prepared(owner, level)
        saved = next(frame for frame in state._consumption.frames if frame.processed == 64)
        old_events = deepcopy(list(cast(Sequence[dict[str, object]], saved.runtime['event_sink'])))
        revised = (*base.candidates[:-1], dict(base.candidates[-1], audit={'version': 1}))
        updated = _OwnedTrendJudgement(level, level - 1, (), revised)
        actual = state.calculate(source, updated, _certified_keys=source.keys)
        cold = _FormalKeyBreakState(level, memory_bytes=32 * 1024 * 1024, _consumption_checkpoints=False).calculate(
            source, updated, _certified_keys=source.keys,
        )
        assert state._consumption.stats['restores'] == 1
        assert actual is not None and cold is not None
        assert mutable_points(actual.points) == mutable_points(cold.points)
        assert mutable_points(actual.candidates) == mutable_points(cold.candidates)
        assert list(cast(Sequence[dict[str, object]], saved.runtime['event_sink'])) == old_events
        assert any(frame is saved for frame in state._consumption.frames)
        assert state._value_bytes([saved.runtime], _covered_ids=state._consumption._seen) == 0

@pytest.mark.parametrize('revision', ('source', 'candidate'))
def test_unmatched_formal_checkpoint_is_discarded_and_keeps_cold_results(revision: str) -> None:
    with owned_evidence_scope() as owner:
        source, base, state = _prepared(owner)
        extra = dict(source[80], trend_level=2, audit={'version': 0})
        expanded = _OwnedTrendJudgement(2, 1, (), (*base.candidates, extra))
        state.calculate(source, expanded, _certified_keys=source.keys)
        assert state._checkpoint is not None
        if revision == 'source':
            rows = [*source]
            rows[0] = dict(rows[0], audit={'revised source': True})
            keys = [owned_signature(point) for point in rows]
            assert all(key is not None for key in keys)
            source = CertifiedSource(rows, cast(list[bytes], keys), owner, source)
            updated = expanded
        else:
            revised = (dict(expanded.candidates[0], audit={'version': 1}), *expanded.candidates[1:])
            updated = _OwnedTrendJudgement(2, 1, (), revised)
        actual = state.calculate(source, updated, _certified_keys=source.keys)
        cold = _FormalKeyBreakState(2, _consumption_checkpoints=False).calculate(
            source, updated, _certified_keys=source.keys,
        )
        assert state._checkpoint is None
        assert actual is not None and cold is not None
        assert mutable_points(actual.points) == mutable_points(cold.points)
        assert mutable_points(actual.candidates) == mutable_points(cold.candidates)


@pytest.mark.parametrize('level', (2, 3))
def test_matching_current_and_saved_prefixes_keep_their_formal_checkpoint_paths(level: int) -> None:
    with owned_evidence_scope() as owner:
        source, base, state = _prepared(owner, level)
        original_keys = state._candidate_keys
        extra = dict(source[80], trend_level=level, audit={'version': 0})
        expanded = _OwnedTrendJudgement(level, level - 1, (), (*base.candidates, extra))
        actual = state.calculate(source, expanded, _certified_keys=source.keys)
        saved = state._checkpoint
        assert saved is not None
        assert saved._candidate_keys == original_keys
        cold = _FormalKeyBreakState(level, _consumption_checkpoints=False).calculate(
            source, expanded, _certified_keys=source.keys,
        )
        assert actual is not None and cold is not None
        assert mutable_points(actual.points) == mutable_points(cold.points)
        assert mutable_points(actual.candidates) == mutable_points(cold.candidates)

        revised = (*base.candidates, dict(extra, audit={'version': 1}))
        updated = _OwnedTrendJudgement(level, level - 1, (), revised)
        actual = state.calculate(source, updated, _certified_keys=source.keys)
        assert state._checkpoint is saved
        cold = _FormalKeyBreakState(level, _consumption_checkpoints=False).calculate(
            source, updated, _certified_keys=source.keys,
        )
        assert actual is not None and cold is not None
        assert mutable_points(actual.points) == mutable_points(cold.points)
        assert mutable_points(actual.candidates) == mutable_points(cold.candidates)
