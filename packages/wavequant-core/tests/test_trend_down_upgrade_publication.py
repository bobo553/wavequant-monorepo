"""下降升级仍需真实本级来源与已到达的确认日，不能借用其他拐点授权。"""

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import cast

import pytest

from wavequant.domain.market_structure.trend_publication import publish_uptrends
from wavequant.domain.market_structure.trend_level_upgrades import promote_trend_key_breaks
from wavequant.domain.market_structure.trend_levels import trend_reversals
from wavequant.domain.models.model import Bar


def sample() -> tuple[list[Bar], list[dict[str, object]]]:
    rows = [(12.0, 11.0, 11.5), (11.0, 10.0, 10.5), (20.0, 11.0, 19.0),
            (16.0, 5.0, 6.0), (10.0, 6.0, 8.0)]
    bars = [Bar(datetime(2026, 1, 1) + timedelta(days=index), 'TEST', (high + low) / 2,
                high, low, closing, 100) for index, (high, low, closing) in enumerate(rows)]
    source: list[dict[str, object]] = [
        dict(index=index, ordinal=0, kind=kind, value=bars[index].high if kind == 'H' else bars[index].low,
             time=bars[index].timestamp.date().isoformat(), available_at=bars[known].timestamp.date().isoformat(),
             state='confirmed', label=f'{kind}{index}', trend_level=1)
        for index, kind, known in ((0, 'H', 0), (1, 'L', 2), (2, 'H', 3), (3, 'L', 4))
    ]
    return bars, source


