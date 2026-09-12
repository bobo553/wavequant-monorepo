from dataclasses import replace
from datetime import datetime, timedelta
import json
from pathlib import Path
import tempfile
import unittest

from wavequant.application.analytics.backtest import run_backtest
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar
from wavequant.application.analytics.notion import inventory_notes
from wavequant.application.analytics.notion import run_notion
from wavequant.application.analytics.research import write_rows
from unittest.mock import patch
from wavequant.domain.market_structure.structure import (StructureConfig, candle_evidence, completed_weekly_context,
                                 structural_signals, volume_votes)


def pattern():
    prices = [(10, 10.5, 9.5)]*22 + [
        (9.5,10,9), (9,9.5,8.6), (8.5,9,8), (9,9.5,8.8), (10,10.5,9.5),
        (11,11.5,10.5), (11.5,12,11), (11,11.8,10.9), (10.9,11.3,10.8),
        (10.8,11.1,10.7), (10.7,11,10.5), (11,11.3,10.7), (11.5,11.8,11),
        (12.2,12.4,11.8), (12.1,12.3,12.05), (12.7,12.8,12.1),
        (12.7,12.9,12.5), (12.7,12.9,12.5)]
    return [Bar(datetime(2020,1,1)+timedelta(days=i), 'TEST', c,h,l,c,1_000_000)
            for i,(c,h,l) in enumerate(prices)]


