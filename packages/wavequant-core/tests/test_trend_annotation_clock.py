"""几何自身知识日与依赖完整左侧上下文的标注知识日不能相互覆盖。"""

from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import date, timedelta
from typing import Literal, cast

import pytest

from wavequant.domain.market_structure import trend_lines as module
from wavequant.domain.market_structure.lecture_trend import _annotate
from wavequant.domain.market_structure.trend_levels import Moment


Clock = Literal['index', 'numeric', 'date']
START = date(2026, 1, 1)
DERIVED_FIELDS = frozenset(('label', 'trend_level', 'source_level', 'trend', 'window_trend',
                           'reversal', 'observations', 'levels', 'preceding_turn'))
REFERENCE_FIELDS = frozenset(('index', 'ordinal', 'time', 'kind', 'value', 'available_at', 'label'))


def _moment(index: int, clock: Clock) -> Moment:
    return index if clock == 'index' else str(index) if clock == 'numeric' else (START + timedelta(days=index)).isoformat()


def _points(level: int, mirror: bool = False, clock: Clock = 'index') -> list[dict[str, object]]:
    # 初始 H 晚知，随后的 L 已早知；五号 H 翻向后，六号 L 的交替观察
    # 仍须等到完整上下文十号日才能成立，各点自己的日期不能随之延后。
    values = (30, 20, 28, 18, 32, 27, 35)
    known = (8, 3, 4, 5, 10, 7, 11)
    points: list[dict[str, object]] = []
    for index, (value, available) in enumerate(zip(values, known), start=1):
        kind = 'H' if index % 2 else 'L'
        if mirror:
            kind, value = ('L' if kind == 'H' else 'H'), 60 - value
        point: dict[str, object] = dict(index=index, ordinal=0, time=(START + timedelta(days=index)).isoformat(),
            kind=kind, value=value, available_at=_moment(available, clock), available_index=available,
            label=f'source-{kind}{index}', state='reversal', trend_level=level, source_level=level-1)
        reference = {field: point[field] for field in REFERENCE_FIELDS}
        point.update(source_confirmation=dict(available_at=point['available_at'], origin=deepcopy(reference)),
            definition_evidence=[deepcopy(reference)],
            upgrade_evidence=dict(available_at=point['available_at'], confirmed_by=deepcopy(reference)),
            confirmed_by=deepcopy(reference), confirmation_rule='independent_structure_fact',
            source_reversal_available_at=point['available_at'], audit=dict(own_known=point['available_at']))
        points.append(point)
    return points


def _references(value: object) -> list[Mapping[str, object]]:
    if isinstance(value, Mapping):
        assert all(isinstance(key, str) for key in value)
        fields = cast(Mapping[str, object], value)
        return [*([fields] if fields.keys() == REFERENCE_FIELDS else []),
                *(reference for child in fields.values() for reference in _references(child))]
    if isinstance(value, (list, tuple)):
        return [reference for child in value for reference in _references(child)]
    return []


