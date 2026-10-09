"""Certify a trend after a known standard N strictly exceeds its frozen one-p."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from fractions import Fraction
from math import isfinite
from types import MappingProxyType

from ..models.model import Bar
from ..models.validated_bars import ValidatedBars
from .hierarchical_confirmation import session_date
from .n_shape import BoxAnchorMode, MilestoneBasis, NObservation, NSetup, PivotRef, observe_n
from .price_action import Direction, teaching_inside


N_TARGET_CONFIRMATION = "source_n_strict_one_p_target"


def is_n_target_reversal(point: Mapping[str, object]) -> bool:
    """Identify candidate vertices introduced by the optional N trend route."""
    return point.get("confirmation_rule") in (N_TARGET_CONFIRMATION, "n_origin_breach_confirms_wave_endpoint")


def _source_identity(point: Mapping[str, object]) -> tuple[int, int, str, Fraction] | None:
    index, ordinal, kind, value = point.get("index"), point.get("ordinal", 0), point.get("kind"), point.get("value")
    if (type(index) is not int or index < 0 or type(ordinal) is not int or ordinal < 0
            or kind not in ("H", "L") or not isinstance(value, (int, float)) or isinstance(value, bool)
            or not isfinite(value) or value <= 0 or point.get("display_only") is True
            or point.get("state", "confirmed") not in ("confirmed", "reversal", "teaching")):
        return None
    return index, ordinal, str(kind), Fraction(str(value))


def _publication_clock(point: Mapping[str, object]) -> int | str | None:
    known = point.get("available_at")
    if type(known) is int:
        index = point.get("index")
        return known if type(index) is int and known >= index else None
    if isinstance(known, str):
        try:
            if date.fromisoformat(known).isoformat() != known:
                return None
        except ValueError:
            return None
        time = point.get("time")
        return known if not isinstance(time, str) or known >= time else None
    return None


def qualified_n_source(
    source: Sequence[Mapping[str, object]], qualified_source: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    """Keep source positions while restricting N anchors to a public source path.

    Structural candidates remain available to the older direction routes. Only
    this returned snapshot masks vertices absent from the actual public source
    polyline, and observes both structural and formal publication clocks.
    """
    indexed: dict[tuple[int, int, str, Fraction], int] = {}
    dated: dict[tuple[int, int, str, Fraction], str] = {}
    for point in qualified_source:
        identity, known = _source_identity(point), _publication_clock(point)
        if identity is None:
            continue
        if type(known) is int:
            indexed[identity] = min(indexed.get(identity, known), known)
        elif isinstance(known, str):
            dated[identity] = min(dated.get(identity, known), known)
    result: list[dict[str, object]] = []
    for point in source:
        copy = dict(point)
        identity, known = _source_identity(point), _publication_clock(point)
        formal: int | str | None = None
        if identity is not None and type(known) is int:
            formal = indexed.get(identity)
        elif identity is not None and isinstance(known, str):
            formal = dated.get(identity)
        if type(known) is int and type(formal) is int:
            copy["available_at"] = max(known, formal)
        elif isinstance(known, str) and isinstance(formal, str):
            copy["available_at"] = max(known, formal)
        else:
            copy["state"] = "unqualified"
        result.append(copy)
    return result


@dataclass(frozen=True)
class _Anchor:
    raw: Mapping[str, object]
    index: int
    ordinal: int
    kind: str
    value: Fraction
    known: int
    integer_clock: bool

    @property
    def order(self) -> tuple[int, int]:
        return self.index, self.ordinal


@dataclass(frozen=True)
class NConfirmationContext:
    """One immutable source and market prefix, reused within one publication.

    Identity bindings prevent reuse with another path or clock. This is a
    parsing and validation snapshot, not a result cache across market days.
    """

    source_identity: int
    bars_identity: int
    end_index: int
    market: Sequence[Bar]
    sessions: Mapping[str, int]
    references: tuple[_Anchor | None, ...]
    ordered: bool
    low_failures: tuple[int, ...]
    high_failures: tuple[int, ...]


def _first_origin_failures(bars: Sequence[Bar], *, up: bool) -> tuple[int, ...]:
    """Locate each candle's next strict adverse extreme in linear time."""
    pending: list[int] = []
    failures = [len(bars)] * len(bars)
    sign = 1 if up else -1
    for index in range(len(bars) - 1, -1, -1):
        value = bars[index].low if up else bars[index].high
        while pending:
            future = bars[pending[-1]].low if up else bars[pending[-1]].high
            if sign * (future - value) < 0:
                break
            pending.pop()
        if pending:
            failures[index] = pending[-1]
        pending.append(index)
    return tuple(failures)


