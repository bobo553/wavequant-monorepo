"""只在生产者封存的点版本上增量投影，公共可变帧仍逐日读取。"""

from copy import deepcopy
from datetime import datetime, timedelta

import pytest

from wavequant.domain.market_structure.lecture_drawing import lecture_drawing
from wavequant.domain.market_structure.trend_point_version import PointVersion
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies import lecture_strategy as module


def _point(index: int, price: float, *, kind: str | None = None,
           state: str = 'confirmed') -> dict[str, object]:
    return dict(index=index, ordinal=0, kind=kind or ('H' if index % 2 else 'L'),
                value=price, state=state)


def test_owned_real_frames_match_every_public_pivot_and_knowledge_clock() -> None:
    values = (10, 13, 11, 15, 12, 17, 14, 19, 15, 13, 16, 12, 15, 11, 14, 10, 13, 16)
    bars = [Bar(datetime(2026, 1, 1) + timedelta(days=index), 'PIVOT', value,
        value + .25, value - .25, value, 1000) for index, value in enumerate(values)]
    bars[8] = Bar(bars[8].timestamp, 'PIVOT', 15., 30., 1., 15., 1000)
    bars[9] = Bar(bars[9].timestamp, 'PIVOT', 15., 29., 2., 15., 1000)
    public, incremental = module.LecturePivotState(), module.LecturePivotState()
    versions = module._OwnedLecturePivotVersions()
    saved: list[tuple[PointVersion, list[dict[str, object]]]] = []

    def collect(index: int, epoch: int, raw: list[dict[str, object]]) -> None:
        version = versions.publish(epoch, raw)
        assert incremental.advance(index, epoch, version) == public.advance(index, epoch, raw)
        assert incremental.prior == public.prior
        assert incremental.epochs == public.epochs and incremental.blocked == public.blocked
        for prior, snapshot in saved:
            assert list(prior) == snapshot
        saved.append((version, deepcopy(list(version))))

    lecture_drawing(bars, on_step=collect)
    assert incremental.snapshots == public.snapshots


def test_version_revisions_truncation_equal_runs_and_epoch_keep_original_semantics() -> None:
    public, incremental = module.LecturePivotState(), module.LecturePivotState()
    previous: PointVersion | None = None
    frames = [
        (0, [_point(0, 10., state='seed'), _point(1, 15.), _point(2, 11.), _point(3, 14.)], 0),
        (0, [_point(4, 14.), _point(5, 12.), _point(6, 17.)], 4),
        (0, [_point(3, 16.), _point(4, 16.), _point(5, 12.), _point(6, 17.)], 3),
        (0, [], 4),
        (0, [_point(4, 13., kind='H'), _point(5, 18., kind='H'), _point(6, 12., kind='L')], 4),
        (13, [], 0),
        (13, [_point(5, 10., state='seed'), _point(6, 15.), _point(7, 11.)], 0),
    ]
    earlier: list[tuple[object, ...]] = []
    for index in range(8):
        assert incremental.advance(index, 0, PointVersion().seal()) == public.advance(index, 0, [])
        earlier.append(())
    for index, (epoch, suffix, start) in enumerate(frames, 8):
        version = PointVersion(suffix, previous=previous, start=start).seal()
        expected = public.advance(index, epoch, list(version))
        actual = incremental.advance(index, epoch, version)
        assert actual == expected and incremental.prior == public.prior
        assert list(incremental.snapshots.values())[:-1] == earlier
        earlier.append(actual)
        previous = version
    assert incremental.blocked == public.blocked == {13}


@pytest.mark.parametrize('count', (128, 2048))
def test_sealed_append_revisits_only_changed_triples(monkeypatch: pytest.MonkeyPatch, count: int) -> None:
    state = module.LecturePivotState()
    version = PointVersion().seal()
    for index in range(count):
        version = PointVersion([_point(index, 15. if index % 2 else 10.)],
            previous=version, start=len(version)).seal()
        state.advance(index, 0, version)
    original = module._same_frozen_pivot
    calls = 0

    def counted(previous: module.ReversalPoint | None, index: int, ordinal: int,
                kind: str, price: float, confirmed: int) -> bool:
        nonlocal calls
        calls += 1
        return original(previous, index, ordinal, kind, price, confirmed)

    monkeypatch.setattr(module, '_same_frozen_pivot', counted)
    for day in range(1, 21):
        position = count + day - 1
        version = PointVersion([_point(position, 15. if position % 2 else 10.)],
            previous=version, start=len(version)).seal()
        state.advance(count + day - 1, 0, version)
    assert calls <= 40


def test_mutable_input_and_transition_from_version_still_reread_old_points() -> None:
    points = [_point(index, 15. if index % 2 else 10.) for index in range(8)]
    public, incremental = module.LecturePivotState(), module.LecturePivotState()
    for index in range(8):
        assert incremental.advance(index, 0, PointVersion().seal()) == public.advance(index, 0, [])
    assert incremental.advance(8, 0, PointVersion(deepcopy(points)).seal()) == public.advance(8, 0, points)
    points[1]['value'] = 20.
    assert incremental.advance(9, 0, points) == public.advance(9, 0, points)
    points[1]['value'] = 21.
    assert incremental.advance(10, 0, points) == public.advance(10, 0, points)
    assert incremental.prior == public.prior


def test_repeated_pivot_keys_replay_prior_day_clock_overrides() -> None:
    public, incremental = module.LecturePivotState(), module.LecturePivotState()
    for index in range(8):
        assert incremental.advance(index, 0, PointVersion().seal()) == public.advance(index, 0, [])
    points = [_point(0, 10.), _point(1, 15.), _point(2, 11.)]
    version = PointVersion(points).seal()
    assert incremental.advance(8, 0, version) == public.advance(8, 0, points)
    suffix = [_point(3, 16.), _point(4, 12.), _point(1, 15.), _point(6, 10.)]
    version = PointVersion(suffix, previous=version, start=len(version)).seal()
    points.extend(suffix)
    for index in range(9, 12):
        assert incremental.advance(index, 0, version) == public.advance(index, 0, points)
        assert incremental.prior == public.prior
