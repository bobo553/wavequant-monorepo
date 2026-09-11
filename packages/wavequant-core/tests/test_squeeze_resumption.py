from dataclasses import replace
import unittest

from tests.test_entry_preflight import bars_and_points, generate
from wavequant.n_shape import NSetup, PivotRef, BoxAnchorMode
from wavequant.price_action import Direction, ShadowPolicy
from wavequant.market_regime import observe_market_regime, RegimePolicy, WaveBoundary, MarketRegime
from wavequant.squeeze_state import observe_squeeze_resumption
from wavequant.integrated_strategy import SystemStrategy


class SqueezeResumptionTests(unittest.TestCase):
    def fixture(self):
        bars,points=bars_and_points()
        # Record at 5, pullback on 6, recovery at 7 below the old record.
        bars[6]=replace(bars[6],open=13.1,high=13.3,low=12.3,close=12.5)
        bars[7]=replace(bars[7],open=12.6,high=13.6,low=12.4,close=13.5)
        setup=NSetup('TEST','1d',Direction.UP,PivotRef(0,0),PivotRef(1,1),PivotRef(2,2),
                     'test',BoxAnchorMode.ATTACK_VIRTUAL_EXTREME)
        result=observe_market_regime(bars,setup,timeframe='1d',
            policy=RegimePolicy(ShadowPolicy(.5),WaveBoundary.ORIGIN))
        return bars,points,result

    def test_prior_squeeze_held_defense_and_local_recovery(self):
        bars,points,result=self.fixture()
        frame=next(f for f in result.frames if f.bar_index==7)
        self.assertIsNone(frame.regime)  # no fabricated canonical record
        r=observe_squeeze_resumption(bars,7,frame=frame,offset=0,attack_index=3,defense=12)
        self.assertEqual(r.prior_confirmation_index,5)
        self.assertEqual(r.local_resistance,13.3)
        self.assertEqual(r.regime,MarketRegime.BULL)

    def test_no_blind_carry_or_unconfirmed_label(self):
        bars,points,result=self.fixture()
        frame=next(f for f in result.frames if f.bar_index==7)
        for change in (dict(last_confirmed_regime=None),dict(first_defense_breach_index=6),
                       dict(first_wave_breach_index=6),dict(last_confirmed_index=6),
                       dict(last_confirmed_regime=MarketRegime.GRIND_UP)):
            self.assertIsNone(observe_squeeze_resumption(bars,7,frame=replace(frame,**change),
                                                        offset=0,attack_index=3,defense=12))

    def test_bounce_requires_close_break_higher_low_and_positive_body(self):
        bars,points,result=self.fixture()
        frame=next(f for f in result.frames if f.bar_index==7)
        for change in (dict(close=13.3),dict(low=12.2),dict(open=13.5),dict(low=11.9)):
            modified=list(bars); modified[7]=replace(bars[7],**change)
            self.assertIsNone(observe_squeeze_resumption(modified,7,frame=frame,offset=0,
                                                        attack_index=3,defense=12))

    def test_preflight_still_blocks_inadequate_resume_target(self):
        bars,points,result=self.fixture()
        r=generate(bars[:8],points)
        self.assertTrue(any(e['event']=='squeeze_resumption_observed' for e in r.audit))
        self.assertFalse(any(s.side=='LONG' for s in r.signals))
        self.assertTrue(any(e['event']=='entry_preflight_rejected' and e['bar_index']==7 for e in r.audit))

    def test_full_pipeline_resumption_prefix(self):
        bars,points,result=self.fixture()
        full=generate(bars,points)
        for end in range(4,len(bars)):
            short=generate(bars[:end+1],points)
            self.assertEqual(short.signals,[s for s in full.signals if s.bar_index<=end])

    def test_no_resumption_without_transition_policy(self):
        with self.assertRaises(ValueError):
            SystemStrategy(entry_policy='legacy_n_continuation').validate()


if __name__=='__main__':
    unittest.main()
