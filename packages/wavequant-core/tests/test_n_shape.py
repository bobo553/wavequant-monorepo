from dataclasses import FrozenInstanceError, asdict, replace
from datetime import datetime, timedelta
import json
import unittest

from wavequant.model import Bar
from wavequant.price_action import Direction
from wavequant.n_shape import (BoxAnchorMode, MilestoneBasis, NSetup, NStatus,
                               PivotRef, ValueDomain, observe_n, project_n_targets)


def fixture():
    rows=[(8.5,9,8,8.5),(10,12,9.5,11),(10.7,11,10,10.5),
          (10.8,12.6,10.4,12.2),(12.2,14,12,13.8),
          (14,17.3,13.5,17.1),(17.2,21.9,17,21.8)]
    return [Bar(datetime(2026,1,1)+timedelta(days=i),'TEST',o,h,l,c,1000)
            for i,(o,h,l,c) in enumerate(rows)]


def setup(**kw):
    return replace(NSetup('TEST','1d',Direction.UP,PivotRef(0,0),PivotRef(1,1),PivotRef(2,2),
                          'test_confirmed_pivots',BoxAnchorMode.ATTACK_VIRTUAL_EXTREME),**kw)


def run(bars=None,s=None,**kw):
    return observe_n(fixture() if bars is None else bars,setup() if s is None else s,
                     timeframe='1d',milestone_basis=kw.pop('milestone_basis',MilestoneBasis.EXTREME),**kw)


def mirror(bar):
    return replace(bar,open=40-bar.open,high=40-bar.low,low=40-bar.high,close=40-bar.close)


