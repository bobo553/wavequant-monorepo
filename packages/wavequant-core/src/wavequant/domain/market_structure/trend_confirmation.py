"""Causal direction certificates shared by all trend hierarchy levels."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from fractions import Fraction
from math import isfinite
from types import MappingProxyType
from typing import Literal

from ..models.model import Bar
from .hierarchical_confirmation import session_date
from .n_trend_confirmation import NConfirmationContext, prepare_n_confirmation_context, qualify_n_trend


SAME_LEVEL_KEY_BREAK = "strict_same_level_market_key_break"
SOURCE_CYCLE_CONFIRMATION = "source_key_break_alternation_then_market_turn"


@dataclass(frozen=True)
class _Reference:
    raw: Mapping[str, object]
    index: int
    ordinal: int
    kind: Literal["H", "L"]
    value: Fraction
    available: str | int
    known: int | None

    @property
    def order(self) -> tuple[int, int]:
        return self.index, self.ordinal


def _reference(
    raw: Mapping[str, object], session_indices: Mapping[str, int] | None, end_index: int,
) -> _Reference | None:
    index, ordinal = raw.get("index"), raw.get("ordinal", 0)
    kind, value, available = raw.get("kind"), raw.get("value"), raw.get("available_at")
    if (type(index) is not int or index < 0 or index > end_index or type(ordinal) is not int or ordinal < 0
            or kind not in ("H", "L") or not isinstance(value, (int, float)) or isinstance(value, bool)
            or not isfinite(value) or value <= 0 or raw.get("display_only") is True
            or raw.get("state", "confirmed") not in ("confirmed", "reversal", "teaching")):
        return None
    known: int | None
    if type(available) is int:
        if available < index or available > end_index:
            return None
        known = available
    elif isinstance(available, str):
        try:
            if date.fromisoformat(available).isoformat() != available:
                return None
        except ValueError:
            return None
        time = raw.get("time")
        if isinstance(time, str) and available < time:
            return None
        if session_indices is None:
            known = None
        else:
            known = session_indices.get(available)
            if known is None or known < index:
                return None
    else:
        return None
    public = MappingProxyType({key: raw[key] for key in
                               ("index", "ordinal", "time", "kind", "value", "available_at", "label") if key in raw})
    return _Reference(public, index, ordinal, "H" if kind == "H" else "L", Fraction(str(value)), available, known)


@dataclass(frozen=True)
class ConfirmationContext:
    """Immutable parsing snapshot reused only within one path publication.

    The supplied source and market prefix must stay unchanged for that call.
    Identity bindings prevent accidentally reusing another path or clock; this
    snapshot is not a cache of qualification results or a cross-day memo.
    """

    source_identity: int
    bars_identity: int
    end_index: int
    bars: tuple[Bar, ...] | None
    session_indices: Mapping[str, int] | None
    references: tuple[_Reference | None, ...]
    ordered: bool
    n_source: Sequence[Mapping[str, object]]
    n_context: NConfirmationContext | None
    n_target_trend_confirmation_enabled: bool


def prepare_confirmation_context(
    source: Sequence[Mapping[str, object]], bars: Sequence[Bar] | None, end_index: int,
    *, n_source: Sequence[Mapping[str, object]] | None = None,
    n_target_trend_confirmation_enabled: bool = False,
) -> ConfirmationContext:
    """Parse one immutable source/clock snapshot without looking past cutoff."""
    if type(end_index) is not int or end_index < 0 or bars is not None and end_index >= len(bars):
        raise ValueError("a confirmation context requires an available market prefix")
    market = tuple(bars[:end_index + 1]) if bars is not None else None
    sessions = (MappingProxyType({session_date(bar): index for index, bar in enumerate(market)})
                if market is not None else None)
    references = tuple(_reference(point, sessions, end_index) for point in source)
    known = [point for point in references if point is not None]
    ordered = (not any(left.order >= right.order for left, right in zip(known, known[1:]))
               and not any(left.known is not None and right.known is not None and left.known > right.known
                           for left, right in zip(known, known[1:])))
    n_points = source if n_source is None else n_source
    n_context = (prepare_n_confirmation_context(n_points, bars, end_index)
                 if bars is not None and n_target_trend_confirmation_enabled else None)
    return ConfirmationContext(id(source), id(bars), end_index, market, sessions, references, ordered,
                               n_points, n_context, n_target_trend_confirmation_enabled)


def _public(point: _Reference) -> dict[str, object]:
    return {key: point.raw[key] for key in ("index", "ordinal", "time", "kind", "value", "available_at", "label")
            if key in point.raw}


def _known_before_price(key: _Reference, point: _Reference) -> bool:
    if key.known is not None:
        return key.known < point.index
    time = point.raw.get("time")
    return isinstance(key.available, str) and isinstance(time, str) and key.available < time


def _bar_reference(bars: Sequence[Bar], index: int, up: bool, integer_clock: bool) -> dict[str, object]:
    bar = bars[index]
    return dict(index=index, ordinal=0, time=session_date(bar), kind="H" if up else "L",
                value=bar.high if up else bar.low, available_at=index if integer_clock else session_date(bar),
                label="当前高点" if up else "当前低点")


def _turn_reference(bars: Sequence[Bar], index: int, up: bool, integer_clock: bool) -> dict[str, object]:
    bar = bars[index]
    return dict(index=index, ordinal=0, time=session_date(bar), kind="K", value=bar.close,
                available_at=index if integer_clock else session_date(bar), label="转多" if up else "转空",
                close=bar.close, market_high=bar.high, market_low=bar.low)


def _certificate(
    origin: _Reference, key: _Reference, confirmed: dict[str, object], up: bool,
    rule: str, *, flip: _Reference | None = None, counter: _Reference | None = None,
    retracement_origin: _Reference | None = None,
) -> dict[str, object]:
    proof: dict[str, object] = dict(
        available_at=confirmed["available_at"], confirmation_rule=rule, direction="up" if up else "down",
        broken_key=_public(key), origin=_public(origin), confirmed_by=confirmed,
    )
    proof["flip_high" if up else "flip_low"] = _public(flip) if flip is not None else confirmed
    proof["alternation_low" if up else "alternation_high"] = _public(counter) if counter is not None else None
    if retracement_origin is not None:
        proof["retracement_origin"] = _public(retracement_origin)
    return proof


def _qualify(
    source: Sequence[Mapping[str, object]], origin: Mapping[str, object], source_position: int,
    same_level_key: Mapping[str, object] | None, bars: Sequence[Bar] | None, end_index: int, *, up: bool,
    context: ConfirmationContext | None = None,
    n_source: Sequence[Mapping[str, object]] | None = None,
    n_target_trend_confirmation_enabled: bool = False,
) -> dict[str, object] | None:
    if (type(end_index) is not int or end_index < 0 or type(source_position) is not int
            or not 0 <= source_position < len(source) or bars is not None and end_index >= len(bars)):
        return None
    prepared = context if context is not None else prepare_confirmation_context(
        source, bars, end_index, n_source=n_source,
        n_target_trend_confirmation_enabled=n_target_trend_confirmation_enabled,
    )
    if (prepared.source_identity != id(source) or prepared.bars_identity != id(bars)
            or prepared.end_index != end_index or id(prepared.n_source) != id(source if n_source is None else n_source)
            or prepared.n_target_trend_confirmation_enabled != n_target_trend_confirmation_enabled):
        raise ValueError("a confirmation context must match its source and market prefix")
    supplied = prepared.references[source_position]
    base = supplied if origin is source[source_position] else _reference(origin, prepared.session_indices, end_index)
    if (base is None or supplied is None or base.kind != ("L" if up else "H")
            or (base.order, base.value) != (supplied.order, supplied.value)):
        return None
    references = prepared.references
    if not prepared.ordered:
        return None
    key_kind = "H" if up else "L"
    sign = 1 if up else -1
    own_key = _reference(same_level_key, prepared.session_indices, end_index) if same_level_key is not None else None
    if own_key is not None and (own_key.kind != key_kind or own_key.order >= base.order
                                or sign * (own_key.value - base.value) <= 0):
        own_key = None
    market = prepared.bars
    if market is None:
        if own_key is None:
            return None
        for point in references[source_position + 1:]:
            if point is None:
                continue
            if point.kind == base.kind and sign * (point.value - base.value) < 0:
                return None
            if (point.kind == key_kind and sign * (point.value - own_key.value) > 0
                    and _known_before_price(own_key, point)):
                return _certificate(base, own_key, _public(point), up, SAME_LEVEL_KEY_BREAK, flip=point)
        return None
    assert base.known is not None
    base_price = float(base.value)
    failed = next((index for index in range(base.index + 1, end_index + 1)
                   if sign * ((market[index].low if up else market[index].high) - base_price) < 0),
                  end_index + 1)
    proofs: list[tuple[int, dict[str, object]]] = []
    if own_key is not None:
        assert own_key.known is not None
        own_price = float(own_key.value)
        for index in range(max(base.index + 1, base.known, own_key.known + 1), min(failed, end_index + 1)):
            extreme = market[index].high if up else market[index].low
            if sign * (extreme - own_price) > 0:
                confirmed = _bar_reference(market, index, up, isinstance(base.available, int))
                proofs.append((index, _certificate(base, own_key, confirmed, up, SAME_LEVEL_KEY_BREAK)))
                break
    n_proof = (qualify_n_trend(prepared.n_source, prepared.n_source[source_position], source_position,
                              bars, end_index, up=up, context=prepared.n_context)
               if n_target_trend_confirmation_enabled else None)
    if n_proof is not None:
        trigger = n_proof['confirmed_by']
        assert isinstance(trigger, dict) and isinstance(trigger['index'], int)
        proofs.append((trigger['index'], n_proof))
    frozen = next((point for point in reversed(references[:source_position])
                   if point is not None and point.kind == key_kind), None)
    if frozen is None or sign * (frozen.value - base.value) <= 0:
        return min(proofs, key=lambda item: item[0])[1] if proofs else None
    flips: list[tuple[int, _Reference, _Reference]] = []
    strongest: _Reference | None = None
    causal_limit = min(failed, min((proof[0] for proof in proofs), default=end_index + 1))
    for position in range(source_position + 1, len(source)):
        point = references[position]
        # Known times are monotone; these cycles cannot precede failure or the first own-key proof.
        if point is not None and point.known is not None and point.known >= causal_limit:
            break
        if (point is None or point.kind != key_kind or sign * (point.value - frozen.value) <= 0
                or not _known_before_price(frozen, point)
                or strongest is not None and sign * (point.value - strongest.value) <= 0):
            continue
        retracement_origin = (next((prior for prior in reversed(references[source_position:position])
                                   if prior is not None and prior.kind == base.kind), base)
                              if strongest is not None else base)
        strongest = point
        flips.append((position, point, retracement_origin))
    for cycle, (position, flip, retracement_origin) in enumerate(flips):
        counter = references[position + 1] if position + 1 < len(references) else None
        if counter is None or counter.kind != base.kind or not flip.order < counter.order:
            continue
        impulse = sign * (flip.value - retracement_origin.value)
        retracement = sign * (flip.value - counter.value)
        if impulse <= 0 or not 0 < retracement < impulse or retracement / impulse >= Fraction(2, 3):
            continue
        assert flip.known is not None and counter.known is not None and retracement_origin.known is not None
        start = max(base.known, flip.known, counter.known, retracement_origin.known) + 1
        next_known = flips[cycle + 1][1].known if cycle + 1 < len(flips) else end_index + 1
        assert next_known is not None
        limit = min(failed, next_known, end_index + 1)
        counter_price, flip_price = float(counter.value), float(flip.value)
        for index in range(counter.index + 1, limit):
            adverse = market[index].low if up else market[index].high
            if sign * (adverse - counter_price) < 0:
                break
            if index < start:
                continue
            previous, closing = market[index - 1].close, market[index].close
            if sign * (previous - flip_price) <= 0 and sign * (closing - flip_price) > 0:
                confirmed = _turn_reference(market, index, up, isinstance(base.available, int))
                proof = _certificate(base, frozen, confirmed, up, SOURCE_CYCLE_CONFIRMATION,
                                     flip=flip, counter=counter, retracement_origin=retracement_origin)
                proof.update(retracement_ratio=float(retracement / impulse),
                             source_break_available_at=flip.available, alternation_available_at=counter.available)
                proofs.append((index, proof))
                # Later cycles start after this cycle's exclusive next-known boundary.
                return min(proofs, key=lambda item: item[0])[1]
    return min(proofs, key=lambda item: item[0])[1] if proofs else None


def qualify_uptrend(
    source: Sequence[Mapping[str, object]], origin: Mapping[str, object], source_position: int,
    same_level_key: Mapping[str, object] | None, bars: Sequence[Bar] | None, end_index: int, *,
    context: ConfirmationContext | None = None,
    n_source: Sequence[Mapping[str, object]] | None = None,
    n_target_trend_confirmation_enabled: bool = False,
) -> dict[str, object] | None:
    """Certify the first own-key break, source cycle or strict N target break.

    Raw hierarchy geometry does not supply permission. Higher source attacks
    may retry a failed pullback with a fresh retracement origin, while the
    original defense and source key remain frozen. Later failures cannot erase
    an earlier certificate. Without bars only a confirmed source own-key break
    can qualify; date-clock inputs must already be a known source prefix.
    """
    return _qualify(source, origin, source_position, same_level_key, bars, end_index, up=True,
                    context=context, n_source=n_source,
                    n_target_trend_confirmation_enabled=n_target_trend_confirmation_enabled)


def qualify_downtrend(
    source: Sequence[Mapping[str, object]], origin: Mapping[str, object], source_position: int,
    same_level_key: Mapping[str, object] | None, bars: Sequence[Bar] | None, end_index: int, *,
    context: ConfirmationContext | None = None,
    n_source: Sequence[Mapping[str, object]] | None = None,
    n_target_trend_confirmation_enabled: bool = False,
) -> dict[str, object] | None:
    """Mirror the same three certificates for a downward direction."""
    return _qualify(source, origin, source_position, same_level_key, bars, end_index, up=False,
                    context=context, n_source=n_source,
                    n_target_trend_confirmation_enabled=n_target_trend_confirmation_enabled)
