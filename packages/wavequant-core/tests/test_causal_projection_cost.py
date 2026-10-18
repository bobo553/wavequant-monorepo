"""历史规模增长时只优化数据搬运，逐日结构及知识时钟保持原适配器语义。"""

from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import datetime, timedelta
from typing import cast

import pytest

from wavequant.domain.market_structure import trend_cascade as cascade
from wavequant.domain.market_structure import trend_lines
from wavequant.domain.market_structure.lecture_drawing import lecture_drawing
from wavequant.domain.market_structure.polyline_lifecycle import PolylineLifecycleSource
from wavequant.domain.market_structure.trend_lines import _OwnedPolylineTurns, polyline_high_low_points, polyline_turns
from wavequant.domain.market_structure.trend_promotion_scope import PROMOTION_FIELD
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.chart_drawing_history import (
    ChartDrawingFrame, ChartDrawingHistory, _OwnedDrawingProjection,
)
from wavequant.domain.strategies.chart_entry_history import _closed_drawing_path


def _slow_enrich(
    source: Sequence[Mapping[str, object]], allowed: Mapping[str, Mapping[bytes, dict[str, object]]],
) -> list[dict[str, object]]:
    """优化前逐点扫描证明的参考实现，保留列表顺序和起点门禁。"""
    actual = {(str(point.get('source_path', 'path-0')), cascade._order(point)) for point in source}
    result = []
    for point in source:
        item = dict(point)
        path = str(point.get('source_path', 'path-0'))
        additions = [record for record in allowed.get(path, {}).values()
            if cascade._order(record) == cascade._order(point)
            and (path, cascade._order(cast(Mapping[str, object],
                 cast(Mapping[str, object], record['upgrade_evidence'])['origin']))) in actual]
        if additions:
            item[PROMOTION_FIELD] = additions
        result.append(item)
    return result


def test_promotion_join_preserves_duplicates_paths_missing_origins_and_evidence_order() -> None:
    source = [dict(index=index, ordinal=ordinal, source_path=path)
        for path in ('a', 'b') for index, ordinal in ((0, 0), (2, 0), (2, 1), (4, 0))]
    source.append(dict(source[1]))
    source[0][PROMOTION_FIELD] = [{'existing': True}]
    records = {
        b'first': dict(index=2, ordinal=0, upgrade_evidence=dict(origin=dict(index=0))),
        b'later': dict(index=2, ordinal=0, upgrade_evidence=dict(origin=dict(index=0)), known=10),
        b'ordinal': dict(index=2, ordinal=1, upgrade_evidence=dict(origin=dict(index=0))),
        b'absent-origin': dict(index=4, upgrade_evidence=dict(origin=dict(index=3))),
        # 未匹配端点的记录不会被原实现读取 origin。
        b'absent-endpoint': dict(index=99),
    }
    allowed = {'a': records, 'b': {b'other': dict(index=4, upgrade_evidence=dict(origin=dict(index=2, ordinal=1)))}}
    before = deepcopy((source, allowed))
    assert cascade._enrich_promotions(source, allowed) == _slow_enrich(source, allowed)
    assert (source, allowed) == before


@pytest.mark.parametrize('count', [64, 256])
def test_promotion_join_work_grows_linearly_in_source_and_proof_count(monkeypatch, count: int) -> None:
    source = [dict(index=index, source_path='path') for index in range(count)]
    allowed = {'path': {str(index).encode(): dict(index=index, upgrade_evidence=dict(origin=dict(index=0)))
        for index in range(1, count)}}
    expected = _slow_enrich(source, allowed)
    original = cascade._order
    visits = 0

    def counted(point: Mapping[str, object]) -> tuple[int, int]:
        nonlocal visits
        visits += 1
        return original(point)

    monkeypatch.setattr(cascade, '_order', counted)
    assert cascade._enrich_promotions(source, allowed) == expected
    assert visits <= 4 * count


def _bars() -> list[Bar]:
    values = (10, 13, 11, 15, 12, 17, 14, 19, 15, 13, 16, 12, 15, 11, 14, 10, 13, 16)
    bars = [Bar(datetime(2026, 1, 1) + timedelta(days=index), 'COST', value,
        value + .25, value - .25, value, 1000) for index, value in enumerate(values)]
    bars[8] = Bar(bars[8].timestamp, 'COST', 15., 30., 1., 15., 1000)
    bars[9] = Bar(bars[9].timestamp, 'COST', 15., 29., 2., 15., 1000)
    return bars


def test_owned_drawing_projection_and_recent_snapshots_equal_every_original_prefix() -> None:
    bars = _bars()
    projection = _OwnedDrawingProjection()
    closed: list[dict[str, object]] = []
    prior_epoch: int | None = None
    prior_raw: list[dict[str, object]] = []
    baselines: list[ChartDrawingFrame] = []
    dates = {bar.timestamp.date().isoformat(): index for index, bar in enumerate(bars)}
    original_lifecycle = PolylineLifecycleSource(dates)
    incremental_lifecycle = PolylineLifecycleSource(dates, _owned_tail=True)
    with ChartDrawingHistory(start=0, count=len(bars), closed=()) as history:
        def collect(index: int, epoch: int, raw: list[dict[str, object]]) -> None:
            nonlocal prior_epoch, prior_raw
            closed_path = None
            if prior_epoch is not None and epoch != prior_epoch and len(prior_raw) > 1:
                closed_path = _closed_drawing_path(prior_epoch, prior_raw, epoch)
                closed.append(closed_path)
            history.append(index, epoch, raw, closed_path)
            drawing = dict(strokes=[*closed, *([dict(id=f'lecture-{epoch}', points=raw)] if len(raw) > 1 else [])])
            frame = ChartDrawingFrame(index, epoch, raw, drawing)
            baselines.append(deepcopy(frame))
            assert projection.project(frame) == polyline_high_low_points(drawing)
            assert incremental_lifecycle.advance(frame) == original_lifecycle.advance(frame)
            history._remember_owned_frame(frame)
            history.read_limit = index + 1
            start = max(0, index - 1)
            actual = list(history.iter_rows(start_index=start))
            assert actual == baselines[start:index + 1]
            if actual[-1].raw:
                actual[-1].raw[0]['value'] = -999
                assert list(history.iter_rows(start_index=start)) == baselines[start:index + 1]
            prior_epoch, prior_raw = epoch, raw

        lecture_drawing(bars, on_step=collect)
        history.read_limit = None
        assert list(history.iter_rows()) == baselines


