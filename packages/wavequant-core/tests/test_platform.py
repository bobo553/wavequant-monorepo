from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from wavequant.infrastructure.persistence.event_store import EventStore
from wavequant.infrastructure.market_data.security_master import SecurityFact, SecurityMaster
from wavequant.application.trading.order_service import OrderService, PortfolioLimits, reserved_cash
from wavequant.application.trading.paper_venue import PaperVenue, PaperBridge, OpeningTick
from wavequant.application.trading.stream_runtime import StrategyRuntime
from wavequant.infrastructure.persistence.experiment_registry import ExperimentRegistry
from wavequant.application.analytics.validation_suite import walk_forward_folds, audit_annotations
from wavequant.infrastructure.market_data.data import fingerprint
from wavequant.infrastructure.market_data.data_catalog import register_dataset
from wavequant.application.trading.intent_execution import PaperIntentExecutor
from tests.test_integrated_strategy import fixture, config
from wavequant.domain.strategies.integrated_strategy import generate_system_signals

T='2026-01-01T09:30:00+08:00'
S='2025-12-31T15:00:00+08:00'
T2='2026-01-02T09:30:00+08:00'
T3='2026-01-03T09:30:00+08:00'


def fact(symbol='TEST', **kw):
    return replace(SecurityFact(symbol,'2020-01-01',None,'2019-12-31T15:00:00+08:00',
        'industrial',True,False,False,100,'.01',1,'synthetic_test_fixture'),**kw)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.path=Path(self.tmp.name)/'journal.sqlite'
        self.store=EventStore(self.path)
    def tearDown(self): self.store.close(); self.tmp.cleanup()

    def test_append_requires_transaction(self):
        with self.assertRaises(RuntimeError): self.store.append('a','TEST',T,{})

    def test_exact_duplicate_conflict_and_rollback(self):
        with self.store.transaction():
            self.store.append('a','TEST',T,{'x':1}); self.store.append('a','TEST',T,{'x':1})
        with self.assertRaises(ValueError),self.store.transaction():
            self.store.append('b','TEST',T,{})
            self.store.append('a','TEST',T,{'x':2})
        self.assertEqual(len(self.store.events()),1)

    def test_naive_time_and_nonfinite_rejected(self):
        for time,p in [('2026-01-01',{}),(T,{'v':float('nan')})]:
            with self.assertRaises(ValueError),self.store.transaction(): self.store.append('a','TEST',time,p)

    def test_update_delete_blocked(self):
        with self.store.transaction(): self.store.append('a','TEST',T,{})
        for sql in ('DELETE FROM events',"UPDATE events SET kind='X'"):
            with self.assertRaises(sqlite3.IntegrityError): self.store.db.execute(sql)

    def test_hash_tamper_detection(self):
        with self.store.transaction(): self.store.append('a','TEST',T,{})
        self.store.db.execute('DROP TRIGGER immutable_events_update')
        self.store.db.execute("UPDATE events SET payload='{} ',digest='bad'")
        with self.assertRaisesRegex(ValueError,'integrity'): self.store.verify()

    def test_backup_restore_and_no_overwrite(self):
        with self.store.transaction(): self.store.append('a','TEST',T,{'v':2})
        target=Path(self.tmp.name)/'backup.sqlite'; self.store.backup(target)
        other=EventStore(target)
        try: self.assertEqual(other.verify(),self.store.verify())
        finally: other.close()
        with self.assertRaises(ValueError): self.store.backup(target)

    def test_two_writer_handles_do_not_duplicate(self):
        other=EventStore(self.path)
        try:
            for s in (self.store,other):
                with s.transaction(): s.append('a','TEST',T,{})
            self.assertEqual(len(other.events()),1)
        finally: other.close()


