"""Causal trading permission and its alternation-low structural range."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from math import isfinite
from typing import cast

from ..market_structure.trend_lifecycle import _pivot, _records
from ..market_structure.confirmation_gap import confirmation_gap
from ..market_structure.source_bear_exit import SOURCE_BEAR_EVENT, SOURCE_BEAR_EXIT, confirmed_source_bear
from ..models.model import Bar


@dataclass(frozen=True)
class TradingInterval:
    trend_level: int
    source_path: str
    start_index: int
    start_flip_index: int
    end_index: int | None = None
    end_flip_index: int | None = None
    end_reason: str | None = None
    start_low_index: int | None = None
    start_low_ordinal: int = 0
    start_low_price: float | None = None
    end_key_index: int | None = None
    end_key_price: float | None = None
    confirmation_gap_index: int | None = None
    confirmation_gap_lower: float | None = None
    confirmation_gap_upper: float | None = None
    confirmation_gap_kind: str | None = None
    gap_replaced_index: int | None = None


class TradingEligibility:
    """确认交替开放交易；缺口、本级翻转及直接来源空头确认结束资格。"""

    def __init__(self, level: int) -> None:
        if type(level) is not int or level not in (1, 2, 3):
            raise ValueError('trading eligibility level must be 1, 2 or 3')
        self.level = level
        self._intervals: list[TradingInterval] = []
        self._opened: dict[str, int] = {}
        self._last_index = -1

    @property
    def intervals(self) -> tuple[TradingInterval, ...]:
        return tuple(self._intervals)

    @property
    def active(self) -> bool:
        """当时已确认的资格；查询不读取未来区间终点。"""
        return bool(self._opened)

    @property
    def active_intervals(self) -> tuple[TradingInterval, ...]:
        """只返回当前打开的窗口及其交替低点，不含未来终点。"""
        return tuple(self._intervals[position] for position in self._opened.values())

    def observe(self, event: Mapping[str, object]) -> bool | None:
        """返回本路径本次开关变化；候选及不改变资格的事件返回 None。"""
        now, path = event.get('bar_index'), event.get('source_path')
        if event.get('trend_level') != self.level or type(now) is not int or now < 0 or not isinstance(path, str):
            return None
        if now < self._last_index:
            raise ValueError('trading eligibility requires chronological events')
        self._last_index = now
        if event.get('event') == 'trend_lifecycle_confirmation_revoked' and event.get('reason') == 'confirmation_gap_filled':
            position = self._opened.get(path)
            if position is None:
                return None
            window = self._intervals[position]
            low = event.get('market_low')
            if (window.confirmation_gap_index is None or window.confirmation_gap_lower is None
                    or window.gap_replaced_index is not None
                    or now <= window.start_index
                    or event.get('qualification_index') != window.start_index
                    or event.get('confirmation_gap_index') != window.confirmation_gap_index
                    or event.get('confirmation_gap_lower') != window.confirmation_gap_lower
                    or event.get('confirmation_gap_upper') != window.confirmation_gap_upper
                    or event.get('confirmation_gap_kind') != window.confirmation_gap_kind
                    or type(low) not in (int, float) or not isfinite(low)
                    or not 0 < low <= window.confirmation_gap_lower):
                return None
            return self._close(path, now, 'confirmation_gap_filled')
        if event.get('event') == SOURCE_BEAR_EVENT:
            proof = event.get('source_confirmation')
            if (not isinstance(proof, Mapping) or proof.get('trend_level') != self.level - 1
                    or not isinstance(event.get('direct_source_path'), str)
                    or proof.get('source_path') != event.get('direct_source_path')
                    or event.get('available_index') != now or not confirmed_source_bear(proof, now)):
                return None
            # 只有资格关闭；不把下级空头写成本级翻空，也不强制清仓。
            return self._close(path, now, SOURCE_BEAR_EXIT,
                key_index=cast(int, proof['flip_index']), key_price=float(cast(float, proof['breakout_level'])))
        if event.get('event') == 'trading_reversal_low_broken':
            index, price = event.get('reversal_low_index'), event.get('reversal_low_price')
            known, low = event.get('reversal_low_known_index'), event.get('market_low')
            if (type(index) is not int or type(known) is not int or not 0 <= index <= known < now
                    or type(price) not in (int, float) or not isfinite(price) or price <= 0
                    or type(low) not in (int, float) or not isfinite(low) or low >= price):
                return None
            return self._close(path, now, 'reversal_low_broken', key_index=index, key_price=float(price))
        if (event.get('event') == 'trend_lifecycle_invalidated'
                and event.get('reason') in ('source_evidence_revised', 'source_path_ended',
                                            'bull_recovery_pending', 'special_bull_origin_broken')):
            return self._close(path, now, str(event['reason']))
        if event.get('event') != 'trend_lifecycle_state':
            return None
        state = event.get('state')
        if state not in ('空多交替', '多空交替'):
            return None
        # 开窗必须携带本级普通/特殊翻多证明；孤立交替标签及旧版事件不能授权交易。
        if state == '空多交替' and event.get('flip_confirmed') is not True:
            return None
        flip, known, counter = event.get('flip_index'), event.get('flip_known_index'), event.get('counter_index')
        if (type(flip) is not int or type(known) is not int or type(counter) is not int
                or not 0 <= flip <= known <= now or not flip <= counter <= now
                or event.get('alternation_index') != now):
            return None
        gap = confirmation_gap(event.get('confirmation_rule'), event.get('confirmation_evidence'), now)
        if state == '空多交替' and path in self._opened:
            position = self._opened[path]
            window = self._intervals[position]
            if (event.get('replaces_gap_confirmation_index') == window.confirmation_gap_index
                    and window.confirmation_gap_index is not None and gap is None):
                # 新的独立确认维持同一窗口；旧缺口此后不再决定该窗口的资格。
                self._intervals[position] = replace(window, gap_replaced_index=now)
            return None
        if state == '空多交替' and path not in self._opened:
            ordinal = event.get('counter_ordinal', 0)
            price = event.get('counter_price')
            if type(ordinal) is not int or ordinal < 0:
                return None
            self._opened[path] = len(self._intervals)
            self._intervals.append(TradingInterval(self.level, path, now, flip,
                start_low_index=counter, start_low_ordinal=ordinal,
                start_low_price=float(price) if type(price) in (int, float) and isfinite(price) and price > 0 else None,
                confirmation_gap_index=gap[0] if gap else None,
                confirmation_gap_lower=gap[1] if gap else None,
                confirmation_gap_upper=gap[2] if gap else None,
                confirmation_gap_kind=gap[3] if gap else None))
            return True
        if state == '多空交替':
            return self._close(path, now, 'bear_alternation_completed', flip)
        return None

    def close_on_confirmation_gap_fill(self, bars: Sequence[Bar], now: int) -> list[dict[str, object]]:
        """周期因其他价格规则退出后，仍需收尾仅依赖跳空的资格窗口。"""
        events: list[dict[str, object]] = []
        for window in self.active_intervals:
            if (window.confirmation_gap_lower is None or window.gap_replaced_index is not None
                    or now <= window.start_index or bars[now].low > window.confirmation_gap_lower):
                continue
            event: dict[str, object] = dict(event='trend_lifecycle_confirmation_revoked',
                state='空多交替', reason='confirmation_gap_filled', bar_index=now, available_index=now,
                trend_level=self.level, source_path=window.source_path, qualification_index=window.start_index,
                confirmation_gap_index=window.confirmation_gap_index,
                confirmation_gap_lower=window.confirmation_gap_lower,
                confirmation_gap_upper=window.confirmation_gap_upper,
                confirmation_gap_kind=window.confirmation_gap_kind, market_low=bars[now].low)
            if self.observe(event) is False:
                event['buy_eligible'] = False
                events.append(event)
        return events

    def close_on_reversal_low_break(
        self, row: Mapping[str, object], bars: Sequence[Bar], now: int, dates: Mapping[str, int],
    ) -> list[dict[str, object]]:
        """只使用本级当日因果几何，低点须在当日开始前已确认；相等触及不关闭。"""
        if row.get('trend_level') != self.level or not self._opened:
            return []
        events: list[dict[str, object]] = []
        for stroke in _records(row.get('strokes')):
            path = stroke.get('id')
            if not isinstance(path, str) or path not in self._opened:
                continue
            window = self._intervals[self._opened[path]]
            anchor = window.start_low_index if window.start_low_index is not None else window.start_flip_index
            lows = (point for raw in _records(stroke.get('points'))
                    if (point := _pivot(raw, dates, now)) is not None
                    and point.kind == 'L' and point.known < now and point.index >= anchor)
            key = max(lows, key=lambda point: (point.index, point.ordinal), default=None)
            if key is None or bars[now].low >= key.price:
                continue
            event: dict[str, object] = dict(event='trading_reversal_low_broken', bar_index=now,
                available_index=now, trend_level=self.level, source_path=path,
                reversal_low_index=key.index, reversal_low_known_index=key.known,
                reversal_low_price=key.price, market_low=bars[now].low,
                confirmation_rule='strict_low_break_of_known_same_level_reversal_low')
            if self.observe(event) is False:
                event['buy_eligible'] = False
                events.append(event)
        return events

    def _close(self, path: str, now: int, reason: str, flip: int | None = None, *,
               key_index: int | None = None, key_price: float | None = None) -> bool | None:
        position = self._opened.pop(path, None)
        if position is None:
            return None
        self._intervals[position] = replace(self._intervals[position], end_index=now,
            end_flip_index=flip, end_reason=reason, end_key_index=key_index, end_key_price=key_price)
        return False


def confirmed_trading_intervals(
    events: Sequence[Mapping[str, object]], size: int, level: int,
) -> tuple[TradingInterval, ...]:
    """Use confirmation clocks, preserving open tails without requiring a future endpoint.

    Same-level reversal-low breaches close the window on their observation day.
    Candidates do not authorize trading. Revised/ended sources and completed
    opposite alternations also close it without joining unrelated paths.
    """
    if type(level) is not int or level not in (1, 2, 3) or type(size) is not int or size < 0:
        raise ValueError('valid selected level and history size required')
    dated: dict[int, list[Mapping[str, object]]] = {}
    for event in events:
        now = event.get('bar_index')
        if event.get('trend_level') == level and type(now) is int and 0 <= now < size:
            dated.setdefault(now, []).append(event)
    eligibility = TradingEligibility(level)
    for now in sorted(dated):
        for event in dated[now]:
            eligibility.observe(event)
    return eligibility.intervals


def interval_days(intervals: Sequence[TradingInterval], size: int, *, include_end: bool = False) -> tuple[bool, ...]:
    """Union overlapping windows in O(days + windows); closing day is sell-only."""
    if type(size) is not int or size < 0:
        raise ValueError('nonnegative market size required')
    changes = [0] * (size + 1)
    for interval in intervals:
        end = size if interval.end_index is None else min(size, interval.end_index + int(include_end))
        if 0 <= interval.start_index < end <= size:
            changes[interval.start_index] += 1
            changes[end] -= 1
    active = 0
    result = []
    for delta in changes[:-1]:
        active += delta
        result.append(active > 0)
    return tuple(result)
