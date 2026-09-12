"""Coordinate the one-shot offline operations pipeline.

    Freeze inputs -> tests -> data health -> theory/backtest -> paper acceptance
    -> readiness report -> sealed artifacts -> verified backup/restore drill.

Every invocation is explicit. Automatic scheduling and external notifications
are intentionally not installed by this module.
"""
from dataclasses import dataclass
from datetime import date, datetime, time
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from zoneinfo import ZoneInfo

from wavequant.infrastructure.filesystem.backup_bundle import create_bundle, restore_bundle
from wavequant.infrastructure.market_data.data import dump_json, fingerprint
from wavequant.infrastructure.market_data.data_catalog import register_dataset
from wavequant.infrastructure.persistence.event_store import EventStore, canonical
from wavequant.infrastructure.persistence.experiment_registry import environment_snapshot
from wavequant.infrastructure.market_data.io import load_bars
from wavequant.infrastructure.persistence.operational_store import (workspace_lock, identifier, now, run_states,
                                                alert_states, synchronize_alerts)
from wavequant.infrastructure.filesystem.project_paths import PROJECT_ROOT
from wavequant.infrastructure.market_data.security_master import SecurityMaster


@dataclass(frozen=True)
class OperationsConfig:
    root: Path
    csv: Path
    protocol: Path
    security_master: Path | None = None
    calendar: Path | None = None
    refresh_tdx: bool = False
    tdx_root: Path | None = None
    symbols: tuple[str, ...] = ()
    start: str = '2018-01-01'
    end: str | None = None
    run_research: bool = True
    mode: str = 'offline'

    def __post_init__(self):
        if self.mode != 'offline': raise ValueError('only offline operations are supported; no live adapter')
        if type(self.refresh_tdx) is not bool or type(self.run_research) is not bool:
            raise ValueError('explicit boolean pipeline switches required')
        date.fromisoformat(self.start)
        if self.end is not None and date.fromisoformat(self.end) < date.fromisoformat(self.start):
            raise ValueError('end precedes start')
        if self.refresh_tdx and (not self.tdx_root or not self.symbols or not self.end):
            raise ValueError('TDX refresh requires root, frozen symbols and explicit end')
        if len(set(self.symbols)) != len(self.symbols) or any(not isinstance(s, str) or not s for s in self.symbols):
            raise ValueError('unique explicit symbols required')
        if self.refresh_tdx and any(not re.fullmatch(r'(sh\.60\d{4}|sz\.00\d{4})',s) for s in self.symbols):
            raise ValueError('TDX refresh supports established SH/SZ main-board identifiers only')
        for p in (self.csv, self.protocol, self.security_master, self.calendar, self.tdx_root):
            if p is not None and p.resolve().is_relative_to(self.root.resolve()):
                raise ValueError('input files must live outside the operations output root')

    def serializable(self):
        return {key: (str(value.resolve()) if isinstance(value, Path) else list(value) if key == 'symbols' else value)
                for key, value in self.__dict__.items()}

    @classmethod
    def load(cls, path):
        path = Path(path).resolve()
        raw = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(raw, dict) or set(raw) - set(cls.__dataclass_fields__):
            raise ValueError('unknown operations setting; credentials and arbitrary commands are not accepted')
        for key in ('root', 'csv', 'protocol', 'security_master', 'calendar', 'tdx_root'):
            if raw.get(key) is not None:
                raw[key] = (path.parent / raw[key]).resolve()
        if 'symbols' in raw:
            if not isinstance(raw['symbols'], list): raise ValueError('symbols must be a JSON list')
            raw['symbols'] = tuple(raw['symbols'])
        return cls(**raw)