def prepare_n_confirmation_context(
    source: Sequence[Mapping[str, object]], bars: Sequence[Bar], end_index: int,
) -> NConfirmationContext:
    """Parse and validate one known prefix without inspecting later bars."""
    if type(end_index) is not int or not 0 <= end_index < len(bars):
        raise ValueError("an N confirmation context requires an available market prefix")
    prefix = bars[:end_index + 1]
    market: Sequence[Bar] = prefix if isinstance(prefix, ValidatedBars) else ValidatedBars(prefix)
    sessions = MappingProxyType({session_date(bar): index for index, bar in enumerate(market)})
    references = tuple(_anchor(MappingProxyType(dict(point)), market, sessions, end_index) for point in source)
    known = [point for point in references if point is not None]
    ordered = not any(left.order >= right.order or left.known > right.known for left, right in zip(known, known[1:]))
    return NConfirmationContext(id(source), id(bars), end_index, market, sessions, references, ordered,
                                _first_origin_failures(market, up=True), _first_origin_failures(market, up=False))


def _anchor(
    raw: Mapping[str, object], bars: Sequence[Bar], sessions: Mapping[str, int], end_index: int,
) -> _Anchor | None:
    index, ordinal = raw.get("index"), raw.get("ordinal", 0)
    value, kind, available = raw.get("value"), raw.get("kind"), raw.get("available_at")
    if (type(index) is not int or not 0 <= index <= end_index or type(ordinal) is not int or ordinal < 0
            or kind not in ("H", "L") or not isinstance(value, (int, float)) or isinstance(value, bool)
            or not isfinite(value) or value <= 0 or raw.get("display_only") is True
            or raw.get("state", "confirmed") not in ("confirmed", "reversal", "teaching")):
        return None
    if type(available) is int:
        known = available
    elif isinstance(available, str):
        try:
            if date.fromisoformat(available).isoformat() != available:
                return None
        except ValueError:
            return None
        known = sessions.get(available, -1)
    else:
        return None
    if not index <= known <= end_index:
        return None
    time = raw.get("time")
    if time is not None and time != session_date(bars[index]):
        return None
    price = Fraction(str(value))
    observed = bars[index].high if kind == "H" else bars[index].low
    if price != Fraction(str(observed)):
        return None
    return _Anchor(raw, index, ordinal, str(kind), price, known, type(available) is int)


def _public(anchor: _Anchor, bars: Sequence[Bar]) -> dict[str, object]:
    point = {key: anchor.raw[key] for key in ("index", "ordinal", "time", "kind", "value", "available_at", "label")
             if key in anchor.raw}
    point.setdefault("ordinal", anchor.ordinal)
    point.setdefault("time", session_date(bars[anchor.index]))
    point.setdefault("label", "N 起点" if anchor.kind == "L" else "N 高点")
    return point


def _moment(index: int, bars: Sequence[Bar], integer_clock: bool) -> str | int:
    return index if integer_clock else session_date(bars[index])


