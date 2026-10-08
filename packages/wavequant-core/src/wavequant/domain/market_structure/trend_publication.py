"""Publish upward trend vertices only after one of the two causal proofs."""

from collections.abc import Mapping, Sequence
from typing import TypeAlias

from ..models.model import Bar
from .trend_confirmation import ConfirmationContext, prepare_confirmation_context, qualify_uptrend

Moment: TypeAlias = int | str


def _moment(point: Mapping[str, object]) -> Moment:
    value = point['available_at']
    if type(value) is int or isinstance(value, str):
        return value
    raise ValueError('trend availability must be a session index or date')


def _later(left: Moment, right: Moment) -> Moment:
    if isinstance(left, str) and isinstance(right, str):
        return max(left, right)
    if type(left) is int and type(right) is int:
        return max(left, right)
    raise ValueError('one trend path must use one availability representation')


def publish_uptrends(
    candidates: Sequence[Mapping[str, object]],
    source: Sequence[Mapping[str, object]],
    bars: Sequence[Bar],
    *,
    source_level: int,
) -> list[dict[str, object]]:
    """Keep structural candidates private until their rising leg is qualified.

    A first confirmed descending H supplies the initial frozen pressure key.
    It grants no rising line. A rejected L cannot expose its following H as a
    completed rising wave. A local candidate H preceding the actual proof
    cannot end the whole impulse; retain the original low until a real peak.
    """
    result: list[dict[str, object]] = []
    pressure: Mapping[str, object] | None = None
    context: ConfirmationContext | None = None
    market = bars or None
    end_index = len(bars) - 1 if bars else max(
        (value for item in source for value in (item['index'],item.get('available_at')) if type(value) is int), default=-1)
    position_field = 'source_turn_position' if source_level == 0 else f'source_level{source_level}_position'
    for offset, candidate in enumerate(candidates):
        point = dict(candidate)
        if point['kind'] == 'H':
            # A causally confirmed descending pressure anchor is independent
            # of whether its preceding rising leg was qualified for display.
            pressure = candidate
            if result and result[-1]['kind'] == 'H':
                continue
            if result:
                proof = result[-1].get('trend_confirmation')
                if isinstance(proof, Mapping):
                    trigger = proof.get('confirmed_by')
                    if isinstance(trigger, Mapping):
                        trigger_index, point_index = trigger.get('index'), point.get('index')
                        if isinstance(trigger_index, int) and isinstance(point_index, int) and point_index < trigger_index:
                            continue
                point['available_at'] = _later(_moment(point), _moment(result[-1]))
                if isinstance(proof, Mapping):
                    point['incoming_trend_confirmation'] = dict(proof)
            result.append(point)
            continue
        position = point.get(position_field)
        if type(position) is not int or not 0 <= position < len(source):
            raise ValueError('a trend candidate must identify its actual source origin')
        origin = source[position]
        identity = ('index', 'ordinal', 'kind', 'value')
        if tuple(point.get(field, 0 if field == 'ordinal' else None) for field in identity) != tuple(
                origin.get(field, 0 if field == 'ordinal' else None) for field in identity):
            raise ValueError('a trend candidate must match its actual source origin')
        if result and result[-1]['kind'] == 'L':
            continue
        key = pressure
        if context is None and end_index >= 0:
            context = prepare_confirmation_context(source, market, end_index)
        confirmation = qualify_uptrend(source, origin, position, key, market, end_index, context=context)
        if confirmation is None:
            continue
        point['available_at'] = _later(_moment(point), _moment(confirmation))
        point['structural_confirmation_rule'] = point['confirmation_rule']
        point['confirmation_rule'] = confirmation['confirmation_rule']
        point['trend_confirmation'] = confirmation
        point['trend_confirmation_route'] = (
            'lower_level_break_alternation_turn' if confirmation.get('alternation_low') is not None else 'same_level_key_break'
        )
        result.append(point)
    return result
