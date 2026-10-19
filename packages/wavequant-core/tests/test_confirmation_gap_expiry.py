"""Gap-only alternation confirmation can expire without erasing the bull flip."""

from dataclasses import replace
from datetime import datetime

import pytest

from wavequant.domain.market_structure.trend_lifecycle import (
    LifecycleState, TrendLifecycle, _Cycle, _Fact, _Pivot, lifecycle_interval_days,
)
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.trading_intervals import (
    TradingEligibility, confirmed_trading_intervals, interval_days,
)


def bars():
    # Existing local unadjusted candles; reduced context, not a full-stock replay.
    rows = [
        ('2026-01-06', 6.1, 6.3, 6.0, 6.2),
        ('2026-01-14', 7.7, 8.0, 7.5, 7.8),
        ('2026-02-06', 6.86, 6.91, 6.68, 6.79),
        ('2026-02-09', 6.92, 7.0, 6.88, 6.92),
        ('2026-02-10', 6.97, 7.25, 6.96, 7.22),
        ('2026-02-11', 7.14, 7.14, 6.87, 6.93),
        ('2026-02-12', 7.0, 7.3, 6.95, 7.2),
    ]
    return [Bar(datetime.fromisoformat(day), 'sh.600825', op, hi, lo, cl, 100)
            for day, op, hi, lo, cl in rows]


def opening(level=2, path='p', *, full=False):
    proof = (dict(gap_confirmation_index=4, gap_previous_high=6.92, gap_low=6.96) if full else
             dict(candidate_index=4, gap_previous_body_high=6.92, gap_open=6.97))
    return dict(event='trend_lifecycle_state', trend_level=level, source_path=path, bar_index=4,
        state='空多交替', flip_confirmed=True, flip_index=1, flip_known_index=1,
        counter_index=2, counter_price=6.68, alternation_index=4,
        confirmation_rule=('positive_n_consolidation_gap_after_qualified_b' if full else
                           'body_gap_volume_bullish_breakout_after_defended_low'),
        confirmation_evidence=proof)


def lifecycle(level=2):
    origin, impulse, low = _Pivot(0, 0, 'L', 6.0, 0), _Pivot(1, 0, 'H', 8.0, 1), _Pivot(2, 0, 'L', 6.68, 4)
    fact = _Fact('翻空为多', impulse, 1, origin)
    observer = TrendLifecycle()
    observer._last_index = 4
    observer._seen[level, 'p'] = frozenset((fact,))
    observer._cycles[level, 'p'] = _Cycle(LifecycleState.BULL_ALTERNATION, True, origin, 1,
        impulse, low, 4, evidence=frozenset((fact,)), confirmation_source=(low,),
        confirmation_gap=(4, 6.92, 6.97, 'body'))
    return observer


@pytest.mark.parametrize('level', (0, 1, 2, 3))
@pytest.mark.parametrize('candidate', (None, (False, True, 8.0), (True, False, 8.0)))
@pytest.mark.parametrize('confirmed_gap', (None, (4, 6.92, 6.97, 'body')))
def test_checkpoint_keeps_candidate_gap_separate_from_confirmed_gap(level, candidate, confirmed_gap):
    observer = lifecycle(level)
    observer._cycles[level, 'p'] = replace(observer._cycles[level, 'p'],
        candidate=candidate, confirmation_gap=confirmed_gap)
    checkpoint = observer.checkpoint()
    restored = TrendLifecycle.from_checkpoint(checkpoint)
    assert restored.checkpoint() == checkpoint
    assert restored._cycles[level, 'p'].candidate == candidate
    assert restored._cycles[level, 'p'].confirmation_gap == confirmed_gap
    events = []
    restored._expire_confirmation_gap((level, 'p'), bars(), 5, events)
    assert bool(events) == (confirmed_gap is not None)
    assert observer.checkpoint() == checkpoint


