"""Build V2 entry context from confirmed level 1/2/3 structures.

Drawing vertices have a price date AND an observation date. Each prefix is
reduced independently; an endpoint seen only later is never traded earlier.
The first alternation needs a higher low, not a 2/3 retracement threshold.
"""
from collections.abc import Mapping, Sequence
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import dataclass, asdict
from typing import TypedDict, cast

from ..market_structure.bottom_n_targets import DeclineStart
from ..market_structure.lecture_drawing import lecture_drawing
from ..market_structure.trend_judgement import judge_trend, mutable_points
from ..market_structure.trend_lines import polyline_turns, qualify_trend_structure
from ..market_structure.trend_structure import StructuralTrend, classify_trend_prices
from ..market_structure.trend_staging import TrendStageRows
from ..models.model import Bar


class _HistoryPoint(TypedDict):
    index: int
    ordinal: int
    kind: str
    value: float
    available_at: int
    label: str


def _identity(point: Mapping[str, object]) -> tuple[int, int, str, float]:
    return (cast(int, point['index']), cast(int, point['ordinal']),
            cast(str, point['kind']), cast(float, point['value']))


@dataclass
class _TrendHistoryFrame:
    """一日的直接下级来源；每完成一个全日期阶段才整体进入下一级。

    中间来源不公开，判断和资格函数只读它，完成下一层后即可释放下级数组。
    临时阶段逐行保存本级来源；公开history仍只保留原有五个点字段。
    """

    index: int
    epoch: int
    source: Sequence[Mapping[str, object]]
    qualified_source: Sequence[Mapping[str, object]]


def _history_frame(value: object) -> _TrendHistoryFrame:
    """恢复本请求自己保存的阶段行，不接受其他对象作为下一层来源。"""
    if not isinstance(value, _TrendHistoryFrame):
        raise ValueError('trend history stage requires a history frame')
    return value


