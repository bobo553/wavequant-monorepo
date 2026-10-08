"""Run the auditable strategy event pipeline without future-suffix leakage.

Strict lecture runs and daily fractal proxies are separate hypotheses. Canonical
observers produce dated evidence; only evidence available on a signal bar is used.
"""
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from typing import Any, Callable, Sequence, cast

from ..models.model import Bar, Signal
from ..market_structure.polyline import LinePoint, PointKind, ReversalPoint, observe_polyline, observe_bar_relations
from ..market_structure.n_shape import NSetup, PivotRef, BoxAnchorMode, MilestoneBasis, observe_n
from ..market_structure.bottom_n_targets import DeclineStart, PositiveNCompletion, bottom_n_target_history
from ..market_structure.positive_n_reformation import PositiveNSeed, positive_n_identity, positive_n_reformations
from ..market_structure.price_action import Direction, ShadowPolicy, AttackBasis, teaching_inside
from ..market_state.market_regime import MarketRegime, RegimePhase, RegimePolicy, ResistanceOutcome, WaveBoundary, observe_market_regime
from ..market_state.control_bar import observe_control_bar
from ..market_state.candle_strength import strong_bullish_candle
from ..market_structure.trend_structure import observe_structure
from .bull_eligibility import bull_permission_history
from .hierarchical_entry import EntryContext
from .shallow_base_breakout import ShallowAlternationCandidate
from .combined_a_entry import CombinedAContext, combined_a_entry_history
from .secondary_reclaim_entry import SecondaryHigh, secondary_reclaim_history
from .secondary_pullback_entry import SecondaryPullbackCandidate, secondary_pullback_history
from .attack_quality import v3_positive_n_attack_rejection
from .completed_wave_recovery import secondary_wave_recovery, inverse_wave_recovery
from .mother_child_inverse_n import MOTHER_CHILD_INVERSE_N_LOW_BREAK, mother_child_inverse_n_break
from .wave_exhaustion_exit import (
    C_EQUAL_NEAR_VOLUME_CLEAR, FIVE_TOP_CHILD_VOLUME_CLEAR, FIVE_TOP_GAP_VOLUME_CLEAR,
    observe_c_equal_near_risk, observe_five_top_child_volume_clear,
    observe_five_top_upper_shadow_clear, observe_five_top_gap_volume_clear,
)
from .wave_continuation import wave_confirmation_is_new, wave_confirmation_state
from .two_t_resistance import two_t_resistance_history
from .five_top_entry import five_top_entry_history
from .ten_full_entry import RetracementAnchor, ten_full_entry_history
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
    shallow_base_breakout_enabled: bool = True
    combined_a_entry_enabled: bool = False
    secondary_reclaim_entry_enabled: bool = False
    secondary_pullback_entry_enabled: bool = False
    ten_full_breakout_window: int = 23
    ten_full_retracement_ratio: float = 2/3
    ten_full_retracement_anchor: RetracementAnchor = 'origin'
    ten_full_timed_half_retracement: bool = True

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
        if type(self.shallow_base_breakout_enabled) is not bool:
            raise ValueError('shallow base breakout switch must be boolean')
        if type(self.combined_a_entry_enabled) is not bool:
            raise ValueError('combined A entry switch must be boolean')
        if type(self.secondary_reclaim_entry_enabled) is not bool:
            raise ValueError('secondary reclaim entry switch must be boolean')
        if type(self.secondary_pullback_entry_enabled) is not bool:
            raise ValueError('secondary pullback entry switch must be boolean')
        if type(self.ten_full_breakout_window) is not int or self.ten_full_breakout_window <= 0:
            raise ValueError('ten-full breakout window must be a positive integer')
        if (type(self.ten_full_retracement_ratio) not in (float, int)
                or self.ten_full_retracement_ratio not in (.5, 2/3)):
            raise ValueError('ten-full retracement ratio must be 1/2 or 2/3')
        if self.ten_full_retracement_anchor not in ('origin', 'b_low'):
            raise ValueError('ten-full retracement anchor must be origin or b_low')
        if type(self.ten_full_timed_half_retracement) is not bool:
            raise ValueError('ten-full timed half retracement switch must be boolean')
        if self.ten_full_timed_half_retracement and (
                self.ten_full_retracement_ratio != 2/3 or self.ten_full_retracement_anchor != 'origin'):
            raise ValueError('ten-full timed half needs A origin and 2/3 deep threshold')
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


def pivot_history(bars: Sequence[Bar], config: SystemStrategy, *, prefix_cache=None):
    """Snapshots are immutable tuples; strict ambiguity starts a NEW episode."""
    snapshots, epochs, limits, blocked = {}, {}, {}, set()
    n = len(bars)
    if config.pivot_mode=='lecture_causal':
        from .lecture_strategy import lecture_pivot_history
        return lecture_pivot_history(bars, prefix_cache=prefix_cache)
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
            known: list[ReversalPoint] = []
            for frame in line.frames:
                known.extend(frame.new_reversals)
                snapshots[frame.bar_index] = tuple(known)
                epochs[frame.bar_index] = start
                limits[frame.bar_index] = end-1
            if end < n:
                blocked.add(end)
            start = end+1
    else:
        points: list[ReversalPoint] = []
        w = config.pivot_width
        for i in range(n):
            if i >= 2*w:
                j = i-w
                others = [*bars[j-w:j], *bars[j+1:i+1]]
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


