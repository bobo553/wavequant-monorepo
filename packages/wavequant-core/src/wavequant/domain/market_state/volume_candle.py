"""跨策略共用的放量阴线定义，不使用相对前收涨跌判断阴阳。"""

from collections.abc import Sequence

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
    """实体覆盖昨日非十字实体且至少一边严格越过；影线不参与。"""
    top, bottom = max(previous.open, previous.close), min(previous.open, previous.close)
    return (bar.symbol == previous.symbol and previous.timestamp < bar.timestamp
            and bar.close < bar.open and top > bottom
            and bar.open >= top and bar.close <= bottom
            and (bar.open > top or bar.close < bottom))


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
