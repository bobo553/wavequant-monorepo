from dataclasses import replace
from datetime import datetime,timedelta
import unittest
from wavequant.model import Bar
from wavequant.hierarchical_entry import EntryContext,_LevelState
from wavequant.whole_wave_entry import select_wave_entry
from wavequant.integrated_strategy import SystemStrategy,generate_system_signals
from wavequant.strategy_profiles import WAVE_PROFILES,whole_wave_profile
from tests.test_lecture_strategy import history


def context(**kwargs):
    return replace(EntryContext(1,0,0,1,18,3,4,20,0,10,6,5,14,None),**kwargs)


def bars():
    return [Bar(datetime(2020,1,1)+timedelta(days=i),'TEST',22,23,21,22,1000) for i in range(18)]


def choose(ctx=None,data=None,**kwargs):
    ctx=ctx or context();params=dict(bars=data or bars(),attack=15,low_index=12,asof=17,deep_ratio=.5,shallow_ratio=1/3)
    params.update(kwargs);return select_wave_entry([ctx],[ctx],**params)


class WholeWaveTests(unittest.TestCase):
    def test_first_exact_boundary_and_depth_choices(self):
        self.assertIsNone(choose(context(alternation_low_price=15))[0])
        self.assertEqual(choose()[0]['counter_ratio'],.6)
        self.assertIsNone(choose(deep_ratio=2/3)[0])
        self.assertEqual(choose(context(alternation_low_price=13),deep_ratio=2/3)[0]['priority'],1)
        self.assertIsNone(choose(context(flip_high_price=40,alternation_low_price=20),deep_ratio=2/3)[0])
        self.assertIsNotNone(choose(context(alternation_low_price=10))[0])
        self.assertIsNone(choose(context(alternation_low_price=9.99))[0])

    def test_first_does_not_expire_while_waiting_for_squeeze(self):
        ctx=context();proof,reason=select_wave_entry([replace(ctx,maturity_index=15)],[ctx],
            bars=bars(),attack=15,low_index=12,asof=17)
        self.assertFalse(reason);self.assertEqual(proof['priority'],1)
        self.assertEqual(choose(context(maturity_index=15))[0]['priority'],1)

    def second_bars(self,close=24,low=11):
        data=bars()
        data[8]=replace(data[8],high=30,open=29,close=29,low=28)
        for i in range(9,18):data[i]=replace(data[i],open=27,high=28,close=27,low=26)
        data[12]=replace(data[12],low=low,close=close)
        return data

    def test_second_whole_wave_denominator_not_local_n_or_wick(self):
        p,_=choose(context(maturity_index=7),self.second_bars(),ratio=.95)
        self.assertEqual(p['priority'],2)
        self.assertEqual((p['ratio_high_price'],p['ratio_low_price'],p['counter_price']),(30,10,24))
        self.assertEqual(p['counter_ratio'],.3)
        self.assertEqual(p['local_n_ratio'],.95)
        self.assertEqual(p['counter_operator'],'<=')
        self.assertIsNone(choose(context(maturity_index=7),self.second_bars(close=23))[0])
        self.assertIsNotNone(choose(context(maturity_index=7),self.second_bars(close=20),shallow_ratio=.5)[0])
        self.assertIsNone(choose(context(maturity_index=7),self.second_bars(close=19.99),shallow_ratio=.5)[0])

    def test_exact_third_inclusive_and_close_recovery_does_not_erase_breach(self):
        data=self.second_bars(close=24)
        data[8]=replace(data[8],high=31)
        self.assertIsNotNone(choose(context(maturity_index=7),data)[0]) # (31-24)/(31-10)=1/3
        data[10]=replace(data[10],close=23,low=22)
        self.assertEqual(choose(context(maturity_index=7),data)[1],'wave_second_close_pullback_too_deep')

    def test_new_peak_after_n_low_and_origin_break_reject(self):
        data=self.second_bars();data[14]=replace(data[14],high=32)
        self.assertEqual(choose(context(maturity_index=7),data)[1],'wave_second_peak_pullback_sequence')
        data=self.second_bars(low=9.99)
        self.assertEqual(choose(context(maturity_index=7),data)[1],'wave_flip_origin_broken')

    def test_missing_attack_context_is_not_backdated_and_priority(self):
        p,r=select_wave_entry([context()],[],bars=bars(),attack=15,low_index=12,asof=17)
        self.assertIsNone(p);self.assertEqual(r,'wave_no_alternation_at_attack')
        contexts=[context(trend_level=3),context(maturity_index=7)]
        p,_=select_wave_entry(contexts,contexts,bars=self.second_bars(),attack=15,low_index=12,asof=17)
        self.assertEqual((p['priority'],p['trend_level']),(2,1))

    def test_whole_flip_low_not_last_small_pullback(self):
        state=_LevelState(1,0,whole_wave=True)
        for i,(kind,value) in enumerate([('H',30),('L',20),('H',27),('L',10),('H',24),('L',15),('H',29),('L',12)]):
            state.point(dict(index=i,ordinal=0,kind=kind,value=value,available_at=i),i)
        self.assertEqual(state.context.origin_price,10)
        self.assertEqual(state.context.origin_index,3)

    def test_four_profiles_and_parameter_guards(self):
        legacy={'scenarios':{'base':{'execution':{}}}}
        for variant in WAVE_PROFILES:
            c=whole_wave_profile(legacy,variant);SystemStrategy(**c['strategy']).validate()
            self.assertEqual(c['strategy']['buy_point_definition'],'whole_flip_wave_v3')
        for field,value in [('first_pullback_threshold',.4),('mature_shallow_ratio',.4)]:
            c=whole_wave_profile(legacy)['strategy'];c[field]=value
            with self.assertRaises(ValueError):SystemStrategy(**c).validate()

    def test_prefix_invariance_all_four_modes(self):
        legacy={'scenarios':{'base':{'execution':{}}}}
        data=history(18,230)
        for variant in WAVE_PROFILES:
            c=SystemStrategy(**whole_wave_profile(legacy,variant)['strategy'])
            full=generate_system_signals(data,c)
            for cut in (70,110,170,210):
                short=generate_system_signals(data[:cut+1],c)
                self.assertEqual(short.signals,[s for s in full.signals if s.bar_index<=cut],(variant,cut))
