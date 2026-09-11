from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta
from fractions import Fraction
import unittest

from wavequant.model import Bar
from wavequant.polyline import LinePoint, PointKind as K, ReversalPoint
from wavequant.price_action import Direction as D, AttackBasis as B, KeyLevel, LevelKind
from wavequant.market_regime import MarketRegime, RegimePolicy, WaveBoundary, observe_market_regime
from wavequant.n_shape import NSetup, PivotRef, BoxAnchorMode
from wavequant.wave_strength import (StrengthScale as Scale, CounterStrength as C,
    measure_strength, observe_wave_strength)
from wavequant.market_turn import (MinorLine, RegimeContext, TurnSetup, TurnPolicy,
    TurnThreshold as Threshold, TurnStage as S, freeze_minor_line, line_break_on_bar,
    observe_market_turn, context_from_regime)


def point(i, kind, price, known=None):
    return ReversalPoint(LinePoint(i, 0, kind, price), i+1 if known is None else known, 'fixed_test_pivot')


def fixture():
    rows = [(36, 40, 35, 38), (26, 27, 20, 22), (15, 20, 10, 12),
            (14, 23, 13, 22), (23, 27, 20, 26), (27, 30, 25, 29),
            (30, 32, 28, 31), (29, 30, 25, 26), (25, 27, 24, 25),
            (25, 28, 24.5, 27), (28, 30, 26, 29), (28, 29, 25, 28), (28, 31, 27, 30)]
    bars = [Bar(datetime(2026, 1, 1)+timedelta(days=i), 'TEST', *r, 1000) for i, r in enumerate(rows)]
    pts = (point(0, K.HIGH, 40), point(2, K.LOW, 10), point(6, K.HIGH, 32), point(8, K.LOW, 24))
    ctx = RegimeContext('TEST', '1d', MarketRegime.BEAR, 3, 'frozen_regime')
    key = KeyLevel('TEST', '1d', LevelKind.RESISTANCE, 40, 0, 3, 'last_fall_high')
    setup = TurnSetup('TEST', '1d', D.UP, *pts, ctx, key)
    line = freeze_minor_line((pts[2], pts[3], point(10, K.HIGH, 30)), symbol='TEST',
        timeframe='1d', direction=D.UP, selected_at_index=11, source='minor_pivots_same_bar_axis')
    return bars, setup, line


def policy(**kw):
    return replace(TurnPolicy(Scale.EXACT_FRACTIONS, Threshold.TWO_THIRDS,
                             Threshold.TWO_THIRDS, B.CLOSE, B.CLOSE), **kw)


def run(bars=None, setup=None, line='default', **kw):
    data, s, l = fixture()
    return observe_market_turn(data if bars is None else bars, s if setup is None else setup,
        policy=kw.pop('policy', policy()), minor_line=l if line == 'default' else line, **kw)


def mirrored():
    bars, s, line = fixture()
    bars = [replace(b, open=50-b.open, high=50-b.low, low=50-b.high, close=50-b.close) for b in bars]
    def p(x):
        return point(x.point.index, K.LOW if x.point.kind == K.HIGH else K.HIGH,
                     50-x.point.price, x.confirmed_index)
    s = TurnSetup('TEST', '1d', D.DOWN, *(p(x) for x in (s.origin, s.impulse_end, s.first_counter, s.second_counter)),
        RegimeContext('TEST', '1d', MarketRegime.BULL, 3, 'mirror'),
        KeyLevel('TEST', '1d', LevelKind.SUPPORT, 10, 0, 3, 'last_rise_low'))
    line = MinorLine('TEST', '1d', D.DOWN, p(line.first), p(line.second), 11, 'mirror_minor')
    return bars, s, line