def hierarchical_history(
    bars: Sequence[Bar], *, prefix_cache=None,
    decline_sink: dict[int, tuple[DeclineStart, ...]] | None = None,
    structure_sink: dict[int, dict[int, tuple[dict[str, object], ...]]] | None = None,
    n_target_trend_confirmation_enabled: bool = False,
):
    """先完成全部折线历史，再按一级、二级、三级顺序完成趋势历史。

    外层循环是级别，内层才是交易日：全部一级计算完毕才开始二级，
    全部二级计算完毕才开始三级。唯一判别器是judge_trend：一级输入折线，二级输入一级结构高低点，
    三级输入二级结构高低点。资格点、N证书和显示尾端均不代替结构来源。
    structure_sink暴露这份几何历史；原返回history仍只包含获本级独立
    策略许可的点，避免让绘图资格自动授予交易权限。
    发生坐标与可知索引分别冻结：后来重新出现或修改的点，从重新可知的当天
    才进入策略，不倒填到其价格日。缓存版本覆盖归并和升级规则，旧检查点
    不可在新规则下继续追加；部分日线重算仍以同一前缀规则验证。
    """
    if type(n_target_trend_confirmation_enabled) is not bool:
        raise ValueError('n_target_trend_confirmation_enabled must be a boolean')
    history = {}; epochs = {}
    structural_history: dict[int, dict[int, tuple[dict[str, object], ...]]] = {}
    decline_history: dict[int, tuple[DeclineStart, ...]] = {}
    if decline_sink is not None:
        decline_sink.clear()
    if structure_sink is not None:
        structure_sink.clear()
    previous: dict[int, dict[tuple[int, int, str, float], int]] = {0: {}, 1: {}, 2: {}, 3: {}}
    last_epoch = None
    last = len(bars) - 1
    cache_version = 'level_first_daily_point_geometry_and_independent_permissions_v114'
    wants_declines = decline_sink is not None
    wants_structure = structure_sink is not None
    prefix_key = (cache_version, tuple(bars[:-1]), wants_declines, n_target_trend_confirmation_enabled, wants_structure)
    cached_key = prefix_cache.get('key') if prefix_cache is not None else None
    resume_start = (last if cached_key == prefix_key else
                    last - 1 if cached_key == (cache_version, tuple(bars[:-2]), wants_declines,
                                               n_target_trend_confirmation_enabled, wants_structure) else 0)
    if resume_start:
        saved_history, saved_epochs, saved_previous, last_epoch, saved_declines, saved_structure = prefix_cache['checkpoint']
        # 公开历史／sink允许调用方编辑；缓存快照必须独立拥有嵌套点字典，
        # 不能让一次读取后的修改倒写旧日期，造成warm与fresh计算不同。
        history, epochs, previous = (
            deepcopy(saved_history), saved_epochs.copy(),
            {level: known.copy() for level, known in saved_previous.items()})
        decline_history = saved_declines.copy()
        structural_history = deepcopy(saved_structure)
        if decline_sink is not None:
            decline_sink.update(decline_history)
        if structure_sink is not None:
            structure_sink.update(structural_history)

    initial_epoch = last_epoch
    checkpoint_previous: dict[int, dict[tuple[int, int, str, float], int]] = {}
    # 相邻日期的已确认点大多相同，内部只读点可共享；不为每个日期深复制一份完整证据树。
    prior_points: dict[int, dict[tuple[int, int, str, float], _HistoryPoint]] = {0: {}, 1: {}, 2: {}, 3: {}}

    def freeze(points: Sequence[Mapping[str, object]], level: int, now_index: int) -> list[_HistoryPoint]:
        frozen: list[_HistoryPoint] = []
        now: dict[tuple[int, int, str, float], int] = {}
        reusable: dict[tuple[int, int, str, float], _HistoryPoint] = {}
        for point in points:
            key = _identity(point)
            # 重现或修改的点从本次观察才可知，不能借完整历史倒填到价格日期。
            known = max(previous[level].get(key, now_index), cast(int, point.get('available_index', 0)))
            if frozen:
                known = max(known, frozen[-1]['available_at'])
            current = cast(_HistoryPoint, dict(point, available_at=known, label=point.get('label', point['kind'])))
            prior = prior_points[level].get(key)
            if prior is not None and prior == current:
                current = prior
            now[key] = known
            reusable[key] = current
            frozen.append(current)
        previous[level] = now
        prior_points[level] = reusable
        return frozen

    # 正式返回历史只有五字段，但中间来源还包含完整结构和资格证据。
    # 每级按日期逐行落到有界临时存储，避免同时持有所有日期的完整证据树；
    # 三个阶段依然全部完成后才返回，成功及任何异常都由同一ExitStack关闭。
    with ExitStack() as staging:
        frames = staging.enter_context(TrendStageRows(_history_frame))
        def collect(i: int, epoch: int, raw: Sequence[Mapping[str, object]]) -> None:
            nonlocal last_epoch
            if i < resume_start:
                return
            if epoch != last_epoch:
                previous[0] = {}
                prior_points[0] = {}
            last_epoch = epoch; epochs[i] = epoch
            # on_step只保存当时真实来源。绘图全部完成之前绝不开始任何一级判断。
            turns = freeze(polyline_turns(raw, source_path=f'lecture-{epoch}'), 0, i)
            frames.write(_TrendHistoryFrame(i, epoch, turns, turns))
            history[i] = {}
            if wants_structure:
                structural_history[i] = {}
            if prefix_cache is not None and i == last - 1:
                checkpoint_previous[0] = previous[0].copy()

        lecture_drawing(bars, on_step=collect)
        frame_count = frames.row_count
        for level in (1, 2, 3):
            output = staging.enter_context(TrendStageRows(_history_frame)) if level < 3 else None
            stage_epoch = initial_epoch
            for frame in frames.iter_rows():
                i, source, qualified_source = frame.index, frame.source, frame.qualified_source
                if frame.epoch != stage_epoch:
                    previous[level] = {}
                    prior_points[level] = {}
                stage_epoch = frame.epoch
                judgement = judge_trend(source, trend_level=level, asof=i)
                # 首次出现/重新出现的几何点按本次观察冻结知识日，策略证书不参与此步。
                geometry = freeze([dict(p, available_index=p['available_at'])
                                   for p in mutable_points(judgement.points)], level, i)
                candidate_source = mutable_points(judgement.candidates)
                if level == 1 and decline_sink is not None:
                    # 普通下降定义与严格低点突破是独立证据。同一个H可先作为
                    # 上涨升级端点而可知，后来才被已确认L的严格破位证明为
                    # 下降起点；不能改写H的旧方向，也不能因旧方向为up漏收新事实。
                    # 下降测幅通道只用这份证据自己的可知日，不倒填到H价格日。
                    declines: dict[int, int] = {}
                    for point in candidate_source:
                        if point['kind'] == 'H' and point.get('wave_direction_after') == 'down':
                            index, known = cast(int, point['index']), cast(int, point['available_at'])
                            # 同一高点可被普通下降与后来严格破低分别证明。两份事实
                            # 都保留，测幅起点采用最早真实下降知日，不能被晚证据覆盖。
                            declines[index] = min(declines.get(index, known), known)
                    for point in candidate_source:
                        upgrade = point.get('upgrade_evidence')
                        if (point['kind'] != 'L' or not isinstance(upgrade, Mapping)
                                or upgrade.get('direction') != 'down' or upgrade.get('role') != 'endpoint'):
                            continue
                        origin = upgrade.get('origin')
                        if (not isinstance(origin, Mapping) or origin.get('kind') != 'H'
                                or type(origin.get('index')) is not int or type(origin.get('available_at')) is not int):
                            continue
                        index = cast(int, origin['index'])
                        known = max(cast(int, point['available_at']), cast(int, origin['available_at']))
                        declines[index] = min(declines.get(index, known), known)
                    decline_history[i] = tuple(DeclineStart(index, declines[index]) for index in sorted(declines))
                    decline_sink[i] = decline_history[i]
                published = qualify_trend_structure(candidate_source, source, bars[:i+1], trend_level=level,
                    qualified_source=qualified_source,
                    n_target_trend_confirmation_enabled=n_target_trend_confirmation_enabled)
                history[i][level] = tuple(dict(index=p['index'], ordinal=p['ordinal'], kind=p['kind'],
                                          value=p['value'], available_at=p['available_at']) for p in published)
                if wants_structure:
                    structural_history[i][level] = tuple(dict(index=p['index'], ordinal=p['ordinal'], kind=p['kind'],
                        value=p['value'], available_at=p['available_at']) for p in geometry)
                # 同一函数的几何输出才是下一层输入。获证书的子集只给下一层资格核验。
                frame.source = geometry
                frame.qualified_source = published
                if output is not None:
                    output.write(frame)
                if prefix_cache is not None and i == last - 1:
                    checkpoint_previous[level] = previous[level].copy()

            # 前级完整日期已经计算完毕，才封存下级输入；旧阶段此时即可释放。
            frames.close()
            if output is not None:
                frames = output

    if structure_sink is not None:
        structure_sink.update(structural_history)
    if prefix_cache is not None and frame_count and resume_start != last and last - 1 in epochs:
        # 阶段完成后才保存完整检查点；最后一根未完K线不能污染昨日三个级别的状态。
        prefix_cache['key'] = prefix_key
        prefix_cache['checkpoint'] = (
            deepcopy({i: value for i, value in history.items() if i < last}),
            {i: value for i, value in epochs.items() if i < last},
            checkpoint_previous, epochs[last-1],
            {i: value for i, value in decline_history.items() if i < last},
            deepcopy({i: value for i, value in structural_history.items() if i < last}))
    return history, epochs


