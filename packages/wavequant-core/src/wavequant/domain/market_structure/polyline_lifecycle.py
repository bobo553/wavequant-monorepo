"""把当时可知的技术折线峰谷交给六态引擎，不先归并成一级趋势。

峰谷适配复用图表的包含关系规则；结构事实采用与趋势注释相同的严格
关键位和小于 67% 的部分回撤定义。价格确认仍由 TrendLifecycle 统一
处理。本模块不读取 K 线，也不为折线补造下一级趋势或整波许可证。
"""

from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from math import isfinite
from typing import Protocol, cast

from .price_action import Direction
from .trend_history_changes import PolylineSourceGate
from .trend_lines import polyline_turns
from .trend_structure import retracement_evidence


class _DrawingFrame(Protocol):
    index: int
    epoch: int
    raw: list[dict[str, object]]


@dataclass(frozen=True, slots=True)
class _Point:
    index: int
    ordinal: int
    kind: str
    value: float
    known: int

    @property
    def order(self) -> tuple[int, int]:
        return self.index, self.ordinal

    def reference(self) -> dict[str, object]:
        return dict(index=self.index, ordinal=self.ordinal, kind=self.kind,
                    value=self.value, available_at=self.known, trend_level=0)


@dataclass(slots=True)
class _Attack:
    origin: _Point
    extreme: _Point
    anchor: _Point
    key: _Point
    checked: bool = False


def _clock(value: object, dates: Mapping[str, int]) -> int:
    if type(value) is int and value >= 0:
        return value
    if isinstance(value, str):
        known = int(value) if value.isdecimal() else dates.get(value)
        if type(known) is int and known >= 0:
            return known
    raise ValueError('polyline points require a known confirmation date')


def _validate_raw(points: Sequence[Mapping[str, object]], dates: Mapping[str, int], now: int) -> None:
    for point in points:
        index, ordinal, value = point.get('index'), point.get('ordinal', 0), point.get('value')
        known = _clock(point.get('available_at'), dates)
        if (type(index) is not int or type(ordinal) is not int or not 0 <= index <= known <= now
                or ordinal < 0 or type(value) not in (int, float) or not isfinite(value) or value <= 0
                or point.get('kind') not in ('H', 'L') or not isinstance(point.get('time'), str)
                or point.get('state') not in ('seed', 'developing', 'confirmed', 'teaching', 'reversal')
                or 'trend_level' in point and (type(point['trend_level']) is not int or point['trend_level'] != 0)):
            raise ValueError('polyline lifecycle requires finite points from the current causal frame')


class _PolylineStructure:
    """每个来源路径独占的追加状态；源点修订时由调用方重新建立。

这里只保留最新两高两低及冻结关键位，结构推进每个确认点为 O(1)。
输出附加事实但不压缩原峰谷，故保留折线自身的观察级别。
"""

    def __init__(self) -> None:
        self.points: list[dict[str, object]] = []
        self.source: list[_Point] = []
        self._prices: dict[str, tuple[float, ...]] = {'H': (), 'L': ()}
        self._extremes: dict[str, tuple[_Point, _Point | None]] = {}
        self._bull: bool | None = None
        self._anchor: _Point | None = None
        self._key: _Point | None = None
        self._attack: _Attack | None = None
        self._suspicion = False

    def append(self, point: _Point) -> None:
        previous = self.source[-1] if self.source else None
        if previous is not None and (point.order <= previous.order or point.known < previous.known
                or point.kind == previous.kind
                or (point.value <= previous.value if point.kind == 'H' else point.value >= previous.value)):
            raise ValueError('polyline lifecycle requires ordered alternating confirmed extrema')
        self.source.append(point)
        self._prices[point.kind] = (*self._prices[point.kind][-1:], point.value)
        old_extreme = self._extremes.get(point.kind)
        sign = 1 if point.kind == 'H' else -1
        if old_extreme is None or sign * (point.value - old_extreme[0].value) > 0:
            self._extremes[point.kind] = point, previous
        observations: list[dict[str, object]] = []
        self.points.append(dict(point.reference(), state='reversal', observations=observations))

        def observe(title: str, **extra: object) -> None:
            observations.append(dict(title=title, available_at=point.known,
                basis='confirmed_polyline_extreme', **extra))

        if self._bull is None:
            highs, lows = self._prices['H'], self._prices['L']
            if len(highs) == len(lows) == 2:
                if highs[1] > highs[0] and lows[1] > lows[0]:
                    self._bull = True
                elif highs[1] < highs[0] and lows[1] < lows[0]:
                    self._bull = False
            if self._bull is not None:
                self._anchor, self._key = self._extremes['H' if self._bull else 'L']
            return
        if self._key is None:
            self._anchor, self._key = self._extremes['H' if self._bull else 'L']
            return
        assert self._anchor is not None
        up = not self._bull
        attack_kind, sign = ('H', 1) if up else ('L', -1)
        attack = self._attack
        if attack is None:
            if (point.kind == self._anchor.kind and previous is not None
                    and sign * (point.value - self._anchor.value) < 0):
                self._anchor, self._key = point, previous
                self._suspicion = False
            elif (point.kind == self._anchor.kind and previous is not None
                    and sign * (point.value - self._anchor.value) >= 0
                    and sign * (previous.value - self._key.value) < 0):
                if not self._suspicion:
                    observe('底部疑虑' if up else '头部疑虑', key=self._key.reference())
                    self._suspicion = True
            if point.kind == attack_kind and sign * (point.value - self._key.value) > 0 and previous is not None:
                self._attack = _Attack(previous, point, self._anchor, self._key)
                extreme = {'confirmed_low' if up else 'confirmed_high': self._anchor.reference()}
                observe('翻空为多' if up else '翻多为空', key=self._key.reference(), **extreme)
        elif point.kind != attack_kind and not attack.checked:
            retracement = retracement_evidence(attack.origin.value, attack.extreme.value, point.value,
                                               direction=Direction.UP if up else Direction.DOWN)
            attack.checked = True
            if retracement.partial and retracement.below_67_percent:
                impulse = {'flip_high' if up else 'flip_low': attack.extreme.reference()}
                observe('空多交替' if up else '多空交替', ratio=retracement.ratio,
                    weak_countermove=retracement.below_33_percent, origin=attack.origin.reference(),
                    broken_key=attack.key.reference(), abc_confirmed=False, **impulse)
                self._bull = up
                self._anchor, self._key = attack.extreme, attack.origin
                self._attack, self._suspicion = None, False
        elif point.kind == attack_kind and sign * (point.value - attack.extreme.value) > 0:
            assert previous is not None
            attack.origin, attack.extreme, attack.checked = previous, point, False
        attack = self._attack
        if (attack is not None and point.kind == attack.anchor.kind
                and sign * (point.value - attack.anchor.value) < 0):
            self._anchor, self._key = point, previous
            self._attack, self._suspicion = None, False


