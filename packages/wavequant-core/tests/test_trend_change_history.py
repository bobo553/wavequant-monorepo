"""变化驱动历史必须与逐日前缀一致，确认、修订和断点不能提前出现。"""

from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta
from typing import cast

import pytest

from wavequant.domain.market_structure.lecture_drawing import lecture_drawing
from wavequant.domain.market_structure.calculation_steps import observe_steps
from wavequant.domain.market_structure.trend_history_changes import (
    PolylineSourceGate, TrendChangeRows, TrendSourceChanges,
)
from wavequant.domain.market_structure.trend_lines import high_low_points_from_strokes, polyline_high_low_points
from wavequant.domain.market_structure import trend_lines
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies import chart_entry_history as chart
from wavequant.domain.strategies.chart_drawing_history import ChartDrawingHistory
from wavequant.infrastructure.persistence.calculation_step_cache import CalculationStepCache


def _row(value: object) -> dict[str, object]:
    return cast(dict[str, object], value)


def _bars(values: tuple[float, ...]) -> list[Bar]:
    return [Bar(datetime(2026, 1, 1) + timedelta(days=i), 'CHANGES', value,
        value + .25, value - .25, value, 1000) for i, value in enumerate(values)]


def _point(known: int = 2) -> dict[str, object]:
    return dict(index=1, ordinal=0, kind='H', value=12., time='2026-01-02', available_at=known)


def test_source_changes_keep_confirmation_revisions_path_closures_and_empty_initial_state() -> None:
    point = _point()
    revised = dict(point, available_at=3)
    closed = dict(revised, source_path_end_index=5)
    with TrendSourceChanges(row_count=7) as history:
        assert history.append(0, [])
        assert history.append(2, [point])
        assert not history.append(3, [dict(point)])
        assert history.append(4, [revised])
        assert history.append(5, [closed])
        assert history.append(6, [])
        actual = list(history.iter_changes())
        assert actual == [(0, []), (2, [point]), (4, [revised]), (5, [closed]), (6, [])]
        assert 'source_path_end_index' not in actual[2][1][0]
        assert actual[1][1][0]['available_at'] == 2
        actual[1][1][0]['value'] = 99.
        assert list(history.iter_changes())[1][1][0]['value'] == 12.


def test_sparse_daily_lookup_never_borrows_a_future_version_and_default_rows_are_owned() -> None:
    with TrendChangeRows(_row, row_count=6) as history:
        history.write_change(0, {'points': []})
        history.write_change(3, {'points': [_point()]})
        owned = list(history.iter_rows())
        assert [len(cast(list[object], row['points'])) for row in owned] == [0, 0, 0, 1, 1, 1]
        cast(list[object], owned[3]['points']).clear()
        assert len(cast(list[object], owned[4]['points'])) == 1
        shared = list(history.iter_rows(shared=True))
        assert shared[0] is shared[2]
        assert shared[3] is shared[5]
        assert shared[0] is not shared[3]
        reader = history.iter_rows(shared=True)
        next(reader)
        reader.close()
        assert len(list(history.iter_rows())) == 6
    assert history.closed


def test_sparse_history_rejects_missing_initial_date_and_owns_empty_resources() -> None:
    with TrendChangeRows(_row, row_count=4) as history:
        with pytest.raises(ValueError, match='initial row'):
            history.write_change(2, {})
    with TrendChangeRows(_row, row_count=0) as empty:
        assert list(empty.iter_rows()) == []
    assert empty.closed


def _drawing_baseline(bars: list[Bar]) -> list[tuple[int, int, list[dict[str, object]], dict[str, object]]]:
    snapshots = []
    closed = []
    previous_epoch = None
    previous_raw = []

    def collect(i, epoch, raw):
        nonlocal previous_epoch, previous_raw
        if previous_epoch is not None and epoch != previous_epoch and len(previous_raw) > 1:
            closed.append(chart._closed_drawing_path(previous_epoch, previous_raw, epoch))
        current = deepcopy(raw)
        drawing = dict(strokes=[*deepcopy(closed),
            *([dict(id=f'lecture-{epoch}', points=current)] if len(current) > 1 else [])])
        snapshots.append((i, epoch, current, drawing))
        previous_epoch, previous_raw = epoch, current

    lecture_drawing(bars, on_step=collect)
    return snapshots


