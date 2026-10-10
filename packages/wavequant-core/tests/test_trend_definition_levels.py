"""The six Figure 008 definitions retain one meaning across recursive levels."""

from dataclasses import replace
from datetime import datetime, timedelta
from itertools import zip_longest

import pytest

from wavequant.domain.market_structure.polyline import LinePoint, PointKind, ReversalPoint
from wavequant.domain.market_structure.trend_foundations import observe_trend_foundations
from wavequant.domain.market_structure.trend_primitives import (
    is_bear_trend,
    is_bull_trend,
    last_fall_high,
    last_rise_low,
    negative_reversal,
    positive_reversal,
)
from wavequant.domain.market_structure.trend_structure import (
    StructuralTrend,
    StructureContext,
    TrendPriceState,
    classify_trend_prices,
    observe_structure,
)
from wavequant.domain.models.model import Bar


LABELS = ('L1', 'H1', 'L0', 'H2', 'L2', 'H3', 'L3', 'H4', 'L4',
          'H0', 'L5', 'H5', 'L6', 'H6', 'L7', 'H7', 'L8')
PRICES = (20, 30, 10, 20, 14, 26, 18, 35, 28, 45, 38, 42, 32, 36, 24, 29, 26)


def figure_points(trend_level: int) -> tuple[ReversalPoint, ...]:
    """Synthetic prices preserve the image's ordering, not measured prices."""
    return tuple(
        ReversalPoint(
            LinePoint(index, 0, PointKind.HIGH if label.startswith('H') else PointKind.LOW, float(price)),
            index + 1,
            f'synthetic_level_{trend_level - 1}',
        )
        for index, (label, price) in enumerate(zip(LABELS, PRICES))
    )


def context(
    points: tuple[ReversalPoint, ...], trend_level: int, *, start: int = 0, end: int = 17,
) -> StructureContext:
    return observe_structure(
        points, symbol='FIG008', timeframe='1d', window_start=start, asof_index=end,
        trend_level=trend_level, source_level=trend_level - 1,
    )


def source_bars(points: tuple[ReversalPoint, ...]) -> tuple[Bar, ...]:
    bars = []
    for reversal in points:
        point = reversal.point
        high = point.price if point.kind == PointKind.HIGH else point.price + 2
        low = point.price if point.kind == PointKind.LOW else point.price - 2
        middle = (high + low) / 2
        bars.append(Bar(datetime(2026, 1, 1) + timedelta(days=point.index), 'FIG008',
                        middle, high, low, middle, 1000))
    bars.append(Bar(datetime(2026, 1, 18), 'FIG008', 27, 29, 27, 28, 1000))
    return tuple(bars)


@pytest.mark.parametrize('trend_level', [1, 2, 3])
def test_figure008_keys_use_nearest_opposite_turn_at_each_level(trend_level: int) -> None:
    points = figure_points(trend_level)
    observed = context(points, trend_level)
    named = dict(zip(LABELS, points))

    for low_name, high_name in (('L0', 'H1'), ('L7', 'H6')):
        key = last_fall_high(observed, low=named[low_name], trend_level=trend_level)
        assert key is not None
        assert (key.source_index, key.price) == (named[high_name].point.index, named[high_name].point.price)
    for high_name, low_name in (('H0', 'L4'), ('H4', 'L3')):
        key = last_rise_low(observed, high=named[high_name], trend_level=trend_level)
        assert key is not None
        assert (key.source_index, key.price) == (named[low_name].point.index, named[low_name].point.price)
    minimum_key = last_fall_high(observed, trend_level=trend_level)
    maximum_key = last_rise_low(observed, trend_level=trend_level)
    assert minimum_key is not None and minimum_key.source_index == 1
    assert maximum_key is not None and maximum_key.source_index == 8


@pytest.mark.parametrize('trend_level', [1, 2, 3])
def test_figure008_reversals_wait_for_confirmation_at_each_level(trend_level: int) -> None:
    for reversal in figure_points(trend_level):
        assert not positive_reversal(reversal, asof_index=reversal.point.index, trend_level=trend_level)
        assert not negative_reversal(reversal, asof_index=reversal.point.index, trend_level=trend_level)
        assert positive_reversal(reversal, asof_index=reversal.confirmed_index, trend_level=trend_level) == (
            reversal.point.kind == PointKind.LOW
        )
        assert negative_reversal(reversal, asof_index=reversal.confirmed_index, trend_level=trend_level) == (
            reversal.point.kind == PointKind.HIGH
        )


@pytest.mark.parametrize('trend_level', [1, 2, 3])
def test_figure008_rising_and_falling_windows_use_every_same_kind_pair(trend_level: int) -> None:
    points = figure_points(trend_level)
    rising = context(points, trend_level, start=2, end=10)
    falling = context(points, trend_level, start=9, end=15)
    full = context(points, trend_level)

    assert is_bull_trend(rising, trend_level=trend_level) is True
    assert is_bear_trend(rising, trend_level=trend_level) is False
    assert is_bear_trend(falling, trend_level=trend_level) is True
    assert is_bull_trend(falling, trend_level=trend_level) is False
    assert is_bull_trend(full, trend_level=trend_level) is False
    assert is_bear_trend(full, trend_level=trend_level) is False


