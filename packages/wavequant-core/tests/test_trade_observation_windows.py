"""资格窗口只推进本窗口交易观察，保留源几何锚点的全局索引。"""

from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from wavequant.domain.market_structure.a_wave import AWaveHistoryStream, AWaveSeed, a_wave_history
from wavequant.domain.market_structure.bottom_n_targets import (
    BottomNTargetHistoryStream, DeclineStart, PositiveNCompletion, bottom_n_target_history,
)
from wavequant.domain.market_structure.positive_n_reformation import PositiveNReformationHistory, PositiveNSeed
from wavequant.domain.market_structure.wave_projection import WaveProjectionSetup, wave_projection_history
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies import pressure_exit as pressure_module
from wavequant.domain.strategies import secondary_resistance as resistance_module
from wavequant.domain.strategies import trend_flip_exit as trend_module
from wavequant.domain.strategies.secondary_resistance import SecondaryResistanceStream
from wavequant.domain.strategies.pressure_exit import PressureExitStream, pressure_exit_history
from wavequant.domain.strategies.trend_flip_exit import TrendFlipExitStream, trend_flip_exit_history
from wavequant.domain.strategies.wave_projection_stream import WaveProjectionHistory


def market(rows):
    return [Bar(datetime(2026, 1, 1) + timedelta(days=index), 'TEST', *row, 1000)
            for index, row in enumerate(rows)]


def test_bottom_window_rebases_known_source_without_replaying_gap_retirement():
    bars = market([(11, 12, 10, 11), (6, 7, 5, 6), (7, 8, 6, 7), (8, 9, 7, 8),
                   (6, 7, 5.25, 6), (8, 9, 7, 8), (9, 10, 8, 9)])
    declines = [DeclineStart(0, 1)]
    completions = [PositiveNCompletion(1, 3, 3, 5.5)]
    assert bottom_n_target_history(bars, declines, completions).retirements[0].bar_index == 4
    stream = BottomNTargetHistoryStream(bars, start_index=5, end_index=6)
    result = stream.advance(6, declines, completions)
    assert result.source_at == (None,) * 5 + (3, 3)
    assert result.completions[3].known_at == 5
    assert result.qualifications[3].bottom_index == 1
    assert result.retirements == ()
    assert stream.processed_days == 2
    assert stream.advance(6, declines, completions) == result
    assert stream.processed_days == 2
    stream.advance(6, declines, [replace(completions[0], defense=6)])
    assert stream.processed_days == 4, 'source revisions replay this window only'
    with pytest.raises(ValueError, match='window'):
        stream.advance(4, declines, completions)


def test_reformation_window_does_not_inherit_an_unobserved_defense_break():
    bars = market([(6, 8, 5, 7), (8, 10, 7, 9), (8, 11, 6, 7),
                   (9, 12, 8, 11), (11, 13, 9, 12), (12, 14, 10, 13)])
    seed = PositiveNSeed(0, 1, 1, 7)
    stream = PositiveNReformationHistory(bars, start_index=3, end_index=4)
    assert stream.advance(3, [seed]) == ()
    assert stream.processed_days == 1
    assert stream.advance(4, [seed]) == ()
    assert stream.processed_days == 2
    assert next(iter(stream.families.values())).seed.known_at == 3
    assert stream.advance(4, [replace(seed, defense=6.5)]) == ()
    assert stream.processed_days == 4
    with pytest.raises(ValueError, match='window'):
        stream.advance(5, [seed])
    reopened = PositiveNReformationHistory(bars, start_index=5)
    assert reopened.advance(5, [seed]) == ()
    assert reopened.processed_days == 1

    class UncachedSeed(PositiveNSeed):
        def __reduce_ex__(self, protocol):
            raise AssertionError('unknown source must not invoke serialization hooks')

    unknown = UncachedSeed(seed.origin, seed.attack, seed.known_at, seed.defense)
    fallback = PositiveNReformationHistory(bars, start_index=3, end_index=4)
    assert fallback.advance(4, [unknown]) == ()
    assert fallback.processed_days == 2
    assert not fallback.families


