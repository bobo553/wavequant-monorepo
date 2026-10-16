"""Publish continuous structure without promoting proven local bullish doubts.

Candidate geometry remains the input to recursive structure and lifecycle
calculation. This projection consumes their existing causal events, never
trading eligibility, and is shared by running previews and completed charts.
"""

from bisect import bisect_left, bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast


TREND_DISPLAY_POLICY = 'continuous_paths_frozen_key_observation_v3'


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


def _replace_path(stroke: Mapping[str, object], points: Sequence[Mapping[str, object]],
                  original: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """Retain path identity and only the evidence for edges that still exist."""
    prior = _rows(stroke.get('points'))
    if len(prior) == len(points) and all(left is right for left, right in zip(prior, points)):
        return dict(stroke)
    identities = [_point_id(point) for point in points]
    positions = {identity: position for position, identity in enumerate(identities)}
    original_positions = {_point_id(point): position for position, point in enumerate(original)}

    def retained(leg: Mapping[str, object]) -> bool:
        leg_ids = [_point_id(point) for point in _rows(leg.get('points'))]
        position = positions.get(leg_ids[0]) if leg_ids else None
        return (len(leg_ids) >= 2 and position is not None
                and identities[position:position + len(leg_ids)] == leg_ids)

    old_legs = {tuple(_point_id(point) for point in _rows(leg.get('points'))): leg
                for leg in _rows(stroke.get('structural_legs'))}
    legs: list[dict[str, object]] = []
    for left, right in zip(points, points[1:]):
        old = old_legs.get((_point_id(left), _point_id(right)))
        if old is not None:
            legs.append(dict(old))
            continue
        start, end = original_positions[_point_id(left)], original_positions[_point_id(right)]
        dates = [cast(str, point['available_at']) for point in original[start:end + 1]
                 if isinstance(point.get('available_at'), str)]
        legs.append(dict(points=[dict(left), dict(right)],
                         direction='up' if left.get('kind') == 'L' else 'down',
                         available_at=max(dates) if dates else right.get('available_at'),
                         basis=TREND_DISPLAY_POLICY, display_only=True))
    result = dict(stroke, points=list(points), structural_legs=legs,
                  confirmed_legs=[dict(leg) for leg in _rows(stroke.get('confirmed_legs')) if retained(leg)],
                  prevent_display_bridge=True, lifecycle_display_policy=TREND_DISPLAY_POLICY)
    if stroke.get('display_only') is True and points:
        result.update(confirmed_anchor=dict(points[0]),
                      wave_direction='up' if points[0].get('kind') == 'L' else 'down')
    return result


def publish_trend_display(row: Mapping[str, object], audit: Sequence[Mapping[str, object]],
                          *, level: int, asof_index: int,
                          source_row: Mapping[str, object] | None = None) -> dict[str, object]:
    """Keep structure independent of lifecycle windows and trading eligibility.

    Missing audit confirmation is observation metadata, not evidence to erase a
    structural leg. Only an explicit unbroken frozen key can reject a local
    third-level bullish promotion. Its existing valid flip history, including
    special recovery, takes precedence. Rejected counter-swings are folded into
    their original path's extreme; they never split a path into orphaned lines.
    """
    windows = _bull_windows(audit, level, asof_index, row)
    starts = {path: [span.start for span in spans] for path, spans in windows.items()}
    source_windows = _bull_windows(audit, level - 1, asof_index, source_row) if source_row is not None else {}
    source_ends = {path: [span.end for span in spans] for path, spans in source_windows.items()}
    source_points = {str(stroke.get('id', '')): {_point_id(point): point for point in _rows(stroke.get('points'))}
                     for stroke in _rows(source_row.get('strokes'))} if source_row is not None else {}
    observations: list[dict[str, object]] = []

    def project(stroke: Mapping[str, object], tail: Mapping[str, object] | None = None,
                *, owner: str | None = None) -> tuple[dict[str, object], dict[str, object] | None]:
        formal = _rows(stroke.get('points'))
        tail_points = _rows(tail.get('points')) if tail is not None else []
        points = formal + tail_points[1:]
        if len(points) < 2:
            return dict(stroke), dict(tail) if tail is not None else None
        # 六态事件 source_path 是本级 ID，几何 source_path 是直接下级 ID。
        path = owner if owner is not None else str(stroke.get('id', ''))
        source_path = str(stroke.get('source_path', ''))
        spans, lower_spans = windows.get(path, []), source_windows.get(source_path, [])
        source = source_points.get(source_path, {})
        rejected: set[tuple[object, ...]] = set()
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
            blocked_own = own_key is not None and not own_confirmed
            blocked_source = lower_key is not None and not lower_confirmed
            blocked = level == 3 and (blocked_own or blocked_source)
            if not own_confirmed or blocked:
                observations.append(dict(id=f"{stroke.get('id', path)}:bull-doubt:{start}",
                    source_path=path, trend_level=level, state='多头疑虑', confirmed=False,
                    reason='source_level_bull_flip_unconfirmed' if blocked_source else 'own_level_bull_flip_unconfirmed',
                    source_level=level - 1, source_key=dict(lower_key) if lower_key is not None else None,
                    points=[dict(left), dict(right)], available_at=right.get('available_at'), display_only=True))
            if blocked:
                rejected.add(_point_id(right))
        if not rejected:
            return dict(stroke), dict(tail) if tail is not None else None

        # 仅在同一原始路径内归并：未成立的反弹不能切断原下降波，
        # 后续更低 L 延伸原极值，更高 L 留作疑虑；合法反转沿保留的极值衔接。
        continuous: list[Mapping[str, object]] = []
        for point in points:
            if _point_id(point) in rejected:
                continue
            if continuous and point.get('kind') == continuous[-1].get('kind'):
                sign = 1 if point.get('kind') == 'H' else -1
                if sign * (cast(float, point['value']) - cast(float, continuous[-1]['value'])) > 0:
                    continuous[-1] = point
            else:
                continuous.append(point)
        published_tail = None
        if tail is not None and tail_points and continuous and continuous[-1] is tail_points[-1]:
            if len(continuous) >= 2:
                published_tail = _replace_path(tail, continuous[-2:], points)
            continuous = continuous[:-1]
        return _replace_path(stroke, continuous, points), published_tail

    original_strokes, original_tails = _rows(row.get('strokes')), _rows(row.get('developing_strokes'))
    tails_by_anchor: dict[tuple[object, ...], list[int]] = {}
    for position, tail in enumerate(original_tails):
        points = _rows(tail.get('points'))
        if len(points) >= 2:
            tails_by_anchor.setdefault((tail.get('source_path'), *_point_id(points[0])), []).append(position)
    strokes: list[dict[str, object]] = []
    tails: dict[int, dict[str, object]] = {}
    consumed: set[int] = set()
    for stroke in original_strokes:
        points = _rows(stroke.get('points'))
        matches = tails_by_anchor.get((stroke.get('source_path'), *_point_id(points[-1])), []) if points else []
        match = matches[0] if len(matches) == 1 and matches[0] not in consumed else None
        projected, tail = project(stroke, original_tails[match] if match is not None else None)
        strokes.append(projected)
        if match is not None:
            consumed.add(match)
            if tail is not None:
                tails[match] = tail
    for position, tail in enumerate(original_tails):
        if position not in consumed:
            projected, _ = project(tail)
            if len(_rows(projected.get('points'))) >= 2:
                tails[position] = projected
    developing = [tails[position] for position in sorted(tails)]
    result = dict(row, strokes=strokes, developing_strokes=developing,
                  observation_strokes=observations, lifecycle_display_policy=TREND_DISPLAY_POLICY)
    # 保留既有计数口径；只有真正归并了顶点才更新显示计数。
    changed = (len(developing) != len(original_tails)
        or any(len(_rows(after.get('points'))) != len(_rows(before.get('points')))
               for after, before in zip(strokes, original_strokes))
        or any([_point_id(point) for point in _rows(after.get('points'))]
               != [_point_id(point) for point in _rows(before.get('points'))]
               for after, before in zip(developing, original_tails)))
    if changed:
        result.update(confirmed_wave_count=sum(len(_rows(stroke.get('points'))) for stroke in strokes),
                      developing_wave_count=len(developing),
                      developing_point_count=sum(len(_rows(stroke.get('points'))) for stroke in developing))
    return result