class StrengthTests(unittest.TestCase):
    def strength(self, move, direction=D.UP, scale=Scale.EXACT_FRACTIONS):
        return measure_strength(100 if direction == D.UP else 400, 400 if direction == D.UP else 100,
            400-move if direction == D.UP else 100+move, impulse_direction=direction, scale=scale)

    def test_rebound_literal_intervals_and_gaps(self):
        for move, grade in [(30, C.UNSPECIFIED), (100, C.BOUNDARY), (120, C.WEAK),
                            (150, C.BOUNDARY), (180, C.MEDIUM), (200, C.BOUNDARY), (210, C.STRONG)]:
            self.assertEqual(self.strength(move, D.DOWN).counter_strength, grade)

    def test_pullback_intervals_and_half_engineering_choice(self):
        for move, grade in [(30, C.WEAK), (100, C.BOUNDARY), (120, C.UNSPECIFIED),
                            (150, C.MEDIUM), (180, C.MEDIUM), (200, C.STRONG), (210, C.STRONG)]:
            self.assertEqual(self.strength(move).counter_strength, grade)

    def test_counter_strength_not_original_trend_strength(self):
        r = self.strength(210)
        self.assertEqual(r.counter_strength, C.STRONG)
        self.assertEqual(r.original_trend_reading, 'original_direction_under_strong_counter_pressure')
        self.assertEqual(self.strength(30).original_trend_reading, 'original_direction_relatively_resilient')

    def test_graph_height_is_complement_for_pullback(self):
        r = self.strength(100)
        self.assertAlmostEqual(r.ratio, 1/3)
        self.assertAlmostEqual(r.height_from_wave_low, 2/3)
        self.assertEqual(r.levels[1].price, 300)
        self.assertEqual(self.strength(100, D.DOWN).levels[1].price, 200)

    def test_exact_sixths_boundary_without_float_fuzz(self):
        for i in range(1, 6):
            r = self.strength(50*i)
            self.assertEqual(r.sixth_boundary, str(Fraction(i, 6)))
            self.assertIsNone(r.sixth_band)
        self.assertEqual(self.strength(49).sixth_band, 1)
        self.assertEqual(self.strength(51).sixth_band, 2)
        self.assertEqual(self.strength(251).sixth_band, 6)

    def test_printed_percentages_not_exact_fractions(self):
        r = self.strength(200, D.DOWN, Scale.PRINTED_PERCENTAGES)
        self.assertEqual(r.counter_strength, C.MEDIUM)
        self.assertIsNone(r.sixth_boundary)
        r = measure_strength(400, 100, 300.1, impulse_direction=D.DOWN, scale=Scale.PRINTED_PERCENTAGES)
        self.assertEqual(r.sixth_boundary, '667/1000')

    def test_zero_full_and_beyond_origin_not_clamped(self):
        self.assertEqual(self.strength(0).counter_strength, C.NONE)
        self.assertEqual(self.strength(300).extent, 'full_retracement')
        r = self.strength(330, D.DOWN)
        self.assertEqual(r.extent, 'beyond_origin')
        self.assertEqual(r.ratio, 1.1)

    def test_price_validation_and_direction(self):
        for args in [(10, 10, 9), (10, 20, 21), (0, 20, 15)]:
            with self.assertRaises(ValueError):
                measure_strength(*args, impulse_direction=D.UP, scale=Scale.EXACT_FRACTIONS)

    def test_confirmed_pivots_only_and_leg_validation(self):
        bars, s, _ = fixture()
        points = (s.origin, s.impulse_end, s.first_counter)
        self.assertIsNone(observe_wave_strength(bars, points, symbol='TEST', scale=Scale.EXACT_FRACTIONS, asof_index=6))
        self.assertEqual(observe_wave_strength(bars, points, symbol='TEST', scale=Scale.EXACT_FRACTIONS, asof_index=7).exact_ratio, '11/15')
        bars[4] = replace(bars[4], high=35)
        with self.assertRaises(ValueError):
            observe_wave_strength(bars, points, symbol='TEST', scale=Scale.EXACT_FRACTIONS, asof_index=7)


class LineTests(unittest.TestCase):
    def test_last_two_known_extrema_selected(self):
        _, _, line = fixture()
        self.assertEqual((line.first.point.index, line.second.point.index), (6, 10))
        self.assertEqual(line.exact_value(12), 29)
        self.assertEqual(line.exact_value(11), Fraction(59, 2))

    def test_line_not_known_on_source_date(self):
        bars, _, line = fixture()
        self.assertIsNone(line_break_on_bar(bars, line, 10, basis=B.CLOSE))
        self.assertIsNone(line_break_on_bar(bars, line, 11, basis=B.CLOSE))
        self.assertEqual(line_break_on_bar(bars, line, 12, basis=B.CLOSE).bar_index, 12)

    def test_strict_equality_and_intrabar_vs_close(self):
        bars, _, line = fixture()
        bars[12] = replace(bars[12], close=29)
        self.assertIsNone(line_break_on_bar(bars, line, 12, basis=B.CLOSE))
        self.assertIsNotNone(line_break_on_bar(bars, line, 12, basis=B.INTRABAR))

    def test_previous_price_uses_previous_line(self):
        bars, _, line = fixture()
        bars[11] = replace(bars[11], high=29.4, close=29.4)
        self.assertIsNotNone(line_break_on_bar(bars, line, 12, basis=B.CLOSE))

    def test_occupancy_is_not_replayed(self):
        bars, _, line = fixture()
        bars[11] = replace(bars[11], high=31, close=30)
        self.assertIsNone(line_break_on_bar(bars, line, 12, basis=B.CLOSE))

    def test_missing_wrong_slope_and_same_bar_rejected(self):
        _, _, line = fixture()
        self.assertIsNone(freeze_minor_line((line.first,), symbol='TEST', timeframe='1d', direction=D.UP,
                                          selected_at_index=11, source='test'))
        with self.assertRaises(ValueError):
            replace(line, second=point(10, K.HIGH, 34))
        with self.assertRaises(ValueError):
            replace(line, second=point(6, K.HIGH, 30))


