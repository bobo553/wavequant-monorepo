from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest

from wavequant.application.analytics.foundation_audit import audit_foundations
from wavequant.domain.market_structure.foundations import (SwingPoint, bar_relations, force_profile, measured_targets,
                                   n_break_evidence, resistance_evidence, swing_context,
                                   three_bar_state, turning_evidence)
from wavequant.application.analytics.research import write_rows


def bar(i,o,h,l,c):
    from wavequant.domain.models.model import Bar
    return Bar(datetime(2020,1,1)+timedelta(days=i),'TEST',o,h,l,c,1_000_000)


def mirror(b):
    return replace(b,open=30-b.open,high=30-b.low,low=30-b.high,close=30-b.close)


class FoundationTests(unittest.TestCase):
    def test_virtual_points_include_gap_and_previous_close(self):
        p=bar(0,10,10,10,10)
        up=bar_relations(bar(1,12,13,12,12.8),p)
        down=bar_relations(bar(1,8,8,7,7.5),p)
        self.assertEqual(up['virtual_low'],10)
        self.assertEqual(down['virtual_high'],10)

    def test_six_basic_candle_terms_and_ties(self):
        p=bar(0,10,11,9,10)
        up=bar_relations(bar(1,11,12,10,11.5),p)
        self.assertTrue(up['sunrise'] and up['extending_head'] and up['lifting_foot'])
        down=bar_relations(bar(1,9,10,8,8.5),p)
        self.assertTrue(down['sunset'] and down['shrinking_head'] and down['falling_tail'])
        equal=bar_relations(replace(p,timestamp=p.timestamp+timedelta(days=1)),p)
        self.assertTrue(equal['equal_high'] and equal['equal_low'])
        self.assertFalse(equal['sunrise'] or equal['sunset'] or equal['inside'] or equal['outside'])

    def test_inside_outside_not_assumed_intraday_path(self):
        p=bar(0,10,12,8,10)
        for b,kind in ((bar(1,10,11,9,10),'inside'),(bar(1,10,13,7,10),'outside')):
            r=bar_relations(b,p)
            self.assertTrue(r[kind] and r['requires_lower_timeframe'])

    def test_real_and_virtual_necklines_are_distinct(self):
        p=bar(0,10,11,9,10.5)
        r=n_break_evidence(bar(1,11,12.2,10.8,11.5),p,direction='UP',neckline_close=11,neckline_extreme=12)
        self.assertTrue(r['completed'])  # close still below old high, as slide 5 permits
        self.assertEqual(r['defense'],10.5)
        r=n_break_evidence(bar(1,11,12.2,10.8,11),p,direction='UP',neckline_close=11,neckline_extreme=12)
        self.assertTrue(r['virtual_break'])
        self.assertFalse(r['real_break'])
        self.assertIsNone(r['defense'])

    def test_inverse_n_symmetry(self):
        p=bar(0,10,11,9,10.5)
        b=bar(1,11,12.2,10.8,11.5)
        r=n_break_evidence(mirror(b),mirror(p),direction='DOWN',neckline_close=19,neckline_extreme=18)
        self.assertTrue(r['completed'])
        self.assertEqual(r['defense'],19.5)

    def test_three_resistance_shapes(self):
        p=bar(0,10,11,9,10)
        for b,flag in ((bar(1,10.5,10.6,9.4,9.5),'opposing_body'),
                       (bar(1,9.5,11,9.5,10.9),'opposing_open'),
                       (bar(1,10,12,10,10.1),'long_shadow')):
            r=resistance_evidence(b,p,direction='UP')
            self.assertTrue(r['resistance'] and r[flag])

    def test_all_six_states_and_confirmation_timestamp(self):
        p=bar(0,10,10.5,9.5,10)
        attack=bar(1,10,12,10,11.8)
        scenarios=[('强轧空','强追杀',bar(2,12,13,12,12.8),bar(3,13,14,13,13.8)),
                   ('轧空','追杀',bar(2,11.9,12,10.8,11.4),bar(3,11.5,13,11.4,12.8)),
                   ('盘坚','盘跌',bar(2,11.9,12,9.7,10.5),bar(3,11,13,10.7,12.8))]
        for up,down,response,confirmation in scenarios:
            r=three_bar_state(p,attack,response,confirmation,direction='UP',wave_boundary=8)
            self.assertEqual(r['state'],up)
            self.assertEqual(r['observed_at'],confirmation.timestamp.isoformat())
            r=three_bar_state(*map(mirror,(p,attack,response,confirmation)),direction='DOWN',wave_boundary=22)
            self.assertEqual(r['state'],down)

    def test_no_continuation_or_broken_wave_not_forced_into_six_states(self):
        p,a,r,c=bar(0,10,10.5,9.5,10),bar(1,10,12,10,11.8),bar(2,11.9,12,10.8,11.4),bar(3,11,12,10.7,11.8)
        self.assertEqual(three_bar_state(p,a,r,c,direction='UP',wave_boundary=8)['state'],'未确认')
        r=replace(r,low=7.9)
        self.assertEqual(three_bar_state(p,a,r,c,direction='UP',wave_boundary=8)['state'],'结构失效')

    def test_equal_wave_one_p_two_t_distinct_anchors(self):
        r=measured_targets(8,12,10,box_anchor=12)
        self.assertEqual((r['equal_wave'],r['one_p'],r['two_t']),(14,16,20))
        self.assertIsNone(measured_targets(8,12,10)['one_p'])
        self.assertEqual(measured_targets(8,12,10,box_anchor=12.5)['one_p'],17)

    def test_nonpositive_bear_target_not_a_bottom_prediction(self):
        r=measured_targets(20,12,16,direction='DOWN',box_anchor=12)
        self.assertEqual((r['equal_wave'],r['one_p']),(8,4))
        self.assertIsNone(r['two_t'])
        self.assertFalse(r['targets_positive'])

    def test_force_boundaries_and_gap_not_hidden(self):
        r=force_profile(6,4)
        self.assertEqual(r['exact_boundary'],'4/6')
        self.assertFalse(r['above_two_thirds'] or r['below_two_thirds'])
        self.assertEqual(force_profile(10,4)['sixth_band'],3)
        self.assertEqual(force_profile(10,11)['sixth_band'],'>=100%')
        self.assertEqual(force_profile(10,0)['ratio'],0)

    def test_confirmed_pivots_only_and_anchor_context(self):
        points=[SwingPoint(0,2,'L',8),SwingPoint(3,5,'H',12),
                SwingPoint(6,8,'L',10),SwingPoint(9,11,'H',14)]
        self.assertEqual(swing_context(points,8),swing_context(points[:3],8))
        self.assertEqual(swing_context(points,8)['trend'],'unknown')
        r=swing_context(points,11)
        self.assertEqual(r['trend'],'bull')
        self.assertEqual(r['last_fall_high'],12)
        self.assertEqual(r['last_rise_low'],10)

    def test_turning_requires_minor_line_evidence(self):
        r=turning_evidence(.8,.4,direction='UP',minor_line_broken=None)
        self.assertTrue(r['suspicion'] and r['ratio_combination'])
        self.assertFalse(r['confirmed'])
        self.assertTrue(turning_evidence(.8,.4,direction='DOWN',minor_line_broken=True)['confirmed'])
        self.assertFalse(turning_evidence(2/3,.4,direction='UP',minor_line_broken=True)['confirmed'])

    def test_invalid_inputs_fail(self):
        with self.assertRaises(ValueError): force_profile(0,1)
        with self.assertRaises(ValueError): measured_targets(8,12,13)
        with self.assertRaises(ValueError): swing_context([SwingPoint(2,1,'L',8)],3)
        with self.assertRaises(ValueError): bar_relations(bar(0,10,11,9,10),bar(1,10,11,9,10))

    def test_audit_artifacts_and_prefix_invariance(self):
        bars=[bar(0,10,11,9,10),bar(1,11,12,10,11.5),bar(2,12,13,11,12.5)]
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            rows=[dict(timestamp=b.timestamp.isoformat(),symbol=b.symbol,open=b.open,high=b.high,low=b.low,close=b.close,volume=b.volume) for b in bars]
            p=root/'input.csv'
            write_rows(p,rows,list(rows[0]))
            r=audit_foundations(p,root/'out')
            self.assertEqual(r['annotated_rows'],2)
            self.assertFalse(r['production_ready'])
            self.assertEqual(r['counts']['sunrise'],2)
            self.assertTrue((root/'out'/'symbol_001.csv').is_file())
            full=[bar_relations(b,a) for a,b in zip(bars,bars[1:])]
            prefix=[bar_relations(b,a) for a,b in zip(bars[:2],bars[1:2])]
            self.assertEqual(full[:1],prefix)


if __name__=='__main__':
    unittest.main()