@pytest.mark.parametrize('level', [1, 2, 3])
@pytest.mark.parametrize('mirror', [False, True])
@pytest.mark.parametrize('clock', ['index', 'numeric', 'date'])
def test_late_then_early_geometry_keeps_own_evidence_but_dates_observations_at_complete_context(
    level: int, mirror: bool, clock: Clock,
) -> None:
    points = _points(level, mirror, clock)
    original = deepcopy(points)
    proofs = [point['source_confirmation'] for point in points]

    module._annotate_geometry(points, 'TEST', module._definition_dates(points), trend_level=level)

    assert [{key: value for key, value in point.items() if key not in DERIVED_FIELDS} for point in points] == [
        {key: value for key, value in point.items() if key not in DERIVED_FIELDS} for point in original]
    assert all(point['source_confirmation'] is proof for point, proof in zip(points, proofs))
    assert [point['available_at'] for point in points] == [_moment(index, clock) for index in (8, 3, 4, 5, 10, 7, 11)]
    assert [point['trend_level'] for point in points] == [level] * len(points)
    assert [point['source_level'] for point in points] == [level-1] * len(points)
    observation = cast(Sequence[Mapping[str, object]], points[5]['observations'])
    assert [(item['title'], item['available_at']) for item in observation] == [
        ('多空交替' if mirror else '空多交替', _moment(10, clock))]
    flip = cast(Sequence[Mapping[str, object]], points[4]['observations'])
    assert [(item['title'], item['available_at']) for item in flip] == [
        ('翻多为空' if mirror else '翻空为多', _moment(10, clock))]
    originals = {(point['index'], point['ordinal']): point for point in original}
    references = [reference for point in points for field in ('observations', 'preceding_turn')
                  for reference in _references(point.get(field))]
    assert references
    for reference in references:
        owner = originals[(reference['index'], reference['ordinal'])]
        assert reference['available_at'] == owner['available_at']
        assert (reference['kind'], reference['value'], reference['time']) == (
            owner['kind'], owner['value'], owner['time'])
    preceding = cast(Mapping[str, object], points[-1]['preceding_turn'])
    assert (preceding['index'], preceding['available_at']) == (6, _moment(7, clock))


@pytest.mark.parametrize('level', [1, 2, 3])
def test_monotonic_geometry_annotation_matches_every_original_derived_field(level: int) -> None:
    points = _points(level)
    for index, point in enumerate(points, start=1):
        point['available_at'] = index + 1
    expected = deepcopy(points)
    dates = module._definition_dates(points)
    annotate = cast(module._DefinitionAnnotator, _annotate)
    annotate(expected, 'TEST', dates, trend_level=level)

    module._annotate_geometry(points, 'TEST', dates, trend_level=level)

    assert points == expected


def test_only_exact_matching_reference_shapes_restore_their_own_dates() -> None:
    original = _points(1)[3]
    reference = {field: original[field] for field in REFERENCE_FIELDS}
    shadow_reference = dict(reference, available_at=8)
    nested = dict(available_at=10, references=[shadow_reference],
        observation=dict(shadow_reference, title='完整上下文事件'),
        incomplete={key: value for key, value in shadow_reference.items() if key != 'label'},
        other_price=dict(shadow_reference, value=999), other_coordinate=dict(shadow_reference, index=99))
    before = deepcopy(nested)

    restored = cast(Mapping[str, object], module._restore_annotation_references(nested, {(4, 0): original}))

    references = cast(Sequence[Mapping[str, object]], restored['references'])
    assert references[0]['available_at'] == 5
    assert restored['available_at'] == 10
    for field in ('observation', 'incomplete', 'other_price', 'other_coordinate'):
        assert cast(Mapping[str, object], restored[field])['available_at'] == 8
    assert nested == before


def test_annotation_projection_cannot_write_back_coordinates_or_independent_proofs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    points = _points(1)
    original = deepcopy(points)
    real_annotate = cast(module._DefinitionAnnotator, _annotate)

    def annotate(shadows: list[dict[str, object]], symbol: str, dates: Mapping[object, int], *, trend_level: int) -> None:
        # 旧标注器的适配边界只接收最小坐标投影，完整账本不进入该边界。
        assert all('source_confirmation' not in point and 'upgrade_evidence' not in point for point in shadows)
        real_annotate(shadows, symbol, dates, trend_level=trend_level)
        for point in shadows:
            point.update(value=-1, available_at=999, available_index=999, confirmation_rule='changed',
                         source_confirmation=dict(available_at=999), new_unlisted_field='changed')

    monkeypatch.setattr(module, '_annotate', annotate)
    module._annotate_geometry(points, 'TEST', module._definition_dates(points), trend_level=1)

    assert [{key: value for key, value in point.items() if key not in DERIVED_FIELDS} for point in points] == [
        {key: value for key, value in point.items() if key not in DERIVED_FIELDS} for point in original]
    assert all('new_unlisted_field' not in point for point in points)


