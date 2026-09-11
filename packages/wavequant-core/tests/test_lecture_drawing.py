from datetime import datetime
import unittest
from wavequant.model import Bar
from wavequant.lecture_drawing import lecture_drawing


def bars(rows):
    return [Bar(datetime(2026,1,i+1),'TEST',o,h,l,c,1000) for i,(o,h,l,c) in enumerate(rows)]


class LectureDrawingTests(unittest.TestCase):
    def test_ordinary_extends_high_low_not_close(self):
        bs=bars([(10,12,9,11),(12,14,11,13),(11,12,10,11),(12,13,11,12)])
        drawing=lecture_drawing(bs);points=drawing['strokes'][0]['points']
        self.assertEqual([p['value'] for p in points],[9,14,10,13])
        self.assertEqual(points[-1]['state'],'developing')
        self.assertNotEqual(points[-1]['value'],bs[-1].close)
        self.assertEqual([p['value'] for p in points if p['state']=='confirmed'],[14,10])

    def test_downward_endpoint_is_low(self):
        drawing=lecture_drawing(bars([(12,13,10,11),(10,11,8,9)]))
        self.assertEqual(drawing['strokes'][0]['points'][-1]['value'],8)

    def test_rising_run_keeps_only_origin_and_highest_even_with_bearish_bodies(self):
        bs=bars([(11,12,9,10),(13,14,10,11),(15,16,11,12),(17,18,12,13)])
        for end in range(2,len(bs)+1):
            points=lecture_drawing(bs[:end])['strokes'][0]['points']
            self.assertEqual([(p['kind'],p['value']) for p in points],[('L',9),('H',bs[end-1].high)])
            self.assertEqual(points[-1]['index'],end-1)

    def test_falling_run_keeps_only_origin_and_lowest_even_with_bullish_bodies(self):
        bs=bars([(11,15,10,14),(9,14,8,13),(7,13,6,12),(5,12,4,11)])
        for end in range(2,len(bs)+1):
            points=lecture_drawing(bs[:end])['strokes'][0]['points']
            self.assertEqual([(p['kind'],p['value']) for p in points],[('H',15),('L',bs[end-1].low)])
            self.assertEqual(points[-1]['index'],end-1)

    def test_equal_low_allows_rising_and_equal_high_allows_falling(self):
        for rows,expected in [([(10,12,9,11),(11,14,9,12),(12,16,9,13)],[9,16]),
                              ([(12,15,10,11),(11,15,8,10),(10,15,6,9)],[15,6])]:
            points=lecture_drawing(bars(rows))['strokes'][0]['points']
            self.assertEqual([p['value'] for p in points],expected)

    def test_single_or_equal_range_bars_do_not_invent_a_direction(self):
        bs=bars([(10,12,9,11),(11,12,9,10)])
        self.assertEqual(lecture_drawing(bs[:1])['strokes'],[])
        self.assertEqual(lecture_drawing(bs)['strokes'],[])

    def test_turns_alternate_and_continuation_does_not_add_vertices(self):
        bs=bars([(10,12,9,11),(12,14,10,13),(11,13,8,12),
                 (10,12,6,11),(8,13,7,12),(10,15,8,14)])
        points=lecture_drawing(bs)['strokes'][0]['points']
        self.assertEqual([(p['kind'],p['value']) for p in points],[('L',9),('H',14),('L',6),('H',15)])
        self.assertEqual([p['index'] for p in points],[0,1,3,5])

    def test_four_explicit_child_mother_cases_and_same_bar_order(self):
        for child_up in (True,False):
            for mother_up in (True,False):
                with self.subTest(child_up=child_up,mother_up=mother_up):
                    child=(10.2,11,10,10.8) if child_up else (10.8,11,10,10.2)
                    mother=(9.5,12,9,11.5) if mother_up else (11.5,12,9,9.5)
                    drawing=lecture_drawing(bars([child,mother]))
                    path=drawing['teaching_paths'][0]['points']
                    self.assertEqual([p['value'] for p in path],[11 if child_up else 10]+([9,12] if mother_up else [12,9]))
                    self.assertEqual([p['index'] for p in path],[0,1,1])
                    self.assertEqual([p['ordinal'] for p in path],[0,0,1])
                    self.assertTrue(all(p['available_at']=='2026-01-02' for p in path))
                    self.assertEqual(len(drawing['strokes']),1)
                    self.assertEqual([p['value'] for p in drawing['strokes'][0]['points']], [p['value'] for p in path])
                    self.assertEqual(drawing['strokes'][0]['teaching_path_ids'],['child-mother-1'])

    def test_missing_inside_and_doji_rules_are_not_invented(self):
        inside=lecture_drawing(bars([(9.5,12,9,11.5),(10,11,9.5,10.5)]))
        self.assertEqual(len(inside['issues']),1)
        self.assertEqual(inside['teaching_paths'],[])
        doji=lecture_drawing(bars([(10,11,9,10),(9,12,8,11)]))
        self.assertEqual(len(doji['issues']),1)
        self.assertEqual(doji['teaching_paths'],[])

    def test_child_mother_path_not_available_before_mother(self):
        bs=bars([(10.2,11,10,10.8),(9.5,12,9,11.5)])
        self.assertEqual(lecture_drawing(bs[:1])['teaching_paths'],[])
        self.assertEqual(len(lecture_drawing(bs)['teaching_paths']),1)

    def test_all_four_cases_join_previous_leg_and_extend_without_restart(self):
        for child_up in (True,False):
            for mother_up in (True,False):
                with self.subTest(child_up=child_up,mother_up=mother_up):
                    before=(8.5,10,8,9.5)
                    child=(10.2,11,10,10.8) if child_up else (10.8,11,10,10.2)
                    mother=(9.5,12,9,11.5) if mother_up else (11.5,12,9,9.5)
                    after=[(11,13,10,12),(12,14,11,13)] if mother_up else [(8.5,11,8,10),(7.5,10,7,9)]
                    bs=bars([before,child,mother]+after)
                    at_mother=lecture_drawing(bs[:3])
                    self.assertEqual(len(at_mother['strokes']),1)
                    path=at_mother['strokes'][0]['points']
                    self.assertEqual([p['index'] for p in path],[0,1,2,2])
                    self.assertEqual([p['value'] for p in path], [8,11 if child_up else 10]+([9,12] if mother_up else [12,9]))
                    final=lecture_drawing(bs)
                    self.assertEqual(len(final['strokes']),1)
                    points=final['strokes'][0]['points']
                    self.assertEqual([p['index'] for p in points],[0,1,2,4])
                    self.assertEqual(points[-1]['value'],14 if mother_up else 7)
                    self.assertEqual(points[:-1],path[:-1])
                    self.assertTrue(points[-1]['teaching_extended'])
                    self.assertEqual(final['teaching_paths'],at_mother['teaching_paths'])

    def test_mother_followed_by_reversal_uses_same_endpoint(self):
        bs=bars([(8.5,10,8,9.5),(10.2,11,10,10.8),(9.5,12,9,11.5),(8.5,11,8,10)])
        points=lecture_drawing(bs)['strokes'][0]['points']
        self.assertEqual([(p['index'],p['value']) for p in points],[(0,8),(1,11),(2,9),(2,12),(3,8)])
        self.assertEqual(points[-2]['state'],'confirmed')

    def test_successive_mothers_share_one_vertex_not_overlapping_paths(self):
        bs=bars([(10.2,11,10,10.8),(9.5,12,9,11.5),(8.5,13,8,12.5)])
        drawing=lecture_drawing(bs)
        self.assertEqual(len(drawing['strokes']),1)
        self.assertEqual(len(drawing['teaching_paths']),2)
        points=drawing['strokes'][0]['points']
        self.assertEqual([p['value'] for p in points],[11,9,12,8,13])
        self.assertEqual([(p['index'],p['ordinal']) for p in points],[(0,0),(1,0),(1,1),(2,0),(2,1)])

    def test_known_direction_inside_connects_one_extreme_without_restart(self):
        bs=bars([(10.2,11,10,10.8),(9.5,12,9,11.5),(10,11,10,10.5),(11,13,11,12)])
        drawing=lecture_drawing(bs)
        self.assertEqual(len(drawing['strokes']),1)
        self.assertFalse(drawing['incomplete'])
        self.assertTrue(drawing['intrabar_incomplete'])
        self.assertEqual([p['value'] for p in drawing['strokes'][0]['points']],[11,9,12,10,13])
        self.assertEqual(drawing['inside_connections'][0]['rule'],'上涨缩头：原高点连子低')

    def test_downtrend_inside_connects_previous_low_to_child_high(self):
        bs=bars([(12,14,10,13),(10,12,8,9),(10,11,9,10.5),(10,13,10,12)])
        drawing=lecture_drawing(bs)
        self.assertEqual(len(drawing['strokes']),1)
        self.assertEqual([p['value'] for p in drawing['strokes'][0]['points']],[14,8,13])
        self.assertEqual(drawing['inside_connections'][0]['rule'],'下跌缩脚：原低点连子高')

    def test_nested_inside_bars_keep_one_path_and_do_not_invent_intrabar_turns(self):
        bs=bars([(9,11,8,10),(10,14,9,13),(11,13,10,12),(11,12,11,11.5),(10,11.5,9,10)])
        drawing=lecture_drawing(bs)
        self.assertEqual(len(drawing['strokes']),1)
        self.assertEqual([p['value'] for p in drawing['strokes'][0]['points']],[8,14,10,12,9])
        self.assertEqual(len(drawing['inside_connections']),2)
        self.assertEqual([p['index'] for p in drawing['strokes'][0]['points']],[0,1,2,3,4])

    def test_inside_drawing_does_not_relax_strict_strategy_polyline(self):
        from wavequant.polyline import observe_polyline
        from wavequant.price_action import Direction
        bs=bars([(9,11,8,10),(10,14,9,13),(11,13,10,12)])
        self.assertEqual(len(lecture_drawing(bs)['strokes']),1)
        strict=observe_polyline(bs,symbol='TEST',timeframe='1d',initial_direction=Direction.UP,start_index=0)
        self.assertEqual(strict.blocked_at,2)

    def test_confirmed_turn_remains_unchanged_when_tail_extends(self):
        bs=bars([(10,12,9,11),(12,14,11,13),(11,12,10,11),(10,11,8,9)])
        a=lecture_drawing(bs[:3])['strokes'][0]['points'];b=lecture_drawing(bs)['strokes'][0]['points']
        self.assertEqual([p for p in a if p['state']=='confirmed'],[p for p in b if p['state']=='confirmed'])
        self.assertEqual(b[-1]['value'],8)


if __name__=='__main__':unittest.main()
