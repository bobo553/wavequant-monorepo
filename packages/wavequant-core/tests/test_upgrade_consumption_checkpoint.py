from collections.abc import Mapping, Sequence
from copy import deepcopy
import json
from typing import cast

import pytest

from wavequant.domain.market_structure.owned_evidence import (
    CertifiedSource, OwnedEvidence, owned_evidence_scope, owned_signature,
)
from wavequant.domain.market_structure.trend_judgement import (
    _FormalKeyBreakState, _OwnedTrendJudgement, mutable_points,
)
from wavequant.domain.market_structure.trend_level_upgrades import _TrendUpgradeState
from wavequant.domain.market_structure.trend_levels import _SourceTurn, _source_turns
from wavequant.domain.market_structure.trend_upgrade_checkpoint import _UpgradeConsumptionCache


def _rows(level: int, count: int = 96) -> list[dict[str, object]]:
    prices = (20, 30, 10, 20, 14, 26, 18, 35, 28, 45, 36, 42, 30, 36, 24, 31, 26, 50)
    return [dict(index=index, ordinal=0, kind='H' if index % 2 else 'L', value=prices[index % len(prices)],
        time=str(index), available_at=index + 1, state='confirmed', trend_level=level - 1)
        for index in range(count)]


def _certify(rows: Sequence[dict[str, object]], owner: OwnedEvidence,
             previous: CertifiedSource | None = None) -> CertifiedSource:
    keys = [owned_signature(point) for point in rows]
    assert all(key is not None for key in keys)
    return CertifiedSource(rows, cast(list[bytes], keys), owner, previous)


def _candidates(source: Sequence[Mapping[str, object]], level: int,
                indices: Sequence[int] = (0, 1, 2, 70)) -> list[dict[str, object]]:
    return [dict(source[index], trend_level=level, audit={'version': 0}) for index in indices]


def _inputs(source: CertifiedSource, candidates: Sequence[Mapping[str, object]], level: int
            ) -> tuple[list[_SourceTurn], list[_SourceTurn], tuple[str, ...], list[int]]:
    turns = _source_turns(source, level - 1, formal_key_attacks=True)
    regular: list[_SourceTurn] = []
    offsets = [0]
    for candidate in candidates:
        regular.extend(_source_turns([candidate], level, retain_geometry=True, accept_direction_facts=True))
        offsets.append(len(regular))
    return turns, regular, tuple(json.dumps(point, sort_keys=True) for point in candidates), offsets


def _run(cache: _UpgradeConsumptionCache, source: CertifiedSource,
         candidates: Sequence[Mapping[str, object]], level: int) -> tuple[_TrendUpgradeState, int, bool]:
    turns, regular, keys, offsets = _inputs(source, candidates, level)
    enabled = cache.prepare(source, source.keys, keys, offsets, regular, owned=True)
    state = _TrendUpgradeState(turns, regular, trend_level=level, event_sink=[], strict_only=True)
    restored = enabled and cache.restore(state)
    resumed_at = state.processed_count
    collecting = enabled and cache.begin(state, cold=not restored)
    state.advance(_snapshot=False, _checkpoint=cache.capture if collecting else None)
    return state, resumed_at, restored


def _assert_cold(source: CertifiedSource, candidates: Sequence[Mapping[str, object]],
                 level: int, state: _TrendUpgradeState) -> None:
    turns, regular, _, _ = _inputs(source, candidates, level)
    cold = _TrendUpgradeState(turns, regular, trend_level=level, event_sink=[], strict_only=True)
    cold.advance(_snapshot=False)
    assert state.snapshot() == cold.snapshot()
    assert list(state.event_sink or ()) == list(cold.event_sink or ())
    assert state.regular_cursor == cold.regular_cursor
    assert state.prefix_known == cold.prefix_known


@pytest.mark.parametrize('level', (1, 2, 3))
def test_regular_tail_revision_and_retraction_resume_same_core_at_consumption_boundary(level: int) -> None:
    with owned_evidence_scope() as owner:
        source = _certify(_rows(level), owner)
        candidates = _candidates(source, level)
        cache = _UpgradeConsumptionCache(16 * 1024 * 1024)
        initial, _, _ = _run(cache, source, candidates, level)
        assert initial.event_sink
        retained = next(frame for frame in cache.frames if frame.processed == 64)
        saved_events = deepcopy(list(cast(Sequence[dict[str, object]], retained.runtime['event_sink'])))

        revised = [*candidates[:-1], dict(candidates[-1], audit={'version': 1})]
        actual, resumed_at, restored = _run(cache, source, revised, level)
        assert restored and resumed_at == 64
        _assert_cold(source, revised, level, actual)
        assert list(cast(Sequence[dict[str, object]], retained.runtime['event_sink'])) == saved_events

        shortened = revised[:-1]
        actual, resumed_at, restored = _run(cache, source, shortened, level)
        assert restored and resumed_at == 64
        _assert_cold(source, shortened, level, actual)
        assert cache.stats['restores'] == 2
        assert cache.stats['replayed_attacks'] == 64
        assert cache.memory_used <= cache.limit