@dataclass(frozen=True)
class EntryContext:
    trend_level: int
    epoch: int
    context_index: int
    key_source_index: int
    key_price: float
    flip_index: int
    flip_high_index: int
    flip_high_price: float
    origin_index: int
    origin_price: float
    alternation_index: int
    alternation_low_index: int
    alternation_low_price: float
    maturity_index: int | None = None
    confirmation_attack: int | None = None

    @property
    def episode(self):
        return self.trend_level, self.epoch, self.context_index, self.flip_index, self.alternation_index


class _LevelState:
    """Causal state machine for one reduced trend level and one chart epoch.

    ``anchor`` is the active directional extreme and ``key`` is the confirmed
    opposite pivot immediately before that extreme. During a bearish episode the
    key is therefore 末跌高; a later high must strictly exceed it before a
    bullish flip can exist. Equality is only a retest.

    The state is scoped to an epoch because an ambiguous parent/child-bar path
    starts a new independent geometry. Carrying keys across that boundary would
    join structures that the lecture reducer intentionally keeps separate.
    """

    def __init__(self, level, epoch, *, whole_wave=False):
        self.level = level; self.epoch = epoch
        self.whole_wave = whole_wave
        self.points = []; self.direction = None; self.anchor = None; self.key = None
        self.flip = None; self.context = None

    def point(self, p, now):
        """Consume one newly confirmed point without reading future points.

        State progression is deliberately one-way within an episode:
        establish direction -> freeze key -> break key -> confirm alternation.
        A new directional extreme may replace the anchor/key before the break;
        intermediate rebounds after the anchor never lower the breakout key.
        """

        previous = self.points[-1] if self.points else None
        self.points.append(p)
        highs = [q for q in self.points if q['kind'] == 'H']
        lows = [q for q in self.points if q['kind'] == 'L']
        if self.direction is None:
            # Direction needs two highs and two lows moving together. Mixed or
            # equal comparisons remain unknown instead of being guessed.
            if len(highs) < 2 or len(lows) < 2:
                return
            trend = classify_trend_prices([q['value'] for q in highs[-2:]],
                                           [q['value'] for q in lows[-2:]],trend_level=self.level)
            self.direction = 'up' if trend == StructuralTrend.BULL else 'down' if trend == StructuralTrend.BEAR else None
            if self.direction is None:
                return
            self.anchor = (max if self.direction == 'up' else min)(
                highs if self.direction == 'up' else lows, key=lambda q:q['value'])
            pos = self.points.index(self.anchor)
            self.key = self.points[pos-1] if pos else None
            return
        if self.key is None:
            # The first same-direction extreme gains its key only when an
            # opposite confirmed pivot exists immediately to its left.
            if previous and p['kind'] == ('H' if self.direction == 'up' else 'L'):
                self.anchor, self.key = p, previous
            return
        assert self.anchor is not None
        if self.direction == 'up':
            if p['kind'] == 'H' and p['value'] > self.anchor['value']:
                self.anchor, self.key = p, previous
            elif p['kind'] == 'L' and p['value'] < self.key['value']:
                self.direction = 'down'; self.anchor, self.key = p, previous
                self.context = self.flip = None
            return
        if self.flip is None:
            if p['kind'] == 'L' and p['value'] < self.anchor['value']:
                # A new bearish extreme re-anchors 末跌高 to the preceding H.
                self.anchor, self.key = p, previous
            elif p['kind'] == 'H' and p['value'] > self.key['value'] and previous:
                # Strictly greater is required: touching 末跌高 does not prove
                # that the bearish structure has been broken.
                self.flip = dict(high=p, origin=previous, known=now, anchor=self.anchor, key=self.key)
            return
        f = self.flip
        if p['kind'] == 'L':
            # The first confirmed low after the key break supplies alternation.
            # Legacy and whole-wave profiles intentionally use different origin
            # boundaries, so the equality rule remains explicit here.
            origin = f['anchor'] if self.whole_wave else f['origin']
            valid_low = origin['value'] <= p['value'] if self.whole_wave else origin['value'] < p['value']
            if valid_low and p['value'] < f['high']['value']:
                self.context = EntryContext(self.level, self.epoch, f['anchor']['index'],
                    f['key']['index'], f['key']['value'], f['known'],
                    f['high']['index'], f['high']['value'], origin['index'], origin['value'],
                    now, p['index'], p['value'])
                self.direction = 'up'; self.anchor, self.key = f['high'], p
                self.flip = None
            else:
                self.flip = None
                if p['value'] <= self.anchor['value']:
                    self.anchor, self.key = p, previous
        elif p['value'] > f['high']['value']:
            self.flip = dict(f, high=p)


