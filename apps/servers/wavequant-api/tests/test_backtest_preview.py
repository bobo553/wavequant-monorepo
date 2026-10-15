from datetime import datetime, timedelta

import pytest

from wavequant.domain.models.model import Bar, Signal
from wavequant.domain.strategies.chart_drawing_history import ChartDrawingFrame
from wavequant.domain.strategies.market_calculations import MarketDay
from wavequant_api.application.backtest_preview import BacktestPreviewPublisher


def test_preview_copies_completed_prefix_and_throttles_but_publishes_last_day():
    bars = tuple(Bar(datetime(2026, 1, 1) + timedelta(days=i), 'TEST', 10, 12, 9, 11, 100)
                 for i in range(4))
    published = []
    clock = [0.0]
    publisher = BacktestPreviewPublisher(lambda revision, value: published.append((revision, value)),
                                         clock=lambda: clock[0])
    points = [dict(time='2026-01-01', value=10)]
    strokes = [dict(id='test', points=points)]
    result = dict(signals=[], orders=[], trades=[], audit=[], equity=[], backtest={})

    def day(index):
        result['equity'] = [dict(timestamp=bar.timestamp.isoformat(), equity=100) for bar in bars[:index + 1]]
        return MarketDay(ChartDrawingFrame(index, 0, points, dict(strokes=strokes)),
                         (dict(strokes=strokes),) * 3, (), (False, False, False))

    publisher.observe(bars, day(0))
    assert not published, 'market-only progress must not be published as a completed account day'
    publisher.observe(bars, day(0), result)
    points.append(dict(time='2026-01-02', value=12))
    assert published[0][1]['bars'][-1]['time'] == '2026-01-01'
    assert len(published[0][1]['market_bars']) == 4
    assert published[0][1]['market_asof'] == '2026-01-04'
    assert published[0][1]['market_bars'][:1] == published[0][1]['bars']
    assert published[0][1]['asof'] == '2026-01-01'
    assert len(published[0][1]['theory']['lecture_drawing']['strokes'][0]['points']) == 1
    publisher.observe(bars, day(1), result)
    assert len(published) == 1
    clock[0] = 3.0
    publisher.observe(bars, day(2), result)
    assert published[-1][0] == 3
    publisher.observe(bars, day(3), result)
    assert published[-1][0] == 4
    assert published[-1][1]['processed'] == 4
    assert published[-1][1]['total'] == 4
    assert published[-1][1]['market_bars'] is published[0][1]['market_bars']
    assert published[-1][1]['bars'] == published[-1][1]['market_bars']


def test_preview_requires_account_alignment_and_preserves_real_bs():
    bars = [Bar(datetime(2026, 1, 1), 'TEST', 10, 12, 9, 11, 100)]
    day = MarketDay(ChartDrawingFrame(0, 0, [], {'strokes': []}),
        ({'strokes': []},) * 3, (), (True, True, True))
    published = []
    publisher = BacktestPreviewPublisher(lambda _, value: published.append(value))
    signal = Signal(bars[0].timestamp, 'TEST', 0, 'LONG', 11, 9, 'test', bars[0].timestamp, 0, 2, 'bull', 15)
    result = dict(signals=[signal], orders=[], trades=[], audit=[], equity=[], backtest={})
    with pytest.raises(ValueError, match='finish the same market day'):
        publisher.observe(bars, day, result)
    result['equity'] = [dict(timestamp=bars[0].timestamp.isoformat(), equity=100)]
    result['orders'] = [dict(timestamp=bars[0].timestamp.isoformat(), side='BUY', status='filled', price=11)]
    publisher.observe(bars, day, result)
    assert {row['kind'] for row in published[0]['markers']} == {'fill', 'signal'}
    assert published[0]['signals'][0]['side'] == 'LONG'
    result['orders'].clear()
    assert published[0]['orders'], 'later mutation cannot erase an already published fill'
