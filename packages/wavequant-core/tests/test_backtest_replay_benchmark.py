"""The offline benchmark must fail closed on incomplete or changed evidence."""

from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import runpy
import shutil
import sys
from types import SimpleNamespace
from typing import Callable, Mapping, Protocol, cast

import pytest
import wavequant
from wavequant.domain.models.model import Bar
from wavequant.infrastructure.market_data.akshare_history import MinuteCoverageError
from wavequant.infrastructure.market_data.minute import MinuteBar, _expected_times


class Benchmark(Protocol):
    def main(self) -> None: ...
    def first_difference(self, left: object, right: object, path: str = '$') -> str | None: ...
    def compare_measurements(self, before: Mapping[str, object], after: Mapping[str, object]) -> str | None: ...
    def input_hashes(self, task: Mapping[str, object]) -> dict[str, str | None]: ...
    def source_hash(self, source_root: Path) -> str: ...
    def peak_rss_bytes(self) -> int: ...
    def run_task(self, task: dict[str, object], timeout: float) -> dict[str, object]: ...
    def _deny_network(self, event: str, args: tuple[object, ...]) -> None: ...
    def _minute_loader(self, path: Path | None, symbol: str, required: bool) -> object: ...
    def _minute_provenance(self, loader: Callable[[Bar], list[MinuteBar]] | None) -> dict[str, object] | None: ...


benchmark = cast(Benchmark, SimpleNamespace(**runpy.run_path(
    str(Path(__file__).resolve().parents[1] / 'scripts' / 'benchmark_backtest_replay.py'))))


def _write(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value, sort_keys=True), encoding='utf-8')
    return path


@pytest.mark.parametrize('field', ['signals', 'audit', 'counts', 'bars'])
def test_comparison_includes_original_signal_evidence(tmp_path: Path, field: str) -> None:
    original: dict[str, object] = dict(signals=[{'bar_index': 7}], audit=[{'known_at': 8}],
                                      counts={'qualified': 1}, bars=[{'adjustment_factor': 1.0}])
    changed = deepcopy(original)
    changed[field] = []
    result = _write(tmp_path / 'result.json', {'orders': []})
    before = dict(status='complete', generated_research=str(_write(tmp_path / 'before.json', original)), result=str(result))
    after = dict(status='complete', generated_research=str(_write(tmp_path / 'after.json', changed)), result=str(result))
    reason = 'type differs' if field == 'counts' else 'length differs'
    assert benchmark.compare_measurements(before, after) == '$.generated_research.'+field+': '+reason


@pytest.mark.parametrize('field', ['signals', 'audit', 'counts', 'orders', 'trades', 'ledger', 'equity', 'metrics', 'minute_fallbacks'])
def test_comparison_includes_every_account_output(tmp_path: Path, field: str) -> None:
    original: dict[str, object] = {name: [{'value': 1.0}] for name in
                                  ('signals', 'audit', 'counts', 'orders', 'trades', 'ledger', 'equity', 'metrics', 'minute_fallbacks')}
    changed = deepcopy(original)
    changed[field] = [{'value': 1.000000001}]
    research = _write(tmp_path / 'research.json', {'bars': []})
    before = dict(status='complete', generated_research=str(research), result=str(_write(tmp_path / 'before.json', original)))
    after = dict(status='complete', generated_research=str(research), result=str(_write(tmp_path / 'after.json', changed)))
    assert benchmark.compare_measurements(before, after) == '$.result.'+field+'[0].value: value differs'


def test_numeric_representation_keys_and_order_are_preserved() -> None:
    assert benchmark.first_difference({'a': 1}, {'a': 1.0}) == '$.a: type differs'
    assert benchmark.first_difference({'a': -0.0}, {'a': 0.0}) == '$.a: value differs'
    assert benchmark.first_difference({'a': 1}, {'a': 1, 'b': None}) == '$: keys differ'
    assert benchmark.first_difference([{'known_at': 2}, {'known_at': 3}],
                                      [{'known_at': 3}, {'known_at': 2}]) == '$[0].known_at: value differs'
    assert benchmark.first_difference({'a': [None, False, 1.0]}, {'a': [None, False, 1.0]}) is None


