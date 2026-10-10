"""新的本级N点不能从真实下级来源复制许可证，同坐标本级旧证书需保留。"""

from collections.abc import Mapping
from copy import deepcopy
from datetime import timedelta
from typing import cast

import pytest

from wavequant.domain.market_structure.n_trend_confirmation import N_TARGET_CONFIRMATION
from wavequant.domain.market_structure.n_trend_reversals import n_target_reversals
from wavequant.domain.market_structure.trend_publication import annotate_published_legs
from wavequant.domain.models.model import Bar

from .test_n_trend_levels import _sample


def _position_field(source_level: int) -> str:
    return 'source_turn_position' if source_level == 0 else f'source_level{source_level}_position'


def _lower_permission(source: list[dict[str, object]]) -> dict[str, object]:
    # 镜像来源的首点为L10，下一点H25。这里只注入下级方向许可，N本身仍
    # 使用真实行情与完整来源独立达标；下级许可不得成为上级相邻上升腿证书。
    return dict(direction='up', confirmation_rule='strict_same_level_market_key_break',
                available_at=source[1]['available_at'], origin=dict(source[0]),
                confirmed_by=dict(source[1]), label='lower-source-license')


def _poison(source: list[dict[str, object]], source_level: int) -> None:
    permission = _lower_permission(source)
    for point in source:
        point.update(trend_level=source_level, source_level=max(0, source_level - 1),
                     incoming_trend_state='confirmed', incoming_trend_confirmation=deepcopy(permission),
                     active_trend_confirmation=deepcopy(permission), trend_confirmation=deepcopy(permission),
                     trend_confirmation_route='lower-only', incoming_leg_confirmation=deepcopy(permission),
                     wave_id='lower-wave', upgrade_evidence=dict(direction='down', role='endpoint'),
                     confirmed_by=dict(index=point['index'], label='auditable-lower-confirmation'))


@pytest.mark.parametrize('source_level', [0, 1, 2])
@pytest.mark.parametrize('up', [False, True])
def test_new_n_origin_rebuilds_its_level_and_does_not_inherit_lower_license(source_level: int, up: bool) -> None:
    bars, source = _sample(up=up)
    _poison(source, source_level)
    before = deepcopy(source)

    result = n_target_reversals([], source, bars, source_level=source_level,
                                n_target_trend_confirmation_enabled=True)

    point = next(item for item in result if item['index'] == 1)
    assert (point['trend_level'], point['source_level']) == (source_level + 1, source_level)
    assert point[_position_field(source_level)] == 1
    assert point['confirmation_rule'] == N_TARGET_CONFIRMATION
    proof = point['n_target_confirmation']
    assert isinstance(proof, Mapping) and proof['direction'] == ('up' if up else 'down')
    assert proof['available_at'] == '2024-01-11'
    assert not any(field in point for field in (
        'incoming_trend_state', 'incoming_trend_confirmation', 'active_trend_confirmation',
        'trend_confirmation', 'trend_confirmation_route', 'incoming_leg_confirmation', 'upgrade_evidence', 'wave_id'))
    assert point['source_confirmation'] == before[1]['confirmed_by']
    assert source == before


@pytest.mark.parametrize('source_level', [0, 1, 2])
def test_new_n_high_cannot_authorize_its_incoming_leg_with_a_lower_level_up_certificate(source_level: int) -> None:
    bars, source = _sample(up=False)
    _poison(source, source_level)
    selected = n_target_reversals([], source, bars, source_level=source_level,
                                  n_target_trend_confirmation_enabled=True)
    high = next(point for point in selected if point['index'] == 1)
    prior = dict(source[0], trend_level=source_level + 1, **{_position_field(source_level): 0})

    # 上行注解消费本级incoming证书，因此复制边界是必要门禁；这里不让
    # 下级L10→H25证书冒充新的本级上涨，而本级H的N证书方向实际上是down。
    annotated = annotate_published_legs([prior, high], source, bars, source_level=source_level,
                                        n_target_trend_confirmation_enabled=True)

    assert annotated[-1]['incoming_trend_state'] == 'unconfirmed'
    assert 'incoming_trend_confirmation' not in annotated[-1]


@pytest.mark.parametrize('source_level', [0, 1, 2])
@pytest.mark.parametrize('up', [False, True])
def test_new_n_breach_endpoint_also_strips_lower_license(source_level: int, up: bool) -> None:
    bars, source = _sample(up=up)
    last = bars[-1]
    high, low = (6.0, 4.0) if up else (26.0, 24.0)
    middle = (high + low) / 2
    bars.append(Bar(last.timestamp + timedelta(days=1), last.symbol, middle, high, low, middle, last.volume))
    kind = 'H' if up else 'L'
    source.append(dict(index=10, ordinal=0, kind=kind, value=last.high if up else last.low,
                       time=last.timestamp.date().isoformat(), available_at=last.timestamp.date().isoformat(),
                       state='confirmed', label=f'{kind}10'))
    _poison(source, source_level)
    before = deepcopy(source)

    result = n_target_reversals([], source, bars, source_level=source_level,
                                n_target_trend_confirmation_enabled=True)

    endpoint = next(item for item in result if item['index'] == 10)
    assert endpoint['confirmation_rule'] == 'n_origin_breach_confirms_wave_endpoint'
    assert endpoint['available_at'] == '2024-01-12'
    assert (endpoint['trend_level'], endpoint['source_level']) == (source_level + 1, source_level)
    assert endpoint[_position_field(source_level)] == 4
    assert endpoint['source_confirmation'] == before[-1]['confirmed_by']
    assert not any(field in endpoint for field in (
        'incoming_trend_confirmation', 'active_trend_confirmation', 'trend_confirmation', 'upgrade_evidence', 'wave_id'))
    assert source == before


@pytest.mark.parametrize('source_level', [0, 1, 2])
@pytest.mark.parametrize('up', [False, True])
def test_existing_same_coordinate_keeps_its_own_earlier_permission_when_n_attaches(source_level: int, up: bool) -> None:
    bars, source = _sample(up=up)
    _poison(source, source_level)
    own = dict(direction='down' if up else 'up', available_at=source[1]['available_at'],
               confirmation_rule='strict_same_level_market_key_break', label='own-earlier-license')
    existing = dict(source[1], trend_level=source_level + 1, source_level=source_level,
                    incoming_trend_confirmation=deepcopy(own), active_trend_confirmation=deepcopy(own),
                    trend_confirmation=deepcopy(own), confirmation_rule='existing-structure',
                    **{_position_field(source_level): 1})
    before = deepcopy(existing)

    selected = n_target_reversals([existing], source, bars, source_level=source_level,
                                  n_target_trend_confirmation_enabled=True)

    origin = next(point for point in selected if point['index'] == 1)
    assert {field: origin[field] for field in before} == before
    assert cast(Mapping[str, object], origin['n_target_confirmation'])['available_at'] == '2024-01-11'
    assert existing == before