class NotionTests(unittest.TestCase):
    def test_p_is_close_location_not_statistical_significance_or_body(self):
        b = Bar(datetime(2020,1,1),'X',9.9,10,9,9.9,1)
        e = candle_evidence(b)
        self.assertAlmostEqual(e['p_value'],9)
        self.assertTrue(e['burst'])
        self.assertFalse(e['body_red'])
        self.assertAlmostEqual(e['burst_target'],10.8)

    def test_p_zero_range_and_zero_denominator_json_safe(self):
        for low in (9,10):
            e = candle_evidence(Bar(datetime(2020,1,1),'X',10,10,low,10,1))
            json.dumps(e,allow_nan=False)
            self.assertIsNone(e['p_value'])
            self.assertEqual(e['burst'],low==9)

    def test_n_confirmation_stop_and_frozen_target(self):
        bars = pattern()
        s, = structural_signals(bars,StructureConfig(target_mode='tide'))
        self.assertEqual(s.bar_index,37)
        self.assertEqual(s.trigger_timestamp,bars[35].timestamp)
        self.assertAlmostEqual(s.retracement,.375)
        self.assertAlmostEqual(s.invalidation_price,10.49)
        self.assertEqual(s.target_price,16)

    def test_full_prefix_invariance_with_real_signal(self):
        bars=pattern()
        for c in (StructureConfig(),StructureConfig(target_mode='tide'),StructureConfig(weekly_filter=True)):
            full=structural_signals(bars,c)
            for length in range(1,len(bars)+1):
                self.assertEqual(structural_signals(bars[:length],c),[s for s in full if s.bar_index<length])

    def test_pivot_cannot_be_used_before_right_confirmation(self):
        bars=pattern()
        # Pullback is visible but unconfirmed at index 32; an earlier spike is not an N entry.
        self.assertFalse(structural_signals(bars[:34],StructureConfig()))

    def test_support_failure_cancels_pending_setup(self):
        bars=pattern()
        bars[36]=replace(bars[36],low=11.9)
        self.assertFalse(structural_signals(bars,StructureConfig()))

    def test_no_resistance_no_confirmation(self):
        bars=pattern()[:38]
        bars[36]=replace(bars[36],open=12.5,close=12.5,high=12.6,low=12.0)
        bars[37]=replace(bars[37],low=12.2)
        self.assertFalse(structural_signals(bars,StructureConfig()))

    def test_weekly_uses_prior_complete_weeks_only(self):
        bars=[Bar(datetime(2020,1,6)+timedelta(days=i),'X',10+i,11+i,9+i,10+i,1)
              for i in range(230)]
        full=completed_weekly_context(bars)
        self.assertTrue(any(full))
        for length in (190,196,200,215):
            self.assertEqual(completed_weekly_context(bars[:length]),full[:length])
        altered=list(bars)
        # Current week's price extremes cannot affect the prior-completed-week signal.
        for i in range(224,230):
            altered[i]=replace(bars[i],close=1,low=1)
        self.assertEqual(full[224:],completed_weekly_context(altered)[224:])

    def test_volume_two_of_three(self):
        bars=pattern()
        self.assertEqual(volume_votes(bars,22),0)
        bars[22]=replace(bars[22],volume=2_000_000)
        self.assertEqual(volume_votes(bars,22),3)

    def test_target_exhausted_or_poor_rr_rejected(self):
        bars=pattern()
        s,=structural_signals(bars,StructureConfig(target_mode='tide'))
        for values, reason in ((dict(target_price=12.6),'target_exhausted_at_open'),
                               (dict(minimum_reward_risk=2),'insufficient_net_reward_risk')):
            r=run_backtest(bars,[replace(s,**values)],StrategyConfig())
            self.assertFalse(r.open_positions)
            self.assertEqual(r.orders[0]['reason'],reason)

    def test_fee_adjusted_rr_and_target_next_open_not_target_fill(self):
        bars=pattern()
        bars[38]=replace(bars[38],high=16.1)
        s,=structural_signals(bars[:38],StructureConfig(target_mode='tide'))
        r=run_backtest(bars,[s],StrategyConfig())
        self.assertEqual(len(r.trades),1)
        self.assertEqual(r.trades[0].exit_time,bars[39].timestamp)
        self.assertLess(r.trades[0].exit_price,16)
        self.assertEqual(r.trades[0].exit_reason,'target_observed')

    def test_high_below_target_is_not_a_fill(self):
        bars=pattern()
        s,=structural_signals(bars,StructureConfig(target_mode='tide'))
        r=run_backtest(bars,[s],StrategyConfig())
        self.assertFalse(r.trades)
        self.assertEqual(len(r.open_positions),1)

    def test_invalid_configuration_and_target(self):
        for c in (StructureConfig(pivot_width=True),StructureConfig(max_retracement=float('nan')),
                  StructureConfig(minimum_reward_risk=2),StructureConfig(volume_filter=1)):
            with self.assertRaises(ValueError): c.validate()
        bars=pattern()
        s,=structural_signals(bars,StructureConfig())
        with self.assertRaises(ValueError):
            run_backtest(bars,[replace(s,target_price=float('nan'))],StrategyConfig())

    def test_inventory_deep_pages_and_missing_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'child').mkdir()
            (root/'root.md').write_text('# Root\n',encoding='utf-8')
            (root/'child'/'deep.md').write_text('# Child\n![](https://example.com/a.png)\n![](missing.png)\n',encoding='utf-8')
            inv=inventory_notes(root)
            self.assertEqual(inv['page_count'],2)
            self.assertEqual(len(inv['external_images']),1)
            self.assertFalse(inv['pages'][0]['local_images'][0]['exists'])

    def test_notion_pipeline_and_future_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            notes=root/'notes'
            notes.mkdir()
            (notes/'page.md').write_text('# Fixture\n',encoding='utf-8')
            rows=[dict(timestamp=b.timestamp.isoformat(),symbol=b.symbol,open=b.open,
                       high=b.high,low=b.low,close=b.close,volume=b.volume) for b in pattern()]
            csv=root/'bars.csv'
            write_rows(csv,rows,list(rows[0]))
            protocol=dict(train=['2020-01-01','2020-01-20'],validation=['2020-01-21','2020-01-31'],
                          diagnostic=['2020-02-01','2020-02-09'],future_start='2020-02-10',
                          variants={'N_tide':{'target_mode':'tide'}})
            p=root/'protocol.json'
            p.write_text(json.dumps(protocol),encoding='utf-8')
            with patch('wavequant.application.analytics.notion.block_bootstrap',return_value={'fixture':True}), patch('builtins.print'):
                r=run_notion(csv,notes,root/'output',p,StrategyConfig())
            self.assertFalse(r['production_ready'])
            self.assertIsNone(r['selected_by_train'])
            self.assertEqual(r['results']['N_tide']['diagnostic']['trades'],0)
            self.assertEqual(r['results']['N_tide']['diagnostic']['open_positions'],1)
            for f in ('report.html','report.md','report.json','notes_inventory.json',
                      'N_tide/diagnostic/orders.csv','N_tide/diagnostic/equity.csv'):
                self.assertTrue((root/'output'/f).is_file())
            protocol['future_start']='2020-02-09'
            p.write_text(json.dumps(protocol),encoding='utf-8')
            with self.assertRaises(ValueError):
                run_notion(csv,notes,root/'rejected',p,StrategyConfig())

    def test_gross_rr_passes_but_net_rr_fails(self):
        bars=pattern()
        s,=structural_signals(bars,StructureConfig(target_mode='tide'))
        # Construct gross RR just over 2; positive fees alone must reject it.
        entry=bars[38].open
        s=replace(s,target_price=entry+2.001*(entry-s.invalidation_price),minimum_reward_risk=2)
        no_fee=replace(StrategyConfig(),slippage_bps_per_side=0,commission_bps_per_side=0,
                       minimum_commission=0,a_share_taxes=False)
        self.assertEqual(len(run_backtest(bars,[s],no_fee).open_positions),1)
        with_fee=replace(no_fee,minimum_commission=50)
        r=run_backtest(bars,[s],with_fee)
        self.assertEqual(r.orders[0]['reason'],'insufficient_net_reward_risk')


if __name__=='__main__':
    unittest.main()
