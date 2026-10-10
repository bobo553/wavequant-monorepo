"""Prove recursive trend sources and ordered Figure 008 confirmation in production."""

import copy
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import datetime, timedelta
import random
from typing import NotRequired, TypedDict, cast
import unittest

from wavequant.domain.market_structure.lecture_trend import reversal_trends
from wavequant.domain.market_structure.secondary_trend import secondary_trends
from wavequant.domain.market_structure.tertiary_trend import tertiary_trends
from wavequant.domain.market_structure.trend_levels import trend_reversals
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.hierarchical_entry import hierarchical_history


class _SourceStroke(TypedDict):
    id: str
    kind: str
    trend_level: int
    points: list[dict[str, object]]


class _TrendSource(TypedDict):
    strokes: list[_SourceStroke]
    structure_strokes: NotRequired[list[_SourceStroke]]


FIGURE_008 = (20, 30, 10, 20, 14, 26, 18, 35, 28, 45, 38, 42, 32, 36, 24, 29, 26, 33, 30, 42)


def _fixture(values: Sequence[float], *, source_level: int) -> tuple[list[Bar], _TrendSource]:
    bars = [Bar(datetime(2026, 1, 1) + timedelta(days=index), 'FIG008', 50, 100, 1, 50, 1000)
            for index in range(len(values) + 1)]
    points: list[dict[str, object]] = []
    for index, value in enumerate(values):
        kind = 'L' if index % 2 == 0 else 'H'
        points.append(dict(index=index, ordinal=0, kind=kind, value=value,
                           time=bars[index].timestamp.date().isoformat(), state='confirmed',
                           available_at=bars[index + 1].timestamp.date().isoformat(),
                           trend_level=source_level, label=f'{kind}{index}'))
    stroke: _SourceStroke = dict(id=f'level{source_level}-figure008',
                                kind='reversal' if source_level == 1 else 'secondary',
                                trend_level=source_level, points=points)
    return bars, dict(strokes=[stroke])


def _geometry(points: Sequence[Mapping[str, object]]) -> list[tuple[object, ...]]:
    return [(point['index'], point['kind'], point['value'], point['available_at']) for point in points]


def _records(value: object) -> list[dict[str, object]]:
    assert isinstance(value, list) and all(isinstance(item, dict) for item in value)
    return cast(list[dict[str, object]], value)


def _result_points(result: Mapping[str, object], field: str = 'strokes') -> list[dict[str, object]]:
    return [point for stroke in _records(result[field]) for point in _records(stroke['points'])]


def _market_fixture(values: Sequence[float], *, source_level: int, mirror: bool = False,
                    first: str = 'L') -> tuple[list[Bar], _TrendSource]:
    bars, source = _fixture(values, source_level=source_level)
    for point in source['strokes'][0]['points']:
        index = cast(int, point['index'])
        value = cast(float, point['value'])
        kind = point['kind']
        if first == 'H':
            kind = 'H' if kind == 'L' else 'L'
        if mirror:
            kind = 'H' if kind == 'L' else 'L'
            value = 100 - value
        point.update(kind=kind, value=value, label=f'{kind}{index}')
        low, high = (value, value + 1) if kind == 'L' else (value - 1, value)
        middle = (low + high) / 2
        bars[index] = Bar(bars[index].timestamp, 'FIG008', middle, high, low, middle, 1000)
    last = bars[len(values) - 1]
    bars[-1] = replace(last, timestamp=bars[-1].timestamp)
    return bars, source


def _primary_market_fixture(values: Sequence[float], *, mirror: bool) -> tuple[list[Bar], _TrendSource]:
    bars, source = _market_fixture((40, *values, 30), source_level=0, mirror=mirror, first='H')
    raw = source['strokes'][0]['points']
    raw[0]['state'] = 'seed'
    raw[-1].update(state='developing', available_at=raw[-1]['time'])
    return bars[:-1], source


def _market_history() -> list[Bar]:
    random_source = random.Random(18)
    bars: list[Bar] = []
    price = 30.0
    for index in range(250):
        close = max(10, price + random_source.uniform(-3, 3))
        high = max(close, price) + random_source.uniform(.1, 2)
        low = min(close, price) - random_source.uniform(.1, 2)
        bars.append(Bar(datetime(2020, 1, 1) + timedelta(days=index), 'TEST', price, high, low, close,
                        random_source.randint(100000, 2000000)))
        price = close
    return bars


