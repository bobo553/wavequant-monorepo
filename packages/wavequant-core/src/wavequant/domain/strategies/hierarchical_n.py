"""Larger N candidates from dated hierarchy pivots, with nested corrections folded."""
from ..market_structure.polyline import LinePoint, PointKind, ReversalPoint
from .hierarchical_entry import hierarchical_history


def hierarchical_n_candidates(bars, *, history=None):
    if history is None:
        history, _ = hierarchical_history(bars)
    result = {}
    seen = set()
    for now, levels in history.items():
        for level in (2, 3):
            points = levels[level]
            for offset in range(1, len(points)):
                a, b = points[offset-1:offset+1]
                if a['kind'] != 'L' or b['kind'] != 'H':
                    continue
                c = None
                for point in points[offset+1:]:
                    if point['kind'] == 'H' and point['value'] >= b['value']:
                        break
                    if point['kind'] == 'L':
                        if point['value'] <= a['value']:
                            c = None
                            break
                        if c is None or point['value'] < c['value']:
                            c = point
                if c is None:
                    continue
                identity = tuple((p['index'], p['available_at']) for p in (a,b,c))
                if identity in seen:
                    continue
                seen.add(identity)
                refs = tuple(ReversalPoint(LinePoint(p['index'],p['ordinal'],
                    PointKind.LOW if p['kind']=='L' else PointKind.HIGH,p['value']),
                    p['available_at'],'confirmed_hierarchical_n') for p in (a,b,c))
                result.setdefault(now,[]).append((refs,level))
    return result
