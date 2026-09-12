"""Build display-only development paths between confirmed trend levels.

Formal points at level N only exist after a source-level structural key break.
When that key remains unbroken for years, hiding all confirmed source points
makes the chart look truncated.  This module exposes the intervening evidence
without promoting it to formal level-N structure.
"""

from .lecture_trend import _ref, _wave_reversals


def _developing_point(point, position, available_at, role, *, trend_level, source_level):
    """Copy one confirmed source point into a higher-level inspection path."""
    result = _ref(point)
    result.update(
        available_at=available_at,
        state='developing',
        trend_level=trend_level,
        **{f'source_level{source_level}_position': position},
        source_label=point['label'],
        display_only=True,
        development_role=role,
    )
    return result


def _strongest(points, kind):
    """Choose the strongest point of one kind; equal prices keep the first."""
    candidates = [item for item in points if item[1]['kind'] == kind]
    if not candidates:
        return None
    return (max if kind == 'H' else min)(candidates, key=lambda item: item[1]['value'])


def hierarchical_developing_path(source, confirmed, *, trend_level, source_level, kind):
    """Return the causal, display-only path after the latest formal point.

    The latest formal point fixes the beginning and direction of the unfinished
    higher-level structure.  The path then applies the existing wave reducer to
    confirmed source points, preserving both confirmed internal turns and every
    point in the unresolved final tail.  It never mutates ``source`` or
    ``confirmed`` and is never valid input for another trend level, a strategy,
    or a backtest.
    """
    if not confirmed:
        return None
    start = confirmed[-1]
    proof_field = f'confirmed_on_level{source_level}'
    source_position_field = f'source_level{source_level}_position'
    proof_position = start.get(proof_field)
    source_points = source['points']
    if not isinstance(proof_position, int) or proof_position >= len(source_points):
        return None

    suffix = source_points[proof_position:]
    nested = _wave_reversals(suffix)
    initial_kind = 'H' if start['wave_direction_after'] == 'up' else 'L'
    start_ref = _ref(start)
    start_ref.update(
        state='confirmed',
        trend_level=trend_level,
        **{source_position_field: start[source_position_field]},
        display_only=True,
        development_role='formal_start',
    )
    path = [start_ref]

    # If the nested reducer first confirms the opposite kind, retain the
    # preceding candidate that geometrically connects the formal point to it.
    first_position = proof_position + nested[0]['source_turn_position'] if nested else len(source_points) - 1
    if not nested or nested[0]['kind'] != initial_kind:
        initial = _strongest(
            enumerate(source_points[proof_position:first_position + 1], proof_position),
            initial_kind,
        )
        if initial:
            position, point = initial
            known = max(start['available_at'], point['available_at'])
            path.append(
                _developing_point(
                    point,
                    position,
                    known,
                    'confirmed_candidate',
                    trend_level=trend_level,
                    source_level=source_level,
                )
            )

    for turn in nested:
        position = proof_position + turn['source_turn_position']
        known = max(start['available_at'], turn['available_at'])
        path.append(
            _developing_point(
                turn,
                position,
                known,
                'confirmed_nested_turn',
                trend_level=trend_level,
                source_level=source_level,
            )
        )

    # A reducer omits its unresolved tail by design.  Retain that evidence so
    # the inspection line reaches the most recently confirmed source point.
    if nested:
        last_position = proof_position + nested[-1]['source_turn_position']
        for position, point in enumerate(source_points[last_position + 1:], last_position + 1):
            known = max(path[-1]['available_at'], point['available_at'])
            path.append(
                _developing_point(
                    point,
                    position,
                    known,
                    'pending_evidence',
                    trend_level=trend_level,
                    source_level=source_level,
                )
            )

    if len(path) < 2:
        return None
    path[-1]['development_role'] = 'active_endpoint'
    active_direction = 'up' if path[-1]['kind'] == 'H' else 'down'
    return dict(
        id=f'{kind}-developing-{source["id"]}',
        source_path=source['id'],
        kind=f'{kind}-developing',
        trend_level=trend_level,
        source_level=source_level,
        state='developing',
        display_only=True,
        initial_direction=start['wave_direction_after'],
        wave_direction=active_direction,
        available_at=path[-1]['available_at'],
        nested_turn_count=sum(p['development_role'] == 'confirmed_nested_turn' for p in path),
        pending_point_count=sum(
            p['development_role'] in ('pending_evidence', 'active_endpoint') for p in path
        ),
        confirmation_rule=(
            f'confirmed_level{source_level}_development_path_after_last_level{trend_level}_reversal'
        ),
        points=path,
    )