def test_late_coordinate_with_early_consumption_rejects_apparently_safe_price_boundary() -> None:
    with owned_evidence_scope() as owner:
        rows = _rows(2)
        rows[5]['available_at'] = 80
        source = _certify(rows, owner)
        candidates = _candidates(source, 2)
        cache = _UpgradeConsumptionCache(16 * 1024 * 1024)
        _run(cache, source, candidates, 2)
        revised = [*candidates[:-1], dict(candidates[-1], audit={'version': 1})]
        actual, resumed_at, restored = _run(cache, source, revised, 2)
        # index70 已经被 index5 的知识80消费，不能恢复到价格64的状态。
        assert not restored and resumed_at == 0
        assert cache.stats['regular_prefix_rejections'] >= 3
        _assert_cold(source, revised, 2, actual)


def test_new_regular_after_equal_consumed_prefix_still_checks_its_knowledge_time() -> None:
    with owned_evidence_scope() as owner:
        rows = _rows(2)
        rows[5]['available_at'] = 80
        source = _certify(rows, owner)
        initial = _candidates(source, 2, (0, 1, 2))
        cache = _UpgradeConsumptionCache(16 * 1024 * 1024)
        _run(cache, source, initial, 2)
        expanded = _candidates(source, 2)
        actual, resumed_at, restored = _run(cache, source, expanded, 2)
        assert not restored and resumed_at == 0
        assert cache.stats['backdated_regular_rejections'] >= 3
        _assert_cold(source, expanded, 2, actual)


def test_source_append_reuses_runtime_but_first_source_revision_rewinds_before_dependency() -> None:
    with owned_evidence_scope() as owner:
        source = _certify(_rows(2), owner)
        candidates = _candidates(source, 2)
        cache = _UpgradeConsumptionCache(16 * 1024 * 1024)
        _run(cache, source, candidates, 2)
        appended = _certify(_rows(2, 98), owner, source)
        actual, resumed_at, restored = _run(cache, appended, candidates, 2)
        assert restored and resumed_at == 96
        _assert_cold(appended, candidates, 2, actual)
        revised_rows = _rows(2, 98)
        revised_rows[0]['audit'] = 'changed source provenance'
        revised = _certify(revised_rows, owner, appended)
        actual, resumed_at, restored = _run(cache, revised, candidates, 2)
        assert not restored and resumed_at == 0
        assert cache.stats['source_revisions'] == 1
        assert cache.stats['source_revision_rejections'] >= 3
        _assert_cold(revised, candidates, 2, actual)


@pytest.mark.parametrize('level', (1, 2, 3))
def test_source_tail_revision_and_shortening_keep_unaffected_consumption_frames(level: int) -> None:
    with owned_evidence_scope() as owner:
        source = _certify(_rows(level), owner)
        candidates = _candidates(source, level)
        cache = _UpgradeConsumptionCache(16 * 1024 * 1024)
        _run(cache, source, candidates, level)
        saved = next(frame for frame in cache.frames if frame.processed == 64)
        assert saved.required_source_count == 64
        revised_rows = _rows(level)
        revised_rows[90]['audit'] = 'tail provenance revised'
        revised = _certify(revised_rows, owner, source)
        actual, resumed_at, restored = _run(cache, revised, candidates, level)
        assert restored and resumed_at == 64
        assert saved in cache.frames
        _assert_cold(revised, candidates, level, actual)

        shortened = _certify(revised_rows[:80], owner, revised)
        actual, resumed_at, restored = _run(cache, shortened, candidates, level)
        assert restored and resumed_at == 64
        _assert_cold(shortened, candidates, level, actual)


def test_consumed_regular_lookahead_is_a_source_dependency_even_before_its_attack() -> None:
    with owned_evidence_scope() as owner:
        rows = _rows(2)
        rows[5]['available_at'] = 80
        source = _certify(rows, owner)
        candidates = _candidates(source, 2)
        cache = _UpgradeConsumptionCache(16 * 1024 * 1024)
        _run(cache, source, candidates, 2)
        frame = next(frame for frame in cache.frames if frame.processed == 32)
        assert frame.attack_position == 31 and frame.required_source_count == 71
        changed = deepcopy(rows)
        changed[70]['value'] = cast(int, changed[70]['value']) + 1
        revised = _certify(changed, owner, source)
        # candidate70 已在 attack5 消费；修改实际来源必须重验它的 provenance。
        with pytest.raises(ValueError, match='actual confirmed source provenance'):
            _run(cache, revised, candidates, 2)
        assert frame not in cache.frames