def check_freshness(grouped, calendar, when):
    """Only a supplied session calendar can establish the latest completed day.

    Daily timestamps in the CSV represent sessions. 15:00 Shanghai is the
    explicitly supported close; no intraday/half-day inference is made.
    """
    current = datetime.fromisoformat(when).astimezone(ZoneInfo('Asia/Shanghai'))
    latest = {s: bars[-1].timestamp.date().isoformat() for s, bars in grouped.items()}
    if calendar is None:
        return dict(status='UNKNOWN', expected_session=None, latest_by_symbol=latest,
                    reason='official_exchange_calendar_not_supplied', lagging_symbols=[])
    if not isinstance(calendar, list) or calendar != sorted(set(calendar)):
        raise ValueError('calendar must contain unique ordered ISO sessions')
    for d in calendar: date.fromisoformat(d)
    # Require coverage through today (or a future session). A truncated calendar
    # must never certify stale data as current on a later date.
    if not calendar or calendar[-1] < current.date().isoformat():
        return dict(status='UNKNOWN', expected_session=None, latest_by_symbol=latest,
                    reason='calendar_does_not_cover_current_date', lagging_symbols=[])
    complete = [d for d in calendar if d < current.date().isoformat()
                or (d == current.date().isoformat() and current.time() >= time(15))]
    if not complete:
        return dict(status='UNKNOWN', expected_session=None, latest_by_symbol=latest,
                    reason='no_completed_session_in_calendar', lagging_symbols=[])
    expected = complete[-1]
    lagging = [s for s, d in latest.items() if d < expected]
    return dict(status='STALE' if lagging else 'CURRENT', expected_session=expected,
                latest_by_symbol=latest, lagging_symbols=lagging,
                reason='supplied_calendar_comparison; suspensions require separate security evidence')


def verify_run(path, expected=None):
    path = Path(path).resolve()
    manifest = json.loads((path / 'artifacts.json').read_text(encoding='utf-8'))
    if expected is not None and fingerprint(path / 'artifacts.json') != expected:
        raise ValueError('run artifact manifest changed')
    if not manifest: raise ValueError('empty run artifact manifest')
    for relative, digest in manifest.items():
        candidate = (path / relative).resolve()
        if not candidate.is_relative_to(path) or not candidate.is_file() or fingerprint(candidate) != digest:
            raise ValueError('run artifact integrity failure: ' + relative)
    return True