@pytest.mark.parametrize('with_break', [False, True])
def test_drawing_tail_history_and_source_gate_match_every_original_daily_prefix(with_break: bool) -> None:
    bars = _bars((10, 13, 11, 15, 12, 17, 14, 19, 15, 13, 16, 12, 15, 11, 14, 10))
    if with_break:
        bars[8] = Bar(bars[8].timestamp, 'CHANGES', 15., 30., 1., 15., 1000)
    baseline = _drawing_baseline(bars)
    gate = PolylineSourceGate()
    with ChartDrawingHistory(start=0, count=len(bars), closed=()) as history:
        prior_epoch = None
        prior_raw = []
        previous_source = None

        def collect(i, epoch, raw):
            nonlocal prior_epoch, prior_raw, previous_source
            closed = (chart._closed_drawing_path(prior_epoch, prior_raw, epoch)
                if prior_epoch is not None and epoch != prior_epoch and len(prior_raw) > 1 else None)
            history.append(i, epoch, raw, closed)
            source = polyline_high_low_points(baseline[i][3])
            if not gate.changed(epoch, raw):
                assert source == previous_source
            previous_source = source
            prior_epoch, prior_raw = epoch, raw

        lecture_drawing(bars, on_step=collect)
        actual = list(history.iter_rows())
        assert [(row.index, row.epoch, row.raw, row.drawing) for row in actual] == baseline
        actual[4].raw.clear()
        assert list(history.iter_rows())[4].raw == baseline[4][2]
    assert history.closed


def test_same_day_projection_count_and_confirmed_point_replacement_are_source_changes() -> None:
    gate = PolylineSourceGate()
    anchor = dict(_point(), state='confirmed')
    tail = dict(index=2, ordinal=0, time=anchor['time'], value=10., state='developing')
    assert gate.changed(0, [anchor, tail])
    assert not gate.changed(0, [anchor, dict(tail, value=9.)])
    assert gate.changed(0, [anchor, dict(tail, time='2026-01-03')])
    assert gate.changed(0, [dict(anchor, available_at=4), tail])
    assert gate.changed(3, [])


def test_three_trend_levels_equal_batch_geometry_at_every_date_including_empty_path() -> None:
    bars = _bars((10, 13, 11, 15, 12, 17, 14, 19, 15, 13, 16, 12, 15, 11, 14, 10, 13, 11, 15, 12))
    bars[10] = Bar(bars[10].timestamp, 'CHANGES', 16., 32., 1., 16., 1000)
    baseline = _drawing_baseline(bars)
    with chart._staged_chart_frames(bars, resume_start=0, closed=(), previous_epoch=None,
                                   previous_raw=()) as (frames, levels):
        for day, actual in enumerate(zip(*(level.iter_rows() for level in levels))):
            # 冷前缀经过同一收敛规则；不能用旧的逐级单轮入口作升级证明基准。
            with chart._staged_chart_frames(bars[:day + 1], resume_start=0, closed=(),
                                           previous_epoch=None, previous_raw=()) as (_, cold_levels):
                expected = tuple(list(history.iter_rows())[-1] for history in cold_levels)
                assert actual == expected, day
        assert [frame.raw for frame in frames.iter_rows()] == [row[2] for row in baseline]


def test_developing_only_days_do_not_recalculate_any_trend_level(monkeypatch) -> None:
    bars = _bars(tuple(float(i + 10) for i in range(80)))
    counts = Counter()
    from wavequant.domain.market_structure import trend_cascade
    prepare = trend_cascade.prepare_trend_lines

    def counted(*args, **kwargs):
        counts[kwargs['trend_level']] += 1
        return prepare(*args, **kwargs)

    monkeypatch.setattr(trend_cascade, 'prepare_trend_lines', counted)
    with chart._staged_chart_frames(bars, resume_start=0, closed=(), previous_epoch=None,
                                   previous_raw=()) as (frames, levels):
        # 空种子到开放路径建立是两个不同来源版本，此后 78 天仅发展尾点。
        assert counts == {1: 2, 2: 2, 3: 2}
        assert all(level.change_count == 1 and level.row_count == 80 for level in levels)
        assert len(list(frames.iter_rows())) == 80


