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
from ..market_state.market_regime import MarketRegime, RegimePolicy, WaveBoundary, observe_market_regime
from ..market_state.control_bar import observe_control_bar
from ..market_structure.trend_structure import observe_structure
from .bull_eligibility import bull_permission_history
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
    volume_filter: bool = True
    regime_filter: bool = True
    entry_policy: str = 'transitioned_squeeze'
    preflight_reward_risk: bool = True
    squeeze_pullback_entries: bool = True
    mature_shallow_ratio: float = 1/3
    buy_point_definition: str = 'legacy_v2'
    first_pullback_threshold: float = .5

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
        if type(self.first_pullback_threshold) not in (float,int) or self.first_pullback_threshold not in (.5,2/3):
            raise ValueError('first pullback threshold must be 1/2 or 2/3')
        if self.buy_point_definition=='whole_flip_wave_v3' and (
                self.entry_policy!='hierarchical_two_buy_points' or self.mature_shallow_ratio not in (1/3,.5)):
            raise ValueError('whole wave entries require hierarchical policy and close threshold 1/3 or 1/2')
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
    def log(i, kind, **fields):
        audit.append(dict(timestamp=bars[i].timestamp.isoformat(), symbol=bars[i].symbol,
                          bar_index=i, event=kind, **fields))
    permissions = {}
    hierarchy_permissions = {}
    hierarchical = config.entry_policy == 'hierarchical_two_buy_points'
    whole_wave = config.buy_point_definition == 'whole_flip_wave_v3'
    if hierarchical:
        from .hierarchical_entry import hierarchical_history, context_history, select_entry
        levels, level_epochs = hierarchical_history(bars)
        hierarchy_permissions, hierarchy_events = context_history(bars, levels, level_epochs, whole_wave=whole_wave)
        for event in hierarchy_events:
            row = dict(event); j, kind = row.pop('bar_index'), row.pop('event')
            log(j, kind, **row); counts[kind] += 1
    if config.entry_policy == 'transitioned_squeeze':
        permissions, permission_events = bull_permission_history(
            bars, snapshots, epochs, blocked, config.structure_window,
            maximum_retracement=config.max_counter_ratio if config.pivot_mode=='lecture_causal' else None)
        for event in permission_events:
            row = dict(event)
            j, kind = row.pop('bar_index'), row.pop('event')
            log(j, kind, **row)
            counts[kind] += 1
    for i in range(len(bars)):
        points = snapshots[i]
        if i in blocked:
            log(i, 'strict_structure_interrupted')
            continue
        if len(points) < 3:
            counts['insufficient_pivots_bars'] += 1
            continue
        a, b, c = points[-3:]
        key = tuple((p.point.index, p.confirmed_index) for p in (a, b, c))
        if key in seen:
            continue
        seen.add(key)
        if not a.point.index<b.point.index<c.point.index:
            counts['same_bar_n_rejected']+=1
            log(i,'n_geometry_rejected',reason='same_bar_vertices_require_lower_timeframe_n')
            continue
        if i-a.point.index > config.structure_window:
            counts['expired_geometry'] += 1
            continue
        direction = Direction.UP if a.point.kind == PointKind.LOW else Direction.DOWN
        setup = NSetup(bars[0].symbol, '1d', direction,
            *(PivotRef(p.point.index, p.confirmed_index) for p in (a, b, c)),
            config.pivot_mode, BoxAnchorMode.ATTACK_VIRTUAL_EXTREME)
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
        regime = observe_market_regime(data, local, timeframe='1d',
            policy=RegimePolicy(ShadowPolicy(config.shadow_fraction), WaveBoundary.ORIGIN))
        force = measure_strength(n.anchors.origin, n.anchors.neckline_extreme, n.anchors.pullback,
                                  impulse_direction=direction, scale=StrengthScale.EXACT_FRACTIONS)
        candidate = dict(setup=setup, epoch=epochs[i], start=offset, finish=finish, n=n,
                         attack=t, regime=regime, control=control, force=force)
        candidates.append(candidate)
        counts['completed_'+direction.value+'_n'] += 1
        log(t, 'n_completed', direction=direction.value, origin=a.point.index, neckline=b.point.index,
            pullback=c.point.index, known_at=i, defense=n.completion.defense, counter_ratio=force.ratio)
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
        params=dict(attack=c['attack'],origin_index=c['setup'].origin.index,
            high_index=c['setup'].neckline.index,low_index=c['setup'].pullback.index,
            ratio=c['force'].ratio,shallow_ratio=config.mature_shallow_ratio,asof=i)
        if whole_wave:
            from .whole_wave_entry import select_wave_entry
            return select_wave_entry(hierarchy_permissions.get(i,()),hierarchy_permissions.get(c['attack'],()),
                bars=bars,deep_ratio=config.first_pullback_threshold,**params)
        return select_entry(hierarchy_permissions.get(i,()),hierarchy_permissions.get(c['attack'],()),**params)
    emitted_attacks = set()
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
        choices = events.get(i, [])+resumptions.get(i, []) if config.regime_filter else [
            (c, c['regime'].frames[0]) for c in candidates if c['attack'] == i]
        def entry_priority(item):
            c, _ = item
            if not hierarchical or c['setup'].direction != Direction.UP:
                return (0, c['attack'])
            proof, _ = select_candidate(c,i)
            return ((proof or {}).get('priority', 0), c['attack'])
        for c, frame in sorted(choices, key=entry_priority, reverse=True):
            if c['setup'].direction != Direction.UP or c['attack'] in emitted_attacks or epochs[i] != c['epoch']:
                continue
            counts['entry_candidate_evaluations'] += 1
            permission = permissions.get(i)
            resumed = (c['attack'],i) in resumption_keys
            entry_regime = MarketRegime.BULL if resumed else frame.regime
            hierarchy_proof = None
            if hierarchical:
                if entry_regime not in (MarketRegime.BULL, MarketRegime.STRONG_BULL):
                    reject = 'not_squeeze_regime'
                else:
                    hierarchy_proof, reject = select_candidate(c,i)
                if reject:
                    log(i, 'entry_rejected', reason=reject, attack=c['attack'])
                    continue
            if config.entry_policy == 'transitioned_squeeze':
                reject = ('not_squeeze_regime' if entry_regime not in (MarketRegime.BULL, MarketRegime.STRONG_BULL) else
                          'bullish_transition_not_ready' if permission is None else
                          'n_attack_not_after_bullish_confirmation' if not permission.permits(c['attack']) else
                          'attack_not_in_same_bullish_episode' if permissions.get(c['attack']) != permission else '')
                if reject:
                    counts['entry_rejected_'+reject] += 1
                    log(i, 'entry_rejected', reason=reject, attack=c['attack'])
                    continue
            n = c['n']
            rvol = c['control'].volume.relative_volume
            reject = ('countermove_too_deep' if not hierarchical and c['force'].ratio >= config.max_counter_ratio else
                      'attack_volume_unavailable_or_low' if config.volume_filter and (rvol is None or rvol < config.minimum_rvol) else '')
            if reject:
                log(i, 'entry_rejected', reason=reject, attack=c['attack'])
                continue
            # A grind has lost the attack defense: its still-known original wave
            # boundary, not a fabricated tighter stop, is the remaining support.
            stop = n.anchors.origin if frame.first_defense_breach_index is not None else n.completion.defense
            hit = {m.name for m in n.milestones if m.bar_index+c['start'] <= i}
            targets = [getattr(n.targets, name) for name in ('equal_wave', 'one_p', 'two_t')
                       if name not in hit and getattr(n.targets, name) is not None and getattr(n.targets, name) > bar.close]
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
            signals.append(Signal(bar.timestamp, bar.symbol, i, 'LONG', bar.close, stop,
                'system_'+tag, bars[c['attack']].timestamp, hierarchy_proof['counter_ratio'] if whole_wave and hierarchy_proof else c['force'].ratio, rvol,
                entry_regime.value if entry_regime else 'n_only_ablation', targets[0], config.minimum_reward_risk))
            emitted_attacks.add(c['attack'])
            log(i, 'long_signal', channel=tag, attack=c['attack'], stop=stop, target=targets[0], rvol=rvol,
                gross_reward_risk=gross_rr)
            if permission is not None:
                log(i, 'long_transition_evidence', attack=c['attack'], regime=entry_regime.value,
                    confirmation_source='pullback_resumption' if resumed else 'canonical_record_event',
                    **permission.__dict__)
            if hierarchy_proof is not None:
                counts['buy_point_'+hierarchy_proof['buy_point_type']] += 1
                log(i, 'long_transition_evidence', attack=c['attack'], regime=entry_regime.value,
                    confirmation_source='pullback_resumption' if resumed else 'canonical_record_event',
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
