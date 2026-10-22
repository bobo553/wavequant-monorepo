"""私有九标量版本保留资格时钟与同源编号，归档只读取真实变化块。"""

from copy import deepcopy
import pickle
from weakref import ref
from zlib import decompress

import pytest

from wavequant.domain.market_structure import trend_lines
from wavequant.domain.market_structure import strategy_geometry_version as versions
from wavequant.domain.market_structure.strategy_geometry_version import (
    PermissionGeometryVersions, StrategyGeometry, StrategyGeometryStroke, scalar_point,
)
from wavequant.domain.market_structure.trend_point_version import PointVersion
from wavequant.domain.strategies import combined_a_replay as replay


def _point(index: int, kind: str | None = None) -> dict[str, object]:
    kind = kind or ('H' if index % 2 else 'L')
    return dict(index=index, ordinal=0, time=str(index), available_at=index, kind=kind,
                value=20. if kind == 'H' else 10., label='source', state='reversal', flip='flip')


def _level(points: PointVersion) -> dict[str, object]:
    names = (('id', 'path'), ('source_path', 'source'))
    return dict(strokes=[dict(names, points=list(points))],
                _combined_a_geometry=StrategyGeometry((StrategyGeometryStroke(names, points),)))


def _restore(archive: replay.CombinedAReplay, index: int):
    return archive._restore_geometry(pickle.loads(decompress(archive._read(index))))


@pytest.mark.parametrize('level', (1, 2, 3))
def test_scalar_permission_labels_match_complete_annotation_after_revision_and_truncation(level):
    cache = PermissionGeometryVersions(1024 * 1024)
    source = PointVersion([_point(index) for index in range(7)]).seal()
    for tail, start in ((None, 0), ([_point(3, 'L'), _point(4), _point(5)], 3), ([], 2),
                        ([_point(2, 'H'), _point(3)], 2)):
        if tail is not None:
            source = PointVersion(tail, previous=source, start=start).seal()
        projected = cache.project(level, 'path', source)
        assert projected is not None
        complete = deepcopy(list(source))
        trend_lines._annotate_permissions(complete, 'TEST', {index: index for index in range(20)}, trend_level=level)
        assert list(projected) == [scalar_point(point) for point in complete]
        assert all(point['available_at'] == source[index]['available_at'] for index, point in enumerate(projected))


def test_direct_parent_survives_expired_raw_version_and_only_reads_new_suffix(monkeypatch):
    cache = PermissionGeometryVersions(1024 * 1024)
    source = PointVersion([_point(index) for index in range(96)]).seal()
    previous = cache.project(1, 'path', source)
    assert previous is not None
    next_source = PointVersion([_point(96)], previous=source, start=96).seal()
    weak = ref(source)
    del source
    assert weak() is None
    calls = []
    original = versions.scalar_point

    def counted(point):
        calls.append(point['index'])
        return original(point)

    monkeypatch.setattr(versions, 'scalar_point', counted)
    current = cache.project(1, 'path', next_source)
    assert current is not None and current.changed_since(previous) == 96
    assert calls == [96]
    unchanged = PointVersion(previous=next_source, start=len(next_source)).seal()
    assert cache.project(1, 'path', unchanged) is current
    last = PointVersion([_point(97)], previous=unchanged, start=len(unchanged)).seal()
    del unchanged, next_source
    calls.clear()
    assert cache.project(1, 'path', last) is not None
    assert calls == [97]


def test_duplicate_coordinates_cannot_borrow_sequential_labels():
    cache = PermissionGeometryVersions(1024 * 1024)
    source = PointVersion([_point(0), _point(0, 'H')]).seal()
    assert cache.project(1, 'path', source) is None


def test_optional_geometry_does_not_reserve_space_from_original_annotation_cache():
    cache = trend_lines._TrendPermissionCache(())
    assert cache._annotations._limit == 8 * 1024 * 1024
    version = PointVersion([_point(0)]).seal()
    assert cache._strategy_geometry.project(1, 'path', version) is not None
    cache._strategy_geometry.discard_optional()
    assert cache._strategy_geometry.memory_used == 0
    assert cache._annotations._limit == 8 * 1024 * 1024
    assert cache._strategy_geometry.project(1, 'path', version) is None
    tiny = PermissionGeometryVersions(4096)
    assert tiny.project(1, 'path', version) is None
    assert tiny.memory_used == 0


def test_public_empty_permission_export_never_leaks_private_descriptor():
    prepared = trend_lines.prepare_trend_lines([], trend_level=1)
    prepared['_combined_a_geometry'] = StrategyGeometry(())
    result = trend_lines.attach_trend_permissions(prepared)
    assert '_combined_a_geometry' not in result


def test_archive_direct_parent_writes_only_changed_block_after_previous_version_expires(monkeypatch):
    empty = dict(strokes=[])
    archive = replay.CombinedAReplay(spill=True)
    try:
        points = PointVersion([_point(index) for index in range(96)]).seal()
        archive.remember(0, (empty, _level(points), empty, empty))
        initial = _restore(archive, 0)
        next_points = PointVersion([_point(96)], previous=points, start=96).seal()
        weak = ref(points)
        del points
        assert weak() is None
        calls = []
        original = replay.scalar_point

        def counted(point):
            calls.append(point['index'])
            return original(point)

        monkeypatch.setattr(replay, 'scalar_point', counted)
        archive.remember(1, (empty, _level(next_points), empty, empty))
        assert calls == [96]
        assert _restore(archive, 0) == initial
        assert _restore(archive, 1)[1]['strokes'][0]['points'] == list(next_points)
        writes = archive._tree_writes
        archive.remember(2, (empty, _level(next_points), empty, empty))
        assert archive._tree_writes == writes
    finally:
        archive.close()


