from collections.abc import Sequence
from copy import deepcopy
from datetime import date, datetime, timedelta

import pytest

from wavequant.domain.market_structure.trend_level_upgrades import promote_trend_key_breaks
from wavequant.domain.market_structure.trend_levels import trend_reversals
from wavequant.domain.market_structure.trend_publication import confirmed_trend_legs, publish_uptrends
from wavequant.domain.models.model import Bar


def source_points(values: Sequence[float], *, level: int, mirror: bool = False) -> list[dict[str, object]]:
    start = date(2026, 1, 1)
    return [dict(index=index, ordinal=0, kind=('H' if mirror else 'L') if index % 2 == 0 else
                 ('L' if mirror else 'H'), value=100 - value if mirror else value,
                 time=(start + timedelta(days=index)).isoformat(),
                 available_at=(start + timedelta(days=index + 1)).isoformat(),
                 label=f'p{index}', trend_level=level, state='reversal')
            for index, value in enumerate(values)]


VALUES = [20, 30, 10, 20, 14, 26, 18, 35, 28, 45, 36, 42, 30, 36, 24, 31, 26, 50]


@pytest.mark.parametrize('level', [1, 2, 3])
@pytest.mark.parametrize('mirror', [False, True])
def test_strict_known_key_break_promotes_whole_wave_without_a_later_holding_turn(level: int, mirror: bool) -> None:
    source = source_points(VALUES, level=level - 1, mirror=mirror)
    regular = trend_reversals(source, trend_level=level)
    before = deepcopy((source, regular))

    promoted = promote_trend_key_breaks(source, regular, trend_level=level)

    assert (source, regular) == before
    assert [point['index'] for point in promoted][-2:] == [14, 17]
    origin, endpoint = promoted[-2:]
    assert origin['value'] == (76 if mirror else 24)
    assert endpoint['value'] == 50
    assert origin['available_at'] == endpoint['available_at'] == source[17]['available_at']
    assert endpoint['confirmed_by'] == {
        field: source[17][field] for field in ('index', 'ordinal', 'kind', 'value', 'time', 'available_at', 'label')
    }
    position = 'source_turn_position' if level == 1 else f'source_level{level - 1}_position'
    assert origin[position] == 14 and endpoint[position] == 17
    assert endpoint['wave_direction_after'] == ('down' if mirror else 'up')
    evidence = endpoint['upgrade_evidence']
    assert isinstance(evidence, dict) and evidence['role'] == 'endpoint'
    origin_evidence = origin['upgrade_evidence']
    assert isinstance(origin_evidence, dict) and origin_evidence['role'] == 'origin'


@pytest.mark.parametrize('mirror', [False, True])
@pytest.mark.parametrize('attack', [44, 45])
def test_equal_or_unbroken_level_key_never_upgrades(attack: int, mirror: bool) -> None:
    source = source_points([*VALUES[:-1], attack], level=1, mirror=mirror)
    regular = trend_reversals(source, trend_level=2)

    assert promote_trend_key_breaks(source, regular, trend_level=2) == regular


@pytest.mark.parametrize('integer_clock', [False, True])
@pytest.mark.parametrize('offset', [-1, 0, 1])
def test_old_key_first_known_must_precede_attack_price_date(integer_clock: bool, offset: int) -> None:
    source = source_points(VALUES, level=1)
    regular = trend_reversals(source, trend_level=2)
    if integer_clock:
        for point in source:
            index = point['index']
            assert isinstance(index, int)
            point['available_at'] = index + 1
        regular = trend_reversals(source, trend_level=2)
        regular[-1]['available_at'] = 17 + offset
    else:
        regular[-1]['available_at'] = (date(2026, 1, 18) + timedelta(days=offset)).isoformat()

    result = promote_trend_key_breaks(source, regular, trend_level=2)

    assert (any(point['index'] == 17 for point in result)) is (offset < 0)


@pytest.mark.parametrize('mirror', [False, True])
def test_future_ordered_origin_is_not_borrowed_before_its_confirmation(mirror: bool) -> None:
    source = source_points([*VALUES, 30], level=1, mirror=mirror)
    regular = trend_reversals(source, trend_level=2)
    future_origin = next(point for point in regular if point['index'] == 14)
    assert future_origin['available_at'] == '2026-01-20'

    result = promote_trend_key_breaks(source[:18], regular, trend_level=2)

    origin = next(point for point in result if point['index'] == 14)
    endpoint = next(point for point in result if point['index'] == 17)
    assert origin['available_at'] == endpoint['available_at'] == '2026-01-19'
    assert origin['confirmation_rule'] != future_origin['confirmation_rule']


def test_unknown_current_level_key_cannot_be_borrowed_from_future_candidates() -> None:
    source = source_points(VALUES, level=1)
    regular = trend_reversals(source, trend_level=2)
    regular[-1]['available_at'] = '2026-02-01'

    result = promote_trend_key_breaks(source, regular, trend_level=2)

    assert not any(point['index'] in (9, 17) for point in result)