@pytest.mark.parametrize('trend_level', [1, 2, 3])
@pytest.mark.parametrize(('highs', 'lows', 'expected'), [
    ([20.0, 25.0, 30.0], [10.0, 14.0, 18.0], StructuralTrend.BULL),
    ([30.0, 25.0, 20.0], [18.0, 14.0, 10.0], StructuralTrend.BEAR),
    ([20.0, 20.0, 30.0], [10.0, 14.0, 18.0], StructuralTrend.MIXED),
    ([20.0, 25.0, 30.0], [10.0, 14.0, 14.0], StructuralTrend.MIXED),
    ([30.0, 20.0, 25.0], [10.0, 12.0, 14.0], StructuralTrend.MIXED),
    ([20.0], [10.0, 14.0], StructuralTrend.UNKNOWN),
    ([], [], StructuralTrend.UNKNOWN),
])
def test_price_classifier_requires_strict_direction_for_the_entire_window(
    trend_level: int, highs: list[float], lows: list[float], expected: StructuralTrend,
) -> None:
    assert classify_trend_prices(highs, lows, trend_level=trend_level) == expected


@pytest.mark.parametrize('trend_level', [1, 2, 3])
@pytest.mark.parametrize(('highs', 'lows', 'local', 'window'), [
    ([20.0, 25.0, 30.0], [10.0, 14.0, 18.0], StructuralTrend.BULL, StructuralTrend.BULL),
    ([30.0, 25.0, 20.0], [18.0, 14.0, 10.0], StructuralTrend.BEAR, StructuralTrend.BEAR),
    ([20.0, 20.0, 30.0], [10.0, 14.0, 18.0], StructuralTrend.BULL, StructuralTrend.MIXED),
    ([20.0, 25.0, 30.0], [10.0, 14.0, 14.0], StructuralTrend.MIXED, StructuralTrend.MIXED),
    ([30.0, 20.0, 25.0], [10.0, 12.0, 14.0], StructuralTrend.BULL, StructuralTrend.MIXED),
    ([20.0, 25.0, 30.0], [10.0], StructuralTrend.UNKNOWN, StructuralTrend.UNKNOWN),
    ([], [10.0, 14.0], StructuralTrend.UNKNOWN, StructuralTrend.UNKNOWN),
    ([], [], StructuralTrend.UNKNOWN, StructuralTrend.UNKNOWN),
])
def test_incremental_prices_match_batch_for_every_prefix_without_losing_earlier_pairs(
    trend_level: int, highs: list[float], lows: list[float], local: StructuralTrend, window: StructuralTrend,
) -> None:
    state = TrendPriceState(trend_level=trend_level)
    known_highs: list[float] = []
    known_lows: list[float] = []
    assert state.trend == state.window_trend == StructuralTrend.UNKNOWN
    assert (state.trend_level, state.source_level) == (trend_level, trend_level - 1)
    for high, low in zip_longest(highs, lows):
        for kind, price, known in ((PointKind.HIGH, high, known_highs), (PointKind.LOW, low, known_lows)):
            if price is None:
                continue
            state.append(kind, price)
            known.append(price)
            assert state.trend == classify_trend_prices(known_highs[-2:], known_lows[-2:], trend_level=trend_level)
            assert state.window_trend == classify_trend_prices(known_highs, known_lows, trend_level=trend_level)
    assert state.trend == local
    assert state.window_trend == window


@pytest.mark.parametrize('trend_level', [1, 2, 3])
def test_incremental_rejected_price_does_not_change_later_comparisons(trend_level: int) -> None:
    state = TrendPriceState(trend_level=trend_level)
    state.append(PointKind.HIGH, 20.0)
    state.append(PointKind.LOW, 10.0)
    with pytest.raises(ValueError, match='trend price'):
        state.append(PointKind.HIGH, float('nan'))
    state.append(PointKind.HIGH, 25.0)
    state.append(PointKind.LOW, 14.0)
    assert state.trend == state.window_trend == StructuralTrend.BULL


@pytest.mark.parametrize('trend_level', [1, 2, 3])
def test_latest_rising_pairs_do_not_replace_full_window_direction(trend_level: int) -> None:
    points = tuple(
        ReversalPoint(LinePoint(index, 0, kind, price), index + 1, 'synthetic_confirmed')
        for index, (kind, price) in enumerate([
            (PointKind.LOW, 10.0), (PointKind.HIGH, 30.0),
            (PointKind.LOW, 12.0), (PointKind.HIGH, 20.0),
            (PointKind.LOW, 14.0), (PointKind.HIGH, 25.0),
        ])
    )
    observed = context(points, trend_level, end=6)
    assert observed.trend == StructuralTrend.BULL
    assert observed.window_trend == StructuralTrend.MIXED
    assert is_bull_trend(observed, trend_level=trend_level) is False


