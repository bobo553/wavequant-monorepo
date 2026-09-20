from datetime import datetime

import pytest

from wavequant.domain.models.model import Bar
from wavequant.infrastructure.market_data.minute import CachedMinuteSource, verify_minute_day
from wavequant.infrastructure.persistence.artifact_cache import ArtifactCache


def rows():
    clocks = (
        [f"09{minute:02d}" for minute in range(35, 60, 5)]
        + [f"10{minute:02d}" for minute in range(0, 60, 5)]
        + [f"11{minute:02d}" for minute in range(0, 35, 5)]
        + [f"13{minute:02d}" for minute in range(5, 60, 5)]
        + [f"14{minute:02d}" for minute in range(0, 60, 5)]
        + ["1500"]
    )
    return [
        dict(
            date="2025-05-15",
            time=f"20250515{clock}00000",
            code="sz.300154",
            open="9.99",
            high="10.01",
            low="9.97",
            close="9.99",
            volume="1000",
            adjustflag="3",
        )
        for clock in clocks
    ]


def day(volume=48000):
    return Bar(datetime(2025, 5, 15), "sz.300154", 9.99, 10.01, 9.97, 9.99, volume)


def test_minute_history_requires_complete_session_and_daily_volume_match():
    assert len(verify_minute_day(rows(), day())) == 48
    with pytest.raises(ValueError, match="完整 48 根"):
        verify_minute_day(rows()[:-1], day())
    with pytest.raises(ValueError, match="成交量不一致"):
        verify_minute_day(rows(), day(47000))


def test_unavailable_old_minute_history_fails_before_provider_access(tmp_path, monkeypatch):
    source = CachedMinuteSource(ArtifactCache(tmp_path))
    monkeypatch.setattr(
        "wavequant.infrastructure.market_data.minute.fetch_baostock_day",
        lambda *_: pytest.fail("unsupported year must not query provider"),
    )
    with pytest.raises(ValueError, match="2020-01-01"):
        source.get(Bar(datetime(2018, 6, 15), "sz.300154", 10, 10, 10, 10, 1000))
