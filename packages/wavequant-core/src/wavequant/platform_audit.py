"""Runnable offline engineering acceptance and real-data capability audit."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from .data import dump_json, fingerprint
from .data_catalog import register_dataset
from .event_store import EventStore
from .experiment_registry import ExperimentRegistry, environment_snapshot
from .integrated_strategy import SystemStrategy
from .intent_execution import PaperIntentExecutor
from .model import Bar
from .order_service import OrderService
from .paper_venue import PaperVenue, PaperBridge, OpeningTick
from .project_paths import PROJECT_ROOT
from .security_master import SecurityFact, SecurityMaster
from .stream_runtime import StrategyRuntime
from .validation_suite import run_validation_suite


def now(): return datetime.now(timezone.utc).isoformat()


def run_paper_demo(output):
    """Known-answer plumbing fixture, NOT the production strategy or a backtest."""
    output=Path(output)
    if output.exists() and any(output.iterdir()): raise ValueError('paper demo output must be empty')
    output.mkdir(parents=True,exist_ok=True)
    zone=timezone(timedelta(hours=8))
    rows=[(10,11,9,10),(11,12,10,11),(10,11,8,9),(9,10,7,8),
          (12,13,11.5,12.5),(11,12,10,11),(12,13,11,12.8),
          (13,14,12,14),(14,15,13,15),(15,16,14,16)]
    bars=[Bar(datetime(2026,1,1,15,tzinfo=zone)+timedelta(days=i),'FIXTURE',*r,
              2000000 if i==7 else 1000000) for i,r in enumerate(rows)]
    opened='2025-12-31T15:00:00+08:00'
    master=SecurityMaster([SecurityFact('FIXTURE','2020-01-01',None,'2019-12-31T15:00:00+08:00',
        'synthetic',True,False,False,100,'.01',1,'engineering_fixture_not_market_metadata')])
    # Deliberately a minimal N/regime fixture, no claim to demonstrate the
    # transitioned-squeeze strategy's empirical profitability or signal count.
    cfg=replace(SystemStrategy(),volume_lookback=3,minimum_reward_risk=0,
                entry_policy='legacy_n_continuation',preflight_reward_risk=False,squeeze_pullback_entries=False)
    store=EventStore(output/'account.sqlite'); vstore=EventStore(output/'venue.sqlite')
    try:
        oms=OrderService(store,master,opened_at=opened)
        runtime=StrategyRuntime(store,cfg)
        for b in bars: runtime.on_bar(b,available_at=b.timestamp)
        longs=[e for e in runtime.pending_intents() if e['payload']['side']=='LONG']
        if len(longs)!=1: raise AssertionError('fixture must emit exactly one canonical N/regime long')
        when='2026-01-11T09:30:00+08:00'; intent=longs[0]['event_id']
        oms.quote('FIXTURE','16',when,event_id='quote:entry')
        result=PaperIntentExecutor(runtime,oms).execute(intent,when=when,raw_open='16',adjustment_factor='1',
                    fee_budget='40',expires_on='2026-01-11')
        if result['disposition']!='ORDERED': raise AssertionError(result)
        oid=result['order_id']; qty=oms.state()['orders'][oid]['quantity']
        venue=PaperVenue(vstore,opened_at=opened); bridge=PaperBridge(oms,venue)
        bridge.dispatch(); bridge.dispatch()
        opening=OpeningTick('entry-half','FIXTURE',when,'16',qty//2,True,True)
        venue.match(opening); venue.match(opening)
        if oms.snapshot()['positions']: raise AssertionError('local ledger changed before fill delivery')
        # Simulate process loss AFTER venue commit and BEFORE local fill delivery.
        store.close(); store=EventStore(output/'account.sqlite')
        oms=OrderService(store,master,opened_at=opened); bridge=PaperBridge(oms,venue)
        bridge.receive(); snapshot_once=oms.snapshot(); bridge.receive()
        if oms.snapshot()!=snapshot_once: raise AssertionError('duplicate delivery changed ledger')
        first_match=bridge.reconcile(when,event_id='reconcile:partial')
        when2='2026-01-12T09:30:00+08:00'
        oms.quote('FIXTURE','16',when2,event_id='quote:remainder')
        venue.match(OpeningTick('entry-rest','FIXTURE',when2,'16',qty,True,True)); bridge.receive()
        second_match=bridge.reconcile(when2,event_id='reconcile:full')
        # Explicit engineering liquidation; not a synthetic theory EXIT label.
        when3='2026-01-13T09:30:00+08:00'
        oms.quote('FIXTURE','17',when3,event_id='quote:exit')
        oms.submit('fixture-liquidation','FIXTURE','SELL',qty,'16.98','12',when3,
                   signal_at='2026-01-12T15:00:00+08:00',fee_budget='40')
        bridge.dispatch(); venue.match(OpeningTick('exit','FIXTURE',when3,'17',qty,True,True)); bridge.receive()
        final_match=bridge.reconcile(when3,event_id='reconcile:flat')
        if not all((first_match,second_match,final_match)) or oms.snapshot()['positions']:
            raise AssertionError('engineering account/venue reconciliation failed')
        health=oms.monitor(when3,event_id='health:final')
        store.backup(output/'account.backup.sqlite')
        restored=EventStore(output/'account.backup.sqlite')
        try: backup_match=restored.verify()==store.verify()
        finally: restored.close()
        report=dict(kind='synthetic_engineering_acceptance_not_strategy_returns',
            checks=dict(canonical_signal_to_order=True,partial_fills=True,restart_recovery=True,
                        duplicate_delivery=True,independent_reconciliation=final_match,T_plus_1_exit=True,backup_restore=backup_match),
            quantity=qty,snapshot=oms.snapshot(),venue_snapshot=venue.snapshot(),health=health,
            account_head=store.verify(),venue_head=vstore.verify(),
            limitations=['Minimal legacy N/regime fixture only; user production entry policy is unchanged.',
                         'Synthetic calendar/quotes/metadata; price path does not establish trading edge.',
                         'Venue rates are explicit scenario assumptions, not a current broker tariff.'])
        dump_json(output/'report.json',report)
        return report
    finally:
        store.close(); vstore.close()


def run_platform_audit(csv_path, protocol_path, output, tests, *, master_path=None, calendar_path=None,
                       with_research=True):
    output=Path(output); csv_path=Path(csv_path).resolve(); protocol_path=Path(protocol_path).resolve()
    if tests.get('status')!='passed': raise ValueError('engineering tests must pass')
    if output.exists() and any(p.name!='test_log.txt' for p in output.iterdir()):
        raise ValueError('use a new output directory')
    output.mkdir(parents=True,exist_ok=True)
    master=SecurityMaster.load(master_path) if master_path else SecurityMaster()
    calendar=json.loads(Path(calendar_path).read_text(encoding='utf-8')) if calendar_path else None
    if calendar is not None and (not isinstance(calendar,list) or not all(isinstance(d,str) for d in calendar)):
        raise ValueError('calendar must be a JSON array of dated exchange sessions')
    store=EventStore(output/'registry.sqlite'); registry=ExperimentRegistry(store); run_id='platform-v1'
    started=False
    try:
        root=PROJECT_ROOT
        inputs={str(p):fingerprint(p) for p in (csv_path,csv_path.with_suffix('.metadata.json'),protocol_path)}
        for path in (master_path,calendar_path):
            if path: inputs[str(Path(path).resolve())]=fingerprint(Path(path))
        environment=environment_snapshot(root)
        registry.start(run_id,now(),config=dict(protocol=str(protocol_path),with_research=with_research),
                       inputs=inputs,environment=environment); started=True
        dump_json(output/'environment.json',environment)
        manifest=register_dataset(store,csv_path,now(),master=master,calendar=calendar)
        dump_json(output/'dataset_manifest.json',manifest)
        paper=run_paper_demo(output/'paper')
        research=run_validation_suite(csv_path,protocol_path,output/'validation') if with_research else None
        capabilities={
            'durable_paper_oms':all(paper['checks'].values()),
            'causal_strategy_reuse':True,'risk_and_reconciliation':True,
            'data_manifest_and_PIT_interface':True,'real_PIT_security_data':manifest['checks']['point_in_time_security_coverage'],
            'official_calendar_input':manifest['checks']['exchange_calendar_available'],
            'real_minor_timeframe_data':False,'historical_universe_complete':False,
            'independent_human_event_annotations':False,'independent_holdout':False,
            'real_broker_adapter':False,'live_order_permission':False,
            'rolling_diagnostics_executed':research is not None,
        }
        report=dict(version='0.3.0',engineering_tests=tests,data=manifest,capabilities=capabilities,
                    paper=paper,production_ready=False,live_orders_enabled=False,
                    research_report='validation/report.json' if research else None,
                    unresolved=[k for k,v in capabilities.items() if not v])
        dump_json(output/'report.json',report)
        lines=['# 量化平台工程验收与通达信数据审计','',
               '结论：离线研究／纸面执行基础设施已增加；实盘就绪：否。没有连接真实券商，也没有修改默认轧空策略。','',
               f"数据：{manifest['rows']:,} 条，{len(manifest['symbols'])} 只，{manifest['start']} 至 {manifest['end']}。",
               f"缺少当时可知证券状态的行情：{manifest['unknown_security_rows']:,} 条。",'',
               '## 能力验收','', '| 能力 | 当前状态 |','|---|---|']
        lines += [f'| {k} | {"通过／存在" if v else "未就绪／缺失"} |' for k,v in capabilities.items()]
        lines += ['','## 工程验证','',
                  '真实理论函数产生教学信号 → 风险计算 → 持久订单 → 独立模拟场所部分成交 → 模拟中断 → 重启接收回报 → T+1 清仓 → 账户对账 → 备份恢复。',
                  '这条教学路径不是生产策略收益证明，日历、行情和证券元数据均为合成夹具。','']
        if research:
            lines += ['## 原策略的真实历史诊断','',
                      '原 v4 参数不变；全历史及滚动窗口均为已看过历史的诊断，没有挑选赢家。',
                      '', '| 规则 | 场景 | 全历史净收益 | 平仓笔数 | 执行证据 |', '|---|---|---:|---:|---|']
            for name,v in research['variants'].items():
                for scenario,s in v['scenarios'].items():
                    m=s['metrics']
                    lines.append(f"| {name} | {scenario} | {m['total_return']:.4%} | {m['trades']} | {s['diagnostics']['status']} |")
            lines += ['',f"滚动诊断 {len(research['folds'])} 个窗口；每窗独立空仓起始，不能拼成实盘净值。",
                      '收益置信区间仅为探索性重采样，不校正多重试验；交易样本不足、无独立人工标注和锁箱，不能证明策略有效。']
        lines += ['','## 后续必须补齐的外部证据','',
                  '- 历史证券状态、退市样本与当时股票池；官方日历；内外包所需真实次级行情。',
                  '- 独立人工标注的理论事件样本；未用于调参的新数据；真实券商沙箱与成交／公司行动接口。',
                  '- 网络故障、吞吐与长时间运行验收；当前实现是单账户、事件重放优先的本地基础设施。','',
                  '接口契约和恢复流程见项目 docs/platform_contract.md。历史结果未覆盖。']
        (output/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
        artifacts=[p for p in output.rglob('*') if p.is_file() and '.sqlite' not in p.name]
        registry.finish(run_id,now(),artifacts=artifacts)
        if not all(registry.verify_artifacts(run_id).values()): raise AssertionError('artifact fingerprint mismatch')
        store.backup(output/'registry.backup.sqlite')
        return report
    except BaseException as exc:
        if started and store.get('experiment:end:'+run_id) is None:
            registry.finish(run_id,now(),artifacts=[],status='FAILED',error=f'{type(exc).__name__}: {exc}')
        raise
    finally: store.close()