def candidates(source: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    points = [dict(point, source_level1_position=position, trend_level=2, source_level=1,
                   confirmation_rule='structure') for position, point in enumerate(source)]
    points[-1]['upgrade_evidence'] = dict(direction='down', role='endpoint')
    return points


def test_real_low_upgrade_uses_its_own_source_and_independent_down_certificate() -> None:
    bars, source = sample()

    published = publish_uptrends(candidates(source), source, bars, source_level=1)

    endpoint = published[-1]
    assert (endpoint['index'], endpoint['kind'], endpoint['value'], endpoint['source_level1_position']) == (3, 'L', 5, 3)
    assert endpoint['incoming_trend_state'] == 'confirmed'
    proof = endpoint['incoming_trend_confirmation']
    assert isinstance(proof, Mapping)
    assert proof['direction'] == 'down'
    assert proof['confirmation_rule'] == 'strict_same_level_market_key_break'
    assert endpoint['available_at'] == '2026-01-05'
    origin = cast(Mapping[str, object], proof['origin'])
    key = cast(Mapping[str, object], proof['broken_key'])
    assert (origin['index'], origin['kind'], origin['value']) == (2, 'H', 20)
    assert (key['index'], key['kind'], key['value']) == (1, 'L', 10)


@pytest.mark.parametrize(('field', 'value'), [
    ('source_level1_position', 1),
    ('source_level1_position', -1),
    ('source_level1_position', 4),
    ('source_level1_position', True),
    ('source_level1_position', None),
    ('value', 4.0),
    ('index', 4),
    ('ordinal', 1),
])
def test_down_upgrade_cannot_replace_its_low_with_a_forged_source_reference(field: str, value: object) -> None:
    bars, source = sample()
    points = candidates(source)
    points[-1][field] = value

    with pytest.raises(ValueError, match='source'):
        publish_uptrends(points, source, bars, source_level=1)


@pytest.mark.parametrize(('field', 'value'), [
    ('source_level1_position', 0),
    ('source_level1_position', -1),
    ('value', 21.0),
    ('index', 1),
    ('ordinal', 1),
])
def test_forged_high_origin_cannot_authorize_the_down_upgrade(field: str, value: object) -> None:
    bars, source = sample()
    points = candidates(source)
    points[2][field] = value

    published = publish_uptrends(points, source, bars, source_level=1)

    assert not any(point['index'] == 3 and point['kind'] == 'L' for point in published)


@pytest.mark.parametrize('length', [3, 4])
def test_down_upgrade_prefix_cannot_borrow_a_future_source_confirmation(length: int) -> None:
    bars, source = sample()
    # 即使完整来源和候选都已传入，攻击价在索引3发生、低点在索引4确认，
    # 前缀截止到攻击前或确认前时仍不得发布该下降末端。
    published = publish_uptrends(candidates(source), source, bars[:length], source_level=1)

    assert not any(point['index'] == 3 and point['kind'] == 'L' for point in published)


def test_missing_low_source_position_is_rejected_in_the_upgrade_branch() -> None:
    bars, source = sample()
    points = candidates(source)
    points[-1].pop('source_level1_position')

    with pytest.raises(ValueError, match='source'):
        publish_uptrends(points, source, bars, source_level=1)


def paired_sample() -> tuple[list[Bar], list[dict[str, object]], list[dict[str, object]]]:
    # 公开链最初只有H90压力，配对H76因为同类H被压缩而尚未公开。真正旧本级
    # 低点是L55，只有最后L50跌破它才能独立发布H76→L50，不能借H90许可。
    values = (20, 30, 10, 20, 14, 26, 18, 35, 28, 45, 38, 42, 32, 36, 24, 29, 26, 50)
    start = datetime(2026, 1, 1)
    bars: list[Bar] = []
    source: list[dict[str, object]] = []
    for index, value in enumerate(values):
        price = float(100 - value)
        kind = 'H' if index % 2 == 0 else 'L'
        low, high = (price - 1, price) if kind == 'H' else (price, price + 1)
        bars.append(Bar(start + timedelta(days=index), 'TEST', (high + low) / 2,
                        high, low, (high + low) / 2, 100))
        source.append(dict(index=index, ordinal=0, kind=kind, value=price,
                           time=(start + timedelta(days=index)).date().isoformat(),
                           available_at=(start + timedelta(days=index + 1)).date().isoformat(),
                           state='reversal', label=f'{kind}{index}', trend_level=1))
    bars.append(Bar(start + timedelta(days=len(values)), 'TEST', bars[-1].open,
                    bars[-1].high, bars[-1].low, bars[-1].close, 100))
    events: list[dict[str, object]] = []
    points = promote_trend_key_breaks(source, trend_reversals(source, trend_level=2),
                                    trend_level=2, event_sink=events)
    # 本样本专门验证“严格下降升级起点”复制边界，不验证最早几何的归并选择。
    # 无先后限制后，H14同日已有普通证明，默认并集会正确保留ordinary；测试
    # 必须选回真实event_sink中的独立strict-origin，才能进入来源身份校验分支。
    # 坐标、价格和可知日均不改写，也不伪造升级证据；普通优先另有公共判定测试。
    strict_origin = next(point for point in events if point['index'] == 14 and 'upgrade_evidence' in point)
    evidence = strict_origin['upgrade_evidence']
    assert isinstance(evidence, Mapping) and evidence['direction'] == 'down' and evidence['role'] == 'origin'
    origin_position = next(index for index, point in enumerate(points) if point['index'] == 14)
    assert all(points[origin_position][field] == strict_origin[field]
               for field in ('index', 'ordinal', 'kind', 'value', 'time', 'available_at'))
    points[origin_position] = strict_origin
    return bars, source, points


def test_real_paired_down_origin_and_endpoint_publish_their_own_strict_key_proof() -> None:
    bars, source, points = paired_sample()

    published = publish_uptrends(points, source, bars, source_level=1)

    assert [(point['index'], point['kind'], point['value']) for point in published[-2:]] == [
        (14, 'H', 76.0), (17, 'L', 50.0),
    ]
    proof = cast(Mapping[str, object], published[-1]['incoming_trend_confirmation'])
    origin = cast(Mapping[str, object], proof['origin'])
    key = cast(Mapping[str, object], proof['broken_key'])
    assert proof['direction'] == 'down' and proof['confirmation_rule'] == 'strict_same_level_market_key_break'
    assert (origin['index'], origin['kind'], origin['value']) == (14, 'H', 76.0)
    assert (key['index'], key['kind'], key['value']) == (9, 'L', 55.0)
    assert bars[17].low == 50.0 < cast(float, key['value'])


@pytest.mark.parametrize(('field', 'value'), [
    ('source_level1_position', 2), ('source_level1_position', True), ('source_level1_position', -1),
    ('index', 12), ('ordinal', 1), ('value', 77.0),
])
def test_paired_down_origin_must_match_its_actual_source(field: str, value: object) -> None:
    bars, source, points = paired_sample()
    points[-2][field] = value

    with pytest.raises(ValueError, match='source origin'):
        publish_uptrends(points, source, bars, source_level=1)


@pytest.mark.parametrize(('field', 'value'), [
    ('index', 12), ('ordinal', 1), ('value', 77.0), ('kind', 'L'),
])
def test_endpoint_cannot_borrow_a_different_paired_high_reference(field: str, value: object) -> None:
    bars, source, points = paired_sample()
    upgrade = dict(cast(Mapping[str, object], points[-1]['upgrade_evidence']))
    upgrade['origin'] = dict(cast(Mapping[str, object], upgrade['origin']), **{field: value})
    points[-1]['upgrade_evidence'] = upgrade

    published = publish_uptrends(points, source, bars, source_level=1)

    assert not any(point['index'] == 17 and point['kind'] == 'L' for point in published)


@pytest.mark.parametrize(('field', 'value'), [
    ('index', 8), ('ordinal', 1), ('value', 56.0), ('kind', 'H'),
    ('available_at', '2026-01-09'), ('available_at', '2026-01-20'),
])
def test_paired_down_key_must_be_a_known_actual_level_low(field: str, value: object) -> None:
    bars, source, points = paired_sample()
    key = dict(cast(Mapping[str, object], points[-1]['broken_key']), **{field: value})
    points[-1]['broken_key'] = key

    published = publish_uptrends(points, source, bars, source_level=1)

    assert not any(point['index'] == 17 and point['kind'] == 'L' for point in published)


@pytest.mark.parametrize('length', [17, 18])
def test_paired_down_origin_and_key_cannot_borrow_endpoint_knowledge(length: int) -> None:
    bars, source, points = paired_sample()

    published = publish_uptrends(points, source, bars[:length], source_level=1)

    assert not any(point['index'] == 17 and point['kind'] == 'L' for point in published)
