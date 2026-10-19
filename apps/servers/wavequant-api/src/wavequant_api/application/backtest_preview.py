"""Build a throttled chart snapshot from the current worker's completed day."""

from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import asdict, fields, is_dataclass
from datetime import datetime
import json
from math import isfinite
from time import monotonic
from typing import cast

from wavequant.domain.models.model import Bar
from wavequant.domain.market_structure.trend_display import publish_trend_display
from wavequant.domain.strategies.market_calculations import MarketDay
from wavequant.application.analytics.trade_evidence import result_markers


def _preview_value(value: object) -> object:
    """发布时一次性复制为 JSON 值；账户全文无需先编码为字符串再解析。"""
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float:
        if not isfinite(value):
            raise ValueError('backtest preview requires finite numbers')
        return value
    if type(value) is dict and all(type(key) is str for key in cast(dict[object, object], value)):
        return {key: _preview_value(item) for key, item in value.items()}
    if type(value) in (list, tuple):
        return [_preview_value(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _preview_value(getattr(value, field.name)) for field in fields(value)}

    def encode(item: object) -> object:
        if isinstance(item, datetime):
            return item.isoformat()
        if is_dataclass(item) and not isinstance(item, type):
            return asdict(item)
        raise TypeError(f'unsupported backtest preview value: {type(item).__name__}')

    # 非标准容器、非字符串键和标量子类保留原 JSON 边界行为。
    return json.loads(json.dumps(value, default=encode, allow_nan=False))


def _preview_rows(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise ValueError('backtest preview requires account record lists')
    return cast(list[dict[str, object]], value)


def _preview_date(row: Mapping[str, object], key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str):
        raise ValueError('backtest preview requires dated account records')
    return value[:10]


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
        account = cast(dict[str, object], _preview_value(dict(result)))
        equity = _preview_rows(account.pop('equity'))
        signals = _preview_rows(account['signals'])
        orders = _preview_rows(account['orders'])
        audit = _preview_rows(account['audit'])
        trades = _preview_rows(account['trades'])
        if len(equity) != index + 1 or _preview_date(equity[-1], 'timestamp') != asof:
            raise ValueError('backtest preview requires the account to finish the same market day')
        if any(_preview_date(row, 'timestamp') > asof for rows in (signals, orders, audit) for row in rows):
            raise ValueError('backtest preview cannot contain future decisions or executions')
        if any(_preview_date(row, 'exit_time') > asof for row in trades):
            raise ValueError('backtest preview cannot contain future trades')
        for signal in signals:
            signal['time'] = _preview_date(signal, 'timestamp')
        account['markers'] = result_markers(account)
        account['curve'] = equity
        account['metrics'] = {}
        for level, (name, row) in enumerate(zip(names, day.levels), start=1):
            if level >= 2 and row.get('scope') == 'structural_trend_points_with_independent_strategy_permissions':
                theory[name] = publish_trend_display(cast(Mapping[str, object], theory[name]),
                    audit, level=level, asof_index=index,
                    source_row=day.levels[1] if level == 3 else None)
        self.publish(index + 1, dict(symbol=bars[0].symbol, asof=asof, timeframe='1d',
            processed=index + 1, total=len(bars), bars=candles, theory=theory,
            market_bars=self.market_bars, market_asof=self.market_bars[-1]['time'],
            phase='daily', **account))
        self.last_publish = now
