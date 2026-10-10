"""Replay entry contexts through the same causal hierarchy used by charts."""

from dataclasses import asdict, dataclass, replace
from collections.abc import Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from math import isfinite
from typing import cast

from ..market_structure.lecture_drawing import lecture_drawing
from ..market_structure.secondary_trend import _bar_date
from ..market_structure.trend_lines import (
    attach_trend_permissions,
    high_low_points_from_strokes,
    polyline_high_low_points,
    prepare_trend_lines,
)
from ..market_structure.trend_staging import TrendStageRows
from ..models.model import Bar
from .hierarchical_entry import EntryContext


_CONFIRMATION_CACHE_VERSION = "staged_point_geometry_independent_strategy_permissions_v114"


@dataclass(slots=True)
class _ChartDrawingFrame:
    """保存某个前缀自己的折线，而非从最终折线反向裁切历史。

    raw 独立拥有当日仍可修订的端点；drawing 的已关闭路径只共享已经冻结
    的点数组。三个 prepared 行各自保留直接下级来源和完整事件账本，供
    最后的资格刷新使用，不额外保存三份带行情证书的全历史深副本。
    """

    index: int
    epoch: int
    raw: list[dict[str, object]]
    drawing: dict[str, object]


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
def _staged_chart_frames(
    bars: Sequence[Bar], *, resume_start: int, closed: Sequence[Mapping[str, object]],
    previous_epoch: int | None, previous_raw: Sequence[Mapping[str, object]],
) -> Iterator[tuple[TrendStageRows[_ChartDrawingFrame], tuple[TrendStageRows[dict[str, object]], ...]]]:
    """先全部折线，再全部一级、二级、三级；历史证据只在临时行中持有。

    每一级读取同一日期上一级的纯几何，不能用最终点事后裁切历史。更高／
    更低的同类点会替换旧坐标；保存当时输入才能保留真正的首次可知日。
    五个固定阶段流各有 2 MiB 内存上限，超出后自动暂存磁盘；原始来源在
    一级完成后即关闭。消费只恢复当前日期的三个 prepared 行，不保留全
    日期三层完整证据树。所有流由 ExitStack 管理，异常同样关闭临时文件。
    """
    with ExitStack() as resources:
        frames = resources.enter_context(TrendStageRows(_stage_frame))
        sources = resources.enter_context(TrendStageRows(_stage_source))
        drawing_closed = [dict(path) for path in closed]
        drawing_epoch = previous_epoch
        drawing_raw = deepcopy([dict(point) for point in previous_raw])

        def collect(i: int, epoch: int, raw: Sequence[Mapping[str, object]]) -> None:
            nonlocal drawing_epoch, drawing_raw
            if i < resume_start:
                return
            if drawing_epoch is not None and epoch != drawing_epoch and len(drawing_raw) > 1:
                drawing_closed.append(_closed_drawing_path(drawing_epoch, drawing_raw, epoch))
            # 只复制当前仍可变 raw；闭合路径共享已冻结的点数组。序列化保存
            # 当日快照后不会受绘图器接下来修订的端点或未来路径影响。
            drawing_raw = deepcopy([dict(point) for point in _permission_records(raw, 'drawing points')])
            drawing_epoch = epoch
            drawing: dict[str, object] = dict(strokes=[*drawing_closed,
                *([dict(id=f"lecture-{epoch}", points=drawing_raw)] if len(drawing_raw) > 1 else [])])
            frames.write(_ChartDrawingFrame(i, epoch, drawing_raw, drawing))
            sources.write(polyline_high_low_points(drawing))

        lecture_drawing(bars, on_step=collect)
        prepared: list[TrendStageRows[dict[str, object]]] = []
        symbol = bars[0].symbol if bars else 'STRUCTURE'
        for trend_level in (1, 2, 3):
            current = resources.enter_context(TrendStageRows(_stage_prepared))
            if trend_level == 1:
                for source in sources.iter_rows():
                    current.write(prepare_trend_lines(source, trend_level=trend_level, symbol=symbol))
                sources.close()
            else:
                for previous in prepared[-1].iter_rows():
                    source = high_low_points_from_strokes(previous['strokes'])
                    current.write(prepare_trend_lines(source, trend_level=trend_level, symbol=symbol))
            if current.row_count != frames.row_count:
                raise ValueError('all dates must complete each trend stage before the next stage')
            prepared.append(current)
        yield frames, tuple(prepared)


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
        if not isinstance(vertices, (list, tuple)):
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
                    if isinstance(vertices, (list, tuple)):
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
        shallow_candidate, resistance_key, anchors_at_attack, prior_shallow, prior_combined, prior_pullback,
    ) = state
    return (
        history.copy(), events.copy(), active.copy(), first_seen.copy(),
        retired.copy(), closed.copy(), previous_epoch, deepcopy(previous_raw),
        signature, landmarks.copy(), invalidated.copy(), levels,
        shallow_candidate, resistance_key, anchors_at_attack.copy(),
        prior_shallow.copy(), prior_combined.copy(), prior_pullback.copy(),
    )


