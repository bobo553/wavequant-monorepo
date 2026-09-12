"""Define lecture primitives with explicit anchors and observable timestamps.

These are evidence/annotation functions, not orders or a validated strategy.
Source: N-shape lecture, slides 3-35. Engineering choices are documented in
docs/n_foundations.md; no historical strategy defaults are changed here.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

from ..models.model import Bar

# Canonical low-level API for new consumers. Older helpers below remain lecture
# proxies for backwards compatibility, not the definitions of basic price action.


def _prices(*values: float) -> None:
    if any(isinstance(v, bool) or not math.isfinite(v) or v <= 0 for v in values):
        raise ValueError('prices must be finite and positive')


def _pair(current: Bar, previous: Bar) -> None:
    if current.symbol != previous.symbol or current.timestamp <= previous.timestamp:
        raise ValueError('same symbol and strictly later timestamp required')
    for b in (current, previous):
        _prices(b.open, b.high, b.low, b.close)
        if b.low > min(b.open, b.close) or b.high < max(b.open, b.close) or b.high < b.low:
            raise ValueError('inconsistent OHLC')


def bar_relations(current: Bar, previous: Bar) -> dict:
    """Exact inequalities from slide 16; equal highs/lows remain explicit."""
    _pair(current, previous)
    h, l = current.high, current.low
    return dict(virtual_low=min(l, previous.close), virtual_high=max(h, previous.close),
                shrinking_head=h < previous.high, lifting_foot=l > previous.low,
                extending_head=h > previous.high, falling_tail=l < previous.low,
                sunrise=h > previous.high and l > previous.low and current.close > previous.high,
                sunset=h < previous.high and l < previous.low and current.close < previous.low,
                inside=h < previous.high and l > previous.low,
                outside=h > previous.high and l < previous.low,
                equal_high=h == previous.high, equal_low=l == previous.low,
                requires_lower_timeframe=h < previous.high and l > previous.low or
                                         h > previous.high and l < previous.low)


def n_break_evidence(current: Bar, previous: Bar, *, direction: str,
                     neckline_close: float, neckline_extreme: float) -> dict:
    """Real and virtual breaks have separate anchors (slide 5, lower diagram).

    Caller must supply an already known N-neckline. This function does not assert
    that an arbitrary rolling high/low is an N formation or identify its wave.
    """
    r = bar_relations(current, previous)
    _prices(neckline_close, neckline_extreme)
    if direction == 'UP':
        if neckline_close > neckline_extreme:
            raise ValueError('UP neckline close must not exceed its high')
        real, virtual = current.close > neckline_close, current.high > neckline_extreme
        defense = r['virtual_low']
    elif direction == 'DOWN':
        if neckline_close < neckline_extreme:
            raise ValueError('DOWN neckline close must not be below its low')
        real, virtual = current.close < neckline_close, current.low < neckline_extreme
        defense = r['virtual_high']
    else:
        raise ValueError('direction must be UP or DOWN')
    return dict(direction=direction, real_break=real, virtual_break=virtual,
                completed=real and virtual, defense=defense if real and virtual else None)


def resistance_evidence(current: Bar, previous: Bar, *, direction: str,
                        long_shadow_fraction: float = .5) -> dict:
    """Legacy lecture proxy; use price_action.observe_resistance for new code.

    Preserves the earlier broad opposing-body rule and explicit 0.5 default for
    historical reproducibility. Neither is the canonical basic definition.
    """
    _pair(current, previous)
    if not math.isfinite(long_shadow_fraction) or not 0 < long_shadow_fraction <= 1:
        raise ValueError('shadow fraction must be in (0,1]')
    span = current.high-current.low
    if direction == 'UP':
        body = current.close < current.open
        gap = current.open < previous.close
        shadow = current.high-max(current.open, current.close)
    elif direction == 'DOWN':
        body = current.close > current.open
        gap = current.open > previous.close
        shadow = min(current.open, current.close)-current.low
    else:
        raise ValueError('direction must be UP or DOWN')
    long_shadow = bool(span and shadow/span >= long_shadow_fraction)
    return dict(opposing_body=body, opposing_open=gap, long_shadow=long_shadow,
                resistance=body or gap or long_shadow)


def three_bar_state(previous: Bar, attack: Bar, response: Bar, confirmation: Bar,
                    *, direction: str, wave_boundary: float,
                    long_shadow_fraction: float = .5) -> dict:
    """Three-bar operational proxy, evaluated only after confirmation closes.

    Precondition: caller has confirmed an N attack with known neckline anchors.
    Six states are allowed, but ambiguous/unfinished paths remain unconfirmed.
    Intraday paths and intentions are never inferred. No trading side effects.
    """
    r = bar_relations(attack, previous)
    _pair(response, attack)
    _pair(confirmation, response)
    _prices(wave_boundary)
    response_evidence = resistance_evidence(response, attack, direction=direction,
                                            long_shadow_fraction=long_shadow_fraction)
    confirm_resistance = resistance_evidence(confirmation, response, direction=direction,
                                            long_shadow_fraction=long_shadow_fraction)['resistance']
    if direction == 'UP':
        defense = r['virtual_low']
        if wave_boundary > defense:
            raise ValueError('UP wave boundary must not be above attack virtual low')
        held = min(response.low, confirmation.low) >= defense
        wave_held = min(response.low, confirmation.low) >= wave_boundary
        continued = confirmation.close > max(attack.high, response.high)
        uninterrupted = response.close > attack.close and confirmation.close > response.close
        names = ('强轧空', '轧空', '盘坚')
    elif direction == 'DOWN':
        defense = r['virtual_high']
        if wave_boundary < defense:
            raise ValueError('DOWN wave boundary must not be below attack virtual high')
        held = max(response.high, confirmation.high) <= defense
        wave_held = max(response.high, confirmation.high) <= wave_boundary
        continued = confirmation.close < min(attack.low, response.low)
        uninterrupted = response.close < attack.close and confirmation.close < response.close
        names = ('强追杀', '追杀', '盘跌')
    else:
        raise ValueError('direction must be UP or DOWN')
    state = '未确认'
    if not wave_held:
        state = '结构失效'
    elif continued:
        if held and not response_evidence['resistance'] and not confirm_resistance and uninterrupted:
            state = names[0]
        elif held and response_evidence['resistance']:
            state = names[1]
        elif not held and response_evidence['resistance']:
            state = names[2]
    return dict(state=state, direction=direction, defense=defense, defense_held=held,
                wave_held=wave_held, continued=continued, response=response_evidence,
                observed_at=confirmation.timestamp.isoformat(), rule='lecture_three_bar_proxy_v1')


def measured_targets(origin: float, neckline: float, retrace: float, *,
                     direction: str = 'UP', box_anchor: float | None = None) -> dict:
    """Slide 8: D=C+(B-A). Slides 9-10: 1P=2B-A, 2T=3B-2A.

    Slide 9 labels B as a virtual-high reference: explicit box_anchor is needed
    to activate 1P/2T, rather than silently equating it with a wave neckline.
    """
    _prices(origin, neckline, retrace)
    if direction not in ('UP','DOWN'):
        raise ValueError('direction must be UP or DOWN')
    sign = 1 if direction == 'UP' else -1
    if not 0 < sign*(retrace-origin) < sign*(neckline-origin):
        raise ValueError('expected an impulse then a partial retracement')
    equal = retrace+neckline-origin
    result = dict(equal_wave=equal if equal > 0 else None, one_p=None, two_t=None,
                  box_anchor=box_anchor, targets_positive=equal > 0)
    if box_anchor is not None:
        _prices(box_anchor)
        if sign*(box_anchor-origin) <= 0:
            raise ValueError('box anchor must follow impulse direction')
        p, t = 2*box_anchor-origin, 3*box_anchor-2*origin
        result.update(one_p=p if p > 0 else None, two_t=t if t > 0 else None,
                      targets_positive=min(equal,p,t) > 0)
    return result


def force_profile(impulse: float, countermove: float) -> dict:
    """Slides 31-33: exact numerical intervals, without filling textual gaps.

    Return boundaries explicitly. 0.67 in the text is not silently identical to
    exact 2/3; this primitive uses exact fractions, documented as an engineering choice.
    """
    if not math.isfinite(impulse) or impulse <= 0 or not math.isfinite(countermove) or countermove < 0:
        raise ValueError('positive impulse and non-negative countermove required')
    ratio = countermove/impulse
    boundaries = (1/6, 1/3, 1/2, 2/3, 5/6)
    boundary = next((str(k)+'/6' for k,b in enumerate(boundaries,1)
                     if math.isclose(ratio,b,rel_tol=0,abs_tol=1e-12)), None)
    band = sum(ratio >= b for b in boundaries)+1 if ratio < 1 else '>=100%'
    return dict(ratio=ratio, sixth_band=band, exact_boundary=boundary,
                below_one_third=ratio < 1/3, above_half=ratio > 1/2,
                above_two_thirds=ratio > 2/3, below_two_thirds=ratio < 2/3,
                interpretation='countermove_strength_not_trend_strength')


@dataclass(frozen=True)
class SwingPoint:
    index: int
    confirmed_index: int
    kind: str
    price: float


def swing_context(points: list[SwingPoint], asof_index: int) -> dict:
    """Only confirmed input pivots. Does not implement ambiguous mother/child paths."""
    if type(asof_index) is not int or asof_index < 0:
        raise ValueError('non-negative as-of index required')
    for i,p in enumerate(points):
        _prices(p.price)
        if (p.kind not in ('H','L') or type(p.index) is not int or p.index < 0
                or type(p.confirmed_index) is not int or p.confirmed_index < p.index
                or (i and (p.index <= points[i-1].index or p.kind == points[i-1].kind
                           or p.confirmed_index < points[i-1].confirmed_index))):
            raise ValueError('strictly ordered, alternating, causally confirmed pivots required')
    known = [p for p in points if p.confirmed_index <= asof_index]
    highs = [p for p in known if p.kind == 'H']
    lows = [p for p in known if p.kind == 'L']
    trend = 'unknown'
    if len(highs) >= 2 and len(lows) >= 2:
        up = highs[-1].price > highs[-2].price and lows[-1].price > lows[-2].price
        down = highs[-1].price < highs[-2].price and lows[-1].price < lows[-2].price
        trend = 'bull' if up else 'bear' if down else 'mixed'
    fall_high = next((p.price for p in reversed(known) if p.kind == 'H' and lows and p.index < lows[-1].index),None)
    rise_low = next((p.price for p in reversed(known) if p.kind == 'L' and highs and p.index < highs[-1].index),None)
    return dict(trend=trend, last_fall_high=fall_high, last_rise_low=rise_low,
                confirmed_points=len(known), asof_index=asof_index)


def turning_evidence(first_counter_ratio: float, second_counter_ratio: float,
                     *, direction: str, minor_line_broken: bool | None) -> dict:
    """Slides 34-35: suspicion vs confirmation; unknown minor line stays unknown."""
    for value in (first_counter_ratio, second_counter_ratio):
        if not math.isfinite(value) or value < 0:
            raise ValueError('finite non-negative ratios required')
    if direction not in ('UP','DOWN') or (minor_line_broken is not None and type(minor_line_broken) is not bool):
        raise ValueError('invalid direction or minor-line evidence')
    suspicion = first_counter_ratio > 2/3
    combination = suspicion and second_counter_ratio < 2/3
    return dict(direction=direction, suspicion=suspicion, ratio_combination=combination,
                minor_line_broken=minor_line_broken,
                confirmed=combination and minor_line_broken is True,
                note='minor trend-line evidence is supplied, not inferred from daily OHLC')
