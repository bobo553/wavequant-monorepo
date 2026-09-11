from copy import deepcopy
from datetime import datetime
from threading import Event
from types import SimpleNamespace
import time
import unittest
from unittest.mock import Mock

from wavequant.buy_scanner import BuyScanner,buy_match
from wavequant.screening_funnel import funnel


def view():
    return dict(symbol='sh.600000',price_basis='causal_adjusted_equivalent',run_id='test',audit=[],
        bars=[dict(time=f'2026-01-0{i}',factor=2) for i in range(1,7)],
        signals=[dict(timestamp='2026-01-06T00:00:00',side='LONG',reference_price=20,
            invalidation_price=18,target_price=24,regime='轧空',rvol=1.5,retracement=.25,reason='system_n')],orders=[])


class BuyMatchTests(unittest.TestCase):
    def test_funnel_uses_window_and_distinguishes_repeated_evaluations(self):
        v=view();v['audit']=[dict(timestamp='2026-01-05',event='entry_rejected',reason='too_old'),
            dict(timestamp='2026-01-06',event='entry_rejected',reason='not_squeeze_regime'),
            dict(timestamp='2026-01-06',event='entry_rejected',reason='not_squeeze_regime')]
        f=funnel(v,1)
        self.assertEqual(f['rejections'],{'not_squeeze_regime':2});self.assertEqual(f['no_long_signal'],0)
        v['signals']=[];self.assertEqual(funnel(v,1)['no_long_signal'],1)

    def test_today_signal_is_not_a_fill_and_uses_adjusted_reference(self):
        result,reason=buy_match(view(),'2026-01-06',1)
        self.assertIsNone(reason);self.assertEqual(result['status'],'awaiting_next_open')
        self.assertEqual(result['raw_reference_price'],10);self.assertEqual(result['gross_reward_risk'],2)
        self.assertIsNone(result['fill_date'])

    def test_historical_signal_not_current_and_filled_status(self):
        v=view();v['signals'][0]['timestamp']='2026-01-02T00:00:00'
        v['orders']=[dict(timestamp='2026-01-03T00:00:00',signal_timestamp='2026-01-02T00:00:00',
            side='BUY',status='filled',reason='system_n')]
        self.assertEqual(buy_match(v,'2026-01-06',1),(None,None))
        result,_=buy_match(v,'2026-01-06',5)
        self.assertEqual(result['status'],'filled');self.assertEqual(result['fill_date'],'2026-01-03')

    def test_holding_update_and_stale_data_are_not_current_buys(self):
        v=view();v['orders']=[dict(timestamp='2026-01-03T00:00:00',side='BUY',status='filled')]
        self.assertEqual(buy_match(v,'2026-01-06',1),(None,None))
        self.assertEqual(buy_match(view(),'2026-01-07',1),(None,'stale'))
        v['orders'].append(dict(timestamp='2026-01-05T00:00:00',side='SELL',status='filled'))
        self.assertEqual(buy_match(v,'2026-01-06',1)[0]['status'],'awaiting_next_open')

    def test_rejected_and_invalidated_historical_signals_not_marked_live(self):
        v=view();v['signals'][0]['timestamp']='2026-01-03T00:00:00'
        v['orders']=[dict(timestamp='2026-01-04T00:00:00',signal_timestamp='2026-01-03T00:00:00',
            side='BUY',status='cancelled',reason='entry_gap')]
        self.assertEqual(buy_match(v,'2026-01-06',5)[0]['status'],'rejected')
        v['orders']=[];v['signals'].append(dict(timestamp='2026-01-04T00:00:00',side='EXIT'))
        self.assertEqual(buy_match(v,'2026-01-06',5)[0]['status'],'invalidated')

    def test_prefix_does_not_need_future_fills_or_mutate_input(self):
        v=view();before=deepcopy(v);result,_=buy_match(v,'2026-01-06',1)
        self.assertEqual(v,before);self.assertNotIn('exit_date',result)


