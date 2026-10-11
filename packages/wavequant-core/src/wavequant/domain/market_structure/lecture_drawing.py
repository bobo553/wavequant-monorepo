"""Build the causal lecture path shared by chart geometry and strategy pivots.

Contained and encompassing pairs follow the candle-colour teaching order, with
same-direction legs collapsed. This order is not an observed intraday path.
An inside doji with a known incoming direction uses only the opposite extreme.
A bounded outside doji extends only the incoming extreme; genuinely undefined
paths still delimit runs. Teaching evidence retains original vertices when a
developing endpoint extends.
"""
from collections.abc import Mapping, Sequence
from typing import cast
from zoneinfo import ZoneInfo

from ..models.model import Bar
from .polyline import (observe_bar_relations, child_mother_path, mother_child_path,
                       teaching_inside, teaching_outside, LinePoint, PointKind, TeachingPath)
from .price_action import Direction


def _bounded_outside_doji(
    points: Sequence[Mapping[str, object]], bar: Bar, direction: Direction | None,
) -> bool:
    """外包十字星仅在已确认对向边界内延伸现有未结束腿。

    新的同向极值一定发生，但母线两个极值的先后仍未知。只有对向极值
    未越过上一已确认对向点、同向极值严格推进时，才保留原路径的单侧
    延伸；不画母线另一极值，也不把本次延伸当成反转确认。seed 没有
    反转证据，不能充当此边界；双向越界或缺少来向仍须保留真正断路。
    """
    if direction is None or len(points) < 2 or bar.open != bar.close:
        return False
    anchor, endpoint = points[-2:]
    up = direction == Direction.UP
    target, opposite = ('H', 'L') if up else ('L', 'H')
    if (anchor.get('kind') != opposite or anchor.get('state') not in ('confirmed', 'teaching')
            or endpoint.get('kind') != target or endpoint.get('state') != 'developing'):
        return False
    sign = 1 if up else -1
    extreme, boundary = (bar.high, bar.low) if up else (bar.low, bar.high)
    return (sign * (extreme - cast(float, endpoint['value'])) > 0
            and sign * (boundary - cast(float, anchor['value'])) >= 0)


