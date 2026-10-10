"""局部组合 A 测幅不能误走正式趋势级别递归入口。"""

from copy import deepcopy
from datetime import date, timedelta
from typing import cast

import pytest

from wavequant.domain.market_structure.trend_levels import trend_reversals
from wavequant.domain.strategies.combined_a_entry import GeometryPoint, _reduce_local_points


def test_local_key_geometry_accepts_first_level_points_without_relabeling_the_formal_source() -> None:
    values = [20, 30, 10, 20, 14, 26, 18, 35, 28, 45, 36, 42, 30, 36, 24, 31, 26, 50]
    start = date(2026, 1, 1)
    raw: list[dict[str, object]] = [
        dict(index=index, ordinal=0, time=(start + timedelta(days=index)).isoformat(),
             kind='L' if index % 2 == 0 else 'H', value=value,
             available_at=(start + timedelta(days=index + 1)).isoformat(),
             label=f'p{index}', state='reversal', trend_level=1)
        for index, value in enumerate(values)
    ]
    before = deepcopy(raw)
    # GeometryPoint 是策略内部的窄边界，真实一级来源另外保留其等级元数据。
    points = cast(list[GeometryPoint], raw)

    local = _reduce_local_points(points)

    assert raw == before
    assert [(point['kind'], point['value']) for point in local] == [('H', 45), ('L', 24)]
    assert local[0]['flip'] == '翻多为空'
    # H50 已突破旧高，但局部 C 必须等到反向关键位突破；正式升级在另一通道。
    assert not any(point['value'] == 50 for point in local)
    # 正式公共入口仍禁止从一级直接跳到三级，局部适配不改变此门禁。
    with pytest.raises(ValueError, match='immediately lower'):
        trend_reversals(raw, trend_level=3)


def test_local_geometry_recomputes_its_own_permission_instead_of_inheriting_source_upgrade_roles() -> None:
    values = [20, 30, 10, 20, 14, 26, 18, 35, 28, 45, 36, 42, 30, 36, 24, 31, 26, 50]
    start = date(2026, 1, 1)
    raw: list[dict[str, object]] = [
        dict(index=index, ordinal=0, time=(start + timedelta(days=index)).isoformat(),
             kind='L' if index % 2 == 0 else 'H', value=value,
             available_at=(start + timedelta(days=index + 1)).isoformat(),
             label=f'p{index}', state='reversal', trend_level=1)
        for index, value in enumerate(values)
    ]
    expected = _reduce_local_points(cast(list[GeometryPoint], raw))
    inherited = deepcopy(raw)
    for point in inherited:
        direction = 'down' if point['kind'] == 'L' else 'up'
        point.update(
            upgrade_evidence=dict(direction=direction, role='endpoint', available_at=point['available_at']),
            incoming_trend_state='confirmed', incoming_trend_confirmation=dict(direction=direction),
            trend_confirmation=dict(direction='up'), n_target_confirmation=dict(direction='up'),
            wave_id='source-authority', flip='来源升级',
        )
    before = deepcopy(inherited)

    local = _reduce_local_points(cast(list[GeometryPoint], inherited))

    assert inherited == before
    assert local == expected
    assert not any('upgrade_evidence' in point or 'n_target_confirmation' in point for point in local)


@pytest.mark.parametrize('holding_low', [26, 25])
def test_local_c_waits_for_a_strict_reverse_key_break_after_a_higher_high(holding_low: int) -> None:
    values = [20, 30, 10, 20, 14, 26, 18, 35, 28, 45, 36, 42, 30, 36, 24, 31, 26, 50, 26, 52, holding_low]
    start = date(2026, 1, 1)
    raw: list[dict[str, object]] = [
        dict(index=index, ordinal=0, time=(start + timedelta(days=index)).isoformat(),
             kind='L' if index % 2 == 0 else 'H', value=value,
             available_at=(start + timedelta(days=index + 1)).isoformat(),
             label=f'p{index}', state='reversal', trend_level=1)
        for index, value in enumerate(values)
    ]
    prefix = _reduce_local_points(cast(list[GeometryPoint], raw[:-1]))

    selected = _reduce_local_points(cast(list[GeometryPoint], raw))

    assert [(point['kind'], point['value']) for point in prefix] == [('H', 45), ('L', 24)]
    if holding_low == 26:
        assert selected == prefix
    else:
        assert [(point['kind'], point['value']) for point in selected] == [('H', 45), ('L', 24), ('H', 52)]
        assert selected[-1]['flip'] == '翻多为空'
        assert selected[-1]['available_at'] == (start + timedelta(days=21)).isoformat()
