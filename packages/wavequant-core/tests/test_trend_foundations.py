from collections.abc import Sequence
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from typing import cast, overload

import pytest

from wavequant.domain.market_structure.n_defense import selloff_high, squeeze_low
from wavequant.domain.market_structure.n_shape import (
    BoxAnchorMode, MilestoneBasis, NSetup, NStatus, PivotRef,
)
from wavequant.domain.market_structure.polyline import LinePoint, PointKind, ReversalPoint
from wavequant.domain.market_structure.price_action import AttackBasis, Direction
from wavequant.domain.market_structure.trend_foundations import TrendFoundations, observe_trend_foundations
from wavequant.domain.market_structure.trend_primitives import is_bear_trend, is_bull_trend
from wavequant.domain.market_structure.trend_structure import StructuralTrend, StructureContext, observe_structure
from wavequant.domain.models.model import Bar


def bar(index: int, opening: float, high: float, low: float, closing: float) -> Bar:
    return Bar(datetime(2026, 1, 1) + timedelta(days=index), 'TEST', opening, high, low, closing, 1000)


def point(index: int, kind: PointKind, price: float, *, confirmed: int | None = None) -> ReversalPoint:
    return ReversalPoint(LinePoint(index, 0, kind, price), index + 1 if confirmed is None else confirmed, 'fixture')


def trend_fixture() -> tuple[list[Bar], list[ReversalPoint]]:
    points = [point(index * 2, kind, price) for index, (kind, price) in enumerate([
        (PointKind.HIGH, 30), (PointKind.LOW, 20), (PointKind.HIGH, 25), (PointKind.LOW, 10),
        (PointKind.HIGH, 18), (PointKind.LOW, 13), (PointKind.HIGH, 28), (PointKind.LOW, 22),
    ])]
    rows = [(24, 30, 23, 25), (24, 26, 22, 23), (22, 23, 20, 21),
            (22, 24, 21, 23), (23, 25, 22, 24), (22, 23, 15, 16),
            (13, 15, 10, 12), (13, 16, 12, 15), (16, 18, 15, 17),
            (16, 17, 14, 15), (15, 16, 13, 14), (16, 24, 15, 23),
            (24, 28, 23, 27), (26, 27, 23, 24), (24, 25, 22, 23),
            (24, 27, 23, 26)]
    return [bar(index, *row) for index, row in enumerate(rows)], points


def figure_008_fixture() -> tuple[list[Bar], list[ReversalPoint]]:
    labels = ['L1', 'H1', 'L0', 'H2', 'L2', 'H3', 'L3', 'H4', 'L4',
              'H0', 'L5', 'H5', 'L6', 'H6', 'L7', 'H7', 'L8']
    prices = [20, 30, 10, 20, 14, 26, 18, 35, 28, 45, 38, 42, 32, 36, 24, 29, 26]
    points = [
        point(index, PointKind.LOW if label.startswith('L') else PointKind.HIGH, price)
        for index, (label, price) in enumerate(zip(labels, prices))
    ]
    bars = [
        bar(index, price + 1, price + 2, price, price + 1) if label.startswith('L')
        else bar(index, price - 1, price, price - 2, price - 1)
        for index, (label, price) in enumerate(zip(labels, prices))
    ]
    return [*bars, bar(17, 28, 29, 27, 28)], points


def frozen_context(points: Sequence[ReversalPoint]) -> StructureContext:
    return observe_structure(points, symbol='TEST', timeframe='1d', window_start=0, asof_index=7)


def observe(
    bars: Sequence[Bar], points: Sequence[ReversalPoint], *, end: int | None = None,
    context: StructureContext | None = None, basis: AttackBasis = AttackBasis.INTRABAR,
    setup: NSetup | None = None, start: int = 0,
) -> TrendFoundations:
    return observe_trend_foundations(
        bars, points, symbol='TEST', timeframe='1d', window_start=start, asof_index=end,
        transition_context=context, attack_basis=basis, n_setup=setup,
    )