class SecurityTests(unittest.TestCase):
    def test_bitemporal_fact_not_known_early(self):
        m=SecurityMaster([fact(),fact(effective_from='2026-01-01',known_at=T2,st=True)])
        self.assertFalse(m.at('TEST','2026-01-01',T).st)
        self.assertTrue(m.at('TEST','2026-01-01',T2).st)
        self.assertIsNone(m.at('UNKNOWN','2026-01-01',T))

    def test_delist_suspension_st_not_in_universe(self):
        m=SecurityMaster([fact(),fact('ST',st=True),fact('SUSP',suspended=True),fact('DELIST',active=False)])
        self.assertEqual(m.universe('2026-01-01',T),['TEST'])

    def test_expired_fact_never_resurrects_older_status(self):
        m=SecurityMaster([fact(),fact(effective_from='2025-12-01',effective_to='2026-01-01')])
        self.assertIsNone(m.at('TEST','2026-01-01',T))

    def test_invalid_types_and_duplicate_identity(self):
        with self.assertRaises(ValueError): fact(active=1)
        with self.assertRaises(ValueError): fact(tick_size='NaN')
        with self.assertRaises(ValueError): SecurityMaster([fact(),fact()])


class OrderTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.path=Path(self.tmp.name)/'oms.sqlite'
        self.store=EventStore(self.path); self.master=SecurityMaster([fact(),fact('OTHER')])
        self.oms=OrderService(self.store,self.master,opened_at=S)
        self.oms.quote('TEST','10',T,event_id='q1')
    def tearDown(self): self.store.close(); self.tmp.cleanup()
    def buy(self, q=100, **kw):
        args=dict(order_id='buy1',symbol='TEST',side='BUY',quantity=q,limit_price='10',stop_price='9',
                  when=T,signal_at=S,fee_budget='20'); args.update(kw)
        return self.oms.submit(**args)
    def filled(self): self.buy(); self.oms.fill('f1','buy1',100,'10','5',T)

    def test_partial_duplicate_fill_and_restart(self):
        self.buy(); self.oms.fill('f1','buy1',40,'10','5',T)
        self.oms.fill('f1','buy1',40,'10','5',T)
        self.assertEqual(self.oms.state()['positions']['TEST'],40)
        self.assertEqual(reserved_cash(self.oms.state()),Decimal('615'))
        self.store.close(); self.store=EventStore(self.path)
        self.oms=OrderService(self.store,self.master,opened_at=S)
        self.oms.fill('f2','buy1',60,'10','5',T)
        self.assertEqual(self.oms.snapshot()['cash'],'998990.00')
        self.assertEqual(reserved_cash(self.oms.state()),0)
        self.assertEqual(self.buy()['status'],'FILLED')

    def test_order_and_fill_identity_conflicts(self):
        self.buy()
        with self.assertRaisesRegex(ValueError,'identity'): self.buy(q=200)
        self.oms.fill('f','buy1',50,'10','5',T)
        with self.assertRaises(ValueError): self.oms.fill('f','buy1',60,'10','5',T)

    def test_cancel_pending_still_reserves_and_can_fill(self):
        self.buy(); self.oms.cancel('buy1',T)
        self.oms.fill('f','buy1',40,'10','5',T)
        self.assertEqual(self.oms.state()['orders']['buy1']['status'],'CANCEL_PENDING')
        self.assertEqual(reserved_cash(self.oms.state()),Decimal('615'))
        self.oms.cancel('buy1',T,confirmed=True)
        self.assertEqual(reserved_cash(self.oms.state()),0)
        with self.assertRaisesRegex(ValueError,'terminal'): self.oms.fill('late','buy1',60,'10','5',T)

    def test_overfill_price_fee_validation_atomic(self):
        self.buy()
        for q,p,f in [(101,'10','5'),(100,'10.01','5'),(100,'10','21')]:
            with self.assertRaises(ValueError): self.oms.fill('f','buy1',q,p,f,T)
        self.assertEqual(self.oms.state()['cash'],Decimal('1000000'))

    def test_t_plus_1_and_no_short(self):
        self.filled()
        args=dict(order_id='sell',symbol='TEST',side='SELL',quantity=100,limit_price='10',stop_price='9',signal_at=S)
        with self.assertRaisesRegex(ValueError,'T_plus_1'): self.oms.submit(when=T,**args)
        self.oms.quote('TEST','10',T2,event_id='q2'); self.oms.submit(when=T2,**args)
        self.oms.fill('exit','sell',100,'10','5',T2)
        self.assertEqual(self.oms.snapshot()['positions'],{})
        self.assertEqual(self.oms.snapshot()['cash'],'999990.00')

    def test_risk_lot_tick_and_future_signal_rejections(self):
        for kw,reason in [({'q':99},'lot'),({'limit_price':'10.001'},'tick'),
                          ({'q':10000},'risk'),({'signal_at':T},'prior signal')]:
            with self.subTest(kw=kw),self.assertRaisesRegex(ValueError,reason): self.buy(**kw)

    def test_position_and_sector_and_gross_limits(self):
        for limits, reason in [ (PortfolioLimits(max_positions=1,max_order_risk='1'),'position_limit'),
          (PortfolioLimits(max_sector_weight='.001',max_order_risk='1'),'sector_weight'),
          (PortfolioLimits(max_gross_weight='.001',max_order_risk='1'),'gross_weight')]:
            with tempfile.TemporaryDirectory() as d:
                store=EventStore(Path(d)/'j.sqlite')
                try:
                    oms=OrderService(store,self.master,opened_at=S,limits=limits)
                    oms.quote('TEST','10',T,event_id='q'); oms.quote('OTHER','10',T,event_id='q2')
                    if reason=='position_limit': oms.submit('a','TEST','BUY',100,'10','9',T,signal_at=S)
                    with self.assertRaisesRegex(ValueError,reason): oms.submit('b','OTHER','BUY',200,'10','9',T,signal_at=S)
                finally: store.close()

    def test_unknown_status_st_and_suspended_fail_closed(self):
        for m in [SecurityMaster(),SecurityMaster([fact(st=True)]),SecurityMaster([fact(suspended=True)])]:
            self.oms.master=m
            with self.assertRaises(ValueError): self.buy()

    def test_stale_quote_halts_and_monitor_idempotent(self):
        self.filled(); first=self.oms.monitor(T3,event_id='health')
        self.assertFalse(first['healthy']); self.assertTrue(self.oms.state()['halted'])
        self.assertEqual(self.oms.monitor(T3,event_id='health'),first)
        self.oms.quote('TEST','10',T3,event_id='q3')
        with self.assertRaisesRegex(ValueError,'halted'): self.buy(order_id='b2',when=T3)

    def test_quote_retry_after_new_quote_is_idempotent(self):
        self.oms.quote('TEST','11',T2,event_id='q2')
        self.oms.quote('TEST','10',T,event_id='q1')
        self.assertEqual(self.oms.state()['quotes']['TEST']['price'],Decimal(11))

    def test_reconciliation_mismatch_and_explicit_resume(self):
        self.filled()
        self.assertFalse(self.oms.reconcile({'cash':'0'},T,event_id='bad'))
        with self.assertRaises(ValueError): self.oms.resume(T,event_id='r',operator_reason='reviewed')
        self.assertTrue(self.oms.reconcile(self.oms.snapshot(),T,event_id='good'))
        self.oms.resume(T,event_id='r',operator_reason='reviewed')
        self.assertFalse(self.oms.state()['halted'])
        self.oms.resume(T,event_id='r',operator_reason='reviewed')

    def test_reconcile_retry_does_not_compare_to_new_ledger(self):
        external=self.oms.snapshot(); self.oms.reconcile(external,T,event_id='r')
        self.filled()
        self.assertTrue(self.oms.reconcile(external,T,event_id='r'))
        self.assertFalse(self.oms.state()['reconciled'])

    def test_backdated_account_mutation_rejected(self):
        self.filled(); self.oms.reconcile(self.oms.snapshot(),T2,event_id='r')
        with self.assertRaisesRegex(ValueError,'backdated'): self.buy(order_id='b2')

    def test_corporate_action_idempotent_and_invalidates_mark(self):
        self.filled()
        self.oms.corporate_action('ca','TEST','1.5','10',T2,known_at=T,source='fixture')
        self.oms.corporate_action('ca','TEST','1.5','10',T2,known_at=T,source='fixture')
        self.assertEqual(self.oms.state()['positions']['TEST'],150)
        self.assertEqual(self.oms.state()['cash'],Decimal('999005'))
        self.assertFalse(self.oms.health(T2)['healthy'])
        self.oms.quote('TEST','7',T2,event_id='q2')
        self.oms.submit('sell','TEST','SELL',150,'7','6',T2,signal_at=T)

    def test_corporate_action_requires_resolved_orders_and_exact_entitlements(self):
        self.buy()
        with self.assertRaisesRegex(ValueError,'open_orders'):
            self.oms.corporate_action('ca','TEST','1.5','10',T2,known_at=T,source='fixture')
        self.oms.fill('f','buy1',1,'10','5',T); self.oms.cancel('buy1',T,confirmed=True)
        with self.assertRaisesRegex(ValueError,'fractional'):
            self.oms.corporate_action('ca','TEST','1.5','10',T2,known_at=T,source='fixture')


