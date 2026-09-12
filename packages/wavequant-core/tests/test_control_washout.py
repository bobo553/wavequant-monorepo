from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta
import unittest

from wavequant.domain.models.model import Bar
from wavequant.domain.market_structure.price_action import Direction, LevelKind
from wavequant.domain.market_structure.n_shape import NSetup, PivotRef, BoxAnchorMode, MilestoneBasis, NStatus
from wavequant.domain.market_state.control_bar import observe_control_bar
from wavequant.domain.market_state.washout import WashoutPolicy, WashoutStage as S, observe_washout


def fixture():
    rows = [(8.5, 9, 8, 8.5), (10, 12, 9.5, 11), (10.7, 11, 10, 10.5),
            (10.8, 12.6, 10.4, 12.2), (13, 19, 12.5, 18), (15, 16, 13, 14),
            (14.5, 16.5, 14, 16), (15.5, 16, 14, 15), (15, 17, 14.5, 16.8)]
    return [Bar(datetime(2026, 1, 1)+timedelta(days=i), 'TEST', *row, 1000 if i != 3 else 2000)
            for i, row in enumerate(rows)]


def setup(direction=Direction.UP, *, second=False, mode=BoxAnchorMode.ATTACK_VIRTUAL_EXTREME):
    a, b, c = (5, 6, 7) if second else (0, 1, 2)
    return NSetup('TEST', '1d', direction, PivotRef(a, a), PivotRef(b, b), PivotRef(c, c),
                  'fixed_test_second' if second else 'fixed_test_initial', mode)


def mirror(bars):
    return [replace(b, open=50-b.open, high=50-b.low, low=50-b.high, close=50-b.close) for b in bars]


def control(bars=None, direction=Direction.UP, **kw):
    return observe_control_bar(fixture() if bars is None else bars, setup(direction), timeframe='1d',
        volume_lookback=kw.pop('volume_lookback', 3), shadow_policy=kw.pop('shadow_policy', None), **kw)


def wash(bars=None, direction=Direction.UP, **kw):
    return observe_washout(fixture() if bars is None else bars, kw.pop('initial', setup(direction)),
        timeframe='1d', policy=WashoutPolicy(kw.pop('basis', MilestoneBasis.CLOSE)),
        reattack_setup=kw.pop('second', setup(direction, second=True)), **kw)


class ControlBarTests(unittest.TestCase):
    def test_frozen_defense_reuses_n_and_is_not_cost(self):
        r = control()
        self.assertEqual(r.defense_level.price, 10.4)
        self.assertEqual(r.defense_level.kind, LevelKind.SUPPORT)
        self.assertEqual(r.defense_level.confirmed_index, 3)
        self.assertEqual(r.cost_basis, 'not_identifiable_from_ohlcv')
        self.assertEqual(r.participant_intent, 'unknown_not_inferred')
        q = control(mirror(fixture()), Direction.DOWN)
        self.assertEqual(q.defense_level.price, 39.6)
        self.assertEqual(q.defense_level.kind, LevelKind.RESISTANCE)

    def test_gap_virtual_defense_not_actual_low(self):
        data = fixture()
        data[3] = replace(data[3], open=12, low=11.9)
        self.assertEqual(control(data).defense_level.price, 10.5)
        self.assertEqual(control(mirror(data), Direction.DOWN).defense_level.price, 39.5)

    def test_volume_previous_and_past_only_mean(self):
        r = control()
        self.assertTrue(r.volume.increased_from_previous)
        self.assertEqual(r.volume.trailing_mean, 1000)
        self.assertEqual(r.volume.relative_volume, 2)
        data = fixture()
        data[8] = replace(data[8], volume=999999)
        self.assertEqual(control(data).volume, r.volume)

    def test_no_volume_increase_does_not_cancel_n(self):
        data = [replace(b, volume=1000) for b in fixture()]
        r = control(data)
        self.assertFalse(r.volume.increased_from_mean)
        self.assertEqual(r.n_status, NStatus.COMPLETED)

    def test_insufficient_or_zero_baseline(self):
        self.assertIsNone(control(volume_lookback=20).volume.trailing_mean)
        r = control([replace(b, volume=0) for b in fixture()])
        self.assertIsNone(r.volume.relative_volume)
        self.assertEqual(r.volume.trailing_mean, 0)

    def test_response_only_next_bar_and_unknown_shadow(self):
        r = control(asof_index=3)
        self.assertIsNone(r.next_bar_resistance)
        self.assertEqual(r.defense_frames, ())
        self.assertIsNone(control().next_bar_resistance.detected)
        data = fixture()
        data[4] = replace(data[4], open=12, low=12)
        self.assertTrue(control(data).next_bar_resistance.detected)

    def test_intrabar_close_breach_recovery_and_no_erasure(self):
        data = fixture()[:7]
        data[4] = replace(data[4], low=10, close=11)
        data[5] = replace(data[5], open=11, high=12, low=9, close=10)
        data[6] = replace(data[6], open=10, high=12, low=9.5, close=10.4)
        r = control(data)
        self.assertEqual(r.defense_frames[0].first_intrabar_breach_index, 4)
        self.assertIsNone(r.defense_frames[0].first_close_breach_index)
        self.assertEqual(r.defense_frames[-1].first_close_breach_index, 5)
        self.assertTrue(r.defense_frames[-1].recovered_on_close)
        self.assertFalse(r.defense_frames[-1].close_beyond)

    def test_equality_not_breach(self):
        data = fixture()[:5]
        data[4] = replace(data[4], open=11, low=10.4, close=10.4)
        self.assertFalse(control(data).defense_frames[-1].intrabar_beyond)

    def test_prefix_invariance(self):
        data = fixture()
        full = control(data)
        for i in range(len(data)):
            r = control(data[:i+1])
            self.assertEqual(r, control(data, asof_index=i))
            self.assertEqual(r.defense_frames, tuple(f for f in full.defense_frames if f.bar_index <= i))

    def test_validation(self):
        for value in (0, True, 1.5):
            with self.assertRaises(ValueError):
                control(volume_lookback=value)
        for value in (-1, float('nan'), float('inf')):
            data = fixture()
            data[3] = replace(data[3], volume=value)
            with self.assertRaises(ValueError):
                control(data)
        with self.assertRaises(ValueError):
            control(shadow_policy=.5)