@pytest.mark.parametrize('failure', ['coordinate', 'same_kind', 'zero_leg'])
@pytest.mark.parametrize('level', [1, 2, 3])
def test_geometry_adapter_still_rejects_invalid_price_paths_without_partial_writeback(failure: str, level: int) -> None:
    points = _points(level)[:2]
    if failure == 'coordinate':
        points[1]['index'] = 0
    elif failure == 'same_kind':
        points[1]['kind'], points[1]['value'] = 'H', 35
    else:
        points[1]['value'] = points[0]['value']
    before = deepcopy(points)

    with pytest.raises(ValueError, match='ordered alternating|nonzero alternating'):
        module._annotate_geometry(points, 'TEST', module._definition_dates(points), trend_level=level)

    assert points == before


@pytest.mark.parametrize('level', [1, 2, 3])
def test_original_annotator_still_rejects_nonmonotonic_confirmation_inputs(level: int) -> None:
    points = _points(level)[:2]
    annotate = cast(module._DefinitionAnnotator, _annotate)

    with pytest.raises(ValueError, match='ordered alternating causally confirmed'):
        annotate(points, 'TEST', module._definition_dates(points), trend_level=level)


def _permission_break(level: int, mirror: bool, clock: Clock) -> tuple[list[dict[str, object]], dict[str, object]]:
    points = _points(level, mirror, clock)
    for index, source_point in enumerate(points, start=1):
        source_point['available_at'] = _moment(index + 1, clock)
    kind, value, origin_value = ('H', 20, 15) if mirror else ('L', 40, 45)
    origin: dict[str, object] = dict(index=8, ordinal=0, time=(START + timedelta(days=8)).isoformat(),
        kind='L' if mirror else 'H', value=origin_value, available_at=_moment(9, clock), label='真实许可起点')
    point: dict[str, object] = dict(index=9, ordinal=0, time=(START + timedelta(days=9)).isoformat(),
        kind=kind, value=value, available_at=_moment(10, clock), available_index=10, label='旧下级标签',
        state='reversal', trend_level=level, source_level=level-1,
        source_confirmation=dict(available_at=_moment(10, clock)), definition_evidence=['原几何证明'],
        upgrade_evidence=dict(available_at=_moment(10, clock)),
        preceding_turn=deepcopy(points[-1]), observations=[dict(title='继承的旧观察')], levels=['继承的旧关键位'])
    proof: dict[str, object] = dict(direction='up' if mirror else 'down', origin=deepcopy(origin),
        available_at=_moment(10, clock), confirmed_by=dict(index=9),
        confirmation_rule='strict_same_level_market_key_break')
    point.update(incoming_trend_state='confirmed', incoming_trend_confirmation=proof,
                 active_trend_confirmation=deepcopy(proof))
    return [*points, point], origin


@pytest.mark.parametrize('level', [1, 2, 3])
@pytest.mark.parametrize('clock', ['index', 'numeric', 'date'])
def test_complete_legal_permission_run_matches_every_original_field(level: int, clock: Clock) -> None:
    points, _ = _permission_break(level, False, clock)
    points.pop()
    expected = deepcopy(points)
    dates = module._definition_dates(points)
    annotate = cast(module._DefinitionAnnotator, _annotate)
    annotate(expected, 'TEST', dates, trend_level=level)

    module._annotate_permissions(points, 'TEST', dates, trend_level=level)

    assert points == expected


