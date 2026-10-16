"""Build a throttled chart snapshot from the current worker's completed day."""

from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import asdict, is_dataclass
from datetime import datetime
import json
from time import monotonic
from typing import cast

from wavequant.domain.models.model import Bar
from wavequant.domain.market_structure.trend_display import publish_trend_display
from wavequant.domain.strategies.market_calculations import MarketDay
from wavequant.application.analytics.trade_evidence import result_markers


class BacktestPreviewPublisher:
    def __init__(self, publish: Callable[[int, dict[str, object]], None], *,
                 clock: Callable[[], float] = monotonic) -> None:
        self.publish = publish
        self.clock = clock
        self.last_publish: float | None = None
        self.source: Sequence[Bar] | None = None
        self.market_bars: list[dict[str, object]] = []

    def observe(self, bars: Sequence[Bar], day: MarketDay, result: Mapping[str, object] | None = None) -> None:
        # 只有账户已经消费同一天才能发布统一回测进度，市场准备不是回测完成。
        if result is None:
            return
        if self.source is None:
            self.source = bars
            # 完整行情只用于图表底图；策略和账户仍只发布已完成交易日的前缀。
            self.market_bars = [
                dict(time=bar.timestamp.date().isoformat(), open=bar.open, high=bar.high,
                     low=bar.low, close=bar.close, volume=bar.volume,
                     factor=bar.adjustment_factor, raw_close=bar.close / bar.adjustment_factor)
                for bar in bars
            ]
        if bars is not self.source:
            return
        now = self.clock()
        index = day.frame.index
        if self.last_publish is not None and now - self.last_publish < 3 and index != len(bars) - 1:
            return
        # 拷贝绘图几何，不保留会继续变化的领域对象，也不传递资格证明树。
        names = ('reversal_trends', 'secondary_trends', 'tertiary_trends')
        theory: dict[str, object] = {
            name: deepcopy({key: row[key] for key in ('strokes', 'developing_strokes') if key in row})
            for name, row in zip(names, day.levels)
        }
        asof = bars[index].timestamp.date().isoformat()
        theory.update(asof=asof, lecture_drawing=deepcopy(day.frame.drawing),
                      shapes=[], events=[], points=[], polyline_segments=[], counts={},
                      computed_from='running_backtest_completed_trading_day')
        candles = self.market_bars[:index + 1]
        def encode(value: object) -> object:
            if isinstance(value, datetime):
                return value.isoformat()
            if is_dataclass(value) and not isinstance(value, type):
                return asdict(value)
            raise TypeError(f'unsupported backtest preview value: {type(value).__name__}')
        account = json.loads(json.dumps(dict(result), default=encode, allow_nan=False))
        equity = account.pop('equity')
        if len(equity) != index + 1 or equity[-1]['timestamp'][:10] != asof:
            raise ValueError('backtest preview requires the account to finish the same market day')
        if any(row['timestamp'][:10] > asof for key in ('signals', 'orders', 'audit') for row in account[key]):
            raise ValueError('backtest preview cannot contain future decisions or executions')
        if any(row['exit_time'][:10] > asof for row in account['trades']):
            raise ValueError('backtest preview cannot contain future trades')
        for signal in account['signals']:
            signal['time'] = signal['timestamp'][:10]
        account['markers'] = result_markers(account)
        account['curve'] = equity
        account['metrics'] = {}
        for level, (name, row) in enumerate(zip(names, day.levels), start=1):
            if level >= 2 and row.get('scope') == 'structural_trend_points_with_independent_strategy_permissions':
                theory[name] = publish_trend_display(cast(Mapping[str, object], theory[name]),
                    account['audit'], level=level, asof_index=index,
                    source_row=day.levels[1] if level == 3 else None)
        self.publish(index + 1, dict(symbol=bars[0].symbol, asof=asof, timeframe='1d',
            processed=index + 1, total=len(bars), bars=candles, theory=theory,
            market_bars=self.market_bars, market_asof=self.market_bars[-1]['time'],
            phase='daily', **account))
        self.last_publish = now