class VenueTests(unittest.TestCase):
    buy=OrderTests.buy
    def setUp(self):
        OrderTests.setUp(self); self.vstore=EventStore(Path(self.tmp.name)/'venue.sqlite')
        self.venue=PaperVenue(self.vstore,opened_at=S,slippage_bps='0')
        self.bridge=PaperBridge(self.oms,self.venue)
    def tearDown(self): self.vstore.close(); OrderTests.tearDown(self)

    def test_crash_after_venue_fill_before_receive(self):
        self.buy(); self.bridge.dispatch(); self.bridge.dispatch()
        tick=OpeningTick('t1','TEST',T,'10',40,True,True)
        self.venue.match(tick); self.venue.match(tick)
        self.assertEqual(self.oms.snapshot()['positions'],{})
        self.store.close(); self.store=EventStore(self.path)
        self.oms=OrderService(self.store,self.master,opened_at=S)
        self.bridge=PaperBridge(self.oms,self.venue); self.bridge.receive(); self.bridge.receive()
        self.assertTrue(self.bridge.reconcile(T,event_id='r'))
        self.assertEqual(self.oms.snapshot()['positions'],{'TEST':40})

    def test_untradable_tick_and_limit_do_not_fill(self):
        self.buy(); self.bridge.dispatch()
        self.venue.match(OpeningTick('a','TEST',T,'10',100,False,True))
        self.venue.match(OpeningTick('b','TEST',T2,'11',100,True,True))
        self.assertEqual(self.venue.reports(),[])

    def test_venue_cancel_ack_releases_reservation(self):
        self.buy(); self.bridge.dispatch(); self.oms.cancel('buy1',T)
        self.venue.cancel('buy1',T); self.bridge.receive()
        self.assertTrue(self.bridge.reconcile(T,event_id='r'))
        self.assertEqual(reserved_cash(self.oms.state()),0)

    def test_invalid_actual_report_halts_instead_of_dropping(self):
        self.buy(fee_budget='0'); self.bridge.dispatch()
        self.venue.match(OpeningTick('a','TEST',T,'10',100,True,True))
        with self.assertRaisesRegex(ValueError,'fee_budget'): self.bridge.receive()
        self.assertTrue(self.oms.state()['halted'])
        self.assertFalse(self.bridge.reconcile(T,event_id='r'))


