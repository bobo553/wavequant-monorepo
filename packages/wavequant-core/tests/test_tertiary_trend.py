import copy
import unittest

from tests.test_secondary_trend import fixture
from wavequant.secondary_trend import _structural_reversals
from wavequant.tertiary_trend import tertiary_trends


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
        self.assertEqual(result['aggregation_rule'],'level2_structural_key_break')
        stroke=result['strokes'][0]; points=stroke['points']; raw=source['strokes'][0]['points']
        self.assertEqual(stroke['source_path'],'secondary-fixture')
        self.assertEqual([(p['kind'],p['value']) for p in points],[('L',10),('H',45),('L',24)])
        self.assertEqual([p['source_level2_position'] for p in points],[2,9,14])
        self.assertEqual([p['confirmed_on_level2'] for p in points],[7,14,17])
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

    def test_source_level_parameter_preserves_existing_algorithm(self):
        _,source=self.source(); points=source['strokes'][0]['points']
        old=_structural_reversals(points); explicit=_structural_reversals(points,source_level=1)
        self.assertEqual(old,explicit)
        third=_structural_reversals(points,source_level=2)
        for a,b in zip(old,third):
            for field in ['time','value','kind','available_at','flip','broken_key','confirmed_by']:
                self.assertEqual(a[field],b[field])
        self.assertEqual(len(old),len(third))

    def test_no_cross_path_or_unconfirmed_tail(self):
        bars,source=self.source()
        source['strokes'].append(dict(source['strokes'][0],id='secondary-other'))
        result=tertiary_trends(source,bars)
        self.assertEqual(len(result['strokes']),2)
        self.assertNotEqual(result['strokes'][0]['id'],result['strokes'][1]['id'])
        for s in result['strokes']:
            self.assertEqual(len(s['points']),3)
            self.assertLess(s['points'][-1]['source_level2_position'],len(source['strokes'][0]['points'])-1)


if __name__=='__main__':
    unittest.main()
