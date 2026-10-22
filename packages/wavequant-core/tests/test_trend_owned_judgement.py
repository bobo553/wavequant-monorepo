"""内部证明只借用已认证 builtin 图；公开结果、审计回退与预算边界不变。"""

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import cast

import pytest

from wavequant.domain.market_structure.owned_evidence import CertifiedSource, owned_evidence_scope, owned_signature
from wavequant.domain.market_structure.trend_judgement import (
    TrendJudgement, _FormalKeyBreakState, _FrozenPoint, _OwnedTrendJudgement,
    _SourceVersionOwner, _TrendJudgementState, _freeze_point_uncached,
    _judgement_working_points, judge_trend, mutable_points,
)
from wavequant.domain.market_structure.trend_lines import _TrendJudgementCache


def _source(level: int = 1) -> list[dict[str, object]]:
    prices = (20., 30., 10., 20., 14., 26., 18., 35., 28., 45., 38., 42., 32., 36., 24., 29., 26.)
    proof = {'observations': [1, 2, 3]}
    return [dict(index=index, ordinal=0, kind='H' if index % 2 else 'L', value=price,
        time=str(index), available_at=index + 1, trend_level=level - 1, state='confirmed', audit=proof)
        for index, price in enumerate(prices)]


def _owned(source: Sequence[Mapping[str, object]], level: int = 1,
           state: _TrendJudgementState | None = None) -> TrendJudgement:
    return judge_trend(source, trend_level=level, _append_state=state,
                       _owned_source=True, _certified_standard=True, _owned_result=True)


@pytest.mark.parametrize('level', (1, 2, 3))
def test_owned_results_keep_proof_identity_and_public_export_independence(level: int) -> None:
    source = _source(level)
    before = deepcopy(source)
    actual = _owned(source, level)
    expected = judge_trend(source, trend_level=level)
    assert type(actual) is _OwnedTrendJudgement
    assert actual.candidates
    assert mutable_points(actual.points) == mutable_points(expected.points)
    assert mutable_points(actual.candidates) == mutable_points(expected.candidates)
    assert source == before
    assert all(type(point) is dict for point in (*actual.points, *actual.candidates))
    assert all(point['audit'] is source[0]['audit'] for point in actual.candidates)

    working = _judgement_working_points(actual.candidates, owned=True)
    assert working[0] is not actual.candidates[0]
    assert working[0]['audit'] is actual.candidates[0]['audit']
    working[0]['label'] = 'private working shell'
    assert actual.candidates[0].get('label') != 'private working shell'
    exported = mutable_points(actual.candidates)
    cast(list[object], cast(dict[str, object], exported[0]['audit'])['observations']).append(99)
    assert mutable_points(actual.candidates) == mutable_points(expected.candidates)
    assert source == before
    assert all(type(point) is _FrozenPoint for point in (*expected.points, *expected.candidates))
    with pytest.raises(TypeError):
        cast(dict[str, object], expected.candidates[0]['audit'])['observations'] = []


@pytest.mark.parametrize('audit', ((1, 2), {1: 'integer key'}, {'large': 10 ** 5000}))
def test_finite_nonstandard_input_cannot_request_shared_result(audit: object) -> None:
    source = _source()
    source[0]['audit'] = audit
    actual = _owned(source)
    expected = judge_trend(source)
    assert type(actual) is TrendJudgement
    assert mutable_points(actual.points) == mutable_points(expected.points)
    assert mutable_points(actual.candidates) == mutable_points(expected.candidates)


def test_source_version_uses_actual_strict_admission_and_shares_only_owned_proof() -> None:
    source = _source()
    with owned_evidence_scope() as evidence:
        keys = tuple(owned_signature(point) for point in source)
        assert all(key is not None for key in keys)
        certified = CertifiedSource(source, cast(tuple[bytes, ...], keys), evidence, None)
        owner = _SourceVersionOwner(1, 8 * 1024 * 1024)
        first = owner.prepare(certified)
        assert first is not None and first.owned_result
        assert first.source[0] is not source[0]
        assert first.source[0]['audit'] is source[0]['audit']
        assert owner.prepare(certified) is first

        revised = [*source[:-1], dict(source[-1], audit={1: 'integer key'})]
        keys = tuple(owned_signature(point) for point in revised)
        assert all(key is not None for key in keys)
        next_source = CertifiedSource(revised, cast(tuple[bytes, ...], keys), evidence, certified)
        second = owner.prepare(next_source)
        assert second is not None and not second.owned_result
        assert second.source[-1]['audit'] == {1: 'integer key'}
        assert second.source[-1]['audit'] is not revised[-1]['audit']
        assert first.owned_result