class RuntimeTests(unittest.TestCase):
    setUp=StoreTests.setUp
    tearDown=StoreTests.tearDown
    def test_real_strategy_prefix_restart_and_ack(self):
        bars=[replace(b,timestamp=b.timestamp.replace(hour=15,tzinfo=timezone(timedelta(hours=8)))) for b in fixture()]
        runtime=StrategyRuntime(self.store,config())
        for b in bars: runtime.on_bar(b,available_at=b.timestamp)
        observed=[e['payload'] for e in runtime.pending_intents()]
        expected=generate_system_signals(bars,config()).signals
        self.assertEqual([(e['timestamp'],e['side']) for e in observed],[(s.timestamp.isoformat(),s.side) for s in expected])
        count=len(self.store.events()); runtime.on_bar(bars[-1],available_at=bars[-1].timestamp)
        self.assertEqual(len(self.store.events()),count)
        self.store.close(); self.store=EventStore(self.path); runtime=StrategyRuntime(self.store,config())
        first=runtime.pending_intents()[0]
        runtime.acknowledge(first['event_id'],bars[-1].timestamp,disposition='FILTERED')
        self.assertEqual(len(runtime.pending_intents()),len(expected)-1)

    def test_crash_between_bar_and_intent_commit_recovers(self):
        b=replace(fixture()[0],timestamp=datetime.fromisoformat(T))
        runtime=StrategyRuntime(self.store,config())
        with patch('wavequant.application.trading.stream_runtime.generate_system_signals',side_effect=RuntimeError('crash')):
            with self.assertRaises(RuntimeError): runtime.on_bar(b,available_at=T)
        self.assertEqual([e['kind'] for e in self.store.events()],['BAR'])
        runtime.on_bar(b,available_at=T)
        self.assertEqual([e['kind'] for e in self.store.events()],['BAR','STRATEGY_EVALUATED'])

    def test_no_naive_unavailable_or_out_of_order_bar(self):
        runtime=StrategyRuntime(self.store,config())
        with self.assertRaises(ValueError): runtime.on_bar(fixture()[0],available_at=T)
        b=replace(fixture()[0],timestamp=datetime.fromisoformat(T2))
        with self.assertRaises(ValueError): runtime.on_bar(b,available_at=T)
        runtime.on_bar(b,available_at=T2)
        with self.assertRaises(ValueError): runtime.on_bar(replace(b,timestamp=datetime.fromisoformat(T)),available_at=T)

    def test_invalid_OHLC_never_commits(self):
        runtime=StrategyRuntime(self.store,config())
        b=replace(fixture()[0],timestamp=datetime.fromisoformat(T),high=1)
        with self.assertRaises(ValueError): runtime.on_bar(b,available_at=T)
        self.assertEqual(self.store.events(),[])


