"""Join one N to a previously known higher-level high without future pivots."""

from bisect import bisect_left, bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from ..market_structure.trend_market_index import _MarketRangeIndex


@dataclass(frozen=True, slots=True)
class _ResistanceKey:
    index: int
    value: float
    known_at: int
    trend_level: int
    position: int


class MultilevelResistanceIndex:
    """每份资格事件只提取一次阻力，同一个 N 的所有帧共享攻击日选择。

    按来源位置裁剪到 N 起点与攻击之间；可知日仍必须严格早于攻击。
    索引只属于本次策略调用，后续日期修订旧证据时重新建立，不能沿用旧选择。
    """

    def __init__(self, events: Sequence[Mapping[str, object]]) -> None:
        keys: list[_ResistanceKey] = []
        for position, event in enumerate(events):
            if (event['event'] != 'hierarchy_resistance_key'
                    or event.get('trend_level') not in (2, 3) or not event.get('key')):
                continue
            key = cast(Mapping[str, object], event['key'])
            keys.append(_ResistanceKey(cast(int, key['index']), cast(float, key['value']),
                cast(int, event['bar_index']), cast(int, event['trend_level']), position))
        self._keys = sorted(keys, key=lambda key: key.index)
        self._indices = [key.index for key in self._keys]
        self._selected: dict[tuple[int, int], _ResistanceKey | None] = {}

    def select(self, *, origin: int, attack: int) -> _ResistanceKey | None:
        identity = origin, attack
        if identity not in self._selected:
            start = bisect_right(self._indices, origin)
            stop = bisect_left(self._indices, attack)
            self._selected[identity] = max(
                (key for key in self._keys[start:stop] if key.known_at < attack),
                key=lambda key: (key.value, key.known_at, -key.position), default=None,
            )
        return self._selected[identity]


def multilevel_squeeze(bars, candidate, frame,
                       key_events: Sequence[Mapping[str, object]] | MultilevelResistanceIndex, *,
                       _market_index: _MarketRangeIndex | None = None):
    attack, now = candidate["attack"], candidate["start"] + frame.bar_index
    setup = candidate["setup"]
    if now < attack + 2 or frame.first_defense_breach_index is not None:
        return None
    index = key_events if isinstance(key_events, MultilevelResistanceIndex) else MultilevelResistanceIndex(key_events)
    key = index.select(origin=setup.origin.index, attack=attack)
    if key is None:
        return None
    if key.index != setup.neckline.index:
        return None
    if not bars[attack - 1].high <= key.value < bars[attack].close:
        return None
    bar, prev = bars[now], bars[now - 1]
    record = (bars[_market_index.extreme_position(attack, now, high=True)].high
        if _market_index is not None and _market_index.bars is bars and _market_index.exact_prices else
        max(b.high for b in bars[attack:now]))
    if not (
        bar.open >= prev.close
        and bar.close > bar.open
        and bar.close > record
        and bar.low >= min(prev.low, bars[now - 2].close)
        and bar.volume > prev.volume
    ):
        return None
    # Supply on the current candle is not declared absent: the close has
    # defeated the *previous* resistance record at both scales despite it.
    return dict(
        buy_point_type="multilevel_breakout_squeeze",
        definition="multilevel_breakout_squeeze_v1",
        priority=1,
        trend_level=key.trend_level,
        key_source_index=key.index,
        key_price=key.value,
        key_known_index=key.known_at,
        origin_index=setup.origin.index,
        pullback_index=setup.pullback.index,
        counter_ratio=candidate["force"].ratio,
        counter_filter_applied=False,
        higher_breakout_index=attack,
        higher_confirmation_index=now,
        higher_resistance_high=record,
        higher_defense=candidate["n"].completion.defense,
        confirmation_close=bar.close,
        confirmation_low=bar.low,
        confirmation_volume=bar.volume,
        previous_volume=prev.volume,
    )
