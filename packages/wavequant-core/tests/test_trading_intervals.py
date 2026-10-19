"""Selected level, confirmed endpoints and causal open tails of B/S windows."""

from dataclasses import replace
from datetime import datetime

import pytest

from wavequant.domain.strategies.buy_intervals import confirmed_buy_interval_days
from wavequant.domain.strategies.trading_intervals import TradingEligibility, confirmed_trading_intervals, interval_days
from wavequant.domain.strategies import integrated_strategy as strategy_module
from tests.test_backtest_analysis_boundary import _bars, _config
from wavequant.domain.models.model import Bar


def state(now, name, *, level=2, path='p', kind='trend_lifecycle_state'):
    return dict(event=kind, trend_level=level, source_path=path, bar_index=now, state=name, flip_confirmed=True,
        flip_index=max(0, now - 2), flip_known_index=max(0, now - 1),
        counter_index=now - 1, alternation_index=now)


def test_eligibility_changes_only_on_confirmation_events_and_keeps_long_open_tail():
    eligibility = TradingEligibility(2)
    assert eligibility.observe(state(2, '空翻多')) is None
    assert eligibility.observe(state(3, '空多交替', kind='trend_lifecycle_candidate')) is None
    assert eligibility.intervals == ()
    assert eligibility.observe(state(4, '空多交替')) is True
    opened = eligibility.intervals
    assert eligibility.observe(state(999, '转多确认')) is None
    assert eligibility.intervals == opened
    assert eligibility.observe(state(1000, '多空交替', level=1)) is None
    assert eligibility.observe(state(1001, '多空交替')) is False
    assert eligibility.intervals[0].start_index == 4
    assert eligibility.intervals[0].end_index == 1001
    assert opened[0].end_index is None


@pytest.mark.parametrize('level', [1, 2, 3])
def test_same_level_bear_confirmation_closes_and_waits_for_new_qualification(level):
    eligibility = TradingEligibility(level)
    eligibility.observe(state(4, '空多交替', level=level))
    event = dict(event='trend_lifecycle_state', trend_level=level, source_path='p',
                 bar_index=10, available_index=10, state='转空确认', direction='down',
                 flip_confirmed=True, origin_index=4, flip_index=6, flip_known_index=7,
                 counter_index=8, alternation_index=9,
                 confirmation_rule='strict_close_cross_after_confirmed_alternation',
                 breakout_level=4.3, previous_close=4.5, confirmation_close=4.21)
    assert eligibility.observe(dict(event, available_index=11)) is None
    assert eligibility.active
    assert eligibility.observe(event) is False
    assert eligibility.intervals[0].end_index == 10
    assert eligibility.intervals[0].end_reason == 'same_level_bear_trend_confirmed'
    assert eligibility.observe(state(11, '空翻多', level=level)) is None
    assert not eligibility.active
    assert eligibility.observe(state(14, '空多交替', level=level)) is True
    assert eligibility.intervals[1].start_index == 14


@pytest.mark.parametrize('level', [1, 2, 3])
def test_formal_last_rise_low_break_closes_without_waiting_for_bear_alternation(level):
    # 4.30→4.21来自用户报价；索引及确认时钟是合成结构，不代表实股回放。
    from wavequant.domain.market_structure.trend_lifecycle import TrendLifecycle, _Fact, _Pivot

    book = TradingEligibility(level)
    book.observe(state(4, '空多交替', level=level))
    frozen = _Pivot(5, 0, 'L', 4.30, 6)
    origin = _Pivot(7, 0, 'H', 6.39, 8)
    attack = _Pivot(10, 0, 'L', 4.21, 10)
    events = []
    TrendLifecycle()._consume((level, 'p'), _Fact('翻多为空', attack, 10, origin, broken=frozen), 10, events)
    assert len(events) == 1
    assert events[0]['broken_key_price'] == 4.30
    assert book.observe(events[0]) is False
    assert book.intervals[0].end_index == 10
    assert book.intervals[0].end_key_index == 5
    assert book.intervals[0].end_key_price == 4.30
    assert book.observe(state(11, '空翻多', level=level)) is None
    assert not book.active
    assert book.observe(state(14, '空多交替', level=level)) is True


@pytest.mark.parametrize('change', [dict(breakout_level=4.30), dict(broken_key_known_index=10),
    dict(source_known_index=11), dict(flip_confirmed=False), dict(source_path='other'),
    dict(trend_level=1), dict(event='trend_lifecycle_candidate'), dict(broken_key_index=None)])