@pytest.mark.parametrize('status', ['timeout', 'unavailable'])
def test_incomplete_measurement_cannot_pass(status: str) -> None:
    assert benchmark.compare_measurements({'status': 'complete'}, {'status': status}) == 'incomplete measurement'


def test_input_certificate_includes_minute_coverage_and_cache(tmp_path: Path) -> None:
    task: dict[str, object] = {name: str(_write(tmp_path / (name+'.json'), {'source': name}))
                              for name in ('research', 'config', 'minutes', 'cached_research')}
    before = benchmark.input_hashes(task)
    _write(Path(cast(str, task['minutes'])), {'source': 'same daily prices, changed minute coverage'})
    after = benchmark.input_hashes(task)
    assert set(before) == {'research', 'config', 'minutes', 'cached_research'}
    assert after['minutes'] != before['minutes']
    assert all(after[key] == before[key] for key in ('research', 'config', 'cached_research'))


def test_source_certificate_detects_added_helper_and_mutation(tmp_path: Path) -> None:
    package = tmp_path / 'wavequant'
    package.mkdir()
    (package / '__init__.py').write_text('', encoding='utf-8')
    first = benchmark.source_hash(tmp_path)
    helper = package / 'request_history.py'
    helper.write_text('VALUE = 1\n', encoding='utf-8')
    second = benchmark.source_hash(tmp_path)
    helper.write_text('VALUE = 2\n', encoding='utf-8')
    assert first != second != benchmark.source_hash(tmp_path)
    with pytest.raises(ValueError, match='complete wavequant package'):
        benchmark.source_hash(package)


def test_intraday_requires_explicit_snapshot_and_forbids_network() -> None:
    assert benchmark._minute_loader(None, 'sz.000678', False) is None
    with pytest.raises(ValueError, match='explicit frozen minute snapshot'):
        benchmark._minute_loader(None, 'sz.000678', True)
    for event in ('socket.connect', 'socket.getaddrinfo'):
        with pytest.raises(RuntimeError, match='forbids network'):
            benchmark._deny_network(event, ())
    benchmark._deny_network('open', ())


def test_minute_snapshot_keeps_upstream_coverage_separate_from_older_cache(tmp_path: Path) -> None:
    sessions = {day: [dict(date=day, time=day.replace('-', '')+clock.strftime('%H%M%S')+'000',
                          code='sz.000678', adjustflag='3', open='10', high='11', low='9', close='10', volume='100')
                     for clock in _expected_times()] for day in ('2020-01-06', '2020-01-07')}
    snapshot = _write(tmp_path / 'minute.json', dict(symbol='sz.000678', sessions=sessions,
                                                  upstream_session_dates=['2020-01-07']))
    loader = cast(Callable[[Bar], list[MinuteBar]], benchmark._minute_loader(snapshot, 'sz.000678', True))
    before = benchmark._minute_provenance(loader)
    assert before is not None and before['session_sha256'] == {}
    cached_bar = Bar(datetime(2020, 1, 6), 'sz.000678', 10.0, 11.0, 9.0, 10.0, 4800.0)
    assert len(loader(cached_bar)) == 48
    after = benchmark._minute_provenance(loader)
    assert after is not None
    hashes = after['session_sha256']
    assert isinstance(hashes, dict) and set(hashes) == {'2020-01-06'}
    assert before['session_sha256'] == {}
    missing_bar = Bar(datetime(2020, 1, 8), 'sz.000678', 10.0, 11.0, 9.0, 10.0, 4800.0)
    with pytest.raises(MinuteCoverageError) as rejected:
        loader(missing_bar)
    assert rejected.value.coverage['available_start'] == '2020-01-07'
    assert rejected.value.coverage['available_end'] == '2020-01-07'
    assert benchmark._minute_provenance(loader) == after