class NShapeTests(unittest.TestCase):
    def test_basic_positive_completion_and_virtual_defense(self):
        r=run(fixture()[:4])
        self.assertEqual(r.status,NStatus.COMPLETED)
        self.assertEqual(r.completion.bar_index,3)
        self.assertTrue(r.completion.real_break and r.completion.virtual_break)
        self.assertAlmostEqual(r.completion.defense,10.4)
        self.assertEqual(r.completion.defense_name,'轧空低')

    def test_inverse_n_symmetry(self):
        r=run(list(map(mirror,fixture())),setup(direction=Direction.DOWN))
        self.assertEqual(r.completion.defense_name,'杀多高')
        self.assertAlmostEqual(r.completion.defense,29.6)
        self.assertAlmostEqual(r.targets.equal_wave,26)
        self.assertTrue(r.measured_wave_complete)

    def test_gap_virtual_defense_uses_previous_close(self):
        bars=fixture()[:4]
        bars[3]=replace(bars[3],open=12.2,low=12)
        self.assertEqual(run(bars).completion.defense,10.5)
        r=run(list(map(mirror,bars)),setup(direction=Direction.DOWN))
        self.assertEqual(r.completion.defense,29.5)

    def test_dual_threshold_close_can_be_below_old_high(self):
        bars=fixture()[:4]
        bars[3]=replace(bars[3],close=11.5)
        self.assertEqual(run(bars).status,NStatus.COMPLETED)
        self.assertLess(bars[3].close,bars[1].high)

    def test_partial_evidence_does_not_accumulate_across_bars(self):
        bars=fixture()[:5]
        bars[3]=replace(bars[3],close=10.8)
        bars[4]=replace(bars[4],open=11,high=11.8,low=10.7,close=11.5)
        r=run(bars)
        self.assertEqual(r.status,NStatus.FORMING)
        self.assertTrue(r.real_break_now)
        self.assertFalse(r.virtual_break_now)
        self.assertIsNone(r.completion)

    def test_equal_threshold_is_not_completion(self):
        for changes in (dict(close=11),dict(high=12,close=11.8)):
            bars=fixture()[:4]
            bars[3]=replace(bars[3],**changes)
            self.assertEqual(run(bars).status,NStatus.FORMING)

    def test_measurements_distinct_and_box_mode_explicit(self):
        r=run()
        self.assertEqual(r.targets.equal_wave,14)
        self.assertAlmostEqual(r.targets.one_p,17.2)
        self.assertAlmostEqual(r.targets.two_t,21.8)
        alt=run(s=setup(box_anchor_mode=BoxAnchorMode.NECKLINE_EXTREME))
        self.assertEqual((alt.targets.equal_wave,alt.targets.one_p,alt.targets.two_t),(14,16,20))

    def test_sequential_milestones_and_first_observation(self):
        r=run()
        self.assertEqual([m.name for m in r.milestones],['equal_wave','one_p','two_t'])
        self.assertEqual([m.bar_index for m in r.milestones],[4,5,6])
        self.assertTrue(r.measured_wave_complete and r.stacking_precondition_observed)
        self.assertIsNone(r.next_target)
        self.assertIn('undefined',r.stacking_rule)

    def test_completion_day_high_not_retroactive_measurement(self):
        bars=fixture()[:4]
        bars[3]=replace(bars[3],high=18)
        r=run(bars)
        self.assertEqual(r.milestones,())
        self.assertEqual(r.next_target,'equal_wave')
        self.assertEqual(r.targets.box_anchor,18)

    def test_completion_close_can_observe_equal_wave(self):
        bars=fixture()[:4]
        bars[3]=replace(bars[3],high=15,close=14.5)
        r=run(bars)
        self.assertEqual(len(r.milestones),1)
        self.assertEqual(r.milestones[0].observation_kind,'completion_close')

    def test_one_bar_multiple_targets_not_multiple_fills(self):
        bars=fixture()[:5]
        bars[4]=replace(bars[4],high=23,close=22)
        r=run(bars)
        self.assertEqual([m.bar_index for m in r.milestones],[4,4,4])
        self.assertEqual([m.shared_bar_with_previous for m in r.milestones],[False,True,True])

    def test_close_and_extreme_measurement_are_separate(self):
        bars=fixture()[:5]
        self.assertEqual(len(run(bars).milestones),1)
        self.assertEqual(len(run(bars,milestone_basis=MilestoneBasis.CLOSE).milestones),0)

    def test_defense_breach_not_automatic_origin_failure(self):
        bars=fixture()[:5]
        bars[4]=replace(bars[4],low=10)
        r=run(bars)
        self.assertEqual(r.first_defense_breach_index,4)
        self.assertIsNone(r.first_origin_breach_index)
        self.assertEqual(r.status,NStatus.COMPLETED)
        self.assertEqual(len(r.milestones),1)

    def test_origin_break_suspends_new_measurements(self):
        bars=fixture()
        bars[4]=replace(bars[4],low=7,high=23)
        r=run(bars)
        self.assertEqual(r.first_origin_breach_index,4)
        self.assertEqual(r.milestones,())
        self.assertFalse(r.measured_wave_complete)
        self.assertEqual(r.status,NStatus.COMPLETED)

    def test_origin_break_before_completion_invalidates(self):
        bars=fixture()
        bars[3]=replace(bars[3],low=7)
        r=run(bars)
        self.assertEqual(r.status,NStatus.INVALIDATED)
        self.assertIsNone(r.completion)

    def test_pullback_extension_requires_new_anchor(self):
        bars=fixture()
        bars[3]=replace(bars[3],low=9.9)
        self.assertEqual(run(bars).status,NStatus.PULLBACK_EXTENDED)

    def test_future_confirmed_pivots_not_used_or_backdated(self):
        s=setup(pullback=PivotRef(2,3))
        self.assertEqual(run(fixture()[:3],s).status,NStatus.AWAIT_ANCHORS)
        r=run(fixture()[:4],s)
        self.assertEqual(r.status,NStatus.FORMING)
        self.assertIsNone(r.completion)
        # Next bar is already closing above both references: no late replay.
        self.assertIsNone(run(fixture()[:5],s).completion)

    def test_no_rewrite_of_completion_defense_targets_or_hits(self):
        bars=fixture()
        full=run(bars)
        for length in range(1,len(bars)+1):
            prefix=run(bars[:length])
            self.assertEqual(prefix,run(bars,asof_index=length-1))
            if prefix.completion:
                self.assertEqual(prefix.completion,full.completion)
                self.assertEqual(prefix.targets,full.targets)
                self.assertEqual(prefix.milestones,tuple(m for m in full.milestones if m.bar_index<length))
        changed=list(bars)
        changed[-1]=replace(changed[-1],high=100,close=99)
        self.assertEqual(run(changed).completion,full.completion)
        self.assertEqual(run(changed).targets,full.targets)

    def test_delayed_anchor_cannot_replay_dual_occupancy_below_old_high(self):
        bars=fixture()[:5]
        bars[3]=replace(bars[3],close=11.5)
        bars[4]=replace(bars[4],open=11.5,high=12.8,low=11.4,close=11.6)
        s=setup(pullback=PivotRef(2,3))
        self.assertEqual(run(bars,s).status,NStatus.FORMING)
        self.assertIsNone(run(bars,s).completion)

    def test_price_targets_nonpositive_unavailable_not_bottom_prediction(self):
        r=project_n_targets(20,12,16,box_anchor=12,direction=Direction.DOWN,domain=ValueDomain.PRICE)
        self.assertEqual(r.equal_wave,8)
        self.assertEqual(r.one_p,4)
        self.assertIsNone(r.two_t)
        self.assertEqual(r.unavailable,('two_t',))

    def test_decimal_target_equality_is_preserved(self):
        r=project_n_targets(8,12,10,box_anchor=12.6,direction=Direction.UP,domain=ValueDomain.PRICE)
        self.assertEqual(r.two_t,21.8)
        bars=fixture()
        bars[-1]=replace(bars[-1],high=21.8,close=21.8)
        self.assertTrue(run(bars).measured_wave_complete)

    def test_signed_indicator_projection_without_fabricated_price_bars(self):
        r=project_n_targets(-5,1,-2,box_anchor=2,direction=Direction.UP,domain=ValueDomain.INDICATOR)
        self.assertEqual((r.equal_wave,r.one_p,r.two_t),(4,9,16))
        r=project_n_targets(5,-1,2,box_anchor=-2,direction=Direction.DOWN,domain=ValueDomain.INDICATOR)
        self.assertEqual((r.equal_wave,r.one_p,r.two_t),(-4,-9,-16))

    def test_invalid_setup_series_and_projection_fail(self):
        with self.assertRaises(ValueError): setup(pullback=PivotRef(1,2))
        with self.assertRaises(ValueError): PivotRef(2,1)
        with self.assertRaises(ValueError): run(s=setup(symbol='OTHER'))
        with self.assertRaises(ValueError): run(s=setup(timeframe='5m'))
        with self.assertRaises(ValueError): run(milestone_basis='close')
        with self.assertRaises(ValueError): project_n_targets(8,12,8,box_anchor=12,direction=Direction.UP,domain=ValueDomain.PRICE)
        with self.assertRaises(ValueError): project_n_targets(8,12,10,box_anchor=11,direction=Direction.UP,domain=ValueDomain.PRICE)
        with self.assertRaises(ValueError): project_n_targets(8,float('nan'),10,box_anchor=12,direction=Direction.UP,domain=ValueDomain.PRICE)

    def test_immutable_setup_and_json_safe_result(self):
        s=setup()
        with self.assertRaises(FrozenInstanceError): s.origin=PivotRef(1,1)
        json.dumps(asdict(run()),default=str,allow_nan=False)

    def test_volume_does_not_change_n_definition(self):
        bars=[replace(b,volume=b.volume*100) for b in fixture()]
        self.assertEqual(run(bars),run())


if __name__=='__main__':
    unittest.main()
