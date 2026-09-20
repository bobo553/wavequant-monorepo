"""Build V2 entry context from confirmed level 1/2/3 structures.

Drawing vertices have a price date AND an observation date. Each prefix is
reduced independently; an endpoint seen only later is never traded earlier.
The first alternation needs a higher low, not a 2/3 retracement threshold.
"""
from dataclasses import dataclass, asdict

from ..market_structure.lecture_drawing import lecture_drawing
from ..market_structure.lecture_trend import _wave_reversals
from ..market_structure.secondary_trend import _structural_reversals


def _identity(p):
    return p['index'], p['ordinal'], p['kind'], p['value']


def hierarchical_history(bars):
    """Reuse the drawing reducers, but freeze availability on each daily prefix."""
    history = {}; epochs = {}; previous = {0: {}, 1: {}, 2: {}, 3: {}}
    last_epoch = None

    def accept(i, epoch, raw):
        nonlocal previous, last_epoch
        if epoch != last_epoch:
            previous = {0: {}, 1: {}, 2: {}, 3: {}}
        last_epoch = epoch; epochs[i] = epoch
        compact = []
        for p in raw:
            if not compact or p['value'] != compact[-1]['value']:
                compact.append(p)
        turns = []
        for left, p, right in zip(compact, compact[1:], compact[2:]):
            if p['state'] in ('seed', 'developing'):
                continue
            kind = ('H' if p['value'] > max(left['value'], right['value']) else
                    'L' if p['value'] < min(left['value'], right['value']) else None)
            if kind:
                turns.append(dict(p, kind=kind))

        def freeze(points, level):
            frozen = []
            now = {}
            for p in points:
                key = _identity(p)
                # A reappearing/revised point becomes known now, not at its x-axis date.
                known = max(previous[level].get(key, i), p.get('available_index', 0))
                if frozen:
                    known = max(known, frozen[-1]['available_at'])
                q = dict(p, available_at=known, label=f'{p["kind"]}{len(frozen)+1}')
                now[key] = known; frozen.append(q)
            previous[level] = now
            return frozen

        turns = freeze(turns, 0)
        levels = {}; source = turns
        for level in (1, 2, 3):
            reduced = _wave_reversals(source) if level == 1 else _structural_reversals(source, source_level=level-1)
            # The reducer's confirmation trigger may occur after the extreme itself.
            reduced = [dict(p, available_index=p['available_at']) for p in reduced]
            source = freeze(reduced, level)
            levels[level] = tuple(dict(index=p['index'], ordinal=p['ordinal'], kind=p['kind'],
                                      value=p['value'], available_at=p['available_at']) for p in source)
        history[i] = levels

    lecture_drawing(bars, on_step=accept)
    return history, epochs


@dataclass(frozen=True)
class EntryContext:
    trend_level: int
    epoch: int
    context_index: int
    key_source_index: int
    key_price: float
    flip_index: int
    flip_high_index: int
    flip_high_price: float
    origin_index: int
    origin_price: float
    alternation_index: int
    alternation_low_index: int
    alternation_low_price: float
    maturity_index: int | None = None
    confirmation_attack: int | None = None

    @property
    def episode(self):
        return self.trend_level, self.epoch, self.context_index, self.flip_index, self.alternation_index


