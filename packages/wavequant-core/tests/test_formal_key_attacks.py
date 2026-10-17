"""正式下级端点严格破本级关键位，不等待下级整波未来结束。"""

from collections.abc import Mapping
from copy import deepcopy
from datetime import date, datetime, timedelta
import json
from pathlib import Path

import pytest

from wavequant.domain.market_structure.lecture_drawing import lecture_drawing
from wavequant.domain.market_structure.trend_judgement import judge_trend
from wavequant.domain.market_structure.trend_levels import completed_source_points, formal_key_attack_points
from wavequant.domain.market_structure.trend_lines import (
    _TrendJudgementCache, high_low_points_from_strokes, polyline_high_low_points, prepare_trend_lines,
)
from wavequant.domain.market_structure.trend_stage_work import _hierarchy_source
from wavequant.domain.models.model import Bar


def source_points(level: int, mirror: bool, integer_clock: bool, attack: float = 50) -> list[dict[str, object]]:
    values = [20, 30, 10, 20, 14, 26, 18, 35, 28, 45, 36, 42, 30, 36, 24, 31, 26, attack]
    start = date(2026, 1, 1)
    points: list[dict[str, object]] = []
    for index, value in enumerate(values):
        kind = ('H' if mirror else 'L') if index % 2 == 0 else ('L' if mirror else 'H')
        points.append(dict(index=index, ordinal=0, time=(start + timedelta(days=index)).isoformat(),
            available_at=index + 1 if integer_clock else (start + timedelta(days=index + 1)).isoformat(),
            kind=kind, value=100 - value if mirror else value, trend_level=level - 1, state='reversal'))
    endpoint = points[-1]
    direction = 'down' if mirror else 'up'
    rule = (f'level{level - 2}_confirmed_' + ('low' if mirror else 'high')
            + f'_breaks_known_level{level - 1}_' + ('last_rise_low' if mirror else 'last_fall_high'))
    references = [{field: point[field] for field in
                   ('index', 'ordinal', 'time', 'kind', 'value', 'available_at')} for point in points[-3:]]
    endpoint.update(source_level=level - 2, wave_completion_policy='whole_wave_v117', wave_identity_state='owned',
        wave_direction_before=direction, wave_direction_after=direction, confirmation_rule=rule,
        upgrade_evidence=dict(role='endpoint', direction=direction, confirmation_rule=rule,
            available_at=endpoint['available_at'], origin=references[1], broken_key=references[0],
            confirmed_by=references[2]))
    return points


@pytest.mark.parametrize('level', [2, 3])
@pytest.mark.parametrize('mirror', [False, True])
@pytest.mark.parametrize('integer_clock', [False, True])
@pytest.mark.parametrize('attack', [44, 45, 50])
def test_formal_endpoint_requires_strict_upper_key_break(level: int, mirror: bool,
                                                       integer_clock: bool, attack: float) -> None:
    source = source_points(level, mirror, integer_clock, attack)
    before = deepcopy(source)
    result = judge_trend(source, trend_level=level)
    endpoints = [point for point in result.points if point['index'] == 17]
    assert bool(endpoints) is (attack > 45)
    if endpoints:
        endpoint = endpoints[0]
        proof = endpoint['upgrade_evidence']
        assert isinstance(proof, Mapping)
        assert proof['broken_key']['index'] == 9
        assert proof['origin']['index'] == 14
        assert endpoint['available_at'] == source[-1]['available_at']
        assert 'wave_completed_at' not in endpoint
    assert source == before
    assert all(point['index'] != 17 for point in completed_source_points(source, source_level=level - 1))


