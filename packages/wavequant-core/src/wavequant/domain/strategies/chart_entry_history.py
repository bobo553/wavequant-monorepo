"""Replay entry contexts through the same causal hierarchy used by charts."""

from wavequant.domain.market_structure.trend_options import ordinary_completion_enabled

from dataclasses import asdict, replace
from collections.abc import Callable, Generator, Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager, nullcontext
from copy import deepcopy
from hashlib import sha256
from math import isfinite
from time import monotonic
from typing import TYPE_CHECKING, cast

from ..market_structure.trend_point_version import PointVersion
from ..market_structure.owned_evidence import CertifiedSource

if TYPE_CHECKING:
    from .backtest_analysis import BacktestAnalysis

from ..market_structure.trend_lines import _prepared_payload
from ..market_structure.trend_cascade import _TrendCascadeCache, prepare_trend_cascade
from ..market_structure.owned_evidence import owned_evidence_scope
from ..market_structure.lecture_drawing import lecture_drawing
from ..market_structure.secondary_trend import _bar_date
from ..market_structure.trend_judgement import _frozen_point_scope
from ..market_structure.trend_lines import (
    _TrendJudgementCache,
    _TrendPermissionCache,
    _ClosedPreparedPaths,
    attach_trend_permissions,
    high_low_points_from_strokes,
)
from ..market_structure.trend_history_changes import PolylineSourceGate, TrendChangeRows
from ..market_structure.trend_lifecycle import TrendLifecycle
from ..market_structure.bull_alternation_confirmation import AlternationPolicy
from ..market_structure.polyline_lifecycle import PolylineLifecycleSource
from ..market_structure.calculation_steps import calculation_steps, record_lifecycle, LIFECYCLE_STEPS
from ..market_structure.calculation_plan import CalculationPlan
from ..market_structure.trend_stage_work import TrendStageRunner
from ..market_structure.trend_evidence_reuse import _cache_completion_projections, _geometry_signature
from ..models.model import Bar
from .backtest_history_reuse import BacktestHistoryReuse, _past_attack_digest
from .hierarchical_entry import EntryContext
from .chart_drawing_history import ChartDrawingFrame as _ChartDrawingFrame, ChartDrawingHistory
from .chart_drawing_history import _OwnedDrawingProjection
from .market_calculations import MarketCalculations, MarketDay


# 六态状态与候选时钟进入检查点；旧元组缺少生命周期，不能继续恢复。
_CONFIRMATION_CACHE_VERSION = "source_causal_polyline_volume_breakout_v159"


def _combined_a_facts(audit: Sequence[Mapping[str, object]]) -> Sequence[Mapping[str, object]]:
    """工作进程只传递组合A实际读取的N事实，公开审计和检查点仍保存原全文。"""
    if type(audit) not in (list, tuple) or _geometry_signature(audit) is None:
        return audit
    fields = ('event', 'direction', 'origin', 'bar_index', 'neckline', 'pullback', 'known_at', 'one_p', 'two_t')
    result: list[Mapping[str, object]] = []
    for event in audit:
        if type(event) is not dict:
            return audit
        if event.get('event') != 'n_completed' or event.get('direction') != 'up':
            continue
        row = {field: event[field] for field in fields if field in event}
        if any(type(value) not in (str, int, float, bool, type(None)) for value in row.values()):
            return audit
        result.append(row)
    return tuple(result)


def _sidecar_level(level: Mapping[str, object]) -> Mapping[str, object]:
    # 两个只读观察器只消费strokes。保留路径和点的全文，避免把来源账本、
    # 候选账本和同一资格路径的副本一起传输；主消费器仍拥有完整level。
    if type(level) is dict:
        return {'strokes': level['strokes']} if 'strokes' in level else {}
    return level


def _stage_frame(value: object) -> _ChartDrawingFrame:
    if not isinstance(value, _ChartDrawingFrame):
        raise ValueError('staged drawing rows must be chart drawing frames')
    return value