@pytest.mark.parametrize('level', (1, 2, 3))
@pytest.mark.parametrize('full', (False, True))
def test_fill_closes_only_gap_window_and_replays_identically(level, full):
    data, gate = bars(), TradingEligibility(level)
    event = opening(level, full=full)
    assert gate.observe(event) is True
    frozen = gate.intervals
    assert gate.close_on_confirmation_gap_fill(data, 4) == []
    expired = gate.close_on_confirmation_gap_fill(data, 5)
    assert len(expired) == 1 and expired[0]['buy_eligible'] is False
    assert frozen[0].end_index is None
    assert gate.intervals[0].end_index == 5
    assert gate.intervals[0].end_reason == 'confirmation_gap_filled'
    assert confirmed_trading_intervals([event, *expired], len(data), level) == gate.intervals
    assert interval_days(gate.intervals, len(data))[4:6] == (True, False)
    assert interval_days(gate.intervals, len(data), include_end=True)[4:6] == (True, True)
    assert gate.close_on_confirmation_gap_fill(data, 6) == []
    assert gate.observe(dict(event, bar_index=6, alternation_index=6,
                             confirmation_rule='confirmed_higher_pullback_strictly_below_two_thirds')) is True


@pytest.mark.parametrize('level', (0, 1, 2, 3))
def test_revocation_preserves_flip_and_can_confirm_again_after_checkpoint(level):
    original = lifecycle(level)
    restored = TrendLifecycle.from_checkpoint(original.checkpoint())
    streams = []
    for observer in (original, restored):
        events = []
        before = observer._cycles[level, 'p']
        observer._expire_confirmation_gap((level, 'p'), bars(), 5, events)
        after = observer._cycles[level, 'p']
        assert after.state == LifecycleState.BULL_FLIP and after.alternated is None
        assert (after.origin, after.impulse, after.evidence) == (before.origin, before.impulse, before.evidence)
        assert after.confirmation_gap is None and after.confirmation_source == ()
        low = _Pivot(2, 0, 'L', 6.68, 6)
        observer._consume((level, 'p'), _Fact('空多交替', low, 6, before.origin, before.impulse), 6, events)
        assert observer._cycles[level, 'p'].alternated == 6
        assert events[-1]['state'] == '空多交替'
        streams.append(events)
    assert streams[0] == streams[1]


def test_independent_confirmation_supersedes_gap_without_rewriting_opening():
    observer, gate = lifecycle(), TradingEligibility(2)
    event = opening()
    gate.observe(event)
    cycle = observer._cycles[2, 'p']
    events = []
    observer._consume((2, 'p'), _Fact('空多交替', cycle.counter, 4, cycle.origin, cycle.impulse), 4, events)
    assert events[-1]['replaces_gap_confirmation_index'] == 4
    assert gate.observe(events[-1]) is None
    assert gate.intervals[0].confirmation_gap_lower == 6.92
    assert gate.intervals[0].gap_replaced_index == 4
    observer._expire_confirmation_gap((2, 'p'), bars(), 5, events)
    assert len(events) == 1
    assert gate.close_on_confirmation_gap_fill(bars(), 5) == [] and gate.active


def test_ordinary_confirmation_and_other_path_do_not_expire_on_nearby_gap():
    gate = TradingEligibility(2)
    ordinary = dict(opening(path='ordinary'), confirmation_rule='confirmed_higher_pullback_strictly_below_two_thirds')
    gate.observe(ordinary)
    gap = opening(path='gap')
    gate.observe(gap)
    expired = gate.close_on_confirmation_gap_fill(bars(), 5)
    assert [e['source_path'] for e in expired] == ['gap']
    assert gate.active and gate.intervals[0].end_index is None


@pytest.mark.parametrize('low, closes', [(6.93, False), (6.92, True), (6.87, True)])
def test_fill_requires_reaching_lower_edge_not_merely_entering_gap(low, closes):
    data, gate = bars(), TradingEligibility(2)
    gate.observe(opening())
    data[5] = replace(data[5], low=low)
    assert bool(gate.close_on_confirmation_gap_fill(data, 5)) is closes


def test_revocation_is_included_in_lifecycle_masks_and_cannot_close_another_confirmation():
    gate = TradingEligibility(2)
    event = opening()
    gate.observe(event)
    expired = gate.close_on_confirmation_gap_fill(bars(), 5)
    assert lifecycle_interval_days([event, *expired], 7, 2, up=True)[4:] == (True, False, False)
    fresh = TradingEligibility(2)
    fresh.observe(event)
    assert fresh.observe(dict(expired[0], confirmation_gap_lower=6.5)) is None
    assert fresh.active
