"""Publish qualified trend vertices while retaining their whole-wave extremes."""

from collections.abc import Iterator, Mapping, MutableSequence, Sequence
from copy import deepcopy
from fractions import Fraction
import hashlib
import json
from math import isfinite
from typing import TypeAlias, cast

from ..models.model import Bar
from .trend_confirmation import (
    ConfirmationContext, _PrefixSessions, prepare_confirmation_context, qualify_downtrend, qualify_uptrend,
)
from .n_trend_confirmation import N_TARGET_CONFIRMATION, is_n_target_reversal, qualified_n_source
from .hierarchical_confirmation import session_date
from .trend_market_index import _MarketPrefixIndex
from .trend_frozen_state import FrozenPrefixList
from .trend_publication_cache import _PublicationCache, _PublicationCheckpoint

Moment: TypeAlias = int | str
LEG_CONFIRMATION_POLICY = 'two_routes_each_direction_v108'
OPTIONAL_LEG_CONFIRMATION_POLICY = 'enabled_routes_each_direction_v110'
WAVE_DISPLAY_POLICY = 'one_connection_per_confirmed_wave_v109'


def _wave_reference(value: object) -> tuple[object, ...] | None:
    if not isinstance(value, Mapping):
        return None
    index, ordinal, price = value.get('index'), value.get('ordinal', 0), value.get('value')
    known, time, kind = value.get('available_at'), value.get('time'), value.get('kind')
    if (type(index) is not int or index < 0 or type(ordinal) is not int or ordinal < 0
            or type(price) not in (int, float) or not isfinite(cast(float, price))
            or not isinstance(time, str) or not time or kind not in ('H', 'L', 'K')
            or not (type(known) is int or isinstance(known, str) and bool(known))):
        return None
    return index, ordinal, time, kind, str(Fraction(str(price))), known