def _freeze(path, target):
    path, target = Path(path), Path(target)
    before = fingerprint(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(path, target)
    if fingerprint(path) != before or fingerprint(target) != before:
        raise ValueError('input changed during snapshot')
    return before


def _test_checkout(output, timeout=600):
    root = PROJECT_ROOT
    if not list((root / 'tests').glob('test_*.py')):
        raise ValueError('source checkout with tests required')
    command = [sys.executable, '-m', 'unittest', 'discover', '-s', str(root / 'tests'), '-v']
    try:
        result = subprocess.run(command, cwd=root, capture_output=True, text=True,
                                encoding='utf-8', errors='replace', timeout=timeout)
        (output / 'test_log.txt').write_text(result.stdout + result.stderr, encoding='utf-8')
    except subprocess.TimeoutExpired as exc:
        (output / 'test_log.txt').write_text('Engineering tests exceeded the 600 second limit.\n', encoding='utf-8')
        raise RuntimeError('engineering tests timed out') from exc
    if result.returncode: raise RuntimeError('engineering tests failed; see test_log.txt')
    return dict(status='passed', command=command, log='test_log.txt', returncode=0)


def _readiness(manifest, freshness, research, paper):
    return [
        dict(code='local_engineering', status='PASS' if all(paper['checks'].values()) else 'FAIL',
             detail='Synthetic signal/order/restart/reconciliation/backup acceptance; not market returns.'),
        dict(code='historical_security_data', status='PASS' if manifest['checks']['point_in_time_security_coverage'] else 'BLOCKED',
             detail=f"Unknown historical security rows: {manifest['unknown_security_rows']}"),
        dict(code='exchange_calendar', status='PASS' if manifest['checks']['exchange_calendar_available'] else 'BLOCKED',
             detail='Supplied calendar is checked, its external authority is not automatically authenticated.'),
        dict(code='daily_freshness', status=freshness['status'], detail=freshness['reason']),
        dict(code='strict_minor_paths', status='BLOCKED', detail='Daily OHLC cannot establish inside/outside intrabar ordering.'),
        dict(code='historical_universe', status='BLOCKED', detail='Local files do not establish historical delisted-universe completeness.'),
        dict(code='independent_annotations', status='BLOCKED', detail='No independent labelled gold set supplied.'),
        dict(code='strategy_edge', status='NOT_ESTABLISHED', detail='Fixed historical diagnostics are not independent forward evidence.'),
        dict(code='historical_diagnostics', status='EXECUTED' if research is not None else 'NOT_RUN',
             detail='Strict and daily proxy remain separate; no automatic parameter promotion.'),
        dict(code='broker_and_live_permission', status='DISABLED', detail='No broker adapter, credentials or real order route.'),
        dict(code='external_notifications', status='NOT_CONFIGURED', detail='Local durable inbox only; no email/webhook delivery.'),
        dict(code='external_backup_security', status='NOT_ESTABLISHED', detail='Local hashes and restore tests are not offsite backups or access control.'),
    ]


def _report_markdown(report):
    lines = ['# 量化系统运行验收', '',
             f"运行：{report['run_id']}；离线流水线已执行；实盘订单关闭；策略有效性未建立。", '',
             f"行情：{report['data']['rows']:,} 条／{len(report['data']['symbols'])} 只；"
             f"{report['data']['start']} 至 {report['data']['end']}。", '',
             '| 验收项 | 状态 | 说明 |', '|---|---|---|']
    lines += [f"| {r['code']} | {r['status']} | {r['detail']} |" for r in report['readiness']]
    if report['research_summary']:
        lines += ['', '## 原参数历史诊断', '', '| 规则 | 成本场景 | 净收益 | 平仓数 |', '|---|---|---:|---:|']
        for name, scenarios in report['research_summary'].items():
            for label, m in scenarios.items():
                lines.append(f"| {name} | {label} | {m['total_return']:.3%} | {m['trades']} |")
    lines += ['', '## 恢复与告警', '',
              '每次运行独立冻结输入、协议、源码与测试；同一成功运行 ID 只校验并返回，不重复执行。',
              '失败／中断目录保留；新尝试使用新 ID，禁止自动重放外部副作用。',
              '本次纸面账户、场所和输入已打包并恢复到独立目录，校验通过；不是券商或异地灾备验收。',
              '本地告警收件箱位于 operations.sqlite；确认告警不等于问题已解决。', '',
              '本次报告不自动注册定时任务，不访问券商，不放宽轧空规则。']
    return '\n'.join(lines) + '\n'


def run_operations(config, run_id, *, test_runner=None):
    """Run under a process lock. Tests inject a tiny checker; CLI always runs suite."""
    identifier(run_id)
    root = config.root.resolve()
    with workspace_lock(root):
        store = EventStore(root / 'operations.sqlite')
        try:
            cfg = config.serializable()
            identity = hashlib.sha256(canonical(cfg).encode()).hexdigest()
            runs = run_states(store)
            previous = runs.get(run_id)
            output = root / 'runs' / run_id
            if previous:
                if previous['config_hash'] != identity: raise ValueError('run ID/configuration conflict')
                if previous['status'] == 'COMPLETED':
                    verify_run(output, previous['artifacts_sha256'])
                    return json.loads((output / 'report.json').read_text(encoding='utf-8'))
                raise ValueError('run ID already used; inspect failure/interruption and choose a new ID')
            # The OS lock proves no other operations worker is still running.
            for rid, old in runs.items():
                if old['status'] == 'RUNNING':
                    with store.transaction():
                        store.append('run:end:' + rid, 'RUN_FINISHED', now(),
                                     dict(run_id=rid, status='INTERRUPTED', error='worker ended before terminal record'))
            if output.exists(): raise ValueError('unregistered existing run directory; never overwrite it')
            output.mkdir(parents=True)
            with store.transaction():
                store.append('run:' + run_id, 'RUN_STARTED', now(),
                             dict(run_id=run_id, config_hash=identity, config=cfg))
            try:
                def stage(name, action):
                    print(f'operations {run_id}: {name}', flush=True)
                    value = action()
                    with store.transaction():
                        store.append('stage:' + run_id + ':' + name, 'RUN_STAGE', now(),
                                     dict(run_id=run_id, stage=name, status='COMPLETED'))
                    return value

                dump_json(output / 'config.json', cfg)
                project_root = PROJECT_ROOT
                environment = environment_snapshot(project_root)
                dump_json(output / 'environment.json', environment)
                inputs = {}
                for relative, digest in environment['hashes'].items():
                    if _freeze(project_root / relative, output / 'snapshot' / 'source' / relative) != digest:
                        raise ValueError('source changed while freezing environment')
                protocol_path = output / 'snapshot' / 'protocol.json'
                inputs[str(config.protocol)] = _freeze(config.protocol, protocol_path)
                frozen_master, frozen_calendar = None, None
                for name, p in (('security_master', config.security_master), ('calendar', config.calendar)):
                    if p is not None:
                        target = output / 'snapshot' / (name + '.json')
                        inputs[str(p)] = _freeze(p, target)
                        if name == 'security_master': frozen_master = target
                        else: frozen_calendar = target
                tests = stage('tests', lambda: (test_runner or _test_checkout)(output))
                if tests.get('status') != 'passed': raise ValueError('engineering gate failed')
                csv = output / 'snapshot' / 'daily.csv'
                if config.refresh_tdx:
                    from wavequant.infrastructure.market_data.tdx import import_tdx
                    # Freeze raw files first so concurrent TDX downloads cannot
                    # make read bytes and provenance fingerprints disagree.
                    raw_root=output/'snapshot'/'tdx_raw'
                    relatives=['T0002/hq_cache/gbbq']
                    for symbol in config.symbols:
                        market,code=symbol.split('.')
                        relatives.append(f'vipdoc/{market}/lday/{market}{code}.day')
                    for relative in relatives:
                        source=config.tdx_root/relative
                        inputs[str(source)]=_freeze(source,raw_root/relative)
                    stage('import_tdx', lambda: import_tdx(raw_root, csv, config.start, config.end, list(config.symbols)))
                else:
                    inputs[str(config.csv)] = _freeze(config.csv, csv)
                    metadata = config.csv.with_suffix('.metadata.json')
                    inputs[str(metadata)] = _freeze(metadata, csv.with_suffix('.metadata.json'))
                dump_json(output / 'inputs.json', inputs)
                master = SecurityMaster.load(frozen_master) if frozen_master else SecurityMaster()
                calendar = json.loads(frozen_calendar.read_text(encoding='utf-8')) if frozen_calendar else None
                grouped = load_bars(csv)
                observed_at = now()
                freshness = check_freshness(grouped, calendar, observed_at)
                manifest = stage('data_audit', lambda: register_dataset(store, csv, observed_at, master=master, calendar=calendar))
                dump_json(output / 'data_manifest.json', manifest)
                if not manifest['checks']['no_future_rows']: raise ValueError('data contains not-yet-available bars')
                if not manifest['checks']['listed_period_only']: raise ValueError('data outside supplied listing periods')
                from ..analytics.validation_suite import run_validation_suite
                from .platform_audit import run_paper_demo
                research = stage('historical_diagnostics', lambda: run_validation_suite(csv, protocol_path, output / 'research')) if config.run_research else None
                paper = stage('paper_acceptance', lambda: run_paper_demo(output / 'paper'))
                if not all(paper['checks'].values()): raise ValueError('paper acceptance failed')
                from ..trading.account_report import account_report
                account=EventStore(output/'paper'/'account.sqlite')
                try:
                    stamp=max(e['event_time'] for e in account.events())
                    accounting=account_report(account,stamp)
                    dump_json(output/'paper'/'accounting.json',accounting)
                    if accounting['pnl_conserved'] is not True: raise ValueError('paper P&L conservation failed')
                finally: account.close()
                # Include frozen data and source, not just database files.
                stage('backup', lambda: create_bundle(output / 'snapshot', output / 'backup_inputs'))
                stage('restore_inputs', lambda: restore_bundle(output / 'backup_inputs', output / 'restored_inputs'))
                stage('backup_accounts', lambda: create_bundle(output / 'paper', output / 'backup_accounts'))
                stage('restore_accounts', lambda: restore_bundle(output / 'backup_accounts', output / 'restored_accounts'))
                for name in ('account', 'venue'):
                    restored = EventStore(output / 'restored_accounts' / (name + '.sqlite'))
                    original = EventStore(output / 'paper' / (name + '.sqlite'))
                    try:
                        if restored.verify() != original.verify(): raise ValueError('restored journal differs')
                    finally: original.close(); restored.close()
                readiness = _readiness(manifest, freshness, research, paper)
                observations = [dict(code=r['code'], severity='warning', message=r['detail'])
                                for r in readiness if r['status'] not in ('PASS', 'CURRENT', 'EXECUTED')]
                if manifest['missing_expected_sessions']:
                    observations.append(dict(code='missing_sessions', severity='critical',
                                             message=f"Expected active sessions missing: {len(manifest['missing_expected_sessions'])}"))
                alerts = synchronize_alerts(store, 'readiness', observations, now())
                synchronize_alerts(store, 'worker', [], now())
                summary = {name: {label: value['metrics'] for label, value in row['scenarios'].items()}
                           for name, row in research['variants'].items()} if research else None
                report = dict(schema=1, run_id=run_id, mode='offline', completed_at=now(),
                              engineering_tests=tests, data=manifest, freshness=freshness, readiness=readiness,
                              research_summary=summary, paper_checks=paper['checks'], active_alerts=alerts,
                              recovery_drill_passed=True, production_ready=False, live_orders_enabled=False,
                              strategy_validated=False)
                dump_json(output / 'report.json', report)
                (output / 'report.md').write_text(_report_markdown(report), encoding='utf-8')
                # Running code must remain the tested/frozen revision.
                if environment_snapshot(project_root) != environment:
                    raise ValueError('source/test/environment changed during execution')
                for p, digest in inputs.items():
                    if fingerprint(Path(p)) != digest: raise ValueError('input changed during execution')
                artifacts = {p.relative_to(output).as_posix(): fingerprint(p) for p in sorted(output.rglob('*'))
                             if p.is_file() and not p.name.endswith(('-wal', '-shm'))}
                dump_json(output / 'artifacts.json', artifacts)
                verify_run(output)
                with store.transaction():
                    store.append('run:end:' + run_id, 'RUN_FINISHED', now(),
                                 dict(run_id=run_id, status='COMPLETED', artifacts_sha256=fingerprint(output / 'artifacts.json')))
                return report
            except BaseException as exc:
                # Persist failure type and stage, not arbitrary exception content
                # that could contain credentials from a future integration.
                failure = dict(run_id=run_id, status='FAILED', error=type(exc).__name__,
                               completed_stages=run_states(store)[run_id]['stages'])
                dump_json(output / 'failure.json', failure)
                with store.transaction(): store.append('run:end:' + run_id, 'RUN_FINISHED', now(), failure)
                synchronize_alerts(store, 'worker', [dict(code='pipeline_failed', severity='critical',
                                   message=f'Run {run_id} failed ({type(exc).__name__}); inspect failure.json and console error.')], now())
                raise
        finally: store.close()


def operations_status(root):
    root = Path(root).resolve()
    if not (root / 'operations.sqlite').is_file(): raise ValueError('existing operations root required')
    # WAL read snapshot: monitoring must remain available while a worker owns
    # the write lock. Reading status never initializes or changes the journal.
    store = EventStore(root / 'operations.sqlite', readonly=True)
    try:
        store.db.execute('BEGIN')
        runs = run_states(store)
        integrity = {}
        for rid, r in runs.items():
            if r['status'] == 'COMPLETED':
                try: integrity[rid] = verify_run(root / 'runs' / rid, r['artifacts_sha256'])
                except (ValueError, OSError): integrity[rid] = False
        return dict(runs=runs, artifact_integrity=integrity, journal_head=store.verify(),
                    alerts=list(alert_states(store).values()), live_orders_enabled=False,
                    note='RUNNING means no terminal record yet; status alone does not prove worker liveness.')
    finally: store.close()