def mirrored(
    bars: Sequence[Bar], points: Sequence[ReversalPoint],
) -> tuple[list[Bar], list[ReversalPoint]]:
    return (
        [replace(source, open=40 - source.open, high=40 - source.low,
                 low=40 - source.high, close=40 - source.close) for source in bars],
        [replace(reversal, point=replace(
            reversal.point, kind=PointKind.HIGH if reversal.point.kind == PointKind.LOW else PointKind.LOW,
            price=40 - reversal.point.price,
        )) for reversal in points],
    )


@pytest.mark.parametrize(
    ('current', 'expected'),
    [
        (bar(1, 12.5, 13, 9, 12.5), (False, True, True, False, True, False)),
        (bar(1, 7.5, 11, 7, 7.5), (True, False, False, True, False, True)),
    ],
)
def test_first_six_definitions_are_exposed_with_virtual_extremes(
    current: Bar, expected: tuple[bool, bool, bool, bool, bool, bool],
) -> None:
    previous = bar(0, 10, 12, 8, 10)
    result = observe([previous, current], [])
    relation = result.bar_relations
    assert relation is not None
    assert (relation.shrinking_head, relation.shrinking_foot, relation.extending_head,
            relation.falling_tail, relation.sunrise, relation.sunset) == expected
    assert result.virtual_low == min(current.low, previous.close)
    assert result.virtual_high == max(current.high, previous.close)


def test_first_bar_exposes_unknown_evidence_without_fabricating_a_reversal() -> None:
    result = observe([bar(0, 10, 12, 8, 10)], [])
    assert result.bar_relations is None
    assert result.virtual_low is result.virtual_high is None
    assert result.structure.trend == StructuralTrend.UNKNOWN
    assert result.positive_reversals == result.negative_reversals == ()
    assert result.transition is None
    assert result.signals is None
    assert result.n_observation is None
    assert result.squeeze_low is result.selloff_high is None


def test_figure_008_extreme_keys_and_confirmed_reversals_share_one_result() -> None:
    bars, points = figure_008_fixture()
    result = observe(bars, points)
    assert result.structure.last_fall_high is not None
    assert result.structure.last_rise_low is not None
    assert result.structure.last_fall_high.source_index == 1  # H1 left of L0.
    assert result.structure.last_rise_low.source_index == 8  # L4 left of H0.
    assert result.positive_reversals == tuple(points[::2])
    assert result.negative_reversals == tuple(points[1::2])
    assert len(result.positive_reversals) == 9
    assert len(result.negative_reversals) == 8
    rising = observe(bars, points, start=2, end=10)
    assert is_bull_trend(rising.structure) is True
    assert is_bear_trend(rising.structure) is False
    falling_bars, falling_points = trend_fixture()
    falling = observe(falling_bars, falling_points, end=7)
    assert is_bear_trend(falling.structure) is True
    assert is_bull_trend(falling.structure) is False


@pytest.mark.parametrize('down', [False, True])
def test_suspicion_flip_formation_and_alternation_remain_separate_facts(down: bool) -> None:
    bars, points = trend_fixture()
    if down:
        bars, points = mirrored(bars, points)
    context = frozen_context(points)
    suspicion = observe(bars, points, end=11, context=context)
    assert suspicion.signals is not None
    assert suspicion.signals.head_suspicion is down
    assert suspicion.signals.bottom_suspicion is (not down)
    assert not suspicion.signals.flip_to_bull
    assert not suspicion.signals.flip_to_bear
    assert not suspicion.signals.head_formed
    assert not suspicion.signals.bottom_formed
    broken = observe(bars, points, end=12, context=context)
    assert broken.signals is not None
    assert broken.signals.flip_to_bear is down
    assert broken.signals.flip_to_bull is (not down)
    assert broken.signals.head_formed is down
    assert broken.signals.bottom_formed is (not down)
    assert not broken.signals.bear_to_bull_alternation
    assert not broken.signals.bull_to_bear_alternation
    alternation = observe(bars, points, end=15, context=context)
    assert alternation.signals is not None
    assert alternation.signals.bull_to_bear_alternation is down
    assert alternation.signals.bear_to_bull_alternation is (not down)
    assert alternation.transition is not None
    assert alternation.transition.retracement is not None
    assert alternation.transition.retracement.ratio == .4


