"""Causal entry after a confirmed chart ABC has become a combined A.

The ABC endpoints use the chart's strict structural turns and original N.
An arbitrary four-pivot sequence is never promoted into a combined wave.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from typing import NotRequired, TypedDict, cast
from zoneinfo import ZoneInfo

from ..market_structure.trend_publication import publish_uptrends
from ..market_structure.trend_market_index import _MarketRangeIndex
from ..models.model import Bar
from .buy_intervals import require_entry_days


class GeometryPoint(TypedDict):
    """Confirmed geometry fields consumed at this strategy's internal boundary."""

    index: int
    kind: str
    value: float
    available_at: str
    state: NotRequired[str]
    label: NotRequired[str]
    flip: NotRequired[str]


class GeometryStroke(TypedDict):
    """A geometry path with its optional ancestry link."""

    id: str
    points: list[GeometryPoint]
    source_path: NotRequired[str]


class GeometryLevel(TypedDict):
    """Only confirmed strokes are consumed from a drawing or trend layer."""

    strokes: list[GeometryStroke]


class _PositiveN(TypedDict):
    origin: int
    high: int
    low: int
    attack: int
    known: int
    one_p: float
    two_t: float
    formal: bool


def _local_reference(point: GeometryPoint) -> dict[str, object]:
    """局部证据只引用来源坐标、价格及其首次可知日，不传递正式级许可证。"""
    fields = ('index', 'ordinal', 'time', 'kind', 'value', 'available_at', 'label')
    source = cast(Mapping[str, object], point)
    return {field: source[field] for field in fields if field in source}


def _reduce_local_points(points: Sequence[GeometryPoint], *, source_level: int = 2) -> list[GeometryPoint]:
    """保留原 N 起点内局部 ABC 的严格关键位降采样，隔离正式趋势递归入口。

    这里的输入仍是一级已确认点，输出仅进入 ordinary-local:* 私有测幅路径。
    source_level 沿用旧局部适配器的位置字段命名，不能据此宣称一级点成为
    正式三级来源；正式一级→二级→三级仍须走公共趋势入口并校验真实级别。

    局部 ABC 继续由原严格末跌高／末升低突破确认，保留其 flip 和知识日期，
    与 Web ordinaryLocalWavePoints 的局部几何语义一致。它等待当前整波
    极值左侧关键位被严格反向突破，不执行全局高点／低点直接升级；等值
    不构成突破。既有无行情发布门禁仍检查已确认来源的真实关键位突破，
    不把任意四拐点当成组合 A。

    正式来源点携带的是其自身等级的授权，不能变成局部路径的许可。
    尤其来源下降升级的 endpoint 角色不能让本地 ABC 的 L 绕过原上涨
    资格门禁；只转入几何字段，本地反转及发布证据在局部边界重新判断。
    """
    geometry_fields = ('index', 'ordinal', 'time', 'kind', 'value', 'available_at', 'state', 'label')
    geometry = cast(list[GeometryPoint], [
        {field: cast(Mapping[str, object], point)[field] for field in geometry_fields if field in point}
        for point in points
    ])
    candidates: list[GeometryPoint] = []
    highs: list[int] = []
    lows: list[int] = []
    direction: str | None = None
    anchor: int | None = None
    key: int | None = None
    for position, point in enumerate(geometry):
        (highs if point['kind'] == 'H' else lows).append(position)
        if direction is None:
            if len(highs) < 2 or len(lows) < 2:
                continue
            high_change = geometry[highs[-1]]['value'] - geometry[highs[-2]]['value']
            low_change = geometry[lows[-1]]['value'] - geometry[lows[-2]]['value']
            direction = 'up' if high_change > 0 and low_change > 0 else (
                'down' if high_change < 0 and low_change < 0 else None)
            if direction is None:
                continue
            pool = highs if direction == 'up' else lows
            anchor = (max if direction == 'up' else min)(pool, key=lambda index: geometry[index]['value'])
            # 窗口首点没有左侧关键位时，只以最近已确认同类点开启局部观察。
            if anchor == 0:
                anchor = pool[-1]
            key = anchor - 1 if anchor > 0 else None
            continue
        assert anchor is not None
        up = direction == 'up'
        target = 'H' if up else 'L'
        sign = 1 if up else -1
        if point['kind'] == target and sign * (point['value'] - geometry[anchor]['value']) > 0:
            anchor, key = position, position - 1
        if key is None or point['kind'] == target or sign * (point['value'] - geometry[key]['value']) >= 0:
            continue
        extreme = geometry[anchor]
        candidate = dict(cast(Mapping[str, object], extreme), available_at=point['available_at'],
                         confirmation_rule='n_origin_local_structural_key_break',
                         local_wave_turn='up_to_down' if up else 'down_to_up',
                         flip='翻多为空' if up else '翻空为多', broken_key=_local_reference(geometry[key]),
                         confirmed_by=_local_reference(point), wave_direction_before=direction,
                         wave_direction_after='down' if up else 'up',
                         **{f'source_level{source_level}_position': anchor,
                            f'source_level{source_level}_available_at': extreme['available_at'],
                            f'confirmed_on_level{source_level}': position})
        candidates.append(cast(GeometryPoint, candidate))
        # 新方向的起点取整段反向区间极值；相同极值保留最早来源，不能
        # 缩到突破 K 本身，也不能读取尚未确认的后续 holding point。
        direction = 'down' if up else 'up'
        pool = [index for index in range(anchor + 1, position + 1)
                if geometry[index]['kind'] == ('L' if up else 'H')]
        anchor = (min if up else max)(pool, key=lambda index: geometry[index]['value'])
        key = anchor - 1
    published = publish_uptrends(candidates, geometry, (), source_level=source_level)
    return cast(list[GeometryPoint], published)