class _LevelState:
    """Causal state machine for one reduced trend level and one chart epoch.

    ``anchor`` is the active directional extreme and ``key`` is the confirmed
    opposite pivot immediately before that extreme. During a bearish episode the
    key is therefore 末跌高; a later high must strictly exceed it before a
    bullish flip can exist. Equality is only a retest.

    The state is scoped to an epoch because an ambiguous parent/child-bar path
    starts a new independent geometry. Carrying keys across that boundary would
    join structures that the lecture reducer intentionally keeps separate.
    """

    def __init__(self, level, epoch, *, whole_wave=False):
        self.level = level; self.epoch = epoch
        self.whole_wave = whole_wave
        self.points = []; self.direction = None; self.anchor = None; self.key = None
        self.flip = None; self.context = None

    def point(self, p, now):
        """Consume one newly confirmed point without reading future points.

        State progression is deliberately one-way within an episode:
        establish direction -> freeze key -> break key -> confirm alternation.
        A new directional extreme may replace the anchor/key before the break;
        intermediate rebounds after the anchor never lower the breakout key.
        """

        previous = self.points[-1] if self.points else None
        self.points.append(p)
        highs = [q for q in self.points if q['kind'] == 'H']
        lows = [q for q in self.points if q['kind'] == 'L']
        if self.direction is None:
            # Direction needs two highs and two lows moving together. Mixed or
            # equal comparisons remain unknown instead of being guessed.
            if len(highs) < 2 or len(lows) < 2:
                return
            dh = highs[-1]['value']-highs[-2]['value']; dl = lows[-1]['value']-lows[-2]['value']
            self.direction = 'up' if dh > 0 and dl > 0 else 'down' if dh < 0 and dl < 0 else None
            if self.direction is None:
                return
            self.anchor = (max if self.direction == 'up' else min)(
                highs if self.direction == 'up' else lows, key=lambda q:q['value'])
            pos = self.points.index(self.anchor)
            self.key = self.points[pos-1] if pos else None
            return
        if self.key is None:
            # The first same-direction extreme gains its key only when an
            # opposite confirmed pivot exists immediately to its left.
            if previous and p['kind'] == ('H' if self.direction == 'up' else 'L'):
                self.anchor, self.key = p, previous
            return
        if self.direction == 'up':
            if p['kind'] == 'H' and p['value'] > self.anchor['value']:
                self.anchor, self.key = p, previous
            elif p['kind'] == 'L' and p['value'] < self.key['value']:
                self.direction = 'down'; self.anchor, self.key = p, previous
                self.context = self.flip = None
            return
        if self.flip is None:
            if p['kind'] == 'L' and p['value'] < self.anchor['value']:
                # A new bearish extreme re-anchors 末跌高 to the preceding H.
                self.anchor, self.key = p, previous
            elif p['kind'] == 'H' and p['value'] > self.key['value'] and previous:
                # Strictly greater is required: touching 末跌高 does not prove
                # that the bearish structure has been broken.
                self.flip = dict(high=p, origin=previous, known=now, anchor=self.anchor, key=self.key)
            return
        f = self.flip
        if p['kind'] == 'L':
            # The first confirmed low after the key break supplies alternation.
            # Legacy and whole-wave profiles intentionally use different origin
            # boundaries, so the equality rule remains explicit here.
            origin = f['anchor'] if self.whole_wave else f['origin']
            valid_low = origin['value'] <= p['value'] if self.whole_wave else origin['value'] < p['value']
            if valid_low and p['value'] < f['high']['value']:
                self.context = EntryContext(self.level, self.epoch, f['anchor']['index'],
                    f['key']['index'], f['key']['value'], f['known'],
                    f['high']['index'], f['high']['value'], origin['index'], origin['value'],
                    now, p['index'], p['value'])
                self.direction = 'up'; self.anchor, self.key = f['high'], p
                self.flip = None
            else:
                self.flip = None
                if p['value'] <= self.anchor['value']:
                    self.anchor, self.key = p, previous
        elif p['value'] > f['high']['value']:
            self.flip = dict(f, high=p)


