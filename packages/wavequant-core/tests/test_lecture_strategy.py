from datetime import datetime,timedelta
import random
import unittest
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.integrated_strategy import SystemStrategy,generate_system_signals,pivot_history
from wavequant.domain.market_structure.lecture_drawing import lecture_drawing
from wavequant.domain.strategies.strategy_profiles import research_profile


def history(seed=901,n=100):
    rng=random.Random(seed);bars=[];price=30
    for i in range(n):
        close=max(10,price+rng.uniform(-3,3));high=max(close,price)+rng.uniform(.1,2);low=min(close,price)-rng.uniform(.1,2)
        bars.append(Bar(datetime(2020,1,1)+timedelta(days=i),'TEST',price,high,low,close,rng.randint(100000,2000000)))
        price=close
    return bars


class LectureStrategyTests(unittest.TestCase):
    def test_callback_preserves_drawing_output_and_all_prefix_frames(self):
        bars=history();frames={}
        from copy import deepcopy
        actual=lecture_drawing(bars,on_step=lambda i,e,p:frames.update({i:(e,deepcopy(p))}))
        self.assertEqual(actual,lecture_drawing(bars));self.assertEqual(len(frames),len(bars))
        for i in range(len(bars)):
            expected={};lecture_drawing(bars[:i+1],on_step=lambda j,e,p:expected.update({j:(e,p)}))
            self.assertEqual(frames[i],expected[i])

    def test_known_mother_child_does_not_reset_unlike_old_strict(self):
        bars=history();c=SystemStrategy(pivot_mode='lecture_causal')
        _,_,_,old=pivot_history(bars,SystemStrategy());snaps,_,_,blocked=pivot_history(bars,c)
        self.assertGreater(len(old),len(blocked))
        for i,points in snaps.items():
            for p in points:
                self.assertLessEqual(p.confirmed_index,i)
                self.assertEqual(p.point.price,getattr(bars[p.point.index],'high' if p.point.kind.value=='H' else 'low'))

    def test_snapshot_and_signal_prefix_invariance(self):
        for seed in (901,18,43):
            bars=history(seed,90);c=SystemStrategy(pivot_mode='lecture_causal')
            full=generate_system_signals(bars,c);snapshots=pivot_history(bars,c)[0]
            for i in range(4,len(bars),5):
                short=generate_system_signals(bars[:i+1],c)
                self.assertEqual(short.signals,[s for s in full.signals if s.bar_index<=i],(seed,i))
                self.assertEqual(pivot_history(bars[:i+1],c)[0][i],snapshots[i])
            self.assertEqual(full.counts['entry_candidate_evaluations'] if 'entry_candidate_evaluations' in full.counts else 0,
                full.counts['entry_candidate_rejections']+full.counts['long_signals'])

    def test_do_not_mutate_legacy_profile_or_lower_filters(self):
        old={'strategy':{'pivot_mode':'strict_polyline'},'folds':[{'old_result':True}],
             'scenarios':{'base':{'execution':{'risk_fraction':.005},'metrics':{'old_result':True}}}}
        new=research_profile(old);self.assertEqual(old['strategy']['pivot_mode'],'strict_polyline')
        self.assertEqual(new['strategy']['minimum_rvol'],1.2);self.assertEqual(new['strategy']['minimum_reward_risk'],1.5)
        self.assertEqual(new['strategy']['entry_policy'],'transitioned_squeeze')
        self.assertEqual(new['strategy']['pivot_mode'],'lecture_causal')
        self.assertEqual(new['scenarios']['base']['execution'],old['scenarios']['base']['execution'])
        self.assertNotIn('metrics',new['scenarios']['base']);self.assertNotIn('folds',new)


if __name__=='__main__':unittest.main()
