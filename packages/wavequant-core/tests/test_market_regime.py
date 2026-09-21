from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta
import unittest

from wavequant.domain.models.model import Bar
from wavequant.domain.market_structure.n_shape import BoxAnchorMode, NSetup, NStatus, PivotRef
from wavequant.domain.market_structure.price_action import Direction, ShadowPolicy
from wavequant.domain.market_state.market_regime import (MarketRegime as R, RegimePhase as P,
    RegimePolicy, ResistanceOutcome as O, WaveBoundary, observe_market_regime)


BASE = [(8.5, 9, 8, 8.5), (10, 12, 9.5, 11), (10.7, 11, 10, 10.5),
        (10.8, 12.6, 10.4, 12.2)]
STRONG = [(12.2, 13.5, 12, 13.5), (13.5, 14.5, 13, 14.5)]
NORMAL = [(12.4, 12.5, 11, 11.5), (11.5, 13.5, 11.2, 13.5)]
GRIND = [(12.4, 12.5, 10.1, 11), (11, 11.8, 9.5, 10),
         (10, 12, 9.6, 11.8), (11.8, 13.5, 11.5, 13.5)]


def make(tail=STRONG):
    return [Bar(datetime(2026, 1, 1) + timedelta(days=i), 'TEST', *row, 1000)
            for i, row in enumerate(BASE + tail)]


def setup(direction=Direction.UP):
    return NSetup('TEST', '1d', direction, PivotRef(0, 0), PivotRef(1, 1),
                  PivotRef(2, 2), 'known_test_pivots', BoxAnchorMode.ATTACK_VIRTUAL_EXTREME)


def mirror(bars):
    return [replace(b, open=40-b.open, high=40-b.low, low=40-b.high, close=40-b.close)
            for b in bars]


def run(bars=None, *, direction=Direction.UP, shadow=ShadowPolicy(.5),
        boundary=WaveBoundary.ORIGIN, **kw):
    return observe_market_regime(make() if bars is None else bars, setup(direction),
        timeframe='1d', policy=RegimePolicy(shadow, boundary), **kw)