def _setup(a: _Anchor, b: _Anchor, c: _Anchor, bars: Sequence[Bar], up: bool) -> NSetup | None:
    """Same-session anchors require the existing lecture candle ordering proof."""
    if not a.order < b.order < c.order or not a.known <= b.known <= c.known:
        return None
    if len({a.integer_clock, b.integer_clock, c.integer_clock}) != 1:
        return None
    sign = 1 if up else -1
    if not 0 < sign * (c.value - a.value) < sign * (b.value - a.value):
        return None
    mother_impulse = mother_pullback = inside_pullback = False
    if a.index == b.index:
        candle = bars[a.index]
        mother_impulse = (up and a.index > 0 and candle.close > candle.open
                          and candle.high > bars[a.index - 1].high and candle.low < bars[a.index - 1].low)
        if not mother_impulse:
            return None
    if b.index == c.index:
        candle = bars[b.index]
        if not up or b.index == 0 or candle.close >= candle.open:
            return None
        previous = bars[b.index - 1]
        mother_pullback = candle.high > previous.high and candle.low < previous.low
        inside_pullback = (not mother_pullback and previous.close != previous.open
                           and teaching_inside(previous, candle))
        if not mother_pullback and not inside_pullback:
            return None
    return NSetup(
        bars[a.index].symbol, "1d", Direction.UP if up else Direction.DOWN,
        PivotRef(a.index, a.known), PivotRef(b.index, b.known), PivotRef(c.index, c.known),
        "lecture_causal", BoxAnchorMode.ATTACK_VIRTUAL_EXTREME,
        allow_confirmation_bar=True, allow_outside_close=True, allow_mother_impulse=mother_impulse,
        staged_defense=True, allow_mother_pullback=mother_pullback, allow_inside_pullback=inside_pullback,
    )


def _observation(setup: NSetup, bars: Sequence[Bar], end_index: int) -> NObservation | None:
    try:
        return observe_n(bars, setup, timeframe="1d", milestone_basis=MilestoneBasis.EXTREME, asof_index=end_index)
    except ValueError:
        # Source folds can fail the N observer's whole-leg extrema contract.
        # Such a fold supplies no certificate; its anchors are never repaired.
        return None


def _target_proof(
    a: _Anchor, b: _Anchor, c: _Anchor, observation: NObservation, bars: Sequence[Bar], end_index: int, up: bool,
    target_sink: list[dict[str, object]] | None = None,
) -> tuple[int, dict[str, object]] | None:
    completion = observation.completion
    if completion is None:
        return None
    sign = 1 if up else -1
    box = Fraction(str(completion.virtual_high if up else completion.virtual_low))
    target = 2 * box - a.value
    if target <= 0:
        return None
    defense = Fraction(str(completion.defense))
    if target_sink is not None:
        target_sink.append(dict(direction='up' if up else 'down', one_p_target=float(target),
                                available_at=_moment(completion.bar_index, bars, a.integer_clock),
                                index=completion.bar_index, origin=float(a.value), defense=float(defense)))
    # A completion and target are close-time facts. The target cannot use an
    # earlier wick on the candle that first makes the N known.
    for index in range(completion.bar_index, end_index + 1):
        bar = bars[index]
        if index > completion.bar_index:
            adverse = Fraction(str(bar.low if up else bar.high))
            if sign * (adverse - a.value) < 0 or sign * (adverse - defense) < 0:
                return None
        closing = index == completion.bar_index
        observed = bar.close if closing else bar.high if up else bar.low
        if sign * (Fraction(str(observed)) - target) <= 0:
            continue
        known = _moment(index, bars, a.integer_clock)
        trigger = dict(index=index, ordinal=0, time=session_date(bar), kind="K" if closing else "H" if up else "L",
                       value=observed, available_at=known, label="正 N 严格超一饱" if up else "倒 N 严格超一饱",
                       close=bar.close, market_high=bar.high, market_low=bar.low)
        attack_index = completion.bar_index
        attack_bar = bars[attack_index]
        attack = dict(index=attack_index, ordinal=0, time=session_date(attack_bar), kind="H" if up else "L",
                      value=float(box), available_at=_moment(attack_index, bars, a.integer_clock),
                      label="正 N 完成" if up else "倒 N 完成", close=attack_bar.close,
                      market_high=attack_bar.high, market_low=attack_bar.low,
                      virtual_high=completion.virtual_high, virtual_low=completion.virtual_low,
                      real_break=completion.real_break, virtual_break=completion.virtual_break)
        return index, dict(
            available_at=known, confirmation_rule=N_TARGET_CONFIRMATION, direction="up" if up else "down",
            broken_key=_public(b, bars), origin=_public(a, bars), confirmed_by=trigger,
            n_neckline=_public(b, bars), n_pullback=_public(c, bars), n_completion=attack,
            one_p_target=float(target), box_anchor=float(box), defense=float(defense),
            target_basis="completion_close" if closing else "market_extreme",
        )
    return None