def _bar_date(bar: Bar) -> str:
    timestamp = bar.timestamp
    if timestamp.tzinfo:
        timestamp = timestamp.astimezone(ZoneInfo("Asia/Shanghai"))
    return timestamp.date().isoformat()


@dataclass(frozen=True)
class CombinedAHigh:
    """A source-level rebound high and the session when it became known."""

    index: int
    price: float
    known_index: int


@dataclass(frozen=True)
class CombinedAContext:
    """Frozen ABC anchors plus prefix-confirmed rebound evidence."""

    source_path: str
    origin_index: int
    origin_price: float
    a_index: int
    a_price: float
    b_index: int
    b_price: float
    c_index: int
    c_price: float
    known_index: int
    n_index: int
    child_pullback_durations: tuple[int, ...]
    rebound_highs: tuple[CombinedAHigh, ...] = ()
    trend_level: int = 2

    @property
    def identity(self) -> tuple[int, int]:
        """Path concatenation must not recreate the same historical ABC."""
        return self.origin_index, self.c_index


def _known_point(point: GeometryPoint, kind: str, bars: Sequence[Bar], dates: Mapping[str, int], now: int) -> bool:
    index = point.get("index")
    known = dates.get(point.get("available_at", ""))
    return (
        point.get("kind") == kind
        and type(index) is int
        and known is not None
        and 0 <= index <= known <= now
        and index < len(bars)
        and point.get("state") != "developing"
        and point.get("value") == (bars[index].high if kind == "H" else bars[index].low)
    )


def _shares_source(stroke: GeometryStroke, source_path: str, paths: Mapping[str, GeometryStroke]) -> bool:
    visited: set[str] = set()
    current: GeometryStroke | None = stroke
    while current is not None and current.get("id") not in visited:
        if current.get("id") == source_path or current.get("source_path") == source_path:
            return True
        visited.add(current.get("id", ""))
        current = paths.get(current.get("source_path", ""))
    return False