def test_last_rise_low_exit_rejects_touch_unknown_key_and_unrelated_evidence(change):
    book = TradingEligibility(2)
    book.observe(state(4, '空多交替'))
    event = dict(event='trend_lifecycle_state', state='多翻空', direction='down',
        source_event='翻多为空', flip_confirmed=True, trend_level=2, source_path='p',
        bar_index=10, available_index=10, broken_key_index=5, broken_key_known_index=6,
        broken_key_price=4.30, origin_index=7, flip_index=10, flip_known_index=10,
        source_known_index=10, breakout_level=4.21)
    event.update(change)
    assert book.observe(event) is None
    assert book.active


@pytest.mark.parametrize('level', [1, 2, 3])
@pytest.mark.parametrize('proof', [None, False, 'true'])
def test_alternation_without_explicit_flip_proof_cannot_authorize_trading(level, proof):
    event = state(4, '空多交替', level=level)
    if proof is None:
        event.pop('flip_confirmed')
    else:
        event['flip_confirmed'] = proof
    eligibility = TradingEligibility(level)
    assert eligibility.observe(event) is None
    assert not eligibility.active and eligibility.intervals == ()


def test_eligibility_source_invalidation_is_path_scoped_and_cannot_rewind():
    eligibility = TradingEligibility(2)
    eligibility.observe(state(2, '空多交替'))
    eligibility.observe(state(3, '空多交替', path='other'))
    assert eligibility.observe(dict(state(4, '空多交替', kind='trend_lifecycle_invalidated'),
                                    reason='source_path_ended')) is False
    assert eligibility.intervals[1].end_index is None
    with pytest.raises(ValueError, match='chronological'):
        eligibility.observe(state(1, '空多交替'))


@pytest.mark.parametrize('level', (1, 2, 3))
def test_reversal_low_break_closes_before_opposite_alternation_and_preserves_confirmation_clock(level):
    # Reduced structural fixture using the reported dates/prices, not a full stock replay.
    values = [
        ('2019-10-31', 6.48, 6.51, 6.30, 6.33),
        ('2019-12-31', 9.16, 10.09, 9.11, 9.93),
        ('2020-09-17', 11.89, 11.97, 11.53, 11.89),
        ('2020-10-23', 11.80, 12.00, 11.53, 11.80),
        ('2020-10-26', 11.63, 11.73, 11.27, 11.71),
    ]
    bars = [Bar(datetime.fromisoformat(day), 'sh.600812', op, hi, lo, cl, 100)
            for day, op, hi, lo, cl in values]
    dates = {bar.timestamp.date().isoformat(): index for index, bar in enumerate(bars)}
    opening = dict(state(1, '空多交替', level=level), counter_price=6.30)
    row = dict(trend_level=level, strokes=[dict(id='p', points=[
        dict(index=0, ordinal=0, kind='L', value=6.30, available_at=1, state='confirmed'),
        dict(index=2, ordinal=0, kind='L', value=11.53, available_at=3, state='confirmed'),
        # A low only confirmed today cannot replace the previously known break threshold.
        dict(index=4, ordinal=0, kind='L', value=11.27, available_at=4, state='confirmed'),
    ])])
    eligibility = TradingEligibility(level)
    eligibility.observe(opening)
    assert eligibility.intervals[0].start_low_index == 0
    assert eligibility.intervals[0].start_index == 1
    assert not eligibility.close_on_reversal_low_break(row, bars, 3, dates)
    events = eligibility.close_on_reversal_low_break(row, bars, 4, dates)
    assert len(events) == 1 and not eligibility.active
    assert events[0]['reversal_low_index'] == 2
    assert events[0]['market_low'] < events[0]['reversal_low_price'] < bars[4].close
    closed = eligibility.intervals[0]
    assert (closed.end_index, closed.end_key_index, closed.end_reason) == (4, 2, 'reversal_low_broken')
    assert confirmed_trading_intervals([opening, *events], 5, level) == eligibility.intervals
    assert interval_days(eligibility.intervals, 5) == (False, True, True, True, False)
    assert not eligibility.close_on_reversal_low_break(row, bars, 4, dates)


