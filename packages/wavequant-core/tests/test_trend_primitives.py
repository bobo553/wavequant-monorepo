from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta
from typing import cast
import unittest

from wavequant.domain.market_structure.polyline import LinePoint, PointKind, ReversalPoint
from wavequant.domain.market_structure.price_action import AttackBasis, Direction, LevelKind
from wavequant.domain.market_structure.trend_primitives import (
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
    RetracementEvidence,
    StructuralTrend,
    StructureContext,
    TransitionStage,
    TrendTransition,
    observe_structure,
    observe_trend_transition,
    retracement_evidence,
)
from wavequant.domain.models.model import Bar


def _point(index: int, kind: PointKind, price: float, confirmed: int | None = None) -> ReversalPoint:
    return ReversalPoint(
        LinePoint(index, 0, kind, price),
        index + 1 if confirmed is None else confirmed,
        'fixture_confirmed',
    )


def _fixture() -> tuple[list[Bar], list[ReversalPoint]]:
    points = [
        _point(index * 2, kind, price)
        for index, (kind, price) in enumerate([
            (PointKind.HIGH, 30), (PointKind.LOW, 20),
            (PointKind.HIGH, 25), (PointKind.LOW, 10),
            (PointKind.HIGH, 18), (PointKind.LOW, 13),
            (PointKind.HIGH, 28), (PointKind.LOW, 22),
        ])
    ]
    rows = [
        (24, 30, 23, 25), (24, 26, 22, 23), (22, 23, 20, 21),
        (22, 24, 21, 23), (23, 25, 22, 24), (22, 23, 15, 16),
        (13, 15, 10, 12), (13, 16, 12, 15), (16, 18, 15, 17),
        (16, 17, 14, 15), (15, 16, 13, 14), (16, 24, 15, 23),
        (24, 28, 23, 27), (26, 27, 23, 24), (24, 25, 22, 23),
        (24, 27, 23, 26),
    ]
    bars = [
        Bar(datetime(2026, 1, 1) + timedelta(days=index), 'TEST', *row, 1000)
        for index, row in enumerate(rows)
    ]
    return bars, points


def _context(points: list[ReversalPoint], *, end: int, start: int = 0) -> StructureContext:
    return observe_structure(points, symbol='TEST', timeframe='1d', window_start=start, asof_index=end)


def _transition(
    bars: list[Bar],
    points: list[ReversalPoint],
    *,
    end: int,
    basis: AttackBasis = AttackBasis.CLOSE,
) -> TrendTransition:
    return observe_trend_transition(
        bars,
        points,
        context=_context(points, end=7),
        attack_basis=basis,
        asof_index=end,
    )


def _mirror(bars: list[Bar], points: list[ReversalPoint]) -> tuple[list[Bar], list[ReversalPoint]]:
    return (
        [replace(bar, open=50 - bar.open, high=50 - bar.low, low=50 - bar.high, close=50 - bar.close)
         for bar in bars],
        [_point(point.point.index, PointKind.LOW if point.point.kind == PointKind.HIGH else PointKind.HIGH,
                50 - point.point.price, point.confirmed_index) for point in points],
    )


