from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta
import unittest

from wavequant.model import Bar
from wavequant.polyline import LinePoint, PointKind as K, ReversalPoint
from wavequant.price_action import AttackBasis, Direction
from wavequant.trend_structure import (StructuralTrend as T, TransitionStage as S,
    observe_structure, observe_trend_transition, preceding_turn,
    retracement_evidence, observe_abc)


def point(index, kind, price, confirmed=None):
    return ReversalPoint(LinePoint(index, 0, kind, price),
                         index+1 if confirmed is None else confirmed, 'fixture_confirmed')


def fixture():
    points = [point(i*2, k, p) for i, (k, p) in enumerate([
        (K.HIGH, 30), (K.LOW, 20), (K.HIGH, 25), (K.LOW, 10),
        (K.HIGH, 18), (K.LOW, 13), (K.HIGH, 28), (K.LOW, 22)])]
    rows = [(24, 30, 23, 25), (24, 26, 22, 23), (22, 23, 20, 21),
            (22, 24, 21, 23), (23, 25, 22, 24), (22, 23, 15, 16),
            (13, 15, 10, 12), (13, 16, 12, 15), (16, 18, 15, 17),
            (16, 17, 14, 15), (15, 16, 13, 14), (16, 24, 15, 23),
            (24, 28, 23, 27), (26, 27, 23, 24), (24, 25, 22, 23),
            (24, 27, 23, 26)]
    bars = [Bar(datetime(2026, 1, 1)+timedelta(days=i), 'TEST', *row, 1000)
            for i, row in enumerate(rows)]
    return bars, points


def context(points=None, end=7, start=0):
    return observe_structure(fixture()[1] if points is None else points,
                             symbol='TEST', timeframe='1d', window_start=start, asof_index=end)


def run(bars=None, points=None, ctx=None, **kw):
    data, pivots = fixture()
    return observe_trend_transition(data if bars is None else bars, pivots if points is None else points,
        context=context() if ctx is None else ctx,
        attack_basis=kw.pop('attack_basis', AttackBasis.CLOSE), **kw)


