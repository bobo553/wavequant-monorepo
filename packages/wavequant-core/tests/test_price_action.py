from dataclasses import FrozenInstanceError, asdict, replace
from datetime import datetime, timedelta
import json
import unittest

from wavequant.domain.models.model import Bar
from wavequant.domain.market_structure.price_action import (AttackBasis, Direction, KeyLevel, LevelKind, Phase,
                                    ShadowPolicy, observe_attack, observe_resistance, observe_sequence)


def b(i,o,h,l,c):
    return Bar(datetime(2026,9,1)+timedelta(days=i),'TEST',o,h,l,c,1000)


def fixture():
    return [b(0,9,10,8.8,9.5),b(1,9.6,10.8,9.4,10.5),
            b(2,10.6,10.7,9.8,10.1),b(3,10.2,11.3,10,11),b(4,11,11.5,10.5,11.2)]


def level(**kw):
    return replace(KeyLevel('TEST','1d',LevelKind.RESISTANCE,10,0,0,'previous_rebound_high'),**kw)


def mirror(bar):
    return replace(bar,open=30-bar.open,high=30-bar.low,low=30-bar.high,close=30-bar.close)


class PriceActionTests(unittest.TestCase):
    def test_intrabar_and_close_breach_separate(self):
        bars=fixture()
        bars[1]=replace(bars[1],close=9.9)
        e=observe_attack(bars,1,level(),timeframe='1d')
        self.assertTrue(e.intrabar_crossed)
        self.assertFalse(e.close_crossed)
        self.assertTrue(e.qualifies(AttackBasis.INTRABAR))
        self.assertFalse(e.qualifies(AttackBasis.CLOSE))

    def test_breakdown_is_symmetric(self):
        bars=list(map(mirror,fixture()))
        e=observe_attack(bars,1,level(kind=LevelKind.SUPPORT,price=20,source='last_rising_origin_low'),timeframe='1d')
        self.assertEqual(e.direction,Direction.DOWN)
        self.assertTrue(e.intrabar_crossed and e.close_crossed)

    def test_touch_not_cross(self):
        bars=fixture()
        bars[1]=b(1,9.7,10,9.6,10)
        e=observe_attack(bars,1,level(),timeframe='1d')
        self.assertTrue(e.touched)
        self.assertFalse(e.intrabar_crossed or e.close_crossed)

    def test_gap_counts_without_touch(self):
        bars=fixture()
        bars[1]=b(1,10.3,10.8,10.2,10.7)
        e=observe_attack(bars,1,level(),timeframe='1d')
        self.assertTrue(e.gap_across and e.close_crossed)
        self.assertFalse(e.touched)

    def test_already_beyond_not_repeated_attack(self):
        e=observe_attack(fixture(),2,level(),timeframe='1d')
        self.assertTrue(e.close_beyond)
        self.assertFalse(e.intrabar_crossed or e.close_crossed)

    def test_return_then_reattack(self):
        bars=fixture()
        bars[2]=replace(bars[2],close=9.9)
        e=observe_attack(bars,3,level(),timeframe='1d')
        self.assertTrue(e.close_crossed)

    def test_higher_open_bearish_not_any_bearish_body(self):
        p=b(0,10,11,9,10)
        e=observe_resistance(p,b(1,10.5,10.5,9.5,9.7),attack_direction=Direction.UP)
        self.assertTrue(e.opposing_open_and_body and e.detected)
        e=observe_resistance(p,b(1,10,10,9.5,9.7),attack_direction=Direction.UP)
        self.assertTrue(e.bearish_body)
        self.assertFalse(e.opposing_open_and_body or e.detected)

    def test_direct_opposing_open_even_with_trend_body(self):
        e=observe_resistance(b(0,10,11,9,10),b(1,9.5,11,9.5,10.9),attack_direction=Direction.UP)
        self.assertTrue(e.direct_opposing_open and e.detected)
        self.assertEqual(e.reasons,('direct_lower_open',))

    def test_bull_resistance_examples_are_symmetric(self):
        bars=fixture()
        e=observe_resistance(mirror(bars[1]),mirror(bars[2]),attack_direction=Direction.DOWN)
        self.assertTrue(e.opposing_open_and_body and e.detected)
        self.assertEqual(e.opposing_side,'bulls')

    def test_shadow_unknown_without_policy(self):
        p,c=b(0,10,11,9,10),b(1,10,12,10,10.2)
        e=observe_resistance(p,c,attack_direction=Direction.UP)
        self.assertIsNone(e.long_shadow)
        self.assertIsNone(e.detected)
        self.assertAlmostEqual(e.shadow_range_fraction,.9)
        e=observe_resistance(p,c,attack_direction=Direction.UP,shadow_policy=ShadowPolicy(.5))
        self.assertTrue(e.long_shadow and e.detected)
        e=observe_resistance(p,c,attack_direction=Direction.UP,shadow_policy=ShadowPolicy(.95))
        self.assertFalse(e.long_shadow or e.detected)

    def test_zero_range_no_shadow_and_no_nan(self):
        e=observe_resistance(b(0,10,10,10,10),b(1,10,10,10,10),attack_direction=Direction.UP)
        self.assertFalse(e.long_shadow or e.detected)
        self.assertIsNone(e.shadow_range_fraction)
        json.dumps(asdict(e),default=str,allow_nan=False)

    def test_phases_follow_available_bars(self):
        bars=fixture()
        kw=dict(timeframe='1d',attack_basis=AttackBasis.CLOSE)
        first=observe_sequence(bars[:2],1,level(),**kw)
        second=observe_sequence(bars[:3],1,level(),**kw)
        third=observe_sequence(bars[:4],1,level(),**kw)
        self.assertEqual(first.phase,Phase.AWAIT_RESPONSE)
        self.assertIsNone(first.response)
        self.assertEqual(second.phase,Phase.AWAIT_CONFIRMATION)
        self.assertIsNone(second.confirmation)
        self.assertEqual(third.phase,Phase.CONFIRMATION_OBSERVED)
        self.assertEqual(first.attack,third.attack)
        self.assertEqual(second.response,third.response)

    def test_confirmation_is_facts_not_success_rule(self):
        e=observe_sequence(fixture(),1,level(),timeframe='1d',attack_basis=AttackBasis.CLOSE)
        self.assertTrue(e.confirmation.close_beyond_attack_extreme)
        self.assertEqual(e.confirmation.resistance_outcome,'undefined_rule')
        self.assertEqual(e.confirmation.observed_at,fixture()[3].timestamp)

    def test_later_prices_never_rewrite_episode(self):
        bars=fixture()
        kw=dict(timeframe='1d',attack_basis=AttackBasis.CLOSE)
        expected=observe_sequence(bars[:4],1,level(),**kw)
        bars[4]=b(4,1,2,.5,1)
        self.assertEqual(expected,observe_sequence(bars,1,level(),**kw))

    def test_no_attack_has_no_response_or_confirmation(self):
        e=observe_sequence(fixture(),2,level(),timeframe='1d',attack_basis=AttackBasis.CLOSE)
        self.assertEqual(e.phase,Phase.NO_ATTACK)
        self.assertIsNone(e.response)
        self.assertIsNone(e.confirmation)

    def test_basis_is_explicit(self):
        with self.assertRaises(TypeError):
            observe_sequence(fixture(),1,level(),timeframe='1d')
        with self.assertRaises(ValueError):
            observe_sequence(fixture(),1,level(),timeframe='1d',attack_basis='close')

    def test_future_anchor_rejected(self):
        with self.assertRaises(ValueError):
            observe_attack(fixture(),1,level(confirmed_index=1),timeframe='1d')
        with self.assertRaises(ValueError):
            level(source_index=2,confirmed_index=1)

    def test_level_is_frozen(self):
        anchor=level()
        with self.assertRaises(FrozenInstanceError):
            anchor.price=11

    def test_symbol_timeframe_ohlc_and_order_validation(self):
        for anchor,tf in ((level(symbol='OTHER'),'1d'),(level(),'5m')):
            with self.assertRaises(ValueError):
                observe_attack(fixture(),1,anchor,timeframe=tf)
        for change in (dict(high=8),dict(close=float('nan')),dict(timestamp=fixture()[0].timestamp)):
            bars=fixture()
            bars[1]=replace(bars[1],**change)
            with self.assertRaises(ValueError):
                observe_attack(bars,1,level(),timeframe='1d')

    def test_observe_attack_reuses_validated_immutable_prefix(self):
        from unittest.mock import patch
        from wavequant.domain.market_state import wave_strength
        from wavequant.domain.models.validated_bars import ValidatedBars

        certified = ValidatedBars(fixture())
        with patch.object(wave_strength, '_validate_bar', side_effect=AssertionError('redundant validation')):
            self.assertEqual(observe_attack(certified, 1, level(), timeframe='1d').bar_index, 1)
            with self.assertRaisesRegex(AssertionError, 'redundant validation'):
                observe_attack(list(certified), 1, level(), timeframe='1d')

    def test_invalid_shadow_policy(self):
        for value in (0,-.1,1.1,float('nan'),True):
            with self.assertRaises(ValueError): ShadowPolicy(value)

    def test_weekend_does_not_skip_next_available_bar(self):
        bars=fixture()
        bars=[replace(bar,timestamp=bar.timestamp+timedelta(days=2 if i>=2 else 0)) for i,bar in enumerate(bars)]
        e=observe_sequence(bars,1,level(),timeframe='1d',attack_basis=AttackBasis.CLOSE)
        self.assertEqual(e.response.observed_at,bars[2].timestamp)
        self.assertEqual(e.confirmation.observed_at,bars[3].timestamp)


if __name__=='__main__':
    unittest.main()
