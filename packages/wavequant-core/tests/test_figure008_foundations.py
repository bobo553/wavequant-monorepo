"""Figure 008's labels with explicit synthetic prices and confirmation times.

The unscaled drawing does not supply numerical ratios or timestamps. This test
keeps its relative geometry, especially H6 < L5 and H7 < L6; it does not claim
that the image itself establishes the synthetic 41% countermovements.
"""

from dataclasses import replace
from datetime import datetime, timedelta
import unittest

from wavequant.domain.market_structure.polyline import LinePoint, PointKind, ReversalPoint
from wavequant.domain.market_structure.price_action import AttackBasis
from wavequant.domain.market_structure.trend_foundations import TrendFoundations, observe_trend_foundations
from wavequant.domain.market_structure.trend_primitives import (
    TrendDefinitionSignals,
    is_bear_trend,
    is_bull_trend,
    last_fall_high,
    last_rise_low,
    negative_reversal,
    observe_trend_definition_signals,
    positive_reversal,
    shallow_countermove,
    weak_countermove,
)
from wavequant.domain.market_structure.trend_structure import (
    StructuralTrend,
    StructureContext,
    TransitionStage,
    TrendTransition,
    observe_structure,
    observe_trend_transition,
)
from wavequant.domain.models.model import Bar


LABELS = ('L1', 'H1', 'L0', 'H2', 'L2', 'H3', 'L3', 'H4', 'L4',
          'H0', 'L5', 'H5', 'L6', 'H6', 'L7', 'H7', 'L8')
PRICES = (20, 30, 10, 20, 14, 26, 18, 35, 28, 45, 38, 42, 32, 36, 24, 29, 26)


def _sample() -> tuple[tuple[Bar, ...], tuple[ReversalPoint, ...]]:
    vertices = tuple((PointKind.HIGH if label.startswith('H') else PointKind.LOW, float(price))
                     for label, price in zip(LABELS, PRICES))
    return _geometry_sample(vertices)


def _geometry_sample(
    vertices: tuple[tuple[PointKind, float], ...],
) -> tuple[tuple[Bar, ...], tuple[ReversalPoint, ...]]:
    bars: list[Bar] = []
    points: list[ReversalPoint] = []
    for index, (kind, price) in enumerate(vertices):
        opening = price - 1 if kind == PointKind.HIGH else price + 1
        high = price if kind == PointKind.HIGH else price + 2
        low = price - 2 if kind == PointKind.HIGH else price
        bars.append(Bar(datetime(2026, 1, 1) + timedelta(days=index), 'FIG008', opening, high, low, opening, 1000))
        points.append(ReversalPoint(LinePoint(index, 0, kind, price), index + 1, 'figure008_test_confirmed'))
    final_close = bars[-1].close
    bars.append(Bar(datetime(2026, 1, 1) + timedelta(days=len(bars)), 'FIG008',
                    final_close, final_close + 2, final_close, final_close + 1, 1000))
    return tuple(bars), tuple(points)


def _context(points: tuple[ReversalPoint, ...], *, start: int, end: int) -> StructureContext:
    return observe_structure(points, symbol='FIG008', timeframe='1d', window_start=start, asof_index=end)


def _transition(
    bars: tuple[Bar, ...],
    points: tuple[ReversalPoint, ...],
    context: StructureContext,
    *,
    end: int,
) -> TrendTransition:
    return observe_trend_transition(bars, points, context=context, attack_basis=AttackBasis.CLOSE, asof_index=end)


def _foundations(
    bars: tuple[Bar, ...],
    points: tuple[ReversalPoint, ...],
    *,
    end: int,
    start: int = 0,
    context: StructureContext | None = None,
) -> TrendFoundations:
    return observe_trend_foundations(
        bars, points, symbol='FIG008', timeframe='1d', window_start=start, asof_index=end,
        transition_context=context, attack_basis=AttackBasis.CLOSE,
    )


def _event_flags(signals: TrendDefinitionSignals) -> tuple[bool, ...]:
    return (
        signals.flip_to_bull, signals.flip_to_bear,
        signals.bear_to_bull_alternation, signals.bull_to_bear_alternation,
        signals.head_suspicion, signals.bottom_suspicion, signals.head_formed, signals.bottom_formed,
    )