class IntentTests(unittest.TestCase):
    setUp=OrderTests.setUp
    tearDown=OrderTests.tearDown
    buy=OrderTests.buy

    def prepare(self, **kw):
        runtime=StrategyRuntime(self.store,config())
        p=dict(strategy_version=runtime.version,symbol='TEST',side='LONG',timestamp=S,
               reference_price=20,invalidation_price=18,target_price=24,minimum_reward_risk=0)
        p.update(kw)
        with self.store.transaction(): self.store.append('intent:test','SIGNAL_INTENT',S,p)
        return runtime,PaperIntentExecutor(runtime,self.oms)

    def execute(self, executor, **kw):
        args=dict(when=T,raw_open='10',adjustment_factor='2',expires_on='2026-01-01')
        args.update(kw)
        return executor.execute('intent:test',**args)

    def test_explicit_adjusted_to_raw_conversion_and_lot_sizing(self):
        runtime,executor=self.prepare(); result=self.execute(executor)
        self.assertEqual(result['disposition'],'ORDERED')
        order=self.oms.state()['orders'][result['order_id']]
        self.assertEqual(order['stop_price'],'9'); self.assertEqual(order['limit_price'],'10.01')
        self.assertEqual(order['quantity']%100,0)
        self.assertLessEqual(Decimal('1.01')*order['quantity']+20,Decimal(5000))
        self.assertEqual(self.execute(executor),result)
        self.assertFalse(runtime.pending_intents())

    def test_crash_after_acceptance_before_ack_recovers_one_order(self):
        runtime,executor=self.prepare()
        with patch.object(runtime,'acknowledge',side_effect=RuntimeError('crash')):
            with self.assertRaises(RuntimeError): self.execute(executor)
        self.assertEqual(len(self.oms.state()['orders']),1)
        self.assertEqual(self.execute(executor)['disposition'],'ORDERED')
        self.assertEqual(len(self.oms.state()['orders']),1)

    def test_rejected_entry_not_silently_retried_after_restart(self):
        runtime,executor=self.prepare(minimum_reward_risk=99)
        with patch.object(runtime,'acknowledge',side_effect=RuntimeError('crash')):
            with self.assertRaises(RuntimeError): self.execute(executor)
        self.assertFalse(self.oms.state()['orders'])
        self.assertEqual(self.execute(executor)['disposition'],'FILTERED')

    def test_stale_entry_expired_without_order(self):
        _,executor=self.prepare()
        self.assertEqual(self.execute(executor,when=T2)['disposition'],'EXPIRED')
        self.assertFalse(self.oms.state()['orders'])

    def test_gap_and_no_same_session_execution(self):
        _,executor=self.prepare()
        with self.assertRaises(ValueError): self.execute(executor,when='2025-12-31T16:00:00+08:00')
        result=self.execute(executor,raw_open='11')
        self.assertEqual(result['reason'],'entry_gap_limit')

    def test_ack_wrong_order_is_rejected(self):
        runtime,executor=self.prepare()
        self.oms.quote('OTHER','10',T,event_id='qother')
        self.oms.submit('other','OTHER','BUY',100,'10','9',T,signal_at=S)
        with self.assertRaisesRegex(ValueError,'match intent'):
            runtime.acknowledge('intent:test',T,disposition='ORDERED',order_id='other')

    def test_exit_with_no_position_consumed_as_observation(self):
        _,executor=self.prepare(side='EXIT')
        self.assertEqual(self.execute(executor)['disposition'],'RISK_EXIT_OBSERVED')

    def test_same_day_position_exit_deferred_not_lost(self):
        OrderTests.filled(self)
        _,executor=self.prepare(side='EXIT')
        result=self.execute(executor)
        self.assertEqual(result['disposition'],'DEFERRED')
        self.assertIsNone(self.store.get('ack:intent:test'))
        self.oms.quote('TEST','10',T2,event_id='q2')
        self.assertEqual(self.execute(executor,when=T2)['disposition'],'ORDERED')