@pytest.mark.parametrize('level', [1, 2, 3])
def test_permission_global_reference_labels_do_not_require_an_inherited_source_label(level: int) -> None:
    points, origin = _permission_break(level, False, 'index')
    for point in points:
        point.pop('label')

    module._annotate_permissions(points, 'TEST', module._definition_dates([*points, origin]), trend_level=level)

    owners = {(point['index'], point['ordinal']): point for point in points}
    for point in points:
        for field in ('observations', 'preceding_turn'):
            for reference in _references(point.get(field)):
                owner = owners.get((reference['index'], reference['ordinal']), origin)
                assert reference['label'] == owner['label']


@pytest.mark.parametrize('level', [1, 2, 3])
@pytest.mark.parametrize('mirror', [False, True])
@pytest.mark.parametrize('clock', ['index', 'numeric', 'date'])
def test_independent_permission_wave_uses_own_complete_origin_without_rewriting_proofs_or_prior_run(
    level: int, mirror: bool, clock: Clock,
) -> None:
    points, origin = _permission_break(level, mirror, clock)
    before = deepcopy(points)
    expected_run = deepcopy(points[:-1])
    dates = module._definition_dates([*points, origin])
    annotate = cast(module._DefinitionAnnotator, _annotate)
    annotate(expected_run, 'TEST', dates, trend_level=level)
    proof = points[-1]['incoming_trend_confirmation']

    module._annotate_permissions(points, 'TEST', dates, trend_level=level)

    assert points[:-1] == expected_run
    assert [{key: value for key, value in point.items() if key not in DERIVED_FIELDS} for point in points] == [
        {key: value for key, value in point.items() if key not in DERIVED_FIELDS} for point in before]
    assert points[-1]['incoming_trend_confirmation'] is proof
    assert points[-1]['preceding_turn'] == origin
    assert points[-1]['observations'] == []
    assert points[-1]['levels'] == [dict(name=('H4 的末升低' if mirror else 'L4 的末跌高'), price=origin['value'])]
    assert points[-1]['incoming_trend_state'] == 'confirmed'
    assert points[-1]['active_trend_confirmation'] == before[-1]['active_trend_confirmation']
    labels = {(point['index'], point['ordinal']): point['label'] for point in points}
    assert len(set(labels.values())) == len(points)
    for point in points:
        for reference in _references(point.get('observations')):
            assert reference['label'] == labels[(reference['index'], reference['ordinal'])]


@pytest.mark.parametrize('failure', ['missing_proof', 'incomplete', 'future', 'wrong_price', 'wrong_kind', 'wrong_direction'])
@pytest.mark.parametrize('level', [1, 2, 3])
def test_broken_permission_connection_without_a_valid_origin_starts_clean_short_run(failure: str, level: int) -> None:
    points, origin = _permission_break(level, False, 'index')
    point = points[-1]
    proof = cast(dict[str, object], point['incoming_trend_confirmation'])
    proof_origin = cast(dict[str, object], proof['origin'])
    if failure == 'missing_proof':
        point.pop('incoming_trend_confirmation')
    elif failure == 'incomplete':
        proof_origin.pop('label')
    elif failure == 'future':
        proof_origin['available_at'] = 12
    elif failure == 'wrong_price':
        proof_origin['value'] = 30
    elif failure == 'wrong_kind':
        proof_origin['kind'] = 'L'
    else:
        proof['direction'] = 'up'
    before = deepcopy(points)

    module._annotate_permissions(points, 'TEST', module._definition_dates([*points, origin]), trend_level=level)

    assert 'preceding_turn' not in point
    assert point['observations'] == [] and point['levels'] == []
    assert point['label'] == 'L4'
    assert [{key: value for key, value in item.items() if key not in DERIVED_FIELDS} for item in points] == [
        {key: value for key, value in item.items() if key not in DERIVED_FIELDS} for item in before]


