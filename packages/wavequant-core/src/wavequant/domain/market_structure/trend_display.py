"""Publish observation metadata without changing shared structural geometry.

Candidate geometry remains the input to recursive structure and lifecycle
calculation. This projection consumes their existing causal events, never
trading eligibility, and is shared by running previews and completed charts.
"""

from bisect import bisect_left, bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast


TREND_DISPLAY_POLICY = 'shared_structure_observation_only_v4'


@dataclass(slots=True)
class _BullWindow:
    start: int
    end: int
    breakout: int


def _rows(value: object) -> list[Mapping[str, object]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [cast(Mapping[str, object], row) for row in value if isinstance(row, Mapping)]


def _bull_windows(audit: Sequence[Mapping[str, object]], level: int,
                  asof_index: int, row: Mapping[str, object] | None = None) -> dict[str, list[_BullWindow]]:
    windows: dict[str, list[_BullWindow]] = {}
    active: dict[str, _BullWindow] = {}
    unbroken: set[tuple[str, int, int]] = set()
    for stroke in (_rows(row.get('strokes')) if row is not None else ()):
        path = str(stroke.get('id', ''))
        for point in _rows(stroke.get('points')):
            context = point.get('lifecycle_context')
            key = context.get('key') if isinstance(context, Mapping) and context.get('direction') == 'up' else None
            if (point.get('kind') == 'H' and type(point.get('index')) is int
                    and isinstance(key, Mapping) and key.get('kind') == 'H'
                    and isinstance(point.get('value'), (int, float)) and isinstance(key.get('value'), (int, float))
                    and cast(float, point['value']) <= cast(float, key['value'])):
                unbroken.add((path, cast(int, point['index']), cast(int, point.get('ordinal', 0))))
    events = [event for event in audit
              if event.get('trend_level') == level
              and event.get('event') in ('trend_lifecycle_state', 'trend_lifecycle_anchor',
                                         'trend_lifecycle_invalidated')
              and isinstance(event.get('source_path'), str)
              and type(event.get('bar_index')) is int
              and 0 <= cast(int, event['bar_index']) <= asof_index]
    for event in sorted(events, key=lambda row: cast(int, row['bar_index'])):
        now = cast(int, event['bar_index'])
        available = event.get('available_index', now)
        if type(available) is not int or cast(int, available) > now:
            continue
        path = cast(str, event['source_path'])
        previous = active.get(path)
        if event['event'] == 'trend_lifecycle_invalidated':
            if previous is not None:
                if event.get('reason') in ('source_evidence_revised', 'source_path_ended'):
                    windows[path] = [span for span in windows[path] if span is not previous]
                else:
                    previous.end = now
                active.pop(path)
            continue
        if event.get('direction') == 'down' and event.get('flip_confirmed') is True:
            if previous is not None:
                origin = event.get('origin_index')
                previous.end = (max(previous.start, cast(int, origin))
                                if type(origin) is int and 0 <= cast(int, origin) <= now else now)
                active.pop(path)
            continue
        if event.get('direction') != 'up' or event.get('flip_confirmed') is not True:
            continue
        special = event.get('special_bull_recovery') is True
        # 冻结末跌高优先于局部抬高的旧标记；特殊恢复沿自己的既有翻多证明。
        if not special and (path, event.get('flip_index'), event.get('flip_ordinal') or 0) in unbroken:
            continue
        # 只消费现有生命周期已经确认的普通/特殊翻多。特殊分支的真实旧
        # 翻多、破低、连续创新低及恢复条件由原状态机负责，不重写第二套。
        # 交替及交易资格不能倒推一个此前不存在的翻多。
        confirmed = (event.get('source_event') in ('短暂跌破后同级再次空翻多', '特殊空翻多同腿高点延伸')
                     if special else event.get('source_event') == '翻空为多')
        if not confirmed:
            continue
        origin, high = event.get('origin_index'), event.get('flip_index')
        known = event.get('flip_known_index', now)
        if (type(origin) is not int or type(high) is not int or type(known) is not int
                or not 0 <= cast(int, origin) < cast(int, high) <= cast(int, known) <= now):
            continue
        if previous is None:
            current = _BullWindow(cast(int, origin), asof_index, cast(int, high))
            windows.setdefault(path, []).append(current)
            active[path] = current
    # 合并已成立周期的覆盖范围，逐腿二分查询；不在发布时重放逐日行情。
    for path, spans in windows.items():
        merged: list[_BullWindow] = []
        for span in sorted(spans, key=lambda item: item.start):
            if merged and span.start <= merged[-1].end:
                merged[-1].end = max(merged[-1].end, span.end)
                merged[-1].breakout = min(merged[-1].breakout, span.breakout)
            else:
                merged.append(_BullWindow(span.start, span.end, span.breakout))
        windows[path] = merged
    return windows


def _point_id(point: Mapping[str, object]) -> tuple[object, ...]:
    return point.get('index'), point.get('ordinal', 0), point.get('kind'), point.get('value')


def _unbroken_key(point: Mapping[str, object]) -> Mapping[str, object] | None:
    context = point.get('lifecycle_context')
    key = context.get('key') if isinstance(context, Mapping) and context.get('direction') == 'up' else None
    if not isinstance(key, Mapping) or key.get('kind') != 'H' or point.get('kind') != 'H':
        return None
    price, boundary = point.get('value'), key.get('value')
    index, key_index = point.get('index'), key.get('index')
    if (type(price) not in (int, float) or type(boundary) not in (int, float)
            or type(index) is not int or type(key_index) is not int
            or not 0 <= cast(int, key_index) < cast(int, index)
            or not 0 < cast(float, price) <= cast(float, boundary)):
        return None
    known, key_known = point.get('available_at'), key.get('available_at')
    if isinstance(known, str) and isinstance(key_known, str) and key_known > known:
        return None
    return cast(Mapping[str, object], key)


def publish_trend_display(row: Mapping[str, object], audit: Sequence[Mapping[str, object]],
                          *, level: int, asof_index: int,
                          source_row: Mapping[str, object] | None = None) -> dict[str, object]:
    """追加观察元数据；正式点、发展尾端和已有腿的证据原样来自共用核心。

    冻结关键位约束在共同整波升级状态中处理。六态确认、特殊恢复和交易
    资格只影响观察提示，不能在图表发布时生成另一份结构或重新锚定端点。
    """
    windows = _bull_windows(audit, level, asof_index, row)
    starts = {path: [span.start for span in spans] for path, spans in windows.items()}
    source_windows = _bull_windows(audit, level - 1, asof_index, source_row) if source_row is not None else {}
    source_ends = {path: [span.end for span in spans] for path, spans in source_windows.items()}
    source_points = {str(stroke.get('id', '')): {_point_id(point): point for point in _rows(stroke.get('points'))}
                     for stroke in _rows(source_row.get('strokes'))} if source_row is not None else {}
    strokes, tails = _rows(row.get('strokes')), _rows(row.get('developing_strokes'))
    tail_owners: dict[tuple[object, ...], str] = {}
    for stroke in strokes:
        points = _rows(stroke.get('points'))
        if points:
            tail_owners[(stroke.get('source_path'), *_point_id(points[-1]))] = str(stroke.get('id', ''))
    observations: list[dict[str, object]] = []
    for stroke in (*strokes, *tails):
        points = _rows(stroke.get('points'))
        if len(points) < 2:
            continue
        path = str(stroke.get('id', ''))
        if stroke.get('display_only') is True:
            path = tail_owners.get((stroke.get('source_path'), *_point_id(points[0])), path)
        source_path = str(stroke.get('source_path', ''))
        spans, lower_spans = windows.get(path, []), source_windows.get(source_path, [])
        source = source_points.get(source_path, {})
        for left, right in zip(points, points[1:]):
            if left.get('kind') != 'L' or right.get('kind') != 'H':
                continue
            start, end = left.get('index'), right.get('index')
            if type(start) is not int or type(end) is not int or cast(int, end) > asof_index:
                continue
            position = bisect_right(starts.get(path, []), cast(int, start)) - 1
            own_confirmed = (position >= 0 and spans[position].breakout <= cast(int, end) <= spans[position].end)
            lower_position = bisect_left(source_ends.get(source_path, []), cast(int, start))
            lower_confirmed = (lower_position < len(lower_spans)
                and lower_spans[lower_position].start <= cast(int, end)
                and lower_spans[lower_position].breakout <= cast(int, end))
            own_key = _unbroken_key(right)
            lower_key = _unbroken_key(source.get(_point_id(right), {}))
            source_doubt = lower_key is not None and not lower_confirmed
            own_doubt = own_key is not None and not own_confirmed
            if not own_confirmed or own_doubt or source_doubt:
                observations.append(dict(id=f"{stroke.get('id', path)}:bull-doubt:{start}",
                    source_path=path, trend_level=level, state='多头疑虑', confirmed=False,
                    reason='source_level_bull_flip_unconfirmed' if source_doubt else 'own_level_bull_flip_unconfirmed',
                    source_level=level - 1, source_key=dict(lower_key) if lower_key is not None else None,
                    points=[dict(left), dict(right)], available_at=right.get('available_at'), display_only=True))
    return dict(row, strokes=[dict(stroke) for stroke in strokes],
                developing_strokes=[dict(tail) for tail in tails], observation_strokes=observations,
                lifecycle_display_policy=TREND_DISPLAY_POLICY)