def lecture_drawing(bars, *, on_step=None):
    def session(index):
        stamp = bars[index].timestamp
        return (stamp.astimezone(ZoneInfo('Asia/Shanghai')) if stamp.tzinfo else stamp).date().isoformat()

    def vertex(index, kind, state, known, ordinal=0, **extra):
        return dict(index=index, ordinal=ordinal, kind=kind.value,
                    value=bars[index].high if kind == PointKind.HIGH else bars[index].low,
                    time=session(index), state=state, available_at=session(known), **extra)

    strokes, issues, teaching_paths, inside_connections = [], [], [], []
    points: list[dict[str, object]] = []
    start = 0
    direction = None
    path_ids: list[str] = []
    outside_doji_seen = False

    def publish(i):
        if on_step is not None: on_step(i,start,points)

    if bars: publish(0)

    def flush():
        if len(points) > 1:
            strokes.append(dict(id=f'lecture-{start}', kind='path' if path_ids else 'ordinary',
                                points=points, teaching_path_ids=list(path_ids),
                                seed_policy='colour_order_for_containment_or_first_directional_pair'))

    for i in range(1, len(bars)):
        relation = observe_bar_relations(bars[i-1], bars[i])
        inside = relation.inside or ((relation.equal_high or relation.equal_low)
                                     and teaching_inside(bars[i-1], bars[i]))
        outside = relation.outside or ((relation.equal_high or relation.equal_low)
                                       and teaching_outside(bars[i-1], bars[i]))
        doji_inside_fallback = False
        doji_outside_extension = False
        if outside or inside:
            teaching = (child_mother_path(bars[i-1], bars[i], child_index=i-1) if outside else
                        mother_child_path(bars[i-1], bars[i], mother_index=i-1))
            if (outside and not teaching.vertices and bars[i-1].open == bars[i-1].close
                    and bars[i].open != bars[i].close and direction is not None
                    and points and points[-1]['index'] == i-1):
                # The known child endpoint is enough to join the coloured mother;
                # no order is inferred for the other extreme of the doji child.
                endpoint = points[-1]
                mother_order = ((PointKind.HIGH, PointKind.LOW) if bars[i].close < bars[i].open else
                                (PointKind.LOW, PointKind.HIGH))
                ordered = (LinePoint(i-1, cast(int, endpoint['ordinal']), PointKind(endpoint['kind']),
                                     cast(float, endpoint['value'])),
                           *(LinePoint(i, ordinal, kind,
                                       bars[i].high if kind == PointKind.HIGH else bars[i].low)
                             for ordinal, kind in enumerate(mother_order)))
                teaching = TeachingPath(ordered, 'known_child_endpoint',
                                        'known_child_endpoint_to_coloured_mother', ordered)
            if teaching.vertices:
                path_id = f'{"child-mother" if outside else "mother-child"}-{i}'
                evidence_points = [vertex(p.index, p.kind, 'teaching', i, p.ordinal)
                                   for p in teaching.vertices]
                if outside:
                    teaching_paths.append(dict(id=path_id, kind='teaching', points=evidence_points,
                                               source=teaching.provenance,
                                               label='子母折线依据；已接入讲义主路径'))
                else:
                    before = points[-1] if points else None
                    inside_connections.append(dict(index=i, time=session(i), path_id=f'lecture-{start}',
                                                   from_index=before['index'] if before else i-1,
                                                   from_value=before['value'] if before else evidence_points[0]['value'],
                                                   to_value=evidence_points[-1]['value'],
                                                   rule='母子阴阳路径：相邻同向线段合并',
                                                   source=teaching.provenance))
                ordered = teaching.raw_vertices
                if points and points[-1]['index'] == i-1:
                    last = points[-1]
                    last_extreme = (last['kind'], last['value'])
                    if last_extreme == (ordered[1].kind.value, ordered[1].price):
                        offset = 2
                    elif last_extreme == (ordered[0].kind.value, ordered[0].price):
                        offset = 1
                    else:
                        raise ValueError('current endpoint must match a first-bar extreme')
                    points[-1] = dict(last, teaching_path_id=path_id, teaching_ordinal=offset,
                                      edge_kind=last.get('edge_kind', 'teaching'))
                else:
                    offset = 0
                for ordinal, item in enumerate(ordered[offset:], start=offset + 1):
                    next_point = vertex(item.index, item.kind, 'developing', i, item.ordinal,
                                        edge_kind='teaching', teaching_path_id=path_id,
                                        teaching_ordinal=ordinal)
                    if teaching.status == 'known_child_endpoint':
                        next_point['intrabar_order_resolved'] = False
                    if not points:
                        points.append(dict(next_point, state='teaching'))
                        continue
                    if points[-1]['value'] == next_point['value']:
                        if outside and item.index == i and points[-1]['index'] == i-1:
                            points[-1] = next_point
                        continue
                    if (len(points) > 1 and points[-1]['state'] == 'developing' and
                            (cast(float, points[-1]['value']) - cast(float, points[-2]['value'])) *
                            (next_point['value'] - cast(float, points[-1]['value'])) > 0):
                        points[-1] = next_point
                    else:
                        if points[-1]['state'] == 'developing':
                            points[-1] = dict(points[-1], state='confirmed', available_at=session(i))
                        points.append(next_point)
                path_ids.append(path_id)
                direction = Direction.UP if points[-1]['kind'] == 'H' else Direction.DOWN
                publish(i)
                continue
            doji_inside_fallback = inside and direction is not None and bool(points)
            doji_outside_extension = outside and _bounded_outside_doji(points, bars[i], direction)
            reason = None if doji_inside_fallback or doji_outside_extension else '十字星包含关系的阴阳顺序未定义'
        else:
            reason = None

        if reason:
            flush()
            issues.append(dict(index=i, time=session(i), reason=reason))
            points, path_ids, direction, start = [], [], None, i
            publish(i)
            continue

        if direction is None:
            if relation.extending_head or relation.shrinking_foot:
                direction = Direction.UP
            elif relation.falling_tail or relation.shrinking_head:
                direction = Direction.DOWN
            else:
                publish(i)
                continue
            opposite = PointKind.LOW if direction == Direction.UP else PointKind.HIGH
            target = PointKind.HIGH if direction == Direction.UP else PointKind.LOW
            points = [vertex(start, opposite, 'seed', i),
                      vertex(i, target, 'developing', i, edge_kind='ordinary')]
            publish(i)
            continue

        # 外包同时出头落尾，不能由反向关系抢走已验证的单侧延伸，更不能
        # 在母线内凭空确认新反转；下一根真实相反极值才有机会结束这条腿。
        reverse = not doji_outside_extension and (
            doji_inside_fallback or ((relation.falling_tail or relation.shrinking_head)
                                     if direction == Direction.UP else
                                     (relation.extending_head or relation.shrinking_foot)))
        candidate = points[-1]
        target = (PointKind.LOW if direction == Direction.UP else PointKind.HIGH) if reverse else PointKind(candidate['kind'])
        next_point = vertex(i, target, 'developing', i, edge_kind='ordinary')
        if doji_inside_fallback:
            rule = '十字星母子：已知上涨方向只连子低' if direction == Direction.UP else '十字星母子：已知下跌方向只连子高'
            next_point.update(drawing_rule=rule, intrabar_order_resolved=False)
            inside_connections.append(dict(index=i, time=session(i), path_id=f'lecture-{start}',
                                           from_index=candidate['index'], from_value=candidate['value'],
                                           to_value=next_point['value'], rule=rule,
                                           source='known_direction_single_extreme_for_inside_doji'))
        if doji_outside_extension:
            outside_doji_seen = True
            next_point.update(
                drawing_rule='十字星外包：未越过已确认对向极值，只延伸已知方向单极值',
                drawing_source='known_direction_bounded_single_extreme_for_outside_doji',
                intrabar_order_resolved=False,
            )
        sign = 1 if direction == Direction.UP else -1
        delta = sign * (next_point['value'] - candidate['value'])
        if not reverse and delta > 0:
            # Extend the single unfinished extreme; never restart at the mother.
            next_point['edge_kind'] = candidate.get('edge_kind', 'ordinary')
            if candidate.get('teaching_path_id'):
                next_point.update(teaching_path_id=candidate['teaching_path_id'],
                                  teaching_ordinal=candidate['teaching_ordinal'], teaching_extended=True)
            points[-1] = next_point
        elif reverse and delta < 0:
            points[-1] = dict(candidate, state='confirmed', available_at=session(i))
            points.append(next_point)
            direction = Direction.DOWN if direction == Direction.UP else Direction.UP

        publish(i)

    flush()
    return dict(strokes=strokes, teaching_paths=teaching_paths, inside_connections=inside_connections, issues=issues,
                scope='lecture_convention_shared_with_causal_pivots', incomplete=bool(issues),
                intrabar_incomplete=bool(issues or teaching_paths or inside_connections or outside_doji_seen),
                note='母子与子母按阴阳高低顺序接入主路径，连续同向线段合并；已知方向的十字星内包只连一极值，'
                     '十字星子线外包时由已知端点接入有阴阳方向的母线；有已确认对向边界的外包十字星'
                     '只延伸来向单极值，不推断母线内顺序。该顺序是讲义约定，不代表实测日内路径。')
