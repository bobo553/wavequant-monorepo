"""N-target trend certification is an explicit, isolated global preference."""

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Protocol, cast

import pytest

from wavequant.domain.market_structure.n_trend_confirmation import N_TARGET_CONFIRMATION
from wavequant.domain.market_structure.n_trend_reversals import n_target_reversals
from wavequant.domain.market_structure.lecture_drawing import lecture_drawing
from wavequant.domain.market_structure.lecture_trend import reversal_trends
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.chart_entry_history import chart_entry_history, _has_confirmation_price_cross
from wavequant.domain.strategies.hierarchical_entry import hierarchical_history
from wavequant.domain.strategies.integrated_strategy import SystemStrategy
from wavequant.domain.strategies.strategy_profiles import hierarchical_profile, research_profile, whole_wave_profile

from .test_n_trend_levels import _points, _public, _sample
from .test_n_target_trend_real import xiangyang, _records


class _Replay(Protocol):
    def __call__(self, bars: Sequence[Bar], *, prefix_cache: dict[str, object] | None = None,
                 n_target_trend_confirmation_enabled: bool = False) -> object: ...


@pytest.mark.parametrize('level', (1, 2, 3))
@pytest.mark.parametrize('up', (True, False))
def test_default_and_unchecked_n_targets_do_not_qualify_any_level(level: int, up: bool) -> None:
    bars, source = _sample(up=up)
    default = _public(level, bars, source, up=up, enabled=None)
    unchecked = _public(level, bars, source, up=up, enabled=False)
    checked = _public(level, bars, source, up=up, enabled=True)

    assert default == unchecked
    assert default['n_target_trend_confirmation_enabled'] is False
    assert not default['n_target_observations']
    assert not any(point['index'] == 1 for point in _points(default))
    proof = next(point['trend_confirmation'] for point in _points(checked) if point['index'] == 1)
    assert isinstance(proof, dict)
    assert proof['confirmation_rule'] == N_TARGET_CONFIRMATION
    assert proof['direction'] == ('up' if up else 'down')
    assert proof['one_p_target'] == (10 if up else 20)


@pytest.mark.parametrize('source_level', (0, 1, 2))
@pytest.mark.parametrize('up', (True, False))
def test_n_candidate_enhancement_requires_explicit_selection(source_level: int, up: bool) -> None:
    bars, source = _sample(up=up)
    assert n_target_reversals([], source, bars, source_level=source_level) == []
    assert n_target_reversals([], source, bars, source_level=source_level,
                             n_target_trend_confirmation_enabled=False) == []
    selected = n_target_reversals([], source, bars, source_level=source_level,
                                 n_target_trend_confirmation_enabled=True)
    proof = selected[0]['n_target_confirmation']
    assert isinstance(proof, dict) and proof['confirmation_rule'] == N_TARGET_CONFIRMATION


@pytest.mark.parametrize('value', (None, 0, 1, 'false', 'true'))
def test_strategy_requires_an_actual_boolean_option(value: object) -> None:
    config = SystemStrategy(n_target_trend_confirmation_enabled=cast(bool, value))
    with pytest.raises(ValueError, match='N target trend confirmation'):
        config.validate()


def test_every_research_profile_defaults_to_unselected() -> None:
    legacy: dict[str, object] = {'scenarios': {'base': {'execution': {}}}}
    for build in (research_profile, hierarchical_profile, whole_wave_profile):
        assert build(legacy)['strategy']['n_target_trend_confirmation_enabled'] is False
    original = SystemStrategy()
    selected = replace(original, n_target_trend_confirmation_enabled=True)
    selected.validate()
    assert selected.n_target_trend_confirmation_enabled is True
    assert original.n_target_trend_confirmation_enabled is False


@pytest.mark.parametrize('up', (True, False))
def test_unchecked_targets_do_not_trigger_extra_market_only_replays(up: bool) -> None:
    bars, source = _sample(up=up)
    selected = _public(1, bars[:10], source, up=up)
    levels: tuple[tuple[Mapping[str, object], Mapping[str, object]], ...] = ((selected, {}),)
    args = ((), levels, bars[9], bars[10], '2024-01-11', 10)
    assert not _has_confirmation_price_cross(*args)
    assert _has_confirmation_price_cross(*args, n_target_trend_confirmation_enabled=True)


@pytest.mark.parametrize('first_mode', (True, False))
@pytest.mark.parametrize('builder', (hierarchical_history, chart_entry_history))
def test_replay_cache_switching_matches_a_cold_replay(builder: _Replay, first_mode: bool) -> None:
    bars, _ = _sample()
    cache: dict[str, object] = {}
    initial = builder(bars, prefix_cache=cache, n_target_trend_confirmation_enabled=first_mode)
    first_key = cache['key']
    switched = builder(bars, prefix_cache=cache, n_target_trend_confirmation_enabled=not first_mode)
    assert cache['key'] != first_key
    assert switched == builder(bars, n_target_trend_confirmation_enabled=not first_mode)
    assert builder(bars, prefix_cache=cache, n_target_trend_confirmation_enabled=first_mode) == initial


def test_real_september_segment_is_visible_only_when_the_option_is_selected(xiangyang: tuple[Bar, ...]) -> None:
    bars = [bar for bar in xiangyang if bar.timestamp.date().isoformat() <= '2018-09-25']
    drawing = lecture_drawing(bars)
    default = reversal_trends(drawing, bars)
    unchecked = reversal_trends(drawing, bars, n_target_trend_confirmation_enabled=False)
    selected = reversal_trends(drawing, bars, n_target_trend_confirmation_enabled=True)
    assert default == unchecked
    assert not any(point['time'] == '2018-09-11' for point in _points(default))
    tail = next(stroke for stroke in _records(selected['developing_strokes'])
                if _records(stroke['points'])[0]['time'] == '2018-09-11')
    assert [(point['time'], point['value']) for point in _records(tail['points'])] == [
        ('2018-09-11', 4.94), ('2018-09-25', 5.78),
    ]
    proof = tail['confirmation']
    assert isinstance(proof, dict) and proof['available_at'] == '2018-09-20'
