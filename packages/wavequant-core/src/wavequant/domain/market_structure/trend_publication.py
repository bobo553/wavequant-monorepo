"""Publish qualified trend vertices while retaining their whole-wave extremes."""

from collections.abc import Mapping, Sequence
from math import isfinite
from typing import TypeAlias, cast

from ..models.model import Bar
from .trend_confirmation import ConfirmationContext, prepare_confirmation_context, qualify_uptrend
from .n_trend_confirmation import N_TARGET_CONFIRMATION, is_n_target_reversal, qualified_n_source

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


def _held_wave_floor(origin: Mapping[str, object], bars: Sequence[Bar], through_index: int) -> bool:
    """An earlier floor belongs to the same wave only while its actual price holds."""
    index, value = cast(int, origin['index']), cast(float, origin['value'])
    if not bars or not 0 <= index <= through_index < len(bars) or bars[index].low != value:
        return False
    return all(isfinite(bars[position].low) and bars[position].low >= value
               for position in range(index + 1, through_index + 1))


def publish_uptrends(
    candidates: Sequence[Mapping[str, object]],
    source: Sequence[Mapping[str, object]],
    bars: Sequence[Bar],
    *,
    source_level: int,
    qualified_source: Sequence[Mapping[str, object]] | None = None,
    n_target_trend_confirmation_enabled: bool = False,
) -> list[dict[str, object]]:
    """Keep structural candidates private until their rising leg is qualified.

    A first confirmed descending H supplies the initial frozen pressure key.
    It grants no rising line. A rejected L cannot expose its following H as a
    completed rising wave. A local candidate H preceding the actual proof
    cannot end the whole impulse; retain the original low until a real peak.
    Rejected rising fragments retain their held whole-wave floor. A later
    legal local certificate grants permission without moving that price anchor
    to its higher B. The certificate keeps its own origin and dates; wave_origin
    identifies the independent earlier market extreme. Without bars, holding
    cannot be verified and the original local-origin behavior remains.
    """
    result: list[dict[str, object]] = []
    pressure: Mapping[str, object] | None = None
    pending_origin: Mapping[str, object] | None = None
    context: ConfirmationContext | None = None
    market = bars or None
    end_index = len(bars) - 1 if bars else max(
        (value for item in source for value in (item['index'],item.get('available_at')) if type(value) is int), default=-1)
    position_field = 'source_turn_position' if source_level == 0 else f'source_level{source_level}_position'
    n_source = (qualified_n_source(source, qualified_source)
                if n_target_trend_confirmation_enabled and qualified_source is not None else source)
    for offset, candidate in enumerate(candidates):
        if not n_target_trend_confirmation_enabled and is_n_target_reversal(candidate):
            continue
        point = dict(candidate)
        if not n_target_trend_confirmation_enabled:
            for field in ('trend_confirmation', 'incoming_trend_confirmation'):
                proof = point.get(field)
                if isinstance(proof, Mapping) and proof.get('confirmation_rule') == N_TARGET_CONFIRMATION:
                    point.pop(field)
            point.pop('n_target_confirmation', None)
            if point.get('trend_confirmation_route') == N_TARGET_CONFIRMATION:
                point.pop('trend_confirmation_route')
        if point['kind'] == 'H':
            # A causally confirmed descending pressure anchor is independent
            # of whether its preceding rising leg was qualified for display.
            pressure = candidate
            descending = point.get('n_target_confirmation')
            if n_target_trend_confirmation_enabled and isinstance(descending, Mapping):
                point['trend_confirmation'] = dict(descending)
                point['trend_confirmation_route'] = N_TARGET_CONFIRMATION
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
            pending_origin = None
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
            context = prepare_confirmation_context(
                source, market, end_index, n_source=n_source,
                n_target_trend_confirmation_enabled=n_target_trend_confirmation_enabled,
            )
        if context is not None and context.references[position] is not None:
            if (pending_origin is None or not _held_wave_floor(pending_origin, bars, cast(int, point['index']))
                    or cast(float, point['value']) < cast(float, pending_origin['value'])):
                pending_origin = point
        confirmation = qualify_uptrend(source, origin, position, key, market, end_index,
                                       context=context, n_source=n_source,
                                       n_target_trend_confirmation_enabled=n_target_trend_confirmation_enabled)
        if confirmation is None:
            continue
        earliest = _later(_moment(point), _moment(confirmation))
        future_key = key
        for later_candidate in candidates[offset + 1:]:
            if not n_target_trend_confirmation_enabled and is_n_target_reversal(later_candidate):
                continue
            if later_candidate['kind'] == 'H':
                trigger = cast(Mapping[str, object], confirmation['confirmed_by'])
                if cast(int, later_candidate['index']) >= cast(int, trigger['index']):
                    break
                future_key = later_candidate
                continue
            if _later(_moment(later_candidate), earliest) != earliest:
                continue
            later_position = later_candidate.get(position_field)
            if type(later_position) is not int or not 0 <= later_position < len(source):
                raise ValueError('a trend candidate must identify its actual source origin')
            later_origin = source[later_position]
            if tuple(later_candidate.get(field, 0 if field == 'ordinal' else None) for field in identity) != tuple(
                    later_origin.get(field, 0 if field == 'ordinal' else None) for field in identity):
                raise ValueError('a trend candidate must match its actual source origin')
            local = qualify_uptrend(source, later_origin, later_position, future_key, market, end_index,
                                    context=context, n_source=n_source,
                                    n_target_trend_confirmation_enabled=n_target_trend_confirmation_enabled)
            if local is None:
                continue
            local_trigger = cast(Mapping[str, object], local['confirmed_by'])
            local_known = _later(_moment(later_candidate), _moment(local))
            if (_later(local_known, earliest) == earliest and local_known != earliest
                    and pending_origin is not None
                    and _held_wave_floor(pending_origin, bars, cast(int, local_trigger['index']))):
                point, confirmation, earliest = dict(later_candidate), local, local_known
        candidate_known = _moment(point)
        trigger = cast(Mapping[str, object], confirmation['confirmed_by'])
        if (pending_origin is not None and pending_origin is not point
                and _held_wave_floor(pending_origin, bars, cast(int, trigger['index']))):
            point = dict(pending_origin)
            wave_position = cast(int, point[position_field])
            confirmation = dict(confirmation, wave_origin={
                field: source[wave_position][field]
                for field in ('index', 'ordinal', 'time', 'kind', 'value', 'available_at', 'label')
                if field in source[wave_position]
            })
        point['available_at'] = _later(_later(_moment(point), candidate_known), _moment(confirmation))
        point['structural_confirmation_rule'] = point['confirmation_rule']
        point['confirmation_rule'] = confirmation['confirmation_rule']
        point['trend_confirmation'] = confirmation
        point['trend_confirmation_route'] = (
            N_TARGET_CONFIRMATION if confirmation['confirmation_rule'] == N_TARGET_CONFIRMATION else
            'lower_level_break_alternation_turn' if confirmation.get('alternation_low') is not None else 'same_level_key_break'
        )
        result.append(point)
        pending_origin = None
    return result