def test_a_window_ignores_old_target_touch_and_first_day_wick_and_restarts_cleanly():
    bars = market([(8.5, 9, 8, 8.5), (9.5, 10, 9, 9.8), (10, 20, 9, 10),
                   (11, 16, 10, 11.5), (12, 13, 11, 12.5), (12, 15, 11, 11.5)])
    seed = AWaveSeed('source', 0, 1, 1, 12, 14, 9)
    assert a_wave_history(bars, [seed])[0].bar_index == 2
    stream = AWaveHistoryStream(bars, start_index=3, end_index=4)
    assert stream.advance(3, [seed]) == ()
    assert stream.processed_observations == 1
    events = stream.advance(4, [seed])
    assert [(event.bar_index, event.event) for event in events] == [(4, 'a_wave_confirmed')]
    assert events[0].a_class == 'ordinary'
    assert events[0].a_high_index == 3, 'the gap high cannot become this window A peak'
    assert stream.processed_observations == 2
    with pytest.raises(ValueError, match='window'):
        stream.advance(5, [seed])
    reopened = AWaveHistoryStream(bars, start_index=5)
    assert reopened.advance(5, [seed]) == ()
    assert reopened.processed_observations == 1
    late = AWaveHistoryStream(bars, start_index=3, end_index=4)
    assert late.advance(3, []) == ()
    assert late.advance(4, [seed]) == events
    assert late.processed_observations == 2
    revised = late.advance(4, [replace(seed, two_t=15)])
    assert [event.bar_index for event in revised] == [4]
    assert late.processed_observations == 4


@pytest.mark.parametrize('policy', ['named_stages', 'nearest_box'])
def test_projection_window_does_not_reuse_gap_targets_or_opening_wick(policy):
    bars = market([(8.5, 9, 8, 8.5), (9, 10, 9, 9.5), (10, 30, 9, 18),
                   (13, 16, 12, 13), (14, 15, 13, 14), (13, 25, 12, 13)])
    setup = WaveProjectionSetup(0, 1, 2, 8, 10, 14, 9)
    assert wave_projection_history(bars, setup, target_policy=policy)[0].bar_index == 2
    stream = WaveProjectionHistory(bars, start_index=3, end_index=4)
    reopened = WaveProjectionHistory(bars, start_index=5)
    try:
        assert stream.advance(setup, 3, target_policy=policy) == ()
        events = stream.advance(setup, 4, target_policy=policy)
        assert [(event.bar_index, event.event) for event in events] == [(4, 'wave_projection_ready')]
        assert events[0].a_high_index == 3
        assert stream.advance(setup, 3, target_policy=policy) == (), 'rewind remains within this window'
        with pytest.raises(ValueError, match='window'):
            stream.advance(setup, 5, target_policy=policy)
        assert reopened.advance(setup, 5, target_policy=policy) == ()
    finally:
        stream.close()
        reopened.close()