def _positive_ns(
    bars: Sequence[Bar], drawing: GeometryLevel, dates: Mapping[str, int], now: int,
    audit: Sequence[Mapping[str, object]],
) -> list[_PositiveN]:
    """Retain formal Ns and the chart projection's valid lecture seed Ns."""
    candidates: dict[tuple[int, int], _PositiveN] = {}
    for event in audit:
        if event.get("event") != "n_completed" or event.get("direction") != "up":
            continue
        origin, attack = event.get("origin"), event.get("bar_index")
        high, low = event.get("neckline"), event.get("pullback")
        if not (type(origin) is int and type(high) is int and type(low) is int and type(attack) is int):
            continue
        known_at = event.get("known_at", attack)
        if type(known_at) is not int:
            continue
        known = max(known_at, attack)
        one_p, two_t = event.get("one_p"), event.get("two_t")
        if (
            0 <= origin < high < low <= attack <= known <= now
            and isinstance(one_p, (int, float))
            and isinstance(two_t, (int, float))
        ):
            candidates[(origin, attack)] = dict(
                origin=origin, high=high, low=low, attack=attack, known=known,
                one_p=float(one_p), two_t=float(two_t), formal=True,
            )
    for stroke in drawing.get("strokes", []):
        points = stroke.get("points", [])
        for position in range(len(points) - 3):
            origin, high, low, attack = points[position : position + 4]
            if origin.get("state") != "seed":
                continue
            if not all(_known_point(point, kind, bars, dates, now)
                       for point, kind in zip((origin, high, low, attack), ("L", "H", "L", "H"))):
                continue
            indices = [point["index"] for point in (origin, high, low, attack)]
            if not indices[0] < indices[1] < indices[2] < indices[3]:
                continue
            if not origin["value"] < low["value"] < high["value"] < attack["value"]:
                continue
            if bars[indices[3]].close <= high["value"]:
                continue
            one_p = 2 * attack["value"] - origin["value"]
            candidates.setdefault((indices[0], indices[3]), dict(
                origin=indices[0], high=indices[1], low=indices[2], attack=indices[3],
                known=indices[3], one_p=one_p, two_t=3 * attack["value"] - 2 * origin["value"], formal=False,
            ))
    return list(candidates.values())


def _child_durations(
    bars: Sequence[Bar], points: Sequence[GeometryPoint], dates: Mapping[str, int], origin: int, c: int, now: int,
    positive: Sequence[_PositiveN], *, _market_index: _MarketRangeIndex | None = None,
) -> tuple[int, ...]:
    """Keep the latest completed child ABC's B, not a microscopic N counterleg."""
    children: list[tuple[int, int, int]] = []
    for n in positive:
        if not origin < n["origin"] < n["attack"] < c or n["known"] > now:
            continue
        for a, b, end in zip(points, points[1:], points[2:]):
            if not all(_known_point(point, kind, bars, dates, now)
                       for point, kind in zip((a, b, end), ("H", "L", "H"))):
                continue
            if not n["attack"] < a["index"] < b["index"] < end["index"] <= c:
                continue
            if a.get("flip") != "翻多为空" or end.get("flip") != "翻多为空":
                continue
            if not bars[n["origin"]].low < b["value"] < a["value"] < end["value"]:
                continue
            if a["value"] <= n["one_p"]:
                continue
            maximum = (bars[_market_index.extreme_position(n["origin"], a["index"] + 1, high=True)].high
                       if _market_index is not None else
                       max(bar.high for bar in bars[n["origin"] : a["index"] + 1]))
            if maximum != a["value"]:
                continue
            children.append((a["index"], b["index"], b["index"] - a["index"]))
    latest = max(children, default=None)
    return (latest[2],) if latest is not None else ()


