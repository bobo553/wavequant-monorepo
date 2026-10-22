"""候选由当前生产账本移交，历史存储容量不决定旧候选是否重新导出。"""

from collections.abc import Mapping, Sequence
from copy import deepcopy
import gc
from sys import getsizeof
from typing import cast
from weakref import ref

import pytest

from wavequant.domain.market_structure import prepared_point_history
from wavequant.domain.market_structure.owned_evidence import owned_evidence_scope
from wavequant.domain.market_structure.trend_judgement import (
    _CandidateBucket, _FormalKeyBreakState, _JudgementDelta, _JudgementLedger,
    _OwnedTrendJudgement, _TrendJudgementState, judge_trend, mutable_points,
)
from wavequant.domain.market_structure.trend_lines import _ClosedPreparedPaths, _TrendJudgementCache, prepare_trend_lines
from wavequant.domain.market_structure.trend_point_version import PointVersion


def _candidate(index: int, *, revision: int = 0) -> dict[str, object]:
    return dict(index=index, ordinal=0, time=str(index), available_at=index,
                kind='H' if index % 2 else 'L', value=20. if index % 2 else 10.,
                audit={'revision': revision})


def _put(ledger: _JudgementLedger, point: dict[str, object]) -> None:
    index = cast(int, point['index'])
    order = index, 0
    if order not in ledger.buckets:
        ledger.orders.append(order)
    ledger.buckets[order] = _CandidateBucket(
        ordinary=[point], merged={(index, 0, cast(str, point['kind']), cast(float, point['value'])): point})


def test_candidate_append_and_checkpoint_fork_only_publish_the_changed_suffix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = _JudgementLedger(_owned_result=True)
    for index in range(64):
        _put(ledger, _candidate(index))
    first, _ = ledger._finish(set(ledger.orders), range(65), 1, [])
    original = first.candidates
    assert type(original) is PointVersion and original._sealed
    before = mutable_points(original)
    checkpoint = _TrendJudgementState._copy_ledger(ledger)
    assert checkpoint is not None and checkpoint.candidates is original
    appended: list[dict[str, object]] = []
    append = PointVersion.append

    def counted(version: PointVersion, point: dict[str, object]) -> None:
        appended.append(point)
        append(version, point)

    monkeypatch.setattr(PointVersion, 'append', counted)
    _put(ledger, _candidate(64))
    following, _ = ledger._finish({(64, 0)}, range(65), 1, [])
    current = following.candidates
    assert type(current) is PointVersion and current._sealed
    assert len(appended) == 1 and appended[0]['index'] == 64
    assert current.parent is original.token and current.changed_from == 64
    assert current[0] is original[0] and mutable_points(original) == before

    unchanged, _ = ledger._finish(set(), range(65), 1, [])
    assert unchanged.candidates is current and len(appended) == 1
    _put(checkpoint, _candidate(63, revision=1))
    revised, _ = checkpoint._finish({(63, 0)}, range(65), 1, [])
    sibling = revised.candidates
    assert type(sibling) is PointVersion and sibling._sealed
    assert sibling.parent is original.token and sibling.changed_from == 63
    assert sibling.changed_since(current) <= 63
    assert len(appended) == 2 and appended[1]['index'] == 63
    assert sibling[0] is original[0] and sibling[63]['audit'] == {'revision': 1}
    assert mutable_points(original) == before and mutable_points(current[:64]) == before
    with pytest.raises(TypeError, match='immutable'):
        current.append(_candidate(65))


def test_empty_and_removed_candidate_versions_do_not_rewrite_previous_snapshots() -> None:
    ledger = _JudgementLedger(_owned_result=True)
    empty, _ = ledger._finish(set(), (), 1, [])
    assert type(empty.candidates) is PointVersion and not empty.candidates
    _put(ledger, _candidate(0))
    populated, _ = ledger._finish({(0, 0)}, (0,), 1, [])
    before = mutable_points(populated.candidates)
    ledger.buckets[(0, 0)] = _CandidateBucket()
    removed, _ = ledger._finish({(0, 0)}, (0,), 1, [])
    assert type(removed.candidates) is PointVersion and not removed.candidates
    assert type(populated.candidates) is PointVersion
    assert removed.candidates.parent is populated.candidates.token
    assert removed.candidates.changed_from == 0 and not empty.candidates
    assert mutable_points(populated.candidates) == before


def _source(level: int) -> list[dict[str, object]]:
    prices = (20., 30., 10., 20., 14., 26., 18., 35., 28., 45., 38., 42., 32., 36., 24., 29., 26.)
    proof = {'observations': [1, 2, 3]}
    return [dict(index=index, ordinal=0, kind='H' if index % 2 else 'L', value=price,
        time=str(index), available_at=index + 1, trend_level=level - 1, state='confirmed', audit=proof)
        for index, price in enumerate(prices)]