@pytest.mark.parametrize('trend_level', [1, 2, 3])
def test_insufficient_points_remain_unknown_in_named_definitions(trend_level: int) -> None:
    observed = context(figure_points(trend_level), trend_level, end=3)
    assert is_bull_trend(observed, trend_level=trend_level) is None
    assert is_bear_trend(observed, trend_level=trend_level) is None


@pytest.mark.parametrize('price', [0.0, -1.0, float('nan'), float('inf'), True])
def test_classifier_rejects_invalid_prices_even_when_evidence_is_insufficient(price: float) -> None:
    with pytest.raises(ValueError, match='trend price'):
        classify_trend_prices([price], [], trend_level=1)


@pytest.mark.parametrize('trend_level', [1, 2, 3])
def test_structure_and_foundations_are_invariant_under_future_prefixes(trend_level: int) -> None:
    points = figure_points(trend_level)
    bars = source_bars(points)
    for end in range(len(bars)):
        known = tuple(point for point in points if point.confirmed_index <= end)
        assert context(points, trend_level, end=end) == context(known, trend_level, end=end)
        full = observe_trend_foundations(
            bars, points, symbol='FIG008', timeframe='1d', asof_index=end, trend_level=trend_level,
        )
        prefix = observe_trend_foundations(
            bars[:end + 1], known, symbol='FIG008', timeframe='1d', trend_level=trend_level,
        )
        assert full == prefix
        assert (full.structure.trend_level, full.structure.source_level) == (trend_level, trend_level - 1)


@pytest.mark.parametrize('trend_level', [0, 4, True, 1.0])
def test_every_definition_rejects_an_invalid_level(trend_level: int) -> None:
    points = figure_points(1)
    observed = context(points, 1)
    with pytest.raises(ValueError, match='trend_level'):
        positive_reversal(points[0], asof_index=17, trend_level=trend_level)
    with pytest.raises(ValueError, match='trend_level'):
        negative_reversal(points[1], asof_index=17, trend_level=trend_level)
    with pytest.raises(ValueError, match='trend_level'):
        last_fall_high(observed, trend_level=trend_level)
    with pytest.raises(ValueError, match='trend_level'):
        last_rise_low(observed, trend_level=trend_level)
    with pytest.raises(ValueError, match='trend_level'):
        is_bull_trend(observed, trend_level=trend_level)
    with pytest.raises(ValueError, match='trend_level'):
        is_bear_trend(observed, trend_level=trend_level)
    with pytest.raises(ValueError, match='trend_level'):
        classify_trend_prices([20.0, 25.0], [10.0, 14.0], trend_level=trend_level)
    with pytest.raises(ValueError, match='trend_level'):
        TrendPriceState(trend_level=trend_level)


@pytest.mark.parametrize(('trend_level', 'source_level'), [(1, 1), (2, 0), (2, 2), (3, 1), (3, 3), (2, True)])
def test_structure_rejects_a_non_immediate_source_level(trend_level: int, source_level: int) -> None:
    points = figure_points(trend_level)
    with pytest.raises(ValueError, match='source_level'):
        observe_structure(
            points, symbol='FIG008', timeframe='1d', window_start=0, asof_index=17,
            trend_level=trend_level, source_level=source_level,
        )
    with pytest.raises(ValueError, match='source_level'):
        replace(context(points, trend_level), source_level=source_level)
    with pytest.raises(ValueError, match='source_level'):
        observe_trend_foundations(
            source_bars(points), points, symbol='FIG008', timeframe='1d',
            trend_level=trend_level, source_level=source_level,
        )


@pytest.mark.parametrize('trend_level', [2, 3])
def test_definition_context_cannot_be_relabelled_as_another_level(trend_level: int) -> None:
    observed = context(figure_points(trend_level), trend_level)
    with pytest.raises(ValueError, match='matching trend_level'):
        last_fall_high(observed)
    with pytest.raises(ValueError, match='matching trend_level'):
        last_rise_low(observed)
    with pytest.raises(ValueError, match='matching trend_level'):
        is_bull_trend(observed)
    with pytest.raises(ValueError, match='matching trend_level'):
        is_bear_trend(observed)


@pytest.mark.parametrize('trend_level', [1, 2, 3])
def test_foundations_preserves_level_when_rechecking_a_frozen_transition(trend_level: int) -> None:
    points = figure_points(trend_level)
    frozen = context(points, trend_level, start=2, end=10)
    result = observe_trend_foundations(
        source_bars(points), points, symbol='FIG008', timeframe='1d', window_start=2, asof_index=10,
        trend_level=trend_level, transition_context=frozen,
    )
    assert result.structure == frozen
    assert result.transition is not None
    with pytest.raises(ValueError, match='trend level'):
        observe_trend_foundations(
            source_bars(points), points, symbol='FIG008', timeframe='1d', asof_index=10,
            trend_level=2 if trend_level == 1 else 1, transition_context=frozen,
        )