class TrendLevelPipelineTests(unittest.TestCase):
    def test_ordered_peak_confirmation_remains_independent_of_immediate_key_upgrades(self) -> None:
        values = (*FIGURE_008[:17], 50, 30, 55, 35, 53, 29, 48)
        for level in (1, 2, 3):
            with self.subTest(trend_level=level):
                _, source = _fixture(values, source_level=level - 1)
                ordered = trend_reversals(source['strokes'][0]['points'], trend_level=level)
                self.assertEqual([(point['kind'], point['value']) for point in ordered],
                                 [('L', 10), ('H', 45), ('L', 24), ('H', 55)])
                field = 'confirmed_on_turn' if level == 1 else f'confirmed_on_level{level - 1}'
                self.assertEqual(ordered[-1][field], 23)

    def test_strict_low_key_break_publishes_a_mirrored_endpoint_without_a_later_holding_high(self) -> None:
        values = (*FIGURE_008[:17], 50)
        for level, derive in ((1, reversal_trends), (2, secondary_trends), (3, tertiary_trends)):
            with self.subTest(trend_level=level):
                bars, source = (_primary_market_fixture(values, mirror=True) if level == 1 else
                                _market_fixture(values, source_level=level - 1, mirror=True))
                result = derive(source, bars)
                published = _result_points(result)
                self.assertEqual([(point['kind'], point['value']) for point in published[-2:]], [('H', 76), ('L', 50)])
                endpoint = published[-1]
                position_field = 'source_turn_position' if level == 1 else f'source_level{level - 1}_position'
                confirmation_field = 'confirmed_on_turn' if level == 1 else f'confirmed_on_level{level - 1}'
                # 来源位置相对于规范化高低点，原折线seed不再占一个来源位置。
                self.assertEqual(endpoint[position_field], 17)
                self.assertEqual(endpoint[confirmation_field], 17)
                source_endpoint = source['strokes'][0]['points'][18 if level == 1 else 17]
                self.assertEqual(endpoint['index'], source_endpoint['index'])
                self.assertEqual(endpoint['available_at'], source_endpoint['available_at'])
                evidence = cast(Mapping[str, object], endpoint['upgrade_evidence'])
                self.assertEqual(evidence['direction'], 'down')
                key = cast(Mapping[str, object], evidence['broken_key'])
                attack = cast(Mapping[str, object], evidence['confirmed_by'])
                self.assertEqual((key['value'], attack['value']), (55, 50))
                self.assertLess(cast(str, key['available_at']), cast(str, attack['time']))
                legs = [leg for stroke in _records(result['strokes']) for leg in _records(stroke['confirmed_legs'])]
                self.assertTrue(any(leg['direction'] == 'down'
                                    and [point['value'] for point in _records(leg['points'])] == [76, 50] for leg in legs))

    def test_equal_or_late_known_low_key_never_publishes_a_mirrored_direct_upgrade(self) -> None:
        for level, derive in ((2, secondary_trends), (3, tertiary_trends)):
            for final, late_key in ((45, False), (50, True)):
                with self.subTest(trend_level=level, final=final, late_key=late_key):
                    bars, source = _market_fixture((*FIGURE_008[:17], final), source_level=level - 1, mirror=True)
                    raw = source['strokes'][0]['points']
                    if late_key:
                        for point in raw[9:17]:
                            point['available_at'] = max(cast(str, point['available_at']), cast(str, raw[17]['time']))
                    result = derive(source, bars)
                    self.assertFalse(any(cast(Mapping[str, object], point.get('upgrade_evidence', {})).get('role') == 'endpoint'
                                         and point['index'] == 17 for point in _result_points(result)))

    def test_figure008_ordered_confirmation_is_identical_at_all_three_levels(self) -> None:
        geometries = []
        for level in (1, 2, 3):
            with self.subTest(trend_level=level):
                _, source = _fixture(FIGURE_008, source_level=level - 1)
                raw = source['strokes'][0]['points']
                before = copy.deepcopy(raw)
                points = trend_reversals(raw, trend_level=level)
                geometries.append(_geometry(points))
                self.assertEqual([(point['kind'], point['value']) for point in points],
                                 [('L', 10), ('H', 45), ('L', 24)])
                self.assertEqual([point['trend_level'] for point in points], [level] * 3)
                self.assertEqual([point['source_level'] for point in points], [level - 1] * 3)
                field = 'confirmed_on_turn' if level == 1 else f'confirmed_on_level{level - 1}'
                self.assertEqual([point[field] for point in points], [6, 13, 18])
                self.assertEqual(raw, before)
        self.assertEqual(geometries[0], geometries[1])
        self.assertEqual(geometries[1], geometries[2])

    def test_secondary_and_tertiary_production_use_same_ordered_candidates(self) -> None:
        candidates = []
        for level, derive in ((2, secondary_trends), (3, tertiary_trends)):
            with self.subTest(trend_level=level):
                bars, source = _fixture(FIGURE_008, source_level=level - 1)
                before = copy.deepcopy(source)
                result = derive(source, bars)
                points = _result_points(result, 'candidate_strokes')
                candidates.append(_geometry(points))
                self.assertEqual([(point['kind'], point['value']) for point in points],
                                 [('L', 10), ('H', 45), ('L', 24)])
                self.assertEqual([point[f'confirmed_on_level{level - 1}'] for point in points], [6, 13, 18])
                self.assertEqual((result['trend_level'], result['source_level']), (level, level - 1))
                self.assertEqual(source, before)
                # 高低点决定完整趋势线；占位行情只影响独立策略资格。
                self.assertEqual([(point['kind'], point['value']) for point in _result_points(result)],
                                 [('L', 10), ('H', 45), ('L', 24)])
                self.assertEqual([(point['kind'], point['value']) for point in _result_points(result, 'qualified_strokes')],
                                 [('H', 45)])
                tails = _records(result['developing_strokes'])
                self.assertEqual(len(tails), 1)
                tail = tails[0]
                self.assertEqual((tail['state'], tail['display_only'], tail['wave_direction']), ('candidate', True, 'up'))
                self.assertEqual(_geometry(_records(tail['points'])),
                                 [(14, 'L', 24, '2026-01-20'), (19, 'H', 42, '2026-01-21')])
                self.assertEqual(tail['confirmed_legs'], [])
                self.assertNotIn('confirmation', tail)
        self.assertEqual(candidates[0], candidates[1])

    def test_private_structure_candidates_never_supply_higher_level_input(self) -> None:
        for level, derive in ((2, secondary_trends), (3, tertiary_trends)):
            with self.subTest(trend_level=level):
                bars, private = _fixture(FIGURE_008, source_level=level - 1)
                private_only: _TrendSource = dict(strokes=[], structure_strokes=private['strokes'])
                result = derive(private_only, bars)
                self.assertEqual(result['input_turn_count'], 0)
                self.assertEqual(result['candidate_strokes'], [])
                self.assertEqual(result['strokes'], [])
                self.assertEqual(result['developing_strokes'], [])

    def test_private_extra_points_do_not_change_published_source_result(self) -> None:
        for level, derive in ((2, secondary_trends), (3, tertiary_trends)):
            with self.subTest(trend_level=level):
                bars, public = _fixture(FIGURE_008, source_level=level - 1)
                baseline = derive(public, bars)
                contaminated = copy.deepcopy(public)
                _, extra = _fixture((20, 30, 10, 20, 14, 50, 18, 60), source_level=level - 1)
                contaminated['structure_strokes'] = extra['strokes']
                before = copy.deepcopy(contaminated)
                self.assertEqual(derive(contaminated, bars), baseline)
                self.assertEqual(contaminated, before)

    def test_unfinished_or_display_only_point_cannot_supply_ordered_confirmation(self) -> None:
        for level in (1, 2, 3):
            for state in ('seed', 'developing', 'display_only'):
                with self.subTest(trend_level=level, state=state):
                    _, source = _fixture(FIGURE_008[:7], source_level=level - 1)
                    points = source['strokes'][0]['points']
                    if state == 'display_only':
                        points[-1]['display_only'] = True
                    else:
                        points[-1]['state'] = state
                    self.assertEqual(trend_reversals(points, trend_level=level), [])

    def test_unknown_mixed_or_equal_structure_never_confirms_a_wave(self) -> None:
        for level in (1, 2, 3):
            for values in ((), (10,), (10, 20, 10, 20, 10, 20), (10, 20, 12, 22, 11, 24, 11, 24)):
                with self.subTest(trend_level=level, values=values):
                    _, source = _fixture(values, source_level=level - 1)
                    self.assertEqual(trend_reversals(source['strokes'][0]['points'], trend_level=level), [])

    def test_structural_prefixes_freeze_price_coordinates_and_confirmation_dates(self) -> None:
        for level in (1, 2, 3):
            with self.subTest(trend_level=level):
                _, source = _fixture(FIGURE_008, source_level=level - 1)
                raw = source['strokes'][0]['points']
                full = trend_reversals(raw, trend_level=level)
                field = 'confirmed_on_turn' if level == 1 else f'confirmed_on_level{level - 1}'
                for end in range(len(raw) + 1):
                    self.assertEqual(trend_reversals(raw[:end], trend_level=level),
                                     [point for point in full if cast(int, point[field]) < end])

    def test_recursive_production_uses_every_lower_level_geometric_point_without_strategy_filtering(self) -> None:
        bars, source = _fixture(FIGURE_008, source_level=1)
        level2 = secondary_trends(source, bars)
        self.assertEqual([(point['kind'], point['value']) for point in _result_points(level2, 'structure_strokes')],
                         [('L', 10), ('H', 45), ('L', 24)])
        level3 = tertiary_trends(level2, bars)
        self.assertEqual([(point['kind'], point['value']) for point in _result_points(level2, 'qualified_strokes')], [('H', 45)])
        self.assertEqual(level3['input_turn_count'], 3)
        self.assertEqual([(point['kind'], point['value']) for point in _result_points(level3, 'source_strokes')],
                         [('L', 10), ('H', 45), ('L', 24)])
        self.assertEqual(level3['strokes'], [])
        self.assertEqual(_result_points(level3, 'candidate_strokes'), [])

    def test_primary_production_metadata_identifies_polyline_source_and_formal_wave(self) -> None:
        # Padding supplies the neighboring vertices required to identify every
        # inner Figure 008 point as a confirmed base-polyline reversal.
        values = (40, *FIGURE_008, 28)
        bars = [Bar(datetime(2026, 1, 1) + timedelta(days=index), 'FIG008', value, value + 1,
                    value - 1, value + .1, 1000) for index, value in enumerate(values)]
        points = [dict(index=index, ordinal=0, time=bar.timestamp.date().isoformat(), value=value,
                       kind='H' if index % 2 == 0 else 'L',
                       state='seed' if index == 0 else 'developing' if index == len(values) - 1 else 'confirmed',
                       available_at=bars[min(index + 1, len(bars) - 1)].timestamp.date().isoformat())
                  for index, (bar, value) in enumerate(zip(bars, values))]
        drawing = dict(strokes=[dict(id='figure008', points=points)])
        before = copy.deepcopy(drawing)
        result = reversal_trends(drawing, bars)
        self.assertEqual(result['trend_level'], 1)
        candidates = _result_points(result, 'candidate_strokes')
        self.assertEqual([(point['index'], point['kind'], point['value']) for point in candidates],
                         [(3, 'L', 10), (10, 'H', 45), (15, 'L', 24)])
        self.assertEqual([point['confirmed_on_turn'] for point in candidates], [6, 13, 18])
        self.assertTrue(all(point['trend_level'] == 1 and point['source_level'] == 0 for point in candidates))
        self.assertEqual(drawing, before)

    def test_daily_history_higher_levels_reference_only_lower_level_geometric_points(self) -> None:
        bars = _market_history()
        snapshots: dict[int, dict[int, tuple[dict[str, object], ...]]] = {}
        hierarchical_history(bars, structure_sink=snapshots)
        self.assertGreater(max(len(levels[2]) for levels in snapshots.values()), 0)
        identity_fields = ('index', 'ordinal', 'kind', 'value')
        for now, levels in snapshots.items():
            self.assertEqual(set(levels), {1, 2, 3})
            for level in (2, 3):
                lower = {tuple(point[field] for field in identity_fields): point for point in levels[level - 1]}
                for point in levels[level]:
                    identity = tuple(point[field] for field in identity_fields)
                    self.assertIn(identity, lower, (now, level, identity))
                    self.assertGreaterEqual(cast(int, point['available_at']), cast(int, lower[identity]['available_at']))
                    self.assertLessEqual(cast(int, point['available_at']), now)
                    price_field = 'high' if point['kind'] == 'H' else 'low'
                    self.assertEqual(point['value'], getattr(bars[cast(int, point['index'])], price_field))

    def test_daily_history_prefix_and_partial_bar_cache_keep_recursive_sources_causal(self) -> None:
        bars = _market_history()
        full, epochs = hierarchical_history(bars)
        cache: dict[str, object] = {}
        for end in (160, 200, 250):
            with self.subTest(end=end):
                prefix = bars[:end]
                expected = hierarchical_history(prefix)
                cached = hierarchical_history(prefix, prefix_cache=cache)
                self.assertEqual(cached, expected)
                self.assertEqual(cached[0][end - 1], full[end - 1])
                self.assertEqual(cached[1][end - 1], epochs[end - 1])
                checkpoint = copy.deepcopy(cache['checkpoint'])
                partial = [*prefix[:-1], replace(prefix[-2], timestamp=prefix[-1].timestamp)]
                self.assertEqual(hierarchical_history(partial, prefix_cache=cache), hierarchical_history(partial))
                self.assertEqual(cache['checkpoint'], checkpoint)
                self.assertEqual(hierarchical_history(prefix, prefix_cache=cache), expected)


if __name__ == '__main__':
    unittest.main()
