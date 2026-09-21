"""Define whole-flip-wave entries using causally known alternation anchors.

Type 1: freeze eligibility at attack, or opt into its exact joint confirmation.
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
                      deep_ratio=.5, shallow_ratio=1/3, first_basis='alternation_low',
                      second_inclusive=True, allow_confirming_n=False, **unused):
    if not 0 <= low_index < attack <= asof < len(bars):
        return None, 'wave_invalid_n_sequence'
    live = {c.episode:c for c in current}; eligible=[]; reasons=[]
    at_attack = list(at_attack)
    if allow_confirming_n:
        # Keep eligibility created by this exact N's confirmation available for
        # its later squeeze; never backdate it or borrow another N's context.
        at_attack += [c for c in current if c.confirmation_attack == attack
                      and attack < c.alternation_index <= asof
                      and c not in at_attack]
    if not at_attack:
        return None, 'wave_no_alternation_at_attack'
    for ctx in at_attack:
        if ctx.episode not in live:
            reasons.append('wave_context_no_longer_live');continue
        joint = (allow_confirming_n and ctx.confirmation_attack == attack
                 and attack < ctx.alternation_index <= asof)
        if not (ctx.flip_index <= ctx.alternation_index and (ctx.alternation_index < attack or joint)):
            reasons.append('wave_alternation_not_before_n');continue
        l0,h0=price(ctx.origin_price),price(ctx.flip_high_price)
        if h0 <= l0:
            reasons.append('wave_invalid_flip_amplitude');continue
        if min(price(b.low) for b in bars[ctx.origin_index:asof+1]) < l0:
            reasons.append('wave_flip_origin_broken');continue
        common=dict(asdict(ctx),definition='whole_flip_wave_v3',
                    counter_filter_applied=True,flip_origin_price=float(l0),
                    local_n_ratio=unused.get('ratio'),
                    eligibility_frozen_at=ctx.alternation_index if joint else attack,
                    joint_alternation_confirmation=joint)
        # Maturation on the attack bar does not retroactively change its class.
        if ctx.maturity_index is None or ctx.maturity_index >= attack:
            if ctx.trend_level not in (2, 3):
                reasons.append('first_buy_requires_level_two_or_three');continue
            a=price(ctx.alternation_low_price)
            if not l0 <= a < h0:
                reasons.append('wave_alternation_origin_broken');continue
            close_index=None
            counter=a
            if first_basis=='minimum_close':
                if not 0<=ctx.flip_high_index<=ctx.alternation_low_index<attack:
                    reasons.append('wave_alternation_not_before_n');continue
                # Freeze the first decline at the confirmed alternation pivot;
                # later closes cannot retroactively qualify that episode.
                close_index=min(range(ctx.flip_high_index,ctx.alternation_low_index+1),
                                key=lambda j:bars[j].close)
                counter=price(bars[close_index].close)
            r=(h0-counter)/(h0-l0)
            if deep_ratio is not None and not r > threshold(deep_ratio):
                reasons.append('wave_first_pullback_not_deep');continue
            eligible.append(dict(common,buy_point_type='transition_squeeze',priority=1,
                counter_filter_applied=deep_ratio is not None,
                counter_ratio=float(r),counter_exact_ratio=str(r),counter_limit=deep_ratio,
                counter_operator='>' if deep_ratio is not None else None,counter_basis=('minimum_close_from_flip_high_to_alternation'
                                                    if first_basis=='minimum_close' else
                                                    'alternation_low_over_whole_flip'),
                ratio_high_price=float(h0),ratio_low_price=float(l0),counter_price=float(counter),
                **({'minimum_close_index':close_index} if close_index is not None else {})))
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
        limit=threshold(shallow_ratio)
        too_deep=r>limit if second_inclusive else r>=limit
        if too_deep:
            reasons.append('wave_second_close_pullback_too_deep');continue
        eligible.append(dict(common,buy_point_type='mature_shallow_squeeze',priority=2,
            counter_ratio=float(r),counter_exact_ratio=str(r),counter_limit=shallow_ratio,
            counter_operator='<=' if second_inclusive else '<',
            counter_basis='minimum_close_over_peak_minus_flip_origin',
            ratio_high_price=float(peak),ratio_low_price=float(l0),counter_price=float(close),
            peak_index=peak_index,peak_price=float(peak),minimum_close_index=close_index,
            pullback_index=low_index,impulse_high_index=peak_index,impulse_origin_index=ctx.origin_index))
    if eligible:
        return max(eligible,key=lambda e:(e['priority'],e['trend_level'])),''
    return None,reasons[0] if reasons else 'wave_no_eligible_context'
