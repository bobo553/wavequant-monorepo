import copy
import unittest

from tests.test_secondary_trend import fixture, market_fixture
from wavequant.domain.market_structure.secondary_trend import _candidate_structural_reversals
from wavequant.domain.market_structure.tertiary_trend import tertiary_trends


class TertiaryTrendTests(unittest.TestCase):
    def source(self):
        bars,source=fixture([20,30,10,20,14,26,18,35,28,45,36,42,30,36,24,31,26,38,30,42])
        source.update(trend_level=2,name='二级趋势线')
        source['strokes'][0].update(id='secondary-fixture',kind='secondary',trend_level=2)
        return bars,source

    def test_level_three_uses_only_level_two_extremes_and_confirmation(self):
        bars,source=self.source(); before=copy.deepcopy(source)
        result=tertiary_trends(source,bars)
        self.assertEqual(source,before)
        self.assertEqual((result['trend_level'],result['source_level']),(3,2))
        self.assertEqual(result['aggregation_rule'],'same_level_key_break_or_lower_level_break_alternation_turn_or_n_strict_one_p')
        stroke=result['strokes'][0]; points=stroke['points']; raw=source['strokes'][0]['points']
        self.assertEqual(stroke['source_path'],'secondary-fixture')
        self.assertEqual([(p['kind'],p['value']) for p in points],[('H',45)])
        self.assertEqual([p['source_level2_position'] for p in points],[9])
        self.assertEqual([p['confirmed_on_level2'] for p in points],[14])
        self.assertEqual([(p['kind'],p['value']) for p in result['candidate_strokes'][0]['points']],
                         [('L',10),('H',45),('L',24)])
        self.assertEqual(result['developing_strokes'],[])
        self.assertEqual(result['developing_wave_count'],0)
        self.assertEqual(result['developing_point_count'],0)
        self.assertEqual(result['confirmed_wave_count'],1)
        self.assertEqual(result['bear_to_bull_highs'],[])
        self.assertIn('bear_bull_alternation_lows',result)
        self.assertIn('post_alternation_bull_highs',result)
        self.assertIn('bullish_turn_signals',result)
        for p in points:
            original=raw[p['source_level2_position']]; proof=raw[p['confirmed_on_level2']]
            self.assertEqual(p['available_at'],proof['available_at'])
            self.assertEqual(p['source_level2_available_at'],original['available_at'])
            for field in ['time','index','ordinal','value','projection_count','projection_rank']:
                self.assertEqual(p[field],original[field])
            self.assertTrue(p['levels'][0]['name'].startswith('二级'))
            self.assertNotIn('source_level1_position',p)

    def test_prefix_and_empty_source(self):
        bars,source=self.source(); points=source['strokes'][0]['points']
        full=tertiary_trends(source,bars)['strokes'][0]['points']
        for end in range(len(points)+1):
            prefix=copy.deepcopy(source); prefix['strokes'][0]['points']=points[:end]
            result=tertiary_trends(prefix,bars)
            self.assertEqual([p for s in result['strokes'] for p in s['points']],
                             [p for p in full if p['confirmed_on_level2']<end])
        self.assertEqual(tertiary_trends(dict(strokes=[]),[])['strokes'],[])
        self.assertEqual(tertiary_trends(dict(strokes=[]),[])['developing_strokes'],[])
        self.assertEqual(tertiary_trends(dict(strokes=[]),[])['bear_to_bull_highs'],[])
        self.assertEqual(tertiary_trends(dict(strokes=[]),[])['bear_bull_alternation_lows'],[])
        self.assertEqual(tertiary_trends(dict(strokes=[]),[])['post_alternation_bull_highs'],[])
        self.assertEqual(tertiary_trends(dict(strokes=[]),[])['bullish_turn_signals'],[])

    def test_source_level_parameter_preserves_existing_algorithm(self):
        _,source=self.source(); points=source['strokes'][0]['points']
        old=_candidate_structural_reversals(points); explicit=_candidate_structural_reversals(points,source_level=1)
        self.assertEqual(old,explicit)
        third=_candidate_structural_reversals(points,source_level=2)
        for a,b in zip(old,third):
            for field in ['time','value','kind','available_at','flip','broken_key','confirmed_by']:
                self.assertEqual(a[field],b[field])
        self.assertEqual(len(old),len(third))

    def test_no_cross_path_and_no_unqualified_developing_path(self):
        bars,source=self.source()
        source['strokes'].append(dict(source['strokes'][0],id='secondary-other'))
        result=tertiary_trends(source,bars)
        self.assertEqual(len(result['strokes']),2)
        self.assertNotEqual(result['strokes'][0]['id'],result['strokes'][1]['id'])
        for s in result['strokes']:
            self.assertEqual(len(s['points']),1)
            self.assertLess(s['points'][-1]['source_level2_position'],len(source['strokes'][0]['points'])-1)
        self.assertEqual(result['developing_strokes'],[])
        self.assertEqual(len(result['candidate_strokes']),2)

    def test_nested_source_turns_without_confirmation_do_not_expose_a_tail(self):
        values=[20,30,10,20,14,26,18,35,28,45,36,42,30,36,24,31,26,38,30,42,
                35,40,34,39,33,41,36,43,37,42]
        bars,source=fixture(values)
        source.update(trend_level=2)
        source['strokes'][0].update(id='secondary-long-tail',kind='secondary',trend_level=2)
        before=copy.deepcopy(source)
        result=tertiary_trends(source,bars)
        self.assertEqual([(p['kind'],p['value']) for p in result['strokes'][0]['points']],[('H',45)])
        self.assertEqual([(p['kind'],p['value']) for p in result['candidate_strokes'][0]['points']],
                         [('L',10),('H',45),('L',24)])
        self.assertEqual(result['developing_strokes'],[])
        self.assertEqual(result['developing_point_count'],0)
        self.assertEqual(source,before)

    def test_equal_unqualified_endpoint_never_becomes_public_or_display_evidence(self):
        bars,source=self.source()
        before=tertiary_trends(source,bars)
        source['strokes'][0]['points'].append(dict(source['strokes'][0]['points'][-1],
                                                   index=20,time='d20',available_at='d21',value=42,label='H11'))
        result=tertiary_trends(source,bars)
        self.assertEqual([stroke['points'] for stroke in result['strokes']],
                         [stroke['points'] for stroke in before['strokes']])
        self.assertEqual(result['candidate_strokes'][0]['points'],before['candidate_strokes'][0]['points'])
        self.assertEqual(result['developing_strokes'],[])

    def test_public_level_three_own_key_upgrade_carries_a_real_market_certificate(self):
        values=[20,30,10,20,14,26,18,35,28,45,36,42,30,36,24,31,26,50]
        bars,source=market_fixture(values)
        source['strokes'][0].update(id='secondary-market',kind='secondary',trend_level=2)
        before=copy.deepcopy(source)
        full=tertiary_trends(source,bars)
        points=full['strokes'][0]['points']
        self.assertEqual([(p['kind'],p['value']) for p in points],[('L',10),('H',45),('L',24),('H',50)])
        self.assertEqual(points[0]['trend_confirmation']['confirmation_rule'],'source_n_strict_one_p_target')
        low,high=points[-2:]
        proof=low['trend_confirmation']
        self.assertEqual(proof['confirmation_rule'],'strict_same_level_market_key_break')
        self.assertGreater(proof['confirmed_by']['value'],proof['broken_key']['value'])
        self.assertEqual(proof['broken_key']['available_at'],points[1]['available_at'])
        self.assertEqual(high['confirmation_rule'],'level2_confirmed_high_breaks_known_level3_last_fall_high')
        self.assertEqual((high['trend_level'],high['source_level2_position']),(3,17))
        self.assertNotIn('source_level1_position',high)
        self.assertGreaterEqual(high['available_at'],low['available_at'])
        raw=source['strokes'][0]['points']
        for end in range(1,len(bars)+1):
            asof=bars[end-1].timestamp.date().isoformat()
            prefix=copy.deepcopy(source)
            prefix['strokes'][0]['points']=[p for p in raw if p['available_at']<=asof]
            result=tertiary_trends(prefix,bars[:end])
            self.assertEqual([p for stroke in result['strokes'] for p in stroke['points']],
                             [p for p in points if p['available_at']<=asof])
        self.assertEqual(source,before)



if __name__=='__main__':
    unittest.main()
