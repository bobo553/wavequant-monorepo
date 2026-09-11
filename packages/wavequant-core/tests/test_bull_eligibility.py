from dataclasses import replace
from datetime import datetime, timedelta
import unittest
from unittest.mock import patch

from wavequant.model import Bar
from wavequant.polyline import LinePoint, PointKind, ReversalPoint
from wavequant.bull_eligibility import BullPermission, bull_permission_history
from wavequant.integrated_strategy import SystemStrategy, generate_system_signals
from wavequant.market_regime import MarketRegime, observe_market_regime
from tests.test_integrated_strategy import fixture, config


def transition_fixture(counter=18):
    rows = [(18,20,16,18), (13,16,10,13), (16,18,13,16), (12,15,8,12),
            (13,17,9,14), (17,21,15,19), (21,23,19,22), (21,22,19,20),
            (20,21,counter,20), (21,23,20,22), (22,25,21,24), (24,26,22,25)]
    bars = [Bar(datetime(2020,1,1)+timedelta(days=i), 'TEST', *r, 1000000)
            for i,r in enumerate(rows)]
    points = [ReversalPoint(LinePoint(i,0,k, bars[i].high if k == PointKind.HIGH else bars[i].low), c, 'test')
              for i,c,k in [(0,1,PointKind.HIGH), (1,2,PointKind.LOW),
                            (2,3,PointKind.HIGH), (3,4,PointKind.LOW),
                            (6,7,PointKind.HIGH), (8,9,PointKind.LOW)]]
    snapshots = {i: tuple(p for p in points if p.confirmed_index <= i) for i in range(len(bars))}
    return bars, snapshots, {i:0 for i in range(len(bars))}


