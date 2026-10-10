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
    recursive_inputs: list[object] = []
    strategy_inputs: list[tuple[Mapping[str, object], Mapping[str, object]]] = []

    def first(drawing: object, market: Sequence[Bar], **options: object) -> dict[str, object]:
        return geometry_levels[0]

    def second(source: Mapping[str, object], market: Sequence[Bar], **options: object) -> dict[str, object]:
        recursive_inputs.append(source['strokes'])
        return geometry_levels[1]

    def third(source: Mapping[str, object], market: Sequence[Bar], **options: object) -> dict[str, object]:
        recursive_inputs.append(source['strokes'])
        return geometry_levels[2]

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
    monkeypatch.setattr(f'{module}.reversal_trends', first)
    monkeypatch.setattr(f'{module}.secondary_trends', second)
    monkeypatch.setattr(f'{module}.tertiary_trends', third)
    monkeypatch.setattr(f'{module}.lecture_drawing', drawing)
    monkeypatch.setattr('wavequant.domain.market_structure.squeeze_alternation.squeeze_anchors', anchors)

    history, events = chart_entry_history(bars)

    assert recursive_inputs and recursive_inputs[0] == geometry_levels[0]['strokes']
    assert recursive_inputs[1] == geometry_levels[1]['strokes']
    assert strategy_inputs
    for level, source in strategy_inputs:
        assert level['strokes'] == level['qualified_strokes']
        assert source['strokes'] == source['qualified_strokes']
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
