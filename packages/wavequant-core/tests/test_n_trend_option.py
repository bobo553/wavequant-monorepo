"""N-target trend certification is an explicit, isolated global preference."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from typing import Protocol, cast

import pytest

from wavequant.domain.market_structure.n_trend_confirmation import N_TARGET_CONFIRMATION
from wavequant.domain.market_structure.n_trend_reversals import n_target_reversals
from wavequant.domain.market_structure.hierarchical_development import hierarchical_developing_path
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


def _drawing(bars: Sequence[Bar]) -> dict[str, object]:
    draw = cast(Callable[[Sequence[Bar]], dict[str, object]], lecture_drawing)
    return draw(bars)


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
    # N只授予独立策略资格；选中它不能把无普通/严格结构证明的点塞进几何。
    assert _points(checked) == _points(default)
    assert not any(point['index'] == 1 for point in _points(default, 'qualified_strokes'))
    origin = next(point for point in _points(checked, 'qualified_strokes') if point['index'] == 1)
    proof = origin['trend_confirmation']
    assert isinstance(proof, dict)
    assert proof['confirmation_rule'] == N_TARGET_CONFIRMATION
    assert proof['direction'] == ('up' if up else 'down')
    assert proof['one_p_target'] == (10 if up else 20)
    assert proof['available_at'] == origin['available_at'] == '2024-01-11'
    equal = _public(level, bars[:10], source, up=up, enabled=True)
    assert not any(point['index'] == 1 for point in _points(equal, 'qualified_strokes'))


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
        cast(Callable[[], None], config.validate)()


def test_every_research_profile_defaults_to_unselected() -> None:
    legacy: dict[str, object] = {'scenarios': {'base': {'execution': {}}}}
    for build in (research_profile, hierarchical_profile, whole_wave_profile):
        assert build(legacy)['strategy']['n_target_trend_confirmation_enabled'] is False
    original = SystemStrategy()
    selected = replace(original, n_target_trend_confirmation_enabled=True)
    cast(Callable[[], None], selected.validate)()
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


def test_real_september_down_endpoint_requires_separate_selected_n_up_permission(xiangyang: tuple[Bar, ...]) -> None:
    bars = [bar for bar in xiangyang if bar.timestamp.date().isoformat() <= '2018-09-25']
    drawing = _drawing(bars)
    default = reversal_trends(drawing, bars)
    unchecked = reversal_trends(drawing, bars, n_target_trend_confirmation_enabled=False)
    selected = reversal_trends(drawing, bars, n_target_trend_confirmation_enabled=True)
    assert default == unchecked
    geometry = next(point for point in _points(default) if point['time'] == '2018-09-11')
    assert (geometry['kind'], geometry['value'], geometry['available_at']) == ('L', 4.94, '2018-09-12')
    assert 'incoming_trend_confirmation' not in geometry and 'trend_confirmation' not in geometry
    assert _points(selected) == _points(default)
    low = next(point for point in _points(default, 'qualified_strokes') if point['time'] == '2018-09-11')
    # 证书来自独立资格视图。9/10低价5.05已严格破9/3低点5.07，9/11
    # 作为下降端点延续；随后9/18普通本级突破先成立，9/20才有独立N达标。
    down = low['incoming_trend_confirmation']
    assert isinstance(down, dict) and down['direction'] == 'down'
    assert down['confirmation_rule'] == 'strict_same_level_market_key_break'
    assert down['available_at'] == '2018-09-10'
    key = down['broken_key']
    assert isinstance(key, dict) and (key['time'], key['value'], key['available_at']) == ('2018-09-03', 5.07, '2018-09-04')
    trigger = down['confirmed_by']
    assert isinstance(trigger, dict) and (trigger['time'], trigger['value']) == ('2018-09-10', 5.05)
    ordinary = low['trend_confirmation']
    assert isinstance(ordinary, dict) and ordinary['confirmation_rule'] == 'strict_same_level_market_key_break'
    assert (ordinary['direction'], ordinary['available_at']) == ('up', '2018-09-18')
    up_key = ordinary['broken_key']
    assert isinstance(up_key, dict) and (up_key['time'], up_key['value'], up_key['available_at']) == ('2018-09-06', 5.23, '2018-09-12')
    assert low.get('n_target_confirmation') is None
    selected_low = next(point for point in _points(selected, 'qualified_strokes') if point['time'] == '2018-09-11')
    assert selected_low['trend_confirmation'] == ordinary
    proof = selected_low['n_target_confirmation']
    assert isinstance(proof, dict) and proof['confirmation_rule'] == N_TARGET_CONFIRMATION
    assert (proof['direction'], proof['available_at'], proof['one_p_target']) == ('up', '2018-09-20', 5.32)
    attack = proof['confirmed_by']
    assert isinstance(attack, dict) and (attack['time'], attack['value']) == ('2018-09-20', 5.34)
    prefix = [bar for bar in bars if bar.timestamp.date().isoformat() <= '2018-09-19']
    before_n = reversal_trends(_drawing(prefix), prefix, n_target_trend_confirmation_enabled=True)
    before_low = next(point for point in _points(before_n, 'qualified_strokes') if point['time'] == '2018-09-11')
    assert before_low.get('n_target_confirmation') is None and before_low['trend_confirmation'] == ordinary
    assert not any(isinstance(point.get('n_target_confirmation'), dict)
                   and cast(dict[str, object], point['n_target_confirmation']).get('available_at') == '2018-09-20'
                   for point in _points(before_n, 'qualified_strokes'))
    # 纯结构发展线没有许可证；独立已获证书的波用真实market前缀求live极值。
    display = next(stroke for stroke in _records(selected['developing_strokes'])
                if _records(stroke['points'])[0]['time'] == '2018-09-11')
    assert display['display_only'] is True and 'confirmation' not in display
    path = display['source_path']
    source_stroke = next(stroke for stroke in _records(selected['source_strokes']) if stroke['id'] == path)
    source_view = dict(source_stroke, points=[dict(point, label=point.get('label', point['kind']))
                                           for point in _records(source_stroke['points'])])
    permission_stroke = next(stroke for stroke in _records(selected['qualified_strokes']) if stroke['source_path'] == path)
    candidate_stroke = next(stroke for stroke in _records(selected['candidate_strokes']) if stroke['source_path'] == path)
    develop = cast(Callable[..., dict[str, object] | None], hierarchical_developing_path)
    tail = develop(source_view, _records(permission_stroke['points']), trend_level=1, source_level=0,
                   kind='reversal', bars=bars, structural=_records(candidate_stroke['points']),
                   n_target_trend_confirmation_enabled=True)
    assert tail is not None
    assert [(point['time'], point['value']) for point in _records(tail['points'])] == [
        ('2018-09-11', 4.94), ('2018-09-25', 5.78),
    ]
    live_proof = tail['confirmation']
    assert isinstance(live_proof, dict) and live_proof['available_at'] == '2018-09-18'
    assert live_proof['confirmation_rule'] == 'strict_same_level_market_key_break'
