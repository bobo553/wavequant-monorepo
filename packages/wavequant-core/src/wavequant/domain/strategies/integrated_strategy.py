"""Run the auditable strategy event pipeline without future-suffix leakage.

Strict lecture runs and daily fractal proxies are separate hypotheses. Canonical
observers produce dated evidence; only evidence available on a signal bar is used.
"""
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from typing import Sequence

from ..models.model import Bar, Signal
from ..market_structure.polyline import LinePoint, PointKind, ReversalPoint, observe_polyline, observe_bar_relations
from ..market_structure.n_shape import NSetup, PivotRef, BoxAnchorMode, MilestoneBasis, observe_n
from ..market_structure.price_action import Direction, ShadowPolicy, AttackBasis
from ..market_state.market_regime import MarketRegime, RegimePhase, RegimePolicy, ResistanceOutcome, WaveBoundary, observe_market_regime
from ..market_state.control_bar import observe_control_bar
from ..market_structure.trend_structure import observe_structure
from .bull_eligibility import bull_permission_history
from .attack_quality import v3_positive_n_attack_rejection
from .completed_wave_recovery import secondary_wave_recovery, inverse_wave_recovery
from ..market_state.squeeze_state import observe_squeeze_resumption
from ..market_state.wave_strength import StrengthScale, measure_strength
from ..market_state.washout import WashoutPolicy, WashoutStage, observe_washout
from ..market_state.market_turn import (RegimeContext, TurnSetup, TurnPolicy, TurnThreshold, freeze_minor_line, observe_market_turn)


@dataclass(frozen=True)
class SystemStrategy:
    pivot_mode: str = 'strict_polyline'
    pivot_width: int = 2
    pattern_ttl: int = 60
    structure_window: int = 120
    volume_lookback: int = 20
    minimum_rvol: float = 1.2
    minimum_reward_risk: float = 1.5
    max_counter_ratio: float = 2/3
    shadow_fraction: float = .5
    strict_n_attack_quality: bool = True
    volume_filter: bool = True
    regime_filter: bool = True
    entry_policy: str = 'transitioned_squeeze'
    preflight_reward_risk: bool = True
    squeeze_pullback_entries: bool = True
    mature_shallow_ratio: float = 1/3
    buy_point_definition: str = 'legacy_v2'
    first_pullback_threshold: float | None = .5
    first_pullback_basis: str = 'alternation_low'
    mature_shallow_inclusive: bool = True

    def validate(self):
        import math
        if self.pivot_mode not in ('strict_polyline', 'confirmed_fractal_proxy', 'lecture_causal'):
            raise ValueError('explicit supported pivot mode required')
        for name in ('pivot_width', 'pattern_ttl', 'structure_window', 'volume_lookback'):
            if type(getattr(self, name)) is not int or getattr(self, name) <= 0:
                raise ValueError('positive integer structural windows required')
        for name in ('minimum_rvol', 'minimum_reward_risk', 'max_counter_ratio', 'shadow_fraction', 'mature_shallow_ratio'):
            value = getattr(self, name)
            if type(value) not in (float, int) or not math.isfinite(value) or value < 0:
                raise ValueError('finite nonnegative parameters required')
        if not 0 < self.max_counter_ratio < 1 or not 0 < self.shadow_fraction <= 1:
            raise ValueError('invalid ratio or shadow threshold')
        if not 0 < self.mature_shallow_ratio < 1:
            raise ValueError('invalid mature pullback threshold')
        if self.buy_point_definition not in ('legacy_v2','whole_flip_wave_v3'):
            raise ValueError('explicit buy point definition required')
        if self.first_pullback_threshold is not None and (type(self.first_pullback_threshold) not in (float,int)
                or self.first_pullback_threshold not in (.5,2/3)):
            raise ValueError('first pullback threshold must be 1/2 or 2/3')
        if self.first_pullback_basis not in ('alternation_low','minimum_close'):
            raise ValueError('first pullback basis must be an alternation low or minimum close')
        if type(self.mature_shallow_inclusive) is not bool:
            raise ValueError('mature shallow inclusive must be boolean')
        if self.buy_point_definition=='whole_flip_wave_v3' and (
                self.entry_policy!='hierarchical_two_buy_points' or self.mature_shallow_ratio not in (1/3,.5)):
            raise ValueError('whole wave entries require hierarchical policy and close threshold 1/3 or 1/2')
        if type(self.strict_n_attack_quality) is not bool:
            raise ValueError('strict_n_attack_quality must be boolean')
        if type(self.volume_filter) is not bool or type(self.regime_filter) is not bool:
            raise ValueError('boolean filters required')
        if self.entry_policy not in ('legacy_n_continuation', 'transitioned_squeeze', 'hierarchical_two_buy_points'):
            raise ValueError('explicit supported entry policy required')
        if self.entry_policy in ('transitioned_squeeze', 'hierarchical_two_buy_points') and not self.regime_filter:
            raise ValueError('transitioned squeeze requires confirmed squeeze regime')
        if self.entry_policy == 'hierarchical_two_buy_points' and self.pivot_mode != 'lecture_causal':
            raise ValueError('hierarchical entries require lecture causal geometry')
        if type(self.preflight_reward_risk) is not bool:
            raise ValueError('preflight_reward_risk must be boolean')
        if type(self.squeeze_pullback_entries) is not bool:
            raise ValueError('squeeze_pullback_entries must be boolean')
        if self.squeeze_pullback_entries and (not self.regime_filter or self.entry_policy not in ('transitioned_squeeze', 'hierarchical_two_buy_points')):
            raise ValueError('pullback resumption requires transitioned squeeze policy')


@dataclass
class SystemResult:
    signals: list[Signal]
    audit: list[dict]
    counts: dict


