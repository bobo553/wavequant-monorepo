"""跨策略共用的放量阴线定义，不使用相对前收涨跌判断阴阳。"""

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