def qualify_n_trend(
    source: Sequence[Mapping[str, object]], origin: Mapping[str, object], source_position: int,
    bars: Sequence[Bar] | None, end_index: int, *, up: bool,
    context: NConfirmationContext | None = None,
    target_sink: list[dict[str, object]] | None = None,
) -> dict[str, object] | None:
    """Return the first strict target proof for an N starting at the real origin.

    Each neckline freezes its first completed N and defense. A later pullback
    cannot revive that N or replace its box. Candidates preceding completion may
    select a newly confirmed C, while another genuine neckline is a separate N.
    Only known source vertices and the supplied market prefix are inspected.
    """
    if (bars is None or type(end_index) is not int or not 0 <= end_index < len(bars)
            or type(source_position) is not int or not 0 <= source_position < len(source)):
        return None
    if context is None:
        try:
            prepared = prepare_n_confirmation_context(source, bars, end_index)
        except ValueError:
            return None
    else:
        prepared = context
    if (prepared.source_identity != id(source) or prepared.bars_identity != id(bars)
            or prepared.end_index != end_index):
        raise ValueError("an N confirmation context must match its source and market prefix")
    market, sessions, references = prepared.market, prepared.sessions, prepared.references
    supplied = references[source_position]
    base = supplied if origin is source[source_position] else _anchor(origin, market, sessions, end_index)
    if (base is None or supplied is None or base.kind != ("L" if up else "H")
            or (base.order, base.value, base.known, base.integer_clock) != (
                supplied.order, supplied.value, supplied.known, supplied.integer_clock)):
        return None
    if not prepared.ordered:
        return None
    sign, neckline_kind = (1, "H") if up else (-1, "L")
    proofs: list[tuple[int, dict[str, object]]] = []
    failed = (prepared.low_failures if up else prepared.high_failures)[base.index]
    search_end = min(end_index, failed - 1)
    for b_position in range(source_position + 1, len(references)):
        b = references[b_position]
        if b is None:
            continue
        if b.known > search_end:
            break
        if b.kind != neckline_kind or sign * (b.value - base.value) <= 0:
            continue
        first_completed: tuple[_Anchor, NObservation] | None = None
        completion_end = search_end
        for c in references[b_position + 1:]:
            if c is None:
                continue
            if c.known > completion_end:
                break
            if c.kind == neckline_kind and sign * (c.value - b.value) >= 0:
                break
            if c.kind != base.kind:
                continue
            if sign * (c.value - base.value) <= 0:
                break
            setup = _setup(base, b, c, market, up)
            if setup is None:
                continue
            observed = _observation(setup, market, completion_end)
            if observed is not None and observed.completion is not None:
                if first_completed is None or observed.completion.bar_index < completion_end:
                    first_completed = c, observed
                    completion_end = observed.completion.bar_index
        if first_completed is None:
            continue
        c, observed = first_completed
        proof = _target_proof(base, b, c, observed, market, search_end, up, target_sink)
        if proof is not None:
            proofs.append(proof)
            search_end = proof[0]
    return min(proofs, key=lambda proof: proof[0])[1] if proofs else None
