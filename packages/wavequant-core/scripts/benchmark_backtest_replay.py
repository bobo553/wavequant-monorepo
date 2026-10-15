"""Replay fixed offline inputs in isolated source trees and compare complete results."""

from __future__ import annotations

import argparse
import cProfile
from contextlib import ExitStack
import hashlib
import importlib
import inspect
import json
import os
from pathlib import Path
import pstats
import signal
import subprocess
import sys
import time
from typing import TYPE_CHECKING, Callable, Mapping, cast

if TYPE_CHECKING:
    from wavequant.domain.models.model import Bar
    from wavequant.domain.strategies.integrated_strategy import SystemResult
    from wavequant.infrastructure.market_data.minute import MinuteBar


def mapping(value: object) -> dict[str, object]:
    """Accept JSON objects without silently converting malformed keys or values."""
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError('JSON object required')
    return cast(dict[str, object], value)


def canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                      separators=(',', ':')).encode('utf-8')


def read_json(path: Path) -> dict[str, object]:
    return mapping(json.loads(path.read_text(encoding='utf-8')))


def first_difference(left: object, right: object, path: str = '$') -> str | None:
    """Report a precise business-result difference, including numeric representation."""
    if type(left) is not type(right):
        return path + ': type differs'
    if isinstance(left, dict):
        a, b = mapping(left), mapping(right)
        if a.keys() != b.keys():
            return path + ': keys differ'
        for key in sorted(a):
            difference = first_difference(a[key], b[key], path+'.'+key)
            if difference is not None:
                return difference
    elif isinstance(left, list):
        other = cast(list[object], right)
        if len(left) != len(other):
            return path + ': length differs'
        for index, (a_value, b_value) in enumerate(zip(left, other)):
            difference = first_difference(a_value, b_value, f'{path}[{index}]')
            if difference is not None:
                return difference
    elif isinstance(left, float) and left.hex() != cast(float, right).hex():
        return path + ': value differs'
    elif left != right:
        return path + ': value differs'
    return None


def source_hash(source_root: Path) -> str:
    """Fingerprint every Python production module, independent of checkout paths."""
    package = source_root / 'wavequant'
    if not (package / '__init__.py').is_file():
        raise ValueError('source root must contain the complete wavequant package')
    rows = [(path.relative_to(package).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest())
            for path in sorted(package.rglob('*.py'))]
    return hashlib.sha256(canonical(rows)).hexdigest()


def input_hashes(task: Mapping[str, object]) -> dict[str, str | None]:
    """Certify every supplied offline input, including minute coverage."""
    result: dict[str, str | None] = {}
    for key in ('research', 'config', 'minutes', 'cached_research'):
        path = task.get(key)
        result[key] = hashlib.sha256(Path(path).read_bytes()).hexdigest() if isinstance(path, str) else None
    return result


def compare_measurements(before: Mapping[str, object], after: Mapping[str, object]) -> str | None:
    """Compare generated evidence as well as the final account, without rounding."""
    if before['status'] != 'complete' or after['status'] != 'complete':
        return 'incomplete measurement'
    for key in ('generated_research', 'result'):
        difference = first_difference(read_json(Path(cast(str, before[key]))),
                                      read_json(Path(cast(str, after[key]))), '$.'+key)
        if difference is not None:
            return difference
    return first_difference(before.get('minute_provenance'), after.get('minute_provenance'), '$.minute_provenance')


def _deny_network(event: str, args: tuple[object, ...]) -> None:
    if event in ('socket.connect', 'socket.getaddrinfo'):
        raise RuntimeError('benchmark forbids network access; supply fixed minute inputs')