def _stage_prepared(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ValueError('staged prepared rows must be mappings with string keys')
    return dict(cast(Mapping[str, object], value))


def _stage_source(value: object) -> list[dict[str, object]]:
    return [dict(point) for point in _permission_records(value, 'staged source points')]


def _closed_drawing_path(
    epoch: int, points: Sequence[Mapping[str, object]], next_epoch: int,
) -> dict[str, object]:
    """把首次路径断点保存到关闭副本，不改写此前已经记录的历史点。

    新 epoch 可以暂时没有任何折线点；等待下一个 seed 才截行情会让旧段
    借到断点后的 K 线。首次 epoch 切换已经确定旧路径的行情上界，因此
    当时就保存真实边界，并随几何递归保留。该字段不改变价格、点数量、
    自身可知日或证书，后续空 epoch 也不能把首次边界推迟。
    """
    closed_points: list[dict[str, object]] = []
    for point in points:
        current = dict(point)
        existing = current.get('source_path_end_index')
        if existing is not None and (type(existing) is not int or existing < 0):
            raise ValueError('source_path_end_index must be a nonnegative integer')
        current['source_path_end_index'] = min(next_epoch, existing) if type(existing) is int else next_epoch
        closed_points.append(current)
    return dict(id=f'lecture-{epoch}', points=closed_points)


@contextmanager
def _chart_calculation_scope(
    bars: Sequence[Bar], *, resume_start: int, closed: Sequence[Mapping[str, object]],
    previous_epoch: int | None, previous_raw: Sequence[Mapping[str, object]],
    _stage: Callable[[str], None] | None = None,
    _after_drawing: Callable[[ChartDrawingHistory], None] | None = None,
    _after_level: Callable[[int, TrendChangeRows[dict[str, object]]], None] | None = None,
    _on_day: Callable[[MarketDay], None] | None = None,
) -> Iterator[MarketCalculations]:
    """折线当天变化立即逐级传播；每级的几何与六态在同一次行情推进中保存。

    初次请求三级时仅遍历一次行情。先单独请求折线或低级别后再请求高级别，
    才读取已有日期流补齐缺失级别；不重新计算已有级别。部分日线续接只提供
    几何，六态继续由既有交易检查点恢复，不能把后缀第一天当作历史第零天。
    """
    with ExitStack() as resources:
        resources.enter_context(owned_evidence_scope())
        if _on_day is not None and resume_start != 0:
            raise ValueError('market day subscriptions require complete history')
        steps = calculation_steps(bars)
        row_count = max(0, len(bars) - resume_start)
        full_history = resume_start == 0
        frames = resources.enter_context(ChartDrawingHistory(start=resume_start, count=row_count, closed=closed))
        prepared: list[TrendChangeRows[dict[str, object]]] = []
        caches: list[_TrendJudgementCache] = []
        paths: list[_ClosedPreparedPaths] = []
        latest: list[dict[str, object] | None] = []
        source_keys: list[bytes | None] = []
        drawing_closed = [dict(path) for path in closed]
        drawing_epoch, drawing_raw = previous_epoch, previous_raw
        drawn = False
        failed = False
        on_frame: Callable[[_ChartDrawingFrame], None] | None = None
        symbol = bars[0].symbol if bars else 'STRUCTURE'
        dates = {bar.timestamp.date().isoformat(): index for index, bar in enumerate(bars)}
        last_report = monotonic()

        def report(message: str) -> None:
            if _stage is not None:
                _stage(message)

        def collect(index: int, epoch: int, raw: Sequence[Mapping[str, object]]) -> None:
            nonlocal drawing_epoch, drawing_raw
            if index < resume_start:
                return
            publication_started = monotonic()
            closed_path = None
            if drawing_epoch is not None and epoch != drawing_epoch and len(drawing_raw) > 1:
                closed_path = _closed_drawing_path(drawing_epoch, drawing_raw, epoch)
                drawing_closed.append(closed_path)
            change = frames.append(index, epoch, raw, closed_path)
            if steps is not None and change is not None:
                steps.append(1, change)
            drawing: dict[str, object] = dict(strokes=[*drawing_closed,
                *([dict(id=f'lecture-{epoch}', points=raw)] if len(raw) > 1 else [])])
            if on_frame is not None:
                frame = _ChartDrawingFrame(index, epoch, cast(list[dict[str, object]], raw), drawing)
                if _on_day is not None:
                    frames._remember_owned_frame(frame)
                calculations.timing.publication += monotonic() - publication_started
                on_frame(frame)
            else:
                calculations.timing.publication += monotonic() - publication_started
            drawing_epoch, drawing_raw = epoch, raw

        def calculate_polyline() -> None:
            nonlocal drawn, failed
            if failed:
                raise RuntimeError('a failed calculation scope cannot be reused')
            if drawn:
                return
            if steps is not None:
                steps.begin(1)
            report('计算折线及变化触发的各级趋势、六态' if on_frame is not None else '计算图表折线历史')
            try:
                drawing_resumed = monotonic()

                def timed_collect(index: int, epoch: int, raw: Sequence[Mapping[str, object]]) -> None:
                    nonlocal drawing_resumed
                    calculations.timing.drawing += monotonic() - drawing_resumed
                    try:
                        collect(index, epoch, raw)
                    finally:
                        drawing_resumed = monotonic()

                lecture_drawing(bars, on_step=timed_collect)
                calculations.timing.drawing += monotonic() - drawing_resumed
                drawn = True
                if steps is not None:
                    steps.complete(1)
                if _after_drawing is not None:
                    _after_drawing(frames)
            except Exception:
                failed = True
                raise

        def calculate_trend_line(level: int) -> None:
            nonlocal on_frame, failed, last_report
            if failed:
                raise RuntimeError('a failed calculation scope cannot be reused')
            if len(prepared) >= level:
                return
            if _on_day is not None and (level != 3 or drawn or prepared):
                raise ValueError('market day subscriptions require an initial three-level cascade')
            first = len(prepared) + 1
            gate = PolylineSourceGate()
            projection = _OwnedDrawingProjection()
            polyline = PolylineLifecycleSource(dates, _owned_tail=True)
            base_rows = prepared[first - 2].iter_rows(shared=True) if first > 1 else None
            base_previous: dict[str, object] | None = None
            lower_events: dict[int, list[dict[str, object]]] = {}
            lower_cached = full_history and first - 1 in calculations._lifecycle_events
            if full_history and first == 1 and level == 3:
                calculations._checkpoints = resources.enter_context(TrendChangeRows(
                    lambda value: cast(tuple[object, ...], value), row_count=row_count))
            if lower_cached:
                for event in calculations._owned_lifecycle_events(first - 1):
                    lower_events.setdefault(cast(int, event['bar_index']), []).append(event)
            for number in range(first, level + 1):
                closed_paths = resources.enter_context(_ClosedPreparedPaths(_owned_history=True))
                paths.append(closed_paths)
                prepared.append(resources.enter_context(TrendChangeRows(closed_paths.decode, row_count=row_count,
                    load_encoded=closed_paths.load_encoded)))
                caches.append(_TrendJudgementCache(tail_reuse=True, state_memory_bytes=32 * 1024 * 1024))
                latest.append(None)
                source_keys.append(None)
                if steps is not None:
                    step = 3 + (number - 1) * 7
                    steps.begin(step)
                    if full_history:
                        for state_step in range(step + 1, step + 7):
                            steps.begin(state_step)

            round_caches = resources.enter_context(_TrendCascadeCache(caches[first - 1:level]))

            def advance(frame: _ChartDrawingFrame) -> None:
                nonlocal base_previous, last_report
                source: Sequence[Mapping[str, object]] | None = None
                checkpoint_changed = False
                events = lower_events.get(frame.index, [])
                projection_started = monotonic()
                if first == 1:
                    if gate.changed(frame.epoch, frame.raw):
                        source = projection.project(frame)
                    if full_history and not lower_cached:
                        polyline_row = polyline.advance(frame)
                        calculations.timing.projection += monotonic() - projection_started
                        lifecycle_started = monotonic()
                        events = calculations._advance_lifecycle(0, polyline_row, frame.index, frame=frame)
                        calculations.timing.lifecycle += monotonic() - lifecycle_started
                    else:
                        calculations.timing.projection += monotonic() - projection_started
                else:
                    assert base_rows is not None
                    base = next(base_rows)
                    if base is not base_previous:
                        source = high_low_points_from_strokes(base['strokes'], include_key_break_attacks=True)
                    base_previous = base
                    calculations.timing.projection += monotonic() - projection_started
                resolved: Sequence[dict[str, object] | None]
                geometry_started = monotonic()
                if source is not None:
                    resolved = prepare_trend_cascade(source, symbol=symbol, bars=bars,
                        first=first, last=level, caches=caches[first-1:level], closed_paths=paths[first-1:level],
                        _round_caches=round_caches, _owned_source=True)
                else:
                    resolved = [latest[number - 1] for number in range(first, level + 1)]
                calculations.timing.geometry += monotonic() - geometry_started
                for number in range(first, level + 1):
                    publication_started = monotonic()
                    offset, step = number - 1, 3 + (number - 1) * 7
                    result = resolved[number - first]
                    if result is None:
                        raise ValueError('a trend requires its initial source on the first date')
                    # 无来源变化时 resolved 借用本阶段的同一只读版本；逐日行情
                    # 仍推进六态，但不必反复编码整棵未变化的结构证据。
                    encoded = None if result is latest[offset] else paths[offset].encode(result)
                    key = source_keys[offset] if encoded is None else _prepared_payload(encoded)
                    changed = result is not latest[offset] and (
                        latest[offset] is None or key is None or key != source_keys[offset])
                    if changed:
                        checkpoint_changed = True
                        latest[offset] = result
                        source_keys[offset] = key
                        if key is not None:
                            prepared[offset]._write_encoded_change(frame.index - resume_start, key)
                        else:
                            assert encoded is not None
                            # 未知审计沿用完整原始行，不能将内部块引用带进公共
                            # pickle 冷路径而破坏跨字段别名或触发额外恢复。
                            prepared[offset].write_change(frame.index - resume_start, result)
                        if steps is not None:
                            steps.append(step, dict(bar_index=frame.index, trend_level=number,
                                history_format='trend_changes_v1', history_end_index=len(bars) - 1,
                                strokes=result['strokes']))
                    current = latest[offset]
                    assert current is not None
                    calculations.timing.publication += monotonic() - publication_started
                    lifecycle_started = monotonic()
                    # 翻向发生时冻结下级来源已处理状态；后续修订只重放检查点之后。
                    for event in events:
                        if (event.get('event') == 'trend_lifecycle_state'
                                and event.get('state') in ('空翻多', '多翻空')):
                            path = event.get('source_path')
                            if isinstance(path, str):
                                round_caches.remember_source_flip(number, path)
                    if full_history:
                        events = calculations._advance_lifecycle(number, current, frame.index, frame=frame,
                                                                  source_events=events)
                        checkpoint_changed = checkpoint_changed or bool(events)
                        if steps is not None:
                            record_lifecycle(steps, events)
                    calculations.timing.lifecycle += monotonic() - lifecycle_started
                if checkpoint_changed and full_history and first == 1 and level == 3:
                    publication_started = monotonic()
                    calculations._save_lifecycle_checkpoint(frame.index)
                    calculations.timing.publication += monotonic() - publication_started
                if full_history and first == 1 and level == 3:
                    calculations._publish_day(frame, cast(Sequence[Mapping[str, object]], latest))
                # 整轮完成后统一上报，避免一级先消耗节流额度而长期遮住二、三级。
                now = monotonic()
                if _stage is not None and (now - last_report >= 1
                        or frame.index in (resume_start, len(bars) - 1)):
                    names = ('折线', '一级', '二级', '三级')
                    chain = ' → '.join(names[0 if first == 1 else first:level + 1])
                    updates = '、'.join(f'{names[number]}{prepared[number - 1].change_count}次'
                        for number in range(first, level + 1))
                    report(f'变化联动：{chain}（含六态） · '
                        f'已处理{frame.index - resume_start + 1}/{row_count}个交易日 · '
                        f'趋势更新：{updates} · 截至{_bar_date(bars[frame.index])}')
                    last_report = now

            try:
                with _frozen_point_scope(memory_bytes=32 * 1024 * 1024):
                    if not drawn:
                        on_frame = advance
                        calculate_polyline()
                    else:
                        reader = frames.iter_rows(shared=True)
                        try:
                            for frame in reader:
                                advance(frame)
                        finally:
                            cast(Generator[_ChartDrawingFrame, None, None], reader).close()
                if full_history and first == 1 and not lower_cached:
                    calculations._seal_lifecycle(0)
                for number in range(first, level + 1):
                    step = 3 + (number - 1) * 7
                    if full_history:
                        calculations._seal_lifecycle(number)
                    if steps is not None:
                        if full_history:
                            for state_step in range(step + 1, step + 7):
                                steps.complete(state_step)
                        steps.complete(step)
                    if _after_level is not None:
                        _after_level(number, prepared[number - 1])
                totals = '、'.join(f'{number}级{prepared[number - 1].change_count}次'
                    for number in range(first, level + 1))
                report(f'折线至{level}级趋势及六态已推进至历史末日 · 共{row_count}个交易日 · 趋势更新：{totals}')
            except Exception:
                failed = True
                raise
            finally:
                on_frame = None
                if base_rows is not None:
                    cast(Generator[dict[str, object], None, None], base_rows).close()

        calculations = MarketCalculations(bars, frames, prepared, draw=calculate_polyline,
            trend=calculate_trend_line, cascade=calculate_trend_line, on_day=_on_day)
        try:
            yield calculations
        finally:
            calculations._close_shared_history()
            caches.clear()
            paths.clear()
            latest.clear()
            source_keys.clear()
            calculations._strategy_permission_cache = None


@contextmanager
def _staged_chart_frames(
    bars: Sequence[Bar], *, resume_start: int, closed: Sequence[Mapping[str, object]],
    previous_epoch: int | None, previous_raw: Sequence[Mapping[str, object]],
    _stage: Callable[[str], None] | None = None,
    _runner: TrendStageRunner | None = None,
    _after_drawing: Callable[[ChartDrawingHistory], None] | None = None,
    _after_level: Callable[[int, TrendChangeRows[dict[str, object]]], None] | None = None,
    _plan: CalculationPlan | None = None,
    _run_step: Callable[[int, ChartDrawingHistory, tuple[TrendChangeRows[dict[str, object]], ...]], None] | None = None,
    _on_ready: Callable[[MarketCalculations], None] | None = None,
    _on_day: Callable[[MarketDay], None] | None = None,
) -> Iterator[tuple[ChartDrawingHistory, tuple[TrendChangeRows[dict[str, object]], ...]]]:
    """历史消费者共用变化联动流，不再逐级等待全部历史完成。"""
    with _chart_calculation_scope(
        bars, resume_start=resume_start, closed=closed, previous_epoch=previous_epoch,
        previous_raw=previous_raw, _stage=_stage, _after_drawing=_after_drawing, _after_level=_after_level,
        _on_day=_on_day,
    ) as calculations:
        if _on_ready is not None:
            _on_ready(calculations)
        calculations.calculate_trend_line(3)
        if _plan is not None:
            if _run_step is None:
                raise ValueError('analysis steps require an execution callback')
            _run_step(2, calculations.frames, calculations.levels)
        yield calculations.frames, calculations.levels


def _strategy_permission_view(level: Mapping[str, object]) -> dict[str, object]:
    """策略只读取独立资格路径，不能把趋势线结构点当作交易许可证。

    新协议的 strokes 是高低点决定的结构，qualified_strokes 才保留行情
    确认证书。显式空资格必须保持空，不能用几何作 fallback。缺少新字段
    的旧存储／旧调用者沿用其原有已发布 strokes 契约；候选下降压力和
    刷新阈值仍作为独立约束保留，不因该视图获得上涨或交替资格。
    """
    result = dict(level)
    if 'qualified_strokes' not in level:
        return result
    qualified = level['qualified_strokes']
    if not isinstance(qualified, (list, tuple)):
        raise ValueError('qualified_strokes must be an explicit sequence of strategy permissions')
    result['strokes'] = qualified
    return result


def _permission_records(value: object, field: str) -> tuple[Mapping[str, object], ...]:
    """显式解析资格视图中的记录序列，不把宽泛元数据当作可迭代路径。

    趋势入口以object承载几何、证书和展示附件；选择资格字段之后仍需在
    消费边界确认其容器形状。价格与坐标由原领域判定器校验，这里只保证
    路径、点、观察或地标可以按字符串字段读取，不以类型断言放行未知对象。
    """
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError(f'{field} must be a sequence of permission records')
    records: list[Mapping[str, object]] = []
    for record in value:
        if not isinstance(record, Mapping) or any(not isinstance(key, str) for key in record):
            raise ValueError(f'{field} must contain mappings with string keys')
        records.append(cast(Mapping[str, object], record))
    return tuple(records)


def _confirmed_bear_flip_events(
    paths: Mapping[str, Sequence[Mapping[str, object]]], *, trend_level: int,
    dates: Mapping[str, int], asof_index: int,
) -> list[dict[str, object]]:
    """翻空发生在破位点；观察时钟包含命名所需的全部左侧上下文。"""
    events: list[dict[str, object]] = []
    for path, points in paths.items():
        for point in points:
            if point.get('state') in ('seed', 'developing') or point.get('display_only'):
                continue
            point_known = point.get('available_at')
            point_index = dates.get(point_known) if isinstance(point_known, str) else None
            if point_index is None or point_index > asof_index:
                continue
            for observation in _permission_records(point.get('observations', []), 'point observations'):
                if observation.get('title') != '翻多为空':
                    continue
                available_at = observation.get('available_at')
                known_index = dates.get(available_at) if isinstance(available_at, str) else None
                if known_index is None or known_index > asof_index:
                    continue
                events.append(dict(bar_index=asof_index, event='hierarchy_bear_flip',
                    trend_level=trend_level, source_path=path, pivot_index=point['index'],
                    pivot_ordinal=point.get('ordinal', 0), available_at=available_at))
    return events


def _confirmed_descent_pressures(
    level: Mapping[str, object], dates: Mapping[str, int], asof_index: int,
) -> list[dict[str, object]]:
    """Retain confirmed downward pressures without publishing an upward trend.

    A higher-level rise can fail its publication gate while its following
    downward reversal is still a confirmed structural fact. Only that level's
    dated descent highs are eligible; raw lower-level highs are not pressures.
    """
    paths = level.get("structure_strokes", level.get("strokes", ()))
    if not isinstance(paths, (list, tuple)):
        return []
    pressures: list[dict[str, object]] = []
    for raw_path in paths:
        if not isinstance(raw_path, Mapping):
            continue
        path = cast(Mapping[str, object], raw_path)
        if path.get("display_only"):
            continue
        vertices = path.get("points")
        if not isinstance(vertices, (list, tuple, PointVersion, CertifiedSource)):
            continue
        for raw_point in vertices:
            if not isinstance(raw_point, Mapping):
                continue
            point = cast(Mapping[str, object], raw_point)
            if (point.get("kind") != "H" or point.get("wave_direction_after") != "down"
                    or point.get("state") not in ("confirmed", "reversal", "teaching")
                    or point.get("display_only")):
                continue
            if point.get("trend_level", 2) != 2:
                continue
            index, value, known = point.get("index"), point.get("value"), point.get("available_at")
            if type(index) is not int or not 0 <= index <= asof_index:
                continue
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not isfinite(value) or value <= 0:
                continue
            known_index = dates.get(known) if isinstance(known, str) else known if type(known) is int else None
            if known_index is None or not index <= known_index <= asof_index:
                continue
            pressure: dict[str, object] = dict(index=index, value=value, source="confirmed_descent",
                                               available_at=known, known_index=known_index)
            for field in ("confirmation_rule", "broken_key", "confirmed_by"):
                if field in point:
                    pressure[field] = point[field]
            pressures.append(pressure)
    return pressures


def _has_confirmation_price_cross(
    raw: Sequence[object], levels: Sequence[tuple[Mapping[str, object], Mapping[str, object]]],
    previous: Bar, current: Bar, asof: str, asof_index: int,
    *, n_target_trend_confirmation_enabled: bool = False,
) -> bool:
    """Refresh price certificates even when no confirmed source vertex changed.

    A live candle may complete a source close re-cross or a same-level extreme
    break. Candidate skeletons retain pressures absent from public strokes;
    raw points also retain pressures from the base level. Unknown evidence and
    developing endpoints never manufacture a refresh threshold.
    """
    points: list[object] = list(raw)
    for pair in levels:
        for level in pair:
            observations = level.get('n_target_observations', ()) if n_target_trend_confirmation_enabled else ()
            if isinstance(observations, (list, tuple)):
                for observation in observations:
                    if not isinstance(observation, Mapping):
                        continue
                    known, target = observation.get('available_at'), observation.get('one_p_target')
                    if not isinstance(target, (int, float)) or isinstance(target, bool) or not isfinite(target):
                        continue
                    if not ((isinstance(known, str) and known <= asof)
                            or (type(known) is int and known <= asof_index)):
                        continue
                    if (observation.get('direction') == 'up' and previous.high <= target < current.high
                            or observation.get('direction') == 'down' and previous.low >= target > current.low):
                        return True
            for field in ("strokes", "candidate_strokes"):
                paths = level.get(field)
                if not isinstance(paths, (list, tuple)):
                    continue
                for raw_path in paths:
                    if not isinstance(raw_path, Mapping):
                        continue
                    path = cast(Mapping[str, object], raw_path)
                    vertices = path.get("points")
                    if isinstance(vertices, (list, tuple, PointVersion, CertifiedSource)):
                        points.extend(vertices)
    known_anchor_count = 0
    for raw_point in points:
        if not isinstance(raw_point, Mapping):
            continue
        point = cast(Mapping[str, object], raw_point)
        if point.get("state") in ("seed", "developing") or point.get("display_only"):
            continue
        value, known = point.get("value"), point.get("available_at")
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        if isinstance(known, str):
            if known > asof:
                continue
            newly_known = known == asof
            newly_eligible = known == _bar_date(previous)
        elif type(known) is int:
            if known > asof_index:
                continue
            newly_known = known == asof_index
            newly_eligible = known == asof_index - 1
        else:
            continue
        known_anchor_count += 1
        if newly_known or newly_eligible:
            return True
        if point.get("kind") == "H" and (previous.high <= value < current.high
                                         or previous.close <= value < current.close):
            return True
        if point.get("kind") == "L" and (previous.low >= value > current.low
                                         or previous.close >= value > current.close):
            return True
    # A one-p target is outside its source pivots. Continuing market extremes
    # must therefore recheck a known three-anchor N even without a pivot cross.
    if (n_target_trend_confirmation_enabled and not levels and known_anchor_count >= 3
            and (current.high > previous.high or current.low < previous.low)):
        return True
    return False


def _copy_replay_state(state):
    """Detach only containers the next callback extends or replaces.

    Dated entries, events, landmarks, and level reductions are read-only after
    publication. The drawing's live point list is the exception: its final
    developing endpoint may be replaced by the next candle.
    """
    (
        history, events, active, first_seen, retired, closed, previous_epoch,
        previous_raw, signature, landmarks, invalidated, levels,
        shallow_candidate, resistance_key, anchors_at_attack, lifecycle, prior_shallow, prior_combined, prior_pullback,
    ) = state
    return (
        history.copy(), events.copy(), active.copy(), first_seen.copy(),
        retired.copy(), closed.copy(), previous_epoch, deepcopy(previous_raw),
        signature, landmarks.copy(), invalidated.copy(), levels,
        shallow_candidate, resistance_key, anchors_at_attack.copy(),
        lifecycle.copy() if isinstance(lifecycle, TrendLifecycle) else TrendLifecycle.from_checkpoint(lifecycle),
        prior_shallow.copy(), prior_combined.copy(), prior_pullback.copy(),
    )


@_cache_completion_projections
def chart_entry_history(
    bars: Sequence[Bar], *, audit=(), shallow_candidate_sink=None, combined_candidate_sink=None,
    secondary_pullback_sink=None, prefix_cache=None, n_target_trend_confirmation_enabled: bool = False,
    _history_reuse: BacktestHistoryReuse | None = None,
    _checkpoint_reuse: BacktestHistoryReuse | None = None,
    _stage: Callable[[str], None] | None = None,
    _prepared_history: 'BacktestAnalysis | None' = None,
    _minute_analysis: 'BacktestAnalysis | None' = None,
    _confirmation_policy: AlternationPolicy | None = None,
    _entry_days: tuple[bool, ...] | None = None,
) -> tuple[dict[int, tuple[EntryContext, ...]], list[dict[str, object]]]:
    """读取趋势确认产生的调度资格，仅在有效窗口准备策略形态。

    折线及三级趋势在同一天逐级传播，六态事件和检查点直接复用。历史行
    来自当时的输入前缀，不由最终点倒裁；部分日线继续恢复旧检查点。消费保留
    signature／行情越位刷新、首次观察日、失效和缓存逻辑；来源已确认的
    回调可作入场证据，但不能借资格附件制造正式上级高低点。
    """
    if type(n_target_trend_confirmation_enabled) is not bool:
        raise ValueError('n_target_trend_confirmation_enabled must be a boolean')
    bars = tuple(bars)
    if _prepared_history is not None and _prepared_history.bars != bars:
        raise ValueError('prepared analysis must match the exact market input')
    entry_days = _prepared_history.buy_days if _prepared_history is not None else _entry_days
    if entry_days is not None and len(entry_days) != len(bars):
        raise ValueError('prepared trading eligibility must cover the market history')
    cache_version = _CONFIRMATION_CACHE_VERSION + ':ordinary:' + str(ordinary_completion_enabled())
    confirmation_policy = (_prepared_history.calculations.confirmation_policy
        if _prepared_history is not None and _prepared_history.calculations is not None
        else _confirmation_policy or AlternationPolicy())
    cache_version += ':' + sha256(repr(confirmation_policy).encode()).hexdigest()
    if entry_days is not None:
        # 带区间裁剪的策略缓存不能冒充旧的全历史分钟检查点。
        cache_version += (':live-causal-v1' if _prepared_history is not None and _prepared_history.calculations is not None
            and _prepared_history.calculations.reading_live_day else ':' + sha256(bytes(entry_days)).hexdigest())
    steps = calculation_steps(bars)
    dates = {_bar_date(bar): i for i, bar in enumerate(bars)}
    history = {}
    events = []
    active = {}
    first_seen = {}
    retired = set()
    closed = []
    previous_epoch = None
    previous_raw = []
    signature = None
    landmarks = []
    invalidated = set()
    from ..market_structure.squeeze_alternation import squeeze_anchors, squeeze_alternations
    attacks = {e["bar_index"] for e in audit if e["event"] == "n_completed" and e.get("direction") == "up"}
    anchors_at_attack = {}
    lifecycle = TrendLifecycle()
    market_calculation = _prepared_history.calculations if _prepared_history is not None else None
    live_owner = (market_calculation._chart_prefix_owner
        if market_calculation is not None and market_calculation.reading_live_day else None)
    cached_lifecycle: dict[int, list[dict[str, object]]] | None = None
    polyline_lifecycle = PolylineLifecycleSource(dates)
    levels: tuple[tuple[dict, dict], ...] = ()
    shallow_candidate = None
    resistance_key = None
    last = len(bars) - 1
    past_attack_certificate = _past_attack_digest(audit, last - 1)
    # Intraday replays change only the unfinished last candle. All earlier
    # drawing steps (and their expensive hierarchy reductions) are identical.
    # Include the earlier attack dates because they control stored anchors.
    prefix_key = (
        tuple(bars[:-1]),
        frozenset(i for i in attacks if i < last),
        shallow_candidate_sink is not None,
        combined_candidate_sink is not None,
        secondary_pullback_sink is not None,
        cache_version,
        n_target_trend_confirmation_enabled,
        past_attack_certificate,
    )
    cached_key = prefix_cache.get("key") if prefix_cache is not None else None
    checkpoint_owner = _history_reuse if _history_reuse is not None else _checkpoint_reuse
    checkpoint_options = (
        n_target_trend_confirmation_enabled,
        shallow_candidate_sink is not None,
        combined_candidate_sink is not None,
        secondary_pullback_sink is not None,
    )
    checkpoint_channel = (checkpoint_owner._checkpoint_capture(
                            'chart', bars, checkpoint_options, version=cache_version)
                          if checkpoint_owner is not None else None)
    if checkpoint_owner is not None and cached_key != prefix_key:
        seed = checkpoint_owner._chart_seed(
            bars, audit=audit, options=checkpoint_options, version=cache_version)
        if seed is not None:
            if prefix_cache is None:
                prefix_cache = seed
            else:
                prefix_cache.update(seed)
            cached_key = prefix_cache['key']
    from .combined_a_replay import CombinedAReplay, attack_certificates, later_attack_revision
    revised_combined = None
    saved_entry_days = prefix_cache.get('_entry_days') if prefix_cache is not None else None
    same_live_prefix = (live_owner is None or prefix_cache is not None
        and prefix_cache.get('_live_owner') is live_owner
        and isinstance(saved_entry_days, tuple) and entry_days is not None
        and len(saved_entry_days) <= len(entry_days)
        and tuple(entry_days[:len(saved_entry_days)]) == saved_entry_days)
    if same_live_prefix and past_attack_certificate is not None and cached_key == prefix_key:
        resume_start = last
    elif (
        same_live_prefix
        and cached_key is not None
        and len(cached_key) == len(prefix_key)
        and cached_key[0] == tuple(bars[:-2])
        and cached_key[1] == frozenset(i for i in attacks if i < last - 1)
        and cached_key[2] == (shallow_candidate_sink is not None)
        and cached_key[3] == (combined_candidate_sink is not None)
        and cached_key[4] == (secondary_pullback_sink is not None)
        and cached_key[5] == cache_version
        and cached_key[6] == n_target_trend_confirmation_enabled
        and cached_key[7] is not None
        and cached_key[7] == _past_attack_digest(audit, last - 2)
    ):
        # Yesterday was unfinished when the prior checkpoint was saved. Replay
        # its final candle, then today's partial candle, from the older state.
        resume_start = last - 1
    elif (
        live_owner is not None and same_live_prefix
        and isinstance(cached_key, tuple) and len(cached_key) == len(prefix_key)
        and isinstance(cached_key[0], tuple) and 0 < len(cached_key[0]) < last - 1
        and cached_key[0] == bars[:len(cached_key[0])]
        and cached_key[1] == frozenset(i for i in attacks if i < len(cached_key[0]))
        and cached_key[2:7] == prefix_key[2:7]
        and cached_key[7] is not None
        and cached_key[7] == _past_attack_digest(audit, len(cached_key[0]) - 1)
    ):
        # 无资格空仓时允许跳过多日调用；旧未完成日和间隔后缀仍按原顺序回放。
        # 完整攻击证据及已观察资格前缀必须一致，不能只用日期命中旧检查点。
        resume_start = len(cached_key[0])
    else:
        resume_start = 0
        # 新资格窗口只重新解释 N。主趋势、生命周期、浅底和回调不依赖
        # audit；只在完整旧前缀及攻击子集可证时，用当时实际几何重算组合 A。
        if (live_owner is not None and market_calculation is not None
                and same_live_prefix and past_attack_certificate is not None
                and isinstance(cached_key, tuple) and len(cached_key) == len(prefix_key)
                and isinstance(cached_key[0], tuple) and 0 < len(cached_key[0]) <= last
                and cached_key[0] == bars[:len(cached_key[0])]
                and frozenset(i for i in attacks if i < len(cached_key[0])).issubset(cached_key[1])
                and cached_key[2:7] == prefix_key[2:7] and cached_key[7] is not None
                and prefix_cache is not None and prefix_cache.get('_attack_key') is cached_key
                and later_attack_revision(audit, prefix_cache.get('_attack_rows'), len(cached_key[0]))):
            replay = prefix_cache.get('_combined_replay')
            if combined_candidate_sink is None:
                resume_start = len(cached_key[0])
            elif isinstance(replay, CombinedAReplay) and entry_days is not None:
                revised_combined = replay.recompute(bars, dates, entry_days, len(cached_key[0]), audit,
                    market_calculation._permission_cache_for(bars)._market_index)
                if revised_combined is not None:
                    resume_start = len(cached_key[0])
    combined_replay = prefix_cache.get('_combined_replay') if prefix_cache is not None else None
    if live_owner is not None and prefix_cache is not None and combined_candidate_sink is not None:
        if not resume_start or not isinstance(combined_replay, CombinedAReplay):
            combined_replay = prefix_cache['_combined_replay'] = CombinedAReplay()
    else:
        combined_replay = None
    if resume_start:
        (
            history, events, active, first_seen, retired, closed, previous_epoch,
            previous_raw, signature, landmarks, invalidated, levels,
            shallow_candidate, resistance_key, anchors_at_attack, lifecycle, prior_shallow, prior_combined, prior_pullback,
        ) = _copy_replay_state(prefix_cache["checkpoint"])
        if revised_combined is not None:
            prior_combined = revised_combined
        anchors_at_attack = {index: value for index, value in anchors_at_attack.items() if index in attacks}
        if shallow_candidate_sink is not None:
            shallow_candidate_sink.update(prior_shallow)
        if combined_candidate_sink is not None:
            combined_candidate_sink.update(prior_combined)
        if secondary_pullback_sink is not None:
            secondary_pullback_sink.update(prior_pullback)

    # 检查点已经持有事件账本；恢复已发布身份即可，无需新增一份时钟状态。
    seen_bear_flips = {
        (event['trend_level'], event['source_path'], event['pivot_index'],
         event['pivot_ordinal'], event['available_at'])
        for event in events if event.get('event') == 'hierarchy_bear_flip'
    }
    permission_cache = (market_calculation._permission_cache_for(bars)
        if market_calculation is not None else _TrendPermissionCache(bars))
    market_index = permission_cache._market_index

    def accept(frame: _ChartDrawingFrame, prepared: Sequence[Mapping[str, object]], *,
               _prepared_at: Callable[[], Sequence[Mapping[str, object]]] | None = None) -> None:
        nonlocal previous_epoch, previous_raw, signature, landmarks, active, invalidated, levels, resistance_key, shallow_candidate
        nonlocal lifecycle
        current_lifecycle: tuple[object, ...] | None = None
        i, epoch, raw = frame.index, frame.epoch, frame.raw
        event_start = len(events)
        if previous_epoch is not None and epoch != previous_epoch and len(previous_raw) > 1:
            closed.append(_closed_drawing_path(previous_epoch, previous_raw, epoch))
        previous_epoch, previous_raw = epoch, raw
        # 交易形态只在当天资格成立后启动；下一日资格不能回填本日计算。
        strategy_day = entry_days is None or entry_days[i]
        live_lifecycle = (market_calculation._live_lifecycle_at(i)
            if cached_lifecycle is not None and market_calculation is not None else None)
        if cached_lifecycle is None:
            # 本级真实翻多必须先成立，才允许按需准备资格内部的 N 证明。
            lifecycle_events = lifecycle.advance((polyline_lifecycle.advance(frame), *prepared), bars, i, dates,
                polyline=frame.raw, polyline_path=f'lecture-{frame.epoch}',
                confirmation_policy=confirmation_policy, _market_index=market_index)
            lifecycle_events = [event for event in lifecycle_events if event['trend_level'] != 0]
            if steps is not None:
                record_lifecycle(steps, lifecycle_events)
        else:
            lifecycle_events = cached_lifecycle.get(i, [])
            needs_state = (checkpoint_channel is not None and i in checkpoint_channel.store.targets
                           or (prefix_cache is not None or _minute_analysis is not None) and i == last - 1)
            if (live_lifecycle is None and (needs_state or n_target_trend_confirmation_enabled and strategy_day)
                    and market_calculation is not None and market_calculation._checkpoints is not None):
                current_lifecycle = market_calculation._checkpoints._at(i)
            if needs_state:
                if live_lifecycle is not None:
                    lifecycle = live_lifecycle.copy()
                elif current_lifecycle is None:
                    raise ValueError('prepared lifecycle checkpoint is missing for this market date')
                else:
                    version, _saved_index, saved_rows = current_lifecycle
                    lifecycle = TrendLifecycle.from_checkpoint((version, i, saved_rows))
        # Source vertices determine the skeleton; market prices can separately
        # publish a previously unqualified trend on an otherwise unchanged day.
        current_signature = (
            epoch,
            tuple(
                (p["index"], p["ordinal"], p["kind"], p["value"], p["state"], p["available_at"])
                for p in raw
                if p["state"] != "developing"
            ),
        ) if strategy_day else None
        market_refresh = strategy_day and current_signature == signature and i > 0 and _has_confirmation_price_cross(
            [*(point for path in closed for point in path["points"]), *raw],
            levels, bars[i - 1], bars[i], _bar_date(bars[i]), i,
            n_target_trend_confirmation_enabled=n_target_trend_confirmation_enabled)
        if not strategy_day:
            # 下一窗口重新消费当时证据；不能继续沿用已跳过日期的旧形态。
            signature = None
        if strategy_day and (current_signature != signature or market_refresh):
            signature = current_signature
            prefix = bars[: i + 1]
            # 三层几何已经完成；这里只追加本级真实行情许可证。保持原刷新
            # 次序和直接下级持证来源，既不重复判点，也不让 N／突破证书改写
            # 下一层来源。未触发旧 gate 的日期仍沿用此前已消费的 levels。
            # prepared 为本请求只读借用的版本；许可创建独立顶层字段，不修改其证明。
            shared = (market_calculation._shared_permissions_at(i,
                n_enabled=n_target_trend_confirmation_enabled)
                if live_owner is not None and market_calculation is not None else None)
            if shared is not None:
                first, second, third = shared
            else:
                if _prepared_at is not None:
                    prepared = _prepared_at()
                permission_lifecycle = lifecycle
                if n_target_trend_confirmation_enabled and cached_lifecycle is not None:
                    if live_lifecycle is not None:
                        permission_lifecycle = live_lifecycle
                    elif current_lifecycle is None:
                        raise ValueError('prepared lifecycle checkpoint is missing for this market date')
                    else:
                        version, _saved_index, saved_rows = current_lifecycle
                        permission_lifecycle = TrendLifecycle.from_checkpoint((version, i, saved_rows))
                first = attach_trend_permissions(prepared[0], prefix,
                    n_target_trend_confirmation_enabled=n_target_trend_confirmation_enabled,
                    _permission_cache=permission_cache, _owned_candidates=True,
                    _n_confirmation_paths=permission_lifecycle.pending_bull_paths(1))
                second = attach_trend_permissions(prepared[1], prefix,
                    n_target_trend_confirmation_enabled=n_target_trend_confirmation_enabled,
                    qualified_source_points=(high_low_points_from_strokes(first['qualified_strokes'])
                        if n_target_trend_confirmation_enabled else None),
                    _permission_cache=permission_cache, _owned_candidates=True,
                    _n_confirmation_paths=permission_lifecycle.pending_bull_paths(2))
                third = attach_trend_permissions(prepared[2], prefix,
                    n_target_trend_confirmation_enabled=n_target_trend_confirmation_enabled,
                    qualified_source_points=(high_low_points_from_strokes(second['qualified_strokes'])
                        if n_target_trend_confirmation_enabled else None),
                    _permission_cache=permission_cache, _owned_candidates=True,
                    _n_confirmation_paths=permission_lifecycle.pending_bull_paths(3))
            first, second, third = (_strategy_permission_view(level) for level in (first, second, third))
            levels = ((second, first), (third, second))
            if shallow_candidate_sink is not None:
                from .shallow_base_breakout import shallow_candidate_from_geometry
                shallow_candidate = shallow_candidate_from_geometry(levels, dates, i)
            # The source-confirmed A high is already tradable evidence before
            # it is promoted to a formal vertex of its own level.
            candidates = [dict(index=p['index'], value=p['value'], source='formal')
                          for stroke in _permission_records(second['strokes'], 'strokes')
                          for p in _permission_records(stroke['points'], 'stroke points')
                          if p['kind'] == 'H' and p.get('state') != 'developing']
            candidates += _confirmed_descent_pressures(second, dates, i)
            candidates += [dict(index=a['high']['index'], value=a['high']['value'], source='confirmed_source')
                           for a in squeeze_anchors(second, dates, first) if a['known_index'] <= i]
            key = max(candidates, key=lambda p: (p['index'], p['value']), default=None)
            resistance_identity = (key['index'], key['value']) if key else None
            if resistance_identity != resistance_key:
                events.append(dict(bar_index=i, event='hierarchy_resistance_key', trend_level=2, key=key))
                resistance_key = resistance_identity
            landmarks = [item for level in (first, second, third)
                         for item in _permission_records(level["bear_bull_alternation_lows"], 'alternation lows')]
            invalidated = set()
            for level in (first, second, third):
                paths = {cast(str, stroke["id"]): _permission_records(stroke["points"], 'stroke points')
                         for stroke in _permission_records(level["strokes"], 'strokes')}
                for flip in _confirmed_bear_flip_events(paths, trend_level=cast(int, level['trend_level']),
                                                       dates=dates, asof_index=i):
                    bear_identity = (flip['trend_level'], flip['source_path'], flip['pivot_index'],
                                     flip['pivot_ordinal'], flip['available_at'])
                    if bear_identity not in seen_bear_flips:
                        seen_bear_flips.add(bear_identity)
                        events.append(flip)
                for low in _permission_records(level["bear_bull_alternation_lows"], 'alternation lows'):
                    if any(
                        (cast(int, p["index"]), cast(int, p["ordinal"])) >
                        (cast(int, low["index"]), cast(int, low.get("ordinal", 0)))
                        and any(event["title"] == "翻多为空"
                                for event in _permission_records(p.get("observations", []), 'point observations'))
                        for p in paths[cast(str, low["source_path"])]
                    ):
                        invalidated.add(low["id"])
        events.extend(dict(event) for event in lifecycle_events)
        if strategy_day and i in attacks:
            anchors_at_attack[i] = [anchor for level, source in levels for anchor in squeeze_anchors(level, dates, source)]
        if strategy_day and shallow_candidate_sink is not None:
            shallow_candidate_sink[i] = shallow_candidate
        # 当前股票共享已恢复的趋势，避免逐日跨进程传输完整证明树。
        if strategy_day and combined_candidate_sink is not None and levels:
            from .combined_a_entry import GeometryLevel, combined_a_contexts_from_geometry
            drawing = frame.drawing
            second, first = levels[0]
            third, _ = levels[1]
            if isinstance(combined_replay, CombinedAReplay):
                combined_replay.remember(i, (drawing, first, second, third))
            combined_candidate_sink[i] = combined_a_contexts_from_geometry(
                bars, cast(GeometryLevel, drawing), cast(GeometryLevel, first),
                cast(GeometryLevel, second), cast(GeometryLevel, third), dates, i, audit,
                _market_index=market_index)
        if strategy_day and secondary_pullback_sink is not None and levels:
            from .secondary_pullback_entry import secondary_pullback_candidates
            drawing = frame.drawing
            secondary_pullback_sink[i] = secondary_pullback_candidates(
                bars, drawing, levels[0][0], dates, i, first=levels[0][1], _market_index=market_index)
        current = {}
        for low in landmarks if strategy_day else ():
            high = low["confirmed_flip_high"]
            origin = low["confirmed_bear_low"]
            key = low["broken_key"]
            identity = (
                low["trend_level"],
                origin["index"],
                high["index"],
                low["index"],
                key["index"],
                high["available_at"],
                low["available_at"],
            )
            if identity in retired or low["id"] in invalidated:
                if identity in active:
                    events.append(
                        dict(bar_index=i, event="hierarchy_context_invalidated", trend_level=low["trend_level"])
                    )
                retired.add(identity)
                continue
            known = first_seen.setdefault(identity, max(i, dates[low["available_at"]]))
            ctx = EntryContext(
                low["trend_level"],
                origin["index"],
                origin["index"],
                key["index"],
                key["value"],
                dates[high["available_at"]],
                high["index"],
                high["value"],
                origin["index"],
                origin["value"],
                known,
                low["index"],
                low["value"],
            )
            origin_broken = (market_index.first_extreme(origin["index"], i + 1, origin["value"], high=False) is not None
                             if market_index is not None and market_index.supports_price(origin["value"])
                             and 0 <= origin["index"] <= i else
                             min(bar.low for bar in bars[origin["index"] : i + 1]) < origin["value"])
            if origin_broken:
                retired.add(identity)
                if identity in active:
                    events.append(dict(bar_index=i, event="hierarchy_context_invalidated", trend_level=ctx.trend_level))
                continue
            if identity in active:
                ctx = replace(ctx, maturity_index=active[identity].maturity_index)
            else:
                events.append(dict(bar_index=i, event="hierarchy_alternation_ready", **asdict(ctx)))
            if ctx.maturity_index is None and i > known and bars[i].high > ctx.flip_high_price:
                ctx = replace(ctx, maturity_index=i)
                events.append(dict(bar_index=i, event="hierarchy_bull_matured", **asdict(ctx)))
            current[identity] = ctx
        # A disappeared or revised chain cannot later revive an old N attack.
        if strategy_day:
            retired.update(set(active) - set(current))
            active = current
        history[i] = tuple(current.values())
        if checkpoint_channel is not None:
            checkpoint_channel.day(0, i, (
                history[i], events[event_start:], tuple(
                    (sink is not None and i in sink, sink.get(i) if sink is not None else None)
                    for sink in (shallow_candidate_sink, combined_candidate_sink, secondary_pullback_sink)),
            ))
            if i in checkpoint_channel.store.targets:
                certificate = _past_attack_digest(audit, i)
                if certificate is not None:
                    checkpoint_channel.state(0, i, (
                        active, first_seen, retired, closed, previous_epoch, previous_raw,
                        signature, landmarks, invalidated, levels, shallow_candidate,
                        resistance_key, anchors_at_attack, lifecycle.checkpoint(),
                    ), certificate)
        if (prefix_cache is not None and past_attack_certificate is not None
                and resume_start != last and i == last - 1):
            prefix_cache['_attack_rows'] = attack_certificates(audit, last - 1)
            prefix_cache["key"] = prefix_key
            prefix_cache['_attack_key'] = prefix_key
            prefix_cache['_live_owner'] = live_owner
            prefix_cache['_entry_days'] = tuple(entry_days) if entry_days is not None else None
            prefix_cache["checkpoint"] = _copy_replay_state((
                history, events, active, first_seen, retired, closed, previous_epoch,
                previous_raw, signature, landmarks, invalidated, levels,
                shallow_candidate, resistance_key, anchors_at_attack, lifecycle,
                shallow_candidate_sink if shallow_candidate_sink is not None else {},
                combined_candidate_sink if combined_candidate_sink is not None else {},
                secondary_pullback_sink if secondary_pullback_sink is not None else {},
            ))

    def bind_market_calculation(current: MarketCalculations) -> None:
        nonlocal market_calculation
        market_calculation = current
        current.confirmation_policy = confirmation_policy

    if _minute_analysis is not None:
        if (len(_minute_analysis.bars) != len(bars)
                or _minute_analysis.bars[:-1] != bars[:-1]
                or bars and replace(bars[-1], high=_minute_analysis.bars[-1].high,
                    low=_minute_analysis.bars[-1].low, close=_minute_analysis.bars[-1].close,
                    volume=_minute_analysis.bars[-1].volume) != _minute_analysis.bars[-1]
                or _minute_analysis.calculations is None
                or _minute_analysis.calculations.confirmation_policy != confirmation_policy):
            raise ValueError('minute history requires identical completed bars and confirmation policy')
        if resume_start < last:
            # 日线只借出昨天以前的逐日版本；今天的完整 K 线和六态不进入分钟核验。
            market_calculation = _minute_analysis.calculations
            cached_lifecycle = {}
            for number in (1, 2, 3):
                for event in market_calculation._lifecycle_events_since(number, resume_start):
                    index = cast(int, event['bar_index'])
                    if index < last:
                        cached_lifecycle.setdefault(index, []).append(event)
            for daily_events in cached_lifecycle.values():
                daily_events.sort(key=lambda event: event.get('reason') == 'source_path_ended')
            with ExitStack() as prior_readers:
                prior_frames = _minute_analysis.frames.iter_rows(shared=True, start_index=resume_start)
                prior_readers.callback(cast(Generator[_ChartDrawingFrame, None, None], prior_frames).close)
                cursors = [prior_readers.enter_context(history._readonly_cursor())
                           for history in _minute_analysis.levels]
                for frame in prior_frames:
                    if frame.index >= last:
                        break
                    accept(frame, (), _prepared_at=lambda: tuple(cursor.at(frame.index) for cursor in cursors))
            resume_start = last
            cached_lifecycle = None
            market_calculation = None

    # 交易资格只消费联动流已保存的结果；自身分钟续接仍保存完整因果状态。
    frame_source = (nullcontext((_prepared_history.frames, _prepared_history.levels))
        if _prepared_history is not None else _staged_chart_frames(bars, resume_start=resume_start, closed=closed,
            previous_epoch=previous_epoch, previous_raw=previous_raw,
            _on_ready=bind_market_calculation, _stage=_stage))
    with frame_source as (frames, prepared), ExitStack() as readers:
        if market_calculation is not None and market_calculation._checkpoints is not None:
            cached_lifecycle = {}
            for number in (1, 2, 3):
                for event in market_calculation._lifecycle_events_since(number, resume_start):
                    cached_lifecycle.setdefault(cast(int, event['bar_index']), []).append(event)
            for daily_events in cached_lifecycle.values():
                daily_events.sort(key=lambda event: event.get('reason') == 'source_path_ended')
            # 事件逐日重放；状态仅在保存检查点或计算 N 许可时按日期读取。
            # 区间外的历史状态无需逐版解码，当前/前日仍优先借用实时副本。
        if _stage is not None: _stage('按已确认资格调度有效区间内的策略形态')
        if steps is not None and cached_lifecycle is None:
            for step_number in LIFECYCLE_STEPS: steps.begin(step_number)
        drawing_reader = frames.iter_rows(shared=True, start_index=resume_start)
        readers.callback(cast(Generator[_ChartDrawingFrame, None, None], drawing_reader).close)
        if cached_lifecycle is not None and live_owner is not None:
            trend_cursors = [readers.enter_context(stage._readonly_cursor()) for stage in prepared]
            for frame in drawing_reader:
                strategy_day = entry_days is None or entry_days[frame.index]
                shared = (market_calculation._shared_permissions_at(frame.index,
                    n_enabled=n_target_trend_confirmation_enabled)
                    if live_owner is not None and market_calculation is not None else None)
                # 六态已有独立事件和状态；区间外及同日已发布资格均不读取几何。
                # 跨资格缺口只定位实际需要的历史前驱，不能逐个解码被跳过的版本。
                required = strategy_day and shared is None
                # 实际刷新门禁在 accept 内决定；不刷新日无需恢复未消费的几何。
                accept(frame, (), _prepared_at=(lambda: tuple(
                    cursor.at(frame.index - frames.start) for cursor in trend_cursors)) if required else None)
        else:
            trend_readers = [stage.iter_rows(shared=True, start_position=resume_start - frames.start,
                                            _reuse_shared=True) for stage in prepared]
            for reader in trend_readers:
                readers.callback(cast(Generator[dict[str, object], None, None], reader).close)
            for frame, first, second, third in zip(drawing_reader, *trend_readers):
                accept(frame, (first, second, third))
        if steps is not None and cached_lifecycle is None:
            for step_number in LIFECYCLE_STEPS: steps.complete(step_number)
    if _history_reuse is not None:
        _history_reuse.capture_chart_base(bars,
            n_target_trend_confirmation_enabled=n_target_trend_confirmation_enabled,
            audit=audit, history=history, events=events, anchors_at_attack=anchors_at_attack)
    if audit:
        additional = squeeze_alternations(bars, audit, anchors_at_attack)
        history = merge_squeeze_history(bars, history, additional)
        events.extend(additional)
    return history, events


def merge_squeeze_history(bars, history, events):
    """Expose confirmed alternations to future N attacks, never to their own N."""
    from collections import defaultdict
    dated = defaultdict(list)
    for event in events:
        dated[event["bar_index"]].append(event)
    active: dict[tuple[int, int, int, int], EntryContext] = {}
    claimed, merged = set(), {}
    for i, bar in enumerate(bars):
        for event in dated[i]:
            identity = (event["trend_level"], event["a_origin_index"], event["a_high_index"], event["b_low_index"])
            if event["event"] == "squeeze_alternation_confirmed":
                claimed.add(identity)
                active = {key: ctx for key, ctx in active.items()
                          if key[:2] != identity[:2] or key[2] > identity[2]}
                active[identity] = EntryContext(
                    trend_level=event["trend_level"], epoch=event["a_origin_index"],
                    context_index=event["a_origin_index"], key_source_index=event["key_source_index"],
                    key_price=event["key_price"], flip_index=event["anchor_known_index"],
                    flip_high_index=event["a_high_index"], flip_high_price=event["a_high_price"],
                    origin_index=event["a_origin_index"], origin_price=event["a_origin_price"],
                    alternation_index=i, alternation_low_index=event["b_low_index"],
                    alternation_low_price=event["b_low_price"], confirmation_attack=event["attack"],
                )
            elif event["event"] == "squeeze_alternation_invalidated":
                active.pop(identity, None)
            elif event["event"] == "squeeze_alternation_breakout" and identity in active:
                active[identity] = replace(active[identity], maturity_index=i)
        active = {key: ctx for key, ctx in active.items() if bar.low >= ctx.origin_price}
        ordinary = [ctx for ctx in history[i] if
                    (ctx.trend_level, ctx.origin_index, ctx.flip_high_index, ctx.alternation_low_index) not in claimed]
        merged[i] = tuple([*ordinary, *active.values()])
    return merged
