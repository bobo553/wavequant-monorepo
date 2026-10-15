"""The market cannot advance until that date's account and snapshot are complete."""

from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from wavequant.domain.models.model import Bar, Signal
from wavequant.domain.strategies.chart_drawing_history import ChartDrawingFrame
from wavequant.domain.strategies.integrated_strategy import DailySignalState, SystemResult, SystemStrategy
from wavequant.domain.strategies.market_calculations import MarketDay
from wavequant.domain.strategies.market_preview import observe_market_preview
from wavequant.interfaces.research_tools import streaming_backtest as module


@pytest.mark.parametrize('minute_enabled', [False, True])
def test_daily_coordinator_publishes_fills_before_advancing_market(monkeypatch, minute_enabled):
    bars = [Bar(datetime(2026, 1, 5) + timedelta(days=i), 'TEST', 10, 11, 9.5, 10.2, 1_000_000)
            for i in range(5)]
    config = SystemStrategy(pivot_mode='lecture_causal', entry_policy='hierarchical_two_buy_points',
                            buy_point_definition='whole_flip_wave_v3')
    buy = Signal(bars[1].timestamp, 'TEST', 1, 'LONG', 10.2, 9, 'entry', bars[0].timestamp, 0, 2, 'bull', 15)
    sell = replace(buy, timestamp=bars[3].timestamp, bar_index=3, side='EXIT', reason='risk')
    previews, minutes, percentages = [], [], []

    def preview(market, day, result):
        index = day.frame.index
        assert len(result['equity']) == index + 1
        assert result['equity'][-1]['timestamp'] == market[index].timestamp.isoformat()
        previews.append((index, deepcopy(result)))

    def daily(market, strategy, *, on_day, progress, stage):
        state = DailySignalState()
        for index, bar in enumerate(market):
            state.signals.extend(signal for signal in (buy, sell) if signal.bar_index == index)
            generated = SystemResult(list(state.signals), [], {'trading_interval_count': 1})
            frame = ChartDrawingFrame(index, 0, [], {'strokes': []})
            day = MarketDay(frame, ({'strokes': []},) * 3, (), (index in (1, 2),) * 3)
            analysis = SimpleNamespace(scan_days=(index in (1, 2, 3),), pivots=({}, {}, {}, set()))
            on_day(day, analysis, generated, state)
            assert [day for day, _ in previews] == list(range(index + 1))
            progress((index + 1) * 100 // len(market))
        return generated

    def minute(prefix, generated, strategy, loader, **options):
        assert options['only_index'] == len(prefix) - 1
        minutes.append(options['only_index'])
        return generated, {}, []

    def format_result(market, strategy, execution, generated, *, account_result, entry_fallbacks_override):
        assert account_result is not None, 'formatting must not start a second account replay'
        return dict(orders=account_result.orders, equity=account_result.equity)

    monkeypatch.setattr(module, 'daily_signals', daily)
    monkeypatch.setattr(module, 'resolve_consolidation_entries', minute)
    monkeypatch.setattr(module, 'single_stock_result', format_result)
    with observe_market_preview(preview):
        generated, result = module.streaming_stock_result(bars, asdict(config),
            dict(entry_at_close=False, net_reward_risk_filter=False,
                 consolidation_entry_intraday=minute_enabled),
            progress=lambda percent, stage: percentages.append((percent, previews[-1][0])))
    assert [signal.side for signal in generated.signals] == ['LONG', 'EXIT']
    assert [(row['side'], row['timestamp']) for row in result['orders'] if row['status'] == 'filled'] == [
        ('BUY', bars[2].timestamp.isoformat()), ('SELL', bars[4].timestamp.isoformat())]
    assert not previews[1][1]['orders'], 'next-open entries must remain pending at signal close'
    assert previews[2][1]['orders'][0]['side'] == 'BUY'
    assert len(result['equity']) == len(bars)
    assert [index for _, index in percentages] == list(range(len(bars)))
    assert minutes == ([1, 2, 3] if minute_enabled else [])


def test_consumer_error_stops_before_the_next_market_date(monkeypatch):
    from wavequant.domain.strategies.daily_strategy import daily_signals
    bars = [Bar(datetime(2026, 1, 5) + timedelta(days=i), 'TEST', 10, 11, 9, 10, 100) for i in range(4)]
    monkeypatch.setattr(MarketDay, 'permits_buy', lambda self, level: False)
    dates, progress = [], []

    def consumer(day, analysis, generated, state):
        dates.append(day.frame.index)
        if day.frame.index == 1:
            raise RuntimeError('account failed')

    config = SystemStrategy(pivot_mode='lecture_causal', entry_policy='hierarchical_two_buy_points',
                            buy_point_definition='whole_flip_wave_v3')
    with pytest.raises(RuntimeError, match='account failed'):
        daily_signals(bars, config, on_day=consumer, progress=progress.append)
    assert dates == [0, 1]
    assert progress == [25], 'an unfinished day must not advance published progress'
