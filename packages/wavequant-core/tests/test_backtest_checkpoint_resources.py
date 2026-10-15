"""Adapters close request-owned files on cache, coverage and failure returns."""

from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import cast

import pytest

from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar
from wavequant.domain.market_structure.trend_stage_work import TrendStageRunner
from wavequant.domain.strategies.backtest_history_reuse import BacktestHistoryReuse, _CheckpointStream
from wavequant.domain.strategies.integrated_strategy import SystemResult, SystemStrategy
from wavequant.infrastructure.market_data.akshare_history import MinuteCoverageError
from wavequant.infrastructure.market_data.akshare import AkShareProvider
from wavequant.interfaces.charts.akshare_browser import AkShareBrowser
from wavequant.interfaces.research_tools import akshare_backtest, tdx_backtest

from .test_akshare_history import Provider
from . import test_tdx_backtest as tdx_fixtures


def _track_owners(monkeypatch: pytest.MonkeyPatch, module: object
                  ) -> tuple[list[BacktestHistoryReuse], list[_CheckpointStream]]:
    owners: list[BacktestHistoryReuse] = []
    streams: list[_CheckpointStream] = []

    def make(bars: Sequence[Bar], strategy: SystemStrategy, *,
             stage_runner_factory: Callable[[tuple[Bar, ...]], TrendStageRunner | None] | None = None) -> BacktestHistoryReuse:
        owner = BacktestHistoryReuse(bars, strategy, stage_runner_factory=stage_runner_factory)
        # Open real owned files even when the external provider fake has no
        # geometry. Lifecycle checks must verify file resources, not only flags.
        channel = owner._minute.begin('chart', (False, False, False, False))
        assert channel is not None
        channel.day(0, 0, {'event': 'resource_fixture'})
        streams.append(channel.journal)
        owners.append(owner)
        return owner

    monkeypatch.setattr(module, 'BacktestHistoryReuse', make)
    return owners, streams


def _closed(owners: Sequence[BacktestHistoryReuse], streams: Sequence[_CheckpointStream]) -> None:
    assert owners
    assert all(owner._minute.closed and owner._minute.disk_used == 0 for owner in owners)
    assert all(stream.closed and stream.file.closed for stream in streams)


@pytest.mark.parametrize('failure', ('none', 'coverage', 'consumer', 'generate'))
def test_akshare_owner_closes_real_stream_on_all_outcomes(
    failure: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = Provider([dict(date='2000-01-01', hfq_factor=1)])
    browser = AkShareBrowser(cast(AkShareProvider, provider), pinned_history=True)
    bars = [Bar(datetime(2026, 1, 1) + timedelta(days=index), 'sz.300154', 10, 11, 9, 10, 100000)
            for index in range(30)]
    monkeypatch.setattr(browser, 'bars', lambda *_: (bars, bars))
    owners, streams = _track_owners(monkeypatch, akshare_backtest)
    generated = SystemResult([], [], {})

    def signals(*args: object, **kwargs: object) -> SystemResult:
        if failure == 'generate':
            raise RuntimeError('generation failed')
        return generated

    monkeypatch.setattr(akshare_backtest, 'generate_system_signals', signals)
    original = akshare_backtest.single_stock_result

    def account(*args: object, **kwargs: object) -> object:
        if failure == 'coverage':
            raise MinuteCoverageError('2026-01-23', '2026-07-24', '2026-09-18')
        if failure == 'consumer':
            raise RuntimeError('consumer failed')
        return cast(Callable[..., object], original)(*args, **kwargs)

    monkeypatch.setattr(akshare_backtest, 'single_stock_result', account)
    execution = StrategyConfig(staged_exit_intraday=False).to_dict()
    service = akshare_backtest.AkShareBacktester(browser, tmp_path)
    if failure in ('consumer', 'generate'):
        with pytest.raises(RuntimeError, match='failed'):
            service.run('sz.300154', '2026-01-21', '2026-01-30', {}, execution)
    else:
        first = service.run('sz.300154', '2026-01-21', '2026-01-30', {}, execution)
        assert first[2]['backtest']['status'] == ('data_unavailable' if failure == 'coverage' else 'complete')
        if failure == 'none':
            second = service.run('sz.300154', '2026-01-21', '2026-01-30', {}, execution)
            assert second == first
            assert len(owners) == 2
    _closed(owners, streams)


@pytest.mark.parametrize('failure', ('none', 'consumer', 'generate'))
def test_tdx_owner_closes_real_stream_on_cache_and_failure_paths(
    failure: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = tdx_fixtures.TdxBacktestTests()
    fixture.setUp()
    owners, streams = _track_owners(monkeypatch, tdx_backtest)
    original = tdx_backtest.single_stock_result

    def account(*args: object, **kwargs: object) -> object:
        if failure == 'consumer':
            raise RuntimeError('consumer failed')
        return cast(Callable[..., object], original)(*args, **kwargs)

    monkeypatch.setattr(tdx_backtest, 'single_stock_result', account)
    if failure == 'generate':
        def fail(*args: object, **kwargs: object) -> SystemResult:
            raise RuntimeError('generation failed')
        monkeypatch.setattr(fixture, 'generated', fail)
    try:
        if failure != 'none':
            with pytest.raises(RuntimeError, match='failed'):
                fixture.run_fixture()
        else:
            first = fixture.run_fixture()
            second = fixture.run_fixture()
            assert first[:2] == second[:2]
            assert first[2]['orders'] == second[2]['orders']
            assert len(owners) == 1
    finally:
        fixture.doCleanups()
    _closed(owners, streams)
