"""已知前高突破当笔／次笔的放量阴线上影风险。"""

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from math import isfinite

from ..market_state.volume_candle import is_bearish_inside_child, is_volume_bearish
from ..market_structure.observation_window import observation_window_end
from ..models.model import Bar


BREAKOUT_SHADOW_REDUCE = 'breakout_volume_bearish_shadow_reduce_90'
BREAKOUT_SHADOW_CLEAR = 'breakout_volume_bearish_shadow_next_lower_open_clear'
BREAKOUT_GAP_BEARISH_CLEAR = 'breakout_gap_volume_large_bearish_clear'
BREAKOUT_CHILD_CLEAR = 'breakout_volume_bearish_inside_child_clear'


@dataclass(frozen=True)
class _High:
    level: int
    index: int
    price: float
    known: int


def _latest_highs(levels: Mapping[int, Sequence[Mapping[str, object]]], index: int) -> list[_High]:
    result = []
    for level in (0, 1, 2, 3):
        for point in reversed(levels.get(level, ())):
            source, known, price = point.get('index'), point.get('available_at'), point.get('value')
            if (point.get('kind') != 'H' or point.get('state') in ('seed', 'developing')
                    or point.get('display_only') is True or type(source) is not int or type(known) is not int
                    or not 0 <= source <= known < index or not isinstance(price, (int, float))
                    or isinstance(price, bool) or not isfinite(price) or price <= 0):
                continue
            result.append(_High(level, source, float(price), known))
            break
    return result


def breakout_shadow_days(
    bars: Sequence[Bar], history: Mapping[int, Mapping[int, Sequence[Mapping[str, object]]]],
    *, start_index: int = 0, end_index: int | None = None,
) -> Iterator[tuple[int, dict[int, dict[str, object]]]]:
    """收盘确认减九成；下一交易日仅用开盘价决定清仓，不延长观察期。

    历史只提供前一日已知的折线／三级前高，不自行从未来K线确认高点。
    每次突破只保留当笔与次笔，跳空放量大阴线或放量阴线子线在当日收盘全清；
    其余长上影仍先减九成。已发布警告不因随后结构重命名撤销。
    """
    end = observation_window_end(len(bars), start_index, end_index)
    risks: dict[int, dict[str, object]] = {}
    candidate: tuple[int, _High] | None = None
    # 当笔上影减仓不能取消次笔阴线子线的独立全清观察。
    child_candidate: tuple[int, _High] | None = None
    warning: tuple[int, dict[str, object]] | None = None
    bearish_index = next((i for i in range(start_index - 1, -1, -1)
                          if bars[i].close < bars[i].open), None)
    for index in range(start_index, end + 1):
        yield index, risks
        bar = bars[index]
        previous = bars[index - 1] if index else None
        bearish = bars[bearish_index] if bearish_index is not None else None
        volume_bearish = is_volume_bearish(bar, previous, bearish)
        if bar.close < bar.open:
            bearish_index = index
        if previous is None:
            continue
        if warning is not None:
            warned, proof = warning
            warning = None
            if index == warned + 1 and bar.open < bars[warned].close:
                # 仅携带昨日完整证据和今日开盘，不能附加今日收盘／成交量。
                clear = {key: value for key, value in proof.items()
                         if key not in ('exit_target_fraction', 'observed_open', 'observed_high',
                                        'observed_low', 'observed_close', 'observed_volume')}
                clear.update(reason=BREAKOUT_SHADOW_CLEAR, exit_fraction=1.0,
                             execution_model='next_open', observed_open=bar.open,
                             previous_close=bars[warned].close,
                             decision_timestamp=bar.timestamp.replace(hour=9, minute=30).isoformat())
                risks[index] = clear
                candidate = None
                continue
        levels = history.get(index - 1, {})
        if candidate is not None:
            attack, _ = candidate
            # 次笔沿用突破时已知的前高；后续新高不能改写当笔突破事实。
            # 历史来源更正由上层流回放，而不是用今天的新标签重写昨天。
            if index > attack + 1:
                candidate = None
        if child_candidate is not None and index > child_candidate[0] + 1:
            child_candidate = None
        # 收盘或最高严格越过末跌高；合法 OHLC 中 high >= close，最高价比较覆盖两种情况。
        crossed = [high for high in _latest_highs(levels, index)
                   if previous.high <= high.price < bar.high]
        if crossed:
            candidate = index, max(crossed, key=lambda high: (high.price, high.index, high.level))
            child_candidate = candidate
        child_clear = (child_candidate is not None and volume_bearish
                       and is_bearish_inside_child(previous, bar))
        if not volume_bearish or candidate is None and not child_clear:
            continue
        high_price, opened, closed = (Fraction(str(value)) for value in (bar.high, bar.open, bar.close))
        upper, body = high_price - max(opened, closed), abs(opened - closed)
        # 突破次笔也可能向下跳空；开盘严格离开昨日价格区间即可，不要求此前连板。
        gap_direction = ('up' if bar.open > previous.high else
                         'down' if bar.open < previous.low else None)
        gap_clear = (gap_direction is not None and body >= opened / 20
                     and body * 2 >= high_price - Fraction(str(bar.low)))
        if not gap_clear and not child_clear and upper <= body:
            continue
        episode = child_candidate if child_clear else candidate
        assert episode is not None
        attack, high = episode
        proof: dict[str, object] = dict(reason=BREAKOUT_SHADOW_REDUCE, exit_fraction=.9, exit_target_fraction=.9,
                     execution_model='same_day_close', trend_level=high.level,
                     breakout_key_index=high.index, breakout_key_date=bars[high.index].timestamp.date().isoformat(),
                     breakout_key_high=high.price, breakout_key_known_index=high.known,
                     breakout_attack_index=attack, breakout_attack_date=bars[attack].timestamp.date().isoformat(),
                     breakout_warning_index=index, breakout_warning_date=bar.timestamp.date().isoformat(),
                     breakout_warning_close=bar.close, breakout_upper_shadow=float(upper), breakout_body=float(body),
                     breakout_resistance_phase='attack' if attack == index else 'response',
                     observed_open=bar.open, observed_high=bar.high, observed_low=bar.low,
                     observed_close=bar.close, observed_volume=bar.volume, previous_volume=previous.volume,
                     bearish_reference_date=bearish.timestamp.date().isoformat() if bearish is not None else None,
                     bearish_reference_volume=bearish.volume if bearish is not None else None,
                     decision_timestamp=bar.timestamp.replace(hour=15, minute=0).isoformat())
        if child_clear:
            proof.pop('exit_target_fraction')
            proof.update(reason=BREAKOUT_CHILD_CLEAR, exit_fraction=1.0,
                         mother_index=index - 1, mother_date=previous.timestamp.date().isoformat(),
                         mother_high=previous.high, mother_low=previous.low,
                         child_index=index, child_date=bar.timestamp.date().isoformat(),
                         child_high=bar.high, child_low=bar.low, child_close=bar.close)
            child_candidate = None
        elif gap_clear:
            proof.pop('exit_target_fraction')
            proof.update(reason=BREAKOUT_GAP_BEARISH_CLEAR, exit_fraction=1.0,
                         breakout_gap_direction=gap_direction,
                         previous_high=previous.high, previous_low=previous.low,
                         previous_close=previous.close)
        risks[index] = proof
        warning = None if gap_clear or child_clear else (index, proof)
        candidate = None
    yield end + 1, risks