def _minute_loader(path: Path | None, symbol: str, required: bool) -> Callable[[Bar], list[MinuteBar]] | None:
    if not required:
        return None
    if path is None:
        raise ValueError('intraday execution requires an explicit frozen minute snapshot')
    from wavequant.infrastructure.market_data.akshare import AkShareProvider
    from wavequant.infrastructure.market_data.akshare_history import AkShareMinuteSource
    from wavequant.infrastructure.persistence.artifact_cache import ArtifactCache

    capture = read_json(path)
    if capture.get('symbol') != symbol:
        raise ValueError('minute snapshot belongs to another symbol')
    sessions: dict[str, list[dict[str, str]]] = {}
    for day, value in mapping(capture['sessions']).items():
        if not isinstance(value, list):
            raise ValueError('minute sessions must contain recorded rows')
        rows: list[dict[str, str]] = []
        for row in value:
            record = mapping(row)
            if not all(isinstance(field, str) for field in record.values()):
                raise ValueError('minute fields must retain their original source strings')
            rows.append(cast(dict[str, str], record))
        if len(rows) != 48 or any(row.get('date') != day or row.get('code') != symbol for row in rows):
            raise ValueError('minute snapshot must contain complete same-symbol sessions')
        sessions[day] = rows

    class OfflineProvider:
        def call(self, name: str, **options: object) -> object:
            raise RuntimeError('frozen minute replay must never request an upstream')

    class FrozenCache:
        def get(self, namespace: str, inputs: Mapping[str, object]) -> object:
            if namespace != 'akshare-sina-minute' or inputs.get('symbol') != symbol:
                raise ValueError('minute cache identity differs')
            day = inputs.get('day')
            if not isinstance(day, str):
                raise ValueError('minute day required')
            return sessions.get(day)

    # Only replace the two I/O ports. Coverage/volume validation uses the real engine.
    source = AkShareMinuteSource(cast(AkShareProvider, OfflineProvider()), cast(ArtifactCache, FrozenCache()), symbol)
    upstream_dates = capture.get('upstream_session_dates', list(sessions))
    if (not isinstance(upstream_dates, list) or any(not isinstance(day, str) or day not in sessions
                                                  for day in upstream_dates)
            or len(set(upstream_dates)) != len(upstream_dates)):
        raise ValueError('upstream session dates must name distinct recorded complete sessions')
    # The live upstream's range determines missing-day evidence; older complete
    # cache sessions remain usable without widening that upstream range.
    source.rows = {day: sessions[day] for day in upstream_dates}
    return source.get


def _minute_provenance(loader: Callable[[Bar], list[MinuteBar]] | None) -> dict[str, object] | None:
    """Record only sessions that the real same-source validator actually consumed."""
    if loader is None:
        return None
    from wavequant.infrastructure.market_data.akshare_history import AkShareMinuteSource

    source = getattr(loader, '__self__', None)
    if not isinstance(source, AkShareMinuteSource):
        raise ValueError('minute loader must retain its validated source identity')
    return mapping(source.provenance())


def _profile_summary(profile: cProfile.Profile) -> list[dict[str, object]]:
    stats = pstats.Stats(profile)
    raw = cast(dict[tuple[str, int, str], tuple[int, int, float, float, object]], getattr(stats, 'stats'))
    return [dict(file=key[0], line=key[1], function=key[2], calls=value[1],
                 self_seconds=value[2], cumulative_seconds=value[3])
            for key, value in sorted(raw.items(), key=lambda item: item[1][3], reverse=True)[:40]]


def peak_rss_bytes() -> int:
    """Return the worker's peak resident memory using the host's native counters."""
    if sys.platform != 'win32':
        import resource
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(peak if sys.platform == 'darwin' else peak * 1024)
    return _windows_peak_rss_bytes(None)


def _windows_peak_rss_bytes(pid: int | None) -> int:
    """Read a live owned worker before terminating it at the deadline."""
    import ctypes
    from ctypes import wintypes

    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in (
                'PeakWorkingSetSize', 'WorkingSetSize', 'QuotaPeakPagedPoolUsage',
                'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage', 'QuotaNonPagedPoolUsage',
                'PagefileUsage', 'PeakPagefileUsage')]

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    current_process = kernel.GetCurrentProcess
    current_process.restype = wintypes.HANDLE
    current_process.argtypes = []
    read_memory = ctypes.WinDLL('psapi', use_last_error=True).GetProcessMemoryInfo
    read_memory.restype = wintypes.BOOL
    read_memory.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessMemoryCounters), wintypes.DWORD]
    counters = ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    process = current_process()
    if pid is not None:
        open_process = kernel.OpenProcess
        open_process.restype = wintypes.HANDLE
        open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        process = open_process(0x0410, False, pid)
        if not process:
            raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not read_memory(process, ctypes.byref(counters), counters.cb):
            raise ctypes.WinError(ctypes.get_last_error())
        return int(counters.PeakWorkingSetSize)
    finally:
        if pid is not None:
            close_handle = kernel.CloseHandle
            close_handle.restype = wintypes.BOOL
            close_handle.argtypes = [wintypes.HANDLE]
            close_handle(process)