class TrendPrimitiveTests(unittest.TestCase):
    def test_reversals_require_the_confirmation_date(self) -> None:
        low = _point(2, PointKind.LOW, 10, confirmed=5)
        high = _point(3, PointKind.HIGH, 20, confirmed=6)
        self.assertFalse(positive_reversal(low, asof_index=4))
        self.assertTrue(positive_reversal(low, asof_index=5))
        self.assertFalse(negative_reversal(high, asof_index=5))
        self.assertTrue(negative_reversal(high, asof_index=6))
        self.assertFalse(positive_reversal(high, asof_index=6))
        self.assertFalse(negative_reversal(low, asof_index=6))

    def test_reversal_rejects_invalid_point_and_index(self) -> None:
        for primitive in (positive_reversal, negative_reversal):
            with self.assertRaises(ValueError):
                primitive(cast(ReversalPoint, object()), asof_index=5)
            with self.assertRaises(ValueError):
                primitive(_point(2, PointKind.LOW, 10), asof_index=-1)

    def test_figure_008_keys_cover_all_four_named_mappings(self) -> None:
        labels = ['L1', 'H1', 'L0', 'H2', 'L2', 'H3', 'L3', 'H4', 'L4',
                  'H0', 'L5', 'H5', 'L6', 'H6', 'L7', 'H7', 'L8']
        prices = [20, 30, 10, 20, 14, 26, 18, 35, 28, 45, 38, 42, 32, 36, 24, 29, 26]
        points = [
            _point(index, PointKind.LOW if label.startswith('L') else PointKind.HIGH, price)
            for index, (label, price) in enumerate(zip(labels, prices))
        ]
        context = _context(points, end=17)
        self.assertIs(last_fall_high(context), context.last_fall_high)
        self.assertIs(last_rise_low(context), context.last_rise_low)
        for target_name, predecessor_name in [('L0', 'H1'), ('L7', 'H6'), ('H0', 'L4'), ('H4', 'L3')]:
            with self.subTest(target=target_name):
                target = points[labels.index(target_name)]
                predecessor = points[labels.index(predecessor_name)]
                key = (last_fall_high(context, target) if target_name.startswith('L')
                       else last_rise_low(context, target))
                self.assertIsNotNone(key)
                if key is None:
                    self.fail('named predecessor should be inside the context')
                self.assertEqual((key.source_index, key.price), (predecessor.point.index, predecessor.point.price))
                self.assertEqual((key.symbol, key.timeframe, key.confirmed_index), ('TEST', '1d', 17))
                self.assertEqual(key.kind, LevelKind.RESISTANCE if target_name.startswith('L') else LevelKind.SUPPORT)
                self.assertIn(f'preceding_{target.point.kind.value}_{target.point.index}_', key.source)

    def test_selected_key_rejects_wrong_kind_nonmember_and_future_point(self) -> None:
        _, points = _fixture()
        context = _context(points, end=7)
        with self.assertRaises(ValueError):
            last_fall_high(context, points[0])
        with self.assertRaises(ValueError):
            last_rise_low(context, points[1])
        with self.assertRaises(ValueError):
            last_fall_high(context, _point(1, PointKind.LOW, 19))
        with self.assertRaisesRegex(ValueError, 'already be confirmed'):
            last_fall_high(context, points[-1])

    def test_missing_left_context_has_no_key(self) -> None:
        _, points = _fixture()
        context = _context(points, end=15, start=6)
        self.assertIsNone(last_fall_high(context))
        self.assertIsNone(last_fall_high(context, points[3]))
        high_first = _context(points, end=7)
        self.assertIsNone(last_rise_low(high_first, points[0]))

    def test_trend_uses_every_high_and_low_in_the_selected_window(self) -> None:
        bars, points = _fixture()
        bear = _context(points, end=7)
        self.assertFalse(is_bull_trend(bear))
        self.assertTrue(is_bear_trend(bear))
        _, mirrored_points = _mirror(bars, points)
        bull = _context(mirrored_points, end=7)
        self.assertTrue(is_bull_trend(bull))
        self.assertFalse(is_bear_trend(bull))
        local_bull = _context(points, end=15)
        self.assertEqual(local_bull.trend, StructuralTrend.BULL)
        self.assertFalse(is_bull_trend(local_bull))
        self.assertFalse(is_bear_trend(local_bull))
        unknown = _context(points, end=5)
        self.assertIsNone(is_bull_trend(unknown))
        self.assertIsNone(is_bear_trend(unknown))

    def test_equal_highs_or_lows_are_not_a_strict_trend(self) -> None:
        points = [_point(0, PointKind.HIGH, 30), _point(2, PointKind.LOW, 10),
                  _point(4, PointKind.HIGH, 30), _point(6, PointKind.LOW, 12)]
        context = _context(points, end=7)
        self.assertFalse(is_bull_trend(context))
        self.assertFalse(is_bear_trend(context))

    def test_partial_countermoves_use_strict_literal_67_and_33_in_both_directions(self) -> None:
        cases = [
            (Direction.UP, 100, 200, 133, 133.1, 167, 167.1),
            (Direction.DOWN, 200, 100, 167, 166.9, 133, 132.9),
        ]
        for direction, origin, extreme, at_67, below_67, at_33, below_33 in cases:
            with self.subTest(direction=direction):
                self.assertFalse(shallow_countermove(retracement_evidence(origin, extreme, at_67, direction=direction)))
                self.assertTrue(shallow_countermove(
                    retracement_evidence(origin, extreme, below_67, direction=direction)))
                self.assertFalse(weak_countermove(retracement_evidence(origin, extreme, at_33, direction=direction)))
                self.assertTrue(weak_countermove(retracement_evidence(origin, extreme, below_33, direction=direction)))
                zero = retracement_evidence(origin, extreme, extreme, direction=direction)
                full = retracement_evidence(origin, extreme, origin, direction=direction)
                for evidence in (zero, full):
                    self.assertFalse(shallow_countermove(evidence))
                    self.assertFalse(weak_countermove(evidence))

    def test_countermove_evidence_has_directional_mirror(self) -> None:
        up = retracement_evidence(10, 20, 16, direction=Direction.UP)
        down = retracement_evidence(30, 20, 24, direction=Direction.DOWN)
        self.assertEqual(up, down)
        self.assertEqual(shallow_countermove(up), shallow_countermove(down))
        self.assertEqual(weak_countermove(up), weak_countermove(down))
        for primitive in (shallow_countermove, weak_countermove):
            with self.assertRaises(ValueError):
                primitive(cast(RetracementEvidence, object()))


