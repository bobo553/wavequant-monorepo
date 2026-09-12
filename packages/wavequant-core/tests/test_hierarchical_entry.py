from dataclasses import replace
from datetime import datetime, timedelta
import unittest

from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.hierarchical_entry import (EntryContext, _LevelState, hierarchical_history,
                                          context_history, select_entry)
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals
from wavequant.domain.strategies.strategy_profiles import hierarchical_profile, research_profile
from tests.test_lecture_strategy import history


def context(**kwargs):
    return replace(EntryContext(1, 0, 3, 2, 27, 5, 4, 29, 3, 17, 7, 6, 18), **kwargs)


def choose(ctx, **kwargs):
    params=dict(attack=20, origin_index=10, high_index=14, low_index=17, ratio=.2, shallow_ratio=1/3)
    params.update(kwargs)
    return select_entry([ctx], [ctx], **params)


class HierarchicalEntryTests(unittest.TestCase):
    def test_first_buy_does_not_apply_old_two_thirds_or_one_third(self):
        for ratio in (.2, 2/3, .9):
            proof, reason=choose(context(), ratio=ratio)
            self.assertFalse(reason); self.assertEqual(proof['buy_point_type'], 'transition_squeeze')
            self.assertFalse(proof['counter_filter_applied'])

    def test_second_buy_requires_maturity_then_new_shallow_pullback(self):
        proof, reason=choose(context(maturity_index=12))
        self.assertFalse(reason); self.assertEqual(proof['priority'], 2)
        self.assertTrue(proof['counter_filter_applied'])
        for ratio in (1/3, .5, .9):
            proof, reason=choose(context(maturity_index=12), ratio=ratio)
            self.assertIsNone(proof); self.assertEqual(reason, 'mature_pullback_not_shallow')

    def test_no_same_level_fallback_or_retroactive_pullback(self):
        for params in ({'high_index':11}, {'low_index':12}, {'origin_index':5}):
            proof, reason=choose(context(maturity_index=12), **params)
            self.assertIsNone(proof); self.assertEqual(reason, 'mature_pullback_sequence_not_ready')

    def test_maturity_on_attack_is_still_first_buy_not_a_future_pullback(self):
        self.assertEqual(choose(context(maturity_index=20),ratio=.8)[0]['priority'],1)

    def test_early_n_cannot_wait_until_mature_and_bypass_shallow_filter(self):
        ctx=context();live=replace(ctx,maturity_index=22)
        proof,reason=select_entry([live],[ctx],attack=20,origin_index=10,high_index=14,low_index=17,
            ratio=.9,shallow_ratio=1/3,asof=25)
        self.assertIsNone(proof);self.assertEqual(reason,'early_n_expired_after_maturity')
        self.assertIsNone(choose(context(maturity_index=20),asof=25)[0])

    def test_alternation_must_precede_attack_and_episode_must_still_exist(self):
        self.assertIsNone(choose(context(),attack=7)[0])
        proof,_=select_entry([], [context()],attack=20,origin_index=10,high_index=14,low_index=17,ratio=.2,shallow_ratio=1/3)
        self.assertIsNone(proof)

    def test_level_two_or_three_can_qualify_without_level_one(self):
        for level in (2,3):
            self.assertEqual(choose(context(trend_level=level))[0]['trend_level'],level)

    def test_second_buy_has_priority_across_levels(self):
        contexts=[context(trend_level=3),context(trend_level=1,maturity_index=12)]
        proof,_=select_entry(contexts,contexts,attack=20,origin_index=10,high_index=14,low_index=17,ratio=.2,shallow_ratio=1/3)
        self.assertEqual((proof['priority'],proof['trend_level']),(2,1))

    def test_deep_but_higher_low_completes_alternation_without_fraction_gate(self):
        state=_LevelState(1,0)
        for i,(kind,value) in enumerate([('H',30),('L',20),('H',27),('L',17),('H',29),('L',18)]):
            state.point(dict(index=i,ordinal=0,kind=kind,value=value,available_at=i),i)
        self.assertIsNotNone(state.context)
        self.assertGreater((29-18)/(29-17),2/3)
        self.assertEqual(state.context.flip_high_price,29)
        self.assertIsNone(state.context.maturity_index)

    def test_full_retrace_is_not_a_valid_structural_alternation(self):
        for low in (17,16):
            state=_LevelState(1,0)
            for i,(kind,value) in enumerate([('H',30),('L',20),('H',27),('L',17),('H',29),('L',low)]):
                state.point(dict(index=i,ordinal=0,kind=kind,value=value,available_at=i),i)
            self.assertIsNone(state.context)

    def test_maturity_cannot_be_backdated_and_epoch_clears_context(self):
        points=[dict(index=i,ordinal=0,kind=k,value=v,available_at=i) for i,(k,v) in
                enumerate([('H',30),('L',20),('H',27),('L',17),('H',29),('L',18)])]
        bars=[Bar(datetime(2020,1,1)+timedelta(days=i),'TEST',25,32,24,30 if i>=5 else 25,1000) for i in range(9)]
        snapshots={i:{level:tuple(points[:i+1]) if level==1 and i<8 else () for level in (1,2,3)} for i in range(9)}
        epochs={i:0 if i<8 else 8 for i in range(9)}
        contexts,events=context_history(bars,snapshots,epochs)
        self.assertIsNone(contexts[5][0].maturity_index)
        self.assertEqual(contexts[6][0].maturity_index,6)
        self.assertFalse(contexts[8])
        self.assertEqual(sum(e['event']=='hierarchy_bull_matured' for e in events),1)

    def test_hierarchy_and_signals_prefix_invariant(self):
        for seed in (18,43):
            bars=history(seed,130);full,epochs=hierarchical_history(bars)
            contexts,_=context_history(bars,full,epochs)
            config=SystemStrategy(pivot_mode='lecture_causal',entry_policy='hierarchical_two_buy_points')
            generated=generate_system_signals(bars,config)
            for i in range(20,len(bars),13):
                short,se=hierarchical_history(bars[:i+1])
                self.assertEqual(short[i],full[i],(seed,i))
                self.assertEqual(context_history(bars[:i+1],short,se)[0][i],contexts[i])
                self.assertEqual(generate_system_signals(bars[:i+1],config).signals,
                                 [s for s in generated.signals if s.bar_index<=i])
            self.assertEqual(generated.counts.get('entry_candidate_evaluations',0),
                             generated.counts['entry_candidate_rejections']+generated.counts['long_signals'])

    def test_profile_is_separate_retains_volume_risk_and_old_policy(self):
        old={'scenarios':{'base':{'execution':{'risk_fraction':.005}}}}
        v1=research_profile(old);v2=hierarchical_profile(old)
        self.assertEqual(v1['strategy']['entry_policy'],'transitioned_squeeze')
        self.assertNotIn('mature_shallow_ratio',v1['strategy'])
        self.assertEqual(v2['strategy']['entry_policy'],'hierarchical_two_buy_points')
        self.assertEqual(v2['strategy']['mature_shallow_ratio'],1/3)
        self.assertEqual(v2['strategy']['minimum_rvol'],1.2)
        self.assertEqual(v2['strategy']['minimum_reward_risk'],1.5)
        self.assertEqual(v1['scenarios'],v2['scenarios'])

    def test_invalid_policy_combinations_rejected(self):
        for kwargs in ({'pivot_mode':'strict_polyline'}, {'regime_filter':False}, {'mature_shallow_ratio':0}):
            params=dict(pivot_mode='lecture_causal',entry_policy='hierarchical_two_buy_points');params.update(kwargs)
            with self.assertRaises(ValueError):SystemStrategy(**params).validate()


if __name__=='__main__':unittest.main()