class MarketRegimeTests(unittest.TestCase):
    def test_local_failed_resistance_does_not_wait_for_whole_episode_high(self):
        bars = make([(12.4, 13.5, 11.8, 12.4), (12.4, 13.2, 12.0, 12.8)])
        policy = RegimePolicy(ShadowPolicy(.5), WaveBoundary.ORIGIN, local_resistance_failure=True)
        self.assertIsNone(run(bars).latest.regime)
        observed = observe_market_regime(bars, setup(), timeframe='1d', policy=policy)
        self.assertEqual(observed.latest.regime, R.BULL)
        self.assertEqual(observed.latest.resistance_outcome, O.FAILED)
        self.assertIsNone(observe_market_regime(bars[:-1], setup(), timeframe='1d', policy=policy).latest.regime)
        broken = bars[:-1] + [replace(bars[-1], low=11.7)]
        self.assertIsNone(observe_market_regime(broken, setup(), timeframe='1d', policy=policy).latest.regime)

    def test_all_six_regimes(self):
        for tail, bull, bear in [(STRONG, R.STRONG_BULL, R.STRONG_BEAR),
                                 (NORMAL, R.BULL, R.BEAR),
                                 (GRIND, R.GRIND_UP, R.GRIND_DOWN)]:
            with self.subTest(bull=bull):
                self.assertEqual(run(make(tail)).latest.regime, bull)
                self.assertEqual(run(mirror(make(tail)), direction=Direction.DOWN).latest.regime, bear)

    def test_attack_response_third_bar_timing(self):
        r = run()
        self.assertEqual([f.phase for f in r.frames],
                         [P.AWAIT_RESPONSE, P.AWAIT_CONFIRMATION, P.CONFIRMED])
        self.assertEqual(r.attack_index, 3)
        self.assertIsNone(r.frames[1].regime)
        self.assertEqual(r.latest.bar_index, 5)

    def test_grind_waits_more_than_three_bars(self):
        r = run(make(GRIND))
        self.assertEqual(r.frames[2].phase, P.PENDING)
        self.assertEqual(r.latest.bar_index, 7)
        self.assertEqual(r.latest.resistance_outcome, O.SUCCEEDED_LOCALLY)

    def test_resistance_is_not_failure_until_continuation(self):
        r = run(make(NORMAL))
        self.assertEqual(r.frames[1].resistance_outcome, O.PENDING)
        self.assertEqual(r.latest.resistance_outcome, O.FAILED)

    def test_unknown_shadow_does_not_mean_no_resistance(self):
        bars = make([(12.2, 13.7, 12, 13.5), STRONG[1]])
        r = run(bars, shadow=None)
        self.assertTrue(r.latest.unknown_resistance_seen)
        self.assertEqual(r.latest.resistance_outcome, O.UNKNOWN)
        self.assertIsNone(r.latest.regime)
        self.assertEqual(run(bars).latest.regime, R.STRONG_BULL)

    def test_no_shadow_needs_no_threshold(self):
        self.assertEqual(run(shadow=None).latest.regime, R.STRONG_BULL)

    def test_unknown_does_not_hide_positive_resistance_evidence(self):
        bars = make([(12.2, 13.7, 12, 13.5), (13.4, 13.6, 12, 12.5),
                     (12.5, 14.5, 12.3, 14.5)])
        r = run(bars, shadow=None)
        self.assertTrue(r.latest.unknown_resistance_seen)
        self.assertEqual(r.latest.regime, R.BULL)

    def test_wave_failure_is_absorbing_not_automatic_bear(self):
        bars = make(NORMAL + [(13.5, 14, 7.9, 13.8), (13.8, 16, 13, 16)])
        r = run(bars)
        self.assertEqual(r.latest.phase, P.INVALIDATED)
        self.assertIsNone(r.latest.regime)
        self.assertEqual(r.latest.last_confirmed_regime, R.BULL)
        self.assertEqual(r.latest.last_confirmed_index, 5)
        self.assertEqual(r.latest.first_wave_breach_index, 6)

    def test_wave_break_overrides_same_bar_new_high(self):
        r = run(make([NORMAL[0], (11.5, 16, 7.9, 15)]))
        self.assertTrue(r.latest.close_continuation)
        self.assertEqual(r.latest.phase, P.INVALIDATED)

    def test_defense_break_and_new_high_same_bar_deferred(self):
        r = run(make([NORMAL[0], (11.5, 14, 9, 13.5), (13.5, 15, 12, 15)]))
        self.assertEqual(r.frames[2].phase, P.PENDING)
        self.assertEqual(r.latest.regime, R.GRIND_UP)

    def test_first_resistance_on_confirmation_bar_deferred(self):
        r = run(make([STRONG[0], (13.4, 14.5, 13, 14.5), (14.5, 15.5, 14, 15.5)]))
        self.assertEqual(r.frames[2].phase, P.PENDING)
        self.assertEqual(r.latest.regime, R.BULL)

    def test_strict_continuation_equality_and_wick_not_sufficient(self):
        for row in [(11.5, 14, 11, 12.6), (11.5, 14, 11, 12.5)]:
            with self.subTest(row=row):
                r = run(make([NORMAL[0], row]))
                self.assertFalse(r.latest.close_continuation)
                self.assertIsNone(r.latest.regime)

    def test_continuation_uses_all_prior_extremes(self):
        r = run(make([NORMAL[0], (11.5, 15, 11, 12), (12, 14.5, 11.5, 14)]))
        self.assertEqual(r.latest.continuation_level, 15)
        self.assertIsNone(r.latest.regime)

    def test_defense_equality_is_held(self):
        r = run(make([(12.4, 12.5, 10.4, 11.5), NORMAL[1]]))
        self.assertIsNone(r.latest.first_defense_breach_index)
        self.assertEqual(r.latest.regime, R.BULL)

    def test_wave_equality_is_held(self):
        r = run(make([(12.4, 12.5, 8, 11.5), NORMAL[1]]))
        self.assertIsNone(r.latest.first_wave_breach_index)
        self.assertEqual(r.latest.regime, R.GRIND_UP)

    def test_boundary_choice_is_material_and_explicit(self):
        self.assertEqual(run(make(GRIND)).latest.regime, R.GRIND_UP)
        r = run(make(GRIND), boundary=WaveBoundary.PULLBACK)
        self.assertEqual(r.wave_boundary, 10)
        self.assertEqual(r.latest.phase, P.INVALIDATED)

    def test_no_resistance_without_progress_is_not_strong(self):
        r = run(make([(12.2, 12.2, 11, 12.2), (12.2, 14, 12, 14)]))
        self.assertEqual(r.latest.resistance_outcome, O.ABSENT)
        self.assertFalse(r.latest.uninterrupted_progress)
        self.assertIsNone(r.latest.regime)

    def test_strong_requires_rolling_virtual_support(self):
        r = run(make([STRONG[0], (13.5, 14.5, 11.5, 14.5)]))
        self.assertEqual(r.latest.rolling_virtual_defense, 12)
        self.assertFalse(r.latest.rolling_defense_held)
        self.assertIsNone(r.latest.regime)

    def test_gap_uses_virtual_not_actual_low(self):
        r = run(make([(13, 14, 13, 14), (14, 15, 12.5, 15)]))
        self.assertEqual(r.latest.rolling_virtual_defense, 12.2)
        self.assertTrue(r.latest.rolling_defense_held)
        self.assertEqual(r.latest.regime, R.STRONG_BULL)

    def test_confirmed_state_not_carried_onto_pending_bar(self):
        r = run(make(STRONG + [(14.5, 14.5, 14, 14.5)]))
        self.assertEqual(r.latest.phase, P.PENDING)
        self.assertIsNone(r.latest.regime)
        self.assertEqual(r.latest.last_confirmed_regime, R.STRONG_BULL)

    def test_strong_can_transition_to_squeeze_and_grind(self):
        bars = make(STRONG + [(14.4, 14.5, 12, 13), (13, 16, 12, 16),
                             (15.5, 16, 9, 11), (11, 18, 10, 18)])
        r = run(bars)
        self.assertEqual([f.regime for f in r.frames if f.regime],
                         [R.STRONG_BULL, R.BULL, R.GRIND_UP])

    def test_no_n_no_regime_or_future_anchor_leak(self):
        r = run(make()[:2])
        self.assertEqual(r.n_status, NStatus.AWAIT_ANCHORS)
        self.assertIsNone(r.latest)
        self.assertIsNone(r.wave_boundary)
        self.assertIsNone(run(make()[:3]).attack_index)

    def test_prefix_invariance_including_later_failure(self):
        for direction in (Direction.UP, Direction.DOWN):
            bars = make(GRIND + [(13.5, 14, 7, 8)])
            if direction == Direction.DOWN:
                bars = mirror(bars)
            full = run(bars, direction=direction)
            for end in range(len(bars)):
                short = run(bars[:end+1], direction=direction)
                self.assertEqual(short, run(bars, direction=direction, asof_index=end))
                self.assertEqual(short.frames, tuple(f for f in full.frames if f.bar_index <= end))

    def test_invalid_future_not_read(self):
        bars = make() + [replace(make()[-1], close=float('nan'))]
        self.assertEqual(run(bars, asof_index=5), run(make()))
        with self.assertRaises(ValueError):
            run(bars)

    def test_validates_policy_timeframe_symbol_and_order(self):
        with self.assertRaises(ValueError):
            RegimePolicy(.5, WaveBoundary.ORIGIN)
        with self.assertRaises(ValueError):
            RegimePolicy(None, 'origin')
        with self.assertRaises(ValueError):
            observe_market_regime(make(), setup(), timeframe='1h',
                                  policy=RegimePolicy(None, WaveBoundary.ORIGIN))
        with self.assertRaises(ValueError):
            observe_market_regime(make(), setup(), timeframe='1d', policy=None)
        for field in ({'symbol': 'OTHER'}, {'timestamp': make()[4].timestamp}):
            bars = make()
            bars[5] = replace(bars[5], **field)
            with self.assertRaises(ValueError):
                run(bars)

    def test_immutable_and_volume_independent(self):
        r = run()
        with self.assertRaises(FrozenInstanceError):
            r.latest.regime = R.BEAR
        self.assertEqual(r, run([replace(b, volume=0) for b in make()]))


if __name__ == '__main__':
    unittest.main()