def context_history(bars, history, epochs, *, whole_wave=False):
    """All >=level-1 contexts are OR alternatives, never three mandatory gates."""
    from dataclasses import replace
    result = {}; events = []; states = {}; prior_epoch = None
    for i, bar in enumerate(bars):
        if epochs[i] != prior_epoch:
            states = {level: _LevelState(level, epochs[i], whole_wave=whole_wave) for level in (1, 2, 3)}
        prior_epoch = epochs[i]; available = []
        for level, state in states.items():
            previous_context = state.context
            points = history[i][level]
            common = 0
            for old, new in zip(state.points, points):
                if old != new:
                    break
                common += 1
            if common < len(state.points):
                # Revisions invalidate the old episode; rebuild at current knowledge time.
                state = states[level] = _LevelState(level, epochs[i], whole_wave=whole_wave); common = 0
            before = state.context
            for p in points[common:]:
                state.point(p, p['available_at'] if whole_wave else i)
            ctx = state.context
            if ctx is not None:
                if whole_wave and previous_context is not None and previous_context.episode == ctx.episode:
                    # Revising a later pivot cannot erase a maturity close that
                    # was already observed for this unchanged flip/alternation.
                    ctx = state.context = replace(ctx,maturity_index=previous_context.maturity_index)
                # Full retracement/structural stop is distinct from an amplitude filter.
                invalid = (min(b.low for b in bars[ctx.origin_index:i+1]) < ctx.origin_price if whole_wave else
                           bar.low <= ctx.origin_price or bar.close < ctx.alternation_low_price)
                if invalid:
                    state.context = None
                    events.append(dict(bar_index=i, event='hierarchy_context_invalidated', trend_level=level))
                    continue
                if before is None or before.episode != ctx.episode:
                    events.append(dict(bar_index=i, event='hierarchy_alternation_ready', **asdict(ctx)))
                # No retroactive maturity: a later daily close must re-confirm the flip high.
                if ctx.maturity_index is None and i > ctx.alternation_index and bar.close > ctx.flip_high_price:
                    ctx = state.context = replace(ctx, maturity_index=i)
                    events.append(dict(bar_index=i, event='hierarchy_bull_matured', **asdict(ctx)))
                available.append(ctx)
        result[i] = tuple(available)
    return result, events


def select_entry(current, at_attack, *, attack, origin_index, high_index, low_index, ratio, shallow_ratio, asof=None):
    """Classify from attack-time knowledge; later evidence cannot rescue an old N.

Type 2 uses the new N's actual upward leg and pullback. Its origin must be at
or after the alternation low, its high at/after maturity, and pullback later.
Once mature, this level cannot fall back to the unfiltered type 1 route.
"""
    live = {c.episode:c for c in current}; eligible = []; reasons = []
    asof = attack if asof is None else asof
    for ctx in at_attack:
        if ctx.episode not in live or not ctx.flip_index <= ctx.alternation_index < attack:
            continue
        if ctx.maturity_index is None or ctx.maturity_index >= attack:
            if ctx.trend_level not in (2, 3):
                reasons.append('first_buy_requires_level_two_or_three'); continue
            # An early N cannot retain the unfiltered route indefinitely after
            # the live context matures. Same-close maturation is the boundary;
            # from the next bar onward a NEW post-maturity pullback is required.
            mature_now = live[ctx.episode].maturity_index
            if mature_now is not None and mature_now < asof:
                reasons.append('early_n_expired_after_maturity'); continue
            eligible.append(dict(asdict(ctx), buy_point_type='transition_squeeze', priority=1,
                                 counter_filter_applied=False, counter_ratio=ratio))
        else:
            if not (origin_index >= ctx.alternation_low_index and high_index >= ctx.maturity_index
                    and low_index > high_index and low_index > ctx.maturity_index):
                reasons.append('mature_pullback_sequence_not_ready'); continue
            if not 0 <= ratio < shallow_ratio:
                reasons.append('mature_pullback_not_shallow'); continue
            eligible.append(dict(asdict(ctx), buy_point_type='mature_shallow_squeeze', priority=2,
                                 counter_filter_applied=True, counter_ratio=ratio,
                                 counter_limit=shallow_ratio, pullback_index=low_index,
                                 impulse_origin_index=origin_index, impulse_high_index=high_index))
    if eligible:
        return max(eligible, key=lambda e:(e['priority'], e['trend_level'])), ''
    return None, (reasons[0] if reasons else 'hierarchy_transition_not_ready')