def test_over_budget_cache_falls_back_and_does_not_repeat_rejected_capture_each_day() -> None:
    with owned_evidence_scope() as owner:
        source = _certify(_rows(2), owner)
        candidates = _candidates(source, 2)
        cache = _UpgradeConsumptionCache(1)
        first, _, _ = _run(cache, source, candidates, 2)
        attempts = cache.stats['capture_attempts']
        second, resumed_at, restored = _run(cache, source, candidates, 2)
        assert not restored and resumed_at == 0
        assert not cache.frames and not cache._seen and cache.memory_used == 0
        assert cache.stats['capture_attempts'] == attempts
        assert cache.stats['budget_blocked_calls'] == 1
        _assert_cold(source, candidates, 2, first)
        _assert_cold(source, candidates, 2, second)


def test_mutable_external_source_is_never_admitted() -> None:
    with owned_evidence_scope() as owner:
        source = _certify(_rows(2), owner)
        candidates = _candidates(source, 2)
        _, regular, keys, offsets = _inputs(source, candidates, 2)
        cache = _UpgradeConsumptionCache(16 * 1024 * 1024)
        assert not cache.prepare(list(source), source.keys, keys, offsets, regular, owned=True)
        assert not cache.frames


def test_generic_container_metadata_is_counted_without_admitting_generic_business_values() -> None:
    with owned_evidence_scope() as owner:
        source = _certify(_rows(2, 1), owner)
        cache = _UpgradeConsumptionCache(1024 * 1024)
        _run(cache, source, (), 2)
        assert len(cache.frames) == 1 and cache.stats['captures'] == 1
        assert cache._new_graph([list[int]], cache.limit) == 'types.GenericAlias'
        assert not cache.stats.get('unsupported_graph_rejections', 0)
        assert not cache.stats.get('budget_rejections', 0)


def test_zero_attack_cursor_has_no_snapshot_or_restoration_overhead() -> None:
    with owned_evidence_scope() as owner:
        source = _certify([], owner)
        cache = _UpgradeConsumptionCache(1024 * 1024)
        _run(cache, source, (), 2)
        assert not cache.frames and not cache._seen
        assert not cache.stats.get('capture_attempts', 0)


@pytest.mark.parametrize('level', (2, 3))
def test_formal_cache_restores_events_and_rebuilds_ledger_consistently(level: int) -> None:
    with owned_evidence_scope() as owner:
        rows = _rows(level)
        rows[-3]['value'], rows[-2]['value'], rows[-1]['value'] = 30, 20, 50
        endpoint = rows[-1]
        rule = f'level{level - 2}_confirmed_high_breaks_known_level{level - 1}_last_fall_high'
        refs = [{name: point[name] for name in ('index', 'ordinal', 'kind', 'value', 'time', 'available_at')}
                for point in rows[-3:]]
        endpoint.update(source_level=level - 2, wave_completion_policy='whole_wave_v117',
            wave_identity_state='owned', wave_direction_before='up', wave_direction_after='up',
            confirmation_rule=rule, upgrade_evidence=dict(role='endpoint', direction='up', confirmation_rule=rule,
                available_at=endpoint['available_at'], broken_key=refs[0], origin=refs[1], confirmed_by=refs[2]))
        source = _certify(rows, owner)
        candidates = _candidates(source, level)
        state = _FormalKeyBreakState(level, memory_bytes=32 * 1024 * 1024)
        base = _OwnedTrendJudgement(level, level - 1, (), tuple(candidates))
        state.calculate(source, base, _certified_keys=source.keys)
        revised = [*candidates[:-1], dict(candidates[-1], audit={'version': 1})]
        updated = _OwnedTrendJudgement(level, level - 1, (), tuple(revised))
        actual = state.calculate(source, updated, _certified_keys=source.keys)
        cold = _FormalKeyBreakState(level, _consumption_checkpoints=False).calculate(
            source, updated, _certified_keys=source.keys)
        assert actual is not None and cold is not None
        assert state._consumption.stats['restores'] == 1
        assert state._upgrade is not None and state._upgrade.event_sink is state._events
        assert mutable_points(actual.points) == mutable_points(cold.points)
        assert mutable_points(actual.candidates) == mutable_points(cold.candidates)