def generate_system_signals(bars: Sequence[Bar], config: SystemStrategy, *,
                            minor_points: Sequence[ReversalPoint] | None = None,
                            chart_history_cache: dict | None = None,
                            progress: Callable[[int], None] | None = None) -> SystemResult:
    config.validate()
    if not bars:
        return SystemResult([], [], {})
    from ..models.validated_bars import ValidatedBars
    bars = ValidatedBars(bars)
    if len({b.timestamp.date() for b in bars}) != len(bars):
        raise ValueError('daily bars required; map supplied minor pivots explicitly to daily index axis')
    snapshots, epochs, limits, blocked = pivot_history(
        bars, config, prefix_cache=chart_history_cache.setdefault('pivot', {}) if chart_history_cache is not None else None)
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
    hierarchy_permissions: dict[int, tuple[EntryContext, ...]] = {}
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
    larger = {}
    folded_sets: list[tuple[int, Sequence[ReversalPoint], int]] = []
    folded_cache = chart_history_cache.setdefault('folded_n', {}) if chart_history_cache is not None else None
    secondary_levels = None
    confirmed_declines: dict[int, tuple[DeclineStart, ...]] = {}
    if whole_wave and config.pivot_mode == 'lecture_causal':
        from .hierarchical_entry import hierarchical_history
        from .hierarchical_n import hierarchical_n_candidates
        from .folded_n import folded_positive_n_candidates, folded_inverse_n_candidates
        secondary_levels, _ = hierarchical_history(
            bars, prefix_cache=chart_history_cache.setdefault('hierarchy', {}) if chart_history_cache is not None else None,
            decline_sink=confirmed_declines)
        larger = hierarchical_n_candidates(bars, history=secondary_levels)
        for i in range(len(bars)):
            earliest = max(epochs[i], i-config.structure_window)
            folded_key = (snapshots[i], earliest)
            if folded_cache is not None and i < len(bars) - 1 and folded_key in folded_cache:
                folded = folded_cache[folded_key]
            else:
                folded = tuple([
                    *folded_positive_n_candidates(snapshots[i], earliest=earliest),
                    *folded_inverse_n_candidates(snapshots[i], earliest=earliest),
                ])
                if folded_cache is not None and i < len(bars) - 1:
                    folded_cache[folded_key] = folded
            folded_sets.extend((i, points, level) for points, level in folded)
    candidate_observations = None
    if chart_history_cache is not None:
        # IntradayEntry supplies the complete fixed series as a certificate:
        # every day before the partial last bar must match that source exactly.
        # This keeps earlier completed observations reusable across sessions.
        source_bars = chart_history_cache.get('source_bars')
        fixed_source = (
            isinstance(source_bars, tuple) and len(source_bars) >= len(bars)
            and tuple(bars[:-1]) == source_bars[:len(bars) - 1]
            and bars[-1].timestamp == source_bars[len(bars) - 1].timestamp
            and bars[-1].symbol == source_bars[len(bars) - 1].symbol
        )
        cache_scope = (
            ('source', config, id(source_bars)) if fixed_source
            else ('day', config, tuple(bars[:-1]))
        )
        if chart_history_cache.get('candidate_scope') != cache_scope:
            chart_history_cache['candidate_scope'] = cache_scope
            chart_history_cache['candidate_observations'] = {}
            chart_history_cache['washout_observations'] = {}
            chart_history_cache['wave_gap_observations'] = {}
            chart_history_cache['wave_selection_observations'] = {}
        candidate_observations = chart_history_cache['candidate_observations']
    washout_observations = chart_history_cache['washout_observations'] if chart_history_cache is not None else None
    wave_gap_observations = chart_history_cache['wave_gap_observations'] if chart_history_cache is not None else None
    candidate_sets = [(i, points, level) for i in range(len(bars))
                      for points, level in [(snapshots[i], 0), *larger.get(i, [])]]
    candidate_sets.extend(folded_sets)
    inverse_entry_risks: dict[int, dict[str, str | int | float | bool]] = {}
    if whole_wave:
        from .inverse_n_entry import inverse_n_low_entry_risk
        for now, known_points, _ in candidate_sets:
            risk = inverse_n_low_entry_risk(bars, now, known_points)
            if risk is not None and (
                now not in inverse_entry_risks
                or (str(risk['inverse_neckline_date']), str(risk['inverse_rebound_date']))
                > (str(inverse_entry_risks[now]['inverse_neckline_date']),
                   str(inverse_entry_risks[now]['inverse_rebound_date']))
            ):
                inverse_entry_risks[now] = risk
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
        same_bar_pullback = (whole_wave and config.pivot_mode == 'lecture_causal'
            and a.point.kind == PointKind.LOW and b.point.kind == PointKind.HIGH and c.point.kind == PointKind.LOW
            and a.point.index < b.point.index == c.point.index and b.point.ordinal < c.point.ordinal)
        inside_pullback = (same_bar_pullback and b.point.index > 0
                           and teaching_inside(bars[b.point.index-1], bars[b.point.index]))
        mother_pullback = same_bar_pullback and not inside_pullback
        if not (a.point.index<b.point.index<c.point.index or mother_impulse or same_bar_pullback):
            counts['same_bar_n_rejected']+=1
            log(i,'n_geometry_rejected',reason='same_bar_vertices_require_lower_timeframe_n')
            continue
        if i-a.point.index > config.structure_window * max(1,n_level):
            counts['expired_geometry'] += 1
            continue
        direction = Direction.UP if a.point.kind == PointKind.LOW else Direction.DOWN
        setup = NSetup(bars[0].symbol, '1d', direction,
            PivotRef(a.point.index, a.confirmed_index),
            PivotRef(b.point.index, b.confirmed_index),
            PivotRef(c.point.index, c.confirmed_index),
            config.pivot_mode, BoxAnchorMode.ATTACK_VIRTUAL_EXTREME,
            allow_confirmation_bar=whole_wave,
            allow_outside_close=whole_wave, allow_mother_impulse=mother_impulse,
            staged_defense=whole_wave and direction == Direction.UP,
            allow_mother_pullback=mother_pullback, allow_inside_pullback=inside_pullback)
        offset = max(0, a.point.index-config.volume_lookback)
        finish = min(len(bars)-1, limits[i], i+config.pattern_ttl)
        local, data = _local_setup(setup, offset), bars[offset:finish+1]
        # A candidate ending before the unfinished minute candle reads exactly
        # the same immutable daily slice in every replay of this session.
        memo = (
            candidate_observations.setdefault((local, offset, finish), {})
            if candidate_observations is not None and finish < len(bars) - 1 else None
        )
        counts['n_candidates'] += 1
        try:
            if memo is not None and 'n_error' in memo:
                raise ValueError(memo['n_error'])
            n = memo['n'] if memo is not None and 'n' in memo else observe_n(
                data, local, timeframe='1d', milestone_basis=MilestoneBasis.EXTREME)
            if memo is not None:
                memo['n'] = n
        except ValueError as exc:
            if memo is not None:
                memo['n_error'] = str(exc)
            counts['rejected_n_geometry'] += 1
            log(i, 'n_geometry_rejected', reason=str(exc))
            continue
        if n.completion is None:
            counts['n_'+n.status.value] += 1
            continue
        assert n.anchors is not None and n.targets is not None
        t = offset+n.completion.bar_index
        if (direction, t) in attack_keys and not inside_pullback:
            continue
        if not inside_pullback:
            attack_keys.add((direction, t))
        control = memo['control'] if memo is not None and 'control' in memo else observe_control_bar(
            data, local, timeframe='1d', volume_lookback=config.volume_lookback,
            shadow_policy=ShadowPolicy(config.shadow_fraction))
        if memo is not None:
            memo['control'] = control
        rejection = None
        if whole_wave and direction == Direction.UP:
            rejection = v3_positive_n_attack_rejection(bars[t], control.volume)
            if rejection is not None and config.strict_n_attack_quality:
                counts['rejected_v3_positive_n_attack'] += 1
                log(t, 'n_attack_rejected', direction=direction.value, reason=rejection,
                    known_at=i, previous_volume=bars[t-1].volume, attack_volume=bars[t].volume,
                    open=bars[t].open, high=bars[t].high, low=bars[t].low, close=bars[t].close)
                continue
        if memo is not None and 'effects' in memo:
            regime, force = memo['effects']
        else:
            regime = observe_market_regime(data, local, timeframe='1d',
                policy=RegimePolicy(ShadowPolicy(config.shadow_fraction), WaveBoundary.ORIGIN,
                                   local_resistance_failure=whole_wave and direction == Direction.UP))
            force = measure_strength(n.anchors.origin, n.anchors.neckline_extreme, n.anchors.pullback,
                                      impulse_direction=direction, scale=StrengthScale.EXACT_FRACTIONS)
            if memo is not None:
                memo['effects'] = (regime, force)
        candidate = dict(setup=setup, epoch=epochs[i], start=offset, finish=finish, n=n,
                         attack=t, known_at=i, regime=regime, control=control, force=force, n_level=n_level, attack_quality_warning=rejection)
        candidates.append(candidate)
        counts['completed_'+direction.value+'_n'] += 1
        log(t, 'n_completed', direction=direction.value, origin=a.point.index, neckline=b.point.index,
            pullback=c.point.index, known_at=i, defense=n.completion.defense, counter_ratio=force.ratio,
            n_level=n_level, box_anchor=n.targets.box_anchor, one_p=n.targets.one_p,
            two_t=n.targets.two_t,
            **({'mother_pullback_confirmed': True} if mother_pullback else {}),
            **({'inside_pullback_confirmed': True} if inside_pullback else {}),
            **({'outside_close_confirmed': True} if whole_wave and setup.allow_outside_close and setup.pullback.index == t else {}))
    bottom_targets = None
    target_retired_at = {}
    a_confirmations = {}
    if whole_wave and secondary_levels is not None:
        declines = []
        seed_decline = None
        formal_decline_seen = False
        for now, highs in confirmed_declines.items():
            high = highs[-1] if highs else None
            if high is not None:
                formal_decline_seen = True
                declines.append(DeclineStart(high.index, now))
            elif not formal_decline_seen:
                # A short history may already contain a confirmed initial
                # falling leg before it has enough turns for level-one reduction.
                if seed_decline is None:
                    seed = next((point for point in snapshots[now] if point.point.kind == PointKind.HIGH), None)
                    if seed is not None:
                        seed_decline = seed.point.index
                if seed_decline is not None:
                    declines.append(DeclineStart(seed_decline, now))
        bottom_targets = bottom_n_target_history(bars, declines, [
            PositiveNCompletion(c['setup'].origin.index, c['attack'], max(c['attack'], c['known_at']),
                                c['n'].completion.defense)
            for c in sorted(candidates, key=lambda item: item['setup'].allow_inside_pullback)
            if c['setup'].direction == Direction.UP
        ])
        canonical_candidates = []
        canonical_attacks = set()
        for candidate in sorted(candidates, key=lambda item: item['setup'].allow_inside_pullback):
            key = (candidate['setup'].direction, candidate['attack'])
            if candidate['setup'].direction == Direction.UP:
                chosen = bottom_targets.completions[candidate['attack']]
                if (candidate['setup'].origin.index != chosen.origin
                        or max(candidate['attack'], candidate['known_at']) != chosen.known_at):
                    continue
            if key not in canonical_attacks:
                canonical_candidates.append(candidate)
                canonical_attacks.add(key)
        candidates = canonical_candidates
        counts['completed_up_n'] = sum(c['setup'].direction == Direction.UP for c in candidates)
        retained_events = {(c['setup'].direction.value, c['attack'], c['setup'].origin.index,
                            c['setup'].neckline.index, c['setup'].pullback.index, c['known_at']) for c in candidates}
        audit[:] = [row for row in audit if row['event'] != 'n_completed'
                    or tuple(row[key] for key in ('direction', 'bar_index', 'origin', 'neckline', 'pullback', 'known_at'))
                    in retained_events]
        reforms = positive_n_reformations(bars, [
            PositiveNSeed(c['setup'].origin.index, c['attack'], max(c['attack'], c['known_at']),
                          c['n'].completion.defense, c['n_level'])
            for c in candidates if c['setup'].direction == Direction.UP
            and bottom_targets.qualifications[c['attack']].eligible
        ])
        reformed_by_source = {}
        for reformed in reforms:
            assert reformed.observation.completion is not None
            reformed_by_source[positive_n_identity(reformed.setup.origin.index, reformed.setup.neckline.index,
                reformed.setup.pullback.index, reformed.observation.completion.bar_index)] = reformed
        for candidate in candidates:
            candidate['reformed'] = (candidate['setup'].direction == Direction.UP
                and positive_n_identity(candidate['setup'].origin.index, candidate['setup'].neckline.index,
                    candidate['setup'].pullback.index, candidate['attack']) in reformed_by_source)
        # Entry selection keeps its canonical daily candidate; independently
        # measured Ns on the same candle retain their own audit identities.
        bottom_targets = bottom_n_target_history(bars, declines, [
            PositiveNCompletion(c['setup'].origin.index, c['attack'], max(c['attack'], c['known_at']),
                                c['n'].completion.defense, c['reformed'])
            for c in candidates if c['setup'].direction == Direction.UP
        ])
        for row in audit:
            if row['event'] != 'n_completed' or row['direction'] != 'up':
                continue
            qualification = bottom_targets.qualifications[row['bar_index']]
            row.update(target_eligible=qualification.eligible,
                       target_source_attack=qualification.source_attack,
                       target_decline_index=qualification.decline_index,
                       target_bottom_index=qualification.bottom_index,
                       target_qualification_reason=qualification.reason, target_primary=True)
            row_reformed = reformed_by_source.get(positive_n_identity(
                row['origin'], row['neckline'], row['pullback'], row['bar_index']))
            if row_reformed is not None:
                row.update(reformed_from=row_reformed.previous_attack, reformed_root=row_reformed.root_attack,
                           target_bottom_index=row['origin'])
            if not qualification.eligible:
                row.update(one_p=None, two_t=None)
        existing_ns = {positive_n_identity(row['origin'], row['neckline'], row['pullback'], row['bar_index']) for row in audit
                       if row['event'] == 'n_completed' and row['direction'] == 'up'}
        for reformed in reforms:
            setup = reformed.setup
            n = reformed.observation
            assert n.completion is not None and n.anchors is not None and n.targets is not None
            if positive_n_identity(setup.origin.index, setup.neckline.index, setup.pullback.index,
                                   n.completion.bar_index) in existing_ns:
                continue
            log(n.completion.bar_index, 'n_completed', direction='up', origin=setup.origin.index,
                neckline=setup.neckline.index, pullback=setup.pullback.index,
                known_at=setup.pullback.confirmed_index, defense=n.completion.defense,
                counter_ratio=n.anchors.retracement_ratio, n_level=reformed.level,
                box_anchor=n.targets.box_anchor, one_p=n.targets.one_p, two_t=n.targets.two_t,
                target_eligible=True, target_primary=False, target_source_attack=n.completion.bar_index,
                target_bottom_index=setup.origin.index, target_qualification_reason='defense_reformed_n',
                reformed_from=reformed.previous_attack, reformed_root=reformed.root_attack)
            counts['completed_up_n'] += 1
        for retired_source in bottom_targets.retirements:
            target_retired_at[retired_source.attack] = retired_source.bar_index
            log(retired_source.bar_index, 'n_target_source_retired', attack=retired_source.attack,
                reason=retired_source.reason)
        positive_events = [row for row in audit if row['event'] == 'n_completed' and row['direction'] == 'up']
        for row in positive_events:
            identity = positive_n_identity(row['origin'], row['neckline'], row['pullback'], row['bar_index'])
            row['n_id'] = identity
            row['target_source_id'] = identity if row['target_eligible'] else None
            for now in range(row['bar_index'] + 1, len(bars)):
                origin_broken = bars[now].low < bars[row['origin']].low
                if origin_broken or bars[now].low < row['defense']:
                    log(now, 'n_invalidated', n_id=identity, attack=row['bar_index'],
                        known_at=max(now, row['known_at']), direction='up',
                        reason='origin_broken' if origin_broken else 'squeeze_defense_broken')
                    break
        from dataclasses import asdict
        from ..market_structure.a_wave import AWaveSeed, AWaveTurn, a_wave_history
        invalidated_at = {row['n_id']: max(row['bar_index'], row['known_at']) for row in audit
                          if row['event'] == 'n_invalidated'}
        seeds = [AWaveSeed(
            row['n_id'], row['origin'], row['bar_index'], max(row['bar_index'], row['known_at']),
            row['one_p'], row['two_t'], row['defense'],
            min(invalidated_at.get(row['n_id'], len(bars)),
                target_retired_at.get(row['bar_index'], len(bars)) if row['target_primary'] else len(bars)),
        ) for row in positive_events if row['target_eligible']]
        turns = { (point.index, point.known_at) for highs in confirmed_declines.values() for point in highs }
        for observation in a_wave_history(bars, seeds, [AWaveTurn(*turn) for turn in sorted(turns)]):
            payload = asdict(observation)
            now, kind = payload.pop('bar_index'), payload.pop('event')
            for a_field in ('origin_index', 'attack_index', 'confirmed_index', 'strong_index',
                        'a_high_index', 'a_top_known_at', 'b_low_index', 'b_known_at', 'c_high_index', 'c_known_at'):
                index = payload[a_field]
                payload[a_field.replace('_index', '_date') if a_field.endswith('_index') else a_field + '_date'] = (
                    bars[index].timestamp.date().isoformat() if index is not None else None)
            payload.update(origin_price=bars[observation.origin_index].low,
                           a_high_price=bars[observation.a_high_index].high,
                           b_low_price=bars[observation.b_low_index].low if observation.b_low_index is not None else None,
                           c_high_price=bars[observation.c_high_index].high if observation.c_high_index is not None else None)
            log(now, kind, **payload)
            counts[kind] += 1
            if kind == 'a_wave_confirmed':
                a_confirmations[observation.source_id] = now
    bottom_candidates = {c['attack']: c for c in candidates
                         if bottom_targets is not None
                         and c['setup'].direction == Direction.UP
                         and bottom_targets.qualifications[c['attack']].eligible}
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
                reversal = volume_reversal_squeeze(bars, attack=t, now=j,
                    origin_low=n.anchors.origin, attack_defense=n.completion.defense,
                    frame=f, offset=offset)
                if reversal is not None:
                    close_break = next(k for k in range(t, j + 1)
                        if bars[k].close > n.anchors.neckline_extreme)
                    reversal_proofs[t, j] = dict(reversal,
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
    shallow_base_proofs: dict[int, dict[str, object]] = {}
    combined_a_proofs: dict[int, dict[str, object]] = {}
    secondary_reclaim_proofs: dict[int, dict[str, object]] = {}
    secondary_pullback_proofs: dict[int, dict[str, object]] = {}
    secondary_pullback_snapshots: dict[int, tuple[SecondaryPullbackCandidate, ...]] | None = (
        {} if whole_wave and config.secondary_pullback_entry_enabled else None)
    combined_candidate_snapshots: dict[int, tuple[CombinedAContext, ...]] | None = (
        {} if whole_wave and config.combined_a_entry_enabled else None)
    shallow_candidate_snapshots: dict[int, ShallowAlternationCandidate | None] | None = (
        {} if whole_wave and config.shallow_base_breakout_enabled else None)
    if whole_wave:
        from .hierarchical_entry import hierarchical_history
        from .secondary_resistance import secondary_resistance_history
        if secondary_levels is None:
            secondary_levels, _ = hierarchical_history(bars)
    if hierarchical:
        from .hierarchical_entry import hierarchical_history, context_history, select_entry
        if whole_wave:
            from .chart_entry_history import chart_entry_history
            hierarchy_permissions, hierarchy_events = chart_entry_history(
                bars, audit=audit, shallow_candidate_sink=shallow_candidate_snapshots,
                combined_candidate_sink=combined_candidate_snapshots,
                secondary_pullback_sink=secondary_pullback_snapshots,
                prefix_cache=chart_history_cache.setdefault('chart', {}) if chart_history_cache is not None else None)
            assert secondary_levels is not None
            secondary_resistance = secondary_resistance_history(
                bars, secondary_levels, key_events=hierarchy_events, include_resolved=True)
        else:
            levels, level_epochs = hierarchical_history(bars)
            hierarchy_permissions, hierarchy_events = context_history(bars, levels, level_epochs, whole_wave=False)
        for event in hierarchy_events:
            row = dict(event); j, kind = row.pop('bar_index'), row.pop('event')
            log(j, kind, **row); counts[kind] += 1
    if shallow_candidate_snapshots is not None:
        from .shallow_base_breakout import shallow_base_history
        shallow_events, shallow_base_proofs = shallow_base_history(
            bars, shallow_candidate_snapshots, minimum_reward_risk=config.minimum_reward_risk)
        for event in shallow_events:
            row = dict(event); j, kind = row.pop('bar_index'), row.pop('event')
            log(j, kind, **row); counts[kind] += 1
    if whole_wave and config.secondary_reclaim_entry_enabled:
        assert secondary_levels is not None
        secondary_highs = {now: [SecondaryHigh(point['index'], point['value'], point['available_at'])
                                for point in levels[2] if point['kind'] == 'H']
                           for now, levels in secondary_levels.items()}
        reclaim_events, secondary_reclaim_proofs = secondary_reclaim_history(bars, secondary_highs)
        for event in reclaim_events:
            row = dict(event)
            j, kind = cast(int, row.pop('bar_index')), cast(str, row.pop('event'))
            log(j, kind, **row); counts[kind] += 1
    multilevel_proofs = {}
    if secondary_pullback_snapshots is not None:
        pullback_events, secondary_pullback_proofs = secondary_pullback_history(bars, secondary_pullback_snapshots)
        for event in pullback_events:
            row = dict(event)
            j, kind = cast(int, row.pop('bar_index')), cast(str, row.pop('event'))
            log(j, kind, **row); counts[kind] += 1
    if combined_candidate_snapshots is not None:
        combined_events, combined_a_proofs = combined_a_entry_history(bars, combined_candidate_snapshots)
        for event in combined_events:
            row = dict(event)
            j, kind = cast(int, row.pop('bar_index')), cast(str, row.pop('event'))
            log(j, kind, **row)
            counts[kind] += 1
    nested_proofs = {}
    if whole_wave:
        from .multilevel_squeeze import multilevel_squeeze
        from .nested_alternation_breakout import nested_alternation_breakout
        for candidate in candidates:
            if candidate['setup'].direction != Direction.UP:
                continue
            updated_frames = list(candidate['regime'].frames)
            for frame_index, frame in enumerate(candidate['regime'].frames):
                j = candidate['start'] + frame.bar_index
                nested = nested_alternation_breakout(
                    bars, hierarchy_permissions.get(j - 1, ()), hierarchy_permissions.get(j, ()),
                    now=j, origin=candidate['setup'].origin.index, pullback=candidate['setup'].pullback.index,
                ) if frame.first_defense_breach_index is None else None
                if nested is not None:
                    nested_proofs[candidate['attack'], j] = nested
                    # Entry-only confirmation: the wave projection still waits
                    # for the N's own chronologically later squeeze frame.
                    confirmed = replace(frame, regime=MarketRegime.BULL, phase=RegimePhase.CONFIRMED,
                        resistance_outcome=ResistanceOutcome.FAILED, close_continuation=True,
                        last_confirmed_regime=MarketRegime.BULL, last_confirmed_index=frame.bar_index)
                    events[j].append((candidate, confirmed))
                    counts['nested_alternation_breakout_confirmed'] += 1
                    log(j, 'nested_alternation_breakout_confirmed', attack=candidate['attack'], **nested)
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
    # A/B/C outlives the N entry TTL and squeeze defense, until the A origin fails.
    # It remains available in the audit even when this N already emitted LONG.
    wave_events, wave_proofs = defaultdict(list), {}
    if whole_wave:
        from .wave_continuation import wave_gap_entry
        from dataclasses import asdict
        from ..market_structure.wave_projection import WaveProjectionSetup, wave_projection_history
        for candidate in candidates:
            if candidate['setup'].direction != Direction.UP:
                continue
            if bottom_targets is not None and candidate['attack'] not in bottom_candidates:
                continue
            identity = positive_n_identity(candidate['setup'].origin.index, candidate['setup'].neckline.index,
                                           candidate['setup'].pullback.index, candidate['attack'])
            a_confirmed_at = a_confirmations.get(identity)
            if secondary_levels is not None and a_confirmed_at is None:
                continue
            n = candidate['n']
            squeeze = next((candidate['start'] + f.bar_index for f in candidate['regime'].frames
                            if f.regime in (MarketRegime.BULL, MarketRegime.STRONG_BULL)), None)
            gap_confirmation = next((j for attack, j in consolidation_proofs if attack == candidate['attack']), None)
            if gap_confirmation is not None:
                squeeze = min(squeeze, gap_confirmation) if squeeze is not None else gap_confirmation
            if squeeze is None:
                continue
            retirement = target_retired_at.get(candidate['attack'], len(bars))
            measured_bars = bars[:retirement]
            projection_setup = WaveProjectionSetup(
                candidate['setup'].origin.index, candidate['attack'], squeeze,
                n.anchors.origin, n.targets.box_anchor, n.targets.two_t, n.completion.defense)
            log(max(squeeze, candidate['known_at']), 'wave_continuation_ready', wave_epoch=candidate['epoch'], **asdict(projection_setup))
            projection_history = (wave_projection_history(measured_bars, projection_setup)
                                  if squeeze < retirement else ())
            candidate['wave_projection'] = projection_history
            # C follows the historically qualified bottom N's complete A/B,
            # whose own lifetime is the A origin. A new local descending leg
            # ends named box guides, not this independent C entry observation.
            for j in range(squeeze + 1, len(bars)):
                gap_key = (projection_setup, a_confirmed_at, j)
                if wave_gap_observations is not None and j < len(bars) - 1 and gap_key in wave_gap_observations:
                    proof = wave_gap_observations[gap_key]
                else:
                    proof = wave_gap_entry(bars, projection_setup, j, pivots=snapshots.get(j-1, ()),
                                           a_confirmed_at=a_confirmed_at)
                    if wave_gap_observations is not None and j < len(bars) - 1:
                        wave_gap_observations[gap_key] = proof
                if proof is not None:
                    proof = dict(proof, wave_a_source_id=identity)
                    frame = next((f for f in candidate['regime'].frames if candidate['start'] + f.bar_index == squeeze),
                                 candidate['regime'].frames[0])
                    wave_events[j].append((candidate, frame))
                    wave_proofs[candidate['attack'], j] = proof
                    log(j, 'wave_gap_observed', attack=candidate['attack'], **proof)
            # Five/ten are larger structural goals. Entry feasibility continues
            # to use the nearest rung, never a farther goal to inflate reward.
            candidate['entry_wave_projection'] = wave_projection_history(
                bars, projection_setup, target_policy='nearest_box')
            for event in projection_history:
                row = asdict(event)
                j, kind = row.pop('bar_index'), row.pop('event')
                row['origin_index'] = projection_setup.origin_index
                row['projection_label'] = {'stacking': '叠箱', 'pushing': '堆箱',
                    'ready': '二吐完成，等待转浪', 'pullback': 'B浪回调，等待再攻击',
                    'invalidated': '最低价跌破A起点，转浪失效'}[event.state]
                log(j, kind, **row)
                counts[kind] += 1
            if max(squeeze, candidate['known_at']) < retirement < len(bars):
                log(retirement, 'wave_projection_invalidated', attack=candidate['attack'],
                    origin_index=projection_setup.origin_index, state='invalidated', target=None,
                    target_stage='five_top', reason='bottom_target_source_retired')
    # Optional opportunity tags use their own confirmation dates, not future shape labels.
    wash_tags = {}
    for new in sorted(candidates, key=lambda c: c['attack']):
        for old in sorted(candidates, key=lambda c: c['attack'], reverse=True):
            if (old['setup'].direction != new['setup'].direction or old['epoch'] != new['epoch'] or
                    not 0 < new['attack']-old['attack'] <= config.structure_window or
                    old['attack'] >= new['setup'].origin.index):
                continue
            offset = old['start']
            washout_bars = bars[offset:new['attack']+1]
            memo = (
                washout_observations.get((old['setup'], new['setup'], offset, new['attack']))
                if washout_observations is not None and new['attack'] < len(bars) - 1 else None
            )
            try:
                if memo is not None and memo[0] == 'error':
                    raise ValueError(memo[1])
                result = memo[1] if memo is not None else observe_washout(
                    washout_bars, _local_setup(old['setup'], offset),
                    timeframe='1d', policy=WashoutPolicy(MilestoneBasis.CLOSE),
                    reattack_setup=_local_setup(new['setup'], offset))
                if memo is None and washout_observations is not None and new['attack'] < len(bars) - 1:
                    washout_observations[old['setup'], new['setup'], offset, new['attack']] = ('result', result)
            except ValueError as exc:
                if memo is None and washout_observations is not None and new['attack'] < len(bars) - 1:
                    washout_observations[old['setup'], new['setup'], offset, new['attack']] = ('error', str(exc))
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
            turn_setup = TurnSetup(bars[0].symbol, '1d', direction,
                             quartet[0], quartet[1], quartet[2], quartet[3],
                             RegimeContext(bars[0].symbol, '1d', f.regime, j, 'dated_six_regime_event'), level)
            line = None if minor_points is None else freeze_minor_line(minor_points, symbol=bars[0].symbol,
                timeframe='1d', direction=direction, selected_at_index=i, source='caller_supplied_minor_structure')
            end = i if line is None else min(limits[i], i+config.pattern_ttl)
            result = observe_market_turn(bars[:end+1], turn_setup,
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
    emitted_secondary_reclaims: set[int] = set()
    emitted_secondary_pullbacks: set[tuple[int, int]] = set()
    exit_structure_cache = (
        chart_history_cache.setdefault('exit_structure', {}) if chart_history_cache is not None else None)
    current_selections: dict[tuple, tuple[dict[str, Any] | None, str]] = {}
    historical_selections = (
        chart_history_cache['wave_selection_observations'] if chart_history_cache is not None else None)
    def select_candidate(c,i):
        if (c['attack'], i) in nested_proofs:
            return dict(nested_proofs[c['attack'], i]), ''
        eligibility_attack = i if (c['attack'], i) in consolidation_proofs else c['attack']
        if whole_wave:
            selection_key = (
                c['setup'], c['epoch'], c['start'], c['n_level'], c['attack'],
                c['force'].ratio, eligibility_attack, i)
            selected_cache = (
                historical_selections if historical_selections is not None and i < len(bars) - 1
                else current_selections if chart_history_cache is not None else None
            )
            if selected_cache is not None and selection_key in selected_cache:
                selected, rejected = selected_cache[selection_key]
                # The caller may enrich its own proof with resistance and recovery.
                return (dict(selected) if selected is not None else None), rejected
        params=dict(attack=eligibility_attack,origin_index=c['setup'].origin.index,
            high_index=c['setup'].neckline.index,low_index=c['setup'].pullback.index,
            ratio=c['force'].ratio,shallow_ratio=config.mature_shallow_ratio,asof=i)
        if whole_wave:
            from .whole_wave_entry import select_wave_entry
            contexts = hierarchy_permissions.get(eligibility_attack,())
            if (c['attack'], i) in consolidation_proofs:
                contexts = tuple(sorted(contexts, key=lambda ctx: ctx.alternation_low_index, reverse=True))
                if any(ctx.confirmation_attack == c['attack'] and ctx.alternation_index == i for ctx in contexts):
                    # Today's independent gap may qualify B for the original
                    # defended N. Use only that joint confirmation, not a
                    # fabricated earlier alternation or unrelated old context.
                    params['attack'] = c['attack']
                    contexts = ()
            selected, rejected = select_wave_entry(hierarchy_permissions.get(i,()),contexts,
                allow_confirming_n=True, allow_same_bar_pullback=c['setup'].allow_outside_close and c['setup'].pullback.index == c['attack'],
                bars=bars,deep_ratio=config.first_pullback_threshold,first_basis=config.first_pullback_basis,
                second_inclusive=config.mature_shallow_inclusive,**params)
            outcome = ((multilevel_proofs[c['attack'], i], '')
                       if rejected and (c['attack'], i) in multilevel_proofs else (selected, rejected))
            if selected_cache is not None:
                selected_cache[selection_key] = (
                    dict(outcome[0]) if outcome[0] is not None else None, outcome[1])
            return outcome
        return select_entry(hierarchy_permissions.get(i,()),hierarchy_permissions.get(c['attack'],()),**params)
    emitted_attacks = set()
    emitted_nested = set()
    target_resistance = two_t_resistance_history(bars, audit) if whole_wave else {}
    projection_events = sorted(
        (event for event in audit if event["event"].startswith("wave_projection_")),
        key=lambda event: event["bar_index"],
    ) if whole_wave else []
    five_top_entry_risks = five_top_entry_history(bars, projection_events) if whole_wave else {}
    ten_full_entry_risks = ten_full_entry_history(
        bars, projection_events,
        breakout_window=config.ten_full_breakout_window,
        retracement_ratio=config.ten_full_retracement_ratio,
        anchor=config.ten_full_retracement_anchor,
        timed_half=config.ten_full_timed_half_retracement,
    ) if whole_wave else {}
    c_equal_events: list[dict] = []
    emitted_waves: dict[tuple[int, int, int], tuple[str, float]] = {}
    bearish_attacks = {c['attack']: c for c in candidates if c['setup'].direction == Direction.DOWN}
    bottom_hits: dict[int, set[str]] = {}
    last_progress = -1
    for i, bar in enumerate(bars):
        live_target_source = bottom_targets.source_at[i] if bottom_targets is not None else None
        if live_target_source is not None:
            measured = bottom_candidates[live_target_source]
            known = max(measured['attack'], measured['known_at'])
            observed = bar.close if i == known else bar.high
            reached = bottom_hits.setdefault(live_target_source, set())
            for stage in ('equal_wave', 'one_p', 'two_t'):
                target = getattr(measured['n'].targets, stage)
                if target is not None and observed >= target:
                    reached.add(stage)
        percent = i * 100 // len(bars)
        if progress is not None and percent != last_progress:
            progress(percent)
            last_progress = percent
        exits = []
        target_risk = target_resistance.get(i)
        five_top_entry_risk = five_top_entry_risks.get(i)
        ten_full_entry_risk = ten_full_entry_risks.get(i)
        inverse_entry_risk = inverse_entry_risks.get(i)
        if inverse_entry_risk is not None:
            log(i, 'inverse_n_entry_observed', **inverse_entry_risk)
        if target_risk is not None:
            log(i, 'target_resistance_observed', **target_risk)
            if target_risk['exit_fraction'] == 1.0:
                exits.append(str(target_risk['reason']))
        c_equal_risk = observe_c_equal_near_risk(bars, i, c_equal_events) if whole_wave else None
        if c_equal_risk is not None:
            log(i, 'c_equal_near_risk_observed', **c_equal_risk)
            if c_equal_risk['reason'] == C_EQUAL_NEAR_VOLUME_CLEAR:
                exits.append(C_EQUAL_NEAR_VOLUME_CLEAR)
        five_top_exit = observe_five_top_child_volume_clear(bars, i, projection_events) if whole_wave else None
        if five_top_exit is not None:
            exits.append(FIVE_TOP_CHILD_VOLUME_CLEAR)
        five_top_gap_exit = observe_five_top_gap_volume_clear(bars, i, projection_events) if whole_wave else None
        if five_top_gap_exit is not None and five_top_exit is None:
            exits.append(FIVE_TOP_GAP_VOLUME_CLEAR)
        five_top_shadow_exit = (
            observe_five_top_upper_shadow_clear(bars, i, projection_events)
            if (whole_wave and five_top_exit is None and five_top_gap_exit is None
                and (target_risk is None or target_risk['exit_fraction'] != 1.0)
                and (c_equal_risk is None or c_equal_risk['exit_fraction'] < 1.0)) else None
        )
        if five_top_shadow_exit is not None:
            exits.append(five_top_shadow_exit['reason'])
        mother_child_inverse = mother_child_inverse_n_break(bars, i) if whole_wave else None
        if mother_child_inverse is not None:
            exits.append(MOTHER_CHILD_INVERSE_N_LOW_BREAK)
        if i in blocked:
            exits.append('strict_structure_unresolved')
        if i in bearish_attacks:
            exits.append('inverse_n_risk_exit')
        if turn_events.get(i) == Direction.DOWN:
            exits.append('negative_turn_risk_exit')
        if i and i not in blocked and epochs[i] < i:
            window_start = max(epochs[i], i-config.structure_window)
            structure_key = (snapshots[i-1], bar.symbol, window_start, i-1)
            if exit_structure_cache is not None and i < len(bars) - 1 and structure_key in exit_structure_cache:
                ctx = exit_structure_cache[structure_key]
            else:
                ctx = observe_structure(snapshots[i-1], symbol=bar.symbol, timeframe='1d',
                    window_start=window_start, asof_index=i-1)
                if exit_structure_cache is not None and i < len(bars) - 1:
                    exit_structure_cache[structure_key] = ctx
            level = ctx.last_rise_low
            if level is not None and bars[i-1].close >= level.price > bar.close:
                exits.append('last_rise_low_close_broken')
        if exits:
            signals.append(Signal(bar.timestamp, bar.symbol, i, 'EXIT', bar.close, bar.high,
                '|'.join(exits), bar.timestamp, 0, None, 'risk_exit'))
            log(i, 'exit_signal', reason='|'.join(exits), **{
                **(mother_child_inverse or {}),
                **({key: value for key, value in c_equal_risk.items()
                    if key not in ('reason', 'exit_fraction', 'exit_target_fraction')}
                   if c_equal_risk is not None else {}),
                **(five_top_exit or {}),
                **({key: value for key, value in five_top_gap_exit.items() if key not in ('reason', 'exit_fraction')}
                   if five_top_gap_exit is not None and five_top_exit is None else {}),
                **({key: value for key, value in five_top_shadow_exit.items() if key != 'reason'}
                   if five_top_shadow_exit is not None else {}),
                **({key: value for key, value in target_risk.items() if key not in ('reason', 'exit_fraction')}
                   if target_risk is not None and target_risk['exit_fraction'] == 1.0 else {}),
            })
            continue
        if inverse_entry_risk is not None:
            log(i, 'entry_rejected', candidate_channel='global_inverse_n_low_guard', **inverse_entry_risk)
            continue
        if ten_full_entry_risk is not None:
            log(i, 'entry_rejected', candidate_channel='global_ten_full_pullback_guard', **ten_full_entry_risk)
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
            nested = nested_proofs.get((c['attack'], i))
            nested_key = (nested['secondary_low_index'], nested['primary_low_index']) if nested else None
            if nested_key in emitted_nested:
                continue
            consolidation = consolidation_proofs.get((c['attack'], i))
            wave = wave_proofs.get((c['attack'], i))
            wave_key = (
                (int(c['attack']), int(wave['wave_a_high_index']), int(wave['wave_b_low_index']))
                if wave is not None else None
            )
            if (wave is not None and wave_key is not None
                    and not wave_confirmation_is_new(wave, emitted_waves.get(wave_key))):
                continue
            # A defended, independently confirmed A/B/C wave outlives local
            # polyline resets. Its frozen defense and live hierarchy still gate
            # entry below; ordinary local N candidates remain epoch-scoped.
            if c['setup'].direction != Direction.UP or (c['attack'] in emitted_attacks and consolidation is None and wave is None) or (epochs[i] != c['epoch'] and wave is None):
                continue
            counts['entry_candidate_evaluations'] += 1
            if target_risk is not None:
                log(i, 'entry_rejected', **target_risk, candidate_attack=c['attack'])
                continue
            if five_top_entry_risk is not None:
                log(i, 'entry_rejected', **five_top_entry_risk, candidate_attack=c['attack'])
                continue
            dual = multilevel_proofs.get((c['attack'], i))
            same_pressure = (dual is not None and i in secondary_resistance
                and secondary_resistance[i]['secondary_high'] <= dual['key_price']
                and secondary_resistance[i]['secondary_resistance_high'] <= dual['higher_resistance_high']
                and secondary_resistance[i]['secondary_attack_date'] == bars[c['attack']].timestamp.date().isoformat())
            same_pressure = same_pressure or (nested is not None and i in secondary_resistance
                and secondary_resistance[i]['secondary_high'] <= nested['breakout_high']
                and secondary_resistance[i]['secondary_attack_date'] == bar.timestamp.date().isoformat())
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
            if whole_wave and nested is None and bar.close <= bars[c['attack']].close:
                log(i, 'entry_rejected', reason='squeeze_below_original_n_close', attack=c['attack'],
                    confirmation_close=bar.close, n_attack_close=bars[c['attack']].close)
                continue
            # Weak attack quality is provisional: later genuine price discovery
            # can resolve it without requiring a second N and another response.
            attack_bar_failure = (whole_wave and frame.regime == MarketRegime.BULL
                and frame.first_resistance_index is not None
                and c['start'] + frame.first_resistance_index <= c['attack'] + 1
                and i >= c['attack'] + 2
                and frame.first_defense_breach_index is None
                and bar.high > bars[c['attack']].high and bar.close > bars[c['attack']].close
                and bar.close > bars[i-1].close and strong_bullish_candle(bar))
            attack_bar_squeeze = attack_bar_failure and bar.volume > bars[i-1].volume
            record_squeeze = (entry_regime == MarketRegime.BULL
                and frame.first_defense_breach_index is None
                and frame.resistance is not None
                and (frame.resistance.detected is False or i > c['attack'] + 1)
                and bar.close > frame.continuation_level
                and bar.volume > bars[i-1].volume)
            if (whole_wave and c.get('attack_quality_warning') is not None
                    and entry_regime != MarketRegime.STRONG_BULL and consolidation is None
                    and wave is None and not record_squeeze and not attack_bar_squeeze
                    and dual is None and nested is None and (c['attack'], i) not in reversal_proofs
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
                                  and bar.close > frame.continuation_level),
                    attack_bar_break=attack_bar_failure)
                recovery = recovery or inverse_wave_recovery(bars, i, wave, inverse_reentry)
                if blocked_reentry is not None and recovery is None:
                    log(i, 'entry_rejected', attack=c['attack'], **blocked_reentry)
                    continue
                if recovery is not None:
                    assert hierarchy_proof is not None
                    hierarchy_proof.update(recovery, recovery_resistance_high=(bars[c['attack']].high
                        if recovery['inverse_reentry_path'] == 'deep_alternation_kill_high_attack_bar_squeeze'
                        else frame.continuation_level))
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
            measurement = c
            stages: tuple[str, ...] = ('equal_wave', 'one_p', 'two_t')
            if bottom_targets is not None:
                source = live_target_source
                measurement = bottom_candidates.get(source, c)
                if source is None:
                    stages = ('equal_wave',)
            measured_n = measurement['n']
            hit = (bottom_hits[live_target_source] if live_target_source is not None else
                   {m.name for m in measured_n.milestones if m.bar_index+measurement['start'] <= i})
            targets = [getattr(measured_n.targets, name) for name in stages
                       if name not in hit and getattr(measured_n.targets, name) is not None
                       and getattr(measured_n.targets, name) > bar.close]
            projection = next((event for event in reversed(measurement.get('entry_wave_projection', ()))
                               if event.bar_index <= i), None)
            if (not targets and 'two_t' in hit and projection is not None
                    and projection.target is not None and projection.target > bar.close):
                targets = [projection.target]
            if wave is not None:
                stop = wave['wave_defense']
                targets = [
                    target for target in (
                        (wave['wave_c_0618_target'], wave['wave_equal_target'])
                        if wave['wave_entry_path'] == 'two_t_strong_a_resistance_rebreak'
                        else (wave['wave_equal_target'],)
                    ) if cast(float, target) > bar.close
                ]
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
            confirmation_source = (('one_p_wave_rebound' if wave.get('wave_a_class') == 'ordinary' else
                                    'strong_a_resistance_rebreak' if wave.get('wave_entry_path') == 'two_t_strong_a_resistance_rebreak'
                                    else 'two_t_wave_push_gap') if wave is not None else
                'secondary_primary_volume_breakout' if nested is not None else
                'fresh_n_defeats_old_n_resistance' if (c['attack'], i) in reconfirmation_proofs else
                'volume_reversal_record_break' if (c['attack'], i) in reversal_proofs else
                'defended_n_consolidation_gap' if consolidation is not None else
                'pullback_resumption' if resumed else
                'gap_up_bullish_record' if whole_wave and entry_regime == MarketRegime.BULL
                    and frame.resistance is not None and frame.resistance.long_shadow is True
                    and bar.open > bars[i-1].high and bar.close > frame.continuation_level else
                'resistance_record_break' if whole_wave and entry_regime == MarketRegime.BULL
                    and bar.close > frame.continuation_level else
                'resistance_attack_bar_break' if attack_bar_failure else
                'local_resistance_failure' if whole_wave and entry_regime == MarketRegime.BULL else
                'uninterrupted_squeeze' if whole_wave else 'canonical_record_event')
            signals.append(Signal(bar.timestamp, bar.symbol, i, 'LONG', bar.close, stop,
                'system_'+tag, bars[c['attack']].timestamp, hierarchy_proof['counter_ratio'] if whole_wave and hierarchy_proof else c['force'].ratio, rvol,
                entry_regime.value if entry_regime else 'n_only_ablation', targets[0], config.minimum_reward_risk))
            emitted_attacks.add(c['attack'])
            if nested_key is not None:
                emitted_nested.add(nested_key)
            if wave is not None and wave_key is not None:
                emitted_waves[wave_key] = wave_confirmation_state(wave)
            log(i, 'long_signal', channel=tag, attack=c['attack'], stop=stop, target=targets[0], rvol=rvol,
                **(dict(target_source_attack=live_target_source,
                        target_source_id=(positive_n_identity(
                            bottom_candidates[live_target_source]['setup'].origin.index,
                            bottom_candidates[live_target_source]['setup'].neckline.index,
                            bottom_candidates[live_target_source]['setup'].pullback.index, live_target_source)
                            if live_target_source is not None else None),
                        target_source_date=(bars[live_target_source].timestamp.date().isoformat()
                                            if live_target_source is not None else None))
                   if bottom_targets is not None else {}),
                **(dict(volume_basis='confirmation_volume_over_previous_session',
                        observed_volume=bar.volume, previous_volume=bars[i-1].volume,
                        volume_pass=volume_pass) if whole_wave else {}),
                weak_n_resolved_by_volume_record=bool(c.get('attack_quality_warning') and record_squeeze and wave is None),
                weak_n_resolved_by_attack_bar_break=bool(c.get('attack_quality_warning') and attack_bar_squeeze
                                                       and not record_squeeze and wave is None),
                confirmation_record_high=wave['wave_gap_previous_high'] if wave is not None else frame.continuation_level,
                n_level=c['n_level'], n_origin_date=bars[c['setup'].origin.index].timestamp.date().isoformat(),
                n_neckline_date=bars[c['setup'].neckline.index].timestamp.date().isoformat(),
                n_pullback_date=bars[c['setup'].pullback.index].timestamp.date().isoformat(),
                **(dict(squeeze_confirmation=confirmation_source,
                        n_attack_high=bars[c['attack']].high, n_attack_close=bars[c['attack']].close,
                        n_resistance_date=(bars[c['start'] + frame.first_resistance_index].timestamp.date().isoformat()
                                           if frame.first_resistance_index is not None else None),
                        n_resistance_window_start=bars[c['attack']].timestamp.date().isoformat(),
                        n_resistance_window_end=(bars[c['attack'] + 1].timestamp.date().isoformat()
                                                 if c['attack'] + 1 <= i else None),
                        prior_bar_date=bars[i-1].timestamp.date().isoformat(),
                        prior_virtual_low=min(bars[i-1].low, bars[i-2].close),
                        confirmation_low=bar.low, confirmation_high=bar.high, confirmation_close=bar.close,
                        prior_close=bars[i-1].close) if whole_wave else {}),
                **(dict(confirmation_strong_bullish=True,
                        confirmation_body_open_ratio=(bar.close-bar.open)/bar.open,
                        confirmation_body_range_ratio=(bar.close-bar.open)/(bar.high-bar.low),
                        confirmation_upper_shadow_ratio=(bar.high-bar.close)/(bar.high-bar.low))
                   if confirmation_source == 'resistance_attack_bar_break' else {}),
                **(wave or {}), **(wave_pressure_recovery or {}), **(consolidation or {}), **reversal_proofs.get((c['attack'], i), {}),
                **(dict(wave_local_epoch_recovered=epochs[i] != c['epoch'],
                        wave_n_epoch=c['epoch'], entry_local_epoch=epochs[i]) if wave is not None else {}),
                gross_reward_risk=gross_rr,
                **({'target_source': ('wave_0618_projection' if wave['wave_entry_path'] == 'two_t_strong_a_resistance_rebreak'
                                      and targets[0] == wave['wave_c_0618_target'] else 'wave_equal_projection') if wave is not None else projection.state if projection is not None and projection.target == targets[0]
                    else 'n_measured_target'} if whole_wave else {}))
            if wave is not None and wave['wave_a_class'] == 'strong':
                log(i, 'wave_c_equal_target', attack=c['attack'], target=wave['wave_equal_target'],
                    defense=stop, owner_signal_index=i)
                c_equal_events.append(audit[-1])
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
        combined = combined_a_proofs.get(i)
        if combined is not None and not any(s.bar_index == i and s.side == 'LONG' for s in signals):
            combined_rejection = target_risk or five_top_entry_risk
            if combined_rejection is None and i in secondary_resistance and not secondary_resistance[i].get('secondary_resistance_resolved'):
                combined_rejection = dict(reason='secondary_breakout_resistance_unresolved',
                    **secondary_resistance[i])
            if combined_rejection is None:
                from .inverse_reentry import inverse_reentry_rejection
                combined_rejection = inverse_reentry_rejection(
                    bars, now=i, attack=i, inverse=inverse_reentry,
                    gap=combined.get('combined_a_breakout_type') == 'gap')
            if combined_rejection is not None:
                log(i, 'entry_rejected', candidate_channel='combined_a_pullback_breakout',
                    candidate_attack=i, **combined_rejection)
            else:
                combined_stop, combined_target = cast(float, combined['stop']), cast(float, combined['target'])
                combined_rvol = cast(float, combined['breakout_volume_multiple'])
                if not combined_stop < bar.close < combined_target:
                    log(i, 'entry_rejected', reason='no_live_structural_risk_reward',
                        candidate_channel='combined_a_pullback_breakout', candidate_attack=i,
                        stop=combined_stop, target=combined_target)
                else:
                    signals.append(Signal(bar.timestamp, bar.symbol, i, 'LONG', bar.close,
                        combined_stop, 'system_combined_a_pullback_breakout', bar.timestamp,
                        cast(float, combined['counter_ratio']), combined_rvol,
                        '组合A回调放量突破', combined_target, config.minimum_reward_risk))
                    counts['buy_point_combined_a_pullback_breakout'] += 1
                    log(i, 'long_signal', channel='combined_a_pullback_breakout', volume_pass=True,
                        stop=combined_stop, target=combined_target, rvol=combined_rvol)
                    log(i, 'long_transition_evidence', **combined)
        pullback = secondary_pullback_proofs.get(i)
        if pullback is not None:
            pullback = {key: value for key, value in pullback.items() if key != 'bar_index'}
            pullback_identity = cast(int, pullback['origin_index']), cast(int, pullback['flip_high_index'])
            if (pullback_identity not in emitted_secondary_pullbacks
                    and not any(s.bar_index == i and s.side == 'LONG' for s in signals)):
                pullback_rejection = target_risk or five_top_entry_risk
                pressure = secondary_resistance.get(i)
                if (pullback_rejection is None and pressure is not None
                        and not pressure.get('secondary_resistance_resolved')
                        and bar.close <= pressure['secondary_resistance_high']):
                    pullback_rejection = dict(reason='secondary_breakout_resistance_unresolved', **pressure)
                if pullback_rejection is None:
                    from .inverse_reentry import inverse_reentry_rejection
                    pullback_rejection = inverse_reentry_rejection(bars, now=i, attack=i, inverse=inverse_reentry,
                        gap=pullback['reclaim_type'] == 'gap')
                if pullback_rejection is not None:
                    log(i, 'entry_rejected', candidate_channel='secondary_deep_pullback_reclaim', **pullback_rejection)
                else:
                    pullback_rr = cast(float, pullback['gross_reward_risk'])
                    if config.preflight_reward_risk and pullback_rr < config.minimum_reward_risk:
                        log(i, 'entry_preflight_rejected', reason='insufficient_close_gross_reward_risk',
                            candidate_channel='secondary_deep_pullback_reclaim',
                            required_reward_risk=config.minimum_reward_risk, **pullback)
                    else:
                        signals.append(Signal(bar.timestamp, bar.symbol, i, 'LONG', bar.close,
                            cast(float, pullback['stop']), 'system_secondary_deep_pullback_reclaim', bar.timestamp,
                            cast(float, pullback['counter_ratio']), cast(float, pullback['breakout_volume_multiple']),
                            '二级深回调放量收复', cast(float, pullback['target']), config.minimum_reward_risk))
                        emitted_secondary_pullbacks.add(pullback_identity)
                        counts['buy_point_secondary_deep_pullback_reclaim'] += 1
                        log(i, 'long_signal', channel='secondary_deep_pullback_reclaim', volume_pass=True,
                            stop=pullback['stop'], target=pullback['target'], rvol=pullback['breakout_volume_multiple'])
                        log(i, 'long_transition_evidence', **pullback)
        reclaim = secondary_reclaim_proofs.get(i)
        if (reclaim is not None and cast(int, reclaim['secondary_attack_index']) not in emitted_secondary_reclaims
                and not any(s.bar_index == i and s.side == 'LONG' for s in signals)):
            reclaim_rejection = target_risk or five_top_entry_risk
            pressure = secondary_resistance.get(i)
            if (reclaim_rejection is None and pressure is not None
                    and not pressure.get('secondary_resistance_resolved')
                    and bar.close <= pressure['secondary_resistance_high']):
                reclaim_rejection = dict(reason='secondary_breakout_resistance_unresolved', **pressure)
            if reclaim_rejection is None:
                from .inverse_reentry import inverse_reentry_rejection
                reclaim_rejection = inverse_reentry_rejection(
                    bars, now=i, attack=i, inverse=inverse_reentry,
                    gap=reclaim['secondary_reclaim_type'] == 'gap')
            if reclaim_rejection is not None:
                log(i, 'entry_rejected', candidate_channel='secondary_resistance_reclaim', **reclaim_rejection)
            else:
                reclaim_attack = cast(int, reclaim['secondary_attack_index'])
                reclaimed_peak = cast(float, reclaim['secondary_observed_peak'])
                reclaim_targets: list[float] = []
                if secondary_levels is not None:
                    reclaim_targets.extend(point['value'] for point in secondary_levels.get(i - 1, {}).get(2, ())
                        if point['kind'] == 'H' and point['available_at'] < i and point['value'] > reclaimed_peak)
                reclaim_measurement = bottom_candidates.get(live_target_source) if live_target_source is not None else None
                if reclaim_measurement is not None:
                    assert live_target_source is not None
                    hit = bottom_hits[live_target_source]
                    reclaim_targets.extend(getattr(reclaim_measurement['n'].targets, stage)
                        for stage in ('equal_wave', 'one_p', 'two_t') if stage not in hit
                        and getattr(reclaim_measurement['n'].targets, stage) is not None
                        and getattr(reclaim_measurement['n'].targets, stage) > bar.high)
                    projection = next((event for event in reversed(reclaim_measurement.get('entry_wave_projection', ()))
                                       if event.bar_index <= i), None)
                    if projection is not None and projection.target is not None and projection.target > bar.high:
                        reclaim_targets.append(projection.target)
                reclaim_stop = cast(float, reclaim['stop'])
                reclaim_target = min(reclaim_targets, default=None)
                reclaim_rr = ((reclaim_target - bar.close) / (bar.close - reclaim_stop)
                              if reclaim_target is not None and reclaim_stop < bar.close else None)
                if reclaim_rr is None:
                    log(i, 'entry_rejected', reason='no_live_structural_risk_reward',
                        candidate_channel='secondary_resistance_reclaim', **reclaim)
                elif config.preflight_reward_risk and reclaim_rr < config.minimum_reward_risk:
                    log(i, 'entry_preflight_rejected', reason='insufficient_close_gross_reward_risk',
                        candidate_channel='secondary_resistance_reclaim', gross_reward_risk=reclaim_rr,
                        required_reward_risk=config.minimum_reward_risk, **reclaim)
                else:
                    reclaim = dict(reclaim, target=reclaim_target, gross_reward_risk=reclaim_rr,
                        target_policy='nearest_unhit_known_secondary_or_n_target_or_confirmed_projection')
                    signals.append(Signal(bar.timestamp, bar.symbol, i, 'LONG', bar.close,
                        reclaim_stop, 'system_secondary_resistance_reclaim', bar.timestamp,
                        cast(float, reclaim['counter_ratio']), cast(float, reclaim['breakout_volume_multiple']),
                        '二级突破抵抗放量收复', reclaim_target, config.minimum_reward_risk))
                    emitted_secondary_reclaims.add(reclaim_attack)
                    counts['buy_point_secondary_resistance_reclaim'] += 1
                    log(i, 'long_signal', channel='secondary_resistance_reclaim', volume_pass=True,
                        stop=reclaim_stop, target=reclaim_target, rvol=reclaim['breakout_volume_multiple'])
                    log(i, 'long_transition_evidence', **reclaim)
        special = shallow_base_proofs.get(i)
        if special is not None and not any(s.bar_index == i and s.side == 'LONG' for s in signals):
            if five_top_entry_risk is not None:
                log(i, 'entry_rejected', **five_top_entry_risk, candidate_attack=i,
                    candidate_channel='shallow_base_breakout')
                continue
            signals.append(Signal(bar.timestamp, bar.symbol, i, 'LONG', bar.close,
                cast(float, special['stop']), 'system_shallow_base_breakout', bar.timestamp,
                cast(float, special['counter_ratio']), cast(float, special['breakout_volume_multiple']),
                '待选交替横盘突破', cast(float, special['target']), config.minimum_reward_risk))
            emitted_attacks.add(i)
            counts['buy_point_shallow_base_breakout'] += 1
            log(i, 'long_signal', channel='shallow_base_breakout', volume_pass=True, **special)
            log(i, 'long_transition_evidence', **special)
    counts['long_signals'] = sum(s.side == 'LONG' for s in signals)
    counts['exit_signals'] = sum(s.side == 'EXIT' for s in signals)
    # Count every gate consistently, including volume, depth and target rejection.
    # These are dated evaluations (not unique N shapes and not exchange rejects).
    rejections=Counter(r['reason'] for r in audit if r['event'] in ('entry_rejected','entry_preflight_rejected'))
    for reason,total in rejections.items(): counts['entry_rejected_'+reason]=total
    counts['entry_candidate_rejections']=sum(rejections.values())
    if progress is not None:
        progress(100)
    return SystemResult(sorted(signals, key=lambda s: (s.timestamp, s.side)),
                        sorted(audit, key=lambda r: (r['bar_index'], r['event'])), dict(counts))