def test_each_drawing_callback_propagates_before_the_next_market_day_without_history_reads(monkeypatch) -> None:
    bars = _bars((10, 13, 11, 15, 12, 17, 14, 19, 15, 13, 16, 12, 15, 11, 14, 10))
    drawing = chart.lecture_drawing
    from wavequant.domain.market_structure import trend_cascade
    prepare = trend_cascade.prepare_trend_lines
    publications = []
    write_change = TrendChangeRows._write_encoded_change

    def publish_change(self, position, payload):
        assert active_day == position
        publications.append((id(self), position))
        return write_change(self, position, payload)

    monkeypatch.setattr(TrendChangeRows, '_write_encoded_change', publish_change)
    active_day = -1
    calls: list[tuple[int, int]] = []

    def collect_drawing(market, *, on_step):
        def collect(index, epoch, raw):
            nonlocal active_day
            active_day = index
            on_step(index, epoch, raw)
            if index == 0:
                assert calls == [(0, 1), (0, 2), (0, 3)]
            active_day = -1
        return drawing(market, on_step=collect)

    def collect_trend(source, **kwargs):
        assert active_day >= 0, 'a trend update must run inside the current drawing callback'
        calls.append((active_day, kwargs['trend_level']))
        return prepare(source, **kwargs)

    def forbidden_history_read(*args, **kwargs):
        raise AssertionError('initial cascade must not reread the complete drawing or trend history')

    monkeypatch.setattr(chart, 'lecture_drawing', collect_drawing)
    monkeypatch.setattr(trend_cascade, 'prepare_trend_lines', collect_trend)
    monkeypatch.setattr(ChartDrawingHistory, 'iter_rows', forbidden_history_read)
    monkeypatch.setattr(TrendChangeRows, 'iter_rows', forbidden_history_read)
    with chart._staged_chart_frames(bars, resume_start=0, closed=(), previous_epoch=None,
                                   previous_raw=()) as (_, levels):
        assert calls == sorted(calls)
        assert all(level.change_count for level in levels)
        # 收敛轮次可以重判，只有最终变化版本进入历史且每级每天最多发布一次。
        for level in levels:
            published = [day for owner, day in publications if owner == id(level)]
            assert len(published) == level.change_count
            assert published == sorted(set(published))


def test_cascade_lifecycle_matches_causal_daily_baseline_and_can_add_levels_lazily() -> None:
    from wavequant.domain.market_structure.trend_lifecycle import TrendLifecycle
    from wavequant.domain.strategies.market_calculations import market_calculations

    bars = _bars((20, 30, 10, 20, 14, 26, 18, 35, 28, 45, 38, 42, 32, 36, 24, 29, 26, 50))
    expected = {}
    with market_calculations(bars) as calculations:
        calculations.calculate_trend_line(3)
        assert calculations._checkpoints is not None
        for index, saved in enumerate(calculations._checkpoints.iter_rows()):
            version, _saved_index, contents = saved
            restored = TrendLifecycle.from_checkpoint((version, index, contents))
            # 检查点包含折线及三级生命周期、直接来源事件和行情证书。
            # 每个截止日冷算只能看到当日以前的行情，严格比较完整检查点。
            with market_calculations(bars[:index + 1]) as cold:
                cold.calculate_trend_line(3)
                cold_saved = list(cold._checkpoints.iter_rows())[-1]
                cold_version, _, cold_contents = cold_saved
                baseline = TrendLifecycle.from_checkpoint((cold_version, index, cold_contents))
                assert restored.checkpoint() == baseline.checkpoint()
                for level in (1, 2, 3):
                    assert tuple(event for event in calculations.history_events(level)
                                 if event['bar_index'] <= index) == cold.history_events(level)
        for level, history in enumerate(calculations.levels, 1):
            expected[level] = (list(history.iter_changes()), calculations.history_events(level))
    with market_calculations(bars) as calculations:
        calculations.calculate_polyline()
        calculations.history_events('折线')
        for level in (1, 2, 3):
            history = calculations.calculate_trend_line(level)
            assert (list(history.iter_changes()), calculations.history_events(level)) == expected[level]


def test_market_day_is_delivered_inside_drawing_after_all_levels_are_current() -> None:
    from wavequant.domain.strategies.market_calculations import market_calculations

    bars = _bars((20, 30, 10, 20, 14, 26, 18, 35, 28, 45, 38, 42, 32, 36, 24, 29, 26, 50))
    delivered = []

    def consume(day):
        assert day.frame.index == len(delivered)
        assert [level['trend_level'] for level in day.levels] == [1, 2, 3]
        assert all(event['bar_index'] == day.frame.index for event in day.lifecycle_events)
        assert not calculations.frames.closed
        assert calculations._lifecycle_events == {}
        delivered.append((day.frame.index, day.buy_eligible))

    with market_calculations(bars, on_day=consume) as calculations:
        calculations.calculate_trend_line(3)
        assert len(delivered) == len(bars)
        calculations.calculate_trend_line(3)
        assert len(delivered) == len(bars)