def test_visible_boundary_zero_does_not_certify_an_unvisited_original_prefix() -> None:
    source = _source()
    source[0] = dict(source[0], state='seed', audit={1: 'integer key'})
    with owned_evidence_scope() as evidence:
        keys = tuple(owned_signature(point) for point in source)
        assert all(key is not None for key in keys)
        certified = CertifiedSource(source, cast(tuple[bytes, ...], keys), evidence, None)
        owner = _SourceVersionOwner(1, 8 * 1024 * 1024)
        first = owner.prepare(certified)
        assert first is not None and not first.owned_result and first.positions[0] == 1
        revised = [source[0], dict(source[1], audit={'revision': 2}), *source[2:]]
        keys = tuple(owned_signature(point) for point in revised)
        assert all(key is not None for key in keys)
        next_source = CertifiedSource(revised, cast(tuple[bytes, ...], keys), evidence, certified)
        second = owner.prepare(next_source)
        assert second is not None and second.positions[0] == 1
        assert not second.owned_result


def test_same_ordinary_state_does_not_return_owned_ledger_to_public_call() -> None:
    source = _source()
    state = _TrendJudgementState(1)
    private = _owned(source, state=state)
    retained = mutable_points(private.candidates)
    public = judge_trend(source, _append_state=state)
    assert type(private) is _OwnedTrendJudgement
    assert type(public) is TrendJudgement
    assert public.candidates and all(type(point) is _FrozenPoint for point in public.candidates)
    assert mutable_points(public.candidates) == retained
    assert mutable_points(private.candidates) == retained
    again = _owned(source, state=state)
    assert type(again) is _OwnedTrendJudgement
    assert mutable_points(again.candidates) == retained


def test_formal_candidate_keys_keep_json_value_semantics_and_reset_result_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared = [1, 1., -0., '中文']
    candidate = {'proof': shared, 'other': shared}
    owned = _OwnedTrendJudgement(2, 1, (), (candidate,))
    frozen = TrendJudgement(2, 1, (), (_freeze_point_uncached(candidate),))
    observed: list[tuple[str, ...]] = []

    def capture(self: _FormalKeyBreakState, source: Sequence[Mapping[str, object]],
                result: TrendJudgement, source_keys: tuple[bytes, ...],
                candidate_keys: tuple[str, ...]) -> TrendJudgement:
        observed.append(candidate_keys)
        return result

    monkeypatch.setattr(_FormalKeyBreakState, '_calculate', capture)
    state = _FormalKeyBreakState(2)
    assert state.calculate([], owned) is owned
    state._checkpoint = _FormalKeyBreakState(2)
    assert state.calculate([], frozen) is frozen
    assert state._checkpoint is None and state._owned_result_mode is False
    assert observed[0] == observed[1]


@pytest.mark.parametrize('kind', ('ordinary', 'formal'))
def test_owned_graph_is_fully_charged_and_shared_aliases_are_counted_once(kind: str) -> None:
    proof = {'observations': list(range(1024))}
    first, second = {'audit': proof}, {'audit': proof}
    measure = (_TrendJudgementState(1)._owned_value_bytes if kind == 'ordinary'
               else _FormalKeyBreakState._value_bytes)
    charged = measure([first, second])
    assert charged > 8192
    assert charged < measure([first]) + measure([second])
    assert measure([first, first]) == measure([first])


def test_owned_result_does_not_bypass_state_capacity() -> None:
    source = _source()
    state = _TrendJudgementState(1, memory_bytes=1)
    result = _owned(source, state=state)
    assert type(result) is _OwnedTrendJudgement
    assert state.memory_used == 0 and state._ledger is None
    assert mutable_points(result.candidates) == mutable_points(judge_trend(source).candidates)


def test_judgement_cache_mode_switch_clears_both_version_and_path_result_memos() -> None:
    source = _source()
    cache = _TrendJudgementCache(tail_reuse=True)
    with owned_evidence_scope() as evidence:
        keys = tuple(owned_signature(point) for point in source)
        assert all(key is not None for key in keys)
        certified = CertifiedSource(source, cast(tuple[bytes, ...], keys), evidence, None)
        versioned = cache.judge(certified, path='version', trend_level=1, _owned_source=True)
        path_result = cache.judge(source, path='path', trend_level=1, asof=len(source) + 1, _owned_source=True)
        assert type(versioned) is type(path_result) is _OwnedTrendJudgement
        assert cache._version_results[1, 'version'][1] is versioned
        assert cache._paths[1, 'path'][1] is path_result

        public = cache.judge(source, path='path', trend_level=1, asof=len(source) + 1)
        assert type(public) is TrendJudgement and public is not path_result
        assert not cache._version_results and not cache._source_owners
        assert (1, 'version') not in cache._states
        assert all(type(saved) is TrendJudgement for _, saved in cache._paths.values())
        assert all(type(point) is _FrozenPoint for point in public.candidates)
        assert mutable_points(public.candidates) == mutable_points(path_result.candidates)

        restored = cache.judge(certified, path='version', trend_level=1, _owned_source=True)
        assert type(restored) is _OwnedTrendJudgement and restored is not versioned
        assert not cache._paths
        assert cache._version_results[1, 'version'][1] is restored
        final_public = cache.judge(source, path='version', trend_level=1)
        assert type(final_public) is TrendJudgement and not cache._version_results
        assert mutable_points(final_public.candidates) == mutable_points(restored.candidates)