def test_archive_middle_revision_truncation_and_regrowth_never_expose_future_tail():
    empty = dict(strokes=[])
    archive = replay.CombinedAReplay(spill=True)
    try:
        points = PointVersion([_point(index) for index in range(70)]).seal()
        archive.remember(0, (empty, _level(points), empty, empty))
        original = _restore(archive, 0)
        revised = PointVersion([dict(_point(index), available_at=index + 1) for index in range(40, 68)],
                               previous=points, start=40).seal()
        archive.remember(1, (empty, _level(revised), empty, empty))
        truncated = PointVersion(previous=revised, start=32).seal()
        archive.remember(2, (empty, _level(truncated), empty, empty))
        grown = PointVersion([dict(_point(32), value=99.)], previous=truncated, start=32).seal()
        archive.remember(3, (empty, _level(grown), empty, empty))
        for index, expected in ((1, revised), (2, truncated), (3, grown)):
            assert _restore(archive, index)[1]['strokes'][0]['points'] == list(expected)
        assert _restore(archive, 0) == original
        archive.remember(3, (empty,) * 4)
        assert _restore(archive, 3) == (empty,) * 4
    finally:
        archive.close()


def test_live_drawing_last_tail_is_preserved_when_epoch_closes_and_fixed_day_is_revisited():
    empty = dict(strokes=[])
    archive = replay.CombinedAReplay(spill=True)
    try:
        raw = [_point(index) for index in range(34)]
        drawing = dict(strokes=[dict(id='lecture-0', points=raw)])
        archive.remember(0, (drawing, empty, empty, empty), _drawing_epoch=0)
        raw = [*raw[:-1], dict(raw[-1], value=99.)]
        drawing = dict(strokes=[dict(id='lecture-0', points=raw)])
        archive.remember(1, (drawing, empty, empty, empty), _drawing_epoch=0)
        closed = dict(id='lecture-0', points=[dict(point, source_path_end_index=2) for point in raw])
        drawing = dict(strokes=[closed])
        archive.remember(2, (drawing, empty, empty, empty), _drawing_epoch=2)
        assert _restore(archive, 2)[0]['strokes'][0]['points'][-1]['value'] == 99.
        writes = archive._tree_writes
        archive.remember(1, (empty,) * 4, _fixed_day=True, _drawing_epoch=0)
        assert archive._tree_writes == writes
        assert _restore(archive, 1)[0]['strokes'][0]['points'][-1]['value'] == 99.
        archive.remember(3, (drawing, empty, empty, empty), _drawing_epoch=3)
        assert archive._tree_writes == writes
    finally:
        archive.close()


def test_tree_write_failure_discards_partial_rows_and_closes_stream(monkeypatch):
    archive = replay.CombinedAReplay(spill=True)
    points = PointVersion([_point(index) for index in range(70)]).seal()
    empty = dict(strokes=[])
    original = archive._append_payload
    count = 0

    def failed(payload):
        nonlocal count
        count += 1
        if count == 3:
            raise OSError('disk failure after a complete leaf')
        return original(payload)

    monkeypatch.setattr(archive, '_append_payload', failed)
    archive.remember(0, (empty, _level(points), empty, empty))
    assert not archive._accepting and not archive._rows and not archive._point_roots
    assert archive._stream is not None and archive._stream.closed


def test_persistent_checkpoint_strips_only_optional_sidecar_and_preserves_live_aliases():
    from wavequant.domain.strategies.chart_entry_history import _checkpoint_permission_levels

    points = PointVersion([_point(0)]).seal()
    first, second = _level(points), _level(points)
    live = ((second, first), (first, second))
    stored = _checkpoint_permission_levels(live)
    assert stored[0][0] is stored[1][1] and stored[0][1] is stored[1][0]
    assert stored[0][0]['strokes'] is second['strokes']
    assert all('_combined_a_geometry' not in level for pair in stored for level in pair)
    assert all('_combined_a_geometry' in level for pair in live for level in pair)


def test_deepcopied_descriptor_is_not_an_immutable_version_certificate():
    empty = dict(strokes=[])
    original = _level(PointVersion([_point(0)]).seal())
    copied = deepcopy(original)
    copied['strokes'][0]['points'][0]['value'] = 99.
    archive = replay.CombinedAReplay(spill=True)
    try:
        archive.remember(0, (empty, copied, empty, empty))
        assert archive._accepting
        assert _restore(archive, 0)[1]['strokes'][0]['points'][0]['value'] == 99.
    finally:
        archive.close()


def test_corrupted_compressed_node_refuses_optional_recompute_without_business_call(monkeypatch):
    empty = dict(strokes=[])
    archive = replay.CombinedAReplay(spill=True)
    points = PointVersion([_point(0)]).seal()
    archive.remember(0, (empty, _level(points), empty, empty))
    assert archive._stream is not None
    archive._stream.seek(0)
    archive._stream.write(b'XX')

    def forbidden(*args, **kwargs):
        raise AssertionError('a damaged optional geometry archive reached trading rules')

    monkeypatch.setattr(replay, 'combined_a_contexts_from_geometry', forbidden)
    assert archive.recompute((), {}, (True,), 1, (), None) is None
    assert not archive._accepting and archive._stream.closed
