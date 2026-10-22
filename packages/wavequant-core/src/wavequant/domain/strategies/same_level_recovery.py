"""同级末升低破位后的资格门槛；只消费共享趋势节点，不重判趋势。"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import isfinite
from typing import cast

from ..market_structure.trend_confirmation import first_strict_market_break
from ..market_structure.trend_levels import _completed_source_point
from ..market_structure.trend_lifecycle import _Pivot, _StructureClocks, _pivot, _records
from ..market_structure.trend_market_index import _MarketRangeIndex
from ..models.model import Bar


RECOVERY_REQUIRED = 'trading_same_level_recovery_required'
RECOVERY_CLEARED = 'trading_same_level_recovery_cleared'


@dataclass(frozen=True)
class _Barrier:
    high: _Pivot
    low: _Pivot
    breach: int


@dataclass(frozen=True)
class _Keys:
    low: _Pivot
    high: _Pivot
    decline_low: _Pivot | None


def _keys(value: object, dates: Mapping[str, int], now: int) -> _Keys | None:
    points = [(raw, point) for raw in _records(value)
              if raw.get('wave_identity_state') != 'direction_only'
              and (point := _pivot(raw, dates, now)) is not None]
    if any((a.index, a.ordinal) >= (b.index, b.ordinal)
           for (_, a), (_, b) in zip(points, points[1:])):
        return None
    position = next((i for i in range(len(points) - 1, 0, -1) if points[i][1].kind == 'H'), None)
    if position is None:
        return None
    low, high = points[position - 1][1], points[position][1]
    if low.kind != 'L' or not low.index < high.index or not 0 < low.price < high.price:
        return None
    decline_low = None
    if position + 1 < len(points):
        # 高点须已结束、随后本级正式低点须破前低；不等待下降腿再向上结束。
        # 发展尾和下级局部反弹仍不能降低门槛。
        completed_high = _pivot(_completed_source_point(points[position][0]), dates, now)
        following = points[position + 1][1]
        if (completed_high is not None and following is not None and following.kind == 'L'
                and following.index > high.index and 0 < following.price < low.price):
            decline_low = following
            high = completed_high
    return _Keys(low, high, decline_low)


def _reference(point: _Pivot) -> dict[str, object]:
    return dict(index=point.index, ordinal=point.ordinal, kind=point.kind,
                value=point.price, available_at=point.known, state='confirmed')


class SameLevelRecovery:
    """行情破位事实跨资格关闭保留；事件重放与逐日计算使用相同门槛。"""

    def __init__(self) -> None:
        self._blocked: dict[str, _Barrier] = {}
        self._released: dict[str, int] = {}
        self._prepared: dict[str, tuple[object, int | None, _Keys | None]] = {}
        self._clocks: dict[str, _StructureClocks] = {}

    def permits(self, path: str, event: Mapping[str, object]) -> bool:
        if path in self._blocked:
            return False
        release = self._released.get(path)
        flip = event.get('flip_index')
        # 解除门槛并非开放资格；旧翻多与旧交替低点不能在恢复后补开历史红区间。
        return release is None or type(flip) is int and flip >= release

    def observe(self, event: Mapping[str, object], now: int, path: str) -> bool:
        kind = event.get('event')
        if kind == 'trend_lifecycle_invalidated' and event.get('reason') == 'source_path_ended':
            self._blocked.pop(path, None)
            self._released.pop(path, None)
            return False
        if kind not in (RECOVERY_REQUIRED, RECOVERY_CLEARED) or event.get('available_index') != now:
            return False
        high, low = _pivot(event.get('recovery_high'), {}, now), _pivot(event.get('broken_low'), {}, now)
        if (high is None or low is None or high.kind != 'H' or low.kind != 'L'
                or not 0 <= low.index < high.index <= high.known <= now
                or low.known >= now or not 0 < low.price < high.price):
            return False
        pending = self._blocked.get(path)
        if kind == RECOVERY_CLEARED:
            price = event.get('market_high')
            if (pending is None or (pending.high, pending.low) != (high, low)
                    or now <= max(pending.breach, high.known)
                    or type(price) not in (int, float) or not isfinite(cast(float, price)) or cast(float, price) <= high.price):
                return False
            self._blocked.pop(path)
            self._released[path] = now
            return True
        price = event.get('market_low')
        if pending is None:
            if type(price) not in (int, float) or not isfinite(cast(float, price)) or not 0 < cast(float, price) < low.price:
                return False
            breach = now
        else:
            completed = _pivot(event.get('decline_low'), {}, now)
            if (completed is None or completed.kind != 'L' or not 0 < completed.price < low.price
                    or not pending.high.index < high.index < completed.index
                    or high.index <= pending.breach):
                return False
            breach = pending.breach
        self._released.pop(path, None)
        self._blocked[path] = _Barrier(high, low, breach)
        return True

    def advance(self, row: Mapping[str, object], bars: Sequence[Bar], now: int,
                dates: Mapping[str, int], *, owned: bool = False,
                market_index: _MarketRangeIndex | None = None) -> list[dict[str, object]]:
        if not owned:
            self._prepared.clear()
            self._clocks.clear()
        events: list[dict[str, object]] = []
        present: set[str] = set()
        cutoffs = row.get('_path_market_end_indices')
        for stroke in _records(row.get('strokes')):
            path = stroke.get('id')
            if not isinstance(path, str):
                continue
            cutoff = cutoffs.get(stroke.get('source_path')) if isinstance(cutoffs, Mapping) else None
            if type(cutoff) is int and now >= cutoff:
                continue
            present.add(path)
            value = stroke.get('points')
            cached = self._prepared.get(path)
            if cached is not None and cached[0] is value and (cached[1] is None or now < cached[1]):
                keys = cached[2]
            else:
                keys = _keys(value, dates, now)
                standard, following = self._clocks.setdefault(path, _StructureClocks()).advance(value, dates, now)
                if owned and standard:
                    self._prepared[path] = value, following, keys
            pending = self._blocked.get(path)
            base: dict[str, object] = dict(bar_index=now, available_index=now,
                trend_level=row.get('trend_level'), source_path=path)
            if pending is not None:
                # 当天先突破原门槛时不再用稍后形成的新下降段替换它。
                if now > max(pending.breach, pending.high.known) and bars[now].high > pending.high.price:
                    events.append(dict(base, event=RECOVERY_CLEARED,
                        recovery_high=_reference(pending.high), broken_low=_reference(pending.low),
                        market_high=bars[now].high))
                elif (keys is not None and keys.decline_low is not None
                      and keys.high.index > max(pending.high.index, pending.breach)):
                    events.append(dict(base, event=RECOVERY_REQUIRED, recovery_high=_reference(keys.high),
                        broken_low=_reference(keys.low), decline_low=_reference(keys.decline_low)))
                continue
            if keys is None or keys.low.known >= now or bars[now].low >= keys.low.price:
                continue
            # 已被后续行情突破的旧高点不能在未来再次破低时复活。
            if first_strict_market_break(bars, keys.high.index + 1, now + 1, keys.high.price,
                                        up=True, market_index=market_index) is not None:
                continue
            events.append(dict(base, event=RECOVERY_REQUIRED, recovery_high=_reference(keys.high),
                broken_low=_reference(keys.low), market_low=bars[now].low))
        self._prepared = {path: value for path, value in self._prepared.items() if path in present}
        self._clocks = {path: value for path, value in self._clocks.items() if path in present}
        return events