def _stretched_sample(
    bars: tuple[Bar, ...], points: tuple[ReversalPoint, ...], mapping: tuple[int, ...],
) -> tuple[tuple[Bar, ...], tuple[ReversalPoint, ...]]:
    stretched: list[Bar] = []
    for source_index, bar in enumerate(bars):
        while len(stretched) < mapping[source_index]:
            previous_close = stretched[-1].close
            stretched.append(Bar(datetime(2026, 1, 1) + timedelta(days=len(stretched)), 'FIG008',
                                 previous_close, previous_close, previous_close, previous_close, 1000))
        stretched.append(replace(bar, timestamp=datetime(2026, 1, 1) + timedelta(days=mapping[source_index])))
    remapped = tuple(ReversalPoint(
        LinePoint(mapping[point.point.index], point.point.ordinal, point.point.kind, point.point.price),
        mapping[point.confirmed_index], 'figure008_stretched_test_confirmed',
    ) for point in points)
    return tuple(stretched), remapped


class Figure008FoundationTests(unittest.TestCase):
    bars: tuple[Bar, ...]
    points: tuple[ReversalPoint, ...]

    def setUp(self) -> None:
        self.bars, self.points = _sample()

    def test_four_keys_use_the_nearest_opposite_reversal_left_of_the_named_extreme(self) -> None:
        context = _context(self.points, start=0, end=17)
        for target_label, key_label in (('L0', 'H1'), ('L7', 'H6'), ('H0', 'L4'), ('H4', 'L3')):
            with self.subTest(target=target_label):
                target = self.points[LABELS.index(target_label)]
                key = (last_fall_high(context, target) if target_label.startswith('L')
                       else last_rise_low(context, target))
                if key is None:
                    self.fail('the labelled predecessor is part of the observed window')
                self.assertEqual(key.source_index, LABELS.index(key_label))
                self.assertEqual(key.price, PRICES[LABELS.index(key_label)])
                self.assertEqual(key.confirmed_index, 17)
        default_high = last_fall_high(context)
        default_low = last_rise_low(context)
        if default_high is None or default_low is None:
            self.fail('global extrema both have a left predecessor')
        self.assertEqual((default_high.source_index, default_low.source_index), (1, 8))

    def test_the_left_background_is_unknown_until_two_confirmed_highs_and_lows_exist(self) -> None:
        at_l0 = _context(self.points, start=0, end=3)
        self.assertEqual(at_l0.window_trend, StructuralTrend.UNKNOWN)
        self.assertIsNone(is_bull_trend(at_l0))
        self.assertIsNone(is_bear_trend(at_l0))
        with self.assertRaises(ValueError):
            _transition(self.bars, self.points, at_l0, end=7)
        at_h2 = _context(self.points, start=0, end=4)
        self.assertTrue(is_bear_trend(at_h2))
        self.assertFalse(is_bull_trend(at_h2))
        initial = _foundations(self.bars, self.points, end=3)
        self.assertIsNone(initial.transition)
        self.assertIsNone(initial.signals)
        self.assertIsNone(initial.n_observation)
        self.assertIsNone(initial.squeeze_low)
        self.assertIsNone(initial.selloff_high)

    def test_l0_to_h0_is_strict_bull_and_h0_to_l7_is_strict_bear_but_l8_breaks_the_window_rule(self) -> None:
        rising = _context(self.points, start=2, end=10)
        falling = _context(self.points, start=9, end=15)
        with_l8 = _context(self.points, start=9, end=17)
        self.assertTrue(is_bull_trend(rising))
        self.assertFalse(is_bear_trend(rising))
        self.assertTrue(is_bear_trend(falling))
        self.assertFalse(is_bull_trend(falling))
        self.assertEqual(with_l8.window_trend, StructuralTrend.MIXED)
        self.assertFalse(is_bull_trend(with_l8))
        self.assertFalse(is_bear_trend(with_l8))

    def test_h4_attack_and_l4_confirmed_alternation_have_distinct_dates(self) -> None:
        context = _context(self.points, start=0, end=4)
        before = observe_trend_definition_signals(_transition(self.bars, self.points, context, end=6))
        attack = _transition(self.bars, self.points, context, end=7)
        unconfirmed_pullback = observe_trend_definition_signals(
            _transition(self.bars, self.points, context, end=8))
        confirmed = _transition(self.bars, self.points, context, end=9)
        self.assertFalse(before.flip_to_bull)
        if attack.attack is None or confirmed.retracement is None:
            self.fail('H4 must break H1 and L4 must supply a confirmed countermove')
        self.assertEqual((attack.key.source_index, attack.attack.bar_index), (1, 7))
        self.assertTrue(observe_trend_definition_signals(attack).flip_to_bull)
        self.assertFalse(observe_trend_definition_signals(attack).bear_to_bull_alternation)
        self.assertFalse(unconfirmed_pullback.bear_to_bull_alternation)
        self.assertEqual(confirmed.alternation_confirmed_index, 9)
        self.assertTrue(observe_trend_definition_signals(confirmed).bear_to_bull_alternation)
        self.assertAlmostEqual(confirmed.retracement.ratio, 7 / 17)
        self.assertTrue(shallow_countermove(confirmed.retracement))
        self.assertFalse(weak_countermove(confirmed.retracement))

    def test_h5_head_suspicion_precedes_l7_break_and_h7_alternation(self) -> None:
        context = _context(self.points, start=2, end=10)
        before = observe_trend_definition_signals(_transition(self.bars, self.points, context, end=11))
        suspicion = _transition(self.bars, self.points, context, end=12)
        attack = _transition(self.bars, self.points, context, end=14)
        unconfirmed_rebound = observe_trend_definition_signals(
            _transition(self.bars, self.points, context, end=15))
        confirmed = _transition(self.bars, self.points, context, end=16)
        self.assertFalse(before.head_suspicion)
        self.assertEqual(suspicion.suspicion_index, 12)
        self.assertTrue(observe_trend_definition_signals(suspicion).head_suspicion)
        self.assertFalse(observe_trend_definition_signals(suspicion).head_formed)
        self.assertIsNone(suspicion.formation_name)
        if attack.attack is None or confirmed.retracement is None:
            self.fail('L7 must break L4 and H7 must supply a confirmed rebound')
        self.assertEqual((attack.key.source_index, attack.attack.bar_index), (8, 14))
        self.assertTrue(observe_trend_definition_signals(attack).flip_to_bear)
        self.assertTrue(observe_trend_definition_signals(attack).head_formed)
        self.assertEqual(attack.formation_name, '头部成形')
        self.assertFalse(unconfirmed_rebound.bull_to_bear_alternation)
        self.assertEqual(confirmed.alternation_confirmed_index, 16)
        self.assertTrue(observe_trend_definition_signals(confirmed).bull_to_bear_alternation)
        self.assertAlmostEqual(confirmed.retracement.ratio, 5 / 12)
        self.assertTrue(shallow_countermove(confirmed.retracement))
        self.assertFalse(weak_countermove(confirmed.retracement))

    def test_l8_is_only_bottom_suspicion_after_its_confirmation_with_h6_still_unbroken(self) -> None:
        context = _context(self.points, start=9, end=15)
        before = observe_trend_definition_signals(_transition(self.bars, self.points, context, end=16))
        transition = _transition(self.bars, self.points, context, end=17)
        signals = observe_trend_definition_signals(transition)
        self.assertFalse(before.bottom_suspicion)
        self.assertEqual(transition.key.source_index, LABELS.index('H6'))
        self.assertEqual(transition.suspicion_index, 17)
        self.assertEqual(transition.stage, TransitionStage.SUSPICION)
        self.assertTrue(signals.bottom_suspicion)
        self.assertFalse(signals.bottom_formed)
        self.assertFalse(signals.flip_to_bull)
        self.assertFalse(signals.bear_to_bull_alternation)
        self.assertIsNone(transition.formation_name)

    def test_reversal_primitives_never_use_a_point_before_its_confirmation(self) -> None:
        for end in range(len(self.bars)):
            for point in self.points:
                with self.subTest(end=end, label=LABELS[point.point.index]):
                    known = point.confirmed_index <= end
                    self.assertEqual(positive_reversal(point, asof_index=end),
                                     known and point.point.kind == PointKind.LOW)
                    self.assertEqual(negative_reversal(point, asof_index=end),
                                     known and point.point.kind == PointKind.HIGH)

    def test_every_structure_and_transition_matches_its_available_prefix(self) -> None:
        for end in range(len(self.bars)):
            visible = tuple(point for point in self.points if point.confirmed_index <= end)
            self.assertEqual(_context(self.points, start=0, end=end), _context(visible, start=0, end=end))
            self.assertEqual(_foundations(self.bars, self.points, end=end),
                             _foundations(self.bars[:end + 1], visible, end=end))
        for start, frozen_end in ((0, 4), (2, 10), (9, 15)):
            context = _context(self.points, start=start, end=frozen_end)
            for end in range(frozen_end, len(self.bars)):
                with self.subTest(start=start, end=end):
                    visible = tuple(point for point in self.points if point.confirmed_index <= end)
                    full = _transition(self.bars, self.points, context, end=end)
                    prefix = _transition(self.bars[:end + 1], visible, context, end=end)
                    self.assertEqual(full, prefix)
                    self.assertEqual(observe_trend_definition_signals(full), observe_trend_definition_signals(prefix))
                    unified = _foundations(self.bars, self.points, start=start, context=context, end=end)
                    prefix_unified = _foundations(self.bars[:end + 1], visible,
                                                  start=start, context=context, end=end)
                    self.assertEqual(unified, prefix_unified)
                    self.assertEqual(unified.structure, _context(self.points, start=start, end=end))
                    self.assertEqual(unified.transition, full)
                    self.assertEqual(unified.signals, observe_trend_definition_signals(full))

    def test_variable_time_spans_move_events_with_their_actual_source_and_confirmation_dates(self) -> None:
        mapping = tuple(2 * index + index // 3 for index in range(len(self.bars)))
        bars, points = _stretched_sample(self.bars, self.points, mapping)
        self.assertGreater(len(bars), len(self.bars))
        for start, frozen_end, final_end in ((0, 4, 9), (2, 10, 16), (9, 15, 17)):
            with self.subTest(start=start):
                original_context = _context(self.points, start=start, end=frozen_end)
                context = _context(points, start=mapping[start], end=mapping[frozen_end])
                original = _foundations(self.bars, self.points, end=final_end, context=original_context)
                stretched = _foundations(bars, points, end=mapping[final_end], context=context)
                if original.transition is None or stretched.transition is None:
                    self.fail('an explicit frozen context must yield transition evidence')
                if original.signals is None or stretched.signals is None:
                    self.fail('the composed observer must project the supplied transition')
                self.assertEqual(_event_flags(original.signals), _event_flags(stretched.signals))
                for old_index, new_index in (
                    (original.transition.suspicion_index, stretched.transition.suspicion_index),
                    (original.transition.alternation_confirmed_index,
                     stretched.transition.alternation_confirmed_index),
                ):
                    self.assertEqual(new_index, None if old_index is None else mapping[old_index])
                if original.transition.attack is not None and stretched.transition.attack is not None:
                    self.assertEqual(stretched.transition.attack.bar_index,
                                     mapping[original.transition.attack.bar_index])
                if original.transition.retracement is not None and stretched.transition.retracement is not None:
                    self.assertAlmostEqual(stretched.transition.retracement.ratio,
                                           original.transition.retracement.ratio)

    def test_positive_price_scaling_and_translation_preserve_structural_and_transition_definitions(self) -> None:
        for scale, offset in ((0.1, 0.0), (2.5, 100.5), (1000.0, 0.0)):
            bars = tuple(replace(bar, open=bar.open * scale + offset, high=bar.high * scale + offset,
                                 low=bar.low * scale + offset, close=bar.close * scale + offset)
                         for bar in self.bars)
            points = tuple(ReversalPoint(
                LinePoint(point.point.index, point.point.ordinal, point.point.kind, point.point.price * scale + offset),
                point.confirmed_index, 'figure008_scaled_test_confirmed',
            ) for point in self.points)
            for start, frozen_end, final_end in ((0, 4, 9), (2, 10, 16), (9, 15, 17)):
                with self.subTest(scale=scale, offset=offset, start=start):
                    original_context = _context(self.points, start=start, end=frozen_end)
                    context = _context(points, start=start, end=frozen_end)
                    original = _foundations(self.bars, self.points, start=start,
                                            end=final_end, context=original_context)
                    transformed = _foundations(bars, points, start=start, end=final_end, context=context)
                    if original.signals is None or transformed.signals is None:
                        self.fail('an explicit context must produce a projected signal snapshot')
                    self.assertEqual(_event_flags(original.signals), _event_flags(transformed.signals))
                    self.assertEqual(transformed.structure.window_trend, original.structure.window_trend)
                    if original.transition is None or transformed.transition is None:
                        self.fail('transition evidence is required for these contexts')
                    self.assertAlmostEqual(transformed.transition.key.price,
                                           original.transition.key.price * scale + offset)
                    if original.transition.retracement is not None and transformed.transition.retracement is not None:
                        self.assertAlmostEqual(transformed.transition.retracement.ratio,
                                               original.transition.retracement.ratio)

    def test_extra_legal_turns_change_the_adjacent_impulse_and_allow_different_window_lengths(self) -> None:
        base = tuple((point.point.kind, point.point.price) for point in self.points)
        expanded = (base[:7] + ((PointKind.HIGH, 29.0), (PointKind.LOW, 21.0)) + base[7:14]
                    + ((PointKind.LOW, 30.0), (PointKind.HIGH, 33.0)) + base[14:])
        bars, points = _geometry_sample(expanded)
        self.assertGreater(len(points), len(self.points))
        origin_low = next(point for point in points if point.point.kind == PointKind.LOW and point.point.price == 10)
        global_high = max(points, key=lambda point: point.point.price)
        final_break_low = next(point for point in points
                               if point.point.kind == PointKind.LOW and point.point.price == 24)
        bull = _foundations(bars, points, start=origin_low.point.index, end=global_high.confirmed_index).structure
        bear = _foundations(bars, points, start=global_high.point.index, end=final_break_low.confirmed_index).structure
        self.assertTrue(is_bull_trend(bull))
        self.assertTrue(is_bear_trend(bear))
        self.assertGreater(len(bull.points), len(_context(self.points, start=2, end=10).points))
        self.assertGreater(len(bear.points), len(_context(self.points, start=9, end=15).points))
        first_bear = _context(points, start=0, end=points[3].confirmed_index)
        for direction_high, extreme_price, context in ((True, 35.0, first_bear), (False, 24.0, bull)):
            with self.subTest(up=direction_high):
                extreme = next(point for point in points if point.point.price == extreme_price)
                position = points.index(extreme)
                origin, counter = points[position - 1], points[position + 1]
                before = _foundations(bars, points, end=extreme.point.index - 1, context=context)
                observed = _foundations(bars, points, end=counter.confirmed_index, context=context)
                if before.signals is None or observed.signals is None or observed.transition is None:
                    self.fail('the frozen context must supply a causal transition')
                self.assertFalse(before.signals.flip_to_bull or before.signals.flip_to_bear)
                transition = observed.transition
                if transition.attack is None or transition.retracement is None:
                    self.fail('expanded geometry should still break its frozen key and confirm a partial countermove')
                self.assertEqual(transition.attack.bar_index, extreme.point.index)
                self.assertEqual(transition.alternation_confirmed_index, counter.confirmed_index)
                self.assertEqual((transition.impulse_origin, transition.impulse_extreme, transition.counterturn),
                                 (origin, extreme, counter))
                expected_ratio = (abs(extreme.point.price - counter.point.price)
                                  / abs(extreme.point.price - origin.point.price))
                self.assertAlmostEqual(transition.retracement.ratio, expected_ratio)
                self.assertAlmostEqual(expected_ratio, 0.5 if direction_high else 5 / 9)
                self.assertTrue(shallow_countermove(transition.retracement))
                self.assertFalse(weak_countermove(transition.retracement))


if __name__ == '__main__':
    unittest.main()