@pytest.mark.parametrize('owned', [False, True])
def test_cached_and_stage_source_match_direct_key_break(owned: bool) -> None:
    source = source_points(2, False, True)
    cache = _TrendJudgementCache(tail_reuse=True)
    for prefix in (source[:-1], source, deepcopy(source), source[:-1]):
        direct = judge_trend(prefix, trend_level=2)
        actual = cache.judge(prefix, path='sample', trend_level=2, _owned_source=owned)
        assert actual == direct
    stage = _hierarchy_source(source)
    assert judge_trend(stage, trend_level=2) == judge_trend(source, trend_level=2)


def test_unknown_or_display_endpoint_never_provides_an_attack() -> None:
    source = source_points(2, False, True)
    assert not any(point['index'] == 17 for point in judge_trend(source, trend_level=2, asof=17).points)
    assert any(point['index'] == 17 for point in judge_trend(source, trend_level=2, asof=18).points)
    for flag in ({'state': 'developing'}, {'display_only': True}):
        altered = deepcopy(source)
        altered[-1].update(flag)
        assert not any(point['index'] == 17 for point in judge_trend(altered, trend_level=2).points)


@pytest.mark.parametrize('change', ['endpoint', 'clock', 'key', 'direction', 'level'])
def test_forged_formal_attack_proof_is_rejected(change: str) -> None:
    source = source_points(2, False, True)
    endpoint = source[-1]
    proof = endpoint['upgrade_evidence']
    assert isinstance(proof, dict)
    if change == 'endpoint':
        proof['confirmed_by']['value'] = 51
    elif change == 'clock':
        proof['available_at'] = 19
    elif change == 'key':
        proof['broken_key']['available_at'] = 17
    elif change == 'direction':
        proof['direction'] = 'down'
    else:
        endpoint['source_level'] = 1
    with pytest.raises(ValueError):
        judge_trend(source, trend_level=2)


def huaci_bars(cutoff: str = '2024-04-30') -> list[Bar]:
    fixture = json.loads((Path(__file__).parent / 'fixtures/huaci_2024_key_break.json').read_text(encoding='utf-8'))
    return [Bar(datetime.fromisoformat(row[0]), fixture['symbol'], *row[1:]) for row in fixture['bars'] if row[0] <= cutoff]


def hierarchy(bars: list[Bar], cache: _TrendJudgementCache | None = None) -> list[dict[str, object]]:
    source = polyline_high_low_points(lecture_drawing(bars))
    levels = []
    for level in (1, 2, 3):
        result = prepare_trend_lines(source, trend_level=level, symbol='sz.001216', bars=bars,
                                     _judgement_cache=cache, _owned_source=cache is not None)
        levels.append(result)
        source = high_low_points_from_strokes(result['strokes'], include_key_break_attacks=True)
    return levels


def points(result: dict[str, object]) -> list[dict[str, object]]:
    return [point for stroke in result['strokes'] for point in stroke['points']]


def test_huaci_real_decline_breaks_2022_secondary_low_with_its_own_clock() -> None:
    bars = huaci_bars()
    quotes = {bar.timestamp.date().isoformat(): bar for bar in bars}
    assert quotes['2022-04-27'].low == 10.88
    assert round(quotes['2024-01-24'].high, 2) == 23.58
    assert round(quotes['2024-02-08'].low, 2) == 10.19
    first, second, _ = hierarchy(bars)
    lower = next(point for point in points(first) if point['time'] == '2024-02-08')
    assert 'wave_completed_at' not in lower
    upper = points(second)
    start, end = upper[-2:]
    assert (start['time'], end['time']) == ('2024-01-24', '2024-02-08')
    assert end['available_at'] == '2024-02-19'
    assert end['upgrade_evidence']['broken_key']['time'] == '2022-04-27'
    assert end['upgrade_evidence']['broken_key']['value'] == 10.88
    assert start['wave_completed_at'] == '2024-02-19'
    assert 'wave_completed_at' not in end