class PolylineLifecycleSource:
    """保存折线六态适配状态，支持绘图回调直接交付当天帧。"""

    def __init__(self, dates: Mapping[str, int]) -> None:
        self.dates = dates
        self.gate = PolylineSourceGate()
        self.epoch: int | None = None
        self.structure = _PolylineStructure()
        self.known_points: dict[_Point, _Point] = {}
        self.row: dict[str, object] = dict(trend_level=0, strokes=[])
        self.previous_index: int | None = None

    def advance(self, frame: _DrawingFrame) -> dict[str, object]:
        dates = self.dates
        if type(frame.index) is not int or frame.index < 0 or (
                self.previous_index is not None and frame.index != self.previous_index + 1):
            raise ValueError('polyline lifecycle requires consecutive causal frames')
        if type(frame.epoch) is not int or not 0 <= frame.epoch <= frame.index:
            raise ValueError('polyline lifecycle requires a causal path origin')
        self.previous_index = frame.index
        if frame.epoch != self.epoch:
            self.epoch, self.structure, self.known_points = frame.epoch, _PolylineStructure(), {}
        if self.gate.changed(frame.epoch, frame.raw):
            _validate_raw(frame.raw, dates, frame.index)
            path = f'lecture-{frame.epoch}'
            projected = polyline_turns(frame.raw, source_path=path)
            current: dict[_Point, _Point] = {}
            points: list[_Point] = []
            for raw in projected:
                native = _Point(cast(int, raw['index']), cast(int, raw.get('ordinal', 0)),
                    cast(str, raw['kind']), cast(float, raw['value']), _clock(raw['available_at'], dates))
                point = self.known_points.get(native, _Point(native.index, native.ordinal, native.kind,
                                                       native.value, max(native.known, frame.index)))
                if points and point.known < points[-1].known:
                    point = _Point(point.index, point.ordinal, point.kind, point.value, points[-1].known)
                current[native] = point
                points.append(point)
            self.known_points = current
            if points[:len(self.structure.source)] != self.structure.source:
                self.structure = _PolylineStructure()
            for point in points[len(self.structure.source):]:
                self.structure.append(point)
            self.row = dict(trend_level=0, strokes=[dict(id=path, source_path=path, points=list(self.structure.points))]
                if self.structure.points else [], source_kind='technical_polyline')
        return self.row


def polyline_lifecycle_rows(
    frames: Iterable[_DrawingFrame], dates: Mapping[str, int],
) -> Iterator[tuple[int, dict[str, object]]]:
    """按首次可知日交付折线结构；实时推进和历史读取共用一个适配器。"""
    source = PolylineLifecycleSource(dates)
    for frame in frames:
        yield frame.index, source.advance(frame)