def test_secondary_window_keeps_the_known_key_without_replaying_old_pressure(monkeypatch):
    bars = market([(9, 10, 8, 9), (9, 9.5, 8.8, 9.4), (9.6, 11, 9.5, 10.1),
                   (10.2, 11.1, 10, 10.3), (10.4, 11.2, 10.3, 10.5)])
    history = {day: {2: [dict(index=0, kind='H', value=8)]} for day in range(len(bars))}
    keys = [dict(bar_index=0, event='hierarchy_resistance_key', key=dict(index=0, value=10))]
    observed = []
    original = resistance_module.observe_resistance

    def observe(previous, current, **options):
        observed.append(current.timestamp)
        return original(previous, current, **options)

    monkeypatch.setattr(resistance_module, 'observe_resistance', observe)
    stream = SecondaryResistanceStream(bars, start_index=2, end_index=3)
    reopened = SecondaryResistanceStream(bars, start_index=4)
    try:
        assert stream.advance(2, history, keys)[2]['secondary_high'] == 10
        assert observed == [bars[2].timestamp]
        assert stream.advance(3, history, keys)[3]['secondary_attack_date'] == '2026-01-03'
        assert observed == [bars[2].timestamp, bars[3].timestamp]
        with pytest.raises(ValueError, match='window'):
            stream.advance(4, history, keys)
        assert reopened.advance(4, history, keys) == {}, 'the prior window attack is not inherited'
        assert observed[-1] == bars[4].timestamp
        keys[0]['key']['value'] = 12
        assert stream.advance(3, history, keys) == {}, 'revised geometry rebuilds only this window'
        assert observed[-2:] == [bars[2].timestamp, bars[3].timestamp]
    finally:
        stream.close()
        reopened.close()


@pytest.mark.parametrize('level', [2, 3])
def test_trend_window_does_not_inherit_an_old_shadow_warning(level):
    bars = market([(9.5, 10, 9, 9.5), (9.1, 9.4, 8.5, 9), (9.5, 11, 9.4, 10.2),
                   (10.3, 11.2, 10, 10.5), (10.4, 10.6, 9.8, 10)])
    high = dict(index=0, kind='H', value=10, available_at=1)
    low = dict(index=1, kind='L', value=8.5, available_at=1)
    history = {day: {level: [dict(high), dict(low)]} for day in range(len(bars))}
    assert trend_flip_exit_history(bars, history)[4]['reason'] == 'trend_last_fall_high_lower_close_clear'
    stream = TrendFlipExitStream(bars, start_index=2, end_index=3)
    reopened = TrendFlipExitStream(bars, start_index=4)
    try:
        assert set(stream.advance(3, history)) == {2, 3}
        assert reopened.advance(4, history) == {}
        history[1][level][0]['available_at'] = 4
        assert stream.advance(3, history) == {}, 'a corrected entry anchor cannot leave an old warning'
        with pytest.raises(ValueError, match='window'):
            stream.advance(4, history)
    finally:
        stream.close()
        reopened.close()
    with pytest.raises(ValueError, match='closed'):
        stream.advance(3, history)


def test_secondary_c_target_only_observes_a_continuous_active_window(monkeypatch):
    bars = market([(8.5, 9, 8, 8.5), (9, 10, 8.8, 9.5), (9.4, 9.8, 9, 9.5),
                   (9.5, 10.6, 9.4, 10.1), (10.1, 11, 9.9, 10.2),
                   (10.6, 11.8, 9.8, 10.7), (10, 10.1, 9.2, 9.5)])
    levels = {1: [dict(index=1, kind='H', value=10, available_at=2),
                  dict(index=2, kind='L', value=9, available_at=2)],
              2: [dict(index=0, kind='L', value=8, available_at=0)]}
    history = {day: levels for day in range(len(bars))}
    expected = trend_flip_exit_history(bars, history)[6]
    assert expected['reason'] == 'secondary_wave_target_resistance_clear'
    observed = []
    original = trend_module.observe_resistance

    def observe(previous, current, **options):
        observed.append(current.timestamp)
        return original(previous, current, **options)

    monkeypatch.setattr(trend_module, 'observe_resistance', observe)
    stream = TrendFlipExitStream(bars, start_index=3)
    reopened = TrendFlipExitStream(bars, start_index=4)
    try:
        assert stream.advance(6, history) == {6: expected}
        assert min(observed) == bars[3].timestamp
        observed.clear()
        assert reopened.advance(6, history) == {}, 'C attack and target observations cannot cross windows'
        assert min(observed) == bars[4].timestamp
        assert stream.advance(3, history) == {}, 'rewind never enters the pre-window dates'
        assert stream.advance(6, history) == {6: expected}
    finally:
        stream.close()
        reopened.close()