def pivot_history(bars: list[Bar], config: SystemStrategy):
    """Snapshots are immutable tuples; strict ambiguity starts a NEW episode."""
    snapshots, epochs, limits, blocked = {}, {}, {}, set()
    n = len(bars)
    if config.pivot_mode=='lecture_causal':
        from .lecture_strategy import lecture_pivot_history
        return lecture_pivot_history(bars)
    if config.pivot_mode == 'strict_polyline':
        start = 0
        while start < n:
            end = start+1
            while end < n:
                r = observe_bar_relations(bars[end-1], bars[end])
                if r.inside or r.outside:
                    break
                end += 1
            stop = min(end, n-1)
            direction = Direction.UP if bars[start].close >= bars[start].open else Direction.DOWN
            line = observe_polyline(bars, symbol=bars[0].symbol, timeframe='1d',
                initial_direction=direction, start_index=start, asof_index=stop)
            known = []
            for frame in line.frames:
                known.extend(frame.new_reversals)
                snapshots[frame.bar_index] = tuple(known)
                epochs[frame.bar_index] = start
                limits[frame.bar_index] = end-1
            if end < n:
                blocked.add(end)
            start = end+1
    else:
        points = []
        w = config.pivot_width
        for i in range(n):
            if i >= 2*w:
                j = i-w
                others = bars[j-w:j]+bars[j+1:i+1]
                lo = all(bars[j].low < b.low for b in others)
                hi = all(bars[j].high > b.high for b in others)
                if lo != hi:
                    kind = PointKind.LOW if lo else PointKind.HIGH
                    p = ReversalPoint(LinePoint(j, 0, kind, bars[j].low if lo else bars[j].high), i,
                                      'causal_fractal_proxy_not_lecture_polyline')
                    if points and points[-1].point.kind == kind:
                        sign = 1 if hi else -1
                        if sign*(p.point.price-points[-1].point.price) > 0:
                            points[-1] = p  # new snapshot, never mutate earlier N setups
                    elif not points or (kind == PointKind.HIGH and p.point.price > points[-1].point.price) or (kind == PointKind.LOW and p.point.price < points[-1].point.price):
                        points.append(p)
            snapshots[i], epochs[i], limits[i] = tuple(points), 0, n-1
    return snapshots, epochs, limits, blocked


def _local_setup(setup, offset):
    return replace(setup, **{name: PivotRef(getattr(setup, name).index-offset,
                                          getattr(setup, name).confirmed_index-offset)
                             for name in ('origin', 'neckline', 'pullback')})