class WashoutTests(unittest.TestCase):
    def test_bottom_complete_sequence(self):
        r = wash()
        self.assertEqual(r.neckline, 12)
        self.assertEqual((r.one_p, r.two_t), (17.2, 21.8))
        self.assertEqual(r.latest.stage, S.CONFIRMED)
        self.assertEqual(r.latest.first_target_band_index, 4)
        self.assertEqual(r.latest.first_pullback_band_index, 5)
        self.assertEqual(r.latest.reattack_index, 8)
        self.assertEqual(r.latest.new_n_defense, 14.5)
        self.assertIsNone(r.probability)
        self.assertFalse(r.opens_short_position)

    def test_top_exact_mirror_risk_only(self):
        r = wash(mirror(fixture()), Direction.DOWN)
        self.assertEqual(r.latest.stage, S.CONFIRMED)
        self.assertEqual((r.one_p, r.two_t), (32.8, 28.2))
        self.assertEqual(r.latest.reattack_index, 8)
        self.assertEqual(r.review_role, 'long_risk_review_not_short_order')
        self.assertFalse(r.opens_short_position)

    def test_no_n_or_unknown_pivots_no_leak(self):
        r = wash(asof_index=1)
        self.assertEqual(r.n_status, NStatus.AWAIT_ANCHORS)
        self.assertEqual(r.frames, ())
        self.assertIsNone(r.one_p)

    def test_new_n_is_required_not_just_rebound(self):
        self.assertEqual(wash(second=None).latest.stage, S.AWAIT_REATTACK)
        self.assertEqual(wash(asof_index=7).latest.stage, S.AWAIT_REATTACK)
        self.assertIsNone(wash(asof_index=7).latest.new_n_defense)

    def test_second_n_must_be_independent_and_same_direction(self):
        for second in (setup(), setup(Direction.DOWN, second=True),
                       replace(setup(second=True), symbol='OTHER')):
            with self.assertRaises(ValueError):
                wash(second=second)

    def test_second_n_origin_must_be_in_observed_pullback(self):
        with self.assertRaises(ValueError):
            wash(second=replace(setup(second=True), origin=PivotRef(4, 4)))

    def test_second_n_failure_exposes_reason_without_false_confirmation(self):
        data = fixture()
        data[8] = replace(data[8], low=13.5)
        r = wash(data)
        self.assertEqual(r.latest.stage, S.AWAIT_REATTACK)
        self.assertIn('pullback_extended', r.latest.reason)
        self.assertIsNone(r.latest.reattack_index)

    def test_volume_not_a_washout_gate(self):
        self.assertEqual(wash(), wash([replace(b, volume=0) for b in fixture()]))

    def test_target_one_p_equality_not_open_band(self):
        data = fixture()[:5]
        data[4] = replace(data[4], close=17.2)
        self.assertEqual(wash(data).latest.stage, S.AWAIT_TARGET)
        self.assertEqual(wash(data, basis=MilestoneBasis.EXTREME).latest.stage, S.AWAIT_PULLBACK)

    def test_two_t_touch_or_gap_over_is_outside_template(self):
        for value in (21.8, 24):
            data = fixture()
            data[4] = replace(data[4], high=value)
            r = wash(data)
            self.assertEqual(r.latest.stage, S.OUTSIDE_TEMPLATE)
            self.assertEqual(r.latest.reason, 'two_t_reached_or_overshot')
            self.assertIsNone(r.latest.reattack_index)

    def test_pullback_band_open_boundaries(self):
        for value in (12, 17.2):
            data = fixture()[:6]
            data[5] = replace(data[5], low=12, high=18, close=value)
            self.assertEqual(wash(data, second=None).latest.stage, S.AWAIT_PULLBACK)

    def test_neckline_touch_holds_but_break_exits(self):
        data = fixture()[:6]
        data[5] = replace(data[5], low=12)
        self.assertEqual(wash(data, second=None).latest.stage, S.AWAIT_REATTACK)
        data[5] = replace(data[5], low=11.9)
        r = wash(data)
        self.assertEqual(r.latest.stage, S.OUTSIDE_TEMPLATE)
        self.assertEqual(r.latest.reason, 'neckline_lost_after_target_band')

    def test_origin_failure_overrides_same_bar_target(self):
        data = fixture()
        data[4] = replace(data[4], low=7, high=23)
        self.assertEqual(wash(data).latest.stage, S.INVALIDATED)

    def test_target_and_neckline_break_same_bar_unknown_order(self):
        data = fixture()
        data[4] = replace(data[4], low=11)
        self.assertEqual(wash(data).latest.reason, 'target_and_neckline_breach_order_unknown')

    def test_same_bar_target_and_pullback_not_two_stages(self):
        r = wash(asof_index=4, basis=MilestoneBasis.EXTREME)
        self.assertEqual(r.latest.stage, S.AWAIT_PULLBACK)
        self.assertIsNone(r.latest.first_pullback_band_index)

    def test_formation_bar_close_only(self):
        data = fixture()[:4]
        data[3] = replace(data[3], high=21, close=18)
        r = wash(data, initial=setup(mode=BoxAnchorMode.NECKLINE_EXTREME), second=None,
                 basis=MilestoneBasis.EXTREME)
        self.assertEqual((r.one_p, r.two_t), (16, 20))
        self.assertEqual(r.latest.stage, S.AWAIT_PULLBACK)
        self.assertEqual(r.latest.first_target_band_index, 3)

    def test_unavailable_negative_price_projections(self):
        data = [replace(b, open=22-b.open, high=22-b.low, low=22-b.high, close=22-b.close)
                for b in fixture()[:4]]
        # More expansive box makes the inverse 2T negative while OHLC stays positive.
        data[3] = replace(data[3], low=7, close=8)
        r = wash(data, Direction.DOWN, second=None)
        self.assertEqual(r.latest.stage, S.UNAVAILABLE)
        self.assertIsNone(r.two_t)

    def test_confirmation_remains_historical_after_later_decline(self):
        data = fixture()
        data.append(replace(data[-1], timestamp=data[-1].timestamp+timedelta(days=1), low=7))
        r = wash(data)
        self.assertEqual(r.latest.stage, S.CONFIRMED)
        self.assertEqual(r.latest.reattack_index, 8)

    def test_prefix_invariance_with_future_second_n(self):
        for direction, data in ((Direction.UP, fixture()), (Direction.DOWN, mirror(fixture()))):
            full = wash(data, direction)
            for i in range(len(data)):
                r = wash(data[:i+1], direction)
                self.assertEqual(r, wash(data, direction, asof_index=i))
                self.assertEqual(r.frames, tuple(f for f in full.frames if f.bar_index <= i))

    def test_future_invalid_prices_not_consumed(self):
        data = fixture()
        data[8] = replace(data[8], close=float('nan'))
        self.assertEqual(wash(data, asof_index=5), wash(asof_index=5))

    def test_reattack_and_neckline_loss_same_bar_not_confirmation(self):
        data = fixture()
        data[8] = replace(data[8], low=11.5)
        r = wash(data)
        self.assertEqual(r.latest.stage, S.OUTSIDE_TEMPLATE)
        self.assertEqual(r.latest.reason, 'neckline_lost_after_target_band')
        self.assertIsNone(r.latest.reattack_index)

    def test_later_second_n_failure_does_not_rewrite_waiting_frames(self):
        data = fixture()
        data[8] = replace(data[8], low=13.5)
        full = wash(data)
        for i in range(3, len(data)):
            self.assertEqual(wash(data[:i+1]).frames, full.frames[:i-2])

    def test_new_n_must_really_have_virtual_and_real_breaks(self):
        data = fixture()
        data[8] = replace(data[8], high=16.5, close=16.4)
        r = wash(data)
        self.assertEqual(r.latest.stage, S.AWAIT_REATTACK)
        self.assertIsNone(r.latest.reattack_index)

    def test_policy_and_immutable_results(self):
        with self.assertRaises(ValueError):
            WashoutPolicy('close')
        with self.assertRaises(FrozenInstanceError):
            wash().latest.stage = S.INVALIDATED


if __name__ == '__main__':
    unittest.main()