class TrendDefinitionSignalTests(unittest.TestCase):
    def test_key_touch_preserves_suspicion_before_strict_formation_in_both_directions(self) -> None:
        bars, points = _fixture()
        bars[8] = replace(bars[8], high=25)
        points[4] = replace(points[4], point=replace(points[4].point, price=25))
        for data, pivots in ((bars, points), _mirror(bars, points)):
            with self.subTest(direction=_context(pivots, end=7).trend):
                before = observe_trend_definition_signals(_transition(data, pivots, end=10))
                transition = _transition(data, pivots, end=11)
                suspicion = observe_trend_definition_signals(transition)
                up = transition.direction == Direction.UP
                self.assertEqual(pivots[4].point.price, transition.key.price)
                self.assertFalse(before.head_suspicion or before.bottom_suspicion)
                self.assertEqual(transition.suspicion_index, 11)
                self.assertEqual(suspicion.stage, TransitionStage.SUSPICION)
                self.assertEqual((suspicion.head_suspicion, suspicion.bottom_suspicion), (not up, up))
                self.assertFalse(suspicion.flip_to_bull or suspicion.flip_to_bear)
                self.assertFalse(suspicion.head_formed or suspicion.bottom_formed)

                attacked = _transition(data, pivots, end=12)
                formed = observe_trend_definition_signals(attacked)
                self.assertIsNotNone(attacked.attack)
                if attacked.attack is None:
                    self.fail('the later strict key crossing should establish an attack')
                self.assertEqual(attacked.attack.bar_index, 12)
                self.assertEqual((formed.flip_to_bull, formed.flip_to_bear), (up, not up))
                self.assertEqual((formed.head_formed, formed.bottom_formed), (not up, up))
                self.assertFalse(formed.bear_to_bull_alternation or formed.bull_to_bear_alternation)

    def test_suspicion_attack_and_alternation_are_separate_causal_events(self) -> None:
        bars, points = _fixture()
        before_suspicion = observe_trend_definition_signals(_transition(bars, points, end=10))
        suspicion = observe_trend_definition_signals(_transition(bars, points, end=11))
        attack = observe_trend_definition_signals(_transition(bars, points, end=12))
        unconfirmed_counter = observe_trend_definition_signals(_transition(bars, points, end=14))
        alternation = observe_trend_definition_signals(_transition(bars, points, end=15))
        self.assertFalse(before_suspicion.bottom_suspicion)
        self.assertTrue(suspicion.bottom_suspicion)
        self.assertFalse(suspicion.flip_to_bull)
        self.assertFalse(suspicion.bottom_formed)
        self.assertTrue(attack.flip_to_bull)
        self.assertTrue(attack.bottom_formed)
        self.assertFalse(attack.bear_to_bull_alternation)
        self.assertFalse(unconfirmed_counter.bear_to_bull_alternation)
        self.assertTrue(alternation.bear_to_bull_alternation)
        self.assertFalse(alternation.flip_to_bear)
        self.assertFalse(alternation.head_suspicion)
        self.assertFalse(alternation.head_formed)
        self.assertEqual((alternation.asof_index, alternation.stage), (15, TransitionStage.ALTERNATION))

    def test_head_and_bearish_definitions_are_exact_mirrors(self) -> None:
        bars, points = _mirror(*_fixture())
        suspicion = observe_trend_definition_signals(_transition(bars, points, end=11))
        attack = observe_trend_definition_signals(_transition(bars, points, end=12))
        alternation = observe_trend_definition_signals(_transition(bars, points, end=15))
        self.assertTrue(suspicion.head_suspicion)
        self.assertFalse(suspicion.flip_to_bear)
        self.assertTrue(attack.flip_to_bear)
        self.assertTrue(attack.head_formed)
        self.assertFalse(attack.bull_to_bear_alternation)
        self.assertTrue(alternation.bull_to_bear_alternation)
        self.assertFalse(alternation.flip_to_bull)
        self.assertFalse(alternation.bottom_suspicion)
        self.assertFalse(alternation.bottom_formed)

    def test_direct_flip_without_suspicion_does_not_claim_head_or_bottom_formation(self) -> None:
        bars, points = _fixture()
        points = points[:4] + points[-2:]
        for data, pivots in ((bars, points), _mirror(bars, points)):
            transition = _transition(data, pivots, end=15)
            self.assertIsNone(transition.suspicion_index)
            self.assertIsNone(transition.formation_name)
            signals = observe_trend_definition_signals(transition)
            self.assertTrue(signals.flip_to_bull or signals.flip_to_bear)
            self.assertFalse(signals.head_formed)
            self.assertFalse(signals.bottom_formed)

    def test_explicit_attack_basis_keeps_close_touch_distinct_from_intrabar_crossing(self) -> None:
        bars, points = _fixture()
        bars[12] = replace(bars[12], close=25)
        close = observe_trend_definition_signals(_transition(bars, points, end=12, basis=AttackBasis.CLOSE))
        intrabar = observe_trend_definition_signals(_transition(bars, points, end=12, basis=AttackBasis.INTRABAR))
        self.assertFalse(close.flip_to_bull)
        self.assertTrue(intrabar.flip_to_bull)
        self.assertFalse(intrabar.bear_to_bull_alternation)

    def test_invalidated_stage_retains_already_observed_history_in_both_directions(self) -> None:
        bars, points = _fixture()
        bars.append(replace(bars[-1], timestamp=bars[-1].timestamp + timedelta(days=1), low=9))
        for data, pivots in ((bars, points), _mirror(bars, points)):
            transition = _transition(data, pivots, end=16)
            signals = observe_trend_definition_signals(transition)
            self.assertEqual(signals.stage, TransitionStage.INVALIDATED)
            self.assertTrue(signals.flip_to_bull or signals.flip_to_bear)
            self.assertTrue(signals.bear_to_bull_alternation or signals.bull_to_bear_alternation)
            self.assertTrue(signals.head_suspicion or signals.bottom_suspicion)
            self.assertTrue(signals.head_formed or signals.bottom_formed)

    def test_failure_before_counter_confirmation_does_not_create_alternation(self) -> None:
        bars, points = _fixture()
        bars[15] = replace(bars[15], low=9)
        signals = observe_trend_definition_signals(_transition(bars, points, end=15))
        self.assertEqual(signals.stage, TransitionStage.INVALIDATED)
        self.assertTrue(signals.flip_to_bull)
        self.assertFalse(signals.bear_to_bull_alternation)

    def test_signals_are_immutable_and_reject_unavailable_or_misordered_events(self) -> None:
        bars, points = _fixture()
        transition = _transition(bars, points, end=15)
        signals = observe_trend_definition_signals(transition)
        with self.assertRaises(FrozenInstanceError):
            setattr(signals, 'bottom_formed', False)
        for malformed in (
            replace(transition, suspicion_index=16),
            replace(transition, suspicion_index=12),
            replace(transition, alternation_confirmed_index=12),
            replace(transition, attack=None),
        ):
            with self.assertRaises(ValueError):
                observe_trend_definition_signals(malformed)


if __name__ == '__main__':
    unittest.main()
