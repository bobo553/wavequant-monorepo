"""Keep causal anchor publication separate from qualified directional waves."""

from collections.abc import Mapping, Sequence
from math import isfinite
from typing import TypeAlias, cast

from ..models.model import Bar
from .trend_confirmation import ConfirmationContext, prepare_confirmation_context, qualify_downtrend, qualify_uptrend
from .hierarchical_confirmation import session_date

Moment: TypeAlias = int | str
LEG_CONFIRMATION_POLICY = 'two_routes_each_direction_v108'


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


def _same_origin(proof: Mapping[str, object], point: Mapping[str, object]) -> bool:
    origin = proof.get('wave_origin', proof.get('origin'))
    return isinstance(origin, Mapping) and all(
        origin.get(field, 0 if field == 'ordinal' else None) == point.get(field, 0 if field == 'ordinal' else None)
        for field in ('index', 'ordinal', 'kind', 'value')
    )


def annotate_published_legs(
    points: Sequence[Mapping[str, object]], source: Sequence[Mapping[str, object]], bars: Sequence[Bar],
    *, source_level: int,
) -> list[dict[str, object]]:
    """A vertex's outgoing proof cannot authorize its incoming opposite leg.

    Keep independently published anchors for pressure, measurement and legal
    rising backgrounds. Certify each displayed H-to-L leg against its frozen
    preceding public low or a complete lower-level bearish cycle. The cutoff
    is the endpoint's own publication session, never the later history tail.
    Missing market/source evidence fails closed. Reuse one parsing context at
    a time so historical charts do not retain a context for every endpoint.
    """
    result = [dict(point) for point in points]
    sessions = {session_date(bar): index for index, bar in enumerate(bars)}
    position_field = 'source_turn_position' if source_level == 0 else f'source_level{source_level}_position'
    context: ConfirmationContext | None = None
    active: Mapping[str, object] | None = None
    extreme: Mapping[str, object] | None = None
    for offset in range(1, len(result)):
        previous, point = result[offset - 1], result[offset]
        direction = 'up' if previous['kind'] == 'L' and point['kind'] == 'H' else 'down'
        point['incoming_trend_state'] = 'unconfirmed'
        proof = point.get('incoming_trend_confirmation') if direction == 'up' else None
        origin: Mapping[str, object] = previous
        if direction == 'down':
            point.pop('incoming_trend_confirmation', None)
            position = origin.get(position_field)
            known = _moment(point)
            cutoff = sessions.get(known, -1) if isinstance(known, str) else known
            if (bars and previous['kind'] == 'H' and point['kind'] == 'L'
                    and type(position) is int and 0 <= position < len(source) and 0 <= cutoff < len(bars)
                    and all(origin.get(field, 0 if field == 'ordinal' else None)
                            == source[position].get(field, 0 if field == 'ordinal' else None)
                            for field in ('index', 'ordinal', 'kind', 'value'))):
                key = next((item for item in reversed(result[:offset - 1]) if item['kind'] == 'L'), None)
                if context is None or context.end_index != cutoff:
                    context = prepare_confirmation_context(source, bars, cutoff)
                proof = qualify_downtrend(source, source[position], position, key, bars, cutoff, context=context)
        trigger = proof.get('confirmed_by') if isinstance(proof, Mapping) else None
        if (isinstance(proof, Mapping) and isinstance(trigger, Mapping)
                and type(trigger.get('index')) is int and cast(int, point['index']) >= cast(int, trigger['index'])
                and proof.get('direction') == direction and _same_origin(proof, origin)
                and _later(_moment(proof), _moment(point)) == _moment(point)):
            if direction == 'down' and active is not None and active['direction'] == 'up' and extreme is not None:
                wave_position = extreme.get(position_field)
                if type(wave_position) is int and 0 <= wave_position < len(source):
                    wave_origin = source[wave_position]
                    if (not _same_origin(dict(origin=extreme), origin) and _same_origin(dict(origin=wave_origin), extreme)
                            and _later(_moment(wave_origin), _moment(proof)) == _moment(proof)
                            and _wave_holds(dict(proof, wave_origin=wave_origin), bars, cast(Mapping[str, object], proof['confirmed_by']))):
                        proof = dict(proof, wave_origin={field: wave_origin[field] for field in
                                     ('index', 'ordinal', 'time', 'kind', 'value', 'available_at', 'label') if field in wave_origin})
            point['incoming_trend_confirmation'] = dict(proof)
            point['incoming_trend_state'] = 'confirmed'
            continued = active is not None and active['direction'] == direction and _wave_holds(active, bars, point)
            if not continued:
                active = proof
                extreme = point
            elif extreme is not None and (cast(float, point['value']) > cast(float, extreme['value'])
                                          if direction == 'up' else cast(float, point['value']) < cast(float, extreme['value'])):
                extreme = point
        else:
            point.pop('incoming_trend_confirmation', None)
        if active is not None:
            point['active_trend_confirmation'] = dict(active)
    return result


def _wave_holds(proof: Mapping[str, object], bars: Sequence[Bar], endpoint: Mapping[str, object]) -> bool:
    origin = cast(Mapping[str, object], proof.get('wave_origin', proof['origin']))
    start, end, value = cast(int, origin['index']), cast(int, endpoint['index']), cast(float, origin['value'])
    if not bars or not 0 <= start < end < len(bars):
        return False
    rising = proof['direction'] == 'up'
    prices = (bar.low if rising else bar.high for bar in bars[start:end + 1])
    return all(isfinite(price) and (price >= value if rising else price <= value) for price in prices)


def confirmed_trend_legs(points: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    """An unqualified retracement cannot terminate or restart a higher wave.

    Retain published anchors as independent evidence, but consolidate same-
    direction extremes until the opposite direction earns its own proof.
    """
    legs: list[dict[str, object]] = []
    for point in points:
        proof = point.get('active_trend_confirmation')
        if point.get('incoming_trend_state') != 'confirmed' or not isinstance(proof, Mapping):
            continue
        direction = proof['direction']
        if point['kind'] != ('H' if direction == 'up' else 'L'):
            continue
        origin = next((item for item in points if _same_origin(proof, item)), None)
        if origin is None:
            continue
        leg = dict(points=[dict(origin), dict(point)], direction=direction, confirmation=dict(proof),
                   available_at=point['available_at'])
        if legs and legs[-1]['direction'] == direction and _same_origin(cast(Mapping[str, object], legs[-1]['confirmation']), origin):
            last_points = cast(list[Mapping[str, object]], legs[-1]['points'])
            previous = cast(float, last_points[-1]['value'])
            value = cast(float, point['value'])
            if (value > previous if direction == 'up' else value < previous):
                legs[-1] = leg
        else:
            legs.append(leg)
    return legs


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
    for candidate in candidates:
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
            context = prepare_confirmation_context(source, market, end_index)
        if context is not None and context.references[position] is not None:
            if (pending_origin is None or not _held_wave_floor(pending_origin, bars, cast(int, point['index']))
                    or cast(float, point['value']) < cast(float, pending_origin['value'])):
                pending_origin = point
        confirmation = qualify_uptrend(source, origin, position, key, market, end_index, context=context)
        if confirmation is None:
            continue
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
            'lower_level_break_alternation_turn' if confirmation.get('alternation_low') is not None else 'same_level_key_break'
        )
        result.append(point)
        pending_origin = None
    return result
