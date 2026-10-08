import copy
from datetime import datetime, timedelta
import unittest

from wavequant.domain.models.model import Bar
from wavequant.domain.market_structure.secondary_trend import (
    _level2_high_promotion,
    _structural_reversals,
    last_fall_high_reanchors,
    secondary_trends,
)


def fixture(values, first='L'):
    bars=[Bar(datetime(2026,1,1)+timedelta(days=i),'TEST',50,100,1,50,100) for i in range(len(values)+1)]
    points=[]
    for i,value in enumerate(values):
        kind=first if i%2==0 else ('H' if first=='L' else 'L')
        points.append(dict(index=i,ordinal=0,time=bars[i].timestamp.date().isoformat(),value=value,
                           kind=kind,label=f'{kind}{i}',available_at=bars[i+1].timestamp.date().isoformat(),
                           projection_count=2,projection_rank=1))
    return bars,dict(strokes=[dict(id='level1-test',kind='reversal',points=points)])


class SecondaryTrendTests(unittest.TestCase):
    values=[20,30,10,20,14,26,18,35,28,45,36,42,30,36,24,31,26,38,30,42]

    def test_figure_wave_extremes_not_breakout_points(self):
        bars,level1=fixture(self.values)
        result=secondary_trends(level1,bars)
        points=result['strokes'][0]['points']
        self.assertEqual([(p['kind'],p['value'],p['source_level1_position']) for p in points],
                         [('L',10,2),('H',45,9),('L',24,14)])
        self.assertEqual([p['confirmed_on_level1'] for p in points],[7,14,17])
        self.assertEqual([p['broken_key']['value'] for p in points],[30,28,36])
        self.assertEqual([p['flip'] for p in points],['翻空为多','翻多为空','翻空为多'])
        self.assertEqual(points[0]['available_at'],level1['strokes'][0]['points'][7]['available_at'])
        self.assertGreater(points[0]['available_at'],points[0]['time'])
        self.assertEqual(result['trend_level'],2)
        self.assertEqual(result['source_level'],1)
        self.assertLess(result['confirmed_wave_count'],result['input_turn_count'])
        self.assertEqual(
            [(p['value'],p['confirmed_low']['value'],p['available_at']) for p in result['bear_to_bull_highs']],
            [(35,10,level1['strokes'][0]['points'][7]['available_at']),
             (38,24,level1['strokes'][0]['points'][17]['available_at'])],
        )
        self.assertIn('bear_bull_alternation_lows',result)
        self.assertIn('post_alternation_bull_highs',result)
        self.assertIn('bullish_turn_signals',result)

    def test_prefix_stability_no_future_repainting(self):
        bars,source=fixture(self.values)
        turns=source['strokes'][0]['points']; full=_structural_reversals(turns)
        for end in range(len(turns)+1):
            self.assertEqual(_structural_reversals(turns[:end]),[p for p in full if p['confirmed_on_level1']<end])

    promotion_base=[20,30,10,20,14,26,18,35,28,45,36,42,30,36,24,31,26]

    def test_known_level2_key_break_promotes_source_high_without_a_later_low(self):
        _,source=fixture([*self.promotion_base,50])
        turns=source['strokes'][0]['points']
        turns[-1]['confirmed_by']=[dict(turns[-2])]
        before=copy.deepcopy(turns)

        confirmed=_structural_reversals(turns)

        self.assertEqual([(p['kind'],p['value']) for p in confirmed],
                         [('L',10),('H',45),('L',24),('H',50)])
        promoted=confirmed[-1]
        self.assertEqual(promoted['source_level1_position'],17)
        self.assertEqual(promoted['confirmed_on_level1'],17)
        self.assertEqual(promoted['available_at'],turns[17]['available_at'])
        self.assertEqual(promoted['source_level1_available_at'],turns[17]['available_at'])
        self.assertEqual(promoted['confirmation_rule'],'level1_confirmed_high_breaks_known_level2_last_fall_high')
        self.assertEqual(promoted['flip'],'二级末跌高突破升级')
        self.assertEqual((promoted['broken_key']['kind'],promoted['broken_key']['value']),('H',45))
        self.assertEqual(promoted['broken_key']['available_at'],turns[14]['available_at'])
        self.assertEqual((promoted['confirmed_by']['kind'],promoted['confirmed_by']['value']),('H',50))
        self.assertEqual(promoted['source_confirmation'],turns[17]['confirmed_by'])
        self.assertNotIn('alternation',promoted)
        self.assertNotIn('provisional_reversal',promoted)
        self.assertEqual(turns,before)

    def test_upgrade_survives_missing_deep_and_two_thirds_pullbacks(self):
        for high,tail in [(50,[]),(50,[30]),(48,[32])]:
            with self.subTest(high=high,tail=tail):
                _,source=fixture([*self.promotion_base,high,*tail])
                points=_structural_reversals(source['strokes'][0]['points'])
                self.assertEqual([(p['kind'],p['value']) for p in points],
                                 [('L',10),('H',45),('L',24),('H',high)])

    def test_equal_or_lower_level2_key_is_not_a_direct_upgrade(self):
        for high in [44,45]:
            with self.subTest(high=high):
                _,source=fixture([*self.promotion_base,high])
                self.assertEqual([(p['kind'],p['value']) for p in _structural_reversals(source['strokes'][0]['points'])],
                                 [('L',10),('H',45),('L',24)])

    def test_old_formal_key_must_be_known_before_the_source_high_price_date(self):
        for offset,upgrades in [(-1,True),(0,False),(1,False)]:
            with self.subTest(offset=offset):
                _,source=fixture([*self.promotion_base,50])
                turns=source['strokes'][0]['points']
                known=(datetime.fromisoformat(turns[-1]['time'])+timedelta(days=offset)).date().isoformat()
                for point in turns[14:]:
                    point['available_at']=max(point['available_at'],known)
                turns[-1]['available_at']=(datetime.fromisoformat(turns[-1]['time'])+timedelta(days=3)).date().isoformat()
                points=_structural_reversals(turns)
                self.assertEqual([(p['kind'],p['value']) for p in points],
                                 [('L',10),('H',45),('L',24)]+([('H',50)] if upgrades else []))

    def test_integer_availability_uses_source_price_index_and_formal_key_proof(self):
        for known,upgrades in [(16,True),(17,False),(18,False)]:
            with self.subTest(known=known):
                _,source=fixture([*self.promotion_base,50])
                turns=source['strokes'][0]['points']
                for point in turns:
                    point['available_at']=max(point['index']+1,known if point['index']>=14 else 0)
                turns[-1]['available_at']=20
                points=_structural_reversals(turns)
                self.assertEqual([(p['kind'],p['value']) for p in points],
                                 [('L',10),('H',45),('L',24)]+([('H',50)] if upgrades else []))

    def test_seed_or_developing_source_high_cannot_upgrade(self):
        for state in ['seed','developing']:
            with self.subTest(state=state):
                _,source=fixture([*self.promotion_base,50])
                source['strokes'][0]['points'][-1]['state']=state
                self.assertNotIn(('H',50),[(p['kind'],p['value']) for p in
                                          _structural_reversals(source['strokes'][0]['points'])])

    def test_direct_upgrade_waits_for_causal_next_low_and_continues_reducing(self):
        _,source=fixture([*self.promotion_base,50,36,42,35,40,34,39,33,51])
        turns=source['strokes'][0]['points']
        before=_structural_reversals(turns[:18])
        one_low=_structural_reversals(turns[:19])
        full=_structural_reversals(turns)

        self.assertEqual(before,one_low)
        self.assertEqual([(p['kind'],p['value']) for p in full],
                         [('L',10),('H',45),('L',24),('H',50),('L',33),('H',51)])
        self.assertEqual(full[-2]['available_at'],turns[25]['available_at'])
        self.assertEqual(full[-1]['available_at'],turns[25]['available_at'])
        for end in range(len(turns)+1):
            self.assertEqual(_structural_reversals(turns[:end]),
                             [p for p in full if p['confirmed_on_level1']<end])

    def test_earlier_impulse_high_waits_for_later_formal_low_publication(self):
        for indexed in [False,True]:
            with self.subTest(indexed=indexed):
                _,source=fixture([*self.promotion_base,50,36,42,35,40])
                turns=source['strokes'][0]['points']
                if indexed:
                    for point in turns:
                        point['available_at']=point['index']+1
                formal=_structural_reversals(turns[:17])
                old_key=formal[-1]
                base=dict(turns[14],available_at=turns[21]['available_at'],trend_level=2)
                before=copy.deepcopy(turns)

                for end in range(18,len(turns)+1):
                    upgraded=_level2_high_promotion(turns[:end],base,17,old_key,21)
                    if end<=21:
                        self.assertIsNone(upgraded)
                    else:
                        self.assertIsNotNone(upgraded)
                        assert upgraded is not None
                        self.assertEqual(upgraded['available_at'],base['available_at'])
                        self.assertEqual(upgraded['source_level1_available_at'],turns[17]['available_at'])
                        self.assertEqual(upgraded['confirmed_on_level1'],21)
                        self.assertEqual(upgraded['source_level1_position'],17)
                self.assertEqual(turns,before)

    def test_waiting_low_requires_a_later_coordinate_and_strictly_lower_price(self):
        for index,ordinal,value in [(16,0,36),(17,0,36),(18,0,50),(18,0,51)]:
            with self.subTest(index=index,ordinal=ordinal,value=value):
                _,source=fixture([*self.promotion_base,50,36,51])
                turns=source['strokes'][0]['points']
                turns[18].update(index=index,ordinal=ordinal,value=value)

                points=_structural_reversals(turns)

                self.assertEqual([(p['kind'],p['value']) for p in points],
                                 [('L',10),('H',45),('L',24),('H',50)])

    def test_waiting_low_accepts_a_later_ordinal_on_the_same_price_session(self):
        _,source=fixture([*self.promotion_base,50,36,51])
        turns=source['strokes'][0]['points']
        turns[18].update(index=17,ordinal=1,time=turns[17]['time'])

        points=_structural_reversals(turns)

        self.assertEqual([(p['kind'],p['value']) for p in points],
                         [('L',10),('H',45),('L',24),('H',50),('L',36),('H',51)])
        self.assertEqual((points[-2]['index'],points[-2]['ordinal']),(17,1))

    def test_later_confirmed_high_can_upgrade_after_the_low_was_already_formal(self):
        _,source=fixture([*self.promotion_base,38,30,50])
        turns=source['strokes'][0]['points']

        before=_structural_reversals(turns[:19])
        after=_structural_reversals(turns)

        self.assertEqual([(p['kind'],p['value']) for p in before],[('L',10),('H',45),('L',24)])
        self.assertEqual([(p['kind'],p['value']) for p in after],
                         [('L',10),('H',45),('L',24),('H',50)])
        self.assertEqual(after[-1]['confirmed_on_level1'],19)
        self.assertEqual(after[-1]['broken_key']['value'],45)
        self.assertEqual(after[-1]['available_at'],turns[-1]['available_at'])

    def test_ordinary_key_break_still_confirms_a_later_lower_high(self):
        _,source=fixture([*self.promotion_base,50,36,42,35,40,34,39,33,41,36,43,37,42,32])
        points=_structural_reversals(source['strokes'][0]['points'])

        self.assertEqual([(p['kind'],p['value']) for p in points],
                         [('L',10),('H',45),('L',24),('H',50),('L',33),('H',43)])
        self.assertEqual(points[-1]['confirmation_rule'],'level1_structural_key_break')
        self.assertEqual(points[-1]['broken_key']['value'],36)

    def test_direct_upgrade_does_not_apply_to_level2_source_points(self):
        _,source=fixture([*self.promotion_base,50,36,42,35])
        points=_structural_reversals(source['strokes'][0]['points'],source_level=2)
        self.assertEqual([(p['kind'],p['value']) for p in points],[('L',10),('H',45),('L',24)])
        self.assertTrue(all(p['confirmation_rule']=='level2_structural_key_break' for p in points))

    def test_developing_path_keeps_confirmed_points_immutable_and_exposes_long_tail(self):
        """A wide frozen key must not hide confirmed level-1 development."""
        values=[20,30,10,20,14,26,18,35,28,45,36,42,30,36,24,31,26,38,30,42,
                35,40,34,39,33,41,36,43,37,42]
        bars,source=fixture(values)
        before=copy.deepcopy(source)

        result=secondary_trends(source,bars)
        formal=result['strokes'][0]['points']
        path=result['developing_strokes'][0]['points']

        self.assertEqual(source,before)
        self.assertEqual([(p['kind'],p['value']) for p in formal],[('L',10),('H',45),('L',24)])
        self.assertEqual([(p['kind'],p['value']) for p in path],
                         [('L',24),('H',42),('L',33),('H',41),('L',36),
                          ('H',43),('L',37),('H',42)])
        self.assertEqual([p['source_level1_position'] for p in path],[14,19,24,25,26,27,28,29])
        self.assertEqual([p['development_role'] for p in path],
                         ['formal_start','confirmed_nested_turn','confirmed_nested_turn','pending_evidence',
                          'pending_evidence','pending_evidence','pending_evidence','active_endpoint'])
        self.assertEqual(result['confirmed_wave_count'],3)
        self.assertEqual(result['developing_point_count'],8)
        self.assertTrue(all(p['display_only'] for p in path))
        self.assertTrue(all(left['available_at']<=right['available_at'] for left,right in zip(path,path[1:])))
        for point in path[1:]:
            original=source['strokes'][0]['points'][point['source_level1_position']]
            for field in ['time','index','ordinal','kind','value']:
                self.assertEqual(point[field],original[field])
            self.assertGreaterEqual(point['available_at'],original['available_at'])

    def test_mirrored_bull_and_bear_rules(self):
        _,a=fixture(self.values); _,b=fixture([100-v for v in self.values],first='H')
        up=_structural_reversals(a['strokes'][0]['points']); down=_structural_reversals(b['strokes'][0]['points'])
        self.assertEqual([p['source_level1_position'] for p in up],[p['source_level1_position'] for p in down])
        self.assertEqual([p['confirmed_on_level1'] for p in up],[p['confirmed_on_level1'] for p in down])
        self.assertEqual([p['value'] for p in down],[100-p['value'] for p in up])

    def test_key_frozen_until_new_extreme_and_touch_is_not_break(self):
        _,source=fixture([30,20,28,18,26,16,24,17,25,17.5,26,18,27],first='H')
        turns=source['strokes'][0]['points']
        self.assertEqual(_structural_reversals(turns[:-1]),[])
        result=_structural_reversals(turns)
        self.assertEqual([(p['value'],p['confirmed_on_level1'],p['broken_key']['value']) for p in result],[(16,12,26)])

    def test_no_cross_path_and_no_mutation_or_coordinate_changes(self):
        bars,source=fixture(self.values)
        source['strokes'].append(dict(id='level1-second',kind='reversal',points=copy.deepcopy(source['strokes'][0]['points'])))
        before=copy.deepcopy(source)
        result=secondary_trends(source,bars)
        self.assertEqual(source,before)
        self.assertEqual(len(result['strokes']),2)
        self.assertNotEqual(result['strokes'][0]['id'],result['strokes'][1]['id'])
        for stroke in result['strokes']:
            for p in stroke['points']:
                original=source['strokes'][0]['points'][p['source_level1_position']]
                for field in ['time','index','ordinal','value','projection_count','projection_rank']:
                    self.assertEqual(p[field],original[field])

    def test_empty_mixed_equal_or_unbroken_tail_has_no_extra_line(self):
        self.assertEqual(secondary_trends(dict(strokes=[]),[])['strokes'],[])
        self.assertEqual(secondary_trends(dict(strokes=[]),[])['developing_strokes'],[])
        for values in [[10],[10,20,10,20,10,20],[10,20,12,22,14,24]]:
            bars,source=fixture(values)
            self.assertEqual(secondary_trends(source,bars)['strokes'],[])

    def test_close_break_of_old_low_reanchors_last_fall_high_to_next_segment(self):
        """A wick/touch holds the old key; the first strict close break moves it."""
        def point(index,time,kind,value,label,available_at):
            return dict(index=index,ordinal=0,time=time,kind=kind,value=value,
                        label=label,available_at=available_at)

        points=[
            point(10,'2025-07-10','H',8.72,'H33','2025-08-26'),
            point(20,'2026-01-23','L',6.32,'L34','2026-03-23'),
            point(30,'2026-04-02','H',7.52,'H34','2026-05-22'),
            point(40,'2026-06-29','L',6.34,'L35','2026-08-04'),
        ]
        bars=[
            # Intraday 6.20 is lower than 6.32, but the 6.50 close does not break it.
            Bar(datetime(2026,8,27),'TEST',6.45,6.60,6.20,6.50,100),
            # An equal close is a touch and remains on the old side of the key.
            Bar(datetime(2026,8,28),'TEST',6.50,6.55,6.30,6.32,100),
            Bar(datetime(2026,8,31),'TEST',6.13,6.24,6.12,6.23,100),
        ]

        events=last_fall_high_reanchors(points,bars,trend_level=2)
        self.assertEqual(len(events),1)
        event=events[0]
        self.assertEqual(event['available_at'],'2026-08-31')
        self.assertEqual((event['broken_low']['time'],event['broken_low']['value']),('2026-01-23',6.32))
        self.assertEqual((event['previous_key']['time'],event['previous_key']['value']),('2025-07-10',8.72))
        self.assertEqual((event['active_low']['time'],event['active_low']['value']),('2026-06-29',6.34))
        self.assertEqual((event['new_key']['time'],event['new_key']['value']),('2026-04-02',7.52))
        self.assertEqual(event['confirmed_by']['value'],6.23)
        self.assertEqual(event['confirmed_by']['previous_close'],6.32)
        self.assertEqual(event['confirmed_by']['break_basis'],'close_cross')

    def test_close_break_at_open_tail_reanchors_to_later_confirmed_high(self):
        """A confirmed tail high becomes the key even before the next level-2 low."""
        def point(index,time,kind,value,label,available_at,**evidence):
            return dict(index=index,ordinal=0,time=time,kind=kind,value=value,
                        label=label,available_at=available_at,**evidence)

        confirming_low=point(5351,'2026-07-14','L',13.26,'L283','2026-07-17')
        points=[
            point(5241,'2026-01-26','H',24.85,'H26','2026-03-05'),
            point(5300,'2026-04-28','L',16.73,'L26','2026-06-04'),
            point(5320,'2026-05-29','H',22.35,'H27','2026-07-17',confirmed_by=confirming_low),
        ]
        bars=[
            Bar(datetime(2026,6,22),'TEST',16.59,16.75,16.11,16.74,100),
            Bar(datetime(2026,6,23),'TEST',16.75,16.95,16.26,16.29,100),
        ]

        events=last_fall_high_reanchors(points,bars,trend_level=2)
        self.assertEqual(len(events),1)
        event=events[0]
        self.assertEqual(event['available_at'],'2026-07-17')
        self.assertEqual((event['broken_low']['time'],event['broken_low']['value']),('2026-04-28',16.73))
        self.assertEqual((event['previous_key']['time'],event['previous_key']['value']),('2026-01-26',24.85))
        self.assertEqual((event['new_key']['time'],event['new_key']['value']),('2026-05-29',22.35))
        self.assertEqual((event['active_low']['time'],event['active_low']['value']),('2026-07-14',13.26))
        self.assertEqual(event['active_low_source_level'],1)
        self.assertEqual(event['confirmed_by']['time'],'2026-06-23')


if __name__=='__main__':
    unittest.main()