def context_history(bars, history, epochs, *, whole_wave=False):
    """All >=level-1 contexts are OR alternatives, never three mandatory gates."""
    from dataclasses import replace
    result = {}; events = []; states = {}; prior_epoch = None
    for i, bar in enumerate(bars):
        if epochs[i] != prior_epoch:
            states = {level: _LevelState(level, epochs[i], whole_wave=whole_wave) for level in (1, 2, 3)}
        prior_epoch = epochs[i]; available = []
        for level, state in states.items():
            previous_context = state.context
            points = history[i][level]
            common = 0
            for old, new in zip(state.points, points):
                if old != new:
                    break
                common += 1
            if common < len(state.points):
                # Revisions invalidate the old episode; rebuild at current knowledge time.
                state = states[level] = _LevelState(level, epochs[i], whole_wave=whole_wave); common = 0
            before = state.context
            for p in points[common:]:
                state.point(p, p['available_at'] if whole_wave else i)
            ctx = state.context
            if ctx is not None:
                if whole_wave and previous_context is not None and previous_context.episode == ctx.episode:
                    # Revising a later pivot cannot erase a maturity close that
                    # was already observed for this unchanged flip/alternation.
                    ctx = state.context = replace(ctx,maturity_index=previous_context.maturity_index)
                # Full retracement/structural stop is distinct from an amplitude filter.
                invalid = (min(b.low for b in bars[ctx.origin_index:i+1]) < ctx.origin_price if whole_wave else
                           bar.low <= ctx.origin_price or bar.close < ctx.alternation_low_price)
                if invalid:
                    state.context = None
                    events.append(dict(bar_index=i, event='hierarchy_context_invalidated', trend_level=level))
                    continue
                if before is None or before.episode != ctx.episode:
                    events.append(dict(bar_index=i, event='hierarchy_alternation_ready', **asdict(ctx)))
                # No retroactive maturity: a later daily close must re-confirm the flip high.
                if ctx.maturity_index is None and i > ctx.alternation_index and bar.close > ctx.flip_high_price:
                    ctx = state.context = replace(ctx, maturity_index=i)
                    events.append(dict(bar_index=i, event='hierarchy_bull_matured', **asdict(ctx)))
                available.append(ctx)
        result[i] = tuple(available)
    return result, events


