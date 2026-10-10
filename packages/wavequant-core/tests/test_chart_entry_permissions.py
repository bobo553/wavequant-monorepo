"""策略历史不能把纯趋势几何或空资格 fallback 当作已发布交易来源。"""

from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from datetime import datetime, timedelta
from typing import cast

import pytest

from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.chart_entry_history import _strategy_permission_view, chart_entry_history


def _level(level: int, *, licensed: bool) -> dict[str, object]:
    time = '2024-01-01'
    geometry = dict(index=0, ordinal=0, kind='H', value=70 + level, time=time,
                    available_at=time, state='reversal', trend_level=level)
    path: dict[str, object] = dict(id=f'level-{level}', source_path=f'level-{level - 1}', points=[geometry])
    permissions = [dict(path, points=[dict(geometry, value=8,
        incoming_trend_confirmation=dict(direction='up', available_at=time, label='producer-permission'))])] if licensed else []
    return dict(trend_level=level, strokes=[path], qualified_strokes=permissions,
                candidate_strokes=[], structure_strokes=[], bear_to_bull_highs=[], bear_bull_alternation_lows=[])


@pytest.mark.parametrize('licensed', [False, True])
def test_chart_recurses_geometry_then_supplies_only_explicit_permissions_to_strategy_consumers(
    monkeypatch: pytest.MonkeyPatch, licensed: bool,
) -> None:
    bars = [Bar(datetime(2024, 1, 1) + timedelta(days=i), 'test', 6, 8, 4, 6, 100) for i in range(3)]
    geometry_levels = [_level(level, licensed=licensed) for level in (1, 2, 3)]
    before = deepcopy(geometry_levels)
    recursive_inputs: list[tuple[int, list[Mapping[str, object]]]] = []
    permission_inputs: list[tuple[int, Sequence[Mapping[str, object]] | None]] = []
    strategy_inputs: list[tuple[Mapping[str, object], Mapping[str, object]]] = []

    def prepare(source: Sequence[Mapping[str, object]], *, trend_level: int = 1,
                symbol: str = 'STRUCTURE') -> dict[str, object]:
        if trend_level > 1:
            recursive_inputs.append((trend_level, list(source)))
        return dict(geometry_levels[trend_level - 1], qualified_strokes=[])

    def attach(prepared: Mapping[str, object], market: Sequence[Bar], *,
               qualified_source_points: Sequence[Mapping[str, object]] | None = None,
               n_target_trend_confirmation_enabled: bool = False) -> dict[str, object]:
        level = cast(int, prepared['trend_level'])
        permission_inputs.append((level, qualified_source_points))
        return geometry_levels[level - 1]

    def drawing(market: Sequence[Bar], *, on_step: Callable[[int, int, object], None]) -> None:
        raw = [dict(index=0, ordinal=0, kind='H', value=8, time='2024-01-01',
                    available_at='2024-01-01', state='confirmed')]
        for index in range(len(market)):
            on_step(index, 0, raw)

    def anchors(level: Mapping[str, object], dates: object, source: Mapping[str, object]) -> list[dict[str, object]]:
        strategy_inputs.append((level, source))
        return []

    # 边界生产者提供两份明确不同的视图，本回归验证真实chart_entry_history
    # 调用链的选择职责；不假装这套小夹具重新证明市场许可证的数值规则。
    module = 'wavequant.domain.strategies.chart_entry_history'
    monkeypatch.setattr(f'{module}.prepare_trend_lines', prepare)
    monkeypatch.setattr(f'{module}.attach_trend_permissions', attach)
    monkeypatch.setattr(f'{module}.lecture_drawing', drawing)
    monkeypatch.setattr('wavequant.domain.market_structure.squeeze_alternation.squeeze_anchors', anchors)

    history, events = chart_entry_history(bars)

    assert {level for level, _ in recursive_inputs} == {2, 3}
    for level, points in recursive_inputs:
        paths = cast(Sequence[Mapping[str, object]], geometry_levels[level - 2]['strokes'])
        expected = [dict(point, source_path=path['id']) for path in paths
                    for point in cast(Sequence[Mapping[str, object]], path['points'])]
        assert points == expected
    assert {level for level, _ in permission_inputs} == {1, 2, 3}
    for level, source in permission_inputs:
        if level > 1:
            assert source is not None
            assert [point['value'] for point in source] == ([8] if licensed else [])
    assert strategy_inputs
    for strategy_level, strategy_source in strategy_inputs:
        assert strategy_level['strokes'] == strategy_level['qualified_strokes']
        assert strategy_source['strokes'] == strategy_source['qualified_strokes']
    keys = [event['key'] for event in events if event['event'] == 'hierarchy_resistance_key']
    if licensed:
        assert keys and cast(Mapping[str, object], keys[0])['value'] == 8
    else:
        assert keys == []
    assert all(contexts == () for contexts in history.values())
    assert geometry_levels == before


def test_explicit_empty_permission_keeps_candidate_pressure_but_cannot_fallback_to_geometry() -> None:
    level = _level(2, licensed=False)
    level['structure_strokes'] = [dict(id='private', points=[dict(kind='H', wave_direction_after='down')])]
    view = _strategy_permission_view(level)
    assert view['strokes'] == []
    assert view['structure_strokes'] == level['structure_strokes']
    assert level['strokes']


def test_legacy_qualified_contract_is_preserved_and_malformed_new_permissions_fail_closed() -> None:
    legacy = _level(2, licensed=True)
    legacy.pop('qualified_strokes')
    assert _strategy_permission_view(legacy)['strokes'] == legacy['strokes']
    malformed = dict(legacy, qualified_strokes=None)
    with pytest.raises(ValueError, match='qualified_strokes'):
        _strategy_permission_view(malformed)
