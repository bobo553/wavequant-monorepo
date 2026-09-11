import copy
from datetime import datetime, timedelta
import unittest

from wavequant.model import Bar
from wavequant.secondary_trend import _structural_reversals, secondary_trends


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

    def test_prefix_stability_no_future_repainting(self):
        bars,source=fixture(self.values)
        turns=source['strokes'][0]['points']; full=_structural_reversals(turns)
        for end in range(len(turns)+1):
            self.assertEqual(_structural_reversals(turns[:end]),[p for p in full if p['confirmed_on_level1']<end])

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
        for values in [[10],[10,20,10,20,10,20],[10,20,12,22,14,24]]:
            bars,source=fixture(values)
            self.assertEqual(secondary_trends(source,bars)['strokes'],[])


if __name__=='__main__':
    unittest.main()