def select_entry(current, at_attack, *, attack, origin_index, high_index, low_index, ratio, shallow_ratio, asof=None):
    """Classify from attack-time knowledge; later evidence cannot rescue an old N.

Type 2 uses the new N's actual upward leg and pullback. Its origin must be at
or after the alternation low, its high at/after maturity, and pullback later.
Once mature, this level cannot fall back to the unfiltered type 1 route.
"""
    live = {c.episode:c for c in current}; eligible = []; reasons = []
    asof = attack if asof is None else asof
    for ctx in at_attack:
        if ctx.episode not in live or not ctx.flip_index <= ctx.alternation_index < attack:
            continue
        if ctx.maturity_index is None or ctx.maturity_index >= attack:
            if ctx.trend_level not in (2, 3):
                reasons.append('first_buy_requires_level_two_or_three'); continue
            # An early N cannot retain the unfiltered route indefinitely after
            # the live context matures. Same-close maturation is the boundary;
            # from the next bar onward a NEW post-maturity pullback is required.
            mature_now = live[ctx.episode].maturity_index
            if mature_now is not None and mature_now < asof:
                reasons.append('early_n_expired_after_maturity'); continue
            eligible.append(dict(asdict(ctx), buy_point_type='transition_squeeze', priority=1,
                                 counter_filter_applied=False, counter_ratio=ratio))
        else:
            if not (origin_index >= ctx.alternation_low_index and high_index >= ctx.maturity_index
                    and low_index > high_index and low_index > ctx.maturity_index):
                reasons.append('mature_pullback_sequence_not_ready'); continue
            if not 0 <= ratio < shallow_ratio:
                reasons.append('mature_pullback_not_shallow'); continue
            eligible.append(dict(asdict(ctx), buy_point_type='mature_shallow_squeeze', priority=2,
                                 counter_filter_applied=True, counter_ratio=ratio,
                                 counter_limit=shallow_ratio, pullback_index=low_index,
                                 impulse_origin_index=origin_index, impulse_high_index=high_index))
    if eligible:
        return max(eligible, key=lambda e:(e['priority'], e['trend_level'])), ''
    return None, (reasons[0] if reasons else 'hierarchy_transition_not_ready')