class ValidationTests(unittest.TestCase):
    def test_folds_order_gap_and_nonoverlap(self):
        days=[(datetime(2020,1,1)+timedelta(days=i)).date().isoformat() for i in range(50)]
        folds=walk_forward_folds(days,train=10,validation=5,test=5,gap=2)
        self.assertEqual(len(folds),6)
        for f in folds:
            self.assertLess(f['train'][1],f['validation'][0]); self.assertLess(f['validation'][1],f['test'][0])
            self.assertEqual(days.index(f['test'][0])-days.index(f['validation'][1]),3)
        self.assertTrue(all(a['test'][1]<b['test'][0] for a,b in zip(folds,folds[1:])))
        with self.assertRaises(ValueError): walk_forward_folds(days[::-1])

    def test_no_annotations_is_unknown_and_recall_is_measured(self):
        self.assertIsNone(audit_annotations([],[])['recall'])
        one=dict(symbol='TEST',timestamp=T,event='up_n'); two=dict(one,event='bull')
        result=audit_annotations([one,two],[one])
        self.assertEqual(result['recall'],.5); self.assertEqual(result['precision'],1)

    def test_registry_fingerprints_inputs_and_outputs(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'input.json'; p.write_text('{}',encoding='utf-8')
            store=EventStore(Path(d)/'j.sqlite'); registry=ExperimentRegistry(store)
            try:
                registry.start('run',T,config={},inputs={str(p):fingerprint(p)},environment={})
                with self.assertRaises(ValueError): registry.start('run',T,config={},inputs={str(p):fingerprint(p)},environment={})
                registry.finish('run',T2,artifacts=[p]); self.assertTrue(all(registry.verify_artifacts('run').values()))
                p.write_text('{"changed":true}',encoding='utf-8')
                self.assertFalse(all(registry.verify_artifacts('run').values()))
            finally: store.close()

    def test_catalog_missing_facts_does_not_pass_readiness(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'bars.csv'
            p.write_text('timestamp,symbol,open,high,low,close,volume\n2026-01-01,TEST,10,11,9,10,100\n',encoding='utf-8')
            p.with_suffix('.metadata.json').write_text(json.dumps({'sha256':fingerprint(p)}),encoding='utf-8')
            store=EventStore(Path(d)/'j.sqlite')
            try:
                manifest=register_dataset(store,p,T2)
                self.assertFalse(manifest['data_ready']); self.assertEqual(manifest['unknown_security_rows'],1)
                manifest=register_dataset(store,p,T2,master=SecurityMaster([fact()]),calendar=['2026-01-01'])
                self.assertTrue(manifest['data_ready']); self.assertFalse(manifest['strict_polyline_ready'])
            finally: store.close()