class ScannerTests(unittest.TestCase):
    def setUp(self):
        self.repo=SimpleNamespace(_run=lambda r:None,
            _json=lambda *args:dict(variants={'strict_full':dict(strategy={},scenarios={'base':{'execution':{}}})}),
            bars=lambda r:{s:[SimpleNamespace(timestamp=datetime(2026,1,6))] for s in ('sh.600000','sh.600001')},
            runs={'test':{'report':{'data':{'end':'2026-01-06'}}}},stock_view=Mock(return_value=view()))
        self.scanner=BuyScanner(self.repo)
        self.repo.strategy_config=lambda *args:self.repo._json()['variants']['strict_full']
        self.params=dict(run='test',variant='strict_full',scenario='base',source='snapshot',
            asof='2026-01-06',start='2020-01-01',lookback=1)

    def wait(self,ident):
        for _ in range(200):
            job=self.scanner.get(ident)
            if job['status'] not in ('running','cancelling'): return job
            time.sleep(.01)
        self.fail('job did not finish')

    def test_progress_complete_and_results_snapshot_copy(self):
        job=self.wait(self.scanner.start(self.params)['id'])
        self.assertEqual(job['status'],'completed');self.assertEqual(job['processed'],2)
        self.assertEqual(len(job['results']),2)
        self.assertEqual(job['funnel']['stocks']['no_long_signal'],0)
        job['results'].clear();self.assertEqual(len(self.scanner.get(job['id'])['results']),2)

    def test_match_published_while_next_stock_is_still_blocked(self):
        entered=Event();release=Event()
        def stock(*args):
            if args[2]=='sh.600001': entered.set();release.wait(3)
            result=view();result['symbol']=args[2];return result
        self.repo.stock_view.side_effect=stock
        job=self.scanner.start(self.params)
        try:
            self.assertTrue(entered.wait(2))
            partial=self.scanner.get(job['id'],after=job['revision'],timeout=.5)
            self.assertEqual(partial['status'],'running')
            self.assertEqual(partial['processed'],1);self.assertEqual(partial['total'],2)
            self.assertEqual([r['symbol'] for r in partial['results']],['sh.600000'])
            self.assertGreater(partial['revision'],job['revision'])
        finally:release.set();self.wait(job['id'])

    def test_waiter_wakes_on_result_without_waiting_for_completion(self):
        from concurrent.futures import ThreadPoolExecutor
        first=Event();second=Event();allow_first=Event();allow_second=Event()
        def stock(*args):
            (first if args[2]=='sh.600000' else second).set()
            (allow_first if args[2]=='sh.600000' else allow_second).wait(3)
            result=view();result['symbol']=args[2];return result
        self.repo.stock_view.side_effect=stock
        job=self.scanner.start(self.params);self.assertTrue(first.wait(2))
        revision=self.scanner.get(job['id'])['revision']
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending=pool.submit(self.scanner.get,job['id'],revision,timeout=2)
            try:
                self.assertFalse(pending.done());allow_first.set();self.assertTrue(second.wait(2))
                update=pending.result(timeout=1)
                self.assertEqual(update['status'],'running');self.assertEqual(len(update['results']),1)
                self.assertEqual(update['processed'],1)
                quiet=self.scanner.get(job['id']);same=self.scanner.get(job['id'],quiet['revision'],timeout=.01)
                self.assertEqual(same,quiet)  # bounded heartbeat, not a fake completion
                with self.assertRaises(ValueError):self.scanner.get(job['id'],quiet['revision']+1)
                for bad in (-1,True,'1'):
                    with self.assertRaises(ValueError):self.scanner.get(job['id'],bad)
            finally:allow_first.set();allow_second.set();self.wait(job['id'])

    def test_terminal_failure_revokes_previously_visible_matches(self):
        def stock(*args):
            if args[2]=='sh.600001':raise RuntimeError('source changed')
            return view()
        self.repo.stock_view.side_effect=stock
        job=self.wait(self.scanner.start(self.params)['id'])
        self.assertEqual(job['status'],'failed');self.assertFalse(job['results'])
        self.assertEqual(self.scanner.get(job['id'],job['revision'],timeout=0),job)

    def test_lecture_profile_is_used_explicitly_without_proxy_fallback(self):
        job=self.wait(self.scanner.start(dict(self.params,variant='lecture_v1'))['id'])
        self.assertEqual(job['status'],'completed');self.assertEqual(job['params']['variant'],'lecture_v1')
        self.assertEqual(self.repo.stock_view.call_args.args[1],'lecture_v1')

    def test_cancel_and_deduplicate_active_job(self):
        entered=Event();release=Event()
        def slow(*args):entered.set();release.wait(3);return view()
        self.repo.stock_view.side_effect=slow
        job=self.scanner.start(self.params);self.assertTrue(entered.wait(2))
        self.assertEqual(self.scanner.start(self.params)['id'],job['id'])
        with self.assertRaises(ValueError):self.scanner.start(dict(self.params,lookback=5))
        self.scanner.cancel(job['id']);release.set();end=self.wait(job['id'])
        self.assertEqual(end['status'],'cancelled');self.assertLess(end['processed'],2)

    def test_failures_are_not_silent_no_match(self):
        self.repo.stock_view.side_effect=ValueError('bad data')
        job=self.wait(self.scanner.start(self.params)['id'])
        self.assertEqual(job['failed'],2);self.assertEqual(job['results'],[])
        self.assertEqual(len(job['errors']),2)

    def test_input_guards(self):
        for bad in (dict(self.params,lookback=0),dict(self.params,lookback=True),dict(self.params,source='all'),
                    dict(self.params,start='2027-01-01'),dict(self.params,path='/secret')):
            with self.assertRaises(ValueError):self.scanner.start(bad)
        with self.assertRaises(ValueError):self.scanner.get('missing')


if __name__=='__main__':unittest.main()
