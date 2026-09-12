from datetime import datetime,timedelta
import unittest

from wavequant.domain.models.model import Bar
from wavequant.domain.market_structure.lecture_trend import reversal_trends,_annotate,_wave_reversals
from wavequant.domain.market_structure.lecture_drawing import lecture_drawing


def fixture(values):
    bars=[Bar(datetime(2026,1,1)+timedelta(days=i),'TEST',v,v+1,v-1,v+.1,100) for i,v in enumerate(values)]
    points=[dict(index=i,ordinal=0,time=b.timestamp.date().isoformat(),value=v,kind='H' if i%2==0 else 'L',
                 state='seed' if i==0 else 'developing' if i==len(values)-1 else 'confirmed',
                 available_at=bars[min(i+1,len(bars)-1)].timestamp.date().isoformat())
            for i,(b,v) in enumerate(zip(bars,values))]
    return bars,dict(strokes=[dict(id='test',points=points)])


class LectureTrendTests(unittest.TestCase):
    def test_figure_008_keys_transitions_and_suspicions(self):
        labels=['L1','H1','L0','H2','L2','H3','L3','H4','L4','H0','L5','H5','L6','H6','L7','H7','L8']
        values=[20,30,10,20,14,26,18,35,28,45,36,42,30,36,24,31,26]
        bars,drawing=fixture([40]+values+[33])
        # Figure 008 is already a wave-level illustration, not a literal input
        # that must survive aggregation as one solid node per small vertex.
        points=[dict(p) for p in drawing['strokes'][0]['points'][1:-1]]
        _annotate(points,'TEST',{b.timestamp.date().isoformat():i for i,b in enumerate(bars)})
        mapping=dict(zip(labels,points))
        for target,prior in [('L0','H1'),('L7','H6'),('H0','L4'),('H4','L3')]:
            self.assertEqual(mapping[target]['preceding_turn']['index'],mapping[prior]['index'])
        expected={'H4':'翻空为多','L4':'空多交替','H5':'头部疑虑','L7':'翻多为空','H7':'多空交替','L2':'底部疑虑'}
        for pivot,title in expected.items():
            self.assertIn(title,[e['title'] for e in mapping[pivot]['observations']])
        self.assertEqual(mapping['H4']['observations'][0]['key']['value'],30)
        self.assertEqual(mapping['L7']['observations'][0]['key']['value'],28)
        self.assertAlmostEqual(mapping['L4']['observations'][0]['ratio'],7/17)
        self.assertFalse(mapping['L4']['observations'][0]['abc_confirmed'])

    def test_no_seed_developing_or_same_direction_middle_points(self):
        bars,drawing=fixture([8,10,12,15,11,16])
        self.assertEqual(reversal_trends(drawing,bars)['strokes'],[])

    def test_same_bar_turns_keep_order_and_original_projection(self):
        bars,drawing=fixture([8,12,9,14,10])
        raw=drawing['strokes'][0]['points']
        raw[2].update(time=raw[1]['time'],index=1,ordinal=1)
        # A pair of same-bar small turns is not, by itself, a wave reversal.
        self.assertEqual(reversal_trends(drawing,bars)['strokes'],[])

    def test_no_cross_path_connection(self):
        bars,drawing=fixture([35,30,20,28,18,26,16,24,17,27,19,29,21,31,23,30,22,28,20,26,18,24,19,27,21,30])
        drawing['strokes'].append(dict(id='second',points=[dict(p) for p in drawing['strokes'][0]['points']]))
        result=reversal_trends(drawing,bars)
        self.assertEqual(len(result['strokes']),2)
        self.assertNotEqual(result['strokes'][0]['id'],result['strokes'][1]['id'])

    def test_ordinary_confirmed_turns_stay_fixed_as_developing_tail_extends(self):
        rows=[(10,12,9,11),(12,14,10,13),(11,13,8,12),(10,12,6,11),(8,13,7,12),(10,15,8,14)]
        bars=[Bar(datetime(2026,1,i+1),'TEST',*row,100) for i,row in enumerate(rows)]
        short=reversal_trends(lecture_drawing(bars[:5]),bars[:5])
        long=reversal_trends(lecture_drawing(bars),bars)
        self.assertEqual(short,long)

    def test_empty_and_insufficient_turns(self):
        bars,drawing=fixture([8,10,12])
        self.assertEqual(reversal_trends(drawing,bars)['strokes'],[])
        self.assertEqual(reversal_trends(dict(strokes=[]),[])['strokes'],[])

    def test_equal_highs_do_not_form_bullish_trend(self):
        bars,drawing=fixture([20,10,30,14,30,18,35])
        points=[dict(p) for p in drawing['strokes'][0]['points'][1:-1]]
        _annotate(points,'TEST',{b.timestamp.date().isoformat():i for i,b in enumerate(bars)})
        self.assertEqual(points[-1]['trend'],'高低点不同向或相等')

    def test_67_percent_boundary_is_not_alternation(self):
        # H=200 -> L=100 -> H=180 -> L=80 establishes bear; H=210
        # breaks 180, then pullback 87.1 / impulse 130 = exactly 67%.
        bars,drawing=fixture([100,200,100,180,80,210,122.9,220])
        points=[dict(p) for p in drawing['strokes'][0]['points'][1:-1]]
        for i,p in enumerate(points):
            p['kind']='H' if i%2==0 else 'L'
        _annotate(points,'TEST',{b.timestamp.date().isoformat():i for i,b in enumerate(bars)})
        self.assertIn('翻空为多',[e['title'] for e in points[-2]['observations']])
        self.assertEqual(points[-1]['observations'][0]['title'],'回档未通过交替条件')
        self.assertAlmostEqual(points[-1]['observations'][0]['ratio'],.67)

    def test_wave_line_spans_many_small_turns(self):
        values=[30,20,28,18,26,16,24,17,27,19,29,21,31,23,30,22,28,20,26,18,24,19,27,21]
        turns=[dict(index=i,ordinal=0,value=v,kind='H' if i%2==0 else 'L',time=str(i),available_at=str(i+1)) for i,v in enumerate(values)]
        result=_wave_reversals(turns)
        self.assertEqual([(p['kind'],p['value'],p['index']) for p in result],[('L',16,5),('H',31,12),('L',18,19)])
        self.assertEqual([p['confirmed_on_turn'] for p in result],[8,15,22])
        self.assertEqual([p['available_at'] for p in result],['9','16','23'])
        self.assertGreater(result[1]['index']-result[0]['index'],1)
        for end in range(4,len(turns)+1):
            prefix=_wave_reversals(turns[:end])
            self.assertEqual(prefix,[p for p in result if p['confirmed_on_turn']<end])

    def test_mixed_or_equal_structure_does_not_trigger_wave_reversal(self):
        values=[10,20,12,22,11,24,11,24]
        turns=[dict(index=i,ordinal=0,value=v,kind='L' if i%2==0 else 'H',time=str(i),available_at=str(i+1)) for i,v in enumerate(values)]
        self.assertEqual(_wave_reversals(turns),[])

    def test_derivation_does_not_mutate_base_polyline(self):
        import copy
        bars,drawing=fixture([35,30,20,28,18,26,16,24,17,27,19,29,21,31,23,30,22,28,20,26,18,24,19,27,21,30])
        before=copy.deepcopy(drawing)
        result=reversal_trends(drawing,bars)
        self.assertEqual(drawing,before)
        self.assertEqual(result['trend_level'],1)
        self.assertEqual(result['name'],'一级趋势线')
        self.assertGreater(result['input_turn_count'],result['confirmed_wave_count'])


if __name__=='__main__':unittest.main()