def combined_a_contexts_from_geometry(
    bars: Sequence[Bar], drawing: GeometryLevel, first: GeometryLevel, second: GeometryLevel, third: GeometryLevel,
    dates: Mapping[str, int], now: int, audit: Sequence[Mapping[str, object]] = (),
    *, _market_index: _MarketRangeIndex | None = None,
) -> tuple[CombinedAContext, ...]:
    """Use the same formal-N and local structural ABC route as chart projections.

    All extrema and availability dates are checked against the current prefix.
    The local route runs the existing strict-key reducer without the global
    level-two promotion rule, matching ``ordinaryLocalWavePoints``.
    """
    if now < 0 or now >= len(bars):
        return ()
    if _market_index is not None and (_market_index.bars is not bars or not _market_index.exact_prices):
        _market_index = None
    positive = _positive_ns(bars, drawing, dates, now, audit)
    if not positive:
        return ()
    paths = {stroke["id"]: stroke for level in (first, second, third)
             for stroke in level.get("strokes", [])}
    groups: dict[tuple[int, int], tuple[CombinedAContext, bool]] = {}
    for n in positive:
        origin, attack = n["origin"], n["attack"]
        origin_price = bars[origin].low
        if n["known"] > now or not origin_price < n["one_p"] < n["two_t"]:
            continue
        # A 必须在 N 之后严格超过一饱；尚未进入该测幅区间时无需做局部 ABC 降采样。
        if (_market_index is not None and _market_index.supports_price(n["one_p"])
                and _market_index.first_extreme(attack + 1, now + 1, n["one_p"], high=True) is None):
            continue
        wave_paths: list[tuple[GeometryStroke, bool]] = [(stroke, False) for stroke in second.get("strokes", [])]
        for source in first.get("strokes", []):
            points = [point for point in source.get("points", [])
                      if point.get("index", -1) >= origin
                      and _known_point(point, point.get("kind"), bars, dates, now)]
            if not points or points[0]["index"] != origin or points[0]["kind"] != "L":
                continue
            local_points = _reduce_local_points(points, source_level=2)
            wave_paths.append((dict(id=f'ordinary-local:{source["id"]}:{origin}',
                                    source_path=source["id"], points=local_points), True))
        for path, local in wave_paths:
            source_path = path.get("source_path", "")
            if source_path not in paths:
                continue
            # The pre-N last-fall high is frozen at N, as in precedingContexts.
            preceding = [point for source in first.get("strokes", [])
                         if _shares_source(source, source_path, paths)
                         for point in source.get("points", [])
                         if point.get("index", -1) < origin
                         and _known_point(point, "H", bars, dates, n["known"])]
            if not preceding:
                continue
            points = path.get("points", [])
            for a in points:
                if not _known_point(a, "H", bars, dates, now) or a.get("flip") != "翻多为空":
                    continue
                ai, ap = a["index"], a["value"]
                if ai <= attack or not n["one_p"] < ap < n["two_t"]:
                    continue
                maximum = (bars[_market_index.extreme_position(origin, ai + 1, high=True)].high
                           if _market_index is not None else max(bar.high for bar in bars[origin : ai + 1]))
                if maximum != ap:
                    continue
                first_break = (_market_index.first_extreme(ai + 1, now + 1, ap, high=True)
                               if _market_index is not None and _market_index.supports_price(ap) else
                               next((index for index in range(ai + 1, now + 1) if bars[index].high > ap), None))
                first_break = now + 1 if first_break is None else first_break
                lows = [point for point in points if ai < point["index"] < first_break
                        and _known_point(point, "L", bars, dates, now)]
                if not lows:
                    continue
                b = min(lows, key=lambda point: (point["value"], point["index"]))
                bi, bp = b["index"], b["value"]
                known = max(n["known"], dates[a["available_at"]], dates[b["available_at"]])
                c = next((point for point in points
                          if point["index"] > bi and point["value"] > ap
                          and _known_point(point, "H", bars, dates, now)
                          and point.get("flip") == "翻多为空"
                          and dates[point["available_at"]] >= known
                           and (bars[_market_index.extreme_position(
                               bi, dates[point["available_at"]] + 1, high=True)].high
                               if _market_index is not None else
                               max(bar.high for bar in bars[bi : dates[point["available_at"]] + 1])) == point["value"]),
                         None)
                if c is None or not origin_price < bp < ap <= c["value"]:
                    continue
                ci, cp = c["index"], c["value"]
                c_known = dates[c["available_at"]]
                if any(bar.low < origin_price and bar.close < origin_price
                       for bar in bars[attack + 1 : c_known + 1]):
                    continue
                pullback = range(ci + 1, now + 1)
                overlap = next((index for index in pullback if origin_price <= bars[index].low < ap), None)
                next_rise = next((index for index in pullback if bars[index].high > cp), None)
                if overlap is None or (next_rise is not None and next_rise <= overlap):
                    continue
                combined_known = max(known, c_known, overlap, *(dates[point["available_at"]] for point in preceding))
                highs = tuple(sorted({CombinedAHigh(point["index"], point["value"], dates[point["available_at"]])
                                      for source in first.get("strokes", [])
                                      if _shares_source(source, source_path, paths)
                                      for point in source.get("points", [])
                                      if point.get("index", -1) > ci
                                      and _known_point(point, "H", bars, dates, now)},
                                     key=lambda point: (point.index, point.known_index)))
                context = CombinedAContext(
                    source_path, origin, origin_price, ai, ap, bi, bp, ci, cp,
                    combined_known, attack, _child_durations(
                        bars, points, dates, origin, ci, c_known, positive, _market_index=_market_index),
                    highs,
                )
                key = origin, ai
                previous = groups.get(key)
                if previous is None or (previous[1] and not local) or (previous[1] == local and bp < previous[0].b_price):
                    groups[key] = context, local
    combined: dict[tuple[int, int], CombinedAContext] = {}
    for context, _ in groups.values():
        previous_combined = combined.get(context.identity)
        if previous_combined is None or context.a_index > previous_combined.a_index:
            combined[context.identity] = context
    return tuple(sorted(combined.values(), key=lambda context: context.identity))


