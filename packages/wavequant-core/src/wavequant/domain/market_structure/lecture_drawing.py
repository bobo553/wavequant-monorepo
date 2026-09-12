"""Derive one lecture path across ordinary and resolved child/mother bars.

This is a drawing contract, not an observed intraday path or strategy input.
Known-direction inside bars use the basic single-extreme drawing rule, not a
claim of resolved intrabar order. Undefined doji paths still delimit runs.
Teaching evidence retains original vertices when developing endpoints extend.
"""
from zoneinfo import ZoneInfo

from .polyline import observe_bar_relations, child_mother_path, PointKind
from .price_action import Direction


def lecture_drawing(bars, *, on_step=None):
    def session(index):
        stamp = bars[index].timestamp
        return (stamp.astimezone(ZoneInfo('Asia/Shanghai')) if stamp.tzinfo else stamp).date().isoformat()

    def vertex(index, kind, state, known, ordinal=0, **extra):
        return dict(index=index, ordinal=ordinal, kind=kind.value,
                    value=bars[index].high if kind == PointKind.HIGH else bars[index].low,
                    time=session(index), state=state, available_at=session(known), **extra)

    strokes, issues, teaching_paths, inside_connections = [], [], [], []
    points = []
    start = 0
    direction = None
    path_ids = []

    def publish(i):
        if on_step is not None: on_step(i,start,points)

    if bars: publish(0)

    def flush():
        if len(points) > 1:
            strokes.append(dict(id=f'lecture-{start}', kind='path' if path_ids else 'ordinary',
                                points=points, teaching_path_ids=list(path_ids),
                                seed_policy='first_directional_high_low_pair_opposite_extreme'))

    for i in range(1, len(bars)):
        relation = observe_bar_relations(bars[i-1], bars[i])
        if relation.outside:
            teaching = child_mother_path(bars[i-1], bars[i], child_index=i-1)
            if teaching.vertices:
                path_id = f'child-mother-{i}'
                raw = [vertex(p.index, p.kind, 'teaching', i, p.ordinal) for p in teaching.vertices]
                teaching_paths.append(dict(id=path_id, kind='teaching', points=raw,
                                           source=teaching.provenance,
                                           label='子母三点原始依据；已接入讲义主路径，不进入策略'))
                first = dict(raw[0], teaching_path_id=path_id, teaching_ordinal=1)
                if points and points[-1]['index'] == i-1:
                    # Reconcile the still-developing child's endpoint with its
                    # prescribed H/L. Consecutive mothers reuse their last point.
                    previous = points[-1]
                    if previous['kind'] == first['kind']:
                        first['ordinal'] = previous['ordinal']
                    first['edge_kind'] = previous.get('edge_kind', 'ordinary')
                    points[-1] = first
                else:
                    if points and points[-1]['state'] == 'developing':
                        points[-1] = dict(points[-1], state='confirmed', available_at=session(i))
                    points.append(dict(first, edge_kind='teaching'))
                points.append(dict(raw[1], edge_kind='teaching', teaching_path_id=path_id, teaching_ordinal=2))
                points.append(dict(raw[2], state='developing', edge_kind='teaching',
                                   teaching_path_id=path_id, teaching_ordinal=3))
                path_ids.append(path_id)
                direction = Direction.UP if raw[2]['kind'] == 'H' else Direction.DOWN
                publish(i)
                continue
            reason = '十字星子母规则未定义'
        elif relation.inside and direction is None:
            reason = '初始母子关系缺少已知方向，不能猜测起始高低顺序'
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

        reverse = ((relation.falling_tail or relation.shrinking_head) if direction == Direction.UP
                   else (relation.extending_head or relation.shrinking_foot))
        candidate = points[-1]
        target = (PointKind.LOW if direction == Direction.UP else PointKind.HIGH) if reverse else PointKind(candidate['kind'])
        next_point = vertex(i, target, 'developing', i, edge_kind='ordinary')
        if relation.inside:
            rule = '上涨缩头：原高点连子低' if direction == Direction.UP else '下跌缩脚：原低点连子高'
            next_point['drawing_rule'] = rule
            next_point['intrabar_order_resolved'] = False
            inside_connections.append(dict(index=i, time=session(i), path_id=f'lecture-{start}', from_index=candidate['index'],
                                           from_value=candidate['value'], to_value=next_point['value'],
                                           rule=rule, source='basic_single_extreme_drawing_only'))
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
                scope='lecture_drawing_only_not_strategy_pivots', incomplete=bool(issues),
                intrabar_incomplete=bool(issues or inside_connections),
                note='子母三点接入主路径；已知方向内包按缩头／缩脚连接单极值，不确认日内顺序；十字星外包及未知初始方向仍单列中断。')