@pytest.mark.parametrize('cutoff', ['2024-01-23', '2024-01-24', '2024-02-08', '2024-02-19', '2024-04-30'])
def test_huaci_prefix_and_cached_geometry_preserve_first_knowledge(cutoff: str) -> None:
    bars = [bar for bar in huaci_bars() if bar.timestamp.date().isoformat() <= cutoff]
    cold = hierarchy(bars)
    cache = _TrendJudgementCache(tail_reuse=True)
    hierarchy(bars[:-1], cache)
    cached = hierarchy(bars, cache)
    assert [row['strokes'] for row in cached] == [row['strokes'] for row in cold]
    endpoint = [point for point in points(cold[1]) if point['time'] == '2024-02-08']
    assert bool(endpoint) is (cutoff >= '2024-02-19')
    if endpoint:
        assert endpoint[0]['available_at'] == '2024-02-19'


def test_lower_continuation_origin_already_beyond_key_keeps_formal_identity() -> None:
    source = source_points(2, False, True)
    source[-2]['value'] = 46
    source[-1]['upgrade_evidence']['origin']['value'] = 46
    formal = formal_key_attack_points(source, source_level=1)
    assert formal[-1]['index'] == 17
    assert 'wave_completed_at' not in formal[-1]


def test_readonly_formal_source_uses_independent_proof_copies() -> None:
    from types import MappingProxyType
    source = source_points(2, False, True)
    expected = judge_trend(source, trend_level=2)
    source[-1]['upgrade_evidence'] = MappingProxyType(source[-1]['upgrade_evidence'])
    assert judge_trend(source, trend_level=2) == expected


def test_huaci_reverse_break_and_bull_flip_run_through_daily_three_level_pipeline() -> None:
    from wavequant.domain.strategies.market_calculations import market_calculations

    bars = huaci_bars('2025-12-31')
    dates = {bar.timestamp.date().isoformat(): index for index, bar in enumerate(bars)}
    assert round(bars[dates['2025-11-24']].high, 2) == 26.84
    assert bars[dates['2025-11-24']].volume == 50564591
    events, snapshots = [], {}
    cutoffs = ('2024-02-08', '2024-02-19', '2025-11-19', '2025-11-20', '2025-11-24', '2025-11-25')

    def collect(day):
        events.extend(deepcopy(event) for event in day.lifecycle_events if event['trend_level'] == 2)
        current = bars[day.frame.index].timestamp.date().isoformat()
        if current in cutoffs:
            snapshots[current] = deepcopy(day.levels[1])

    with market_calculations(tuple(bars), on_day=collect) as work:
        work.calculate_trend_line(3)
    flips = [event for event in events if event['event'] == 'trend_lifecycle_state']
    decline = next(event for event in flips if event.get('flip_index') == dates['2024-02-08'])
    assert decline['state'] == '多翻空'
    assert decline['origin_index'] == dates['2024-01-24']
    assert decline['bar_index'] == dates['2024-02-19']
    rises = [event for event in flips if event.get('origin_index') == dates['2024-02-08'] and event['state'] == '空翻多']
    assert rises[0]['bar_index'] == dates['2025-11-20']
    assert rises[0]['flip_index'] == dates['2025-11-19']
    latest = next(event for event in rises if event['flip_index'] == dates['2025-11-24'])
    assert latest['bar_index'] == dates['2025-11-25']
    assert latest['flip_confirmed'] is True
    high = points(snapshots['2025-11-25'])[-1]
    assert high['time'] == '2025-11-24'
    assert high['upgrade_evidence']['origin']['time'] == '2024-02-08'
    assert high['upgrade_evidence']['broken_key']['time'] == '2024-01-24'
    for cutoff in cutoffs:
        prefix = [bar for bar in bars if bar.timestamp.date().isoformat() <= cutoff]
        cold = hierarchy(prefix)[1]
        assert snapshots[cutoff]['strokes'] == cold['strokes']
    assert all(point['time'] != '2024-02-08' for point in points(snapshots['2024-02-08']))
    assert all(point['time'] != '2025-11-24' for point in points(snapshots['2025-11-24']))