def generate_system_signals(bars: list[Bar], config: SystemStrategy, *,
                            minor_points: Sequence[ReversalPoint] | None = None) -> SystemResult:
    config.validate()
    if not bars:
        return SystemResult([], [], {})
    from ..models.validated_bars import ValidatedBars
    bars = ValidatedBars(bars)
    if len({b.timestamp.date() for b in bars}) != len(bars):
        raise ValueError('daily bars required; map supplied minor pivots explicitly to daily index axis')
    snapshots, epochs, limits, blocked = pivot_history(bars, config)
    counts = Counter(rows=len(bars), ambiguous_bars=len(blocked), minor_data_available=minor_points is not None)
    audit, candidates, seen, attack_keys = [], [], set(), set()
    events = defaultdict(list)
    resumptions, resumption_keys = defaultdict(list), set()
    consolidation_events, consolidation_proofs = defaultdict(list), {}
    reversal_proofs = {}
    reconfirmation_proofs = {}
    def log(i, kind, **fields):
        audit.append(dict(timestamp=bars[i].timestamp.isoformat(), symbol=bars[i].symbol,
                          bar_index=i, event=kind, **fields))
    permissions = {}
    hierarchy_permissions = {}
    hierarchical = config.entry_policy == 'hierarchical_two_buy_points'
    whole_wave = config.buy_point_definition == 'whole_flip_wave_v3'
    if config.entry_policy == 'transitioned_squeeze':
        permissions, permission_events = bull_permission_history(
            bars, snapshots, epochs, blocked, config.structure_window,
            maximum_retracement=config.max_counter_ratio if config.pivot_mode=='lecture_causal' else None)
        for event in permission_events:
            row = dict(event)
            j, kind = row.pop('bar_index'), row.pop('event')
            log(j, kind, **row)
            counts[kind] += 1
    larger, folded_sets = {}, []
    if whole_wave and config.pivot_mode == 'lecture_causal':
        from .hierarchical_n import hierarchical_n_candidates
        from .folded_n import folded_positive_n_candidates, folded_inverse_n_candidates
        larger = hierarchical_n_candidates(bars)
        for i in range(len(bars)):
            folded = folded_positive_n_candidates(snapshots[i], earliest=max(epochs[i], i-config.structure_window))
            folded += folded_inverse_n_candidates(snapshots[i], earliest=max(epochs[i], i-config.structure_window))
            folded_sets.extend((i, points, level) for points, level in folded)
    candidate_sets = [(i, points, level) for i in range(len(bars))
                      for points, level in [(snapshots[i], 0), *larger.get(i, [])]]
    candidate_sets.extend(folded_sets)
    for i, points, n_level in candidate_sets:
        if i in blocked:
            log(i, 'strict_structure_interrupted')
            continue
        if len(points) < 3:
            counts['insufficient_pivots_bars'] += 1
            continue
        a, b, c = points[-3:]
        if whole_wave and a.point.kind == PointKind.HIGH and a.point.index == b.point.index < c.point.index:
            # A mother candle's internal high/low cannot order a daily N. A
            # preceding confirmed high may supply an independent larger A.
            a = next((p for p in reversed(points[:-3])
                      if p.point.kind == PointKind.HIGH and p.point.index < b.point.index
                      and bars[p.point.index].high > bars[c.point.index].high), a)
        key = tuple((p.point.index, p.confirmed_index) for p in (a, b, c))
        if key in seen:
            continue
        seen.add(key)
        mother_impulse = (whole_wave and config.pivot_mode == 'lecture_causal'
            and a.point.kind == PointKind.LOW and a.point.index == b.point.index < c.point.index
            and a.point.ordinal < b.point.ordinal)
        if not (a.point.index<b.point.index<c.point.index or mother_impulse):
            counts['same_bar_n_rejected']+=1
            log(i,'n_geometry_rejected',reason='same_bar_vertices_require_lower_timeframe_n')
            continue
        if i-a.point.index > config.structure_window * max(1,n_level):
            counts['expired_geometry'] += 1
            continue
        direction = Direction.UP if a.point.kind == PointKind.LOW else Direction.DOWN
        setup = NSetup(bars[0].symbol, '1d', direction,
            *(PivotRef(p.point.index, p.confirmed_index) for p in (a, b, c)),
            config.pivot_mode, BoxAnchorMode.ATTACK_VIRTUAL_EXTREME,
            allow_confirmation_bar=whole_wave,
            allow_outside_close=whole_wave, allow_mother_impulse=mother_impulse,
            staged_defense=whole_wave and direction == Direction.UP)
        offset = max(0, a.point.index-config.volume_lookback)
        finish = min(len(bars)-1, limits[i], i+config.pattern_ttl)
        local, data = _local_setup(setup, offset), bars[offset:finish+1]
        counts['n_candidates'] += 1
        try:
            n = observe_n(data, local, timeframe='1d', milestone_basis=MilestoneBasis.EXTREME)
        except ValueError as exc:
            counts['rejected_n_geometry'] += 1
            log(i, 'n_geometry_rejected', reason=str(exc))
            continue
        if n.completion is None:
            counts['n_'+n.status.value] += 1
            continue
        t = offset+n.completion.bar_index
        if (direction, t) in attack_keys:
            continue
        attack_keys.add((direction, t))
        control = observe_control_bar(data, local, timeframe='1d', volume_lookback=config.volume_lookback,
                                      shadow_policy=ShadowPolicy(config.shadow_fraction))
        rejection = None
        if whole_wave and direction == Direction.UP:
            rejection = v3_positive_n_attack_rejection(bars[t], control.volume)
            if rejection is not None and config.strict_n_attack_quality:
                counts['rejected_v3_positive_n_attack'] += 1
                log(t, 'n_attack_rejected', direction=direction.value, reason=rejection,
                    known_at=i, previous_volume=bars[t-1].volume, attack_volume=bars[t].volume,
                    open=bars[t].open, high=bars[t].high, low=bars[t].low, close=bars[t].close)
                continue
        regime = observe_market_regime(data, local, timeframe='1d',
            policy=RegimePolicy(ShadowPolicy(config.shadow_fraction), WaveBoundary.ORIGIN,
                               local_resistance_failure=whole_wave and direction == Direction.UP))
        force = measure_strength(n.anchors.origin, n.anchors.neckline_extreme, n.anchors.pullback,
                                  impulse_direction=direction, scale=StrengthScale.EXACT_FRACTIONS)
        candidate = dict(setup=setup, epoch=epochs[i], start=offset, finish=finish, n=n,
                         attack=t, known_at=i, regime=regime, control=control, force=force, n_level=n_level, attack_quality_warning=rejection)
        candidates.append(candidate)
        counts['completed_'+direction.value+'_n'] += 1
        log(t, 'n_completed', direction=direction.value, origin=a.point.index, neckline=b.point.index,
            pullback=c.point.index, known_at=i, defense=n.completion.defense, counter_ratio=force.ratio,
            n_level=n_level, **({'outside_close_confirmed': True} if whole_wave and setup.allow_outside_close and setup.pullback.index == t else {}))
    # A later confirmed inverse N terminates ordinary local-bounce entries.
    # Use its availability, not a pivot source date, to preserve prior signals.
    inverse_known = sorted((max(c['attack'], c['known_at']), c['attack']) for c in candidates
                           if c['setup'].direction == Direction.DOWN)
    inverse_reentry = [dict(known_at=max(c['attack'], c['known_at']), attack=c['attack'],
                           b_index=c['setup'].pullback.index, b_high=c['n'].anchors.pullback,
                           kill_high=c['n'].completion.defense)
                       for c in candidates if c['setup'].direction == Direction.DOWN]
    for candidate in candidates:
        direction = candidate['setup'].direction
        offset, t, n = candidate['start'], candidate['attack'], candidate['n']
        regime = candidate['regime']
        full_frames = regime.frames
        if whole_wave and direction == Direction.UP:
            invalidation = next(((known, attack) for known, attack in inverse_known if attack > t), None)
            if invalidation is not None:
                known, inverse_attack = invalidation
                regime = replace(regime, frames=tuple(f for f in regime.frames if offset + f.bar_index < known))
                candidate['regime'] = regime
                log(known, 'n_squeeze_invalidated', attack=t, inverse_attack=inverse_attack,
                    reason=('later_inverse_n_ends_local_bounce_larger_defense_still_observed'
                            if candidate['n_level'] >= 1 else 'later_confirmed_inverse_n_requires_new_positive_n'))
                counts['n_squeeze_invalidated'] += 1
            from .n_record_reconfirmation import n_record_reconfirmation
            fresh_by_attack = {c['attack']: c for c in candidates
                               if c['setup'].direction == Direction.UP and c['attack'] > t}
            retained = {f.bar_index: f for f in regime.frames}
            for f in full_frames:
                j = offset + f.bar_index
                if f.bar_index in retained and f.regime in (MarketRegime.BULL, MarketRegime.STRONG_BULL):
                    continue
                fresh = fresh_by_attack.get(j)
                if fresh is None:
                    continue
                proof = n_record_reconfirmation(bars, attack=t, defense=n.completion.defense, now=j,
                    new_origin=fresh['setup'].origin.index, new_neckline=fresh['setup'].neckline.index,
                    new_pullback=fresh['setup'].pullback.index, new_known=fresh['known_at'])
                if proof is None:
                    continue
                reconfirmation_proofs[t, j] = proof
                retained[f.bar_index] = replace(f, regime=MarketRegime.BULL, phase=RegimePhase.CONFIRMED,
                    resistance_outcome=ResistanceOutcome.FAILED, close_continuation=True,
                    last_confirmed_regime=MarketRegime.BULL, last_confirmed_index=f.bar_index)
            regime = replace(regime, frames=tuple(retained[k] for k in sorted(retained)))
            candidate['regime'] = regime
        if whole_wave and direction == Direction.UP and candidate['n_level'] >= 1:
            from .n_consolidation import consolidation_gap
            # The larger defense remains observable independently of smaller exits.
            for f in full_frames:
                j = offset + f.bar_index
                proof = consolidation_gap(bars, attack=t, now=j, defense=n.completion.defense)
                if proof is not None:
                    consolidation_events[j].append((candidate, f))
                    consolidation_proofs[t, j] = proof
                    log(j, 'n_consolidation_gap_confirmed', attack=t, **proof)
        if whole_wave and direction == Direction.UP:
            from ..market_state.volume_reversal import volume_reversal_squeeze
            updated = []
            for f in regime.frames:
                j = offset + f.bar_index
                proof = volume_reversal_squeeze(bars, attack=t, now=j,
                    origin_low=n.anchors.origin, attack_defense=n.completion.defense,
                    frame=f, offset=offset)
                if proof is not None:
                    close_break = next(k for k in range(t, j + 1)
                        if bars[k].close > n.anchors.neckline_extreme)
                    reversal_proofs[t, j] = dict(proof,
                        n_close_break_date=bars[close_break].timestamp.date().isoformat())
                    f = replace(f, regime=MarketRegime.BULL, phase=RegimePhase.CONFIRMED,
                        close_continuation=True, last_confirmed_regime=MarketRegime.BULL,
                        last_confirmed_index=f.bar_index)
                    log(j, 'volume_reversal_squeeze_confirmed', attack=t, **reversal_proofs[t, j])
                updated.append(f)
            regime = replace(regime, frames=tuple(updated))
            candidate['regime'] = regime
        for f in regime.frames:
            j = offset+f.bar_index
            if f.regime is not None:
                counts['regime_'+f.regime.value] += 1
                events[j].append((candidate, f))
                log(j, 'regime_confirmation', regime=f.regime.value, attack=t)
            elif config.squeeze_pullback_entries and direction == Direction.UP:
                resume = observe_squeeze_resumption(bars, j, frame=f, offset=offset,
                    attack_index=t, defense=n.completion.defense)
                if resume is not None:
                    resumptions[j].append((candidate, f))
                    resumption_keys.add((t,j))
                    counts['squeeze_resumption_observed'] += 1
                    log(j, 'squeeze_resumption_observed', attack=t,
                        prior_confirmation=resume.prior_confirmation_index,
                        local_resistance=resume.local_resistance, defense=resume.defense,
                        classification='held_squeeze_defense_and_fresh_pullback_rebound')
    secondary_resistance = {}
    if whole_wave:
        from .hierarchical_entry import hierarchical_history
        from .secondary_resistance import secondary_resistance_history
        secondary_levels, _ = hierarchical_history(bars)
    if hierarchical:
        from .hierarchical_entry import hierarchical_history, context_history, select_entry
        if whole_wave:
            from .chart_entry_history import chart_entry_history
            hierarchy_permissions, hierarchy_events = chart_entry_history(bars, audit=audit)
            secondary_resistance = secondary_resistance_history(
                bars, secondary_levels, key_events=hierarchy_events, include_resolved=True)
        else:
            levels, level_epochs = hierarchical_history(bars)
            hierarchy_permissions, hierarchy_events = context_history(bars, levels, level_epochs, whole_wave=False)
        for event in hierarchy_events:
            row = dict(event); j, kind = row.pop('bar_index'), row.pop('event')
            log(j, kind, **row); counts[kind] += 1
    multilevel_proofs = {}
    if whole_wave:
        from .multilevel_squeeze import multilevel_squeeze
        for candidate in candidates:
            if candidate['setup'].direction != Direction.UP:
                continue
            updated_frames = list(candidate['regime'].frames)
            for frame_index, frame in enumerate(candidate['regime'].frames):
                proof = multilevel_squeeze(bars, candidate, frame, hierarchy_events)
                if proof is None:
                    continue
                j = candidate['start'] + frame.bar_index
                multilevel_proofs[candidate['attack'], j] = proof
                if frame.regime is None:
                    confirmed = replace(frame, regime=MarketRegime.BULL, phase=RegimePhase.CONFIRMED,
                        resistance_outcome=ResistanceOutcome.FAILED, close_continuation=True,
                        last_confirmed_regime=MarketRegime.BULL, last_confirmed_index=frame.bar_index)
                    updated_frames[frame_index] = confirmed
                    events[j].append((candidate, confirmed))
                    counts['regime_'+MarketRegime.BULL.value] += 1
                    log(j, 'regime_confirmation', regime=MarketRegime.BULL.value, attack=candidate['attack'],
                        confirmation_source='multilevel_record_break')
                log(j, 'multilevel_squeeze_confirmed', attack=candidate['attack'], **proof)
            candidate['regime'] = replace(candidate['regime'], frames=tuple(updated_frames))
    # Projection outlives the entry candidate TTL, but never its frozen defense.
    # It remains available in the audit even when this N already emitted LONG.
    wave_events, wave_proofs = defaultdict(list), {}
    if whole_wave:
        from .wave_continuation import wave_gap_entry
        from dataclasses import asdict
        from ..market_structure.wave_projection import WaveProjectionSetup, wave_projection_history
        for candidate in candidates:
            if candidate['setup'].direction != Direction.UP:
                continue
            n = candidate['n']
            squeeze = next((candidate['start'] + f.bar_index for f in candidate['regime'].frames
                            if f.regime in (MarketRegime.BULL, MarketRegime.STRONG_BULL)), None)
            gap_confirmation = next((j for attack, j in consolidation_proofs if attack == candidate['attack']), None)
            if gap_confirmation is not None:
                squeeze = min(squeeze, gap_confirmation) if squeeze is not None else gap_confirmation
            if squeeze is None:
                continue
            projection_setup = WaveProjectionSetup(
                candidate['setup'].origin.index, candidate['attack'], squeeze,
                n.anchors.origin, n.targets.box_anchor, n.targets.two_t, n.completion.defense)
            log(max(squeeze, candidate['known_at']), 'wave_continuation_ready', wave_epoch=candidate['epoch'], **asdict(projection_setup))
            projection = wave_projection_history(bars, projection_setup)
            candidate['wave_projection'] = projection
            for j in range(squeeze + 1, len(bars)):
                proof = wave_gap_entry(bars, projection_setup, j, pivots=snapshots.get(j-1, ()))
                if proof is not None:
                    frame = next((f for f in candidate['regime'].frames if candidate['start'] + f.bar_index == squeeze),
                                 candidate['regime'].frames[0])
                    wave_events[j].append((candidate, frame))
                    wave_proofs[candidate['attack'], j] = proof
                    log(j, 'wave_gap_observed', attack=candidate['attack'], **proof)
            # Five/ten are larger structural goals. Entry feasibility continues
            # to use the nearest rung, never a farther goal to inflate reward.
            candidate['entry_wave_projection'] = wave_projection_history(
                bars, projection_setup, target_policy='nearest_box')
            for event in projection:
                row = asdict(event)
                j, kind = row.pop('bar_index'), row.pop('event')
                row['projection_label'] = {'stacking': '叠箱', 'pushing': '堆箱',
                    'ready': '二吐完成，等待转浪', 'pullback': 'B浪回调，等待再攻击',
                    'invalidated': '轧空低失守，转浪失效'}[event.state]
                log(j, kind, **row)
                counts[kind] += 1
    # Optional opportunity tags use their own confirmation dates, not future shape labels.
    wash_tags = {}
    for new in sorted(candidates, key=lambda c: c['attack']):
        for old in sorted(candidates, key=lambda c: c['attack'], reverse=True):
            if (old['setup'].direction != new['setup'].direction or old['epoch'] != new['epoch'] or
                    not 0 < new['attack']-old['attack'] <= config.structure_window or
                    old['attack'] >= new['setup'].origin.index):
                continue
            offset = old['start']
            try:
                result = observe_washout(bars[offset:new['attack']+1], _local_setup(old['setup'], offset),
                    timeframe='1d', policy=WashoutPolicy(MilestoneBasis.CLOSE),
                    reattack_setup=_local_setup(new['setup'], offset))
            except ValueError:
                counts['washout_pair_rejected'] += 1
                continue
            if result.latest is not None and result.latest.stage == WashoutStage.CONFIRMED:
                wash_tags[(new['setup'].direction, new['attack'])] = old['attack']
                log(new['attack'], 'washout_reattack', initial_attack=old['attack'], direction=new['setup'].direction.value)
                counts['washout_confirmed'] += 1
                break
    turn_events = {}
    turn_seen = set()
    for i, points in snapshots.items():
        if i in blocked or len(points) < 4:
            continue
        quartet = points[-4:]
        key = tuple(p.point.index for p in quartet)
        if key in turn_seen:
            continue
        turn_seen.add(key)
        a, b, c, d = quartet
        direction = Direction.UP if a.point.kind == PointKind.HIGH else Direction.DOWN
        history = [(j, f) for j, es in events.items() if epochs.get(j) == epochs[i] and j <= b.confirmed_index
                   for _, f in es]
        if not history:
            counts['turn_background_unavailable'] += 1
            continue
        j, f = max(history, key=lambda p: p[0])
        if (f.regime in (MarketRegime.BEAR, MarketRegime.STRONG_BEAR, MarketRegime.GRIND_DOWN)) != (direction == Direction.UP):
            counts['turn_background_opposite'] += 1
            continue
        ctx = observe_structure(snapshots[b.confirmed_index], symbol=bars[0].symbol, timeframe='1d',
            window_start=max(epochs[i], b.confirmed_index-config.structure_window), asof_index=b.confirmed_index)
        level = ctx.last_fall_high if direction == Direction.UP else ctx.last_rise_low
        if level is None:
            counts['turn_key_unavailable'] += 1
            continue
        try:
            setup = TurnSetup(bars[0].symbol, '1d', direction, *quartet,
                             RegimeContext(bars[0].symbol, '1d', f.regime, j, 'dated_six_regime_event'), level)
            line = None if minor_points is None else freeze_minor_line(minor_points, symbol=bars[0].symbol,
                timeframe='1d', direction=direction, selected_at_index=i, source='caller_supplied_minor_structure')
            end = i if line is None else min(limits[i], i+config.pattern_ttl)
            result = observe_market_turn(bars[:end+1], setup,
                policy=TurnPolicy(StrengthScale.EXACT_FRACTIONS, TurnThreshold.TWO_THIRDS,
                                  TurnThreshold.TWO_THIRDS, AttackBasis.CLOSE, AttackBasis.CLOSE), minor_line=line)
        except ValueError:
            counts['turn_setup_rejected'] += 1
            continue
        if result.suspicion_index is not None:
            counts['turn_suspicions'] += 1
            log(i, 'turn_observation', direction=direction.value, stage=result.stage.value,
                suspicion_index=result.suspicion_index, minor_line_available=line is not None)
        if result.line_break is not None:
            t = result.line_break.bar_index
            turn_events[t] = direction
            log(t, 'turn_confirmed', direction=direction.value)
            counts['turn_confirmed'] += 1
    signals = []
    def select_candidate(c,i):
        eligibility_attack = i if (c['attack'], i) in consolidation_proofs else c['attack']
        params=dict(attack=eligibility_attack,origin_index=c['setup'].origin.index,
            high_index=c['setup'].neckline.index,low_index=c['setup'].pullback.index,
            ratio=c['force'].ratio,shallow_ratio=config.mature_shallow_ratio,asof=i)
        if whole_wave:
            from .whole_wave_entry import select_wave_entry
            contexts = hierarchy_permissions.get(eligibility_attack,())
            if (c['attack'], i) in consolidation_proofs:
                contexts = sorted(contexts, key=lambda ctx: ctx.alternation_low_index, reverse=True)
                if any(ctx.confirmation_attack == c['attack'] and ctx.alternation_index == i for ctx in contexts):
                    # Today's independent gap may qualify B for the original
                    # defended N. Use only that joint confirmation, not a
                    # fabricated earlier alternation or unrelated old context.
                    params['attack'] = c['attack']
                    contexts = []
            selected, rejected = select_wave_entry(hierarchy_permissions.get(i,()),contexts,
                allow_confirming_n=True, allow_same_bar_pullback=c['setup'].allow_outside_close and c['setup'].pullback.index == c['attack'],
                bars=bars,deep_ratio=config.first_pullback_threshold,first_basis=config.first_pullback_basis,
                second_inclusive=config.mature_shallow_inclusive,**params)
            return (multilevel_proofs[c['attack'], i], '') if rejected and (c['attack'], i) in multilevel_proofs else (selected, rejected)
        return select_entry(hierarchy_permissions.get(i,()),hierarchy_permissions.get(c['attack'],()),**params)
    emitted_attacks, emitted_waves = set(), set()
    bearish_attacks = {c['attack']: c for c in candidates if c['setup'].direction == Direction.DOWN}
    for i, bar in enumerate(bars):
        exits = []
        if i in blocked:
            exits.append('strict_structure_unresolved')
        if i in bearish_attacks:
            exits.append('inverse_n_risk_exit')
        if turn_events.get(i) == Direction.DOWN:
            exits.append('negative_turn_risk_exit')
        if i and i not in blocked and epochs[i] < i:
            ctx = observe_structure(snapshots[i-1], symbol=bar.symbol, timeframe='1d',
                window_start=max(epochs[i], i-config.structure_window), asof_index=i-1)
            level = ctx.last_rise_low
            if level is not None and bars[i-1].close >= level.price > bar.close:
                exits.append('last_rise_low_close_broken')
        if exits:
            signals.append(Signal(bar.timestamp, bar.symbol, i, 'EXIT', bar.close, bar.high,
                '|'.join(exits), bar.timestamp, 0, None, 'risk_exit'))
            log(i, 'exit_signal', reason='|'.join(exits))
            continue
        choices = events.get(i, [])+resumptions.get(i, [])+consolidation_events.get(i, [])+wave_events.get(i, []) if config.regime_filter else [
            (c, c['regime'].frames[0]) for c in candidates if c['attack'] == i]
        def entry_priority(item):
            c, _ = item
            if not hierarchical or c['setup'].direction != Direction.UP:
                return (0, c['attack'])
            proof, _ = select_candidate(c,i)
            # Reconfirmation is a fallback for an interrupted N. It must not
            # displace an independently valid newer N or an established C wave.
            supplemental = (c['attack'], i) in reconfirmation_proofs and (c['attack'], i) not in wave_proofs
            return ((proof or {}).get('priority', 0), not supplemental, c['attack'])
        for c, frame in sorted(choices, key=entry_priority, reverse=True):
            consolidation = consolidation_proofs.get((c['attack'], i))
            wave = wave_proofs.get((c['attack'], i))
            wave_key = (c['attack'], wave['wave_a_high_index'], wave['wave_b_low_index']) if wave else None
            if wave_key in emitted_waves:
                continue
            if c['setup'].direction != Direction.UP or (c['attack'] in emitted_attacks and consolidation is None and wave is None) or epochs[i] != c['epoch']:
                continue
            counts['entry_candidate_evaluations'] += 1
            dual = multilevel_proofs.get((c['attack'], i))
            same_pressure = (dual is not None and i in secondary_resistance
                and secondary_resistance[i]['secondary_high'] <= dual['key_price']
                and secondary_resistance[i]['secondary_resistance_high'] <= dual['higher_resistance_high']
                and secondary_resistance[i]['secondary_attack_date'] == bars[c['attack']].timestamp.date().isoformat())
            wave_pressure_recovery = secondary_wave_recovery(bars, i, wave, secondary_resistance.get(i))
            if i in secondary_resistance and not secondary_resistance[i].get('secondary_resistance_resolved') and not same_pressure and wave_pressure_recovery is None:
                log(i, 'entry_rejected', reason='secondary_breakout_resistance_unresolved',
                    attack=c['attack'], **secondary_resistance[i])
                continue
            permission = permissions.get(i)
            resumed = (c['attack'],i) in resumption_keys
            entry_regime = MarketRegime.BULL if resumed or consolidation is not None or wave is not None else frame.regime
            # A bounce below the original breakout close cannot revive this N,
            # including entries supplied by the separate resumption observer.
            if whole_wave and bar.close <= bars[c['attack']].close:
                log(i, 'entry_rejected', reason='squeeze_below_original_n_close', attack=c['attack'],
                    confirmation_close=bar.close, n_attack_close=bars[c['attack']].close)
                continue
            # Weak attack quality is provisional: later genuine price discovery
            # can resolve it without requiring a second N and another response.
            record_squeeze = (entry_regime == MarketRegime.BULL
                and frame.first_defense_breach_index is None
                and frame.resistance is not None and frame.resistance.detected is False
                and bar.close > frame.continuation_level
                and bar.volume > bars[i-1].volume)
            if (whole_wave and c.get('attack_quality_warning') is not None
                    and entry_regime != MarketRegime.STRONG_BULL and consolidation is None
                    and wave is None and not record_squeeze and dual is None and (c['attack'], i) not in reversal_proofs
                    and (c['attack'], i) not in reconfirmation_proofs):
                log(i, 'entry_rejected', reason='weak_n_requires_uninterrupted_squeeze', attack=c['attack'])
                continue
            hierarchy_proof = None
            if hierarchical:
                if entry_regime not in (MarketRegime.BULL, MarketRegime.STRONG_BULL):
                    reject = 'not_squeeze_regime'
                else:
                    hierarchy_proof, reject = select_candidate(c,i)
                if reject:
                    log(i, 'entry_rejected', reason=reject, attack=c['attack'])
                    continue
                if hierarchy_proof is not None and secondary_resistance.get(i, {}).get('secondary_resistance_resolved'):
                    hierarchy_proof.update(secondary_resistance[i])
                if hierarchy_proof is not None and wave_pressure_recovery is not None:
                    hierarchy_proof.update(wave_pressure_recovery)
            if config.entry_policy == 'transitioned_squeeze':
                reject = ('not_squeeze_regime' if entry_regime not in (MarketRegime.BULL, MarketRegime.STRONG_BULL) else
                          'bullish_transition_not_ready' if permission is None else
                          'n_attack_not_after_bullish_confirmation' if not permission.permits(c['attack']) else
                          'attack_not_in_same_bullish_episode' if permissions.get(c['attack']) != permission else '')
                if reject:
                    counts['entry_rejected_'+reject] += 1
                    log(i, 'entry_rejected', reason=reject, attack=c['attack'])
                    continue
            if whole_wave:
                from .inverse_reentry import inverse_reentry_rejection, deep_pullback_recovery, fresh_strong_squeeze_recovery
                blocked_reentry = inverse_reentry_rejection(
                    bars, now=i, attack=i if (c['attack'], i) in reconfirmation_proofs else c['attack'],
                    inverse=inverse_reentry, gap=consolidation is not None or wave is not None)
                recovery = fresh_strong_squeeze_recovery(
                    bars, now=i, attack=c['attack'], origin=c['setup'].origin.index, inverse=inverse_reentry,
                    strong_squeeze=frame.regime == MarketRegime.STRONG_BULL)
                recovery = recovery or deep_pullback_recovery(
                    bars, now=i, attack=c['attack'], inverse=inverse_reentry, alternation=hierarchy_proof,
                    record_break=(frame.first_defense_breach_index is None
                                  and frame.regime in (MarketRegime.BULL, MarketRegime.STRONG_BULL)
                                  and bar.close > frame.continuation_level))
                recovery = recovery or inverse_wave_recovery(bars, i, wave, inverse_reentry)
                if blocked_reentry is not None and recovery is None:
                    log(i, 'entry_rejected', attack=c['attack'], **blocked_reentry)
                    continue
                if recovery is not None:
                    hierarchy_proof.update(recovery, recovery_resistance_high=frame.continuation_level)
            n = c['n']
            rvol = c['control'].volume.relative_volume
            if whole_wave:
                rvol = bar.volume/bars[i-1].volume if i and bars[i-1].volume > 0 else None
            volume_pass = (bar.volume > bars[i-1].volume if whole_wave and i else
                           rvol is not None and rvol >= config.minimum_rvol)
            # A verified C price-break path is the explicit alternative to volume.
            volume_pass = volume_pass or bool(wave and wave.get('wave_gap_trigger') in ('breakout', 'breakout_and_volume'))
            reject = ('countermove_too_deep' if not hierarchical and c['force'].ratio >= config.max_counter_ratio else
                      'attack_volume_unavailable_or_low' if config.volume_filter and not volume_pass else '')
            if reject:
                log(i, 'entry_rejected', reason=reject, attack=c['attack'])
                continue
            # A grind has lost the attack defense: its still-known original wave
            # boundary, not a fabricated tighter stop, is the remaining support.
            stop = n.anchors.origin if frame.first_defense_breach_index is not None else n.completion.defense
            hit = {m.name for m in n.milestones if m.bar_index+c['start'] <= i}
            targets = [getattr(n.targets, name) for name in ('equal_wave', 'one_p', 'two_t')
                       if name not in hit and getattr(n.targets, name) is not None and getattr(n.targets, name) > bar.close]
            projection = next((event for event in reversed(c.get('entry_wave_projection', ()))
                               if event.bar_index <= i), None)
            if (not targets and 'two_t' in hit and projection is not None
                    and projection.target is not None and projection.target > bar.close):
                targets = [projection.target]
            if wave is not None:
                stop, targets = wave['wave_defense'], [wave['wave_equal_target']]
            if stop >= bar.close or not targets:
                log(i, 'entry_rejected', reason='no_live_structural_risk_reward', attack=c['attack'])
                continue
            # A price-infeasible observation is NOT a submitted order. Keep the
            # N eligible for a later, freshly confirmed regime and target stage.
            # Never jump over an unhit nearer target merely to manufacture R.
            gross_rr = (targets[0]-bar.close)/(bar.close-stop)
            if config.preflight_reward_risk and gross_rr < config.minimum_reward_risk:
                counts['preflight_reward_risk_rejected'] += 1
                log(i, 'entry_preflight_rejected', reason='insufficient_close_gross_reward_risk',
                    attack=c['attack'], reference_price=bar.close, stop=stop, target=targets[0],
                    gross_reward_risk=gross_rr, required_reward_risk=config.minimum_reward_risk,
                    consumed_attack=False)
                continue
            tag = 'washout_reattack' if (Direction.UP, c['attack']) in wash_tags else 'n_continuation'
            if tag == 'n_continuation' and any(t < c['attack'] and c['attack']-t <= config.pattern_ttl and d == Direction.UP for t, d in turn_events.items()):
                tag = 'positive_turn_then_n'
            if resumed:
                tag = 'squeeze_pullback_resume'
            if hierarchy_proof is not None:
                tag = hierarchy_proof['buy_point_type']
            if wave is not None:
                tag = 'wave_push_gap'
            confirmation_source = (('one_p_wave_rebound' if wave.get('wave_a_class') == 'ordinary' else 'two_t_wave_push_gap') if wave is not None else
                'fresh_n_defeats_old_n_resistance' if (c['attack'], i) in reconfirmation_proofs else
                'volume_reversal_record_break' if (c['attack'], i) in reversal_proofs else
                'defended_n_consolidation_gap' if consolidation is not None else
                'pullback_resumption' if resumed else
                'resistance_record_break' if whole_wave and entry_regime == MarketRegime.BULL
                    and bar.close > frame.continuation_level else
                'local_resistance_failure' if whole_wave and entry_regime == MarketRegime.BULL else
                'uninterrupted_squeeze' if whole_wave else 'canonical_record_event')
            signals.append(Signal(bar.timestamp, bar.symbol, i, 'LONG', bar.close, stop,
                'system_'+tag, bars[c['attack']].timestamp, hierarchy_proof['counter_ratio'] if whole_wave and hierarchy_proof else c['force'].ratio, rvol,
                entry_regime.value if entry_regime else 'n_only_ablation', targets[0], config.minimum_reward_risk))
            emitted_attacks.add(c['attack'])
            if wave_key is not None:
                emitted_waves.add(wave_key)
            log(i, 'long_signal', channel=tag, attack=c['attack'], stop=stop, target=targets[0], rvol=rvol,
                **(dict(volume_basis='confirmation_volume_over_previous_session',
                        observed_volume=bar.volume, previous_volume=bars[i-1].volume,
                        volume_pass=volume_pass) if whole_wave else {}),
                weak_n_resolved_by_volume_record=bool(c.get('attack_quality_warning') and record_squeeze and wave is None),
                confirmation_record_high=wave['wave_gap_previous_high'] if wave is not None else frame.continuation_level,
                n_level=c['n_level'], n_origin_date=bars[c['setup'].origin.index].timestamp.date().isoformat(),
                n_neckline_date=bars[c['setup'].neckline.index].timestamp.date().isoformat(),
                n_pullback_date=bars[c['setup'].pullback.index].timestamp.date().isoformat(),
                **(dict(squeeze_confirmation=confirmation_source,
                        prior_bar_date=bars[i-1].timestamp.date().isoformat(),
                        prior_virtual_low=min(bars[i-1].low, bars[i-2].close),
                        confirmation_low=bar.low, confirmation_close=bar.close,
                        prior_close=bars[i-1].close) if whole_wave else {}),
                **(wave or {}), **(wave_pressure_recovery or {}), **(consolidation or {}), **reversal_proofs.get((c['attack'], i), {}),
                gross_reward_risk=gross_rr,
                **({'target_source': 'wave_equal_projection' if wave is not None else projection.state if projection is not None and projection.target == targets[0]
                    else 'n_measured_target'} if whole_wave else {}))
            if permission is not None:
                log(i, 'long_transition_evidence', attack=c['attack'], regime=entry_regime.value,
                    confirmation_source=confirmation_source,
                    **permission.__dict__)
            if hierarchy_proof is not None:
                counts['buy_point_'+hierarchy_proof['buy_point_type']] += 1
                log(i, 'long_transition_evidence', attack=c['attack'], regime=entry_regime.value,
                    confirmation_source=confirmation_source,
                    **hierarchy_proof)
            break
    counts['long_signals'] = sum(s.side == 'LONG' for s in signals)
    counts['exit_signals'] = sum(s.side == 'EXIT' for s in signals)
    # Count every gate consistently, including volume, depth and target rejection.
    # These are dated evaluations (not unique N shapes and not exchange rejects).
    rejections=Counter(r['reason'] for r in audit if r['event'] in ('entry_rejected','entry_preflight_rejected'))
    for reason,total in rejections.items(): counts['entry_rejected_'+reason]=total
    counts['entry_candidate_rejections']=sum(rejections.values())
    return SystemResult(sorted(signals, key=lambda s: (s.timestamp, s.side)),
                        sorted(audit, key=lambda r: (r['bar_index'], r['event'])), dict(counts))