@pytest.mark.parametrize('level', [1, 2, 3])
@pytest.mark.parametrize('mirror', [False, True])
def test_later_legal_permission_run_uses_global_labels_in_all_nested_named_references(level: int, mirror: bool) -> None:
    points, origin = _permission_break(level, mirror, 'date')
    for index, value in enumerate((44, 38, 46, 43, 48), start=10):
        kind = 'H' if index % 2 == 0 else 'L'
        if mirror:
            kind, value = ('L' if kind == 'H' else 'H'), 60-value
        points.append(dict(index=index, ordinal=0, kind=kind, value=value,
            time=(START + timedelta(days=index)).isoformat(), available_at=_moment(index+1, 'date'),
            label='下级旧标签', state='reversal', trend_level=level, source_level=level-1,
            incoming_trend_state='unconfirmed', source_confirmation=dict(available_at=_moment(index+1, 'date'))))
    before = deepcopy(points)

    module._annotate_permissions(points, 'TEST', module._definition_dates([*points, origin]), trend_level=level)

    flip = cast(Sequence[Mapping[str, object]], points[10]['observations'])
    alternation = cast(Sequence[Mapping[str, object]], points[11]['observations'])
    assert [(item['title'], item['available_at']) for item in flip] == [
        ('翻多为空' if mirror else '翻空为多', _moment(13, 'date'))]
    assert [(item['title'], item['available_at']) for item in alternation] == [
        ('多空交替' if mirror else '空多交替', _moment(14, 'date'))]
    owners = {(point['index'], point['ordinal']): point for point in points}
    references = [reference for point in points[7:] for field in ('observations', 'preceding_turn')
                  for reference in _references(point.get(field))]
    assert references
    for reference in references:
        assert cast(int, reference['index']) >= 8
        owner = owners.get((reference['index'], reference['ordinal']), origin)
        assert (reference['label'], reference['available_at']) == (owner['label'], owner['available_at'])
    assert len(set(point['label'] for point in points)) == len(points)
    assert [{key: value for key, value in point.items() if key not in DERIVED_FIELDS} for point in points] == [
        {key: value for key, value in point.items() if key not in DERIVED_FIELDS} for point in before]


@pytest.mark.parametrize('level', [1, 2, 3])
@pytest.mark.parametrize('clock', ['index', 'numeric', 'date'])
def test_independent_origin_reference_keeps_its_own_later_date_at_an_existing_public_coordinate(
    level: int, clock: Clock,
) -> None:
    points: list[dict[str, object]] = [dict(index=index, ordinal=0, kind=kind, value=value,
        time=(START + timedelta(days=index)).isoformat(), available_at=_moment(known, clock),
        label=f'原来源{index}', trend_level=level, source_level=level-1)
        for index, kind, value, known in ((0, 'H', 30, 2), (1, 'L', 10, 3), (2, 'H', 25, 4),
                                         (3, 'L', 12, 5), (4, 'H', 20, 6), (6, 'L', 22, 8))]
    origin = {field: points[2][field] for field in REFERENCE_FIELDS}
    origin['available_at'] = _moment(7, clock)
    proof: dict[str, object] = dict(direction='down', origin=deepcopy(origin),
        available_at=_moment(8, clock), confirmed_by=dict(index=6),
        confirmation_rule='strict_same_level_market_key_break')
    points[-1].update(incoming_trend_state='confirmed', incoming_trend_confirmation=proof,
                     active_trend_confirmation=deepcopy(proof))
    before = deepcopy(points)

    module._annotate_permissions(points, 'TEST', module._definition_dates([*points, origin]), trend_level=level)

    preceding = cast(Mapping[str, object], points[-1]['preceding_turn'])
    assert (preceding['index'], preceding['label'], preceding['available_at']) == (2, 'H2', _moment(7, clock))
    assert points[2]['available_at'] == _moment(4, clock)
    assert points[2]['label'] == 'H2'
    assert points[-1]['incoming_trend_confirmation'] is proof
    assert points[-1]['observations'] == []
    assert [{key: value for key, value in point.items() if key not in DERIVED_FIELDS} for point in points] == [
        {key: value for key, value in point.items() if key not in DERIVED_FIELDS} for point in before]