def combined_a_entry_history(
    bars: Sequence[Bar], candidates: Mapping[int, tuple[CombinedAContext, ...]],
    *, entry_days: Sequence[bool] | None = None,
) -> tuple[list[dict[str, object]], dict[int, dict[str, object]]]:
    """Emit one qualified volume/body breakout per confirmed combined A.

    Duration includes the consolidation through the breakout session. A lost
    close floor retires that ABC permanently, even if later prices recover.
    """
    require_entry_days(entry_days, len(bars))
    events: list[dict[str, object]] = []
    proofs: dict[int, dict[str, object]] = {}
    market_index = _MarketRangeIndex.build(tuple(bars))
    if market_index is not None and not market_index.exact_prices:
        market_index = None
    seen: set[tuple[int, int]] = set()
    retired: set[tuple[int, int]] = set()
    for now in range(1, len(bars)):
        for current in candidates.get(now, ()):
            if current.known_index <= now and current.identity not in seen:
                seen.add(current.identity)
                events.append(dict(bar_index=now, event="combined_a_candidate", origin_index=current.origin_index,
                                   a_high_index=current.a_index, b_low_index=current.b_index, c_high_index=current.c_index,
                                   candidate_known_index=current.known_index, source_path=current.source_path))
        for context in candidates.get(now - 1, ()):
            if context.identity in retired or context.known_index >= now or not 0 <= context.c_index < now:
                continue
            floor = Fraction(str(context.c_price)) - Fraction(2, 3) * (
                Fraction(str(context.c_price)) - Fraction(str(context.origin_price)))
            pullback = range(context.c_index + 1, now + 1)
            if market_index is not None and market_index.supports_price(context.origin_price):
                origin_break = market_index.first_extreme(
                    context.c_index + 1, now + 1, context.origin_price, high=False)
                close_break = market_index.first_close_below(context.c_index + 1, now + 1, floor)
                invalid = min((index for index in (origin_break, close_break) if index is not None), default=None)
            else:
                invalid = next((index for index in pullback if bars[index].low < context.origin_price
                                 or Fraction(str(bars[index].close)) < floor), None)
            if invalid is not None:
                retired.add(context.identity)
                events.append(dict(bar_index=now, event="combined_a_invalidated", origin_index=context.origin_index,
                                   c_high_index=context.c_index, invalidated_index=invalid,
                                   invalidation_reason="origin_low_broken" if bars[invalid].low < context.origin_price
                                   else "two_thirds_close_broken"))
                continue
            if entry_days is not None and not entry_days[now]:
                continue
            duration = now - context.c_index
            b_duration = context.b_index - context.a_index
            exceeded_b = duration > b_duration
            exceeded_child = any(duration > child for child in context.child_pullback_durations)
            if not exceeded_b and not exceeded_child:
                continue
            bar, previous = bars[now], bars[now - 1]
            exact_body = Fraction(str(bar.close)) - Fraction(str(bar.open))
            exact_spread = Fraction(str(bar.high)) - Fraction(str(bar.low))
            if (previous.volume <= 0 or bar.volume <= previous.volume or bar.open <= 0 or exact_spread <= 0
                    or exact_body < Fraction(str(bar.open)) * Fraction(3, 100)
                    or exact_body < exact_spread * Fraction(3, 5)):
                continue
            highs = [high for high in context.rebound_highs if high.index < now and high.known_index < now]
            high = max(highs, key=lambda item: (item.index, item.known_index), default=None)
            gap = bar.open > previous.high and bar.low > previous.high and bar.close > previous.high
            close_breakout = high is not None and previous.close <= high.price < bar.close
            if not gap and not close_breakout:
                continue
            if gap:
                reference_index, reference_price, reference_known = now - 1, previous.high, now - 1
            elif high is not None:
                reference_index, reference_price, reference_known = high.index, high.price, high.known_index
            else:
                continue
            # 完整防线失效先更新；只有实际满足突破窗口的日期才提取回调极值和证明。
            bottom = (market_index.extreme_position(context.c_index + 1, now + 1, high=False)
                      if market_index is not None else min(pullback, key=lambda index: (bars[index].low, index)))
            retired.add(context.identity)
            proof: dict[str, object] = dict(
                buy_point_type="combined_a_pullback_breakout", trend_level=context.trend_level,
                source_path=context.source_path, origin_index=context.origin_index, origin_price=context.origin_price,
                a_high_index=context.a_index, a_high_price=context.a_price,
                b_low_index=context.b_index, b_low_price=context.b_price,
                c_high_index=context.c_index, c_high_price=context.c_price,
                candidate_known_index=context.known_index, n_index=context.n_index,
                pullback_low_index=bottom, pullback_low_price=bars[bottom].low,
                pullback_duration=duration, internal_b_duration=b_duration,
                child_pullback_durations=list(context.child_pullback_durations),
                duration_exceeded_internal_b=exceeded_b, duration_exceeded_child_n=exceeded_child,
                two_thirds_floor=float(floor), minimum_pullback_close=min(bars[index].close for index in pullback),
                breakout_type="gap" if gap else "body_breakout",
                rebound_high_index=reference_index, rebound_high_price=reference_price,
                rebound_high_known_index=reference_known,
                breakout_close=bar.close, breakout_body_fraction=float(exact_body / Fraction(str(bar.open))),
                breakout_body_range_fraction=float(exact_body / exact_spread),
                breakout_volume=bar.volume, previous_volume=previous.volume,
                stop=bars[bottom].low, target=context.c_price, attack=now,
                squeeze_confirmation="combined_a_pullback_breakout",
                combined_a_origin_date=_bar_date(bars[context.origin_index]),
                combined_a_origin_price=context.origin_price,
                combined_a_top_date=_bar_date(bars[context.c_index]), combined_a_top_price=context.c_price,
                combined_a_known_date=_bar_date(bars[context.known_index]),
                combined_a_internal_pullback_sessions=b_duration,
                combined_a_child_pullback_sessions=context.child_pullback_durations[-1]
                if context.child_pullback_durations else None,
                combined_a_pullback_sessions=duration,
                combined_a_pullback_date=_bar_date(bars[bottom]), combined_a_pullback_low=bars[bottom].low,
                combined_a_two_thirds_price=float(floor),
                combined_a_minimum_close=min(bars[index].close for index in pullback),
                combined_a_breakout_date=_bar_date(bars[reference_index]),
                combined_a_breakout_price=reference_price,
                combined_a_breakout_known_date=_bar_date(bars[reference_known]),
                combined_a_breakout_type="gap" if gap else "body_breakout",
                confirmation_close=bar.close, breakout_volume_multiple=bar.volume / previous.volume,
                breakout_body_pct=float(exact_body / Fraction(str(bar.open))),
                breakout_body_ratio=float(exact_body / exact_spread),
                counter_ratio=(context.c_price - min(bars[index].close for index in pullback))
                / (context.c_price - context.origin_price),
            )
            events.append(dict(proof, bar_index=now, event="combined_a_pullback_breakout"))
            # Multiple historical combinations can qualify together; prefer the latest C.
            existing = proofs.get(now)
            if existing is None or context.c_index > int(str(existing["c_high_index"])):
                proofs[now] = proof
    return events, proofs
