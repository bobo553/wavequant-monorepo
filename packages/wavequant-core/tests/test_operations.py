from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from wavequant.infrastructure.filesystem.backup_bundle import create_bundle, restore_bundle, verify_bundle
from wavequant.infrastructure.market_data.data import dump_json, write_dataset, fingerprint
from wavequant.infrastructure.market_data.data_catalog import register_dataset
from wavequant.infrastructure.persistence.event_store import EventStore
from wavequant.infrastructure.persistence.operational_store import (workspace_lock, identifier, acknowledge_alert, synchronize_alerts)
from wavequant.application.governance.operations import OperationsConfig, run_operations, operations_status, check_freshness
from wavequant.infrastructure.market_data.io import load_bars
from wavequant.infrastructure.market_data.security_master import SecurityMaster
from tests.test_platform import fact, T, T2, T3, S
from wavequant.application.trading.order_service import OrderService
from wavequant.application.trading.paper_venue import PaperVenue, PaperBridge, OpeningTick


def passed(output):
    (output/'test_log.txt').write_text('Injected unit fixture checker; not a full checkout test run.',encoding='utf-8')
    return dict(status='passed',scope='test_fixture_only')


class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.base=Path(self.tmp.name)
        self.csv=self.base/'daily.csv'
        write_dataset(self.csv,[dict(timestamp=f'2026-01-{i:02}',symbol='TEST',open=10,high=11,low=9,
                                     close=10,volume=100000,buyable=1,sellable=1,adjustment_factor=1)
                               for i in range(1,7)],dict(kind='synthetic_test_fixture'))
        self.protocol=self.base/'protocol.json'; dump_json(self.protocol,{'not_used_in_engineering_only':True})
        self.cfg=OperationsConfig(root=self.base/'ops',csv=self.csv,protocol=self.protocol,run_research=False)

    def tearDown(self): self.tmp.cleanup()

    def test_ids_reject_path_traversal_and_reserved_syntax(self):
        for invalid in ('../x','/root','C:x','','x y','a.b','x'*81,'NUL','con','COM1'):
            with self.assertRaises(ValueError): identifier(invalid)

    def test_process_lock_and_release(self):
        with workspace_lock(self.cfg.root):
            with self.assertRaises(RuntimeError):
                with workspace_lock(self.cfg.root): pass
        with workspace_lock(self.cfg.root): pass

    def test_status_available_while_writer_owns_lock(self):
        store=EventStore(self.cfg.root/'operations.sqlite')
        try:
            with workspace_lock(self.cfg.root),store.transaction():
                store.append('pending','EXAMPLE',S,{})
                # Uncommitted changes are invisible; reader does not block.
                self.assertEqual(operations_status(self.cfg.root)['runs'],{})
        finally: store.close()

    def test_readonly_journal_does_not_initialize_or_allow_writes(self):
        path=self.base/'readonly.sqlite'
        with self.assertRaises(ValueError): EventStore(path,readonly=True)
        self.assertFalse(path.exists())
        store=EventStore(path)
        with store.transaction(): store.append('event','TEST',T,{})
        store.close(); before=fingerprint(path)
        reader=EventStore(path,readonly=True)
        try:
            with self.assertRaises(RuntimeError):
                with reader.transaction(): pass
            self.assertEqual(len(reader.events()),1)
        finally: reader.close()
        self.assertEqual(fingerprint(path),before)

    def test_config_rejects_live_credentials_unknown_keys_and_moving_inputs(self):
        with self.assertRaises(ValueError): replace(self.cfg,mode='live')
        with self.assertRaises(ValueError): replace(self.cfg,refresh_tdx=True)
        with self.assertRaises(ValueError): replace(self.cfg,csv=self.cfg.root/'nested.csv')
        path=self.base/'ops.json'
        dump_json(path,dict(root='ops',csv='daily.csv',protocol='protocol.json',token='secret'))
        with self.assertRaises(ValueError): OperationsConfig.load(path)
        dump_json(path,dict(root='ops',csv='daily.csv',protocol='protocol.json',run_research=False))
        self.assertEqual(OperationsConfig.load(path),self.cfg)

    def test_complete_recovery_and_idempotent_run(self):
        report=run_operations(self.cfg,'acceptance',test_runner=passed)
        self.assertTrue(report['recovery_drill_passed'])
        self.assertFalse(report['live_orders_enabled']); self.assertFalse(report['strategy_validated'])
        self.assertIsNone(report['research_summary'])
        with patch('wavequant.application.governance.operations._test_checkout',side_effect=AssertionError('must not rerun')):
            self.assertEqual(report,run_operations(self.cfg,'acceptance'))
        status=operations_status(self.cfg.root)
        self.assertEqual(status['runs']['acceptance']['status'],'COMPLETED')
        self.assertTrue(status['artifact_integrity']['acceptance'])
        self.assertTrue(any(a['code']=='historical_security_data' for a in status['alerts']))
        with self.assertRaisesRegex(ValueError,'conflict'):
            run_operations(replace(self.cfg,run_research=True),'acceptance',test_runner=passed)

    def test_failed_run_preserved_and_new_attempt_allowed(self):
        with self.assertRaisesRegex(ValueError,'engineering gate'):
            run_operations(self.cfg,'failed',test_runner=lambda _:dict(status='failed'))
        self.assertTrue((self.cfg.root/'runs/failed/failure.json').is_file())
        with self.assertRaisesRegex(ValueError,'already used'):
            run_operations(self.cfg,'failed',test_runner=passed)
        run_operations(self.cfg,'retry',test_runner=passed)
        status=operations_status(self.cfg.root)
        self.assertEqual(status['runs']['failed']['status'],'FAILED')
        self.assertFalse(any(a['active'] for a in status['alerts'] if a['code']=='pipeline_failed'))

    def test_unfinished_previous_worker_marked_interrupted(self):
        store=EventStore(self.cfg.root/'operations.sqlite')
        with store.transaction():
            store.append('run:old','RUN_STARTED',S,dict(run_id='old',config_hash='fixture',config={}))
        store.close()
        run_operations(self.cfg,'new',test_runner=passed)
        self.assertEqual(operations_status(self.cfg.root)['runs']['old']['status'],'INTERRUPTED')

    def test_report_tamper_detected_not_reused(self):
        run_operations(self.cfg,'tamper',test_runner=passed)
        path=self.cfg.root/'runs/tamper/report.md'; path.write_text('modified',encoding='utf-8')
        self.assertFalse(operations_status(self.cfg.root)['artifact_integrity']['tamper'])
        with self.assertRaisesRegex(ValueError,'integrity'):
            run_operations(self.cfg,'tamper',test_runner=passed)

    def test_input_mutation_during_run_fails_sealing(self):
        def mutate(output):
            dump_json(self.protocol,dict(changed=True))
            return passed(output)
        with self.assertRaisesRegex(ValueError,'input changed'):
            run_operations(self.cfg,'mutation',test_runner=mutate)

    def test_bad_provenance_fails_closed(self):
        metadata=self.csv.with_suffix('.metadata.json')
        dump_json(metadata,{'sha256':'incorrect'})
        with self.assertRaisesRegex(ValueError,'hash mismatch'):
            run_operations(self.cfg,'bad_data',test_runner=passed)

    def test_future_data_fails_closed(self):
        with patch('wavequant.application.governance.operations.now',return_value=T):
            with self.assertRaisesRegex(ValueError,'not-yet-available'):
                run_operations(self.cfg,'future_data',test_runner=passed)

    def test_full_research_branch_dispatch(self):
        research=dict(variants={'strict_full':{'scenarios':{'base':{'metrics':{'total_return':0,'trades':0}}}}})
        with patch('wavequant.application.analytics.validation_suite.run_validation_suite',return_value=research) as run:
            report=run_operations(replace(self.cfg,run_research=True),'research',test_runner=passed)
        run.assert_called_once()
        self.assertEqual(report['research_summary']['strict_full']['base']['trades'],0)
        self.assertFalse(report['strategy_validated'])

    def test_unknown_and_stale_freshness_with_calendar_cutoffs(self):
        grouped=load_bars(self.csv)
        self.assertEqual(check_freshness(grouped,None,T)['status'],'UNKNOWN')
        days=[f'2026-01-{i:02}' for i in range(1,10)]
        self.assertEqual(check_freshness(grouped,days,'2026-01-07T14:00:00+08:00')['status'],'CURRENT')
        self.assertEqual(check_freshness(grouped,days,'2026-01-07T15:00:00+08:00')['status'],'STALE')
        self.assertEqual(check_freshness(grouped,days,'2026-01-10T15:00:00+08:00')['status'],'UNKNOWN')
        with self.assertRaises(ValueError): check_freshness(grouped,days+days,T)

    def test_calendar_outside_snapshot_not_false_missing(self):
        store=EventStore(self.base/'catalog.sqlite')
        try:
            calendar=['2025-12-31']+[f'2026-01-{i:02}' for i in range(1,9)]
            result=register_dataset(store,self.csv,'2026-01-09T16:00:00+08:00',
                                    master=SecurityMaster([fact()]),calendar=calendar)
            self.assertEqual(result['missing_expected_sessions'],[])
        finally: store.close()


class AlertTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.store=EventStore(Path(self.tmp.name)/'ops.sqlite')
    def tearDown(self): self.store.close(); self.tmp.cleanup()
    def test_dedup_ack_resolution_and_recurrence(self):
        obs=[dict(code='data',severity='critical',message='missing data')]
        a=synchronize_alerts(self.store,'health',obs,T)[0]
        self.assertEqual(len(synchronize_alerts(self.store,'health',obs,T2)),1)
        self.assertEqual(len(self.store.events()),1)
        ack=acknowledge_alert(self.store,a['alert_id'],'reviewed',T2)
        self.assertTrue(ack['active']); self.assertTrue(ack['acknowledged'])
        self.assertEqual(synchronize_alerts(self.store,'health',[],T3),[])
        new=synchronize_alerts(self.store,'health',obs,T3)[0]
        self.assertNotEqual(new['alert_id'],a['alert_id'])
        self.assertFalse(new['acknowledged'])
    def test_unknown_ack_and_duplicate_codes_rejected(self):
        with self.assertRaises(ValueError): acknowledge_alert(self.store,'missing','review',T)
        obs=dict(code='data',severity='critical',message='missing')
        with self.assertRaises(ValueError): synchronize_alerts(self.store,'x',[obs,obs],T)


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.base=Path(self.tmp.name)
        self.source=self.base/'source'; self.source.mkdir()
        (self.source/'notes.txt').write_text('original',encoding='utf-8')
    def tearDown(self): self.tmp.cleanup()
    def test_wal_snapshot_restore_and_no_overwrite(self):
        store=EventStore(self.source/'account.sqlite')
        try:
            with store.transaction(): store.append('a','TEST',T,{'q':3})
            head=store.verify(); bundle=self.base/'backup'
            create_bundle(self.source,bundle)
        finally: store.close()
        restored=self.base/'restored'; result=restore_bundle(bundle,restored)
        self.assertTrue(result['verified'])
        store=EventStore(restored/'account.sqlite')
        try: self.assertEqual(store.verify(),head)
        finally: store.close()
        with self.assertRaises(ValueError): restore_bundle(bundle,restored)
        with self.assertRaises(ValueError): create_bundle(self.source,bundle)
        with self.assertRaises(ValueError): create_bundle(self.source,self.source/'nested')
    def test_tamper_extra_files_and_traversal_rejected(self):
        bundle=self.base/'backup'; create_bundle(self.source,bundle)
        (bundle/'payload/extra').write_text('x',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'unregistered'): verify_bundle(bundle)
        for path in ('../outside','C:/outside','/outside','a\\b'):
            dump_json(bundle/'manifest.json',dict(schema=1,files={path:'bad'}))
            with self.assertRaises(ValueError): verify_bundle(bundle)
    def test_wrong_hash_does_not_create_restore_target(self):
        bundle=self.base/'backup'; create_bundle(self.source,bundle)
        (bundle/'payload/notes.txt').write_text('tampered',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'integrity'): restore_bundle(bundle,self.base/'restored')
        self.assertFalse((self.base/'restored').exists())

    def test_checkpointed_sealed_journal_keeps_byte_fingerprint(self):
        path=self.source/'sealed.sqlite'; store=EventStore(path)
        with store.transaction(): store.append('event','TEST',T,{'value':7})
        store.close(); expected=fingerprint(path)
        # Read-only readers may leave empty WAL sidecars. They are not frames.
        reader=EventStore(path,readonly=True); reader.close()
        bundle=self.base/'backup'; create_bundle(self.source,bundle)
        self.assertEqual(fingerprint(bundle/'payload/sealed.sqlite'),expected)
        restore_bundle(bundle,self.base/'restored')
        self.assertEqual(fingerprint(self.base/'restored/sealed.sqlite'),expected)

    def test_whole_operations_restore_preserves_sealed_run_integrity(self):
        csv=self.base/'daily.csv'
        write_dataset(csv,[dict(timestamp='2026-01-01',symbol='TEST',open=10,high=11,low=9,
                               close=10,volume=100000,buyable=1,sellable=1,adjustment_factor=1)],
                      dict(kind='synthetic'))
        protocol=self.base/'protocol.json'; dump_json(protocol,{'fixture':True})
        cfg=OperationsConfig(root=self.base/'ops',csv=csv,protocol=protocol,run_research=False)
        run_operations(cfg,'whole',test_runner=passed)
        create_bundle(cfg.root,self.base/'backup')
        restore_bundle(self.base/'backup',self.base/'restored')
        self.assertTrue(operations_status(self.base/'restored')['artifact_integrity']['whole'])


class VenueLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); base=Path(self.tmp.name)
        self.s=EventStore(base/'oms.sqlite'); self.v=EventStore(base/'venue.sqlite')
        self.oms=OrderService(self.s,SecurityMaster([fact()]),opened_at=S)
        self.venue=PaperVenue(self.v,opened_at=S); self.bridge=PaperBridge(self.oms,self.venue)
        self.oms.quote('TEST','10',T,event_id='quote')
        self.oms.submit('buy','TEST','BUY',200,'10.01','9',T,signal_at=S,fee_budget='40')
        self.bridge.dispatch()
    def tearDown(self): self.s.close(); self.v.close(); self.tmp.cleanup()
    def test_cancel_outbox_roundtrip_idempotent(self):
        self.oms.cancel('buy',T2); self.bridge.dispatch(); self.bridge.dispatch()
        self.bridge.receive(); self.bridge.receive()
        self.assertEqual(self.oms.state()['orders']['buy']['status'],'CANCELED')
        self.assertTrue(self.bridge.reconcile(T2,event_id='reconcile'))
    def test_partial_then_venue_reject_keeps_shares(self):
        self.venue.match(OpeningTick('partial','TEST',T,'10',100,True,True)); self.bridge.receive()
        self.venue.reject('buy',T2,reason='fixture venue rejection')
        self.bridge.receive(); self.bridge.receive()
        self.assertEqual(self.oms.snapshot()['positions'],{'TEST':100})
        self.assertEqual(self.oms.state()['orders']['buy']['status'],'REJECTED')
        self.assertTrue(self.bridge.reconcile(T2,event_id='reconcile'))
    def test_independent_corporate_action_reconcile_and_restart(self):
        self.venue.match(OpeningTick('fill','TEST',T,'10',200,True,True)); self.bridge.receive()
        for target in (self.oms,self.venue):
            target.corporate_action('split','TEST','2','20',T2,known_at=S,source='synthetic entitlement')
            target.corporate_action('split','TEST','2','20',T2,known_at=S,source='synthetic entitlement')
        self.assertEqual(self.oms.snapshot()['positions'],{'TEST':400})
        self.assertTrue(self.bridge.reconcile(T2,event_id='action-reconcile'))
        self.assertNotIn('TEST',self.oms.state()['quotes'])
    def test_corporate_action_pending_and_unknown_rejected(self):
        with self.assertRaises(ValueError):
            self.venue.corporate_action('split','TEST','2','20',T2,known_at=S,source='fixture')
        self.venue.reject('buy',T2,reason='no fill')
        with self.assertRaises(ValueError): self.venue.reject('unknown',T3,reason='unknown')

    def test_fifo_report_asof_stale_marks_and_conservation(self):
        from wavequant.application.trading.account_report import account_report
        self.venue.match(OpeningTick('fill','TEST',T,'10',200,True,True)); self.bridge.receive()
        report=account_report(self.s,T)
        self.assertTrue(report['pnl_conserved'])
        self.assertEqual(report['total_pnl'],'-7.00')
        self.assertEqual(account_report(self.s,S)['symbols'],[])
        for target in (self.oms,self.venue):
            target.corporate_action('split','TEST','2','20',T2,known_at=S,source='synthetic entitlement')
        self.assertIsNone(account_report(self.s,T2)['equity'])
        self.oms.quote('TEST','5.1',T2,event_id='post-split')
        self.assertEqual(account_report(self.s,T2)['total_pnl'],'53.00')
        self.oms.submit('sell','TEST','SELL',400,'5.09','4',T3,signal_at=T2,fee_budget='40')
        self.bridge.dispatch()
        self.venue.match(OpeningTick('sell-fill','TEST',T3,'5.1',400,True,True)); self.bridge.receive()
        flat=account_report(self.s,T3)
        self.assertTrue(flat['pnl_conserved']); self.assertEqual(flat['symbols'][0]['quantity'],0)
        self.assertEqual(flat['total_pnl'],'42.98')
        self.assertEqual(flat['realized_pnl'],'22.98')

    def test_fifo_report_stale_mark_no_false_nav(self):
        from wavequant.application.trading.account_report import account_report
        self.venue.match(OpeningTick('fill','TEST',T,'10',200,True,True)); self.bridge.receive()
        self.assertIsNone(account_report(self.s,T3)['equity'])
        self.assertEqual(account_report(self.s,T3)['unmarked_symbols'],['TEST'])


if __name__=='__main__': unittest.main()
