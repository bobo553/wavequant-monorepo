"""跨策略共用的放量阴线定义，不使用相对前收涨跌判断阴阳。"""

from collections.abc import Sequence
from fractions import Fraction

from ..models.model import Bar


def is_volume_bearish(bar: Bar, previous: Bar | None, last_bearish: Bar | None) -> bool:
    """阴线且量大于昨日或最近阴线；调用方维护最近阴线以支持逐日常量查询。"""
    if bar.close >= bar.open:
        return False
    references = (previous, last_bearish if last_bearish is not None
                  and last_bearish.close < last_bearish.open else None)
    return any(reference is not None and reference.symbol == bar.symbol
               and reference.timestamp < bar.timestamp and reference.volume > 0
               and bar.volume > reference.volume for reference in references)


def is_bearish_inside_child(mother: Bar, child: Bar) -> bool:
    """非十字母线包含阴线子线的完整高低区间；允许一端相等，不接受两端完全相同。"""
    return (mother.symbol == child.symbol and mother.timestamp < child.timestamp
            and mother.close != mother.open and child.close < child.open
            and child.high <= mother.high and child.low >= mother.low
            and (child.high < mother.high or child.low > mother.low))


def bearish_body_engulfs(bar: Bar, previous: Bar) -> bool:
    """阴线实体完整覆盖昨日非十字实体；上下沿均可相等，影线不参与。"""
    top, bottom = max(previous.open, previous.close), min(previous.open, previous.close)
    return (bar.symbol == previous.symbol and previous.timestamp < bar.timestamp
            and bar.close < bar.open and top > bottom
            and bar.open >= top and bar.close <= bottom)


def volume_bearish_buy_rejection(
    bar: Bar, previous: Bar | None, last_bearish: Bar | None = None,
) -> str | None:
    """放量阴线或跳空收阴禁止新买和加仓，与资格、目标及可选量过滤无关。"""
    if not is_volume_bearish(bar, previous, last_bearish):
        if (previous is not None and previous.symbol == bar.symbol
                and previous.timestamp < bar.timestamp
                and bar.open > previous.high and bar.close < bar.open):
            return 'gap_open_bearish_buy_blocked'
        return None
    if previous is not None and bearish_body_engulfs(bar, previous):
        return 'volume_bearish_body_engulfing_buy_blocked'
    return 'volume_bearish_buy_blocked'


def volume_bearish_entry_rejection(bars: Sequence[Bar], index: int) -> str | None:
    """仅消费指定决策棒及其历史；不读取未来棒或混用原始收盘价。"""
    bar = bars[index]
    if index < 1 or bar.close >= bar.open:
        return None
    previous = bars[index - 1]
    rejection = volume_bearish_buy_rejection(bar, previous)
    if rejection is not None:
        return rejection
    last_bearish = next((bars[prior] for prior in range(index - 1, -1, -1)
        if bars[prior].symbol == bar.symbol and bars[prior].close < bars[prior].open), None)
    rejection = volume_bearish_buy_rejection(bar, previous, last_bearish)
    if rejection is not None:
        return rejection
    if bar.open > previous.high:
        return 'gap_open_bearish_buy_blocked'
    return None


def is_long_upper_shadow(bar: Bar) -> bool:
    """上影至少占振幅一半且不短于实体，十进制边界不受浮点舍入影响。"""
    high, low, opened, closed = (Fraction(str(value)) for value in (bar.high, bar.low, bar.open, bar.close))
    upper = high - max(opened, closed)
    return high > low and 2 * upper >= high - low and upper >= abs(closed - opened)


def volume_upper_shadow_rejection(bar: Bar, previous: Bar | None) -> str | None:
    """阴阳线共用；只比较已知上一交易日的正成交量。"""
    if (previous is not None and previous.symbol == bar.symbol
            and previous.timestamp < bar.timestamp and previous.volume > 0
            and bar.volume > previous.volume and is_long_upper_shadow(bar)):
        return 'volume_long_upper_shadow_buy_blocked'
    return None


VOLUME_SHADOW_NEXT_WEAK_CLEAR = 'volume_upper_shadow_next_open_decline_clear'


def volume_shadow_next_open_eligible(bars: Sequence[Bar], index: int) -> bool:
    """仅依赖前两根收盘棒和当日开盘，不能读取当日最终最低价预选分钟风险。"""
    return (index >= 2 and bars[index].symbol == bars[index - 1].symbol
            and bars[index].timestamp > bars[index - 1].timestamp
            and bars[index].open < bars[index - 1].close
            and volume_upper_shadow_rejection(bars[index - 1], bars[index - 2]) is not None)


def volume_shadow_next_decline(
    bars: Sequence[Bar], index: int, observed: Bar | None = None,
) -> dict[str, object] | None:
    """传入部分日线时只用已观察最低价；默认完整日线在收盘确认。"""
    if not volume_shadow_next_open_eligible(bars, index):
        return None
    bar = bars[index] if observed is None else observed
    if bar.symbol != bars[index].symbol or bar.timestamp != bars[index].timestamp or bar.low >= bars[index].open:
        return None
    return dict(reason=VOLUME_SHADOW_NEXT_WEAK_CLEAR, exit_fraction=1.0,
                execution_model='same_day_close', warning_index=index - 1,
                warning_date=bars[index - 1].timestamp.date().isoformat(),
                warning_close=bars[index - 1].close, observed_open=bars[index].open,
                observed_low=bar.low, observed_close=bar.close,
                decision_timestamp=bar.timestamp.replace(hour=15, minute=0).isoformat())