def test_tail_projection_keeps_equal_prices_same_day_ranks_and_revised_anchor() -> None:
    projector = _OwnedPolylineTurns()
    raw: list[dict[str, object]] = []
    for index, value in enumerate((10., 15., 15., 12., 14., 11., 16., 13.)):
        if raw:
            raw[-1] = dict(raw[-1], state='confirmed', available_at=index)
        raw.append(dict(index=index, ordinal=index % 2, time=str(index // 2), value=value,
            kind='H' if index % 2 else 'L', state='developing', available_at=index))
        assert projector.project(raw, source_path='same') == polyline_turns(raw, source_path='same')
        raw[-1] = dict(raw[-1], value=value + .5)
        assert projector.project(raw, source_path='same') == polyline_turns(raw, source_path='same')
    raw[-2] = dict(raw[-2], available_at=9)
    assert projector.project(raw, source_path='same') == polyline_turns(raw, source_path='same')
    assert projector.project(raw[:4], source_path='same') == polyline_turns(raw[:4], source_path='same')
    assert projector.project(raw, source_path='new') == polyline_turns(raw, source_path='new')
    assert projector.project([], source_path='empty') == []


def test_equal_tail_runs_keep_the_earliest_vertex_and_recalculate_its_right_neighbor() -> None:
    projector = _OwnedPolylineTurns()
    raw = []
    for index, value in enumerate((10., 15., 15., 15., 12., 12., 17., 11.)):
        if raw:
            raw[-1] = dict(raw[-1], state='confirmed')
        raw.append(dict(index=index, ordinal=index % 3, time=str(index // 3), value=value,
            kind='H' if index % 2 else 'L', state='developing', available_at=index))
        assert projector.project(raw, source_path='equal') == polyline_turns(raw, source_path='equal')


def test_tail_projection_does_not_rejudge_historical_triples(monkeypatch) -> None:
    projector = _OwnedPolylineTurns()
    original = trend_lines._polyline_turn
    visits = 0

    def counted(left: Mapping[str, object], point: Mapping[str, object], right: Mapping[str, object],
                *, source_path: str) -> dict[str, object] | None:
        nonlocal visits
        visits += 1
        return original(left, point, right, source_path=source_path)

    monkeypatch.setattr(trend_lines, '_polyline_turn', counted)
    raw = []
    count = 512
    for index in range(count):
        if raw:
            raw[-1] = dict(raw[-1], state='confirmed')
        raw.append(dict(index=index, ordinal=0, time=str(index), value=20. if index % 2 else 10.,
            kind='H' if index % 2 else 'L', state='developing', available_at=index))
        result = projector.project(raw, source_path='cost')
    assert visits <= 3 * count
    monkeypatch.setattr(trend_lines, '_polyline_turn', original)
    assert result == polyline_turns(raw, source_path='cost')


def test_public_recent_snapshot_still_detaches_nested_mutable_inputs() -> None:
    raw = [dict(index=0, evidence={'items': [1]})]
    frame = ChartDrawingFrame(0, 0, raw, dict(strokes=[dict(id='lecture-0', points=raw)]))
    before = deepcopy(frame)
    with ChartDrawingHistory(start=0, count=1, closed=()) as history:
        history.remember_frame(frame)
        cast(dict[str, list[int]], raw[0]['evidence'])['items'].append(2)
        history.read_limit = 1
        assert list(history.iter_rows()) == [before]


@pytest.mark.parametrize('mirror', [False, True])
def test_cascade_indexed_join_matches_original_join_for_each_prefix(monkeypatch, mirror: bool) -> None:
    values = [20, 30, 10, 20, 14, 26, 18, 35, 28, 45, 36, 42, 30, 36, 24, 31, 26, 50]
    source = [dict(index=index, ordinal=0, time=(datetime(2026, 1, 1) + timedelta(days=index)).date().isoformat(),
        available_at=index + 1,
        kind=('H' if mirror else 'L') if index % 2 == 0 else ('L' if mirror else 'H'),
        value=100 - value if mirror else value, trend_level=0, state='reversal', source_path='sample')
        for index, value in enumerate(values)]
    optimized = cascade._enrich_promotions
    for end in range(1, len(source) + 1):
        prefix = deepcopy(source[:end])
        monkeypatch.setattr(cascade, '_enrich_promotions', _slow_enrich)
        expected = cascade.prepare_trend_cascade(prefix, symbol='COST')
        monkeypatch.setattr(cascade, '_enrich_promotions', optimized)
        assert cascade.prepare_trend_cascade(prefix, symbol='COST') == expected
        assert prefix == source[:end]
