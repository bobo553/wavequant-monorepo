"""分钟兼容历史只在本级真实翻多后申请新的内部 N 证明。"""

from datetime import datetime, timedelta

import pytest

from wavequant.domain.market_structure import n_trend_confirmation, n_trend_reversals, trend_confirmation
from wavequant.domain.market_structure.trend_lines import _legacy_pending_bull_n
from wavequant.domain.market_structure.trend_stage_work import TrendStageComputer
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.hierarchical_entry import hierarchical_history
from wavequant.domain.strategies.hierarchy_stream import HierarchyHistoryStream


def _own_geometry(level):
    values = (12., 8., 11., 7., 10., 6., 11., 9.)
    bars, points = [], []
    for index, value in enumerate(values):
        kind = 'H' if index % 2 == 0 else 'L'
        low, high = (value - 1, value) if kind == 'H' else (value, value + 1)
        bar = Bar(datetime(2026, 1, 1) + timedelta(days=index), 'TEST',
                  (low + high) / 2, high, low, (low + high) / 2, 100.)
        bars.append(bar)
        points.append(dict(index=index, ordinal=0, kind=kind, value=value, available_at=index,
            time=bar.timestamp.date().isoformat(), label=kind, state='confirmed',
            trend_level=level, source_level=level - 1))
    return bars, points


@pytest.mark.parametrize('level', (1, 2, 3))
def test_own_frozen_key_controls_n_demand_and_alternation_closes_it(level):
    bars, points = _own_geometry(level)
    # 裸标签或下级方向不授予本级需求；末跌高 10 尚未真实突破。
    points[4].update(flip='翻空为多', wave_direction_after='up')
    assert not _legacy_pending_bull_n(points[:6], bars[:6], trend_level=level)
    assert _legacy_pending_bull_n(points[:7], bars[:7], trend_level=level)
    assert not _legacy_pending_bull_n(points, bars, trend_level=level)
    revised = [dict(point) for point in points[:7]]
    revised[-1]['value'] = 10.
    assert not _legacy_pending_bull_n(revised, bars[:7], trend_level=level)


@pytest.mark.parametrize('entry', ('batch', 'stream', 'worker'))
def test_legacy_entry_never_prepares_n_before_own_level_bull_flip(monkeypatch, entry):
    bars, _points = _own_geometry(1)

    def forbidden(*args, **kwargs):
        raise AssertionError('no own-level bull flip: N preparation is not authorized')

    for module in (n_trend_confirmation, n_trend_reversals, trend_confirmation):
        monkeypatch.setattr(module, 'prepare_n_confirmation_context', forbidden)
    monkeypatch.setattr(n_trend_confirmation, '_observation', forbidden)
    if entry == 'batch':
        history, _epochs = hierarchical_history(bars, n_target_trend_confirmation_enabled=True,
            _drawing_frames=iter((index, 0, ()) for index in range(len(bars))))
        assert all(points == () for day in history.values() for points in day.values())
    elif entry == 'stream':
        stream = HierarchyHistoryStream(bars, n_enabled=True)
        try:
            for index in range(len(bars)):
                stream.advance(index, 0, ())
        finally:
            stream.close()
    else:
        computer = TrendStageComputer(tuple(bars))
        try:
            for level in (1, 2, 3):
                result = computer.compute('hierarchy', len(bars) - 1, ((), ()),
                    level=level, n_enabled=True, options=())
                assert result['published'] == []
        finally:
            computer.close()