@pytest.mark.parametrize('down', [False, True])
def test_default_strict_extreme_break_matches_figure_008_without_requiring_close_cross(down: bool) -> None:
    bars, points = trend_fixture()
    bars[12] = replace(bars[12], close=24)
    if down:
        bars, points = mirrored(bars, points)
    context = frozen_context(points)
    default = observe(bars, points, end=12, context=context)
    closing = observe(bars, points, end=12, context=context, basis=AttackBasis.CLOSE)
    assert default.signals is not None
    assert closing.signals is not None
    assert default.signals.flip_to_bear is down
    assert default.signals.flip_to_bull is (not down)
    assert not closing.signals.flip_to_bear
    assert not closing.signals.flip_to_bull
    assert default.transition is not None
    assert default.transition.attack is not None
    assert default.transition.attack.intrabar_crossed
    assert not default.transition.attack.close_crossed


def test_equal_key_is_a_touch_and_does_not_flip_the_frozen_trend() -> None:
    bars, points = trend_fixture()
    bars[12] = replace(bars[12], high=25, close=24)
    result = observe(bars, points, end=12, context=frozen_context(points))
    assert result.signals is not None
    assert not result.signals.flip_to_bull


def test_current_structure_window_can_differ_from_the_frozen_transition_window() -> None:
    bars, points = trend_fixture()
    context = frozen_context(points)
    result = observe(bars, points, start=8, context=context)
    assert result.structure.window_start == 8
    assert result.structure.trend == StructuralTrend.BULL
    assert context.window_start == 0
    assert result.signals is not None
    assert result.signals.bear_to_bull_alternation
    assert all(reversal.point.index >= 8 for reversal in result.structure.points)


@pytest.mark.parametrize('turn_count', [0, 1, 2, 3, 4, 6, 8])
def test_any_confirmed_turn_count_is_observed_without_fixed_chart_label_assumptions(turn_count: int) -> None:
    bars, points = trend_fixture()
    result = observe(bars, points[:turn_count])
    assert len(result.structure.points) == turn_count
    assert len(result.positive_reversals) + len(result.negative_reversals) == turn_count
    if turn_count < 4:
        assert result.structure.trend == StructuralTrend.UNKNOWN
    assert result.transition is None
    assert result.signals is None


@pytest.mark.parametrize('day_interval', [3, 11])
def test_changing_calendar_span_does_not_change_price_relation_or_transition_rules(day_interval: int) -> None:
    bars, points = trend_fixture()
    context = frozen_context(points)
    original = observe(bars, points, context=context)
    stretched = [replace(source, timestamp=bars[0].timestamp + timedelta(days=index * day_interval))
                 for index, source in enumerate(bars)]
    result = observe(stretched, points, context=context)
    assert result.structure == original.structure
    assert result.bar_relations == original.bar_relations
    assert result.signals == original.signals
    assert result.transition is not None
    assert result.transition.attack is not None
    assert result.transition.attack.bar_index == 12
    assert result.transition.attack.observed_at == stretched[12].timestamp


