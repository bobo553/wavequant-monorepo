from datetime import datetime
import unittest
from zoneinfo import ZoneInfo
from wavequant.domain.models.model import Bar
from wavequant.domain.market_structure.lecture_drawing import lecture_drawing


def bars(rows):
    return [Bar(datetime(2026,1,i+1),'TEST',o,h,l,c,1000) for i,(o,h,l,c) in enumerate(rows)]


class LectureDrawingTests(unittest.TestCase):
    def test_containment_pairs_follow_colour_order_and_merge_monotone_edges(self):
        cases = (
            (True, True, [('L', 9), ('H', 12), ('L', 10), ('H', 11)],
             [('L', 10), ('H', 11), ('L', 9), ('H', 12)]),
            (True, False, [('L', 9), ('H', 12), ('L', 10)],
             [('H', 11), ('L', 9), ('H', 12)]),
            (False, False, [('H', 12), ('L', 9), ('H', 11), ('L', 10)],
             [('H', 11), ('L', 10), ('H', 12), ('L', 9)]),
            (False, True, [('H', 12), ('L', 9), ('H', 11)],
             [('L', 10), ('H', 12), ('L', 9)]),
        )
        for mother_up, child_up, mother_child, child_mother in cases:
            mother = (9.5, 12, 9, 11.5) if mother_up else (11.5, 12, 9, 9.5)
            child = (10.2, 11, 10, 10.8) if child_up else (10.8, 11, 10, 10.2)
            for rows, expected in (([mother, child], mother_child),
                                   ([child, mother], child_mother)):
                with self.subTest(mother_up=mother_up, child_up=child_up, rows=rows):
                    drawing = lecture_drawing(bars(rows))
                    self.assertEqual(drawing['issues'], [])
                    self.assertEqual(len(drawing['strokes']), 1)
                    self.assertEqual([(point['kind'], point['value'])
                                      for point in drawing['strokes'][0]['points']], expected)

    def test_equal_boundary_child_mother_keeps_mother_extreme_in_all_colours(self):
        shanghai = ZoneInfo('Asia/Shanghai')
        cases = (
            (12, 10, True, True, [(0, 'L', 10), (0, 'H', 12), (1, 'L', 9), (1, 'H', 12)]),
            (12, 10, True, False, [(0, 'L', 10), (1, 'H', 12), (1, 'L', 9)]),
            (12, 10, False, False, [(0, 'H', 12), (0, 'L', 10), (1, 'H', 12), (1, 'L', 9)]),
            (12, 10, False, True, [(0, 'H', 12), (1, 'L', 9), (1, 'H', 12)]),
            (11, 9, True, True, [(0, 'L', 9), (0, 'H', 11), (1, 'L', 9), (1, 'H', 12)]),
            (11, 9, True, False, [(0, 'L', 9), (1, 'H', 12), (1, 'L', 9)]),
            (11, 9, False, False, [(0, 'H', 11), (0, 'L', 9), (1, 'H', 12), (1, 'L', 9)]),
            (11, 9, False, True, [(0, 'H', 11), (1, 'L', 9), (1, 'H', 12)]),
        )
        for child_high, child_low, child_up, mother_up, expected in cases:
            with self.subTest(child_high=child_high, child_low=child_low,
                              child_up=child_up, mother_up=mother_up):
                def bar(day, up, high, low):
                    opened, closed = (low + .2, high - .2) if up else (high - .2, low + .2)
                    return Bar(datetime(2025, 11, day, tzinfo=shanghai), 'TEST',
                               opened, high, low, closed, 1000)

                drawing = lecture_drawing([bar(13, child_up, child_high, child_low),
                                           bar(14, mother_up, 12, 9)])
                self.assertEqual(drawing['issues'], [])
                self.assertEqual(drawing['inside_connections'], [])
                self.assertEqual(len(drawing['teaching_paths']), 1)
                self.assertEqual([(p['index'], p['kind'], p['value']) for p in
                                  drawing['teaching_paths'][0]['points']], expected)
                self.assertTrue(all(p['available_at'] == '2025-11-14' for p in
                                    drawing['teaching_paths'][0]['points']))
                self.assertEqual([(p['index'], p['kind'], p['value']) for p in
                                  drawing['strokes'][0]['points']], expected)

    def test_equal_boundary_mother_child_keeps_all_eight_colour_paths(self):
        shanghai = ZoneInfo('Asia/Shanghai')
        cases = (
            (12, 10, True, True, [(0, 'L', 9), (0, 'H', 12), (1, 'L', 10), (1, 'H', 12)]),
            (12, 10, True, False, [(0, 'L', 9), (0, 'H', 12), (1, 'L', 10)]),
            (12, 10, False, False, [(0, 'H', 12), (0, 'L', 9), (1, 'H', 12), (1, 'L', 10)]),
            (12, 10, False, True, [(0, 'H', 12), (0, 'L', 9), (1, 'H', 12)]),
            (11, 9, True, True, [(0, 'L', 9), (0, 'H', 12), (1, 'L', 9), (1, 'H', 11)]),
            (11, 9, True, False, [(0, 'L', 9), (0, 'H', 12), (1, 'L', 9)]),
            (11, 9, False, False, [(0, 'H', 12), (0, 'L', 9), (1, 'H', 11), (1, 'L', 9)]),
            (11, 9, False, True, [(0, 'H', 12), (0, 'L', 9), (1, 'H', 11)]),
        )
        for child_high, child_low, mother_up, child_up, expected in cases:
            with self.subTest(child_high=child_high, child_low=child_low,
                              mother_up=mother_up, child_up=child_up):
                def bar(day, up, high, low):
                    opened, closed = (low + .2, high - .2) if up else (high - .2, low + .2)
                    return Bar(datetime(2025, 11, day, tzinfo=shanghai), 'TEST',
                               opened, high, low, closed, 1000)

                drawing = lecture_drawing([bar(13, mother_up, 12, 9),
                                           bar(14, child_up, child_high, child_low)])
                self.assertEqual(drawing['issues'], [])
                self.assertEqual(len(drawing['inside_connections']), 1)
                self.assertEqual([(p['index'], p['kind'], p['value']) for p in
                                  drawing['strokes'][0]['points']], expected)

    def test_equal_high_child_mother_reuses_live_endpoint_at_mother(self):
        data = bars([(9.5, 11, 9, 10.5), (10.2, 12, 10, 11.8),
                     (11.8, 12, 8, 8.2)])
        before = lecture_drawing(data[:2])['strokes'][0]['points']
        self.assertEqual([(p['index'], p['kind'], p['value']) for p in before],
                         [(0, 'L', 9), (1, 'H', 12)])
        points = lecture_drawing(data)['strokes'][0]['points']
        self.assertEqual([(p['index'], p['kind'], p['value']) for p in points],
                         [(0, 'L', 9), (2, 'H', 12), (2, 'L', 8)])
        self.assertEqual(points[1]['available_at'], '2026-01-03')

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

    def test_equal_boundary_child_mother_run_keeps_each_candle_turn(self):
        for rows,expected in [([(10,12,9,11),(11,14,9,12),(12,16,9,13)],[9,12,9,14,9,16]),
                              ([(12,15,10,11),(11,15,8,10),(10,15,6,9)],[15,10,15,8,15,6])]:
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
        expected_cases = {
            (True, True): [10, 11, 9, 12],
            (True, False): [10, 12, 9],
            (False, True): [11, 9, 12],
            (False, False): [11, 10, 12, 9],
        }
        for child_up in (True,False):
            for mother_up in (True,False):
                with self.subTest(child_up=child_up,mother_up=mother_up):
                    child=(10.2,11,10,10.8) if child_up else (10.8,11,10,10.2)
                    mother=(9.5,12,9,11.5) if mother_up else (11.5,12,9,9.5)
                    drawing=lecture_drawing(bars([child,mother]))
                    path=drawing['teaching_paths'][0]['points']
                    self.assertEqual([p['value'] for p in path], expected_cases[(child_up, mother_up)])
                    self.assertEqual([p['index'] for p in path],
                                     [0, 0, 1, 1] if child_up == mother_up else [0, 1, 1])
                    self.assertEqual([p['ordinal'] for p in path],
                                     [0, 1, 0, 1] if child_up == mother_up else [0, 0, 1])
                    self.assertTrue(all(p['available_at']=='2026-01-02' for p in path))
                    self.assertEqual(len(drawing['strokes']),1)
                    self.assertEqual([p['value'] for p in drawing['strokes'][0]['points']], [p['value'] for p in path])
                    self.assertEqual(drawing['strokes'][0]['teaching_path_ids'],['child-mother-1'])

    def test_initial_mother_child_is_defined_but_doji_remains_unresolved(self):
        inside=lecture_drawing(bars([(9.5,12,9,11.5),(10,11,9.5,10.5)]))
        self.assertEqual(inside['issues'],[])
        self.assertEqual([p['value'] for p in inside['strokes'][0]['points']],[9,12,9.5,11])
        self.assertEqual(inside['teaching_paths'],[])
        doji=lecture_drawing(bars([(10,11,9,10),(9,12,8,11)]))
        self.assertEqual(len(doji['issues']),1)
        self.assertEqual(doji['teaching_paths'],[])
        initial_inside_doji=lecture_drawing(bars([(9.5,12,9,11.5),(10.5,11,10,10.5)]))
        self.assertEqual(len(initial_inside_doji['issues']),1)

    def test_xianfeng_equal_high_bullish_mother_child_keeps_both_turns(self):
        shanghai = ZoneInfo('Asia/Shanghai')
        data = [
            Bar(datetime(2025, 11, 12, tzinfo=shanghai), 'sz.300163',
                4.82, 5.06, 4.82, 4.86, 61887300),
            Bar(datetime(2025, 11, 13, tzinfo=shanghai), 'sz.300163',
                4.84, 5.03, 4.70, 5.00, 72400000),
            Bar(datetime(2025, 11, 14, tzinfo=shanghai), 'sz.300163',
                4.91, 5.03, 4.91, 4.96, 45350900),
        ]
        drawing = lecture_drawing(data)
        points = drawing['strokes'][0]['points']
        self.assertEqual([(p['kind'], p['value']) for p in
                          lecture_drawing(data[:2])['strokes'][0]['points']],
                         [('H', 5.06), ('L', 4.70)])
        self.assertEqual([(p['index'], p['kind'], p['value']) for p in points],
                         [(0, 'H', 5.06), (1, 'L', 4.70), (1, 'H', 5.03),
                          (2, 'L', 4.91), (2, 'H', 5.03)])
        self.assertEqual(points[2]['available_at'], '2025-11-14')
        self.assertEqual(drawing['strokes'][0]['teaching_path_ids'], ['mother-child-2'])
        self.assertEqual(drawing['inside_connections'][0]['time'], '2025-11-14')

    def test_inside_doji_with_known_direction_keeps_single_extreme_and_history(self):
        drawing=lecture_drawing(bars([(10,11,9,10.5),(10.5,12,10,11.5),(10.5,11,10.5,10.5)]))
        self.assertEqual(drawing['issues'],[])
        self.assertEqual([p['value'] for p in drawing['strokes'][0]['points']],[9,12,10.5])
        self.assertFalse(drawing['strokes'][0]['points'][-1]['intrabar_order_resolved'])
        self.assertEqual(drawing['inside_connections'][0]['source'],
                         'known_direction_single_extreme_for_inside_doji')

    def test_guofang_equal_high_inside_doji_connects_child_low(self):
        data = [
            Bar(datetime(2024, 9, 19), 'sh.601086',
                4.836005140153086, 5.027259580724112, 4.836005140153086, 5.013598549254753, 7381500),
            Bar(datetime(2024, 9, 20), 'sh.601086',
                5.040920612193471, 5.040920612193471, 4.945293391907958, 5.013598549254753, 4120300),
            Bar(datetime(2024, 9, 23), 'sh.601086',
                4.986276486316035, 5.040920612193471, 4.958954423377317, 4.986276486316035, 4303900),
            Bar(datetime(2024, 9, 24), 'sh.601086',
                5.013598549254753, 5.19119195835642, 5.013598549254753, 5.177530926887061, 7713300),
        ]
        on_doji = lecture_drawing(data[:3])
        self.assertEqual(on_doji['issues'], [])
        self.assertEqual([(point['time'], point['kind'], point['value'])
                          for point in on_doji['strokes'][0]['points']], [
                              ('2024-09-19', 'L', data[0].low),
                              ('2024-09-20', 'H', data[1].high),
                              ('2024-09-23', 'L', data[2].low),
                          ])
        self.assertFalse(on_doji['strokes'][0]['points'][-1]['intrabar_order_resolved'])
        after_rebound = lecture_drawing(data)
        self.assertEqual([(point['time'], point['kind'])
                          for point in after_rebound['strokes'][0]['points']], [
                              ('2024-09-19', 'L'), ('2024-09-20', 'H'),
                              ('2024-09-23', 'L'), ('2024-09-24', 'H'),
                          ])
        self.assertEqual(after_rebound['strokes'][0]['points'][-2]['state'], 'confirmed')

    def test_guofang_doji_child_connects_to_bearish_outside_mother(self):
        data = [
            Bar(datetime(2024, 9, 10), 'sh.601086',
                5.177530926887061, 5.19119195835642, 5.040920612193471, 5.095564738070907, 10546477),
            Bar(datetime(2024, 9, 11), 'sh.601086',
                5.027259580724112, 5.068242675132189, 4.9316323604385985, 4.986276486316035, 7790800),
            Bar(datetime(2024, 9, 12), 'sh.601086',
                4.986276486316035, 5.040920612193471, 4.945293391907958, 4.986276486316035, 6627200),
            Bar(datetime(2024, 9, 13), 'sh.601086',
                4.945293391907958, 5.05458164366283, 4.863327203091804, 4.876988234561162, 8388993),
            Bar(datetime(2024, 9, 18), 'sh.601086',
                4.876988234561162, 4.890649266030522, 4.754038951336931, 4.822344108683726, 6167300),
        ]
        on_mother = lecture_drawing(data[:4])
        self.assertEqual(on_mother['issues'], [])
        self.assertEqual(len(on_mother['strokes']), 1)
        self.assertEqual([(point['time'], point['kind'], point['value'])
                          for point in on_mother['strokes'][0]['points']], [
                              ('2024-09-10', 'H', data[0].high),
                              ('2024-09-11', 'L', data[1].low),
                              ('2024-09-13', 'H', data[3].high),
                              ('2024-09-13', 'L', data[3].low),
                          ])
        self.assertEqual(on_mother['teaching_paths'][0]['source'],
                         'known_child_endpoint_to_coloured_mother')
        self.assertFalse(on_mother['strokes'][0]['points'][-1]['intrabar_order_resolved'])
        after_decline = lecture_drawing(data)
        self.assertEqual(len(after_decline['strokes']), 1)
        self.assertEqual([(point['time'], point['kind'])
                          for point in after_decline['strokes'][0]['points'][-2:]],
                         [('2024-09-13', 'H'), ('2024-09-18', 'L')])

    def test_outside_doji_mother_keeps_unresolved_order(self):
        drawing = lecture_drawing(bars([(9,11,8,10),(10,12,9,11),(10,13,8,10)]))
        self.assertEqual(len(drawing['issues']), 1)
        self.assertEqual(drawing['teaching_paths'], [])

    def test_known_doji_child_connects_to_bullish_outside_mother(self):
        drawing = lecture_drawing(bars([(12,14,10,13),(10,12,8,9),
                                        (10,11,9,10),(8,12,7,11)]))
        self.assertEqual(drawing['issues'], [])
        self.assertEqual([(point['kind'], point['value'])
                          for point in drawing['strokes'][0]['points']],
                         [('H', 14), ('L', 8), ('H', 11), ('L', 7), ('H', 12)])

    def test_equal_low_inside_doji_connects_child_high_on_decline(self):
        drawing = lecture_drawing(bars([(12,14,10,13),(10,12,8,9),(11,11,8,11)]))
        self.assertEqual(drawing['issues'], [])
        self.assertEqual([(point['kind'], point['value'])
                          for point in drawing['strokes'][0]['points']],
                         [('H', 14), ('L', 8), ('H', 11)])

    def test_child_mother_path_not_available_before_mother(self):
        bs=bars([(10.2,11,10,10.8),(9.5,12,9,11.5)])
        self.assertEqual(lecture_drawing(bs[:1])['teaching_paths'],[])
        self.assertEqual(len(lecture_drawing(bs)['teaching_paths']),1)

    def test_all_four_cases_join_previous_leg_and_extend_without_restart(self):
        expected_at_mother = {
            (True, True): [(0,8),(1,11),(2,9),(2,12)],
            (True, False): [(0,8),(2,12),(2,9)],
            (False, True): [(0,8),(1,11),(2,9),(2,12)],
            (False, False): [(0,8),(1,11),(1,10),(2,12),(2,9)],
        }
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
                    self.assertEqual([(p['index'],p['value']) for p in path],
                                     expected_at_mother[(child_up,mother_up)])
                    final=lecture_drawing(bs)
                    self.assertEqual(len(final['strokes']),1)
                    points=final['strokes'][0]['points']
                    self.assertEqual([p['index'] for p in points],
                                     [index for index,_ in expected_at_mother[(child_up,mother_up)][:-1]]+[4])
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
        self.assertEqual([p['value'] for p in points],[10,11,9,12,8,13])
        self.assertEqual([(p['index'],p['ordinal']) for p in points],
                         [(0,0),(0,1),(1,0),(1,1),(2,0),(2,1)])

    def test_known_direction_inside_keeps_reversal_and_later_extension(self):
        bs=bars([(10.2,11,10,10.8),(9.5,12,9,11.5),(10,11,10,10.5),(11,13,11,12)])
        drawing=lecture_drawing(bs)
        self.assertEqual(len(drawing['strokes']),1)
        self.assertFalse(drawing['incomplete'])
        self.assertTrue(drawing['intrabar_incomplete'])
        self.assertEqual([p['value'] for p in drawing['strokes'][0]['points']],[10,11,9,12,10,13])
        self.assertEqual(drawing['inside_connections'][0]['rule'],'母子阴阳路径：相邻同向线段合并')

    def test_downtrend_inside_connects_previous_low_to_child_high(self):
        bs=bars([(12,14,10,13),(10,12,8,9),(10,11,9,10.5),(10,13,10,12)])
        drawing=lecture_drawing(bs)
        self.assertEqual(len(drawing['strokes']),1)
        self.assertEqual([p['value'] for p in drawing['strokes'][0]['points']],[14,8,13])
        self.assertEqual(drawing['inside_connections'][0]['rule'],'母子阴阳路径：相邻同向线段合并')

    def test_nested_inside_bars_follow_colour_order_in_one_path(self):
        bs=bars([(9,11,8,10),(10,14,9,13),(11,13,10,12),(11,12,11,11.5),(10,11.5,9,10)])
        drawing=lecture_drawing(bs)
        self.assertEqual(len(drawing['strokes']),1)
        self.assertEqual([p['value'] for p in drawing['strokes'][0]['points']],[8,14,10,13,11,12,9])
        self.assertEqual(len(drawing['inside_connections']),2)
        self.assertEqual([p['index'] for p in drawing['strokes'][0]['points']],[0,1,2,2,3,3,4])

    def test_inside_drawing_does_not_relax_strict_strategy_polyline(self):
        from wavequant.domain.market_structure.polyline import observe_polyline
        from wavequant.domain.market_structure.price_action import Direction
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
