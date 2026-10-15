"""全窗索引不得提前执行未来行情的自定义时区钩子。"""

from datetime import datetime, timedelta, timezone, tzinfo
from typing import Never
from zoneinfo import ZoneInfo

import pytest

from wavequant.domain.market_structure.hierarchical_confirmation import session_date
from wavequant.domain.market_structure.trend_market_index import _MarketRangeIndex
from wavequant.domain.models.model import Bar


@pytest.mark.parametrize('zone', (None, timezone.utc, timezone(timedelta(hours=9)), ZoneInfo('Asia/Tokyo')))
def test_market_index_preserves_standard_timezone_calendar(zone: tzinfo | None) -> None:
    bars = tuple(Bar(datetime(2026, 10, 9, 0, 30, tzinfo=zone) + timedelta(days=index),
                     'TEST', 10, 12, 9, 11, 100) for index in range(3))
    market = _MarketRangeIndex.build(bars)
    assert market is not None
    assert dict(market.positions) == {session_date(bar): (index,) for index, bar in enumerate(bars)}


def test_market_index_rejects_future_custom_timezone_without_calling_it() -> None:
    class CustomZone(tzinfo):
        calls = 0

        def utcoffset(self, stamp: datetime | None) -> Never:
            self.calls += 1
            raise RuntimeError('future timezone must remain unobserved')

        def dst(self, stamp: datetime | None) -> Never:
            self.calls += 1
            raise RuntimeError('future timezone must remain unobserved')

        def tzname(self, stamp: datetime | None) -> Never:
            self.calls += 1
            raise RuntimeError('future timezone must remain unobserved')

    zone = CustomZone()
    current = Bar(datetime(2026, 10, 9), 'TEST', 10, 12, 9, 11, 100)
    future = Bar(datetime(2026, 10, 10, tzinfo=zone), 'TEST', 10, 12, 9, 11, 100)
    assert _MarketRangeIndex.build((current, future)) is None
    assert zone.calls == 0


def test_market_index_rejects_timezone_subclass_before_offset_hooks() -> None:
    class CustomZone(ZoneInfo):
        calls = 0

        def utcoffset(self, stamp: datetime | None) -> Never:
            self.calls += 1
            raise RuntimeError('custom ZoneInfo remains on the ordinary prefix path')

    zone = CustomZone('UTC')
    future = Bar(datetime(2026, 10, 10, tzinfo=zone), 'TEST', 10, 12, 9, 11, 100)
    assert _MarketRangeIndex.build((future,)) is None
    assert zone.calls == 0
