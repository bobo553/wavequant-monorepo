from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta
import unittest

from wavequant.model import Bar
from wavequant.price_action import Direction
from wavequant.n_shape import BoxAnchorMode, MilestoneBasis, NStatus, observe_n
from wavequant.market_regime import RegimePolicy, WaveBoundary, observe_market_regime
from wavequant.polyline import (BarPathEvidence, LinePoint, PointKind as K,
    child_mother_path, n_setup_from_polyline, observe_bar_relations, observe_polyline)


def bars(rows):
    return [Bar(datetime(2026, 1, 1)+timedelta(days=i), 'TEST', *row, 1000)
            for i, row in enumerate(rows)]


def fixture():
    return bars([(10, 11, 9, 10), (11, 12, 10, 11), (10, 11, 8, 9),
                 (9, 10, 7, 8), (10, 13, 9, 12), (11, 12, 10, 11),
                 (12, 14, 11, 13.5), (13.5, 15, 12, 15), (15, 16, 14, 16)])


def run(data=None, **kw):
    return observe_polyline(fixture() if data is None else data, symbol='TEST', timeframe='1d',
        initial_direction=kw.pop('initial_direction', Direction.UP), start_index=0, **kw)


class PolylineTests(unittest.TestCase):
    def test_six_bar_terms(self):
        prev, up, down = bars([(10, 12, 8, 11), (13, 15, 10, 14), (8, 10, 6, 7)])
        a = observe_bar_relations(prev, up)
        self.assertTrue(a.extending_head and a.shrinking_foot and a.sunrise)
        b = observe_bar_relations(up, down)
        self.assertTrue(b.shrinking_head and b.falling_tail and b.sunset)

    def test_sunrise_and_sunset_strict_close(self):
        a, b = bars([(10, 12, 8, 11), (11, 14, 9, 12)])
        self.assertFalse(observe_bar_relations(a, b).sunrise)
        self.assertFalse(observe_bar_relations(b, replace(a, close=9,
                         timestamp=b.timestamp+timedelta(days=1))).sunset)

    def test_equal_high_low_are_not_strict_events(self):
        a, b = bars([(10, 12, 8, 11), (10, 12, 8, 9)])
        r = observe_bar_relations(a, b)
        self.assertTrue(r.equal_high and r.equal_low)
        self.assertFalse(any((r.shrinking_head, r.shrinking_foot, r.extending_head, r.falling_tail)))

    def test_up_leg_extends_then_confirms_negative_turn(self):
        r = run(fixture()[:4])
        self.assertEqual(len(r.reversals), 1)
        self.assertEqual(r.reversals[0].point.index, 1)
        self.assertEqual(r.reversals[0].confirmed_index, 2)
        self.assertEqual(r.reversals[0].reversal, '负反转')
        self.assertEqual(r.frames[-1].candidate.price, 7)
        self.assertEqual(r.frames[-1].direction, Direction.DOWN)

    def test_positive_turn_and_seed_not_confirmed(self):
        r = run(fixture()[:5])
        self.assertEqual(r.reversals[-1].reversal, '正反转')
        self.assertEqual(r.reversals[-1].point.index, 3)
        self.assertEqual(r.reversals[-1].confirmed_index, 4)
        self.assertEqual(run(fixture()[:1]).reversals, ())

    def test_down_mirror(self):
        source = fixture()[:5]
        mirrored = [replace(b, open=40-b.open, high=40-b.low, low=40-b.high, close=40-b.close) for b in source]
        a, b = run(source), run(mirrored, initial_direction=Direction.DOWN)
        self.assertEqual([p.confirmed_index for p in a.reversals], [p.confirmed_index for p in b.reversals])
        for p, q in zip(a.reversals, b.reversals):
            self.assertEqual(p.point.price, 40-q.point.price)
            self.assertNotEqual(p.point.kind, q.point.kind)

    def test_inside_blocks_not_silently_skipped(self):
        r = run()
        self.assertEqual(r.blocked_at, 5)
        self.assertEqual(r.frames[-1].candidate, r.frames[4].candidate)
        self.assertEqual(len(r.reversals), 2)

    def test_outside_blocks_without_path(self):
        r = run(bars([(10, 12, 8, 11), (11, 14, 7, 13), (14, 15, 10, 14)]))
        self.assertEqual(r.blocked_at, 1)
        self.assertEqual(r.reversals, ())

    def test_explicit_outside_order_changes_turns(self):
        data = bars([(10, 12, 8, 11), (11, 14, 7, 13)])
        a = run(data, paths={1: BarPathEvidence(1, (K.HIGH, K.LOW), 'verified_ticks')})
        b = run(data, paths={1: BarPathEvidence(1, (K.LOW, K.HIGH), 'verified_ticks')})
        self.assertEqual([p.point.price for p in a.reversals], [14])
        self.assertEqual([p.point.price for p in b.reversals], [12, 7])
        self.assertEqual(a.frames[-1].direction, Direction.DOWN)
        self.assertEqual(b.frames[-1].direction, Direction.UP)

    def test_ambiguous_inside_resolves_with_explicit_order(self):
        r = run(paths={5: BarPathEvidence(5, (K.HIGH, K.LOW), 'explicit_intraday_extremes')})
        self.assertIsNone(r.blocked_at)
        self.assertEqual([p.point.index for p in r.reversals], [1, 3, 4, 5])
        self.assertEqual(r.reversals[2].source, 'explicit_intraday_extremes')

    def test_four_teaching_cases(self):
        for child_bull in (True, False):
            for mother_bull in (True, False):
                data = bars([(9, 11, 8, 10) if child_bull else (10, 11, 8, 9),
                             (8, 13, 7, 12) if mother_bull else (12, 13, 7, 8)])
                path = child_mother_path(*data, child_index=0)
                expected = [K.HIGH if child_bull else K.LOW] + ([K.LOW, K.HIGH] if mother_bull else [K.HIGH, K.LOW])
                self.assertEqual([v.kind for v in path.vertices], expected)
                self.assertIn('not_observed', path.provenance)

    def test_teaching_doji_undefined_and_mother_child_not_invented(self):
        data = bars([(9, 11, 8, 9), (8, 13, 7, 12)])
        self.assertEqual(child_mother_path(*data, child_index=0).status, 'undefined_doji')
        with self.assertRaises(ValueError):
            child_mother_path(*bars([(8, 13, 7, 12), (9, 11, 8, 10)]), child_index=0)

    def test_zero_length_and_equal_extreme_do_not_create_pivots(self):
        r = run(bars([(10, 10, 10, 10), (10, 10, 10, 10)]))
        self.assertEqual(r.reversals, ())
        self.assertEqual(r.frames[-1].candidate.index, 0)

    def test_full_prefix_invariance(self):
        data = fixture()
        paths = {5: BarPathEvidence(5, (K.HIGH, K.LOW), 'intraday')}
        full = run(data, paths=paths)
        for end in range(len(data)):
            short = run(data[:end+1], paths=paths)
            self.assertEqual(short, run(data, paths=paths, asof_index=end))
            self.assertEqual(short.frames, full.frames[:end+1])
            self.assertEqual(short.reversals, tuple(p for p in full.reversals if p.confirmed_index <= end))

    def test_future_invalid_bar_and_path_not_consumed(self):
        data = fixture()
        data[8] = replace(data[8], close=float('nan'))
        self.assertEqual(run(data, asof_index=3, paths={8: 'invalid'}), run(data[:4]))

    def test_n_bridge_and_regime_integration(self):
        # C is only known at 6; the joint break at 6 cannot be replayed later.
        data = fixture()
        data[6] = replace(data[6], high=13, close=12.5)
        line = run(data, asof_index=6,
                   paths={5: BarPathEvidence(5, (K.HIGH, K.LOW), 'intraday')})
        setup = n_setup_from_polyline(line, box_anchor_mode=BoxAnchorMode.ATTACK_VIRTUAL_EXTREME)
        self.assertEqual(setup.pullback.confirmed_index, 6)
        self.assertEqual(setup.origin.index, 3)
        n = observe_n(data, setup, timeframe='1d', milestone_basis=MilestoneBasis.CLOSE)
        self.assertEqual(n.status, NStatus.COMPLETED)
        self.assertEqual(n.completion.bar_index, 7)
        regime = observe_market_regime(data, setup, timeframe='1d',
            policy=RegimePolicy(None, WaveBoundary.ORIGIN))
        self.assertEqual(regime.attack_index, 7)

    def test_n_bridge_rejects_blocked_insufficient_and_same_bar(self):
        mode = BoxAnchorMode.ATTACK_VIRTUAL_EXTREME
        for line in (run(), run(fixture()[:2])):
            with self.assertRaises(ValueError):
                n_setup_from_polyline(line, box_anchor_mode=mode)
        data = bars([(10, 12, 8, 11), (11, 14, 7, 13), (12, 13, 9, 10)])
        line = run(data, paths={1: BarPathEvidence(1, (K.LOW, K.HIGH), 'ticks'),
                               2: BarPathEvidence(2, (K.HIGH, K.LOW), 'ticks')})
        with self.assertRaises(ValueError):
            n_setup_from_polyline(line, box_anchor_mode=mode)

    def test_input_validation_and_immutability(self):
        with self.assertRaises(ValueError):
            BarPathEvidence(1, (K.HIGH, K.HIGH), 'test')
        with self.assertRaises(ValueError):
            LinePoint(0, 0, 'H', 10)
        with self.assertRaises(ValueError):
            run(initial_direction='up')
        with self.assertRaises(ValueError):
            run(paths={5: BarPathEvidence(6, (K.HIGH, K.LOW), 'wrong')})
        with self.assertRaises(FrozenInstanceError):
            run().frames[0].candidate.price = 5


if __name__ == '__main__':
    unittest.main()