def test_minute_provenance_cannot_borrow_an_unvalidated_loader() -> None:
    assert benchmark._minute_provenance(None) is None
    with pytest.raises(ValueError, match='validated source identity'):
        benchmark._minute_provenance(lambda bar: [])


def test_account_comparison_retains_actual_consumed_minute_identity(tmp_path: Path) -> None:
    research = _write(tmp_path / 'research.json', {'bars': []})
    result = _write(tmp_path / 'result.json', {'orders': []})
    before = dict(status='complete', generated_research=str(research), result=str(result),
                  minute_provenance={'session_sha256': {'2020-01-06': 'first'}})
    after = dict(before, minute_provenance={'session_sha256': {'2020-01-06': 'changed'}})
    assert benchmark.compare_measurements(before, after) == (
        '$.minute_provenance.session_sha256.2020-01-06: value differs')


@pytest.mark.parametrize('dates', [['2020-01-06'], [1], ['2020-01-07', '2020-01-07']])
def test_minute_snapshot_rejects_unrecorded_or_duplicate_upstream_dates(tmp_path: Path, dates: list[object]) -> None:
    day = '2020-01-07'
    rows = [dict(date=day, code='sz.000678') for _ in range(48)]
    snapshot = _write(tmp_path / 'minute.json', dict(symbol='sz.000678', sessions={day: rows},
                                                  upstream_session_dates=dates))
    with pytest.raises(ValueError, match='distinct recorded complete sessions'):
        benchmark._minute_loader(snapshot, 'sz.000678', True)