def trend_wave_identity(proof: Mapping[str, object], *, trend_level: int, source_path: str) -> str | None:
    """Name a causal wave independently of its changing displayed endpoint.

    Labels and direct-route rendering fields are not new proofs. Origin,
    source boundary, level and every required first-known evidence are; equal
    prices or dates alone must never collapse independently confirmed waves.
    """
    rule = proof.get('confirmation_rule')
    if (type(trend_level) is not int or trend_level not in (1, 2, 3) or not source_path or proof.get('direction') not in ('up', 'down')
            or rule not in ('strict_same_level_market_key_break', 'source_key_break_alternation_then_market_turn', N_TARGET_CONFIRMATION)):
        return None
    fields = ['origin', 'broken_key', 'confirmed_by']
    if rule == 'source_key_break_alternation_then_market_turn':
        fields += ['flip_high', 'alternation_low'] if proof['direction'] == 'up' else ['flip_low', 'alternation_high']
    if rule == N_TARGET_CONFIRMATION:
        fields += ['n_neckline', 'n_pullback', 'n_completion']
    references = [_wave_reference(proof.get(field)) for field in fields]
    if rule == 'source_key_break_alternation_then_market_turn':
        references.append(_wave_reference(proof.get('retracement_origin', proof.get('origin'))))
    wave_origin = _wave_reference(proof.get('wave_origin', proof.get('origin')))
    known = proof.get('available_at')
    if any(ref is None for ref in references) or wave_origin is None or not (type(known) is int or isinstance(known, str) and bool(known)):
        return None
    payload = [trend_level, source_path, proof['direction'], rule, known, references, wave_origin]
    if rule == N_TARGET_CONFIRMATION:
        anchor, target = proof.get('box_anchor'), proof.get('one_p_target')
        if any(type(value) not in (int, float) or not isfinite(cast(float, value)) for value in (anchor, target)):
            return None
        payload += [str(Fraction(str(anchor))), str(Fraction(str(target)))]
    return 'trend-wave-v1:' + hashlib.sha256(json.dumps(payload, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _moment(point: Mapping[str, object]) -> Moment:
    value = point['available_at']
    if type(value) is int or isinstance(value, str):
        return value
    raise ValueError('trend availability must be a session index or date')


def _later(left: Moment, right: Moment) -> Moment:
    if isinstance(left, str) and isinstance(right, str):
        return max(left, right)
    if type(left) is int and type(right) is int:
        return max(left, right)
    raise ValueError('one trend path must use one availability representation')


def _held_wave_floor(origin: Mapping[str, object], bars: Sequence[Bar], through_index: int,
                     *, _market_index: _MarketPrefixIndex | None = None) -> bool:
    """An earlier floor belongs to the same wave only while its actual price holds."""
    index, value = cast(int, origin['index']), cast(float, origin['value'])
    if not bars or not 0 <= index <= through_index < len(bars) or bars[index].low != value:
        return False
    if _market_index is not None:
        if not _market_index.matches(bars):
            raise ValueError('a market index must match its immutable Bar prefix')
        if _market_index.owner.exact_prices and _indexed_price(value):
            return _market_index.first_extreme(index + 1, through_index + 1, value, high=False) is None
    return all(isfinite(bars[position].low) and bars[position].low >= value
               for position in range(index + 1, through_index + 1))


def _indexed_price(value: object) -> bool:
    return (type(value) is float and isfinite(value)
            or type(value) is int and abs(value) <= 2 ** 53)


def _same_origin(proof: Mapping[str, object], point: Mapping[str, object]) -> bool:
    origin = proof.get('wave_origin', proof.get('origin'))
    return isinstance(origin, Mapping) and all(
        origin.get(field, 0 if field == 'ordinal' else None) == point.get(field, 0 if field == 'ordinal' else None)
        for field in ('index', 'ordinal', 'kind', 'value')
    )


class _OriginLookup:
    """同次只读发布按原相等语义索引来源，重复坐标保持输入的首个匹配。"""

    def __init__(self, points: Sequence[Mapping[str, object]]) -> None:
        self.points = points
        self._index: dict[tuple[object, ...], list[Mapping[str, object]]] | None = {}
        for point in points:
            key = self._key(point)
            if key is None or 'kind' not in point:
                # 自定义比较／mapping 继续走旧查找，不增加其 __eq__/get 调用。
                self._index = None
                break
            self._index.setdefault(key, []).append(point)

    def update(self, points: Sequence[Mapping[str, object]]) -> None:
        from .trend_point_version import PointVersion
        from .owned_evidence import CertifiedSource

        if points is self.points:
            return
        if (type(points) not in (PointVersion, CertifiedSource)
                or not cast(PointVersion, points)._sealed or self._index is None):
            self.__init__(points)
            return
        previous = self.points if isinstance(self.points, PointVersion) else None
        start = cast(PointVersion, points).changed_since(previous)
        suffix = [(self._key(point), point) for point in points[start:]]
        if any(key is None or 'kind' not in point for key, point in suffix):
            self.points, self._index = points, None
            return
        for point in reversed(self.points[start:]):
            key = self._key(point)
            if key is not None:
                bucket = self._index[key]
                bucket.pop()
                if not bucket:
                    del self._index[key]
        for key, point in suffix:
            assert key is not None
            self._index.setdefault(key, []).append(point)
        self.points = points

    @staticmethod
    def _key(point: Mapping[str, object]) -> tuple[object, ...] | None:
        if type(point) is not dict:
            return None
        key = tuple(point.get(field, 0 if field == 'ordinal' else None)
                    for field in ('index', 'ordinal', 'kind', 'value'))
        if any(type(value) not in (int, float, bool, str, type(None))
               or type(value) is float and not isfinite(value) for value in key):
            return None
        return key

    def find(self, reference: Mapping[str, object], *, kind: str | None = None,
             known_point: Mapping[str, object] | None = None) -> Mapping[str, object] | None:
        key = self._key(reference)
        indexed = self._index.get(key, ()) if self._index is not None and key is not None else self.points
        return next((point for point in indexed if (kind is None or point['kind'] == kind)
            and (self._index is not None and key is not None or _same_origin(dict(origin=reference), point))
            and (known_point is None or _moment(point) == _moment(known_point))), None)


def _known_leg_proof(
    proof: object, point: Mapping[str, object], direction: str, origin: Mapping[str, object],
) -> bool:
    trigger = proof.get('confirmed_by') if isinstance(proof, Mapping) else None
    return (isinstance(proof, Mapping) and isinstance(trigger, Mapping)
            and type(trigger.get('index')) is int and cast(int, point['index']) >= cast(int, trigger['index'])
            and proof.get('direction') == direction and _same_origin(proof, origin)
            and _later(_moment(proof), _moment(point)) == _moment(point))


class _PublishedTrendState:
    """Share causal direction tracking before and after a candidate is exposed.

    A local proof does not reset an active wave. A real opposite proof does,
    including on a private candidate lacking permission for its next up leg.
    Retain one static snapshot and the latest prefix context, proof and extreme.
    """

    def __init__(self, source: Sequence[Mapping[str, object]], bars: Sequence[Bar], source_level: int,
                 *, qualified_source: Sequence[Mapping[str, object]] | None = None,
                 n_target_trend_confirmation_enabled: bool = False,
                 _market_index: _MarketPrefixIndex | None = None) -> None:
        self.source = source
        self.n_enabled = n_target_trend_confirmation_enabled
        self.n_source = (qualified_n_source(source, qualified_source)
                         if self.n_enabled and qualified_source is not None else source)
        self.bars = bars
        if _market_index is not None and not _market_index.matches(bars):
            raise ValueError('a market index must match its immutable Bar prefix')
        self.market_index = _market_index
        self.sessions: Mapping[str, int] = (
            _PrefixSessions(_market_index.owner.positions, len(bars) - 1) if _market_index is not None else
            {session_date(bar): index for index, bar in enumerate(bars)})
        self.position_field = 'source_turn_position' if source_level == 0 else f'source_level{source_level}_position'
        self.parsed_context: ConfirmationContext | None = None
        self.context: ConfirmationContext | None = None
        self.active: Mapping[str, object] | None = None
        self.extreme: Mapping[str, object] | None = None

    def confirmation_context(self, cutoff: int) -> ConfirmationContext:
        """同次路径发布共享静态解析，各方向证书仍使用自身真实截止前缀。

        完整快照只在第一次核验时准备，复用来源坐标／日期／Fraction 解析。
        下降腿常有不同的首次可知日，不能把完整快照直接授给早日；它们通过
        prepare_confirmation_context 投影并重新验证可知来源及 N 前缀。上涨
        和下降升级后的独立上涨使用整体截止时，复用同一完整上下文即可。
        只保留完整快照与最近一个投影，不为每个历史日期积累解析缓存。
        启用 N 时沿用原来的实际 cutoff 构建路径，避免仅为了静态复用而
        提前解析完整 N 行情；N 的首次核验、来源校验和异常边界保持原样。
        """
        if self.n_enabled:
            if self.context is None or self.context.end_index != cutoff:
                self.context = prepare_confirmation_context(
                    self.source, self.bars or None, cutoff, n_source=self.n_source,
                    n_target_trend_confirmation_enabled=True,
                    _market_index=self.market_index,
                )
            return self.context
        if self.parsed_context is None:
            market = self.bars or None
            snapshot_end = len(self.bars) - 1 if self.bars else cutoff
            self.parsed_context = prepare_confirmation_context(
                self.source, market, snapshot_end, n_source=self.n_source,
                n_target_trend_confirmation_enabled=self.n_enabled,
                _market_index=self.market_index,
            )
        if self.parsed_context.end_index == cutoff:
            return self.parsed_context
        if self.context is None or self.context.end_index != cutoff:
            self.context = prepare_confirmation_context(
                self.source, self.bars or None, cutoff, n_source=self.n_source,
                n_target_trend_confirmation_enabled=self.n_enabled, parsed_context=self.parsed_context,
                _market_index=self.market_index,
            )
        return self.context

    def continuation(self, point: Mapping[str, object], direction: str) -> Mapping[str, object] | None:
        known = _moment(point)
        cutoff = self.sessions.get(known, -1) if isinstance(known, str) else known
        if not 0 <= cast(int, point['index']) <= cutoff < len(self.bars):
            return None
        active = self.active
        if not self.n_enabled and active is not None and active.get('confirmation_rule') == N_TARGET_CONFIRMATION:
            return None
        if active is None or active['direction'] != direction or not _wave_holds(
                active, self.bars, point, _market_index=self.market_index):
            return None
        position = point.get(self.position_field)
        if (type(position) is not int or not 0 <= position < len(self.source)
                or not _same_origin(dict(origin=self.source[position]), point)):
            return None
        source_point = self.source[position]
        if (source_point.get('state') in ('seed', 'developing')
                or _later(_moment(source_point), _moment(point)) != _moment(point)):
            return None
        origin = cast(Mapping[str, object], active.get('wave_origin', active['origin']))
        return active if _known_leg_proof(active, point, direction, origin) else None

    def annotate(
        self, point: dict[str, object], previous: Mapping[str, object], key: Mapping[str, object] | None,
    ) -> None:
        direction = 'up' if previous['kind'] == 'L' and point['kind'] == 'H' else 'down'
        point['incoming_trend_state'] = 'unconfirmed'
        proof = point.get('incoming_trend_confirmation') if direction == 'up' else None
        if not self.n_enabled and isinstance(proof, Mapping) and proof.get('confirmation_rule') == N_TARGET_CONFIRMATION:
            proof = None
        origin: Mapping[str, object] = previous
        if direction == 'down':
            point.pop('incoming_trend_confirmation', None)
            position = origin.get(self.position_field)
            known = _moment(point)
            cutoff = self.sessions.get(known, -1) if isinstance(known, str) else known
            if (self.bars and previous['kind'] == 'H' and point['kind'] == 'L'
                    and type(position) is int and 0 <= position < len(self.source) and 0 <= cutoff < len(self.bars)
                    and all(origin.get(field, 0 if field == 'ordinal' else None)
                            == self.source[position].get(field, 0 if field == 'ordinal' else None)
                            for field in ('index', 'ordinal', 'kind', 'value'))):
                proof = qualify_downtrend(self.source, self.source[position], position, key, self.bars,
                                          cutoff, context=self.confirmation_context(cutoff), n_source=self.n_source,
                                          n_target_trend_confirmation_enabled=self.n_enabled)
        if not _known_leg_proof(proof, point, direction, origin):
            continued = self.continuation(point, direction)
            if continued is not None:
                proof = continued
                origin = cast(Mapping[str, object], continued.get('wave_origin', continued['origin']))
        if isinstance(proof, Mapping) and _known_leg_proof(proof, point, direction, origin):
            active, extreme = self.active, self.extreme
            if direction == 'down' and active is not None and active['direction'] == 'up' and extreme is not None:
                wave_position = extreme.get(self.position_field)
                if type(wave_position) is int and 0 <= wave_position < len(self.source):
                    wave_origin = self.source[wave_position]
                    if (not _same_origin(dict(origin=extreme), origin) and _same_origin(dict(origin=wave_origin), extreme)
                            and _later(_moment(wave_origin), _moment(proof)) == _moment(proof)
                            and _wave_holds(dict(proof, wave_origin=wave_origin), self.bars,
                                            cast(Mapping[str, object], proof['confirmed_by']),
                                            _market_index=self.market_index)):
                        proof = dict(proof, wave_origin={field: wave_origin[field] for field in
                                     ('index', 'ordinal', 'time', 'kind', 'value', 'available_at', 'label') if field in wave_origin})
            point['incoming_trend_confirmation'] = dict(proof)
            point['incoming_trend_state'] = 'confirmed'
            # A newly enabled N owns its source cycle; ordinary local proofs extend the active wave.
            same_wave = (active is not None and active['direction'] == direction
                         and _wave_holds(active, self.bars, point, _market_index=self.market_index)
                         and not (self.n_enabled and proof.get('confirmation_rule') == N_TARGET_CONFIRMATION
                                  and not _same_origin(active, origin)))
            if not same_wave:
                self.active = proof
                self.extreme = point
            elif extreme is not None and (cast(float, point['value']) > cast(float, extreme['value'])
                                          if direction == 'up' else cast(float, point['value']) < cast(float, extreme['value'])):
                self.extreme = point
        else:
            point.pop('incoming_trend_confirmation', None)
        if self.active is not None:
            point['active_trend_confirmation'] = dict(self.active)


def annotate_published_legs(
    points: Sequence[Mapping[str, object]], source: Sequence[Mapping[str, object]], bars: Sequence[Bar],
    *, source_level: int, qualified_source: Sequence[Mapping[str, object]] | None = None,
    n_target_trend_confirmation_enabled: bool = False,
) -> list[dict[str, object]]:
    """Certify incoming legs at their own publication cutoff, preserving the active wave."""
    result = [dict(point) for point in points]
    state = _PublishedTrendState(source, bars, source_level, qualified_source=qualified_source,
                                 n_target_trend_confirmation_enabled=n_target_trend_confirmation_enabled)
    last_low: Mapping[str, object] | None = None
    for offset in range(1, len(result)):
        previous = result[offset - 1]
        state.annotate(result[offset], previous, last_low)
        if previous['kind'] == 'L':
            last_low = previous
    return result


def _wave_holds(proof: Mapping[str, object], bars: Sequence[Bar], endpoint: Mapping[str, object],
                *, _market_index: _MarketPrefixIndex | None = None) -> bool:
    origin = cast(Mapping[str, object], proof.get('wave_origin', proof['origin']))
    start, end, value = cast(int, origin['index']), cast(int, endpoint['index']), cast(float, origin['value'])
    if not bars or not 0 <= start < end < len(bars):
        return False
    rising = proof['direction'] == 'up'
    if _market_index is not None:
        if not _market_index.matches(bars):
            raise ValueError('a market index must match its immutable Bar prefix')
        if _market_index.owner.exact_prices and _indexed_price(value):
            return _market_index.first_extreme(start, end + 1, value, high=not rising) is None
    prices = (bar.low if rising else bar.high for bar in bars[start:end + 1])
    return all(isfinite(price) and (price >= value if rising else price <= value) for price in prices)


def confirmed_trend_legs(
    points: Sequence[Mapping[str, object]], *, trend_level: int | None = None, source_path: str = '',
) -> list[dict[str, object]]:
    """An unqualified retracement cannot terminate or restart a higher wave.

    Retain published anchors as independent evidence, but consolidate same-
    direction extremes until the opposite direction earns its own proof.
    """
    legs: list[dict[str, object]] = []
    origins = _OriginLookup(points)
    for point in points:
        proof = point.get('active_trend_confirmation')
        if point.get('incoming_trend_state') != 'confirmed' or not isinstance(proof, Mapping):
            continue
        direction = proof['direction']
        if point['kind'] != ('H' if direction == 'up' else 'L'):
            continue
        reference = proof.get('wave_origin', proof.get('origin'))
        origin = origins.find(reference) if isinstance(reference, Mapping) else None
        if origin is None:
            continue
        leg = dict(points=[dict(origin), dict(point)], direction=direction, confirmation=dict(proof),
                   available_at=point['available_at'])
        wave_id = trend_wave_identity(proof, trend_level=trend_level, source_path=source_path) if trend_level is not None else None
        if wave_id is not None:
            leg['wave_id'] = wave_id
        if legs and legs[-1]['direction'] == direction and _same_origin(cast(Mapping[str, object], legs[-1]['confirmation']), origin):
            last_points = cast(list[Mapping[str, object]], legs[-1]['points'])
            previous = cast(float, last_points[-1]['value'])
            value = cast(float, point['value'])
            if (value > previous if direction == 'up' else value < previous):
                legs[-1] = leg
        else:
            legs.append(leg)
    return legs


def _candidate_tail(candidates: Sequence[Mapping[str, object]], start: int) -> Iterator[Mapping[str, object]]:
    if isinstance(candidates, FrozenPrefixList):
        yield from candidates.iter_range(start)
    elif type(candidates) in (list, tuple):
        for position in range(start, len(candidates)):
            yield candidates[position]
    else:
        yield from candidates[start:]


def publish_uptrends(
    candidates: Sequence[Mapping[str, object]],
    source: Sequence[Mapping[str, object]],
    bars: Sequence[Bar],
    *,
    source_level: int,
    qualified_source: Sequence[Mapping[str, object]] | None = None,
    n_target_trend_confirmation_enabled: bool = False,
    _market_index: _MarketPrefixIndex | None = None,
    _origin_lookups: tuple[_OriginLookup, _OriginLookup] | None = None,
    _publication_cache: _PublicationCache | None = None,
    _readonly_result: bool = False,
) -> list[dict[str, object]]:
    """Keep structural candidates private until their rising leg is qualified.

    A first confirmed descending H supplies the initial frozen pressure key.
    It grants no rising line. A rejected L cannot expose its following H as a
    completed rising wave. The first certificate cannot authorize a preceding
    peak; a still-active earlier certificate can preserve its own whole-wave
    peak regardless of a later local certificate. Qualify reverse candidates
    independently of permission for their subsequent rising leg.
    Rejected rising fragments retain their held whole-wave floor. A later
    legal local certificate grants permission without moving that price anchor
    to its higher B. The certificate keeps its own origin and dates; wave_origin
    identifies the independent earlier market extreme. Without bars, holding
    cannot be verified and the original local-origin behavior remains.
    """
    result: MutableSequence[dict[str, object]] = []
    pressure: Mapping[str, object] | None = None
    pending_origin: Mapping[str, object] | None = None
    pending_down_origin: Mapping[str, object] | None = None
    context: ConfirmationContext | None = None
    state = _PublishedTrendState(source, bars, source_level, qualified_source=qualified_source,
        n_target_trend_confirmation_enabled=n_target_trend_confirmation_enabled, _market_index=_market_index)
    last_low: Mapping[str, object] | None = None
    market = bars or None
    end_index = len(bars) - 1 if bars else max(
        (value for item in source for value in (item['index'],item.get('available_at')) if type(value) is int), default=-1)
    position_field = 'source_turn_position' if source_level == 0 else f'source_level{source_level}_position'
    n_source = state.n_source
    candidate_origins, source_origins = (_origin_lookups if _origin_lookups is not None else
                                         (_OriginLookup(candidates), _OriginLookup(source)))
    cache = _publication_cache if not n_target_trend_confirmation_enabled else None
    incremental = cache is not None and cache.admit(candidates, source, _market_index, source_level)
    saved = cache.checkpoint if incremental and cache is not None else None
    start = saved.offset if saved is not None else 0
    if incremental:
        result = saved.result.copy() if saved is not None else FrozenPrefixList()
        if saved is not None:
            pressure, pending_origin = saved.pressure, saved.pending_origin
            pending_down_origin, last_low = saved.pending_down_origin, saved.last_low
            state.active, state.extreme = saved.active, saved.extreme
    stable = incremental
    checkpoint = saved

    def capture(offset: int) -> _PublicationCheckpoint:
        assert isinstance(result, FrozenPrefixList)
        result.freeze(len(result))
        return _PublicationCheckpoint(offset, end_index, result.copy(), pressure, pending_origin,
                                     pending_down_origin, last_low, state.active, state.extreme)

    for offset, candidate in enumerate(_candidate_tail(candidates, start), start):
        # Capture before the next candidate so every continue follows the same
        # frontier rule; an unresolved predecessor blocks all later promotion.
        if stable and offset > start:
            checkpoint = capture(offset)
        if not n_target_trend_confirmation_enabled and is_n_target_reversal(candidate):
            continue
        point = dict(candidate)
        if not n_target_trend_confirmation_enabled:
            for field in ('trend_confirmation', 'incoming_trend_confirmation'):
                proof = point.get(field)
                if isinstance(proof, Mapping) and proof.get('confirmation_rule') == N_TARGET_CONFIRMATION:
                    point.pop(field)
            point.pop('n_target_confirmation', None)
            if point.get('trend_confirmation_route') == N_TARGET_CONFIRMATION:
                point.pop('trend_confirmation_route')
        if point['kind'] == 'H':
            upgrade = point.get('upgrade_evidence')
            if (isinstance(upgrade, Mapping) and upgrade.get('direction') == 'down'
                    and upgrade.get('role') == 'origin'):
                # 下降升级起点是攻击旧低点之前的真实整段高点。它可以低于
                # 上一个压力H，不能由“上涨创新高延伸”分支提前丢弃；先保存，
                # 等配对低点获得独立下降证书后再决定公开的整波起点。
                position = point.get(position_field)
                if (type(position) is not int or not 0 <= position < len(source)
                        or not _same_origin(dict(origin=source[position]), point)):
                    raise ValueError('a trend upgrade must identify its actual source origin')
                pending_down_origin = point
            # A causally confirmed descending pressure anchor is independent
            # of whether its preceding rising leg was qualified for display.
            pressure = candidate
            descending = point.get('n_target_confirmation')
            if n_target_trend_confirmation_enabled and isinstance(descending, Mapping):
                point['trend_confirmation'] = dict(descending)
                point['trend_confirmation_route'] = N_TARGET_CONFIRMATION
            if result and result[-1]['kind'] == 'H':
                continued = state.continuation(point, 'up')
                if (continued is not None and len(result) > 1
                        and cast(float, point['value']) > cast(float, result[-1]['value'])):
                    point['available_at'] = _later(_moment(point), _moment(result[-1]))
                    point['incoming_trend_confirmation'] = dict(continued)
                    state.annotate(point, result[-2], last_low)
                    result[-1] = point
                    pending_origin = None
                continue
            if result:
                proof = result[-1].get('trend_confirmation')
                if isinstance(proof, Mapping):
                    trigger = proof.get('confirmed_by')
                    if isinstance(trigger, Mapping):
                        trigger_index, point_index = trigger.get('index'), point.get('index')
                        if isinstance(trigger_index, int) and isinstance(point_index, int) and point_index < trigger_index:
                            continued = state.continuation(point, 'up')
                            if continued is None:
                                continue
                            proof = continued
                point['available_at'] = _later(_moment(point), _moment(result[-1]))
                if isinstance(proof, Mapping):
                    point['incoming_trend_confirmation'] = dict(proof)
                state.annotate(point, result[-1], last_low)
            result.append(point)
            pending_origin = None
            continue
        # 普通上涨原点与直接下降升级端点共用真实来源校验。升级描述本身
        # 不授予改写坐标/价格的权限，必须先核对本点，再走各方向资格门禁。
        position = point.get(position_field)
        if type(position) is not int or not 0 <= position < len(source):
            raise ValueError('a trend candidate must identify its actual source origin')
        origin = source[position]
        identity = ('index', 'ordinal', 'kind', 'value')
        if tuple(point.get(field, 0 if field == 'ordinal' else None) for field in identity) != tuple(
                origin.get(field, 0 if field == 'ordinal' else None) for field in identity):
            raise ValueError('a trend candidate must match its actual source origin')
        upgrade = point.get('upgrade_evidence')
        if (isinstance(upgrade, Mapping) and upgrade.get('direction') == 'down'
                and upgrade.get('role') == 'endpoint'):
            # 严格跌破已知本级低点是独立的下降升级许可，不必等随后反弹。
            # 普通L候选仍需后续上涨资格；只有本通道可在尚无上涨时发布下降末端。
            # 起点必须是已有真实H，且本级下降证书、原点/来源/可知日全由共同
            # annotate门禁重新验证，不能沿用下一级携来的许可证。
            if result:
                previous = result[-1] if result[-1]['kind'] == 'H' else result[-2] if len(result) >= 2 else None
                key = last_low
                paired_key: Mapping[str, object] | None = None
                reference, upgraded_key = upgrade.get('origin'), point.get('broken_key')
                # 同坐标H可能更早已有上涨或N证据，此时归并器保留早证书，
                # 不再把其顶层role改写为后来下降的origin。配对以真实坐标
                # 找回本级候选，下降知识仍由本次端点/旧低点自己的日期决定。
                paired = pending_down_origin
                if isinstance(reference, Mapping) and (paired is None
                        or not _same_origin(dict(origin=reference), paired)):
                    paired = candidate_origins.find(reference, kind='H')
                if (paired is not None and isinstance(reference, Mapping)
                        and _same_origin(dict(origin=reference), paired)
                        and isinstance(upgraded_key, Mapping)):
                    # 本级旧低点可已由共同定义确认，却因后续上涨尚未达标而未
                    # 进入公开上升链。只接受本次候选集合中真实已知的本级L，
                    # 不把任意下级低点或伪造的升级字段当作本级突破证据。
                    known_key = candidate_origins.find(upgraded_key, kind='L', known_point=upgraded_key)
                    source_key = source_origins.find(upgraded_key)
                    if (known_key is not None and source_key is not None
                            and _later(_moment(source_key), _moment(upgraded_key)) == _moment(upgraded_key)
                            and _later(_moment(paired), _moment(point)) == _moment(point)
                            and _later(_moment(upgraded_key), _moment(point)) == _moment(point)):
                        previous, key, paired_key = dict(paired), upgraded_key, known_key
                if previous is not None and previous['kind'] == 'H':
                    state.annotate(point, previous, key)
                    if point.get('incoming_trend_state') == 'confirmed':
                        # 同一个L可以先因跌破旧低成为下降末端，随后再独立
                        # 获得本级突破、下级完整交替或启用的N上涨证书。上行
                        # 用当前整体截止重新取证，下降仍按本端点自己的截止
                        # 取证；两份证书各自保留日期，不能让下降分支的continue
                        # 吞掉后来合法上涨，也不能把后来上涨借给此前下降。
                        outgoing = qualify_uptrend(source, source[position], position, pressure, market, end_index,
                            context=state.confirmation_context(end_index),
                            n_source=n_source, n_target_trend_confirmation_enabled=n_target_trend_confirmation_enabled)
                        if outgoing is None and incremental:
                            assert _market_index is not None
                            stable = stable and _PublicationCache.rejected_forever(source[position], _market_index)
                        if outgoing is not None:
                            point['trend_confirmation'] = outgoing
                            point['trend_confirmation_route'] = outgoing['confirmation_rule']
                        # 已公开压力H也属于历史事实，不能为展示新下降原点
                        # 删除它。需要接入另一个真实H时，把已定义的旧L作为
                        # 独立压力参考衔接，再加入配对H；二者从本次突破才公开，
                        # 不倒填到之前仍未获准的日期，不凭参考点授予上涨权限。
                        active = point.get('active_trend_confirmation')
                        if (result[-1]['kind'] == 'H' and isinstance(active, Mapping)
                                and _same_origin(active, previous)
                                and not _same_origin(active, result[-1])):
                            if (paired_key is None
                                    or (paired_key['index'], paired_key.get('ordinal', 0)) <=
                                       (result[-1]['index'], result[-1].get('ordinal', 0))
                                    or (paired_key['index'], paired_key.get('ordinal', 0)) >=
                                       (previous['index'], previous.get('ordinal', 0))):
                                continue
                            reference_low = dict(paired_key, available_at=_moment(point))
                            reference_low['incoming_trend_state'] = 'unconfirmed'
                            reference_low.pop('incoming_trend_confirmation', None)
                            reference_high = dict(previous, available_at=_moment(point))
                            reference_high['incoming_trend_state'] = 'unconfirmed'
                            reference_high.pop('incoming_trend_confirmation', None)
                            result.extend((reference_low, reference_high))
                        if result[-1]['kind'] == 'L':
                            if cast(float, point['value']) < cast(float, result[-1]['value']):
                                result[-1] = point
                        else:
                            result.append(point)
                        last_low = point
                        pending_origin = None
                        pending_down_origin = None
            continue
        if result and result[-1]['kind'] == 'L':
            continue
        key = pressure
        if context is None and end_index >= 0:
            context = state.confirmation_context(end_index)
        if context is not None and context.references[position] is not None:
            if (pending_origin is None or not _held_wave_floor(pending_origin, bars, cast(int, point['index']),
                                                            _market_index=_market_index)
                    or cast(float, point['value']) < cast(float, pending_origin['value'])):
                pending_origin = point
        confirmation = qualify_uptrend(source, origin, position, key, market, end_index,
                                       context=context, n_source=n_source,
                                       n_target_trend_confirmation_enabled=n_target_trend_confirmation_enabled)
        if confirmation is None:
            if incremental:
                assert _market_index is not None
                stable = stable and _PublicationCache.rejected_forever(origin, _market_index)
            if result and result[-1]['kind'] == 'H':
                state.annotate(point, result[-1], last_low)
            continue
        earliest = _later(_moment(point), _moment(confirmation))
        future_key = key
        for later_candidate in _candidate_tail(candidates, offset + 1):
            if not n_target_trend_confirmation_enabled and is_n_target_reversal(later_candidate):
                continue
            if later_candidate['kind'] == 'H':
                trigger = cast(Mapping[str, object], confirmation['confirmed_by'])
                if cast(int, later_candidate['index']) >= cast(int, trigger['index']):
                    break
                future_key = later_candidate
                continue
            if _later(_moment(later_candidate), earliest) != earliest:
                continue
            later_position = later_candidate.get(position_field)
            if type(later_position) is not int or not 0 <= later_position < len(source):
                raise ValueError('a trend candidate must identify its actual source origin')
            later_origin = source[later_position]
            if tuple(later_candidate.get(field, 0 if field == 'ordinal' else None) for field in identity) != tuple(
                    later_origin.get(field, 0 if field == 'ordinal' else None) for field in identity):
                raise ValueError('a trend candidate must match its actual source origin')
            local = qualify_uptrend(source, later_origin, later_position, future_key, market, end_index,
                                    context=context, n_source=n_source,
                                    n_target_trend_confirmation_enabled=n_target_trend_confirmation_enabled)
            if local is None:
                continue
            local_trigger = cast(Mapping[str, object], local['confirmed_by'])
            local_known = _later(_moment(later_candidate), _moment(local))
            if (_later(local_known, earliest) == earliest and local_known != earliest
                    and pending_origin is not None
                    and _held_wave_floor(pending_origin, bars, cast(int, local_trigger['index']),
                                         _market_index=_market_index)):
                point, confirmation, earliest = dict(later_candidate), local, local_known
        candidate_known = _moment(point)
        trigger = cast(Mapping[str, object], confirmation['confirmed_by'])
        if (pending_origin is not None and pending_origin is not point
                and _held_wave_floor(pending_origin, bars, cast(int, trigger['index']),
                                     _market_index=_market_index)):
            point = dict(pending_origin)
            wave_position = cast(int, point[position_field])
            confirmation = dict(confirmation, wave_origin={
                field: source[wave_position][field]
                for field in ('index', 'ordinal', 'time', 'kind', 'value', 'available_at', 'label')
                if field in source[wave_position]
            })
        point['available_at'] = _later(_later(_moment(point), candidate_known), _moment(confirmation))
        if result:
            point['available_at'] = _later(_moment(point), _moment(result[-1]))
        point['structural_confirmation_rule'] = point['confirmation_rule']
        point['confirmation_rule'] = confirmation['confirmation_rule']
        point['trend_confirmation'] = confirmation
        point['trend_confirmation_route'] = (
            N_TARGET_CONFIRMATION if confirmation['confirmation_rule'] == N_TARGET_CONFIRMATION else
            'lower_level_break_alternation_turn' if confirmation.get('alternation_low') is not None else 'same_level_key_break'
        )
        if result:
            state.annotate(point, result[-1], last_low)
        result.append(point)
        last_low = point
        pending_origin = None
    if incremental:
        assert cache is not None
        if stable and len(candidates) > start:
            checkpoint = capture(len(candidates))
        cache.save(checkpoint)
        # 层级投影只读五个标量字段，立即产生独立轻量结果；无需复制旧证明。
        # 完整许可标注和公共调用仍独立复制，不能修改缓存或旧日期的证据。
        return list(result) if _readonly_result else deepcopy(list(result))
    return list(result)