def test_touch_future_low_and_other_path_cannot_close_selected_interval():
    bars = _bars()[:5]
    bars = [replace(bar, low=11.53, high=12.0, open=11.8, close=11.8) for bar in bars]
    eligibility = TradingEligibility(2)
    eligibility.observe(state(2, '空多交替'))
    point = dict(index=2, kind='L', value=11.53, available_at=3, state='confirmed')
    row = dict(trend_level=2, strokes=[dict(id='p', points=[point])])
    assert not eligibility.close_on_reversal_low_break(row, bars, 4, {})
    bars[4] = replace(bars[4], low=11.27)
    assert not eligibility.close_on_reversal_low_break(dict(row, trend_level=1), bars, 4, {})
    assert not eligibility.close_on_reversal_low_break(
        dict(trend_level=2, strokes=[dict(id='other', points=[point])]), bars, 4, {})
    assert not eligibility.close_on_reversal_low_break(
        dict(trend_level=2, strokes=[dict(id='p', points=[dict(point, available_at=4)])]), bars, 4, {})
    assert not eligibility.close_on_reversal_low_break(
        dict(trend_level=2, strokes=[dict(id='p', points=[dict(point, state='developing')])]), bars, 4, {})
    assert eligibility.active


@pytest.mark.parametrize('level', (1, 2, 3))
def test_selected_level_opens_at_bull_alternation_and_waits_for_bear_alternation(level):
    events = [state(2, '空多交替', level=level), state(3, '多翻空', level=level),
        dict(state(4, '空多交替', level=level, kind='trend_lifecycle_invalidated'), reason='defense_broken'),
        state(5, '多空交替', level=level, kind='trend_lifecycle_candidate'),
        state(7, '多空交替', level=level)]
    intervals = confirmed_trading_intervals(events, 10, level)
    assert len(intervals) == 1
    assert (intervals[0].start_index, intervals[0].end_index) == (2, 7)
    assert interval_days(intervals, 10) == (False, False, True, True, True, True, True, False, False, False)
    assert interval_days(intervals, 10, include_end=True)[7] is True
    # Missing legacy contexts must not shrink the user's selected interval.
    assert confirmed_buy_interval_days({}, 10, level, events=events, require_lifecycle=True) == interval_days(intervals, 10)
    assert not confirmed_trading_intervals(events, 10, 1 if level != 1 else 3)


def test_future_endpoint_does_not_remove_open_tail_or_change_past_buy_days():
    events = [state(2, '空多交替'), state(5, '多翻空'), state(8, '多空交替')]
    prefix = confirmed_trading_intervals(events, 7, 2)
    full = confirmed_trading_intervals(events, 10, 2)
    assert prefix[0].end_index is None
    assert interval_days(prefix, 7) == interval_days(full, 10)[:7]


def test_distinct_paths_and_cycles_are_not_joined():
    events = [state(2, '空多交替'), state(3, '多空交替', path='other'),
              state(5, '多空交替'), state(7, '空多交替'), state(9, '多空交替')]
    assert [(row.start_index, row.end_index) for row in confirmed_trading_intervals(events, 11, 2)] == [(2, 5), (7, 9)]


def test_candidates_future_knowledge_and_source_revision_cannot_authorize_trading():
    assert not confirmed_trading_intervals([state(2, '空多交替', kind='trend_lifecycle_candidate')], 6, 2)
    assert not confirmed_trading_intervals([dict(state(2, '空多交替'), flip_known_index=4)], 6, 2)
    events = [state(2, '空多交替'), dict(state(4, '空多交替', kind='trend_lifecycle_invalidated'),
                                       reason='source_evidence_revised')]
    assert confirmed_trading_intervals(events, 6, 2)[0].end_reason == 'source_evidence_revised'


def test_no_selected_interval_skips_policy_scan(monkeypatch):
    import wavequant.domain.strategies.backtest_analysis as analysis_module

    selected = []

    def no_intervals(self, level):
        selected.append(level)
        return ()

    def forbidden(*args, **kwargs):
        raise AssertionError('policy scan must not run without a selected interval')

    monkeypatch.setattr(analysis_module.MarketCalculations, 'trading_intervals', no_intervals)
    monkeypatch.setattr(strategy_module, '_generate_system_signals', forbidden)
    result = strategy_module.generate_system_signals(_bars(), replace(_config(), buy_interval_level=3))
    assert selected == [3]
    assert result.signals == []
    assert result.counts['trading_interval_count'] == 0