def test_empty_cascade_completes_without_inventing_points_or_events() -> None:
    from wavequant.domain.strategies.market_calculations import market_calculations

    with market_calculations([]) as calculations:
        calculations.calculate_trend_line(3)
        assert calculations.frames.row_count == 0
        for level, history in enumerate(calculations.levels, 1):
            assert list(history.iter_rows()) == []
            assert calculations.history_events(level) == ()


def test_partial_cascade_never_initializes_a_lifecycle_at_a_suffix_date(monkeypatch) -> None:
    from wavequant.domain.strategies.market_calculations import MarketCalculations

    bars = _bars((10, 13, 11, 15, 12, 17, 14, 19))
    prior = _drawing_baseline(bars)[3]

    def forbidden(*args, **kwargs):
        raise AssertionError('partial history must restore its existing strategy lifecycle checkpoint')

    monkeypatch.setattr(MarketCalculations, '_advance_lifecycle', forbidden)
    with chart._staged_chart_frames(bars, resume_start=4, closed=(), previous_epoch=prior[1],
                                   previous_raw=prior[2]) as (frames, levels):
        assert frames.row_count == 4
        assert all(len(list(level.iter_rows())) == 4 for level in levels)


def test_drawing_tail_resume_keeps_previous_closed_paths_and_first_new_epoch_boundary() -> None:
    bars = _bars((10, 13, 11, 15, 12, 17, 14, 19, 15, 13, 16, 12))
    bars[8] = Bar(bars[8].timestamp, 'CHANGES', 15., 30., 1., 15., 1000)
    baseline = _drawing_baseline(bars)
    prior = baseline[7]
    with chart._staged_chart_frames(bars, resume_start=8, closed=(), previous_epoch=prior[1],
                                   previous_raw=prior[2]) as (frames, levels):
        assert [(frame.index, frame.epoch, frame.raw, frame.drawing) for frame in frames.iter_rows()] == baseline[8:]
        assert all(len(list(level.iter_rows())) == 4 for level in levels)


def test_persistent_steps_store_reconstructable_drawing_changes_and_sparse_trend_versions(tmp_path) -> None:
    bars = _bars(tuple(float(i + 10) for i in range(20)))
    recorder = CalculationStepCache(tmp_path, 'change-history', lambda snapshot: None, record_details=True)
    with observe_steps(recorder):
        with chart._staged_chart_frames(bars, resume_start=0, closed=(), previous_epoch=None,
                                       previous_raw=()) as (_, levels):
            assert [recorder.steps[number - 1]['rows'] for number in (3, 10, 17)] == [1, 1, 1]
            assert all(level.change_count == 1 for level in levels)
    baseline = _drawing_baseline(bars)
    raw = []
    records = [row for key in recorder.chunks[1] for row in recorder.cache.get('calculation-chunk', key)]
    for record in records:
        assert record['history_format'] == 'drawing_tail_changes_v1'
        assert 'drawing' not in record
        raw = [*raw[:record['keep']], *record['points']]
        assert raw == baseline[record['bar_index']][2]
        assert record['history_end_index'] == len(bars) - 1
    assert raw == baseline[-1][2]
    # 单向延伸后续记录只替换一个尾点，不把整条历史作为每日存储协议。
    assert all(len(record['points']) == 1 for record in records[2:])


def test_cross_reader_borrowing_respects_available_prefix_and_public_isolation() -> None:
    with TrendChangeRows(_row, row_count=6) as history:
        history.write_change(0, {'points': []})
        history.write_change(3, {'points': [_point()]})
        history.read_limit = 3
        early = list(history.iter_rows(shared=True, _reuse_shared=True))
        assert early == [{'points': []}] * 3
        history.read_limit = 5
        later = list(history.iter_rows(shared=True, start_position=2, _reuse_shared=True))
        assert later[0] is early[0]
        assert later[1]['points'] == [_point()]
        public = list(history.iter_rows(shared=True, start_position=3))
        cast(list[object], public[0]['points']).clear()
        assert later[1]['points'] == [_point()]
        with pytest.raises(ValueError, match='readonly shared'):
            list(history.iter_rows(_reuse_shared=True))
