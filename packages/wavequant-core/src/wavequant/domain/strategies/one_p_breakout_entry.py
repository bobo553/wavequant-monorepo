"""正 N 先到一饱后突破冻结末跌高的三交易日入场观察。"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from math import isfinite
from typing import cast

from ..models.model import Bar


ONE_P_BREAKOUT_NO_ENTRY = 'one_p_before_last_fall_high_breakout_no_entry'
ONE_P_BREAKOUT_WATCH = 'one_p_breakout_watch'


@dataclass(frozen=True)
class _Source:
    identity: str
    path: str
    attack: int
    known: int
    one_p: float
    reached: int | None = None


@dataclass(frozen=True)
class _Key:
    level: int
    path: str
    index: int
    ordinal: int
    price: float
    known: int

    @property
    def identity(self) -> tuple[int, str, int, int, float]:
        return self.level, self.path, self.index, self.ordinal, self.price


@dataclass(frozen=True)
class _Breakout:
    key: _Key
    index: int
    sources: tuple[_Source, ...]


def _items(value: object) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return cast(Sequence[object], value)


def _price(value: object) -> float | None:
    if type(value) not in (int, float):
        return None
    price = float(cast(float, value))
    return price if isfinite(price) and price > 0 else None


def _base_path(path: str) -> str:
    # 各级正式 stroke ID 逐层添加类别前缀，资格事件保留本级 ID。
    while path.startswith(('reversal-', 'secondary-', 'tertiary-')):
        path = path.split('-', 1)[1]
    return path


class OnePBreakoutStream:
    """消费已公布的各级关键位和结构 N，不重新计算趋势或交易资格。

    advance 必须逐日调用。当天观察行只带前日已知事实，拒绝函数再用实际
    决策时的高价判断；完整日 K 仅用于保存下一交易日的突破和到价状态。
    """

    def __init__(self, bars: Sequence[Bar]) -> None:
        self.bars = tuple(bars)
        self.events: list[dict[str, object]] = []
        self._dates = {bar.timestamp.date().isoformat(): index for index, bar in enumerate(bars)}
        self._index = -1
        self._event_cursor = 0
        self._pending_events: list[Mapping[str, object]] = []
        self._sources: dict[str, _Source] = {}
        self._seen_sources: set[str] = set()
        self._retired: set[str] = set()
        self._keys: dict[tuple[int, str], _Key] = {}
        self._paths: set[tuple[int, str]] = set()
        self._crossed: set[tuple[int, str, int, int, float]] = set()
        self._windows: list[_Breakout] = []

    def _clock(self, value: object) -> int | None:
        if type(value) is int and value >= 0:
            return value
        return self._dates.get(value) if isinstance(value, str) else None

    def _date(self, index: int) -> str:
        return self.bars[index].timestamp.date().isoformat()

    def _observe_sources(self, index: int, events: Sequence[Mapping[str, object]]) -> None:
        self._pending_events.extend(events[self._event_cursor:])
        self._event_cursor = len(events)
        pending: list[Mapping[str, object]] = []
        for event in self._pending_events:
            day = self._clock(event.get('bar_index'))
            known = self._clock(event.get('known_at', day))
            identity = event.get('n_id')
            if day is None or known is None or not isinstance(identity, str) or not identity:
                continue
            if max(day, known) > index:
                pending.append(event)
                continue
            if event.get('event') == 'n_structure_invalidated':
                self._retired.add(identity)
                self._sources.pop(identity, None)
                continue
            if (event.get('event') != 'n_structure_completed' or event.get('direction') != 'up'
                    or identity in self._seen_sources or identity in self._retired):
                continue
            one_p = _price(event.get('one_p'))
            path = event.get('source_path')
            attack = self._clock(event.get('attack', day))
            if (one_p is None or attack is None or attack > max(day, known)
                    or not isinstance(path, str) or not path):
                continue
            self._seen_sources.add(identity)
            # 延迟交付的事实也不能倒填观察日；首次真正消费时只使用收盘。
            self._sources[identity] = _Source(identity, path, attack, index, one_p)
        self._pending_events = pending
        bar = self.bars[index]
        for identity, source in self._sources.items():
            observed = bar.close if source.known == index else bar.high
            if source.reached is None and observed >= source.one_p:
                self._sources[identity] = replace(source, reached=index)

    def _read_keys(self, index: int, levels: Sequence[Mapping[str, object]]) -> None:
        keys: dict[tuple[int, str], _Key] = {}
        paths: set[tuple[int, str]] = set()
        for position, row in enumerate(levels, 1):
            level = row.get('trend_level', position)
            if type(level) is not int or level not in (1, 2, 3):
                continue
            for stroke in _items(row.get('strokes')):
                if not isinstance(stroke, Mapping):
                    continue
                path = stroke.get('source_path', stroke.get('id'))
                if not isinstance(path, str) or not path:
                    continue
                cutoffs = row.get('_path_market_end_indices')
                cutoff = cutoffs.get(path) if isinstance(cutoffs, Mapping) else None
                if type(cutoff) is int and index >= cutoff:
                    continue
                owner = level, path
                paths.add(owner)
                for point in reversed(_items(stroke.get('points'))):
                    if not isinstance(point, Mapping):
                        continue
                    if (point.get('state') in ('seed', 'developing') or point.get('display_only') is True
                            or point.get('wave_identity_state') == 'direction_only'):
                        continue
                    context = point.get('lifecycle_context')
                    if not isinstance(context, Mapping):
                        continue
                    known = self._clock(context.get('available_at'))
                    point_known = self._clock(point.get('available_at', known))
                    if known is None or point_known is None or max(known, point_known) > index:
                        continue
                    # 最新反向上下文结束未突破的旧门槛；已启动观察期另行保留。
                    if context.get('direction') != 'up':
                        break
                    key = context.get('key')
                    if not isinstance(key, Mapping) or key.get('kind') != 'H':
                        break
                    key_index = self._clock(key.get('index'))
                    key_known = self._clock(key.get('available_at'))
                    ordinal, price = key.get('ordinal', 0), _price(key.get('value'))
                    if (key_index is not None and key_known is not None and key_index <= key_known <= index
                            and type(ordinal) is int and ordinal >= 0 and price is not None):
                        keys[owner] = _Key(level, path, key_index, ordinal, price,
                            max(known, point_known, key_known))
                    break
        self._keys, self._paths = keys, paths

    def _publish(self, index: int, key: _Key, source: _Source, breakout: int, *, prior: bool) -> None:
        assert source.reached is not None
        self.events.append(dict(event=ONE_P_BREAKOUT_WATCH, bar_index=index, known_at=index,
            timestamp=self.bars[index].timestamp.isoformat(), symbol=self.bars[index].symbol,
            reason=ONE_P_BREAKOUT_NO_ENTRY, trend_level=key.level, source_path=key.path,
            key_index=key.index, key_date=self._date(key.index), key_price=key.price,
            key_known_index=key.known, n_id=source.identity, n_index=source.attack,
            n_date=self._date(source.attack), one_p=source.one_p, one_p_known_index=source.known,
            one_p_reached_index=source.reached, one_p_reached_date=self._date(source.reached),
            breakout_index=breakout, breakout_date=self._date(breakout),
            observation_day=index - breakout + 1, prior_breakout=prior))

    def advance(self, index: int, levels: Sequence[Mapping[str, object]],
                structural_events: Sequence[Mapping[str, object]]) -> None:
        """保存本日因果观察，未来日期、跳日及重复推进均不允许。"""
        if type(index) is not int or index != self._index + 1 or not 0 <= index < len(self.bars):
            raise ValueError('one-p breakout observation requires consecutive market dates')
        self._index = index
        retained: list[_Breakout] = []
        for window in self._windows:
            if index > window.index + 2 or (window.key.level, window.key.path) not in self._paths:
                continue
            sources = tuple(source for source in window.sources if source.identity in self._sources)
            if not sources:
                continue
            retained.append(replace(window, sources=sources))
            for source in sources:
                self._publish(index, window.key, source, window.index, prior=True)
        self._windows = retained
        for key in self._keys.values():
            if key.identity in self._crossed or key.known >= index:
                continue
            sources = tuple(source for source in self._sources.values()
                if _base_path(source.path) == _base_path(key.path)
                and source.attack > key.index and source.known < index
                and source.reached is not None and source.reached < index)
            for source in sources:
                self._publish(index, key, source, index, prior=False)
            # 首破时未到一饱也登记已突破，避免日后到价反向补出三日禁买。
            if self.bars[index].high > key.price:
                self._crossed.add(key.identity)
                if sources:
                    self._windows.append(_Breakout(key, index, sources))
        self._observe_sources(index, structural_events)
        self._read_keys(index, levels)


def one_p_breakout_entry_rejection(
    events: Sequence[Mapping[str, object]], index: int, *, observed_high: float,
) -> dict[str, object] | None:
    """仅消费本日观察及决策时可见高价；不借用该日后续完整 K 线。"""
    high = _price(observed_high)
    if type(index) is not int or index < 0 or high is None:
        return None
    for event in events:
        if event.get('event') != ONE_P_BREAKOUT_WATCH or event.get('bar_index') != index:
            continue
        known = event.get('known_at', index)
        reached, key_known = event.get('one_p_reached_index'), event.get('key_known_index')
        source_known, breakout = event.get('one_p_known_index'), event.get('breakout_index')
        price = _price(event.get('key_price'))
        if (type(known) is not int or not 0 <= known <= index
                or type(breakout) is not int or breakout < 0
                or type(reached) is not int or not 0 <= reached < breakout
                or type(source_known) is not int or not 0 <= source_known <= reached
                or type(key_known) is not int or not 0 <= key_known < breakout
                or not 0 <= index - breakout <= 2 or price is None):
            continue
        prior = event.get('prior_breakout') is True
        if (prior and breakout >= index) or (not prior and (breakout != index or high <= price)):
            continue
        return dict({key: value for key, value in event.items()
                     if key not in ('event', 'bar_index', 'timestamp', 'symbol')},
                    observed_high=high)
    return None

