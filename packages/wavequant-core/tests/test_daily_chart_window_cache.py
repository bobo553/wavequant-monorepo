"""交易窗口重开只保留已认证的图表历史，窗口内策略状态仍重新创建。"""

from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from typing import cast

import pytest

import wavequant.domain.strategies.daily_strategy as daily
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.backtest_analysis import BacktestAnalysis, prepare_backtest_analysis
from wavequant.domain.strategies.integrated_strategy import DailySignalState, SystemResult, SystemStrategy
from wavequant.domain.strategies.market_calculations import MarketDay
from wavequant.domain.strategies.trading_intervals import TradingInterval

from .test_chart_live_permission_reuse import _read, _record_starts, _strategy
from .test_chart_owned_transfer import _typed
from .test_staged_trend_pipeline import _multiple_path_bars


@pytest.mark.parametrize('revision', ('none', 'attack', 'eligibility', 'owner', 'options'))
def test_daily_window_reopen_preserves_only_the_chart_checkpoint_and_checks_its_full_prefix(
    revision: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    bars = _multiple_path_bars(count=18)
    windows = ((5, 10), (18, 23), (27, 32))
    starts = _record_starts(monkeypatch)
    reopened: list[int | None] = []
    chart_objects: list[dict[str, object]] = []
    called: list[int] = []
    audit: dict[str, object] = dict(event='n_completed', direction='up', bar_index=2,
        known_at=2, origin=0, neckline=1, pullback=2, one_p=1500., two_t=1800., proof={'version': 1})

    @contextmanager
    def scheduled_analysis(
        market: Sequence[Bar], config: SystemStrategy, *,
        on_day: Callable[[MarketDay, BacktestAnalysis], None] | None = None,
    ) -> Iterator[BacktestAnalysis]:
        def consume(day: MarketDay, analysis: BacktestAnalysis) -> None:
            assert on_day is not None
            index = day.frame.index
            days = [any(start <= i < end for start, end in windows) for i in range(index + 1)]
            if revision == 'eligibility' and index >= windows[1][0]:
                days[7] = False
            intervals = tuple(TradingInterval(2, f'window-{start}', start, start,
                end_index=end if end <= index else None) for start, end in windows if start <= index)
            # 只控制调度边界；折线、三级趋势、六态、资格、图表附件均读取真实实现。
            on_day(day, replace(analysis, buy_days=tuple(days), trading_intervals=intervals))

        with prepare_backtest_analysis(market, config, on_day=consume) as analysis:
            yield analysis

    def chart_consumer(
        market: Sequence[Bar], config: SystemStrategy, *,
        chart_history_cache: dict[str, object], _analysis: BacktestAnalysis,
        _daily: DailySignalState, **_kwargs: object,
    ) -> SystemResult:
        index = len(market) - 1
        called.append(index)
        new_window = index in {start for start, _ in windows}
        assert ('window-local' not in chart_history_cache) is new_window
        chart_history_cache['window-local'] = index
        checkpoint = cast(dict[str, object], chart_history_cache.setdefault('chart', {}))
        chart_objects.append(checkpoint)
        evidence = deepcopy([audit])
        enabled = revision == 'options' and index >= windows[1][0]
        if revision == 'attack' and index >= windows[1][0]:
            cast(dict[str, object], evidence[0]['proof'])['version'] = 2
        if revision == 'owner' and index == windows[1][0]:
            checkpoint['_live_owner'] = object()
        before = len(starts)
        actual = _read(_analysis, checkpoint, evidence, enabled=enabled)
        if new_window:
            reopened.append(starts[before])
        assert _typed(actual) == _typed(_read(_analysis, None, evidence, enabled=enabled))
        assert _daily.window_start == max(start for start, _ in windows if start <= index)
        assert chart_history_cache['source_bars'] == tuple(bars)
        return SystemResult([], [], {'rows': len(market)})

    monkeypatch.setattr(daily, 'prepare_backtest_analysis', scheduled_analysis)
    monkeypatch.setattr(daily.policy, '_generate_system_signals', chart_consumer)
    daily.daily_signals(bars, _strategy())
    assert called == [index for start, end in windows for index in range(start, end)]
    assert chart_objects and all(checkpoint is chart_objects[0] for checkpoint in chart_objects)
    assert reopened == [0, 9 if revision == 'none' else 0, 22]