def worker(task: dict[str, object]) -> dict[str, object]:
    """One owned process, one fixed prefix, and no provider/cache side effects."""
    with ExitStack() as resources:
        return _worker(task, resources)


def _worker(task: dict[str, object], resources: ExitStack) -> dict[str, object]:
    sys.addaudithook(_deny_network)
    (Path(cast(str, task['output'])) / 'worker-process.json').write_bytes(canonical(dict(pid=os.getpid())))
    from wavequant.domain.models.model import Bar
    from wavequant.domain.models.config import StrategyConfig
    from wavequant.domain.strategies.integrated_strategy import SystemResult, SystemStrategy, generate_system_signals
    from wavequant.interfaces.research_tools.stock_backtest import single_stock_result
    from wavequant.interfaces.research_tools.tdx_backtest import decode_research, encode_research

    source_root = Path(cast(str, task['source_root'])).resolve()
    initial_source = source_hash(source_root)
    initial_inputs = input_hashes(task)
    if task.get('source_sha256') != initial_source or task.get('input_hashes') != initial_inputs:
        raise ValueError('source or fixed inputs changed before worker execution')
    module = importlib.import_module('wavequant.domain.strategies.integrated_strategy')
    if module.__file__ is None or not Path(module.__file__).resolve().is_relative_to(source_root):
        raise ValueError('worker imported a different production source tree')
    config = read_json(Path(cast(str, task['config'])))
    strategy, execution = mapping(config['strategy']), mapping(config['execution'])
    strategy_model = cast(Callable[..., SystemStrategy], SystemStrategy)(**strategy)
    cast(Callable[[], None], strategy_model.validate)()
    execution_model = cast(Callable[..., StrategyConfig], StrategyConfig)(**execution)
    execution_model.validate()
    decode = cast(Callable[[dict[str, object]], tuple[list[Bar], SystemResult]], decode_research)
    encode = cast(Callable[[list[Bar], SystemResult], dict[str, object]], encode_research)
    generate = cast(Callable[..., SystemResult], generate_system_signals)
    simulate = cast(Callable[..., dict[str, object]], single_stock_result)
    full_research = read_json(Path(cast(str, task['research'])))
    all_bars, _ = decode(full_research)
    count = cast(int, task['size'])
    if count <= 0 or count > len(all_bars):
        raise ValueError('prefix size is outside the frozen input')
    bars = all_bars[:count]
    if len({bar.symbol for bar in bars}) != 1 or any(a.timestamp >= b.timestamp for a, b in zip(bars, bars[1:])):
        raise ValueError('frozen bars must be ordered and contain exactly one symbol')
    required = execution_model.consolidation_entry_intraday or execution_model.staged_exit_intraday
    minute_path = task.get('minutes')
    minutes = _minute_loader(Path(minute_path) if isinstance(minute_path, str) else None, bars[0].symbol, required)
    output = Path(cast(str, task['output']))
    output.mkdir(parents=True, exist_ok=True)
    profile = cProfile.Profile()
    use_profile = task.get('profile') is True
    reuse: object | None = None
    reuse_stats: dict[str, dict[str, int]] = {}
    if task.get('reuse') is True:
        reuse_module = importlib.import_module('wavequant.domain.strategies.backtest_history_reuse')
        reuse_type = cast(Callable[..., object], getattr(reuse_module, 'BacktestHistoryReuse'))
        stage_options: dict[str, object] = {}
        if (source_root / 'wavequant/infrastructure/computation/trend_workers.py').is_file():
            stage_module = importlib.import_module('wavequant.infrastructure.computation.trend_workers')
            stage_options['stage_runner_factory'] = getattr(stage_module, 'stock_stage_runner')
        reuse = reuse_type(bars, strategy_model, **stage_options)
        executor = getattr(reuse, '_runner', None)
        collect = getattr(executor, '_collect', None)
        if callable(collect):
            collect_count = 0

            def observed_collect(*args: object) -> object:
                nonlocal collect_count
                result = collect(*args)
                collect_count += 1
                if collect_count % 256 == 0:
                    print(json.dumps(dict(event='stage_computation', collections=collect_count,
                                          statistics=executor.statistics())), flush=True)
                return result

            setattr(executor, '_collect', observed_collect)
        close = getattr(reuse, 'close', None)
        if callable(close):
            resources.callback(cast(Callable[[], None], close))
        for name in ('pivot', 'hierarchy', 'chart', 'refresh_chart'):
            if not hasattr(reuse, name):
                continue
            original = cast(Callable[..., object], getattr(reuse, name))
            counts = reuse_stats[name] = dict(calls=0, hits=0)

            def observed(*args: object, _original: Callable[..., object] = original,
                         _counts: dict[str, int] = counts, _name: str = name, **options: object) -> object:
                value = _original(*args, **options)
                _counts['calls'] += 1
                _counts['hits'] += value is not None
                print(json.dumps(dict(event='history_reuse', kind=_name, **_counts)), flush=True)
                return value

            setattr(reuse, name, observed)
    reuse_option: dict[str, object] = {'history_reuse': reuse} if reuse is not None else {}
    wall, cpu = time.perf_counter(), time.process_time()
    stages: dict[str, float] = {}
    stage_events: list[dict[str, object]] = []

    def stage(name: str) -> None:
        event = dict(event='stage', stage=name, wall_seconds=time.perf_counter()-wall,
                     reuse_stats={key: dict(value) for key, value in reuse_stats.items()})
        stage_events.append(event)
        print(json.dumps(event, ensure_ascii=False), flush=True)

    mode = task['mode']
    if use_profile:
        profile.enable()
    if mode == 'cold':
        stage('generate_system_signals')
        signal_stage: dict[str, object] = {'stage': stage} if 'stage' in inspect.signature(generate_system_signals).parameters else {}
        generated = generate(bars, strategy_model, **reuse_option, **signal_stage)
        research = encode(bars, generated)
    else:
        cached_path = Path(cast(str, task['cached_research']))
        research = read_json(cached_path)
        cached_bars, generated = decode(research)
        if cached_bars != bars:
            raise ValueError('cached research must match the exact prefix; full-window slicing is forbidden')
        if any(signal.timestamp > bars[-1].timestamp or (
                signal.trigger_timestamp is not None and signal.trigger_timestamp > bars[-1].timestamp)
               for signal in generated.signals):
            raise ValueError('cached signal evidence borrows a future observation')
        for row in generated.audit:
            if any(type(row.get(key)) is int and cast(int, row[key]) >= count
                   for key in ('bar_index', 'known_at')):
                raise ValueError('cached audit evidence borrows a future observation')
    # Preserve pre-execution evidence even if a consumer mutates the result.
    research_bytes = canonical(research)
    stages['signals_seconds'] = time.perf_counter()-wall
    simulation_started = time.perf_counter()
    stage('single_stock_result')
    simulation_stage: dict[str, object] = {'stage': stage} if 'stage' in inspect.signature(single_stock_result).parameters else {}
    result = simulate(bars, strategy, execution, generated, minute_loader=minutes, **reuse_option, **simulation_stage)
    if use_profile:
        profile.disable()
    if reuse is not None:
        getattr(reuse, 'close')()
    elapsed, compute = time.perf_counter()-wall, time.process_time()-cpu
    stages['simulation_seconds'] = time.perf_counter()-simulation_started
    if source_hash(source_root) != initial_source or input_hashes(task) != initial_inputs:
        raise ValueError('source or fixed inputs changed during worker execution')
    generated_path = output / 'generated-research.json'
    generated_path.write_bytes(research_bytes)
    result_path = output / 'result.json'
    result_bytes = canonical(result)
    result_path.write_bytes(result_bytes)
    if use_profile:
        profile.dump_stats(str(output / 'compute.prof'))
    executor = getattr(reuse, '_runner', None) if reuse is not None else None
    statistics = getattr(executor, 'statistics', None) if executor is not None else None
    stage_computation = statistics() if callable(statistics) else None
    return dict(status='complete', mode=mode, size=count, wall_seconds=elapsed, cpu_seconds=compute,
                stage_computation=stage_computation,
                peak_rss_bytes=peak_rss_bytes(),
                stages=stages, stage_events=stage_events,
                minute_provenance=_minute_provenance(minutes),
                first=bars[0].timestamp.isoformat(), last=bars[-1].timestamp.isoformat(),
                source_sha256=initial_source, input_hashes=initial_inputs, profiled=use_profile,
                reuse=reuse is not None, reuse_stats=reuse_stats,
                research_sha256=hashlib.sha256(research_bytes).hexdigest(),
                result_sha256=hashlib.sha256(result_bytes).hexdigest(), result=str(result_path),
                generated_research=str(generated_path), profile=_profile_summary(profile) if use_profile else [])


