"""Observe window-scoped swings and frozen-key trend-transition evidence."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Sequence

from ..models.model import Bar
from .polyline import PointKind, ReversalPoint
from .price_action import (AttackBasis, AttackEvidence, Direction, KeyLevel, LevelKind,
                           _index, _ordered_pair, _positive, _validate_bar, observe_attack)


class StructuralTrend(str, Enum):
    BULL = '多头趋势'
    BEAR = '空头趋势'
    MIXED = '高低点不同向或相等'
    UNKNOWN = '已确认拐点不足'


def _trend_level(trend_level: int, source_level: int | None = None) -> int:
    """Validate one recursive definition and return its immediate source level."""
    # 紧邻下级是定义的来源约束：2级不能绕过1级直接读取折线，3级不能直接读取1级。
    if type(trend_level) is not int or trend_level not in (1, 2, 3):
        raise ValueError('trend_level must be 1, 2 or 3')
    expected_source = trend_level - 1
    if source_level is not None and (type(source_level) is not int or source_level != expected_source):
        raise ValueError('source_level must equal trend_level - 1')
    return expected_source


def _classify_trend_comparisons(
    high_count: int, low_count: int, *, all_up: bool, all_down: bool,
) -> StructuralTrend:
    if high_count < 2 or low_count < 2:
        return StructuralTrend.UNKNOWN
    if all_up:
        return StructuralTrend.BULL
    if all_down:
        return StructuralTrend.BEAR
    return StructuralTrend.MIXED


@dataclass
class _TrendPriceSequence:
    """一类点的固定大小摘要；全窗升降一旦被反例破坏，就不能被后续局部恢复覆盖。"""

    count: int = 0
    previous: float | None = None
    all_up: bool = True
    all_down: bool = True
    latest_up: bool = False
    latest_down: bool = False

    def append(self, price: float) -> None:
        if self.previous is not None:
            self.latest_up = price > self.previous
            self.latest_down = price < self.previous
            self.all_up = self.all_up and self.latest_up
            self.all_down = self.all_down and self.latest_down
        self.previous = price
        self.count += 1


class TrendPriceState:
    """Incrementally classify confirmed prices from one immediate lower level.

    Create one state for one source path and observation window, append each
    known price in that kind's chronological order, and replace the state when
    the source or window changes. Confirmation dates, source membership and
    causal ordering remain the caller's responsibility. The state retains no
    price history: append and both direction properties take O(1) time/space.
    Invalid levels, PointKind or prices raise ValueError without changing state.

    一级状态消费折线高低点，二级消费一级趋势高低点，三级消费二级趋势高低点。
    两类点分别保存数量、上一价格、最近一对升降及全窗是否一直升降，因而无需保留
    或重复扫描整段历史，追加 n 个点总耗时 O(n)，辅助状态始终 O(1)。严格 >/< 的
    比较同时服务批量分类与增量分类；相等让该对的上升、下降都不成立。

    一个实例只属于一个来源路径及一个累积观察窗口。切换标的、来源路径、级别或
    窗口起点时必须新建实例；追加式摘要不能删除被滑动窗口淘汰的早期反例。调用方
    只在确认时点已到达后追加点，并保证各同类点的来源顺序，本类不猜测确认日期。
    """

    def __init__(self, *, trend_level: int) -> None:
        self._source_level = _trend_level(trend_level)
        self._trend_level = trend_level
        self._highs = _TrendPriceSequence()
        self._lows = _TrendPriceSequence()

    @property
    def trend_level(self) -> int:
        return self._trend_level

    @property
    def source_level(self) -> int:
        return self._source_level

    def append(self, kind: PointKind, price: float) -> None:
        """追加一个已确认下级高/低价；非法种类或价格不会改变已有窗口状态。

        确认资格由调用方决定，发生在历史较早位置的点不能仅按绘图日期提前加入。
        允许高、低两类证据数量不同，各自只与其上一同类点比较。
        """
        if not isinstance(kind, PointKind):
            raise ValueError('PointKind required')
        _positive(price, 'trend price')
        (self._highs if kind == PointKind.HIGH else self._lows).append(price)

    @property
    def trend(self) -> StructuralTrend:
        """最近两高两低的局部方向，用于追踪当前结构。

        最近高点和低点均严格抬高为 BULL，均严格降低为 BEAR；相等或方向不同为
        MIXED。任一类不足两个点为 UNKNOWN。此属性不会替代全窗“每一个”的判定。
        """
        return _classify_trend_comparisons(
            self._highs.count, self._lows.count,
            all_up=self._highs.latest_up and self._lows.latest_up,
            all_down=self._highs.latest_down and self._lows.latest_down,
        )

    @property
    def window_trend(self) -> StructuralTrend:
        """整个累积窗口每一对同类点的严格方向。

        所有相邻高点及所有相邻低点都严格上升才为 BULL，都严格下降才为 BEAR；
        任一相等或混合方向为 MIXED。缺少两高或两低为 UNKNOWN，避免空序列的
        all=True 被误当成有足够证据的多头或空头趋势。
        """
        return _classify_trend_comparisons(
            self._highs.count, self._lows.count,
            all_up=self._highs.all_up and self._lows.all_up,
            all_down=self._highs.all_down and self._lows.all_down,
        )


def classify_trend_prices(
    highs: Sequence[float], lows: Sequence[float], *, trend_level: int,
) -> StructuralTrend:
    """Classify every successive high/low using the same rule at levels 1–3.

    Each input is chronological evidence from the immediate lower level. Both
    kinds need at least two prices. Every pair must strictly rise for BULL or
    strictly fall for BEAR; equality and mixed directions produce MIXED.
    Invalid levels, sequences or nonpositive/nonfinite prices raise ValueError.
    The observer makes one linear pass without copying price sequences.

    highs 与 lows 分别按来源顺序提供所有已确认高价和低价：一级来自折线，二级
    来自一级趋势，三级来自二级趋势。多头需所有 H[i] > H[i-1] 且所有 L[j] >
    L[j-1]；空头把两种比较均替换为 <。相等或任何不同向比较返回 MIXED，每种
    少于两个价格返回 UNKNOWN。level 不引入额外阈值，三个级别仅改变证据来源。
    批量入口复用 TrendPriceState 的逐对比较及最终分类，避免与实时状态出现分歧。
    """
    state = TrendPriceState(trend_level=trend_level)
    for kind, prices in ((PointKind.HIGH, highs), (PointKind.LOW, lows)):
        if not isinstance(prices, Sequence) or isinstance(prices, (str, bytes)):
            raise ValueError('chronological high and low price sequences required')
        for price in prices:
            state.append(kind, price)
    return state.window_trend


@dataclass(frozen=True)
class StructureContext:
    """冻结在 asof_index 的窗口事实，供六项趋势定义复用。

    points 仅包含窗口中已经确认的来源拐点，point.index 表示价格发生位置，
    confirmed_index 表示首次可知位置。trend 比较最近两高两低，window_trend
    比较全窗所有同类相邻点；两者有意分开，近期恢复不能掩盖早期相等或逆向反例。
    window_low/high 保留最早出现的同价极值，关键位取该极值左侧最近相反拐点。

    trend_level=1/2/3 分别对应折线/一级趋势/二级趋势来源，source_level 必须为
    trend_level-1。Context 不可重新标记为另一个级别，也不能混用不同观察窗口。
    不足两高或两低时方向为 UNKNOWN，各单项同类比较不足时为 None；缺少可用
    极值或左侧反转时对应对象为 None，调用方须保留这种证据缺失状态。
    """

    symbol: str
    timeframe: str
    window_start: int
    asof_index: int
    points: tuple[ReversalPoint, ...]
    trend: StructuralTrend
    higher_high: bool | None
    higher_low: bool | None
    lower_high: bool | None
    lower_low: bool | None
    window_low: ReversalPoint | None
    window_high: ReversalPoint | None
    last_fall_high: KeyLevel | None
    last_rise_low: KeyLevel | None
    window_trend: StructuralTrend
    trend_level: int = 1
    source_level: int = 0

    def __post_init__(self) -> None:
        if type(self.source_level) is not int:
            raise ValueError('source_level must equal trend_level - 1')
        _trend_level(self.trend_level, self.source_level)


def _known_points(
    points: Sequence[ReversalPoint], start: int, end: int
) -> tuple[ReversalPoint, ...]:
    """Return pivots that were observable inside the requested causal window.

    ``point.index`` is the date drawn on the chart while ``confirmed_index`` is
    the date on which the reversal became knowable. Both constraints matter:
    accepting a visually earlier pivot before its confirmation date would leak
    future information into a backtest.
    """

    known: list[ReversalPoint] = []
    for point in points:
        if not isinstance(point, ReversalPoint):
            raise ValueError('ReversalPoint sequence required')
        if point.confirmed_index > end:
            continue
        if point.point.index < start:
            continue
        if known:
            previous = known[-1]
            if (
                (point.point.index, point.point.ordinal)
                <= (previous.point.index, previous.point.ordinal)
                or point.point.kind == previous.point.kind
                or point.confirmed_index < previous.confirmed_index
            ):
                raise ValueError('ordered alternating causally confirmed points required')
            if (
                point.point.kind == PointKind.HIGH
                and point.point.price <= previous.point.price
                or point.point.kind == PointKind.LOW
                and point.point.price >= previous.point.price
            ):
                raise ValueError('nonzero alternating high/low legs required')
        known.append(point)
    return tuple(known)


def preceding_turn(points: Sequence[ReversalPoint], target: ReversalPoint) -> ReversalPoint | None:
    """Find the nearest opposite reversal strictly to the target's left.

    This is the shared definition behind ``末跌高`` and ``末升低``. It is
    intentionally relative to one selected extreme; it is neither the latest
    pivot on the chart nor the highest/lowest pivot in the whole window.

    在有序来源序列中，只取目标之前最近的异类点：低点取左侧最近高点，高点取
    左侧最近低点。选点依据是相对位置而非价格大小，右侧拐点永远不能充当关键位。

    Raises:
        ValueError: If ``target`` is not part of the confirmed sequence.
    """

    try:
        index = points.index(target)
    except ValueError as exc:
        raise ValueError('target must belong to the confirmed sequence') from exc
    return next((p for p in reversed(points[:index]) if p.point.kind != target.point.kind), None)


def _key_from_preceding_turn(
    points: Sequence[ReversalPoint],
    target: ReversalPoint | None,
    *,
    symbol: str,
    timeframe: str,
    kind: LevelKind,
    window_start: int,
    asof_index: int,
) -> KeyLevel | None:
    """Materialize a frozen key while preserving source and knowledge dates."""

    if target is None:
        return None
    preceding = preceding_turn(points, target)
    if preceding is None:
        # Missing left context is an unknown result, not permission to invent a
        # key from an unrelated candle or from a later pivot.
        return None
    return KeyLevel(
        symbol,
        timeframe,
        kind,
        preceding.point.price,
        preceding.point.index,
        asof_index,
        f'window_{window_start}_through_{asof_index}_preceding_extreme',
    )


def observe_structure(
    points: Sequence[ReversalPoint],
    *,
    symbol: str,
    timeframe: str,
    window_start: int,
    asof_index: int,
    trend_level: int = 1,
    source_level: int | None = None,
) -> StructureContext:
    """Observe trend direction and frozen reversal keys as of one bar.

    The function compares the two latest confirmed highs and lows for the local
    trend, and all same-kind pivots for the full-window trend. The window's
    earliest equal low/high remains the anchor so a later touch cannot silently
    rewrite historical context. Level 1 consumes the polyline (source 0), level 2
    consumes level 1 and level 3 consumes level 2. A mismatched source is rejected.

    points 必须提供紧邻下级的已确认反转：trend_level=1 用折线，2 用一级趋势，
    3 用二级趋势。source_level 省略时推导为 trend_level-1，显式错误来源抛
    ValueError。窗口只纳入发生位置不早于 window_start、确认日不晚于 asof_index
    的点，因此完整历史和截至观察日的前缀应产生相同结果。

    局部 trend 只比较最近两高两低；全窗 window_trend 要求每一相邻同类高/低点
    严格同向，任一相等或混合即 MIXED，任一类不足两个点即 UNKNOWN。默认末跌高
    从窗口最低点向左取最近高点，末升低从窗口最高点向左取最近低点；极值同价时
    保留最早锚点，不让后来的触碰改写历史关键位。仅执行一次窗口观察，不推断新
    拐点、不补造左侧上下文，也不把结构观察自动转换为交易指令。
    """

    source_level = _trend_level(trend_level, source_level)
    _index(window_start, 'window_start')
    _index(asof_index, 'asof_index')
    if (window_start > asof_index or not isinstance(symbol, str) or not symbol.strip()
            or not isinstance(timeframe, str) or not timeframe.strip()):
        raise ValueError('explicit symbol, timeframe and ordered window required')
    known_points = _known_points(points, window_start, asof_index)
    highs = [point for point in known_points if point.point.kind == PointKind.HIGH]
    lows = [point for point in known_points if point.point.kind == PointKind.LOW]

    higher_high = highs[-1].point.price > highs[-2].point.price if len(highs) >= 2 else None
    lower_high = highs[-1].point.price < highs[-2].point.price if len(highs) >= 2 else None
    higher_low = lows[-1].point.price > lows[-2].point.price if len(lows) >= 2 else None
    lower_low = lows[-1].point.price < lows[-2].point.price if len(lows) >= 2 else None

    high_prices = [point.point.price for point in highs]
    low_prices = [point.point.price for point in lows]
    trend = classify_trend_prices(
        high_prices[-2:], low_prices[-2:], trend_level=trend_level,
    )
    window_trend = classify_trend_prices(
        high_prices, low_prices, trend_level=trend_level,
    )

    # Earliest equal extreme wins; a touch does not silently replace the anchor.
    window_low = min(lows, key=lambda point: point.point.price) if lows else None
    window_high = max(highs, key=lambda point: point.point.price) if highs else None
    last_fall_high = _key_from_preceding_turn(
        known_points,
        window_low,
        symbol=symbol,
        timeframe=timeframe,
        kind=LevelKind.RESISTANCE,
        window_start=window_start,
        asof_index=asof_index,
    )
    last_rise_low = _key_from_preceding_turn(
        known_points,
        window_high,
        symbol=symbol,
        timeframe=timeframe,
        kind=LevelKind.SUPPORT,
        window_start=window_start,
        asof_index=asof_index,
    )
    return StructureContext(
        symbol,
        timeframe,
        window_start,
        asof_index,
        known_points,
        trend,
        higher_high,
        higher_low,
        lower_high,
        lower_low,
        window_low,
        window_high,
        last_fall_high,
        last_rise_low,
        window_trend,
        trend_level,
        source_level,
    )


@dataclass(frozen=True)
class RetracementEvidence:
    impulse: float
    countermove: float
    ratio: float
    below_67_percent: bool
    below_33_percent: bool
    partial: bool


def retracement_evidence(origin: float, extreme: float, counter: float, *,
                         direction: Direction) -> RetracementEvidence:
    from .price_action import _positive
    if not isinstance(direction, Direction):
        raise ValueError('Direction required')
    for value in (origin, extreme, counter):
        _positive(value, 'price')
    a, b, c = (Decimal(str(v)) for v in (origin, extreme, counter))
    sign = 1 if direction == Direction.UP else -1
    impulse, move = sign*(b-a), sign*(b-c)
    if impulse <= 0 or move < 0:
        raise ValueError('positive impulse and nonnegative counter move required')
    return RetracementEvidence(float(impulse), float(move), float(move/impulse),
        move < impulse*Decimal('.67'), move < impulse*Decimal('.33'), 0 < move < impulse)


@dataclass(frozen=True)
class ABCEvidence:
    direction: Direction
    first_leg: float
    third_leg: float
    equal_wave_or_more: bool
    observed_at_index: int


def observe_abc(points: Sequence[ReversalPoint], *, direction: Direction,
                asof_index: int) -> ABCEvidence | None:
    """Exactly four confirmed vertices = three countertrend legs, no inferred abc."""
    _index(asof_index, 'asof_index')
    if not isinstance(direction, Direction):
        raise ValueError('Direction required')
    known = _known_points(points, 0, asof_index)
    if len(known) != 4:
        return None
    expected = PointKind.LOW if direction == Direction.UP else PointKind.HIGH
    if known[0].point.kind != expected:
        raise ValueError('ABC starting kind differs from direction')
    a, b, c, d = (Decimal(str(p.point.price)) for p in known)
    sign = 1 if direction == Direction.UP else -1
    first, retrace, third = sign*(b-a), sign*(b-c), sign*(d-c)
    if not 0 < retrace < first or third <= 0:
        return None
    return ABCEvidence(direction, float(first), float(third), third >= first,
                       known[-1].confirmed_index)


class TransitionStage(str, Enum):
    WATCHING = 'watching_frozen_key'
    SUSPICION = 'head_or_bottom_suspicion'
    KEY_BROKEN = 'key_broken_await_confirmed_countermove'
    ALTERNATION = 'countermove_below_67_percent'
    DEEP_COUNTERMOVE = 'countermove_not_below_67_percent'
    INVALIDATED = 'original_extreme_breached'


@dataclass(frozen=True)
class TrendTransition:
    asof_index: int
    direction: Direction
    stage: TransitionStage
    key: KeyLevel
    suspicion_index: int | None = None
    attack: AttackEvidence | None = None
    impulse_origin: ReversalPoint | None = None
    impulse_extreme: ReversalPoint | None = None
    counterturn: ReversalPoint | None = None
    retracement: RetracementEvidence | None = None
    invalidated_index: int | None = None
    alternation_confirmed_index: int | None = None
    rule_version: str = 'frozen_extreme_key_decimal_67_v1'

    @property
    def suspicion_name(self) -> str:
        return '底部疑虑' if self.direction == Direction.UP else '头部疑虑'

    @property
    def break_name(self) -> str:
        return '翻空为多' if self.direction == Direction.UP else '翻多为空'

    @property
    def formation_name(self) -> str | None:
        """A prior observable suspicion and its later key attack form the pattern.

        This is historical evidence, so a later invalidation does not erase a
        formation already observed. Direct attacks without suspicion are flips.
        """
        if (
            self.attack is None
            or self.suspicion_index is None
            or not 0 <= self.suspicion_index < self.attack.bar_index <= self.asof_index
        ):
            return None
        return '底部成形' if self.direction == Direction.UP else '头部成形'

    @property
    def alternation_name(self) -> str:
        return '空多交替' if self.direction == Direction.UP else '多空交替'


def observe_trend_transition(bars: Sequence[Bar], points: Sequence[ReversalPoint], *,
                             context: StructureContext, attack_basis: AttackBasis,
                             asof_index: int | None = None) -> TrendTransition:
    """One frozen bull/bear context -> break -> first confirmed retracement.

    Separate from local HH/HL comparisons. No automatic buy/sell, abc or N.
    Confirmed pivots need their real availability indices, not drawing dates.
    """
    if not isinstance(context, StructureContext) or context.trend not in (StructuralTrend.BULL, StructuralTrend.BEAR):
        raise ValueError('explicit previously bullish or bearish context required')
    if not isinstance(attack_basis, AttackBasis):
        raise ValueError('explicit AttackBasis required')
    end = len(bars)-1 if asof_index is None else asof_index
    _index(end, 'asof_index')
    if end >= len(bars) or end < context.asof_index:
        raise ValueError('available asof at or after frozen context required')
    up = context.trend == StructuralTrend.BEAR
    direction = Direction.UP if up else Direction.DOWN
    sign = 1 if up else -1
    key = context.last_fall_high if up else context.last_rise_low
    anchor = context.window_low if up else context.window_high
    if key is None or anchor is None:
        raise ValueError('known frozen extreme and preceding key required')
    known = _known_points(points, context.window_start, end)
    original = observe_structure(points, symbol=context.symbol, timeframe=context.timeframe,
                                 window_start=context.window_start, asof_index=context.asof_index,
                                 trend_level=context.trend_level, source_level=context.source_level)
    if original != context:
        raise ValueError('supplied points changed the frozen context')
    for i in range(end+1):
        _validate_bar(bars[i])
        if bars[i].symbol != context.symbol:
            raise ValueError('symbol mismatch')
        if i:
            _ordered_pair(bars[i-1], bars[i])
    for p in known:
        bar = bars[p.point.index]
        price = bar.high if p.point.kind == PointKind.HIGH else bar.low
        if p.point.price != price:
            raise ValueError('pivot price differs from source bar and adjustment basis')
    attack = None
    invalidated = None
    for i in range(context.asof_index+1, end+1):
        bar = bars[i]
        adverse = bar.low if up else bar.high
        if sign*(adverse-anchor.point.price) < 0:
            invalidated = i
            break
        if attack is None:
            # Cheap candidate screen; canonical primitive verifies the crossing
            # once. Avoid repeatedly revalidating every historical bar (O(n^2)).
            value = bar.close if attack_basis == AttackBasis.CLOSE else bar.high if up else bar.low
            if sign*(bars[i-1].close-key.price) <= 0 and sign*(value-key.price) > 0:
                evidence = observe_attack(bars, i, key, timeframe=context.timeframe)
                if evidence.qualifies(attack_basis):
                    attack = evidence
    # Suspicion requires the weak rebound/retest to have itself reversed, i.e.
    # the third pivot is CONFIRMED. Equality at the old extreme is a retest.
    suspicion = None
    start = known.index(anchor)
    for j in range(start+2, len(known)):
        a, b, c = known[j-2:j+1]
        if (a.point.kind == anchor.point.kind and c.point.kind == anchor.point.kind
                and sign*(c.point.price-a.point.price) >= 0
                and sign*(b.point.price-key.price) <= 0
                and sign*(c.point.price-anchor.point.price) >= 0
                and c.confirmed_index > context.asof_index
                and (attack is None or c.confirmed_index < attack.bar_index)
                and (invalidated is None or c.confirmed_index < invalidated)):
            suspicion = c.confirmed_index
            break
    stage = TransitionStage.SUSPICION if suspicion is not None else TransitionStage.WATCHING
    origin = extreme = counter = ratio = None
    alternation_index = None
    if attack is not None:
        stage = TransitionStage.KEY_BROKEN
        extreme_kind = PointKind.HIGH if up else PointKind.LOW
        for j, p in enumerate(known):
            if (p.point.kind == extreme_kind and p.point.index >= attack.bar_index
                    and p.confirmed_index >= attack.bar_index and j > 0):
                origin, extreme = known[j-1], p
                # Do not merge sub-bar legs into a fictitious daily impulse.
                if not origin.point.index < attack.bar_index <= extreme.point.index:
                    origin = extreme = None
                    break
                if j+1 < len(known):
                    counter = known[j+1]
                    if counter.point.index <= extreme.point.index:
                        counter = None
                        break
                    ratio = retracement_evidence(origin.point.price, extreme.point.price,
                        counter.point.price, direction=direction)
                    if ratio.partial and ratio.below_67_percent:
                        stage = TransitionStage.ALTERNATION
                        alternation_index = counter.confirmed_index
                    else:
                        stage = TransitionStage.DEEP_COUNTERMOVE
                break
    if invalidated is not None:
        stage = TransitionStage.INVALIDATED
        # Preserve earlier confirmed facts, never manufacture one after failure.
        if alternation_index is not None and alternation_index >= invalidated:
            alternation_index = None
        if extreme is not None and extreme.confirmed_index >= invalidated:
            origin = extreme = counter = ratio = None
        elif counter is not None and counter.confirmed_index >= invalidated:
            counter = ratio = None
    return TrendTransition(end, direction, stage, key, suspicion, attack, origin,
        extreme, counter, ratio, invalidated, alternation_index)