@pytest.mark.parametrize('level', (1, 2, 3))
def test_preparation_borrows_current_candidates_without_a_history_or_export_owner(
    level: int, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source(level)
    source_before = deepcopy(source)
    expected = judge_trend(source, trend_level=level)
    assert type(expected.points) is tuple and type(expected.candidates) is tuple
    cache = _TrendJudgementCache(tail_reuse=True)
    channels: list[tuple[int, str, str] | None] = []
    export = cache.export_owned

    def counted(points: Sequence[Mapping[str, object]], *, channel: tuple[int, str, str] | None = None,
                delta: _JudgementDelta | None = None, _owned_result: bool = False) -> PointVersion:
        channels.append(channel)
        return export(points, channel=channel, delta=delta, _owned_result=_owned_result)

    monkeypatch.setattr(cache, 'export_owned', counted)
    with owned_evidence_scope(memory_bytes=0):
        prepared = prepare_trend_lines(source, trend_level=level, _judgement_cache=cache,
                                       _owned_source=True, _versioned_source=True)
        strokes = cast(list[dict[str, object]], prepared['candidate_strokes'])
        candidates = strokes[0]['points']
        assert type(candidates) is PointVersion and candidates._sealed
        result = cache._version_results[(level, 'path-0')][1]
        assert type(result) is _OwnedTrendJudgement and candidates is result.candidates
        assert mutable_points(candidates) == mutable_points(expected.candidates)
        previous = ref(candidates)
        del prepared, strokes, candidates, result
        gc.collect()
        assert previous() is not None
        following = prepare_trend_lines(source, trend_level=level, _judgement_cache=cache,
                                        _owned_source=True, _versioned_source=True)
        next_candidates = cast(list[dict[str, object]], following['candidate_strokes'])[0]['points']
        assert next_candidates is previous()
        assert all(channel is not None and channel[2] == 'points' for channel in channels)
        assert (level, 'path-0', 'candidates') not in cache._exports
        exported = mutable_points(cast(PointVersion, next_candidates))
        cast(list[object], cast(dict[str, object], exported[0]['audit'])['observations']).append(99)
        assert mutable_points(cast(PointVersion, next_candidates)) == mutable_points(expected.candidates)
        assert source == source_before
        del following, next_candidates
        cache.clear()
        gc.collect()
        assert previous() is None


def test_candidate_version_payload_is_included_in_both_existing_budget_visitors() -> None:
    proof = 'proof-' * 10000
    version = PointVersion([{'audit': {'history': [proof]}}]).seal()
    ordinary = _TrendJudgementState(1)
    assert ordinary._owned_value_bytes([version]) > getsizeof(proof)
    assert _FormalKeyBreakState._value_bytes([version]) > getsizeof(proof)
    ledger = _JudgementLedger(_owned_result=True)
    ledger.candidates = version
    ordinary._ledger = ledger
    before = ordinary._container_bytes()
    ledger.candidates = PointVersion().seal()
    assert before > ordinary._container_bytes()


def test_direct_candidates_bootstrap_history_and_only_sign_changed_suffix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = PointVersion(_candidate(index) for index in range(64)).seal()
    appended = PointVersion([_candidate(64)], previous=original, start=64).seal()
    revised = PointVersion([_candidate(63, revision=1), _candidate(64)], previous=original, start=63).seal()
    current = [original]
    cache = _TrendJudgementCache()

    def judge(source: Sequence[Mapping[str, object]], **_: object) -> _OwnedTrendJudgement:
        return _OwnedTrendJudgement(1, 0, (), current[0])

    monkeypatch.setattr(cache, 'judge', judge)
    signed: list[object] = []
    signature = prepared_point_history.owned_signature

    def counted(value: object) -> bytes | None:
        signed.append(value)
        return signature(value)

    monkeypatch.setattr(prepared_point_history, 'owned_signature', counted)
    with owned_evidence_scope(), _ClosedPreparedPaths(_owned_history=True) as paths:
        for version, expected_tail in ((original, list(original)), (appended, list(appended[64:])),
                                       (revised, list(revised[63:])), (revised, [])):
            current[0] = version
            prepared = prepare_trend_lines(_source(1), trend_level=1, _judgement_cache=cache,
                                           _closed_paths=paths, _owned_source=True)
            candidate = cast(list[dict[str, object]], prepared['candidate_strokes'])[0]
            assert candidate['points'] is version
            signed.clear()
            paths.encode(prepared)
            assert [id(point) for point in signed] == [id(point) for point in expected_tail]
            assert paths._point_history is not None
            channel = 'candidate_strokes', str(candidate['id'])
            assert paths._point_history._owned_points[channel] is version
            assert not paths._point_hints