def chart_entry_history(
    bars: Sequence[Bar], *, audit=(), shallow_candidate_sink=None, combined_candidate_sink=None,
    secondary_pullback_sink=None, prefix_cache=None, n_target_trend_confirmation_enabled: bool = False,
) -> tuple[dict[int, tuple[EntryContext, ...]], list[dict[str, object]]]:
    """按股票完整阶段计算几何，再沿原日期刷新条件消费独立交易资格。

    折线、一级、二级、三级各阶段完成全部日期后才进入下一阶段。历史行
    仍来自自己当时的输入前缀，阶段顺序不授予未来证据。最后的消费保留
    signature／行情越位刷新、首次观察日、失效和缓存逻辑；来源已确认的
    回调可作入场证据，但不能借资格附件制造正式上级高低点。
    """
    if type(n_target_trend_confirmation_enabled) is not bool:
        raise ValueError('n_target_trend_confirmation_enabled must be a boolean')
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
    levels: tuple[tuple[dict, dict], ...] = ()
    shallow_candidate = None
    resistance_key = None
    last = len(bars) - 1
    # Intraday replays change only the unfinished last candle. All earlier
    # drawing steps (and their expensive hierarchy reductions) are identical.
    # Include the earlier attack dates because they control stored anchors.
    prefix_key = (
        tuple(bars[:-1]),
        frozenset(i for i in attacks if i < last),
        shallow_candidate_sink is not None,
        combined_candidate_sink is not None,
        secondary_pullback_sink is not None,
        _CONFIRMATION_CACHE_VERSION,
        n_target_trend_confirmation_enabled,
    )
    cached_key = prefix_cache.get("key") if prefix_cache is not None else None
    if cached_key == prefix_key:
        resume_start = last
    elif (
        cached_key is not None
        and len(cached_key) == len(prefix_key)
        and cached_key[0] == tuple(bars[:-2])
        and cached_key[1] == frozenset(i for i in attacks if i < last - 1)
        and cached_key[2] == (shallow_candidate_sink is not None)
        and cached_key[3] == (combined_candidate_sink is not None)
        and cached_key[4] == (secondary_pullback_sink is not None)
        and cached_key[5] == _CONFIRMATION_CACHE_VERSION
        and cached_key[6] == n_target_trend_confirmation_enabled
    ):
        # Yesterday was unfinished when the prior checkpoint was saved. Replay
        # its final candle, then today's partial candle, from the older state.
        resume_start = last - 1
    else:
        resume_start = 0
    if resume_start:
        (
            history, events, active, first_seen, retired, closed, previous_epoch,
            previous_raw, signature, landmarks, invalidated, levels,
            shallow_candidate, resistance_key, anchors_at_attack, prior_shallow, prior_combined, prior_pullback,
        ) = _copy_replay_state(prefix_cache["checkpoint"])
        if shallow_candidate_sink is not None:
            shallow_candidate_sink.update(prior_shallow)
        if combined_candidate_sink is not None:
            combined_candidate_sink.update(prior_combined)
        if secondary_pullback_sink is not None:
            secondary_pullback_sink.update(prior_pullback)

    def accept(frame: _ChartDrawingFrame, prepared: Sequence[Mapping[str, object]]) -> None:
        nonlocal previous_epoch, previous_raw, signature, landmarks, active, invalidated, levels, resistance_key, shallow_candidate
        i, epoch, raw = frame.index, frame.epoch, frame.raw
        if previous_epoch is not None and epoch != previous_epoch and len(previous_raw) > 1:
            closed.append(_closed_drawing_path(previous_epoch, previous_raw, epoch))
        previous_epoch, previous_raw = epoch, raw
        # Source vertices determine the skeleton; market prices can separately
        # publish a previously unqualified trend on an otherwise unchanged day.
        current_signature = (
            epoch,
            tuple(
                (p["index"], p["ordinal"], p["kind"], p["value"], p["state"], p["available_at"])
                for p in raw
                if p["state"] != "developing"
            ),
        )
        market_refresh = current_signature == signature and i > 0 and _has_confirmation_price_cross(
            [*(point for path in closed for point in path["points"]), *raw],
            levels, bars[i - 1], bars[i], _bar_date(bars[i]), i,
            n_target_trend_confirmation_enabled=n_target_trend_confirmation_enabled)
        if current_signature != signature or market_refresh:
            signature = current_signature
            prefix = bars[: i + 1]
            # 三层几何已经完成；这里只追加本级真实行情许可证。保持原刷新
            # 次序和直接下级持证来源，既不重复判点，也不让 N／突破证书改写
            # 下一层来源。未触发旧 gate 的日期仍沿用此前已消费的 levels。
            first = attach_trend_permissions(prepared[0], prefix,
                n_target_trend_confirmation_enabled=n_target_trend_confirmation_enabled)
            second = attach_trend_permissions(prepared[1], prefix,
                n_target_trend_confirmation_enabled=n_target_trend_confirmation_enabled,
                qualified_source_points=high_low_points_from_strokes(first['qualified_strokes']))
            third = attach_trend_permissions(prepared[2], prefix,
                n_target_trend_confirmation_enabled=n_target_trend_confirmation_enabled,
                qualified_source_points=high_low_points_from_strokes(second['qualified_strokes']))
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
                for low in _permission_records(level["bear_bull_alternation_lows"], 'alternation lows'):
                    if any(
                        (cast(int, p["index"]), cast(int, p["ordinal"])) >
                        (cast(int, low["index"]), cast(int, low.get("ordinal", 0)))
                        and any(event["title"] == "翻多为空"
                                for event in _permission_records(p.get("observations", []), 'point observations'))
                        for p in paths[cast(str, low["source_path"])]
                    ):
                        invalidated.add(low["id"])
        if i in attacks:
            anchors_at_attack[i] = [anchor for level, source in levels for anchor in squeeze_anchors(level, dates, source)]
        if shallow_candidate_sink is not None:
            shallow_candidate_sink[i] = shallow_candidate
        if combined_candidate_sink is not None and levels:
            from .combined_a_entry import GeometryLevel, combined_a_contexts_from_geometry
            drawing = frame.drawing
            second, first = levels[0]
            third, _ = levels[1]
            combined_candidate_sink[i] = combined_a_contexts_from_geometry(
                bars, cast(GeometryLevel, drawing), cast(GeometryLevel, first),
                cast(GeometryLevel, second), cast(GeometryLevel, third), dates, i, audit)
        if secondary_pullback_sink is not None and levels:
            from .secondary_pullback_entry import secondary_pullback_candidates
            drawing = frame.drawing
            secondary_pullback_sink[i] = secondary_pullback_candidates(
                bars, drawing, levels[0][0], dates, i, first=levels[0][1])
        current = {}
        for low in landmarks:
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
            if min(bar.low for bar in bars[origin["index"] : i + 1]) < origin["value"]:
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
        retired.update(set(active) - set(current))
        active = current
        history[i] = tuple(current.values())
        if prefix_cache is not None and resume_start != last and i == last - 1:
            prefix_cache["key"] = prefix_key
            prefix_cache["checkpoint"] = _copy_replay_state((
                history, events, active, first_seen, retired, closed, previous_epoch,
                previous_raw, signature, landmarks, invalidated, levels,
                shallow_candidate, resistance_key, anchors_at_attack,
                dict(shallow_candidate_sink or {}),
                dict(combined_candidate_sink or {}),
                dict(secondary_pullback_sink or {}),
            ))

    # 几何阶段先完整结束，旧资格消费状态才开始逐日推进。临时文件记录
    # 当前请求自己生成的可信对象，关闭时不进入 checkpoint 或公开结果。
    with _staged_chart_frames(bars, resume_start=resume_start, closed=closed,
            previous_epoch=previous_epoch, previous_raw=previous_raw) as (frames, prepared):
        for frame, first, second, third in zip(frames.iter_rows(), *(stage.iter_rows() for stage in prepared)):
            accept(frame, (first, second, third))
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