@pytest.mark.parametrize('segment_multiplier', [2, 5])
def test_changing_the_number_of_bars_in_each_segment_preserves_the_confirmed_logic(
    segment_multiplier: int,
) -> None:
    bars, points = trend_fixture()
    stretched_bars = [replace(
        bars[index // segment_multiplier], timestamp=bars[0].timestamp + timedelta(days=index),
    ) for index in range(len(bars) * segment_multiplier)]
    stretched_points = [replace(
        reversal, point=replace(reversal.point, index=reversal.point.index * segment_multiplier),
        confirmed_index=reversal.confirmed_index * segment_multiplier,
    ) for reversal in points]
    context = observe_structure(stretched_points, symbol='TEST', timeframe='1d', window_start=0,
                                asof_index=7 * segment_multiplier)
    result = observe(stretched_bars, stretched_points, end=15 * segment_multiplier, context=context)
    assert result.signals is not None
    assert result.signals.bottom_suspicion
    assert result.signals.flip_to_bull
    assert result.signals.bear_to_bull_alternation
    assert result.transition is not None
    assert result.transition.attack is not None
    assert result.transition.attack.bar_index == 12 * segment_multiplier
    assert result.transition.alternation_confirmed_index == 15 * segment_multiplier
    assert result.transition.retracement is not None
    assert result.transition.retracement.ratio == .4


def n_fixture() -> tuple[list[Bar], NSetup]:
    bars = [bar(0, 8.5, 9, 8, 8.5), bar(1, 10, 12, 9.5, 11),
            bar(2, 10.7, 11, 10, 10.5), bar(3, 12.2, 12.6, 12, 12.2)]
    setup = NSetup('TEST', '1d', Direction.UP, PivotRef(0, 0), PivotRef(1, 1),
                   PivotRef(2, 2), 'fixture_confirmed', BoxAnchorMode.ATTACK_VIRTUAL_EXTREME)
    return bars, setup


@pytest.mark.parametrize('down', [False, True])
def test_completed_positive_and_inverse_n_publish_only_their_named_virtual_defense(down: bool) -> None:
    bars, setup = n_fixture()
    if down:
        bars, _ = mirrored(bars, [])
        setup = replace(setup, direction=Direction.DOWN)
    result = observe(bars, [], setup=setup)
    assert result.n_observation is not None
    assert result.n_observation.status == NStatus.COMPLETED
    if down:
        assert result.selloff_high == selloff_high(result.n_observation) == 29.5
        assert result.squeeze_low is None
    else:
        assert result.squeeze_low == squeeze_low(result.n_observation) == 10.5
        assert result.selloff_high is None


@pytest.mark.parametrize(('end', 'status'), [(1, NStatus.AWAIT_ANCHORS), (2, NStatus.FORMING)])
def test_uncompleted_n_does_not_manufacture_squeeze_or_selloff_reference(end: int, status: NStatus) -> None:
    bars, setup = n_fixture()
    result = observe(bars, [], end=end, setup=setup)
    assert result.n_observation is not None
    assert result.n_observation.status == status
    assert result.squeeze_low is result.selloff_high is None


def test_each_historical_result_equals_an_observation_of_its_visible_prefix() -> None:
    bars, points = trend_fixture()
    context = frozen_context(points)
    for end in range(len(bars)):
        visible_points = [reversal for reversal in points if reversal.confirmed_index <= end]
        applicable_context = context if end >= context.asof_index else None
        assert observe(bars, points, end=end, context=applicable_context) == observe(
            bars[:end + 1], visible_points, context=applicable_context,
        )
    n_bars, setup = n_fixture()
    for end in range(len(n_bars)):
        assert observe(n_bars, [], end=end, setup=setup) == observe(n_bars[:end + 1], [], setup=setup)


class PrefixOnlyBars(Sequence[Bar]):
    """Expose a future length while refusing reads beyond the historical cutoff."""

    def __init__(self, bars: Sequence[Bar], end: int) -> None:
        self.bars = tuple(bars)
        self.end = end

    def __len__(self) -> int:
        return len(self.bars)

    @overload
    def __getitem__(self, index: int) -> Bar: ...

    @overload
    def __getitem__(self, index: slice) -> Sequence[Bar]: ...

    def __getitem__(self, index: int | slice) -> Bar | Sequence[Bar]:
        if isinstance(index, slice):
            positions = range(*index.indices(len(self)))
            assert all(position <= self.end for position in positions), 'future bar read'
            return self.bars[index]
        assert 0 <= index <= self.end, 'future bar read'
        return self.bars[index]


def test_future_bars_and_unconfirmed_pivot_prices_cannot_influence_an_earlier_result() -> None:
    bars, points = trend_fixture()
    context = frozen_context(points)
    end = 11
    future = point(12, PointKind.LOW, 9999, confirmed=99)
    visible_points = [reversal for reversal in points if reversal.confirmed_index <= end]
    expected = observe(bars[:end + 1], visible_points, context=context)
    assert observe(PrefixOnlyBars(bars, end), [*visible_points, future], end=end, context=context) == expected
    corrupted_future = [*bars[:end + 1], replace(bars[12], high=float('nan'), symbol='OTHER')]
    assert observe(corrupted_future, [*visible_points, future], end=end, context=context) == expected


@pytest.mark.parametrize(('start', 'end'), [(0, -1), (0, 16), (-1, 10), (12, 11), (0, True)])
def test_unavailable_or_invalid_windows_are_rejected(start: int, end: int) -> None:
    bars, points = trend_fixture()
    with pytest.raises(ValueError):
        observe(bars, points, start=start, end=end)


@pytest.mark.parametrize('empty', [[], ()])
def test_empty_bar_history_is_rejected(empty: Sequence[Bar]) -> None:
    with pytest.raises(ValueError):
        observe(empty, [])


@pytest.mark.parametrize('invalid', [
    replace(bar(1, 10, 12, 8, 10), low=11),
    replace(bar(1, 10, 12, 8, 10), high=float('nan')),
    replace(bar(1, 10, 12, 8, 10), symbol='OTHER'),
    replace(bar(1, 10, 12, 8, 10), timestamp=datetime(2026, 1, 1)),
    replace(bar(1, 10, 12, 8, 10), timestamp=datetime(2026, 1, 2, tzinfo=timezone.utc)),
])
def test_visible_ohlc_symbol_and_time_invariants_are_enforced(invalid: Bar) -> None:
    with pytest.raises(ValueError):
        observe([bar(0, 10, 12, 8, 10), invalid], [])


def test_confirmed_pivot_prices_are_checked_even_outside_the_current_structure_window() -> None:
    bars, points = trend_fixture()
    points[0] = replace(points[0], point=replace(points[0].point, price=31))
    with pytest.raises(ValueError, match='pivot price'):
        observe(bars, points, start=8)


def test_visible_pivot_order_and_alternation_are_enforced() -> None:
    bars, points = trend_fixture()
    with pytest.raises(ValueError):
        observe(bars, [points[1], points[0]])
    with pytest.raises(ValueError):
        observe(bars, [points[0], points[2]])


@pytest.mark.parametrize('field', ['symbol', 'timeframe'])
def test_context_and_n_setup_must_match_the_requested_instrument_and_period(field: str) -> None:
    bars, points = trend_fixture()
    context = frozen_context(points)
    wrong_context = replace(context, symbol='OTHER') if field == 'symbol' else replace(context, timeframe='1w')
    with pytest.raises(ValueError, match='matching symbol and timeframe'):
        observe(bars, points, context=wrong_context)
    n_bars, setup = n_fixture()
    wrong_setup = replace(setup, symbol='OTHER') if field == 'symbol' else replace(setup, timeframe='1w')
    with pytest.raises(ValueError, match='matching symbol and timeframe'):
        observe(n_bars, [], setup=wrong_setup)


def test_future_or_changed_frozen_context_is_rejected() -> None:
    bars, points = trend_fixture()
    context = frozen_context(points)
    with pytest.raises(ValueError):
        observe(bars, points, end=6, context=context)
    points[0] = replace(points[0], source='changed')
    with pytest.raises(ValueError, match='changed the frozen context'):
        observe(bars, points, context=context)


def test_wrong_runtime_types_and_policy_values_raise_value_error() -> None:
    bars, points = trend_fixture()
    with pytest.raises(ValueError):
        observe(cast(Sequence[Bar], [object()]), [])
    with pytest.raises(ValueError):
        observe(bars, cast(Sequence[ReversalPoint], [object()]))
    with pytest.raises(ValueError):
        observe(bars, points, context=cast(StructureContext, object()))
    with pytest.raises(ValueError):
        observe(bars, points, setup=cast(NSetup, object()))
    with pytest.raises(ValueError):
        observe(bars, points, basis=cast(AttackBasis, 'close'))
    with pytest.raises(ValueError):
        observe_trend_foundations(bars, points, symbol='TEST', timeframe='1d',
                                 milestone_basis=cast(MilestoneBasis, 'extreme'))
    with pytest.raises(ValueError):
        observe(cast(Sequence[Bar], 'bars'), [])


def test_the_result_and_its_nested_evidence_are_immutable() -> None:
    bars, points = trend_fixture()
    result = observe(bars, points, context=frozen_context(points))
    with pytest.raises(FrozenInstanceError):
        setattr(result, 'squeeze_low', 1)
    with pytest.raises(FrozenInstanceError):
        setattr(result.structure, 'trend', StructuralTrend.BEAR)
    assert isinstance(result.positive_reversals, tuple)
    assert isinstance(result.negative_reversals, tuple)
