"""已多翻空后的短暂突破放量收回；只关闭资格，不制造六态或未来拐点。"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from math import isfinite
from typing import TYPE_CHECKING, cast

from ..models.model import Bar

if TYPE_CHECKING:
    from .trend_lifecycle import _Fact, _Pivot


FAILED_BREAKOUT_EVENT = 'trend_bear_failed_breakout'
FAILED_BREAKOUT_RULE = 'bear_flip_short_breakout_massive_bearish_close_rejection'
VOLUME_WINDOW = 20
VOLUME_MULTIPLE = 2.0
BEARISH_BODY_MIN = .03
STABLE_CLOSE_SESSIONS = 3


def prepare_failed_breakout(facts: Sequence['_Fact'], points: Sequence['_Pivot']) -> '_Fact | None':
    """最近正式翻空及其原高、破位低必须仍属于同一当前来源。"""
    seed = next((fact for fact in reversed(facts) if fact.title == '翻多为空'), None)
    if seed is None or seed.origin is None or seed.broken is None:
        return None
    origin, low, broken = seed.origin, seed.point, seed.broken
    if (origin.kind != 'H' or low.kind != 'L' or broken.kind != 'L'
            or origin not in points or low not in points or broken not in points
            or not broken.index < origin.index < low.index <= seed.known
            or not low.price < broken.price < origin.price):
        return None
    return seed


@dataclass(frozen=True, slots=True)
class FailedBreakoutState:
    """逐日追加价格计数；不可变状态可安全复制，检查点不保存行情或可执行对象。"""

    seed: '_Fact'
    last: int
    attack: int | None = None
    volume_mean: float = 0.0
    consecutive: int = 0
    longest: int = 0
    finished: bool = False

    def advance(self, bars: Sequence[Bar], now: int, level: int, path: str,
                ) -> tuple['FailedBreakoutState', dict[str, object] | None]:
        state = self
        origin = state.seed.origin
        assert origin is not None
        anchor = bars[origin.index]
        # 来源价格必须与当前同复权行情一致，不能混用另一快照的关键位。
        if anchor.high != origin.price:
            return replace(state, last=now, finished=True), None
        event = None
        for index in range(state.last + 1, now + 1):
            if state.finished:
                break
            bar = bars[index]
            consecutive = state.consecutive + 1 if bar.close > anchor.close else 0
            state = replace(state, consecutive=consecutive, longest=max(state.longest, consecutive))
            if state.longest >= STABLE_CLOSE_SESSIONS:
                state = replace(state, finished=True)
                break
            if state.attack is None:
                if bar.high <= origin.price:
                    continue
                if index < VOLUME_WINDOW:
                    return replace(state, last=now, finished=True), None
                volumes = [item.volume for item in bars[index - VOLUME_WINDOW:index]]
                if any(not isfinite(value) or value < 0 for value in volumes):
                    return replace(state, last=now, finished=True), None
                volume_mean = sum(volumes) / VOLUME_WINDOW
                if not isfinite(volume_mean):
                    return replace(state, last=now, finished=True), None
                state = replace(state, attack=index, volume_mean=volume_mean)
            if bar.close >= anchor.close:
                continue
            # 一次突破第一次收回即结束该次观察；普通回落不能留待很久后的放量补判。
            qualified = (state.volume_mean > 0 and isfinite(bar.volume)
                and bar.volume >= VOLUME_MULTIPLE * state.volume_mean
                and bar.open > 0 and (bar.open - bar.close) / bar.open >= BEARISH_BODY_MIN
                and bars[index - 1].close >= anchor.close)
            if qualified:
                state = replace(state, finished=True)
                if index == now:
                    event = dict(event=FAILED_BREAKOUT_EVENT, confirmation_rule=FAILED_BREAKOUT_RULE,
                        bar_index=now, available_index=now, trend_level=level, source_path=path,
                        direction='down', flip_confirmed=True,
                        prior_flip_origin_index=origin.index, prior_flip_index=state.seed.point.index,
                        prior_flip_known_index=state.seed.known, origin_index=origin.index,
                        origin_known_index=origin.known, flip_index=origin.index,
                        flip_known_index=origin.known, breakout_level=anchor.close,
                        anchor_high=origin.price, attack_index=state.attack,
                        attack_high=bars[cast(int, state.attack)].high,
                        previous_close=bars[index - 1].close, confirmation_close=bar.close,
                        observed_open=bar.open, observed_volume=bar.volume,
                        volume_baseline_end=cast(int, state.attack) - 1,
                        volume_window=VOLUME_WINDOW, volume_mean=state.volume_mean,
                        volume_min_multiple=VOLUME_MULTIPLE, bearish_body_min=BEARISH_BODY_MIN,
                        longest_held_sessions=state.longest, stable_close_sessions=STABLE_CLOSE_SESSIONS,
                        stability_basis='last_rise_high_close')
                break
            state = replace(state, attack=None, volume_mean=0.0, consecutive=0, longest=0)
        return replace(state, last=now), event


def confirmed_failed_breakout(event: Mapping[str, object], now: int) -> bool:
    """跨级路由只接收同日、完整且符合量价边界的证明。"""
    if (event.get('event') != FAILED_BREAKOUT_EVENT or event.get('confirmation_rule') != FAILED_BREAKOUT_RULE
            or event.get('bar_index') != now or event.get('available_index') != now
            or event.get('direction') != 'down' or event.get('flip_confirmed') is not True):
        return False
    names = ('origin_index', 'origin_known_index', 'prior_flip_index', 'prior_flip_known_index',
             'attack_index', 'volume_baseline_end', 'longest_held_sessions')
    indices = [event.get(name) for name in names]
    if any(type(value) is not int for value in indices):
        return False
    origin, known, flip, flip_known, attack, baseline_end, longest = cast(list[int], indices)
    if (not 0 <= origin <= known <= flip_known < attack <= now or not origin < flip <= flip_known
            or attack < VOLUME_WINDOW or baseline_end != attack - 1
            or not 0 <= longest < STABLE_CLOSE_SESSIONS
            or event.get('prior_flip_origin_index') != origin or event.get('flip_index') != origin
            or event.get('flip_known_index') != known
            or event.get('volume_window') != VOLUME_WINDOW
            or event.get('volume_min_multiple') != VOLUME_MULTIPLE
            or event.get('bearish_body_min') != BEARISH_BODY_MIN
            or event.get('stable_close_sessions') != STABLE_CLOSE_SESSIONS
            or event.get('stability_basis') != 'last_rise_high_close'):
        return False
    numbers = [event.get(name) for name in ('breakout_level', 'anchor_high', 'attack_high', 'previous_close',
        'confirmation_close', 'observed_open', 'observed_volume', 'volume_mean')]
    if any(type(value) not in (int, float) or not isfinite(cast(float, value)) for value in numbers):
        return False
    threshold, high, attack_high, previous, close, opening, volume, mean = cast(list[float], numbers)
    return (0 < close < threshold <= previous and threshold <= high < attack_high and opening > 0 and mean > 0
            and volume >= VOLUME_MULTIPLE * mean and (opening - close) / opening >= BEARISH_BODY_MIN)
