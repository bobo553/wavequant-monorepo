"""Causal N-formation rules and measurements built from price-action primitives.

Known A-B-C pivot references are supplied by the structural layer. This module
does not select pivots, impose retracement/volume/trade filters, or count waves
retrospectively. Figure interpretations and timing rules: docs/n_shape_contract.md.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, localcontext
from enum import Enum
import math
from typing import Sequence

from ..models.model import Bar
from .price_action import Direction, KeyLevel, LevelKind, observe_attack, _ordered_pair, _validate_bar


class NStatus(str, Enum):
    AWAIT_ANCHORS = 'await_anchors'
    FORMING = 'forming'
    INVALIDATED = 'origin_breached_before_completion'
    PULLBACK_EXTENDED = 'pullback_extended_new_anchor_required'
    COMPLETED = 'completed'


class BoxAnchorMode(str, Enum):
    ATTACK_VIRTUAL_EXTREME = 'attack_virtual_extreme'
    NECKLINE_EXTREME = 'neckline_extreme_explicit_alternative'


class MilestoneBasis(str, Enum):
    EXTREME = 'extreme'
    CLOSE = 'close'


class ValueDomain(str, Enum):
    PRICE = 'price'
    INDICATOR = 'indicator'


def _index(value, name):
    if type(value) is not int or value < 0:
        raise ValueError(f'{name} must be a non-negative integer')


def _finite(value, name):
    if type(value) not in (int,float) or not math.isfinite(value):
        raise ValueError(f'{name} must be finite')


@dataclass(frozen=True)
class PivotRef:
    index: int
    confirmed_index: int

    def __post_init__(self):
        _index(self.index,'pivot index')
        _index(self.confirmed_index,'pivot confirmation')
        if self.confirmed_index < self.index:
            raise ValueError('pivot confirmation cannot precede its source')


@dataclass(frozen=True)
class NSetup:
    symbol: str
    timeframe: str
    direction: Direction
    origin: PivotRef
    neckline: PivotRef
    pullback: PivotRef
    source: str
    # No default: the caller must acknowledge the box-anchor interpretation.
    box_anchor_mode: BoxAnchorMode
    allow_confirmation_bar: bool = False

    def __post_init__(self):
        if type(self.allow_confirmation_bar) is not bool:
            raise ValueError('allow_confirmation_bar must be boolean')
        for name in ('symbol','timeframe','source'):
            if not isinstance(getattr(self,name),str) or not getattr(self,name).strip():
                raise ValueError(f'{name} is required')
        if not isinstance(self.direction,Direction) or not isinstance(self.box_anchor_mode,BoxAnchorMode):
            raise ValueError('direction and box mode must be enums')
        refs = (self.origin,self.neckline,self.pullback)
        if not all(isinstance(p,PivotRef) for p in refs):
            raise ValueError('all three pivots require source and confirmation indices')
        if not self.origin.index < self.neckline.index < self.pullback.index:
            raise ValueError('A, B, C must be strictly chronological')
        if not self.origin.confirmed_index <= self.neckline.confirmed_index <= self.pullback.confirmed_index:
            raise ValueError('pivot confirmations must be chronological')


@dataclass(frozen=True)
class NAnchors:
    origin: float
    neckline_extreme: float
    neckline_close: float
    pullback: float
    known_at_index: int
    retracement_ratio: float


@dataclass(frozen=True)
class NCompletion:
    bar_index: int
    observed_at: datetime
    real_break: bool
    virtual_break: bool
    virtual_low: float
    virtual_high: float
    defense: float
    defense_name: str


@dataclass(frozen=True)
class NTargets:
    equal_wave: float | None
    one_p: float | None
    two_t: float | None
    box_anchor: float
    domain: ValueDomain
    unavailable: tuple[str,...]


@dataclass(frozen=True)
class Milestone:
    name: str
    target: float
    bar_index: int
    observed_at: datetime
    observation_kind: str
    # Several levels may be observed on one bar; this is not intrabar execution.
    shared_bar_with_previous: bool


@dataclass(frozen=True)
class NObservation:
    status: NStatus
    asof_index: int
    observed_at: datetime
    anchors: NAnchors | None = None
    real_break_now: bool | None = None
    virtual_break_now: bool | None = None
    invalidated_index: int | None = None
    completion: NCompletion | None = None
    targets: NTargets | None = None
    milestones: tuple[Milestone,...] = ()
    next_target: str | None = None
    first_defense_breach_index: int | None = None
    first_origin_breach_index: int | None = None
    measured_wave_complete: bool = False
    stacking_precondition_observed: bool = False
    stacking_rule: str = 'undefined_no_automatic_five_or_ten_targets'


def project_n_targets(origin: float, neckline: float, pullback: float, *,
                      box_anchor: float, direction: Direction,
                      domain: ValueDomain) -> NTargets:
    """Equal=C+B-A; 1P=2X-A; 2T=3X-2A. X is a separate box anchor.

    Mathematical projection supports signed indicators; PRICE rejects nonpositive
    inputs and marks nonpositive projected targets unavailable (not a bottom).
    """
    if not isinstance(direction,Direction) or not isinstance(domain,ValueDomain):
        raise ValueError('direction/domain must be enums')
    for name,value in (('origin',origin),('neckline',neckline),('pullback',pullback),('box_anchor',box_anchor)):
        _finite(value,name)
        if domain == ValueDomain.PRICE and value <= 0:
            raise ValueError('price anchors must be positive')
    sign = 1 if direction == Direction.UP else -1
    if not 0 < sign*(pullback-origin) < sign*(neckline-origin):
        raise ValueError('standard N requires a positive impulse and a partial retracement')
    if sign*(box_anchor-neckline) < 0:
        raise ValueError('box anchor must be at or beyond the neckline in attack direction')
    # Preserve decimal arithmetic of quoted anchors; e.g. 3*12.6-2*8 is 21.8,
    # not a binary rounding artifact that changes a target-equality observation.
    with localcontext() as context:
        context.prec = 40
        aa,bb,cc,xx = (Decimal(str(v)) for v in (origin,neckline,pullback,box_anchor))
        raw = tuple(float(v) for v in (cc+bb-aa,2*xx-aa,3*xx-2*aa))
    for target in raw:
        _finite(target,'projected target')
    names = ('equal_wave','one_p','two_t')
    unavailable = tuple(name for name,value in zip(names,raw) if domain==ValueDomain.PRICE and value<=0)
    values = tuple(None if name in unavailable else value for name,value in zip(names,raw))
    return NTargets(*values,box_anchor,domain,unavailable)


def observe_n(bars: Sequence[Bar], setup: NSetup, *, timeframe: str,
              milestone_basis: MilestoneBasis, asof_index: int | None = None) -> NObservation:
    """Earliest same-bar real+virtual completion, never backdated to unknown pivots.

    A/C are lows and B a high for positive N, symmetric for inverse N. Real
    threshold is B-bar close; virtual threshold is B-bar extreme (figure 003).
    Completion-bar targets are credited only by its CLOSE, because earlier high/
    low may predate formation. Later-bar extrema are allowed only if requested.
    """
    if not isinstance(setup,NSetup) or timeframe != setup.timeframe:
        raise ValueError('NSetup with matching timeframe required')
    if not isinstance(milestone_basis,MilestoneBasis):
        raise ValueError('milestone_basis must be explicit')
    end = len(bars)-1 if asof_index is None else asof_index
    _index(end,'asof_index')
    if end >= len(bars):
        raise ValueError('as-of bar is unavailable')
    # Only inspect the visible prefix, never validate against future prices.
    from ..models.validated_bars import ValidatedBars
    if isinstance(bars, ValidatedBars) and bars[0].symbol != setup.symbol:
        raise ValueError('setup and bar symbols differ')
    for i in range(0 if isinstance(bars, ValidatedBars) else end+1):
        _validate_bar(bars[i])
        if bars[i].symbol != setup.symbol:
            raise ValueError('setup and bar symbols differ')
        if i:
            _ordered_pair(bars[i-1],bars[i])
    known = setup.pullback.confirmed_index
    when = bars[end].timestamp
    if known > end:
        return NObservation(NStatus.AWAIT_ANCHORS,end,when)
    up = setup.direction == Direction.UP
    sign = 1 if up else -1
    a = bars[setup.origin.index].low if up else bars[setup.origin.index].high
    bb = bars[setup.neckline.index]
    b = bb.high if up else bb.low
    c = bars[setup.pullback.index].low if up else bars[setup.pullback.index].high
    # Validate the explicit standard A-B-C geometry; no arbitrary 1/2 filter.
    project_n_targets(a,b,c,box_anchor=b,direction=setup.direction,domain=ValueDomain.PRICE)
    anchors = NAnchors(a,b,bb.close,c,known,(b-c)/(b-a))
    kind = LevelKind.RESISTANCE if up else LevelKind.SUPPORT
    # B can already be known when today's close confirms a historical C.
    # This is a close-time observation, never an intraday order using future C.
    same_confirmation = (setup.allow_confirmation_bar and setup.pullback.index < known
                         and setup.neckline.confirmed_index < known)
    key_known = setup.neckline.confirmed_index if same_confirmation else known
    real_key = KeyLevel(setup.symbol,timeframe,kind,bb.close,setup.neckline.index,key_known,setup.source)
    virtual_key = KeyLevel(setup.symbol,timeframe,kind,b,setup.neckline.index,key_known,setup.source)
    # Validate supplied pivot prices against the visible structural legs. We do
    # not choose alternative pivots or invent an intrabar order on the B candle.
    adverse_value = lambda bar: bar.low if up else bar.high
    forward_value = lambda bar: bar.high if up else bar.low
    if any(sign*(adverse_value(bars[i])-a)<0 for i in range(setup.origin.index,setup.pullback.index+1)):
        raise ValueError('origin is not a valid boundary of the supplied structure')
    # The lecture path starts at A's terminal low/high. Its opposite wick
    # belongs to the preceding leg, not the subsequent A-B impulse.
    neckline_start = setup.origin.index + (setup.source == 'lecture_causal')
    if any(sign*(forward_value(bars[i])-b)>0 for i in range(neckline_start,setup.pullback.index+1)):
        raise ValueError('neckline is not the extreme of the supplied A-B-C structure')
    if any(sign*(adverse_value(bars[i])-c)<0 for i in range(setup.neckline.index+1,setup.pullback.index+1)):
        raise ValueError('C is not the pullback extreme of the supplied structure')
    # C must remain valid until confirmed. Later extension requires a new setup,
    # not silent mutation of C and its equal-wave target.
    for i in range(setup.pullback.index+1,known+1):
        adverse = bars[i].low if up else bars[i].high
        if sign*(adverse-c) < 0:
            raise ValueError('supplied pullback pivot was exceeded before its confirmation')
    completion = None
    for i in range(known if same_confirmation else known+1,end+1):
        bar = bars[i]
        adverse = bar.low if up else bar.high
        if sign*(adverse-a) < 0:
            return NObservation(NStatus.INVALIDATED,end,when,anchors,invalidated_index=i)
        if sign*(adverse-c) < 0:
            return NObservation(NStatus.PULLBACK_EXTENDED,end,when,anchors,invalidated_index=i)
        extreme = bar.high if up else bar.low
        if sign*(bar.close-bb.close) <= 0 or sign*(extreme-b) <= 0:
            continue
        prev = bars[i-1]
        previous_joint = (sign*(prev.close-bb.close)>0 and
                          sign*((prev.high if up else prev.low)-b)>0)
        if previous_joint:
            continue
        real = observe_attack(bars,i,real_key,timeframe=timeframe)
        virtual = observe_attack(bars,i,virtual_key,timeframe=timeframe)
        # Occupancy beyond both levels alone must not create a belated attack.
        if not (real.close_crossed or virtual.intrabar_crossed):
            continue
        vl, vh = min(bar.low,bars[i-1].close),max(bar.high,bars[i-1].close)
        completion = NCompletion(i,bar.timestamp,True,True,vl,vh,vl if up else vh,
                                 '轧空低' if up else '杀多高')
        break
    if completion is None:
        bar = bars[end]
        return NObservation(NStatus.FORMING,end,when,anchors,
                            real_break_now=sign*(bar.close-bb.close)>0,
                            virtual_break_now=sign*((bar.high if up else bar.low)-b)>0)
    box = ((completion.virtual_high if up else completion.virtual_low)
           if setup.box_anchor_mode == BoxAnchorMode.ATTACK_VIRTUAL_EXTREME else b)
    targets = project_n_targets(a,b,c,box_anchor=box,direction=setup.direction,domain=ValueDomain.PRICE)
    hit, defense_breach, origin_breach = [], None, None
    names = ('equal_wave','one_p','two_t')
    for i in range(completion.bar_index,end+1):
        bar = bars[i]
        formation_bar = i == completion.bar_index
        # Do not compare the newly created defense with an earlier low/high on
        # the formation bar. Future defense breaches are observations, not exits.
        if not formation_bar:
            adverse = bar.low if up else bar.high
            if defense_breach is None and sign*(adverse-completion.defense)<0:
                defense_breach = i
            if origin_breach is None and sign*(adverse-a)<0:
                origin_breach = i
        # An origin break suspends further measurements of this same wave. If a
        # daily bar also reaches a target, do not invent which happened first.
        if origin_breach is not None:
            continue
        use_close = formation_bar or milestone_basis == MilestoneBasis.CLOSE
        price = bar.close if use_close else bar.high if up else bar.low
        while len(hit)<3:
            name = names[len(hit)]
            target = getattr(targets,name)
            if target is None or sign*(price-target)<0:
                break
            hit.append(Milestone(name,target,i,bar.timestamp,
                       'completion_close' if formation_bar else milestone_basis.value,
                       bool(hit and hit[-1].bar_index==i)))
    return NObservation(NStatus.COMPLETED,end,when,anchors,completion=completion,targets=targets,
                        milestones=tuple(hit),next_target=names[len(hit)] if len(hit)<3 else None,
                        first_defense_breach_index=defense_breach,first_origin_breach_index=origin_breach,
                        measured_wave_complete=len(hit)==3,stacking_precondition_observed=len(hit)==3)