@pytest.mark.parametrize('mirror', [False, True])
def test_source_prefixes_preserve_the_first_legal_upgrade(mirror: bool) -> None:
    source = source_points(VALUES, level=1, mirror=mirror)
    full = promote_trend_key_breaks(source, trend_reversals(source, trend_level=2), trend_level=2)
    for end in range(1, len(source) + 1):
        prefix = source[:end]
        result = promote_trend_key_breaks(prefix, trend_reversals(prefix, trend_level=2), trend_level=2)
        assert result == [point for point in full if str(point['available_at']) <= str(prefix[-1]['available_at'])]


def test_upgraded_endpoint_cannot_invent_a_reverse_origin_without_a_new_source_turn() -> None:
    source = source_points(VALUES, level=1)
    source.append(dict(source[-1], index=18, time='2026-01-19', available_at='2026-01-20', value=55))
    regular = trend_reversals(source, trend_level=2)

    result = promote_trend_key_breaks(source, regular, trend_level=2)

    assert [point['index'] for point in result][-2:] == [14, 17]
    assert not any(point['index'] == 18 for point in result)


@pytest.mark.parametrize('mirror', [False, True])
def test_a_later_causal_source_origin_can_upgrade_against_the_first_upgraded_key(mirror: bool) -> None:
    source = source_points([*VALUES, 30, 60], level=1, mirror=mirror)
    regular = trend_reversals(source, trend_level=2)

    result = promote_trend_key_breaks(source, regular, trend_level=2)

    assert [point['index'] for point in result][-4:] == [14, 17, 18, 19]
    assert result[-1]['available_at'] == source[19]['available_at']
    evidence = result[-1]['upgrade_evidence']
    assert isinstance(evidence, dict)
    key = evidence['broken_key']
    assert isinstance(key, dict) and key['index'] == 17


@pytest.mark.parametrize('state', ['seed', 'developing'])
def test_unfinished_source_attack_is_ineligible(state: str) -> None:
    source = source_points(VALUES, level=1)
    source[-1]['state'] = state
    regular = trend_reversals(source, trend_level=2)

    assert promote_trend_key_breaks(source, regular, trend_level=2) == regular


@pytest.mark.parametrize('level', [True, 0, 4, 2.0])
def test_invalid_upgrade_levels_are_rejected(level: object) -> None:
    with pytest.raises(ValueError, match='trend_level'):
        promote_trend_key_breaks([], [], trend_level=level)  # type: ignore[arg-type]


def test_upgrade_rejects_cross_level_source_and_candidates() -> None:
    source = source_points(VALUES, level=0)
    with pytest.raises(ValueError, match='immediately lower'):
        promote_trend_key_breaks(source, [], trend_level=2)

    source = source_points(VALUES, level=1)
    wrong_candidates = trend_reversals(source, trend_level=2)
    wrong_candidates[-1]['trend_level'] = 3
    with pytest.raises(ValueError, match='immediately lower'):
        promote_trend_key_breaks(source, wrong_candidates, trend_level=2)


@pytest.mark.parametrize('field,value', [('value', 44), ('time', '2026-01-09'), ('available_at', '2026-01-10')])
def test_current_level_key_requires_its_actual_confirmed_source_provenance(field: str, value: object) -> None:
    source = source_points(VALUES, level=1)
    regular = trend_reversals(source, trend_level=2)
    regular[-1][field] = value

    with pytest.raises(ValueError, match='source provenance'):
        promote_trend_key_breaks(source, regular, trend_level=2)


@pytest.mark.parametrize('level', [2, 3])
@pytest.mark.parametrize('upgrade', [False, True])
def test_lower_level_publication_certificate_cannot_authorize_an_upper_leg(level: int, upgrade: bool) -> None:
    source = source_points(VALUES, level=level - 1)
    reference_fields = ('index', 'ordinal', 'kind', 'value', 'time', 'available_at', 'label')
    proof = dict(direction='up', confirmation_rule='strict_same_level_market_key_break',
                 available_at=source[17]['available_at'],
                 origin={field: source[14][field] for field in reference_fields},
                 broken_key={field: source[9][field] for field in reference_fields},
                 confirmed_by={field: source[17][field] for field in reference_fields})
    for point in source:
        point.update(incoming_trend_confirmation=proof, incoming_trend_state='confirmed',
                     active_trend_confirmation=proof, trend_confirmation=proof,
                     trend_confirmation_route='same_level_key_break', n_target_confirmation=proof,
                     incoming_leg_confirmation=proof, wave_id='lower-only-wave')
    candidates = trend_reversals(source, trend_level=level)
    if upgrade:
        candidates = promote_trend_key_breaks(source, candidates, trend_level=level)
    authorization_fields = (
        'incoming_trend_confirmation', 'incoming_trend_state', 'active_trend_confirmation',
        'trend_confirmation', 'trend_confirmation_route', 'n_target_confirmation',
        'incoming_leg_confirmation', 'wave_id',
    )
    assert all(not any(field in point for field in authorization_fields) for point in candidates)
    # 几何来源已有自己的强证书，但本级行情在源起点后即跌破整波防守，
    # 所以任何上级上涨腿仍必须被本级 publication gate 拒绝。
    bars = [Bar(datetime(2026, 1, 1) + timedelta(days=index), 'TEST', 50, 100, 1, 50, 100)
            for index in range(len(source) + 1)]

    published = publish_uptrends(candidates, source, bars, source_level=level - 1)

    assert confirmed_trend_legs(published) == []