class BullEligibilityTests(unittest.TestCase):
    def test_sequence_and_old_attack_exclusion(self):
        bars, snapshots, epochs = transition_fixture()
        history, events = bull_permission_history(bars, snapshots, epochs, set(), 120)
        self.assertTrue(all(history[i] is None for i in range(9)))
        p = history[9]
        self.assertEqual((p.context_index,p.flip_index,p.alternation_index,p.bullish_index), (4,5,9,9))
        self.assertFalse(p.permits(5))
        self.assertFalse(p.permits(9))
        self.assertTrue(p.permits(10))
        self.assertEqual([e['event'] for e in events], ['bearish_context_frozen',
            'bear_to_bull_flip','bear_bull_alternation','bullish_entry_permission_confirmed'])

    def test_full_prefix_invariance_including_event_dates(self):
        bars, snapshots, epochs = transition_fixture()
        full, events = bull_permission_history(bars, snapshots, epochs, set(), 120)
        for end in range(len(bars)):
            short, es = bull_permission_history(bars[:end+1], snapshots, epochs, set(), 120)
            self.assertEqual(short, {i:p for i,p in full.items() if i<=end})
            self.assertEqual(es, [e for e in events if e['bar_index']<=end])

    def test_67_percent_boundary_not_alternation(self):
        bars, snapshots, epochs = transition_fixture(counter=12.95)  # (23-12.95)/(23-8)=.67
        history, events = bull_permission_history(bars, snapshots, epochs, set(), 120)
        self.assertTrue(all(p is None for p in history.values()))
        self.assertTrue(any(e.get('reason')=='countermove_not_below_67_percent' for e in events))

    def test_bottom_failure_cannot_reuse_old_flip(self):
        bars, snapshots, epochs = transition_fixture()
        bars[8] = replace(bars[8], low=7)
        # Do not publish a fabricated pivot after changing its source low.
        snapshots = {i: tuple(p for p in ps if p.point.index!=8) for i,ps in snapshots.items()}
        history, events = bull_permission_history(bars, snapshots, epochs, set(), 120)
        self.assertTrue(all(p is None for p in history.values()))
        self.assertTrue(any(e.get('reason')=='frozen_bottom_breached' for e in events))

    def test_known_last_rise_low_break_revokes_permission(self):
        bars, snapshots, epochs = transition_fixture()
        # At 9 the window high is 23 at 6, preceding confirmed low is 8 at 3.
        # A later confirmed high updates the last-rise key to 18 at 8.
        p = ReversalPoint(LinePoint(10,0,PointKind.HIGH,25),11,'test')
        bars.append(Bar(bars[-1].timestamp+timedelta(days=1),'TEST',20,21,16,17,1000000))
        snapshots[11] += (p,)
        snapshots[12] = snapshots[11]
        epochs[12] = 0
        history, events = bull_permission_history(bars, snapshots, epochs, set(), 120)
        self.assertIsNotNone(history[11])
        self.assertIsNone(history[12])
        self.assertTrue(any(e.get('reason')=='last_rise_low_close_broken' for e in events))

    def test_strict_ambiguity_revokes_and_does_not_bridge(self):
        bars, snapshots, epochs = transition_fixture()
        epochs[11], snapshots[11] = 11, ()
        history, _ = bull_permission_history(bars, snapshots, epochs, {10}, 120)
        self.assertIsNotNone(history[9])
        self.assertIsNone(history[10])
        self.assertIsNone(history[11])

    def test_new_default_requires_regime(self):
        self.assertEqual(SystemStrategy().entry_policy, 'transitioned_squeeze')
        with self.assertRaises(ValueError):
            SystemStrategy(regime_filter=False).validate()

    def test_n_without_prior_transition_is_rejected(self):
        result = generate_system_signals(fixture(), config(entry_policy='transitioned_squeeze'))
        self.assertFalse(any(s.side=='LONG' for s in result.signals))
        self.assertTrue(any(e.get('reason')=='bullish_transition_not_ready' for e in result.audit))

    def test_integrated_permission_admission_and_old_n_rejection(self):
        # Gate wiring test; actual transition mathematics is tested above.
        for ready, expected in [(6,True), (7,False), (8,False)]:
            permission = BullPermission(0,0,10,1,2,ready)
            with patch('wavequant.integrated_strategy.bull_permission_history',
                       return_value=({i:permission for i in range(13)}, [])):
                result = generate_system_signals(fixture(), config(entry_policy='transitioned_squeeze'))
            self.assertEqual(any(s.side=='LONG' for s in result.signals), expected)
            if expected:
                self.assertTrue(all(s.regime in ('轧空','强轧空') for s in result.signals if s.side=='LONG'))
                self.assertTrue(any(e['event']=='long_transition_evidence' for e in result.audit))

    def test_grind_is_rejected_even_with_bullish_permission(self):
        permission = BullPermission(0,0,10,1,2,6)
        def grind(*args, **kw):
            result = observe_market_regime(*args, **kw)
            return replace(result, frames=tuple(replace(f, regime=MarketRegime.GRIND_UP)
                if f.regime is not None else f for f in result.frames))
        with patch('wavequant.integrated_strategy.bull_permission_history',
                   return_value=({i:permission for i in range(13)}, [])), \
             patch('wavequant.integrated_strategy.observe_market_regime', side_effect=grind):
            result = generate_system_signals(fixture(), config(entry_policy='transitioned_squeeze'))
        self.assertFalse(any(s.side=='LONG' for s in result.signals))
        self.assertTrue(any(e.get('reason')=='not_squeeze_regime' for e in result.audit))

    def test_attack_day_must_itself_be_bullish(self):
        permission = BullPermission(0,0,10,1,2,6)
        history = {i:permission for i in range(13)}
        history[7] = None
        with patch('wavequant.integrated_strategy.bull_permission_history', return_value=(history, [])):
            result = generate_system_signals(fixture(), config(entry_policy='transitioned_squeeze'))
        self.assertFalse(any(s.side=='LONG' for s in result.signals))
        self.assertTrue(any(e.get('reason')=='attack_not_in_same_bullish_episode' for e in result.audit))

    def test_new_policy_random_prefix_invariance(self):
        import random
        rng, bars, previous = random.Random(712), [], 50
        for i in range(180):
            close = max(5, previous+rng.uniform(-4,4))
            bars.append(Bar(datetime(2020,1,1)+timedelta(days=i), 'TEST', previous,
                max(previous,close)+rng.uniform(.1,2), min(previous,close)-rng.uniform(.1,2),close,1000000))
            previous = close
        for mode in ('strict_polyline','confirmed_fractal_proxy'):
            c = config(entry_policy='transitioned_squeeze', pivot_mode=mode, volume_filter=False)
            full = generate_system_signals(bars,c)
            for end in (30,60,90,120,150):
                short = generate_system_signals(bars[:end+1],c)
                self.assertEqual(short.signals,[s for s in full.signals if s.bar_index<=end])


if __name__ == '__main__':
    unittest.main()