def run_task(task: dict[str, object], timeout: float) -> dict[str, object]:
    output = Path(cast(str, task['output']))
    output.mkdir(parents=True, exist_ok=True)
    task_path = output / 'task.json'
    task_path.write_bytes(canonical(task))
    env = dict(os.environ, PYTHONPATH=cast(str, task['source_root']), PYTHONIOENCODING='utf-8')
    command = [sys.executable, str(Path(__file__).resolve()), '--worker', str(task_path)]
    with subprocess.Popen(command, cwd=output, env=env, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True, encoding='utf-8') as process:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            peak: int | None = None
            peak_error: str | None = None
            worker_pid = process.pid
            process_record = output / 'worker-process.json'
            if process_record.is_file():
                recorded_pid = read_json(process_record).get('pid')
                if type(recorded_pid) is int:
                    worker_pid = recorded_pid
            try:
                if sys.platform == 'win32':
                    peak = _windows_peak_rss_bytes(worker_pid)
            except OSError as error:
                peak_error = str(error)
            finally:
                try:
                    tree_stopped = False
                    if sys.platform == 'win32':
                        # 只终止本任务实际worker及其计算子进程，不能留下后台进程池。
                        killed = subprocess.run(['taskkill', '/PID', str(worker_pid), '/T', '/F'],
                                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
                        tree_stopped = killed.returncode == 0
                    if worker_pid != process.pid and not tree_stopped:
                        os.kill(worker_pid, signal.SIGTERM)
                except OSError as error:
                    if peak_error is None:
                        peak_error = str(error)
                finally:
                    process.kill()
            stdout, stderr = process.communicate()
            log = stdout+stderr
            (output / 'worker.log').write_text(log, encoding='utf-8')
            events = [mapping(json.loads(line)) for line in log.splitlines()
                      if line.startswith(('{"event": "stage",', '{"event": "history_reuse",'))]
            stages = [event for event in events if event['event'] == 'stage']
            reuse_stats = {cast(str, event['kind']): dict(calls=event['calls'], hits=event['hits'])
                           for event in events if event['event'] == 'history_reuse'}
            computation = [mapping(json.loads(line)) for line in log.splitlines()
                           if line.startswith('{"event": "stage_computation",')]
            return dict(status='timeout', size=task['size'], mode=task['mode'], timeout_seconds=timeout,
                        peak_rss_bytes=peak, peak_rss_error=peak_error,
                        peak_rss_pid=worker_pid,
                        reuse_stats=reuse_stats, last_stage=stages[-1] if stages else None,
                        last_computation=computation[-1] if computation else None)
    (output / 'worker.log').write_text(stdout+stderr, encoding='utf-8')
    report = output / 'measurement.json'
    if process.returncode != 0 or not report.is_file():
        raise RuntimeError(f'benchmark worker failed; inspect {output / "worker.log"}')
    return read_json(report)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--research', type=Path)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--minutes', type=Path)
    parser.add_argument('--source-root', type=Path, default=Path(__file__).resolve().parents[1] / 'src')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--sizes', default='64,128,256')
    parser.add_argument('--mode', choices=('cold', 'cached', 'both'), default='both')
    parser.add_argument('--repeat', type=int, default=2)
    parser.add_argument('--timeout', type=float, default=45)
    parser.add_argument('--compare', type=Path, help='another run summary; compare every complete result')
    parser.add_argument('--profile', action='store_true', help='collect cProfile; timing includes profiler overhead')
    parser.add_argument('--reuse', action='store_true', help='pass one request-local history object through both stages')
    args = parser.parse_args()
    if args.worker is not None:
        task = read_json(args.worker)
        measured = worker(task)
        (Path(cast(str, task['output'])) / 'measurement.json').write_bytes(canonical(measured))
        return
    if args.research is None or args.config is None or args.output is None:
        parser.error('--research, --config and --output are required')
    if not 1 <= args.repeat <= 10 or args.timeout <= 0:
        parser.error('repeat must be 1..10 and timeout must be positive')
    research, output = args.research.resolve(), args.output.resolve()
    captured = read_json(research)
    rows = captured['bars']
    if not isinstance(rows, list) or not rows:
        raise ValueError('fixed encoded research must include nonempty bars')
    sizes = [len(rows) if item == 'all' else int(item) for item in args.sizes.split(',')]
    if any(size <= 0 or size > len(rows) for size in sizes):
        raise ValueError('requested prefix size exceeds fixed input')
    if output.exists():
        raise ValueError('use a new output directory; never overwrite benchmark evidence')
    output.mkdir(parents=True)
    initial_source = source_hash(args.source_root.resolve())
    fixed_inputs = dict(research=str(research), config=str(args.config.resolve()),
                        minutes=str(args.minutes.resolve()) if args.minutes else None)
    initial_inputs = input_hashes(fixed_inputs)
    measurements: list[dict[str, object]] = []
    for size in sizes:
        modes = ('cold', 'cached') if args.mode == 'both' else (args.mode,)
        cached_research = research if args.mode == 'cached' and size == len(rows) else None
        for mode in modes:
            if mode == 'cached' and cached_research is None:
                measurements.extend(dict(status='unavailable', size=size, mode=mode,
                                         reason='no completed causal cold prefix') for _ in range(args.repeat))
                continue
            for repeat in range(args.repeat):
                target = output / f'{size}-{mode}-{repeat+1}'
                task = dict(fixed_inputs, source_root=str(args.source_root.resolve()),
                            cached_research=str(cached_research) if cached_research else None,
                            size=size, mode=mode, output=str(target), profile=args.profile, reuse=args.reuse,
                            source_sha256=initial_source)
                task['input_hashes'] = input_hashes(task)
                measured = run_task(task, args.timeout)
                measurements.append(measured)
                shown = ('status', 'size', 'mode', 'wall_seconds', 'cpu_seconds', 'peak_rss_bytes',
                         'stages', 'research_sha256', 'result_sha256', 'last_stage')
                print(json.dumps({key: measured[key] for key in shown if key in measured}, ensure_ascii=False), flush=True)
                if measured['status'] == 'complete' and mode == 'cold' and cached_research is None:
                    cached_research = Path(cast(str, measured['generated_research']))
    if source_hash(args.source_root.resolve()) != initial_source or input_hashes(fixed_inputs) != initial_inputs:
        raise ValueError('source or fixed inputs changed during benchmark execution')
    differences: list[dict[str, object]] = []
    complete_by_size: dict[int, dict[str, object]] = {}
    for measured in measurements:
        if measured['status'] != 'complete':
            differences.append(dict(size=measured['size'], mode=measured['mode'], difference='incomplete measurement'))
            continue
        size = cast(int, measured['size'])
        before = complete_by_size.setdefault(size, measured)
        difference = compare_measurements(before, measured)
        if difference is not None:
            differences.append(dict(size=size, mode=measured['mode'], difference=difference))
    summary: dict[str, object] = dict(input_hashes=initial_inputs, source_sha256=initial_source,
                                     profiled=args.profile, reuse=args.reuse,
                                     measurements=measurements, differences=differences)
    if args.compare is not None:
        previous = read_json(args.compare)
        if previous['input_hashes'] != summary['input_hashes']:
            raise ValueError('comparison inputs or configurations differ')
        old = previous['measurements']
        if not isinstance(old, list) or len(old) != len(measurements):
            raise ValueError('comparison windows/modes/repeat counts differ')
        for before_raw, after in zip(old, measurements):
            before = mapping(before_raw)
            if (before['size'], before['mode']) != (after['size'], after['mode']):
                raise ValueError('comparison windows/modes differ')
            difference = compare_measurements(before, after)
            if difference is not None:
                differences.append(dict(size=after['size'], mode=after['mode'], difference=difference))
    (output / 'summary.json').write_bytes(canonical(summary))
    if summary.get('differences'):
        raise SystemExit('benchmark incomplete or results differ; inspect summary.json')


if __name__ == '__main__':
    main()
