"""Confirm an ordinary or recovery pullback using the same alternation rules.

This observer produces structural evidence before trading permission exists.
It never generates an order or promotes a polyline low into a trend vertex.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction
from typing import Protocol

from ..models.model import Bar
from ..market_state.control_bar import observe_control_bar
from ..market_state.market_regime import MarketRegime, RegimePolicy, WaveBoundary, observe_market_regime
from ..market_state.squeeze_state import observe_squeeze_resumption
from ..market_state.volume_reversal import volume_reversal_squeeze
from ..market_state.candle_strength import double_volume_bullish_breakout, volume_high_open_rising
from ..strategies.attack_quality import medium_bullish_body, squeeze_buy_rejection, v3_positive_n_attack_rejection
from ..strategies.n_consolidation import consolidation_gap
from .alternation_duration import filter_duration_landmarks, short_shallow_pullback
from .n_shape import BoxAnchorMode, MilestoneBasis, NSetup, PivotRef, observe_n
from .price_action import Direction, ShadowPolicy, teaching_inside
from .squeeze_alternation import squeeze_alternations
from .trend_market_index import _MarketRangeIndex


class ConfirmedTurn(Protocol):
    @property
    def index(self) -> int: ...
    @property
    def ordinal(self) -> int: ...
    @property
    def kind(self) -> str: ...
    @property
    def price(self) -> float: ...
    @property
    def known(self) -> int: ...


@dataclass(frozen=True)
class AlternationPolicy:
    structure_window: int = 120
    pattern_ttl: int = 60
    volume_lookback: int = 20
    shadow_fraction: float = .5
    strict_n_attack_quality: bool = True
    squeeze_pullback_entries: bool = True


@dataclass(frozen=True)
class AlternationProof:
    low: ConfirmedTurn
    rule: str
    source: tuple[ConfirmedTurn, ...] = ()
    evidence: dict[str, object] | None = None
    trend_source: tuple[ConfirmedTurn, ...] = ()


def _reference(point: ConfirmedTurn, bars: Sequence[Bar]) -> dict[str, object]:
    return dict(index=point.index, ordinal=point.ordinal, kind=point.kind, value=point.price,
                time=bars[point.index].timestamp.date().isoformat(), label=point.kind,
                available_at=bars[point.known].timestamp.date().isoformat())


def _polyline_last_fall_pair(
    polyline: Sequence[ConfirmedTurn],
) -> tuple[ConfirmedTurn, ConfirmedTurn, int] | None:
    from .polyline import LinePoint, PointKind, ReversalPoint
    from .polyline_lifecycle import polyline_last_fall_pair

    pair = polyline_last_fall_pair(tuple(ReversalPoint(
        LinePoint(point.index, point.ordinal, PointKind(point.kind), point.price), point.known,
        'causal_alternation_polyline') for point in polyline))
    if pair is None:
        return None
    key, low = pair
    return (
        next(point for point in polyline if (point.index, point.ordinal) == (key.point.index, key.point.ordinal)),
        next(point for point in polyline if (point.index, point.ordinal) == (low.point.index, low.point.ordinal)),
        max(key.confirmed_index, low.confirmed_index),
    )


def confirm_volume_breakout_alternation(
    bars: Sequence[Bar], now: int, *, origin: ConfirmedTurn, high: ConfirmedTurn,
    polyline: Sequence[ConfirmedTurn], _market_index: _MarketRangeIndex | None = None,
    confirmed_n_squeeze: bool = False,
) -> AlternationProof | None:
    """倍量强阳突破冻结折线末跌高，独立确认已有翻多背景的回撤结束。"""
    if (not 0 <= origin.index < high.index < now < len(bars)
            or max(origin.known, high.known) > now or any(point.known > now for point in polyline)):
        return None
    bar, previous = bars[now], bars[now - 1]
    doubled = double_volume_bullish_breakout(bar, previous)
    if confirmed_n_squeeze:
        if (not medium_bullish_body(bar) or bar.volume <= previous.volume
                or squeeze_buy_rejection(bar, previous) is not None):
            return None
    elif not doubled and not volume_high_open_rising(bar, previous):
        return None
    pair = _polyline_last_fall_pair(polyline)
    if pair is None:
        return None
    key, low, known = pair
    if (known >= now or not high.index <= key.index < low.index < now
            or not origin.price < low.price < high.price or bar.close <= key.price):
        return None
    market = (_market_index if _market_index is not None and bars is _market_index.bars
              and _market_index.exact_prices else None)
    for point in (origin, low):
        broken = (market.first_extreme(point.index + 1, now + 1, point.price, high=False) is not None
                  if market is not None and market.supports_price(point.price) else
                  any(row.low < point.price for row in bars[point.index + 1:now + 1]))
        if broken:
            return None
    resistance = max(key.price,
        bars[market.extreme_position(low.index, now, high=True)].high if market is not None
        else max(row.high for row in bars[low.index:now]))
    if bar.close <= resistance:
        return None
    minimum_close = (bars[market.lowest_close_position(high.index + 1, low.index + 1)].close
                     if market is not None else min(row.close for row in bars[high.index + 1:low.index + 1]))
    if (not confirmed_n_squeeze and short_shallow_pullback(origin.price, high.price, minimum_close,
                              high.index - origin.index, low.index - high.index)
            and bar.close <= high.price):
        return None
    return AlternationProof(low, 'volume_bullish_polyline_last_fall_high_breakout', (key, low), dict(
        confirmation_source='double_volume_bullish_breakout' if doubled else 'volume_high_open_rising',
        candidate_index=now, polyline_key_index=key.index, polyline_key_price=key.price,
        polyline_key_known_index=known,
        b_low_index=low.index, b_low_price=low.price, b_minimum_close=minimum_close,
        breakout_resistance=resistance, confirmation_close=bar.close,
        volume=bar.volume, previous_volume=previous.volume, volume_multiple=bar.volume / previous.volume,
    ))


def confirm_body_gap_alternation(
    bars: Sequence[Bar], now: int, *, origin: ConfirmedTurn, high: ConfirmedTurn,
    polyline: Sequence[ConfirmedTurn], _market_index: _MarketRangeIndex | None = None,
) -> AlternationProof | None:
    """守低后的实体跳空放量中大阳，收盘突破回撤低点以来的已知阻力。"""
    if not 0 <= origin.index < high.index < now < len(bars) or max(origin.known, high.known) > now:
        return None
    bar, previous = bars[now], bars[now - 1]
    if (not medium_bullish_body(bar) or previous.volume <= 0 or bar.volume <= previous.volume
            or bar.open <= max(previous.open, previous.close)):
        return None
    market = (_market_index if _market_index is not None and bars is _market_index.bars
              and _market_index.exact_prices else None)
    broken = (market.first_extreme(origin.index + 1, now + 1, origin.price, high=False) is not None
              if market is not None and market.supports_price(origin.price)
              else min(item.low for item in bars[origin.index + 1:now + 1]) < origin.price)
    if broken:
        return None
    low_index = (market.extreme_position(high.index + 1, now, high=False)
                 if market is not None and high.index + 1 < now else
                 min(range(high.index + 1, now), key=lambda index: (bars[index].low, index), default=None))
    low = next((point for point in polyline if point.kind == 'L' and point.index == low_index
                and point.known <= now and point.price == bars[point.index].low), None)
    if low is None or not origin.price < low.price < high.price:
        return None
    resistance = (bars[market.extreme_position(low.index, now, high=True)].high if market is not None
                  else max(item.high for item in bars[low.index:now]))
    pair = _polyline_last_fall_pair(polyline)
    if pair is not None:
        key, _, known = pair
        if known >= now:
            return None
        resistance = max(resistance, key.price)
    if bar.close <= resistance:
        return None
    minimum_close = (bars[market.lowest_close_position(high.index + 1, low.index + 1)].close if market is not None
                     else min(item.close for item in bars[high.index + 1:low.index + 1]))
    if (short_shallow_pullback(origin.price, high.price, minimum_close,
                              high.index - origin.index, low.index - high.index)
            and bar.close <= high.price):
        return None
    return AlternationProof(low, 'body_gap_volume_bullish_breakout_after_defended_low', (low,), dict(
        confirmation_source='body_gap_volume_bullish_breakout', candidate_index=now,
        a_origin_index=origin.index, a_origin_price=origin.price,
        a_high_index=high.index, a_high_price=high.price,
        b_low_index=low.index, b_low_price=low.price, b_minimum_close=minimum_close,
        a_duration=high.index - origin.index, b_duration=low.index - high.index,
        gap_previous_body_high=max(previous.open, previous.close), gap_open=bar.open,
        breakout_resistance=resistance, confirmation_close=bar.close,
        volume=bar.volume, previous_volume=previous.volume,
        body_fraction=(bar.close - bar.open) / bar.open,
        body_range_fraction=(bar.close - bar.open) / (bar.high - bar.low),
    ))



def confirm_double_n_pullback(
    bars: Sequence[Bar], now: int, *, attack: int, origin: ConfirmedTurn,
    high: ConfirmedTurn, polyline: Sequence[ConfirmedTurn],
) -> AlternationProof | None:
    """已确认正 N 和轧空的调用方，以整个翻多波段确认回调资格。"""
    if not 0 <= origin.index < high.index < attack < now < len(bars) or attack == 0:
        return None
    if max(origin.known, high.known) > attack:
        return None
    n_bar, previous, bar = bars[attack], bars[attack - 1], bars[now]
    if (previous.volume <= 0 or n_bar.volume < 2 * previous.volume or n_bar.close <= n_bar.open
            or bar.close <= n_bar.close or not medium_bullish_body(bar)
            or squeeze_buy_rejection(bar, bars[now - 1], previous) is not None):
        return None
    bottom = min(range(high.index + 1, now + 1), key=lambda index: (bars[index].low, index))
    low = next((point for point in polyline if point.kind == 'L' and point.index == bottom
                and point.price == bars[bottom].low and point.known <= now), None)
    if low is None or low.index >= attack:
        return None
    start, peak, floor = (Fraction(str(value)) for value in (origin.price, high.price, low.price))
    if peak <= start or not start < floor < peak or (peak - floor) * 3 > (peak - start) * 2:
        return None
    if any(row.low < origin.price for row in bars[origin.index + 1:now + 1]):
        return None
    if any(row.close > high.price for row in bars[high.index + 1:attack]):
        return None
    return AlternationProof(low, 'double_volume_positive_n_squeeze_whole_flip_pullback', (low,), dict(
        confirmation_source='double_volume_positive_n_squeeze', attack=attack,
        candidate_index=now, bar_index=now,
        a_origin_index=origin.index, a_origin_price=origin.price,
        a_high_index=high.index, a_high_price=high.price,
        b_low_index=low.index, b_low_price=low.price,
        retracement_ratio=float((peak - floor) / (peak - start)),
        two_thirds_price=float(peak - (peak - start) * Fraction(2, 3)),
        counter_basis='minimum_low_over_whole_flip', counter_operator='<=',
        attack_volume_multiple=n_bar.volume / previous.volume,
        confirmation_close=bar.close,
    ))



def alternation_n_sources(
    polyline: Sequence[ConfirmedTurn], *, earliest: int,
) -> tuple[tuple[ConfirmedTurn, ConfirmedTurn, ConfirmedTurn], ...]:
    """资格前与资格内复用相同外层 N 枚举，保留原始顶点和知识日期。"""
    from ..strategies.folded_n import folded_positive_n_candidates
    from .polyline import LinePoint, PointKind, ReversalPoint

    source = tuple(point for point in polyline if point.index >= earliest)
    triples = [(a, b, c) for a, b, c in zip(source, source[1:], source[2:])
               if (a.kind, b.kind, c.kind) == ('L', 'H', 'L')]
    references = tuple(ReversalPoint(
        LinePoint(point.index, point.ordinal, PointKind(point.kind), point.price), point.known,
        'causal_alternation_polyline') for point in source)
    originals = {reference: point for reference, point in zip(references, source)}
    for refs, _ in folded_positive_n_candidates(references, earliest=earliest, allow_equal_origin=True):
        triple = (originals[refs[0]], originals[refs[1]], originals[refs[2]])
        if triple not in triples:
            triples.append(triple)
    return tuple(triples)


def confirm_bull_alternation(
    bars: Sequence[Bar], now: int, *, origin: ConfirmedTurn, high: ConfirmedTurn,
    broken: ConfirmedTurn, points: Sequence[ConfirmedTurn], polyline: Sequence[ConfirmedTurn],
    level: int, path: str, policy: AlternationPolicy = AlternationPolicy(), allow_gap_confirmation: bool = True,
    flip_confirmed_at: int | None = None, after_index: int | None = None,
    _market_index: _MarketRangeIndex | None = None,
) -> AlternationProof | None:
    """守住已确认翻多起点，再以浅回撤、重突破、N轧空或跳空确认交替。"""
    if (type(level) is not int or level not in (0, 1, 2, 3) or not path
            or not (origin.index < high.index <= now < len(bars))):
        return None
    if flip_confirmed_at is not None and (type(flip_confirmed_at) is not int
            or not 0 <= flip_confirmed_at <= now):
        return None
    if after_index is not None and (type(after_index) is not int or not 0 <= after_index < now):
        return None
    if any(point.known > now for point in (origin, high, broken, *points, *polyline)):
        raise ValueError('alternation confirmation requires causal points')
    market = _market_index if _market_index is not None and bars is _market_index.bars else None
    broken_origin = (market.first_extreme(origin.index + 1, now + 1, origin.price, high=False) is not None
                     if market is not None and market.supports_price(origin.price)
                     else min(bar.low for bar in bars[origin.index + 1:now + 1]) < origin.price)
    if broken_origin:
        return None
    structural_breakout = confirm_volume_breakout_alternation(
        bars, now, origin=origin, high=high, polyline=polyline, _market_index=market)
    gap_proof = (confirm_body_gap_alternation(bars, now, origin=origin, high=high, polyline=polyline, _market_index=market)
                 if allow_gap_confirmation else None)
    ordinary_proof: AlternationProof | None = None
    # 回调可以继续创新低；首个低点破位后，应由当前已确认的整段最低点
    # 接受原确认门禁。相等保留最早坐标，未确认的新低仍由行情守低校验拒绝。
    counter = min((point for point in points if point.kind == 'L' and point.index > high.index
                   and (after_index is None or point.known > after_index)),
                  key=lambda point: (point.price, point.index, point.ordinal), default=None)
    counter_defended = (counter is not None and not (
        market.first_extreme(counter.index + 1, now + 1, counter.price, high=False) is not None
        if market is not None and market.supports_price(counter.price)
        else any(bar.low < counter.price for bar in bars[counter.index + 1:now + 1])))
    if counter is not None and counter_defended and origin.price < counter.price < high.price:
        ratio = (high.price - counter.price) / (high.price - origin.price)
        rebreak = next((point for point in points if point.kind == 'H'
                       and point.index > counter.index and point.price > high.price), None)
        if ratio < 2 / 3 or rebreak is not None:
            known = max(high.known, counter.known, rebreak.known if ratio >= 2 / 3 and rebreak else 0)
            landmark = dict(_reference(counter, bars), confirmed_bear_low=_reference(origin, bars),
                            confirmed_flip_high=_reference(high, bars),
                            available_at=bars[known].timestamp.date().isoformat())
            if ((after_index is None or known > after_index)
                    and filter_duration_landmarks([landmark], bars[:now + 1])):
                ordinary_proof = AlternationProof(counter, 'confirmed_higher_pullback_strictly_below_two_thirds'
                    if ratio < 2 / 3 else 'confirmed_higher_pullback_then_confirmed_flip_high_rebreak',
                    trend_source=(counter, rebreak) if ratio >= 2 / 3 and rebreak is not None else (counter,))

    # Ordinary and special flips both observe these bounded N candidates.
    # Their vertices come from the current causal polyline, never future trend lows.
    anchor = dict(origin=_reference(origin, bars), high=_reference(high, bars),
                  key=_reference(broken, bars), source_path=path, trend_level=level,
                  known_index=max(origin.known, high.known, broken.known))
    for a, b, c in alternation_n_sources(
            polyline, earliest=max(high.index + 1, now - policy.structure_window)):
        known = max(a.known, b.known, c.known)
        if (a.kind, b.kind, c.kind) != ('L', 'H', 'L') or a.index <= high.index:
            continue
        if now - a.index > policy.structure_window or now > known + policy.pattern_ttl:
            continue
        mother = a.index == b.index < c.index and a.ordinal < b.ordinal
        same_bar = a.index < b.index == c.index and b.ordinal < c.ordinal
        if not (a.index < b.index < c.index or mother or same_bar):
            continue
        inside = same_bar and b.index > 0 and teaching_inside(bars[b.index - 1], bars[b.index])
        offset = max(0, a.index - policy.volume_lookback)
        validated = market.validated_window(offset, now + 1) if market is not None else None
        data = validated if validated is not None else bars[offset:now + 1]
        setup = NSetup(bars[0].symbol, '1d', Direction.UP,
            PivotRef(a.index - offset, a.known - offset), PivotRef(b.index - offset, b.known - offset),
            PivotRef(c.index - offset, c.known - offset), 'lecture_causal', BoxAnchorMode.ATTACK_VIRTUAL_EXTREME,
            allow_confirmation_bar=True, allow_outside_close=True, allow_mother_impulse=mother,
            staged_defense=True, allow_mother_pullback=same_bar and not inside, allow_inside_pullback=inside)
        try:
            n = observe_n(data, setup, timeframe='1d', milestone_basis=MilestoneBasis.EXTREME,
                          measure_targets=False)
        except ValueError:
            continue
        if n.completion is None:
            continue
        attack = n.completion.bar_index + offset
        # 旧窗口的 N 已消费；续查只接受结束后新完成的攻击，避免旧轧空状态每日重开。
        if after_index is not None and max(attack, known) <= after_index:
            continue
        if policy.strict_n_attack_quality:
            control = observe_control_bar(data, setup, timeframe='1d', volume_lookback=policy.volume_lookback,
                                          shadow_policy=ShadowPolicy(policy.shadow_fraction), _prepared_n=n)
            if v3_positive_n_attack_rejection(bars[attack], control.volume):
                continue
        positive = dict(event='n_completed', direction='up', bar_index=attack, known_at=known,
                        origin=a.index, neckline=b.index, pullback=c.index,
                        outside_close_confirmed=c.index == attack)

        def confirmed_proof(triggers: Sequence[dict[str, object]]) -> AlternationProof | None:
            confirmed = squeeze_alternations(bars[:now + 1], [positive, *triggers], {attack: [anchor]})
            for event in confirmed:
                if event['event'] != 'squeeze_alternation_confirmed' or event['bar_index'] != now:
                    continue
                low = next((point for point in polyline if point.kind == 'L'
                            and point.index == event['b_low_index'] and point.price == event['b_low_price']), None)
                if low is not None:
                    rule = ('positive_n_consolidation_gap_after_qualified_b'
                            if event.get('confirmation_source') == 'defended_n_consolidation_gap'
                            else 'positive_n_and_squeeze_after_qualified_b')
                    return AlternationProof(low, rule, tuple(dict.fromkeys((low, a, b, c))), dict(event))
            return None

        triggers: list[dict[str, object]] = []
        gap = (consolidation_gap(bars, attack=attack, now=now, defense=n.completion.defense)
               if allow_gap_confirmation else None)
        if gap is not None:
            triggers.append(dict(gap, event='n_consolidation_gap_confirmed', bar_index=now, attack=attack))
            proof = confirmed_proof(triggers)
            if proof is not None:
                return proof
        # A completed gap proof needs no second regime replay; ordinary N
        # confirmation shares the same formation with its downstream observers.
        regime = observe_market_regime(data, setup, timeframe='1d',
            policy=RegimePolicy(ShadowPolicy(policy.shadow_fraction), WaveBoundary.ORIGIN,
                                local_resistance_failure=True), _prepared_n=n)
        frame = regime.latest
        if frame is not None:
            reversal = (volume_reversal_squeeze(bars, attack=attack, now=now, origin_low=n.anchors.origin,
                attack_defense=n.completion.defense, frame=frame, offset=offset) if n.anchors is not None else None)
            if frame.regime in (MarketRegime.BULL, MarketRegime.STRONG_BULL) or reversal is not None:
                confirmed_regime = MarketRegime.BULL if reversal is not None else frame.regime
                assert confirmed_regime is not None
                local_breakout = confirm_volume_breakout_alternation(
                    bars, now, origin=origin, high=high, polyline=polyline,
                    _market_index=market, confirmed_n_squeeze=True)
                if (local_breakout is not None and attack < now
                        and bars[now].close > bars[attack].close
                        and squeeze_buy_rejection(bars[now], bars[now - 1], bars[attack - 1]) is None):
                    return AlternationProof(local_breakout.low,
                        'positive_n_and_squeeze_after_qualified_b',
                        tuple(dict.fromkeys((*local_breakout.source, a, b, c))),
                        dict(local_breakout.evidence or {},
                             confirmation_source='positive_n_squeeze_polyline_key_breakout',
                             bar_index=now, candidate_index=now, attack=attack,
                             n_origin=a.index, n_neckline=b.index, n_pullback=c.index,
                             source_path=path, trend_level=level))
                whole_flip = confirm_double_n_pullback(
                    bars, now, attack=attack, origin=origin, high=high, polyline=polyline)
                if whole_flip is not None:
                    return AlternationProof(whole_flip.low, whole_flip.rule,
                        tuple(dict.fromkeys((whole_flip.low, a, b, c))),
                        dict(whole_flip.evidence or {}, n_origin=a.index, n_neckline=b.index,
                             n_pullback=c.index, source_path=path, trend_level=level))
                triggers.append(dict(event='regime_confirmation', regime=confirmed_regime.value,
                                     bar_index=now, attack=attack))
            elif policy.squeeze_pullback_entries and observe_squeeze_resumption(bars, now,
                    frame=frame, offset=offset, attack_index=attack, defense=n.completion.defense) is not None:
                triggers.append(dict(event='squeeze_resumption_observed', bar_index=now, attack=attack))
        if not triggers:
            continue
        proof = confirmed_proof(triggers)
        if proof is not None:
            return proof
    # 当天同时满足正 N 轧空时，优先发布带原 N 身份的共同确认；
    # 没有共同确认才回退独立证据，资格日期始终属于当前已知收盘。
    return structural_breakout or gap_proof or ordinary_proof


# 保留旧调用名，普通与特殊分支使用同一个确认实现。
confirm_recovery_alternation = confirm_bull_alternation
