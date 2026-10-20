"""Indexed reanchors preserve positional selection under nonmonotone clocks."""

from copy import deepcopy
from datetime import datetime, timedelta

import pytest

from wavequant.domain.market_structure.secondary_trend import _ReanchorPositions, last_fall_high_reanchors
from wavequant.domain.market_structure.trend_market_index import _MarketPrefixIndex, _MarketRangeIndex, market_prefix
from wavequant.domain.models.model import Bar


class LegacyPoints(list):
    """Exercise the retained public-sequence fallback as the comparison oracle."""


def _fixture():
    start = datetime(2026, 1, 1)
    bars = tuple(Bar(start + timedelta(days=i), 'INDEX', 12., 22., 2., 12. if i % 2 == 0 else 4., 100)
                 for i in range(14))
    points = [dict(index=i, ordinal=0, time=start.date().isoformat(), kind='H' if i % 2 == 0 else 'L',
                   value=20. if i % 2 == 0 else 10., label=str(i),
                   available_at=(start + timedelta(days=(i * 7) % 10)).date().isoformat())
              for i in range(20)]
    return bars, points


@pytest.mark.parametrize('level', (1, 2, 3))
@pytest.mark.parametrize('indexed_market', (False, True))
def test_indexed_reanchors_match_original_search_at_every_prefix(level, indexed_market):
    bars, points = _fixture()
    owner = _MarketRangeIndex(bars)
    before = deepcopy(points)
    all_events = []
    for stop in range(2, len(bars) + 1):
        market = market_prefix(bars, stop)
        kwargs = {'_market_index': _MarketPrefixIndex(owner, market)} if indexed_market else {}
        expected = last_fall_high_reanchors(LegacyPoints(points), market, trend_level=level, **kwargs)
        actual = last_fall_high_reanchors(points, market, trend_level=level, **kwargs)
        assert actual == expected
        all_events.extend(actual)
    assert all_events
    assert points == before


def test_rightmost_known_low_uses_position_not_latest_knowledge_date():
    _, points = _fixture()
    index = _ReanchorPositions.build(points)
    assert index is not None
    for after in range(len(points)):
        for day in range(14):
            known = (datetime(2026, 1, 1) + timedelta(days=day)).date().isoformat()
            expected = [i for i, point in enumerate(points)
                        if i > after and point['kind'] == 'L' and point['available_at'] <= known]
            assert index.last_low(after, known) == (expected[-1] if expected else None)
        highs = [i for i in range(after) if points[i]['kind'] == 'H']
        assert index.previous_high[after] == (highs[-1] if highs else None)


def test_revision_truncation_and_same_day_ties_rebuild_local_index():
    bars, points = _fixture()
    for current in (points[:8], [dict(point, available_at='2026-01-01') for point in points], points[:1], []):
        assert last_fall_high_reanchors(current, bars) == last_fall_high_reanchors(LegacyPoints(current), bars)
    points[-1]['available_at'] = '2099-01-01'
    assert last_fall_high_reanchors(points, bars) == last_fall_high_reanchors(LegacyPoints(points), bars)


@pytest.mark.parametrize('level', (1, 2, 3))
def test_open_tail_uses_the_same_lower_level_confirmed_low(level):
    bars, _ = _fixture()
    points = [dict(index=i, ordinal=0, time='2026-01-01', available_at='2026-01-01',
                   kind=kind, value=value, label=f'{kind}{i}')
              for i, kind, value in ((0, 'H', 20.), (1, 'L', 10.), (2, 'H', 18.))]
    points[-1]['confirmed_by'] = dict(index=2, ordinal=0, time='2026-01-01',
        available_at='2026-01-01', kind='L', value=8., label='source-low')
    actual = last_fall_high_reanchors(points, bars, trend_level=level)
    assert actual == last_fall_high_reanchors(LegacyPoints(points), bars, trend_level=level)
    assert actual and actual[0]['active_low_source_level'] == level - 1
