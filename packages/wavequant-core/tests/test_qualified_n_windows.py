"""Trading targets consume only their own confirmed window; no closed-gap replay."""

from dataclasses import replace

import pytest

from .test_eligible_polyline_n import opening, point
from .test_n_shape import fixture, setup
from .test_trend_lifecycle import scenario
from wavequant.domain.market_state.control_bar import observe_control_bar
from wavequant.domain.market_state.market_regime import RegimePolicy, WaveBoundary, observe_market_regime
from wavequant.domain.market_structure import n_shape
from wavequant.domain.market_structure.n_shape import MilestoneBasis, observe_n
from wavequant.domain.market_structure.price_action import ShadowPolicy
from wavequant.domain.market_structure.trend_lifecycle import TrendLifecycle
from wavequant.domain.strategies.eligible_polyline_n import EligibleNSource, EligiblePolylineN
from wavequant.domain.strategies.integrated_strategy import SystemStrategy
from wavequant.domain.strategies.polyline_target_stream import PolylineTargetStream


def source(known: int) -> EligibleNSource:
    return EligibleNSource((point(0, 'L', 8), point(1, 'H', 12), point(2, 'L', 10)),
                           known, 'p', known, 0, 0)


def test_first_eligible_close_does_not_inherit_earlier_target_touches():
    bars = fixture()
    bars[6] = replace(bars[6], open=14, low=13, close=13.8)
    observed = observe_n(bars, setup(), timeframe='1d', milestone_basis=MilestoneBasis.EXTREME,
                         measurement_start=6)
    assert observed.completion is not None and observed.completion.bar_index == 3
    assert observed.targets is not None and observed.targets.one_p == 17.2
    assert observed.milestones == ()
    first = observe_n(bars[:6], setup(), timeframe='1d', milestone_basis=MilestoneBasis.EXTREME,
                      measurement_start=5)
    assert [(hit.name, hit.bar_index, hit.observation_kind) for hit in first.milestones] == [
        ('equal_wave', 5, 'eligibility_close')]


def test_reopened_target_does_not_inherit_a_closed_gap_defense_break():
    bars = fixture()
    bars[4] = replace(bars[4], low=7)
    bars[6] = replace(bars[6], open=14, low=7, high=18, close=17.5)
    observed = observe_n(bars, setup(), timeframe='1d', milestone_basis=MilestoneBasis.EXTREME,
                         measurement_start=6)
    assert observed.first_defense_breach_index is None
    assert observed.first_origin_breach_index is None
    assert observed.milestones and all(hit.bar_index == 6 for hit in observed.milestones)
    assert all(hit.observation_kind == 'eligibility_close' for hit in observed.milestones)


def test_closed_window_does_not_advance_targets_and_reopening_is_independent():
    bars = fixture()
    bars[6] = replace(bars[6], open=14, low=13, close=13.8)
    rows = []
    stream = PolylineTargetStream(bars, SystemStrategy(), publish=rows.append)
    for now in range(4):
        stream.advance(now, (), buy_eligible=False, active_scopes=set())
    assert stream.processed_observations == 0 and rows == []
    first, reopened = source(4), source(6)
    stream.advance(4, (first,), active_scopes={first.scope})
    frozen = stream.current_scoped_observations()[first, 4]
    assert frozen.milestones == ()
    stream.advance(5, (), buy_eligible=False, active_scopes=set())
    assert stream.processed_observations == 1
    assert stream.current_scoped_observations() == {(first, 4): frozen}
    stream.advance(6, (reopened,), active_scopes={reopened.scope})
    assert stream.processed_observations == 2
    assert stream.scoped_observations[first, 4] == frozen
    assert stream.scoped_observations[reopened, 6].milestones == ()
    assert [row['bar_index'] for row in rows] == [4, 6]
    stream.finish()


def test_closing_one_overlapping_scope_does_not_stop_another():
    bars = fixture()
    left, right = source(3), replace(source(3), source_path='right')
    stream = PolylineTargetStream(bars, SystemStrategy())
    for now in range(3):
        stream.advance(now, (), buy_eligible=False, active_scopes=set())
    stream.advance(3, (left, right), active_scopes={left.scope, right.scope})
    stream.advance(4, (), active_scopes={right.scope})
    assert (left, 3) in stream.scoped_observations
    assert (right, 4) in stream.current_scoped_observations()
    assert stream.processed_observations == 3


