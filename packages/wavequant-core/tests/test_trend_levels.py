from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import date, timedelta
from math import inf, nan

import pytest

from wavequant.domain.market_structure.trend_levels import trend_reversals


def source_turns(values: Sequence[float], *, source_level: int, first: str = 'L') -> list[dict[str, object]]:
    origin = date(2026, 1, 1)
    return [dict(index=index, ordinal=0, kind=first if index % 2 == 0 else ('H' if first == 'L' else 'L'),
                 value=value, time=(origin + timedelta(days=index)).isoformat(),
                 available_at=(origin + timedelta(days=index + 1)).isoformat(),
                 label=f'{first}{index}', trend_level=source_level)
            for index, value in enumerate(values)]


VALUES = [20, 30, 10, 20, 14, 26, 18, 35, 28, 45, 36, 42, 30, 36, 24, 31, 26, 38, 30, 42]


def reference_values(points: Sequence[Mapping[str, object]], field: str) -> list[object]:
    values: list[object] = []
    for point in points:
        reference = point[field]
        assert isinstance(reference, Mapping)
        values.append(reference['value'])
    return values


@pytest.mark.parametrize('level', [1, 2, 3])
def test_levels_share_whole_wave_extremes_and_ordered_confirmation(level: int) -> None:
    turns = source_turns(VALUES, source_level=level - 1)
    before = deepcopy(turns)

    result = trend_reversals(turns, trend_level=level)

    assert turns == before
    assert [(point['kind'], point['value']) for point in result] == [('L', 10), ('H', 45), ('L', 24)]
    assert [point['available_at'] for point in result] == [
        turns[6]['available_at'], turns[13]['available_at'], turns[18]['available_at'],
    ]
    source_field = 'source_turn_position' if level == 1 else f'source_level{level - 1}_position'
    assert [point[source_field] for point in result] == [2, 9, 14]
    assert all(point['trend_level'] == level and point['source_level'] == level - 1 for point in result)
    for point in result:
        evidence = point['definition_evidence']
        assert isinstance(evidence, list)
        assert len(evidence) == 4
    if level > 1:
        assert reference_values(result, 'broken_key') == [30, 28, 36]
        assert reference_values(result, 'confirmed_by') == [18, 36, 30]


@pytest.mark.parametrize('level', [1, 2, 3])
def test_new_high_without_later_holding_low_is_not_a_reversal(level: int) -> None:
    turns = source_turns([10, 20, 8, 18, 9, 22], source_level=level - 1)

    assert trend_reversals(turns, trend_level=level) == []

    turns.extend(source_turns([12], source_level=level - 1))
    turns[-1].update(index=6, time='2026-01-07', available_at='2026-01-08')
    result = trend_reversals(turns, trend_level=level)
    assert [(point['kind'], point['value']) for point in result] == [('L', 8)]
    assert result[0]['available_at'] == '2026-01-08'


@pytest.mark.parametrize('level', [1, 2, 3])
def test_equal_or_mixed_pairs_do_not_change_wave_direction(level: int) -> None:
    # A lower high still belongs to a rising-low structure; equality does not
    # supply the strict falling-low evidence needed to reverse the whole wave.
    turns = source_turns([10, 20, 12, 25, 12, 24, 13, 23], source_level=level - 1)

    assert trend_reversals(turns, trend_level=level) == []


@pytest.mark.parametrize('level', [1, 2, 3])
def test_every_source_prefix_keeps_its_first_confirmation_evidence(level: int) -> None:
    turns = source_turns(VALUES, source_level=level - 1)
    complete = trend_reversals(turns, trend_level=level)
    field = 'confirmed_on_turn' if level == 1 else f'confirmed_on_level{level - 1}'

    for end in range(len(turns) + 1):
        expected = []
        for point in complete:
            confirmed_position = point[field]
            assert isinstance(confirmed_position, int)
            if confirmed_position < end:
                expected.append(point)
        assert trend_reversals(turns[:end], trend_level=level) == expected


@pytest.mark.parametrize('state', ['seed', 'developing'])
def test_unfinished_source_turn_cannot_confirm_a_wave(state: str) -> None:
    turns = source_turns(VALUES[:14], source_level=1)
    turns[-1]['state'] = state

    result = trend_reversals(turns, trend_level=2)
    assert [(point['kind'], point['value']) for point in result] == [('L', 10)]


def test_display_only_source_turn_cannot_confirm_a_wave() -> None:
    turns = source_turns(VALUES[:14], source_level=2)
    turns[-1]['display_only'] = True

    result = trend_reversals(turns, trend_level=3)
    assert [(point['kind'], point['value']) for point in result] == [('L', 10)]


def test_latest_evidence_date_is_used_even_when_publication_is_delayed() -> None:
    turns = source_turns(VALUES[:14], source_level=1)
    turns[12]['available_at'] = '2026-02-01'

    result = trend_reversals(turns, trend_level=2)

    assert result[-1]['available_at'] == '2026-02-01'
    assert result[-1]['time'] == turns[9]['time']


@pytest.mark.parametrize('delayed_position', [3, 8])
def test_old_direction_and_left_key_evidence_remain_part_of_the_knowledge_date(delayed_position: int) -> None:
    turns = source_turns(VALUES, source_level=1)
    turns[delayed_position]['available_at'] = '2026-02-01'

    result = trend_reversals(turns, trend_level=2)

    # The initialization high (3) and the high extreme's preceding low (8)
    # have both left the last four-point proof by the time H45 is confirmed.
    assert result[1]['available_at'] == '2026-02-01'
    assert result[2]['available_at'] == '2026-02-01'
    assert [(point['kind'], point['value']) for point in result] == [('L', 10), ('H', 45), ('L', 24)]


