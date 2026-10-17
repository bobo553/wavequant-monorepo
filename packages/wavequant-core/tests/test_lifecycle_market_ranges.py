"""共用行情树查询不跨越当日，保持守低、等值极值及公开输入回退语义。"""

from dataclasses import replace
from datetime import timedelta

import pytest

from wavequant.domain.market_structure.bull_alternation_confirmation import confirm_body_gap_alternation
from wavequant.domain.market_structure.trend_lifecycle import TrendLifecycle
from wavequant.domain.market_structure.trend_market_index import _MarketRangeIndex
from wavequant.domain.market_structure.source_bear_exit import _prepare_bearish_structure

from .test_body_gap_alternation import sample
from .test_bull_recovery import recovery_case
from .test_source_bear_structure_exit import sample as bear_sample


@pytest.mark.parametrize('level', (0, 1, 2, 3))
def test_indexed_recovery_has_identical_daily_events_with_adverse_future_prices(level: int) -> None:
    raw, geometry = recovery_case(level)
    bars = tuple([*raw, replace(raw[-1], timestamp=raw[-1].timestamp + timedelta(days=1), low=.01, high=100.)])
    market = _MarketRangeIndex.build(bars)
    assert market is not None
    indexed, ordinary = TrendLifecycle(), TrendLifecycle()
    for now in range(len(bars)):
        actual = indexed.advance((geometry,), bars, now, {}, _owned_structure=True, _market_index=market)
        expected = ordinary.advance((geometry,), bars, now, {}, _owned_structure=True)
        assert actual == expected, (level, now)
        assert indexed.checkpoint() == ordinary.checkpoint()


@pytest.mark.parametrize('change', ('none', 'equal_low', 'broken_origin', 'foreign_market'))
def test_body_gap_extremes_match_scan_and_ignore_future_suffix(change: str) -> None:
    raw, origin, high, points = sample()
    if change == 'equal_low':
        raw[3] = replace(raw[3], low=raw[2].low)
    elif change == 'broken_origin':
        raw[3] = replace(raw[3], low=origin.price - .01)
    bars = tuple([*raw, replace(raw[-1], timestamp=raw[-1].timestamp + timedelta(days=1), low=.01, high=100.)])
    indexed_bars = tuple(replace(bar, low=.001) for bar in bars) if change == 'foreign_market' else bars
    market = _MarketRangeIndex.build(indexed_bars)
    assert market is not None
    actual = confirm_body_gap_alternation(bars, 5, origin=origin, high=high, polyline=points, _market_index=market)
    expected = confirm_body_gap_alternation(bars, 5, origin=origin, high=high, polyline=points)
    assert actual == expected
    if change in ('none', 'equal_low', 'foreign_market'):
        assert actual is not None and actual.low.index == 2


@pytest.mark.parametrize('crossed', (False, True))
def test_bear_rebound_extreme_uses_the_same_strict_boundary(crossed: bool) -> None:
    raw, stroke = bear_sample()
    dates = {bar.timestamp.date().isoformat(): index for index, bar in enumerate(raw)}
    now = dates['2020-11-26']
    candidate = _prepare_bearish_structure(stroke, now, dates)
    assert candidate is not None
    position = candidate.rebound.index + 1
    raw[position] = replace(raw[position], high=candidate.rebound.price + (.01 if crossed else 0.))
    bars = tuple(raw)
    market = _MarketRangeIndex.build(bars)
    assert market is not None
    assert candidate.observe(1, bars, now, dates, _market_index=market) == candidate.observe(1, bars, now, dates)
    if crossed:
        assert candidate.observe(1, bars, now, dates, _market_index=market) == []