def test_reopened_geometry_has_a_new_source_scope():
    stream = EligiblePolylineN(2)
    points = (point(1, 'L', 10), point(2, 'H', 14), point(3, 'L', 12))
    for now in range(4):
        stream.advance(now, (), ())
    first = stream.advance(4, points, (opening(4, 1),))[0]
    stream.advance(5, points, (dict(opening(5, 1), state='多空交替'),))
    second = stream.advance(6, points, (opening(6, 1),))[0]
    assert first.points == second.points and first.scope != second.scope
    assert (first.known_at, second.known_at) == (4, 6)


def test_qualification_n_reuses_formation_without_computing_targets(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('qualification N must not compute trading goals or form the same N again')

    monkeypatch.setattr(n_shape, 'project_n_targets', forbidden)
    bars = fixture()
    formation = observe_n(bars, setup(), timeframe='1d', milestone_basis=MilestoneBasis.EXTREME,
                          measure_targets=False)
    assert formation.completion is not None
    assert formation.targets is None and formation.milestones == ()
    from wavequant.domain.market_state import control_bar, market_regime
    monkeypatch.setattr(control_bar, 'observe_n', forbidden)
    monkeypatch.setattr(market_regime, 'observe_n', forbidden)
    control = observe_control_bar(bars, setup(), timeframe='1d', volume_lookback=2,
                                  shadow_policy=ShadowPolicy(.5), measurement_start=5, _prepared_n=formation)
    regimes = observe_market_regime(bars, setup(), timeframe='1d',
                                    policy=RegimePolicy(ShadowPolicy(.5), WaveBoundary.ORIGIN),
                                    measurement_start=5, _prepared_n=formation)
    assert [frame.bar_index for frame in control.defense_frames] == [5, 6]
    assert [frame.bar_index for frame in regimes.frames] == [5, 6]


@pytest.mark.parametrize('level', (1, 2, 3))
def test_internal_n_gate_requires_this_levels_confirmed_flip_and_pending_alternation(level):
    bars, geometry = scenario(level)
    lifecycle = TrendLifecycle()
    dates = {bar.timestamp.date().isoformat(): index for index, bar in enumerate(bars)}
    for now in range(5):
        lifecycle.advance([geometry], bars, now, dates)
        assert lifecycle.pending_bull_paths(level) == (frozenset({'path'}) if now in (2, 3) else frozenset())
        assert lifecycle.pending_bull_paths(1 if level != 1 else 2) == frozenset()


def test_confirmed_body_gap_does_not_request_n_or_regime_observers(monkeypatch):
    from .test_body_gap_alternation import sample
    from wavequant.domain.market_structure import bull_alternation_confirmation as confirmation
    from wavequant.domain.market_structure.trend_lifecycle import _Pivot

    def forbidden(*args, **kwargs):
        raise AssertionError('an already sufficient body-gap proof must not request N/squeeze work')

    monkeypatch.setattr(confirmation, 'observe_n', forbidden)
    monkeypatch.setattr(confirmation, 'observe_control_bar', forbidden)
    monkeypatch.setattr(confirmation, 'observe_market_regime', forbidden)
    bars, origin, high, points = sample()
    args = dict(origin=origin, high=high, broken=_Pivot(0, 0, 'H', 4.09, 0),
                points=points, polyline=points, level=2, path='p')
    assert confirmation.confirm_bull_alternation(bars, 5, **args, flip_confirmed_at=6) is None
    proof = confirmation.confirm_bull_alternation(bars, 5, **args, flip_confirmed_at=1)
    assert proof is not None and proof.rule == 'body_gap_volume_bullish_breakout_after_defended_low'


def test_suspicion_and_orphan_alternation_do_not_request_internal_n(monkeypatch):
    from wavequant.domain.market_structure import trend_lifecycle

    def forbidden(*args, **kwargs):
        raise AssertionError('this level has not confirmed a bullish flip')

    monkeypatch.setattr(trend_lifecycle, 'confirm_bull_alternation', forbidden)
    bars, geometry = scenario(2)
    geometry['strokes'][0]['points'][1]['observations'] = []
    lifecycle = TrendLifecycle()
    dates = {bar.timestamp.date().isoformat(): index for index, bar in enumerate(bars)}
    for now in range(len(bars)):
        lifecycle.advance([geometry], bars, now, dates)
        assert not lifecycle.pending_bull_paths(2)