class StructureTests(unittest.TestCase):
    def test_figure_008_all_four_named_key_mappings(self):
        labels = ['L1', 'H1', 'L0', 'H2', 'L2', 'H3', 'L3', 'H4', 'L4',
                  'H0', 'L5', 'H5', 'L6', 'H6', 'L7', 'H7', 'L8']
        prices = [20, 30, 10, 20, 14, 26, 18, 35, 28, 45, 36, 42, 30, 36, 24, 31, 26]
        pts = [point(i, K.LOW if label.startswith('L') else K.HIGH, price)
               for i, (label, price) in enumerate(zip(labels, prices))]
        r = context(pts, end=17)
        self.assertEqual(r.last_fall_high.source_index, labels.index('H1'))
        self.assertEqual(r.last_rise_low.source_index, labels.index('L4'))
        for target, prior in [('L0', 'H1'), ('L7', 'H6'), ('H0', 'L4'), ('H4', 'L3')]:
            self.assertEqual(preceding_turn(r.points, pts[labels.index(target)]), pts[labels.index(prior)])

    def test_trend_requires_both_highs_and_lows(self):
        self.assertEqual(context().trend, T.BEAR)
        self.assertEqual(context(end=15).trend, T.BULL)
        self.assertEqual(context().window_trend, T.BEAR)
        self.assertEqual(context(end=15).window_trend, T.MIXED)
        self.assertEqual(context(end=5).trend, T.UNKNOWN)
        self.assertEqual(context(end=11).trend, T.MIXED)

    def test_equal_extrema_are_not_a_strict_trend(self):
        pts = [point(0, K.HIGH, 30), point(2, K.LOW, 10),
               point(4, K.HIGH, 30), point(6, K.LOW, 12)]
        self.assertEqual(context(pts).trend, T.MIXED)

    def test_last_fall_high_not_latest_high(self):
        r = context(end=15)
        self.assertEqual(r.window_low.point.price, 10)
        self.assertEqual(r.last_fall_high.price, 25)
        self.assertNotEqual(r.last_fall_high.price, 28)
        self.assertEqual(r.last_fall_high.source_index, 4)

    def test_last_rise_low_selected_for_window_high(self):
        pts = [point(i*2, k, p) for i, (k, p) in enumerate([
            (K.LOW, 10), (K.HIGH, 20), (K.LOW, 13), (K.HIGH, 30),
            (K.LOW, 22), (K.HIGH, 27), (K.LOW, 18)])]
        r = context(pts, end=13)
        self.assertEqual(r.last_rise_low.price, 13)
        self.assertEqual(preceding_turn(r.points, r.points[-2]).point.price, 22)

    def test_window_scope_and_missing_left_context(self):
        r = context(end=15, start=6)
        self.assertIsNone(r.last_fall_high)
        self.assertEqual(r.window_low.point.price, 10)
        self.assertEqual(r.window_high.point.price, 28)
        self.assertEqual(r.last_rise_low.price, 13)

    def test_extreme_tie_keeps_first_anchor(self):
        pts = [point(0, K.HIGH, 30), point(2, K.LOW, 10),
               point(4, K.HIGH, 25), point(6, K.LOW, 10)]
        self.assertEqual(context(pts).last_fall_high.price, 30)

    def test_confirmation_time_not_drawing_time(self):
        pts = fixture()[1]
        self.assertNotIn(pts[3], context(end=6).points)
        self.assertIn(pts[3], context(end=7).points)
        self.assertEqual(context().last_fall_high.confirmed_index, 7)

    def test_structure_prefix_invariance(self):
        pts = fixture()[1]
        for end in range(16):
            visible = [p for p in pts if p.confirmed_index <= end]
            self.assertEqual(context(pts, end=end), context(visible, end=end))

    def test_invalid_order_alternation_and_geometry(self):
        for pts in ([point(2, K.HIGH, 20), point(0, K.LOW, 10)],
                    [point(0, K.HIGH, 20), point(2, K.HIGH, 21)],
                    [point(0, K.LOW, 20), point(2, K.HIGH, 10)]):
            with self.assertRaises(ValueError):
                context(pts)

    def test_literal_67_and_33_not_exact_fractions(self):
        a = retracement_evidence(100, 200, 133, direction=Direction.UP)
        self.assertFalse(a.below_67_percent)
        self.assertTrue(retracement_evidence(100, 200, 133.1, direction=Direction.UP).below_67_percent)
        self.assertFalse(retracement_evidence(100, 200, 167, direction=Direction.UP).below_33_percent)
        self.assertTrue(retracement_evidence(100, 200, 167.1, direction=Direction.UP).below_33_percent)
        self.assertFalse(retracement_evidence(100, 200, 200, direction=Direction.UP).partial)

    def test_retracement_mirror_and_validation(self):
        self.assertEqual(retracement_evidence(10, 20, 16, direction=Direction.UP),
                         retracement_evidence(30, 20, 24, direction=Direction.DOWN))
        for values in ((10, 10, 9), (10, 20, 21), (0, 20, 15)):
            with self.assertRaises(ValueError):
                retracement_evidence(*values, direction=Direction.UP)

    def test_abc_separate_confirmed_three_legs(self):
        pts = [point(0, K.HIGH, 30), point(2, K.LOW, 20),
               point(4, K.HIGH, 25), point(6, K.LOW, 15)]
        self.assertIsNone(observe_abc(pts, direction=Direction.DOWN, asof_index=6))
        r = observe_abc(pts, direction=Direction.DOWN, asof_index=7)
        self.assertEqual((r.first_leg, r.third_leg), (10, 10))
        self.assertTrue(r.equal_wave_or_more)
        shorter = pts[:-1]+[point(6, K.LOW, 16)]
        self.assertFalse(observe_abc(shorter, direction=Direction.DOWN, asof_index=7).equal_wave_or_more)

    def test_abc_mirror_and_invalid_geometry(self):
        pts = [point(0, K.LOW, 10), point(2, K.HIGH, 20),
               point(4, K.LOW, 15), point(6, K.HIGH, 25)]
        self.assertTrue(observe_abc(pts, direction=Direction.UP, asof_index=7).equal_wave_or_more)
        pts[2] = point(4, K.LOW, 9)
        self.assertIsNone(observe_abc(pts, direction=Direction.UP, asof_index=7))


