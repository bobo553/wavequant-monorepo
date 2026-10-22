"""重叠 N 观察窗口只认证新增行情，保留公开输入与因果边界。"""

from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from wavequant.domain.models.model import Bar
from wavequant.domain.models.validated_bars import ValidatedBars, ValidatedBarWindows


def _bars(count: int) -> tuple[Bar, ...]:
    return tuple(Bar(datetime(2024, 1, 1) + timedelta(days=index), 'TEST',
                     10.0, 12.0, 9.0, 11.0, 100.0) for index in range(count))


def test_overlapping_windows_reuse_prices_and_validate_only_extension() -> None:
    bars = _bars(500)
    windows = ValidatedBarWindows(bars)
    for stop in range(100, 501):
        observed = windows.window(stop - 100, stop)
        assert isinstance(observed, ValidatedBars)
        assert tuple(observed) == bars[stop - 100:stop]
    # 首窗100根，之后每次1根新行情和1根真实接缝；没有重复扫描100根窗口。
    assert windows.validated_bars == 100 + 400 * 2
    before = windows.validated_bars
    for _ in range(20):
        assert tuple(windows.window(50, 150)) == bars[50:150]
    assert windows.validated_bars == before
    assert windows._ranges == [(0, 500)]


def test_separate_ranges_cannot_skip_the_unvalidated_join() -> None:
    bars = list(_bars(4))
    bars[2] = replace(bars[2], timestamp=bars[1].timestamp)
    windows = ValidatedBarWindows(tuple(bars))
    windows.window(0, 2)
    windows.window(2, 4)
    with pytest.raises(ValueError, match='chronological'):
        windows.window(0, 4)
    assert windows._ranges == [(0, 2), (2, 4)]


def test_overlap_can_extend_left_and_bridge_checked_ranges() -> None:
    bars = _bars(30)
    windows = ValidatedBarWindows(bars)
    for left, right in ((10, 15), (20, 25), (5, 22), (0, 30)):
        assert tuple(windows.window(left, right)) == bars[left:right]
    assert windows._ranges == [(0, 30)]
    assert windows.validated_bars <= 36


def test_future_invalid_prices_are_not_validated_until_visible() -> None:
    bars = _bars(5)
    invalid = (*bars[:4], replace(bars[4], close=20.0))
    windows = ValidatedBarWindows(invalid)
    assert tuple(windows.window(0, 4)) == invalid[:4]
    with pytest.raises(ValueError, match='inconsistent OHLC'):
        windows.window(1, 5)
    assert tuple(windows.window(0, 4)) == invalid[:4]
    assert windows._ranges == [(0, 4)]


def test_symbol_mismatch_at_seam_is_not_hidden_by_old_certificates() -> None:
    bars = _bars(6)
    changed = (*bars[:3], *(replace(bar, symbol='OTHER') for bar in bars[3:]))
    windows = ValidatedBarWindows(changed)
    windows.window(0, 3)
    windows.window(3, 6)
    with pytest.raises(ValueError):
        windows.window(1, 5)


def test_nonstandard_bar_does_not_receive_a_validation_certificate() -> None:
    class CustomBar(Bar):
        pass

    bar = CustomBar(datetime(2024, 1, 1), 'TEST', 10.0, 12.0, 9.0, 11.0, 100.0)
    windows = ValidatedBarWindows((bar,))
    assert type(windows.window(0, 1)) is tuple
    assert windows._ranges == []


def test_nonstandard_price_keeps_the_standalone_validation_path() -> None:
    class CustomPrice(float):
        pass

    bar = replace(_bars(1)[0], close=CustomPrice(11.0))
    windows = ValidatedBarWindows((bar,))
    assert type(windows.window(0, 1)) is tuple
    assert windows._ranges == []
