"""Export deltas survive a full decode cache without retaining historical graphs."""

from collections.abc import Mapping, Sequence
import gc
from weakref import ref

import pytest

from wavequant.domain.market_structure import trend_lines
from wavequant.domain.market_structure.trend_judgement import _JudgementDelta, _freeze_point_uncached
from wavequant.domain.market_structure.trend_lines import _TrendJudgementCache
from wavequant.domain.market_structure.trend_point_version import PointVersion
from wavequant.domain.market_structure.trend_readonly_cache import _ReadonlySnapshotCache


def _point(index: int, *, proof: str = '') -> Mapping[str, object]:
    return _freeze_point_uncached(dict(index=index, ordinal=0, time=str(index), available_at=index,
                                      kind='H' if index % 2 else 'L', value=index + 10, audit=proof))


def _cache() -> _TrendJudgementCache:
    cache = _TrendJudgementCache()
    cache._point_exports = _ReadonlySnapshotCache(memory_bytes=0, max_entries=1)
    return cache


@pytest.mark.parametrize('level', (1, 2, 3))
def test_large_live_export_reuses_delta_with_exhausted_decode_cache(
    level: int, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = _cache()
    channel = level, 'path', 'points'
    first = _point(0, proof='x' * (4 * 1024 * 1024))
    initial = _JudgementDelta(object(), None, 0, 0)
    before = cache.export_owned((first,), channel=channel, delta=initial)
    calls: list[Mapping[str, object]] = []
    decode = trend_lines.mutable_points

    def measured(points: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
        calls.extend(points)
        return decode(points)

    monkeypatch.setattr(trend_lines, 'mutable_points', measured)
    added = _point(1)
    following = _JudgementDelta(object(), initial.token, 1, 1)
    after = cache.export_owned((first, added), channel=channel, delta=following)
    assert calls == [added]
    assert after[0] is before[0] and after.changed_from == 1
    assert cache._exports[channel][1]() is after
    assert cache._point_exports.memory_used == 0
    hint = cache._export_hints[id(after)]
    assert hint[0] is after and hint[1] is before and hint[2] == 1


def test_export_metadata_does_not_keep_source_or_output_alive() -> None:
    class Points(list[Mapping[str, object]]):
        pass

    cache = _cache()
    channel = 1, 'path', 'points'
    source = Points([_point(0)])
    source_ref = ref(source)
    output = cache.export_owned(source, channel=channel, delta=_JudgementDelta(object(), None, 0, 0))
    output_ref = ref(output)
    del source, output
    gc.collect()
    assert source_ref() is None and output_ref() is None
    assert channel not in cache._exports and channel not in cache._export_deltas
    assert not cache._export_hints


def test_expired_output_rebuilds_instead_of_trusting_retained_delta(monkeypatch: pytest.MonkeyPatch) -> None:
    cache = _cache()
    channel = 1, 'path', 'points'
    first, added = _point(0), _point(1)
    initial = _JudgementDelta(object(), None, 0, 0)
    previous = cache.export_owned((first,), channel=channel, delta=initial)
    previous_ref = ref(previous)
    del previous
    gc.collect()
    assert previous_ref() is None
    calls: list[Mapping[str, object]] = []
    decode = trend_lines.mutable_points

    def measured(points: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
        calls.extend(points)
        return decode(points)

    monkeypatch.setattr(trend_lines, 'mutable_points', measured)
    following = _JudgementDelta(object(), initial.token, 1, 1)
    output = cache.export_owned((first, added), channel=channel, delta=following)
    assert calls == [first, added] and output.changed_from == 0


def test_hints_do_not_keep_previous_version_alive() -> None:
    cache = _cache()
    channel = 1, 'path', 'points'
    first, added = _point(0), _point(1)
    initial = _JudgementDelta(object(), None, 0, 0)
    before = cache.export_owned((first,), channel=channel, delta=initial)
    before_ref = ref(before)
    following = _JudgementDelta(object(), initial.token, 1, 1)
    after = cache.export_owned((first, added), channel=channel, delta=following)
    del before
    gc.collect()
    assert before_ref() is None
    hint = cache._export_hints[id(after)]
    assert hint[0] is after and hint[1] is None and hint[2] == 0


@pytest.mark.parametrize('channel_name,expected', (('points', 1), ('candidates', 0)))
def test_same_parent_fork_keeps_original_delta_boundary(channel_name: str, expected: int) -> None:
    cache = _cache()
    channel = 2, 'path', channel_name
    ancestor = object()
    first, middle, last = _point(0), _point(1), _point(2)
    initial = _JudgementDelta(object(), ancestor, 2, 0)
    before = cache.export_owned((first, middle, last), channel=channel, delta=initial)
    following = _JudgementDelta(object(), ancestor, 1, 2)
    after = cache.export_owned((first, _point(1, proof='revised'), last), channel=channel, delta=following)
    assert after.changed_from == expected
    if expected:
        assert after[0] is before[0]
    else:
        assert after[0] is not before[0]


def test_same_version_returns_existing_export_and_no_delta_rebuilds() -> None:
    cache = _cache()
    channel = 1, 'path', 'points'
    points = (_point(0),)
    delta = _JudgementDelta(object(), None, 0, 0)
    first = cache.export_owned(points, channel=channel, delta=delta)
    assert cache.export_owned(points, channel=channel, delta=delta) is first
    second = cache.export_owned(points, channel=channel)
    assert second is not first and second[0] is not first[0]
    assert list(second) == list(first)


def test_annotation_replacement_cannot_be_removed_by_old_weak_callback() -> None:
    cache = _cache()
    channel = 1, 'path', 'points'
    delta = _JudgementDelta(object(), None, 0, 0)
    original = cache.export_owned((_point(0),), channel=channel, delta=delta)
    original_ref = ref(original)
    annotated = PointVersion([dict(original[0], label='L1')], previous=original, start=0)
    cache._save_export(channel, annotated, delta)
    cache._export_hints.pop(id(original), None)
    cache._export_hints[id(annotated)] = annotated, original, 0
    del original
    gc.collect()
    assert original_ref() is None and cache._exports[channel][1]() is annotated
    cache._forget_path(channel[:2])
    assert channel not in cache._exports and not cache._export_hints


def test_unknown_audit_and_plain_inputs_do_not_gain_export_checkpoints() -> None:
    cache = _cache()
    channel = 1, 'path', 'points'
    unknown = _freeze_point_uncached(dict(index=0, audit=object()))
    output = cache.export_owned((unknown,), channel=channel, delta=_JudgementDelta(object(), None, 0, 0))
    assert len(output) == 1 and channel not in cache._exports and not cache._export_hints
    ordinary = cache.export_owned((dict(index=0),), channel=channel, delta=_JudgementDelta(object(), None, 0, 0))
    assert len(ordinary) == 1 and channel not in cache._exports and not cache._export_hints