class TurnTests(unittest.TestCase):
    def test_three_stages_and_fresh_line(self):
        self.assertEqual(run(asof_index=6).stage, S.AWAIT_FIRST)
        self.assertEqual(run(asof_index=7).stage, S.SUSPICION)
        self.assertEqual(run(asof_index=9).stage, S.WAIT_LINE)
        self.assertEqual(run(asof_index=11).stage, S.WAIT_LINE)
        r = run()
        self.assertEqual(r.stage, S.CONFIRMED)
        self.assertEqual((r.suspicion_index, r.combination_index, r.line_break.bar_index), (7, 9, 12))

    def test_negative_mirror(self):
        bars, s, line = mirrored()
        r = run(bars, s, line)
        self.assertEqual(r.stage, S.CONFIRMED)
        self.assertEqual(r.direction, D.DOWN)
        self.assertEqual(r.line_break.line_price, 21)
        self.assertFalse(r.opens_short_position)

    def test_missing_line_is_not_true(self):
        r = run(line=None)
        self.assertEqual(r.stage, S.WAIT_LINE)
        self.assertIsNone(r.line_break)

    def test_regime_background_and_timing_validation(self):
        _, s, _ = fixture()
        with self.assertRaises(ValueError):
            replace(s, background=replace(s.background, regime=MarketRegime.BULL))
        with self.assertRaises(ValueError):
            replace(s, background=replace(s.background, asof_index=8))
        with self.assertRaises(ValueError):
            replace(s, trend_key=replace(s.trend_key, confirmed_index=8))

    def test_positive_major_key_break_preempts_early_turn(self):
        bars, s, _ = fixture()
        s = replace(s, trend_key=replace(s.trend_key, price=27, source_index=1))
        r = run(bars, s)
        self.assertEqual(r.stage, S.MAJOR_FLIP)
        self.assertIsNone(r.line_break)

    def test_negative_key_break_or_branch_without_strong_ratio(self):
        bars, s, _ = mirrored()
        bars[4] = replace(bars[4], open=27, high=30, low=26, close=27)
        bars[5] = replace(bars[5], open=26, high=28, low=24, close=25)
        bars[6] = replace(bars[6], open=24, high=25, low=22, close=23)
        # Ensure the first counter low and next rebound high remain true extrema.
        bars[7] = replace(bars[7], open=24, high=25, low=23, close=24)
        s = replace(s, first_counter=point(6, K.LOW, 22),
                    trend_key=replace(s.trend_key, price=23, source_index=1))
        r = run(bars, s, line=None, policy=policy(key_basis=B.INTRABAR), asof_index=9)
        self.assertLess(r.first_strength.ratio, 2/3)
        self.assertEqual(r.suspicion_reason, 'last_rise_low_broken')
        self.assertEqual(r.stage, S.WAIT_LINE)

    def test_counter_extension_overrides_same_bar_crossing(self):
        bars, _, _ = fixture()
        bars[12] = replace(bars[12], low=23)
        r = run(bars)
        self.assertEqual(r.stage, S.COUNTER_EXTENDED)
        self.assertIsNone(r.line_break)

    def test_later_failure_preserves_historical_confirmation(self):
        bars, _, _ = fixture()
        bars.append(replace(bars[-1], timestamp=bars[-1].timestamp+timedelta(days=1), low=23))
        r = run(bars)
        self.assertEqual(r.stage, S.COUNTER_EXTENDED)
        self.assertEqual(r.line_break.bar_index, 12)

    def test_second_threshold_equality_not_silently_non_strong(self):
        bars, s, _ = fixture()
        # First counter 22 / 30; second counter 14.74 / 22 = 67% exactly.
        bars[8] = replace(bars[8], low=17.26)
        s = replace(s, second_counter=point(8, K.LOW, 17.26))
        r = run(bars, s, line=None, policy=policy(second_threshold=Threshold.PERCENT_67), asof_index=9)
        self.assertEqual(r.stage, S.BOUNDARY)

    def test_no_suspicion_if_first_below_threshold(self):
        bars, s, _ = fixture()
        bars[5] = replace(bars[5], high=29, close=28)
        bars[6] = replace(bars[6], open=28, high=29, low=27, close=28)
        bars[7] = replace(bars[7], high=29)
        s = replace(s, first_counter=point(6, K.HIGH, 29))
        self.assertEqual(run(bars, s, line=None, asof_index=9).stage, S.NO_SUSPICION)

    def test_two_thirds_and_67_are_materially_different_policies(self):
        bars, s, _ = fixture()
        bars[0] = replace(bars[0], high=42.9)
        s = replace(s, origin=point(0, K.HIGH, 42.9), trend_key=replace(s.trend_key, price=42.9))
        self.assertEqual(run(bars, s, asof_index=7).stage, S.SUSPICION)
        r = run(bars, s, asof_index=7, policy=policy(first_threshold=Threshold.PERCENT_67))
        self.assertEqual(r.stage, S.NO_SUSPICION)

    def test_already_beyond_key_at_context_requires_earlier_context(self):
        _, s, _ = fixture()
        s = replace(s, trend_key=replace(s.trend_key, price=20))
        with self.assertRaises(ValueError):
            run(setup=s)

    def test_combination_known_on_crossing_bar_cannot_backdate(self):
        bars, s, line = fixture()
        s = replace(s, second_counter=replace(s.second_counter, confirmed_index=12))
        r = run(bars, s, line)
        self.assertEqual(r.combination_index, 12)
        self.assertEqual(r.stage, S.WAIT_LINE)
        self.assertIsNone(r.line_break)

    def test_strong_second_counter_rejects_combination(self):
        bars, s, _ = fixture()
        bars[8] = replace(bars[8], low=14)
        s = replace(s, second_counter=point(8, K.LOW, 14))
        r = run(bars, s, line=None, asof_index=9)
        self.assertEqual(r.stage, S.SECOND_TOO_STRONG)
        self.assertIsNone(r.combination_index)

    def test_bridge_from_actual_six_regime_observation(self):
        rows = [(8.5, 9, 8, 8.5), (10, 12, 9.5, 11), (10.7, 11, 10, 10.5),
                (10.8, 12.6, 10.4, 12.2), (12.2, 13.5, 12, 13.5), (13.5, 14.5, 13, 14.5)]
        bars = [Bar(datetime(2026, 1, 1)+timedelta(days=i), 'TEST', *r, 1000) for i, r in enumerate(rows)]
        setup = NSetup('TEST', '1d', D.UP, PivotRef(0, 0), PivotRef(1, 1), PivotRef(2, 2),
                       'known_pivots', BoxAnchorMode.ATTACK_VIRTUAL_EXTREME)
        def observe(data):
            return observe_market_regime(data, setup, timeframe='1d', policy=RegimePolicy(None, WaveBoundary.ORIGIN))
        c = context_from_regime(observe(bars), symbol='TEST', timeframe='1d')
        self.assertEqual(c.regime, MarketRegime.STRONG_BULL)
        self.assertEqual(c.asof_index, 5)
        with self.assertRaises(ValueError):
            context_from_regime(observe(bars[:4]), symbol='TEST', timeframe='1d')
        bars.append(replace(bars[-1], timestamp=bars[-1].timestamp+timedelta(days=1), low=7))
        with self.assertRaises(ValueError):
            context_from_regime(observe(bars), symbol='TEST', timeframe='1d')

    def test_full_prefix_invariance_both_directions(self):
        for bars, s, line in (fixture(), mirrored()):
            for end in range(len(bars)):
                self.assertEqual(run(bars, s, line, asof_index=end), run(bars[:end+1], s, line))

    def test_future_invalid_data_ignored_and_results_immutable(self):
        bars, _, _ = fixture()
        bars[12] = replace(bars[12], close=float('nan'))
        self.assertEqual(run(bars, asof_index=7), run(asof_index=7))
        with self.assertRaises(FrozenInstanceError):
            run().stage = S.NO_SUSPICION


if __name__ == '__main__':
    unittest.main()