class TransitionTests(unittest.TestCase):
    def test_suspicion_break_and_alternation_are_separate(self):
        self.assertEqual(run(asof_index=10).stage, S.WATCHING)
        self.assertEqual(run(asof_index=11).stage, S.SUSPICION)
        self.assertEqual(run(asof_index=12).stage, S.KEY_BROKEN)
        self.assertEqual(run(asof_index=14).stage, S.KEY_BROKEN)
        r = run()
        self.assertEqual(r.stage, S.ALTERNATION)
        self.assertEqual(r.suspicion_index, 11)
        self.assertEqual(r.attack.bar_index, 12)
        self.assertEqual(r.alternation_confirmed_index, 15)
        self.assertAlmostEqual(r.retracement.ratio, .4)
        self.assertEqual(r.break_name, '翻空为多')
        self.assertEqual(r.formation_name, '底部成形')
        self.assertEqual(r.alternation_name, '空多交替')

    def test_head_top_bull_to_bear_exact_mirror(self):
        bars, pts = fixture()
        bars = [replace(b, open=50-b.open, high=50-b.low, low=50-b.high, close=50-b.close) for b in bars]
        pts = [point(p.point.index, K.LOW if p.point.kind == K.HIGH else K.HIGH,
                     50-p.point.price, p.confirmed_index) for p in pts]
        r = run(bars, pts, context(pts))
        self.assertEqual(r.direction, Direction.DOWN)
        self.assertEqual(r.stage, S.ALTERNATION)
        self.assertEqual(r.suspicion_name, '头部疑虑')
        self.assertEqual(r.formation_name, '头部成形')
        self.assertEqual(r.alternation_name, '多空交替')
        self.assertAlmostEqual(r.retracement.ratio, .4)

    def test_key_is_frozen_not_updated_to_intermediate_high(self):
        r = run(asof_index=11)
        self.assertEqual(r.key.price, 25)
        self.assertIsNone(r.attack)
        self.assertEqual(run().key, context().last_fall_high)

    def test_intrabar_vs_close_and_equality(self):
        bars, pts = fixture()
        bars[12] = replace(bars[12], close=25)
        self.assertIsNone(run(bars, pts, asof_index=12).attack)
        self.assertEqual(run(bars, pts, asof_index=12, attack_basis=AttackBasis.INTRABAR).attack.bar_index, 12)
        bars[12] = replace(bars[12], high=25)
        self.assertIsNone(run(bars, pts, asof_index=12, attack_basis=AttackBasis.INTRABAR).attack)

    def test_unconfirmed_counter_is_not_alternation(self):
        r = run(asof_index=14)
        self.assertIsNotNone(r.impulse_extreme)
        self.assertIsNone(r.counterturn)
        self.assertIsNone(r.retracement)

    def test_deep_countermove_not_alternation(self):
        bars, pts = fixture()
        bars[14] = replace(bars[14], low=17)
        pts[-1] = point(14, K.LOW, 17)
        r = run(bars, pts)
        self.assertGreater(r.retracement.ratio, .67)
        self.assertEqual(r.stage, S.DEEP_COUNTERMOVE)
        self.assertIsNone(r.alternation_confirmed_index)

    def test_invalidated_original_extreme_absorbing(self):
        bars, pts = fixture()
        bars[11] = replace(bars[11], low=9)
        r = run(bars, pts)
        self.assertEqual(r.stage, S.INVALIDATED)
        self.assertEqual(r.invalidated_index, 11)
        self.assertIsNone(r.attack)
        self.assertIsNone(r.alternation_confirmed_index)

    def test_same_bar_anchor_break_and_key_break_is_invalidated(self):
        bars, pts = fixture()
        bars[12] = replace(bars[12], low=9)
        r = run(bars, pts)
        self.assertEqual(r.stage, S.INVALIDATED)
        self.assertIsNone(r.attack)

    def test_later_failure_preserves_earlier_confirmation(self):
        bars, pts = fixture()
        bars.append(replace(bars[-1], timestamp=bars[-1].timestamp+timedelta(days=1), low=9))
        r = run(bars, pts)
        self.assertEqual(r.stage, S.INVALIDATED)
        self.assertEqual(r.alternation_confirmed_index, 15)
        self.assertEqual(r.attack.bar_index, 12)

    def test_failure_before_counter_confirmation_does_not_backdate(self):
        bars, pts = fixture()
        bars[15] = replace(bars[15], low=9)
        r = run(bars, pts)
        self.assertEqual(r.stage, S.INVALIDATED)
        self.assertIsNone(r.alternation_confirmed_index)
        self.assertIsNone(r.counterturn)

    def test_prefix_invariance_and_future_prices_not_validated(self):
        bars, pts = fixture()
        for end in range(7, len(bars)):
            visible = [p for p in pts if p.confirmed_index <= end]
            self.assertEqual(run(bars, pts, asof_index=end), run(bars[:end+1], visible))
        bars[-1] = replace(bars[-1], close=float('nan'))
        self.assertEqual(run(bars, pts, asof_index=11), run(asof_index=11))

    def test_rejects_modified_frozen_context_and_mismatched_prices(self):
        bars, pts = fixture()
        pts[1] = point(2, K.LOW, 19)
        with self.assertRaises(ValueError):
            run(bars, pts)
        bars, pts = fixture()
        pts[-1] = point(14, K.LOW, 21)
        with self.assertRaises(ValueError):
            run(bars, pts)

    def test_unknown_mixed_context_not_assumed_trend_and_immutable(self):
        with self.assertRaises(ValueError):
            run(ctx=context(end=5))
        with self.assertRaises(ValueError):
            run(ctx=context(end=11))
        with self.assertRaises(FrozenInstanceError):
            run().key.price = 10


if __name__ == '__main__':
    unittest.main()
