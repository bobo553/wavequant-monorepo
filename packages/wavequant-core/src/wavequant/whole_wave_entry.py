"""Whole-flip-wave buy definitions. All anchors are known at the N attack.

Type 1: deep alternation, low never below L0; freeze eligibility at attack.
Type 2: after maturity, a new peak and pullback; CLOSE drawdown / (peak-L0).
These are entry filters, not an alternative drawing algorithm.
"""
from dataclasses import asdict
from fractions import Fraction


def price(value):
    return Fraction(str(value))


def threshold(value):
    return {1/3:Fraction(1,3), .5:Fraction(1,2), 2/3:Fraction(2,3)}[value]


def select_wave_entry(current, at_attack, *, bars, attack, low_index, asof,
                      deep_ratio=.5, shallow_ratio=1/3, **unused):
    if not 0 <= low_index < attack <= asof < len(bars):
        return None, 'wave_invalid_n_sequence'
    live = {c.episode:c for c in current}; eligible=[]; reasons=[]
    if not at_attack:
        return None, 'wave_no_alternation_at_attack'
    for ctx in at_attack:
        if ctx.episode not in live:
            reasons.append('wave_context_no_longer_live');continue
        if not ctx.flip_index < ctx.alternation_index < attack:
            reasons.append('wave_alternation_not_before_n');continue
        l0,h0=price(ctx.origin_price),price(ctx.flip_high_price)
        if h0 <= l0:
            reasons.append('wave_invalid_flip_amplitude');continue
        if min(price(b.low) for b in bars[ctx.origin_index:asof+1]) < l0:
            reasons.append('wave_flip_origin_broken');continue
        common=dict(asdict(ctx),definition='whole_flip_wave_v3',
                    counter_filter_applied=True,flip_origin_price=float(l0),
                    local_n_ratio=unused.get('ratio'),eligibility_frozen_at=attack)
        # Maturation on the attack bar does not retroactively change its class.
        if ctx.maturity_index is None or ctx.maturity_index >= attack:
            a=price(ctx.alternation_low_price);r=(h0-a)/(h0-l0)
            if not l0 <= a < h0:
                reasons.append('wave_alternation_origin_broken');continue
            if not r > threshold(deep_ratio):
                reasons.append('wave_first_pullback_not_deep');continue
            eligible.append(dict(common,buy_point_type='transition_squeeze',priority=1,
                counter_ratio=float(r),counter_exact_ratio=str(r),counter_limit=deep_ratio,
                counter_operator='>',counter_basis='alternation_low_over_whole_flip',
                ratio_high_price=float(h0),ratio_low_price=float(l0),counter_price=float(a)))
            continue
        # The known pre-attack peak must follow the close-confirmed breakout.
        peak_index=max(range(ctx.maturity_index,attack),key=lambda j:bars[j].high)
        peak=price(bars[peak_index].high)
        if peak <= h0 or not ctx.alternation_index < ctx.maturity_index <= peak_index < low_index < attack:
            reasons.append('wave_second_peak_pullback_sequence');continue
        # Include the peak bar close: a long upper wick is not a shallow close
        # retracement merely because the high/low intrabar order is unknown.
        close_index=min(range(peak_index,asof+1),key=lambda j:bars[j].close)
        close=price(bars[close_index].close)
        r=max(Fraction(0),peak-close)/(peak-l0)
        if r > threshold(shallow_ratio):
            reasons.append('wave_second_close_pullback_too_deep');continue
        eligible.append(dict(common,buy_point_type='mature_shallow_squeeze',priority=2,
            counter_ratio=float(r),counter_exact_ratio=str(r),counter_limit=shallow_ratio,
            counter_operator='<=',counter_basis='minimum_close_over_peak_minus_flip_origin',
            ratio_high_price=float(peak),ratio_low_price=float(l0),counter_price=float(close),
            peak_index=peak_index,peak_price=float(peak),minimum_close_index=close_index,
            pullback_index=low_index,impulse_high_index=peak_index,impulse_origin_index=ctx.origin_index))
    if eligible:
        return max(eligible,key=lambda e:(e['priority'],e['trend_level'])),''
    return None,reasons[0] if reasons else 'wave_no_eligible_context'