def test_pressure_window_does_not_replay_old_supply_or_pre_window_context_edits(monkeypatch):
    bars = market([(9, 9.2, 8.8, 9)] * 24)
    bars[20] = replace(bars[20], open=10.5, high=11, low=9, close=9.2, volume=5000)
    bars[21] = replace(bars[21], open=9.2, high=9.4, low=9, close=9.3)
    bars[22] = replace(bars[22], open=9.4, high=10, low=9.4, close=9.9)
    bars[23] = replace(bars[23], open=9.9, high=10.3, low=9.85, close=9.95)
    context = [None] * 22 + [22, 22]
    assert pressure_exit_history(bars, context, StrategyConfig())[23]['pressure_date'] == '2026-01-21'
    original = pressure_module.observe_resistance
    observed = []

    def observe(previous, current, **options):
        observed.append(current.timestamp)
        return original(previous, current, **options)

    monkeypatch.setattr(pressure_module, 'observe_resistance', observe)
    stream = PressureExitStream(bars, StrategyConfig(), start_index=22)
    try:
        assert stream.advance(23, context) == ({}, {}, {})
        assert observed == [bars[22].timestamp, bars[23].timestamp]
        context[5] = 5
        stream.advance(23, context)
        assert len(observed) == 2, 'an unobserved old context cannot reset this window'
        context[22] = None
        assert stream.advance(23, context) == ({}, {}, {})
        assert observed[-2:] == [bars[22].timestamp, bars[23].timestamp]
        with pytest.raises(ValueError, match='window'):
            stream.advance(21, context[:22])
    finally:
        stream.close()


def test_record_risk_keeps_the_old_high_but_drops_previous_window_warning():
    bars = market([(9, 9.2, 8.8, 9)] * 23)
    bars[1] = replace(bars[1], open=9, high=10, low=8.9, close=9.9)
    bars[21] = replace(bars[21], open=10, high=11, low=9, close=9.5, volume=3000)
    bars[22] = replace(bars[22], open=9.6, high=9.8, low=9, close=9.1, volume=3000)
    context = [None] * len(bars)
    stream = PressureExitStream(bars, StrategyConfig(), start_index=21, end_index=21)
    continued = PressureExitStream(bars, StrategyConfig(), start_index=21)
    reopened = PressureExitStream(bars, StrategyConfig(), start_index=22)
    try:
        _, record, massive = stream.advance(21, context[:22])
        assert record[21]['record_high_date'] == '2026-01-02'
        assert massive[21]['reason'] == 'record_high_massive_resistance_reduce_30'
        assert continued.advance(22, context)[2][22]['reason'] == 'record_high_massive_followthrough_clear'
        assert reopened.advance(22, context) == ({}, {}, {})
        with pytest.raises(ValueError, match='window'):
            stream.advance(22, context)
    finally:
        stream.close()
        continued.close()
        reopened.close()
    with pytest.raises(ValueError, match='closed'):
        stream.advance(21, context[:22])


@pytest.mark.parametrize('factory', [BottomNTargetHistoryStream, PositiveNReformationHistory,
                                    AWaveHistoryStream, WaveProjectionHistory,
                                    SecondaryResistanceStream, TrendFlipExitStream,
                                    pytest.param(lambda bars, **bounds: PressureExitStream(
                                        bars, StrategyConfig(), **bounds), id='PressureExitStream')])
@pytest.mark.parametrize('bounds', [dict(start_index=True), dict(start_index=-1), dict(start_index=2),
                                  dict(start_index=1, end_index=0), dict(end_index=2)])
def test_observation_window_bounds_are_explicit_available_dates(factory, bounds):
    bars = market([(8.5, 9, 8, 8.5), (9.5, 10, 9, 9.8)])
    with pytest.raises(ValueError, match='window'):
        factory(bars, **bounds)