@pytest.mark.parametrize('level', [True, False, 0, 4, 1.0, '2'])
def test_invalid_trend_levels_are_rejected(level: object) -> None:
    with pytest.raises(ValueError, match='trend_level'):
        trend_reversals([], trend_level=level)  # type: ignore[arg-type]


@pytest.mark.parametrize('value', [0, -1, True, nan, inf, '10'])
def test_invalid_source_prices_are_rejected(value: object) -> None:
    turns = source_turns([10, 20], source_level=0)
    turns[0]['value'] = value

    with pytest.raises(ValueError, match='finite'):
        trend_reversals(turns, trend_level=1)


def test_source_cannot_skip_or_reuse_the_target_trend_level() -> None:
    turns = source_turns(VALUES, source_level=1)

    with pytest.raises(ValueError, match='immediately lower'):
        trend_reversals(turns, trend_level=3)


def test_source_order_and_incompatible_confirmation_clocks_are_rejected() -> None:
    turns = source_turns(VALUES, source_level=1)
    turns[1]['index'] = turns[0]['index']
    with pytest.raises(ValueError, match='strictly ordered'):
        trend_reversals(turns, trend_level=2)

    turns = source_turns(VALUES, source_level=1)
    turns[-1]['available_at'] = 20
    with pytest.raises(ValueError, match='clocks'):
        trend_reversals(turns, trend_level=2)


def test_integer_clock_keeps_original_source_positions_and_confirmation_indices() -> None:
    turns = source_turns(VALUES, source_level=2)
    for index, point in enumerate(turns):
        point['available_at'] = index + 1
    turns.insert(4, dict(turns[3], state='developing', index=3, ordinal=1))

    result = trend_reversals(turns, trend_level=3)

    assert [point['source_level2_position'] for point in result] == [2, 10, 15]
    assert [point['confirmed_on_level2'] for point in result] == [7, 14, 19]
    assert [point['available_at'] for point in result] == [7, 14, 19]


def test_legacy_numeric_string_clocks_compare_indices_numerically() -> None:
    turns = source_turns(VALUES, source_level=0)
    for index, point in enumerate(turns):
        point.update(time=str(index), available_at=str(index + 1))

    result = trend_reversals(turns, trend_level=1)

    assert [point['available_at'] for point in result] == ['7', '14', '19']


def test_missing_optional_source_ordinal_and_label_have_canonical_output_defaults() -> None:
    turns = source_turns(VALUES, source_level=1)
    for point in turns:
        point.pop('ordinal')
        point.pop('label')

    result = trend_reversals(turns, trend_level=2)

    assert all(point['ordinal'] == 0 and point['label'] == point['kind'] for point in result)


def test_third_level_metadata_describes_its_direct_source_without_inherited_first_level_positions() -> None:
    turns = source_turns(VALUES, source_level=2)
    for point in turns:
        point.update(source_turn_position=99, confirmed_on_turn=99, source_reversal_available_at=99,
                     source_level1_position=99, confirmed_on_level1=99, source_level1_available_at=99,
                     source_level2_position=99, confirmed_on_level2=99, source_level2_available_at=99)

    result = trend_reversals(turns, trend_level=3)

    assert [point['source_level2_position'] for point in result] == [2, 9, 14]
    assert [point['confirmed_on_level2'] for point in result] == [6, 13, 18]
    assert [point['source_level2_available_at'] for point in result] == [
        turns[2]['available_at'], turns[9]['available_at'], turns[14]['available_at'],
    ]
    for point in result:
        assert not any(field in point for field in (
            'source_turn_position', 'confirmed_on_turn', 'source_reversal_available_at',
            'source_level1_position', 'confirmed_on_level1', 'source_level1_available_at',
        ))


@pytest.mark.parametrize('source', ['', 'source', b'source', None])
def test_non_sequence_source_inputs_fail_with_a_domain_error(source: object) -> None:
    with pytest.raises(ValueError, match='sequence of mappings'):
        trend_reversals(source, trend_level=1)  # type: ignore[arg-type]


def test_explicit_unknown_source_level_is_rejected() -> None:
    turns = source_turns(VALUES, source_level=1)
    turns[0]['trend_level'] = None

    with pytest.raises(ValueError, match='immediately lower'):
        trend_reversals(turns, trend_level=2)


def test_ordered_direction_change_does_not_inherit_or_invent_a_strict_key_flip() -> None:
    turns = source_turns(VALUES, source_level=1)
    for point in turns:
        point['flip'] = '翻空为多'

    result = trend_reversals(turns, trend_level=2)

    assert all('flip' not in point for point in result)
    assert [point['direction_definition'] for point in result] == ['多头趋势', '空头趋势', '多头趋势']
    assert reference_values(result, 'broken_key') == [30, 28, 36]


def test_consecutive_public_pressure_points_keep_the_nearest_opposite_source_key() -> None:
    turns = source_turns([20, 30, 10, 12, 35, 14, 25, 9, 22], source_level=1)
    for point, kind in zip(turns, ['H', 'H', 'L', 'L', 'H', 'L', 'H', 'L', 'H']):
        point['kind'] = kind

    result = trend_reversals(turns, trend_level=2)

    assert [(point['kind'], point['value']) for point in result] == [('H', 35)]
    assert reference_values(result, 'broken_key') == [12]