@pytest.mark.parametrize('future', ['unsliced_bars', 'bar_index', 'known_at'])
def test_actual_worker_rejects_future_cache_for_partial_prefix(tmp_path: Path, future: str) -> None:
    assert wavequant.__file__ is not None
    source = tmp_path / 'src'
    shutil.copytree(Path(wavequant.__file__).parent, source / 'wavequant',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    rows = [dict(timestamp=f'2020-01-{day:02d}T00:00:00', symbol='sz.000678',
                 open=10.0, high=11.0, low=9.0, close=10.0, volume=10000.0)
            for day in (6, 7, 8)]
    research = _write(tmp_path / 'research.json', dict(bars=rows, signals=[], audit=[], counts={}))
    if future == 'unsliced_bars':
        cached = research
        reason = 'cached research must match the exact prefix; full-window slicing is forbidden'
    else:
        cached = _write(tmp_path / 'cached.json', dict(bars=rows[:2], signals=[], audit=[{future: 2}], counts={}))
        reason = 'cached audit evidence borrows a future observation'
    config = _write(tmp_path / 'config.json', dict(strategy={}, execution=dict(
        consolidation_entry_intraday=False, staged_exit_intraday=False)))
    output = tmp_path / 'worker'
    task: dict[str, object] = dict(source_root=str(source), source_sha256=benchmark.source_hash(source),
                                  research=str(research), config=str(config), minutes=None,
                                  cached_research=str(cached), size=2, mode='cached', output=str(output))
    task['input_hashes'] = benchmark.input_hashes(task)
    with pytest.raises(RuntimeError, match='benchmark worker failed'):
        benchmark.run_task(task, 10)
    assert reason in (output / 'worker.log').read_text(encoding='utf-8')


def test_peak_resident_memory_is_reported_in_bytes() -> None:
    assert benchmark.peak_rss_bytes() > 0


def test_owned_worker_deadline_returns_incomplete_with_log(tmp_path: Path) -> None:
    assert wavequant.__file__ is not None
    source = tmp_path / 'src'
    shutil.copytree(Path(wavequant.__file__).parent, source / 'wavequant',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    stock_backtest = source / 'wavequant' / 'interfaces' / 'research_tools' / 'stock_backtest.py'
    stock_backtest.write_text(stock_backtest.read_text(encoding='utf-8')+
        '\n\n_benchmark_simulate = single_stock_result\n'
        'def single_stock_result(*args, **options):\n'
        '    import time\n'
        '    time.sleep(10)\n'
        '    return _benchmark_simulate(*args, **options)\n', encoding='utf-8')
    rows = [dict(timestamp='2020-01-06T00:00:00', symbol='sz.000678',
                 open=10.0, high=11.0, low=9.0, close=10.0, volume=10000.0)]
    research = _write(tmp_path / 'research.json', dict(bars=rows, signals=[], audit=[], counts={}))
    config = _write(tmp_path / 'config.json', dict(strategy={}, execution=dict(
        consolidation_entry_intraday=False, staged_exit_intraday=False)))
    task: dict[str, object] = dict(source_root=str(source), source_sha256=benchmark.source_hash(source),
                                  research=str(research), config=str(config), minutes=None,
                                  cached_research=str(research), size=1, mode='cached', output=str(tmp_path / 'worker'))
    task['input_hashes'] = benchmark.input_hashes(task)
    measured = benchmark.run_task(task, 2)
    assert measured['status'] == 'timeout'
    assert measured['size'] == 1
    assert cast(dict[str, object], measured['last_stage'])['stage'] == 'single_stock_result'
    assert (tmp_path / 'worker' / 'worker.log').is_file()
    if sys.platform == 'win32':
        assert cast(int, measured['peak_rss_bytes']) > 0
        assert measured['peak_rss_error'] is None
        identity = json.loads((tmp_path / 'worker' / 'worker-process.json').read_text(encoding='utf-8'))
        assert measured['peak_rss_pid'] == identity['pid']


@pytest.mark.parametrize('cold_status', ['complete', 'timeout'])
def test_full_window_cache_requires_its_own_completed_cold_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cold_status: str,
) -> None:
    source = tmp_path / 'src'
    (source / 'wavequant').mkdir(parents=True)
    (source / 'wavequant' / '__init__.py').write_text('', encoding='utf-8')
    research = _write(tmp_path / 'research.json', dict(bars=[{'date': '2020-01-06'}], signals=[], audit=[]))
    config = _write(tmp_path / 'config.json', dict(strategy={}, execution={}))
    output = tmp_path / 'measurements'
    calls: list[dict[str, object]] = []

    def run_task(task: dict[str, object], timeout: float) -> dict[str, object]:
        calls.append(task)
        if task['mode'] == 'cold' and cold_status == 'timeout':
            return dict(status='timeout', mode='cold', size=1)
        target = Path(cast(str, task['output']))
        target.mkdir(parents=True)
        generated = _write(target / 'generated-research.json', dict(bars=[{'date': '2020-01-06'}], signals=[], audit=[]))
        result = _write(target / 'result.json', {'orders': []})
        return dict(status='complete', mode=task['mode'], size=1,
                    generated_research=str(generated), result=str(result))

    function = cast(Callable[..., object], benchmark.main)
    monkeypatch.setitem(function.__globals__, 'run_task', run_task)
    monkeypatch.setattr(sys, 'argv', ['benchmark', '--research', str(research), '--config', str(config),
                                   '--source-root', str(source), '--output', str(output),
                                   '--sizes', 'all', '--mode', 'both', '--repeat', '1'])
    if cold_status == 'timeout':
        with pytest.raises(SystemExit, match='incomplete'):
            benchmark.main()
        assert len(calls) == 1
        summary = json.loads((output / 'summary.json').read_text(encoding='utf-8'))
        assert summary['measurements'][1]['status'] == 'unavailable'
    else:
        benchmark.main()
        assert len(calls) == 2
        assert calls[1]['cached_research'] == str(output / '1-cold-1' / 'generated-research.json')
        assert calls[1]['cached_research'] != str(research)
