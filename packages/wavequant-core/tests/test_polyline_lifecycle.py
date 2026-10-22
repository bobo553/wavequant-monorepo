"""折线自身六态的确认日、包含峰谷和来源边界，不借用一级趋势。"""

from copy import deepcopy
from datetime import datetime, timedelta

import pytest

from wavequant.domain.market_structure.polyline_lifecycle import PolylineLifecycleSource, polyline_lifecycle_rows
from wavequant.domain.market_structure.trend_point_version import PointVersion
from wavequant.domain.market_structure.trend_lifecycle import TrendLifecycle
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.chart_drawing_history import ChartDrawingFrame


def _scenario(*, mirrored=False):
    start = datetime(2026, 1, 1)
    prices = [(10, 11, 8, 10), (12, 15, 11, 13), (12, 13, 10, 11),
              (12, 14, 11, 13), (10, 12, 9, 10), (12, 16, 11, 14),
              (14, 15, 12, 13), (14, 18, 13, 15), (15, 17.5, 13, 17),
              (15, 16, 11, 12), (12, 14, 12, 13), (12, 13, 10, 12), (11, 12, 9, 10)]
    if mirrored:
        prices = [(30-opened, 30-low, 30-high, 30-close) for opened, high, low, close in prices]
    bars = [Bar(start + timedelta(days=index), 'sz.000678', *price, 100)
            for index, price in enumerate(prices)]
    dates = {bar.timestamp.date().isoformat(): index for index, bar in enumerate(bars)}
    vertices = [(0, 'L', 8), (1, 'H', 15), (2, 'L', 10), (3, 'H', 14),
                (4, 'L', 9), (5, 'H', 16), (6, 'L', 12), (7, 'H', 18),
                (9, 'L', 11), (10, 'H', 14), (11, 'L', 10)]
    if mirrored:
        vertices = [(index, 'H' if kind == 'L' else 'L', 30-price) for index, kind, price in vertices]
    frames = []
    for now in range(len(bars)):
        points = []
        for position, (index, kind, price) in enumerate(vertices):
            if index > now:
                break
            next_index = vertices[position + 1][0] if position + 1 < len(vertices) else len(bars)
            state = 'seed' if position == 0 else 'confirmed' if next_index <= now else 'developing'
            known = next_index if state == 'confirmed' else index
            points.append(dict(index=index, ordinal=0, kind=kind, value=price,
                state=state, time=bars[index].timestamp.date().isoformat(),
                available_at=bars[known].timestamp.date().isoformat()))
        frames.append(ChartDrawingFrame(now, 0, points, {}))
    return bars, dates, frames


def _replay(bars, dates, frames):
    lifecycle = TrendLifecycle()
    events = []
    rows = []
    for now, row in polyline_lifecycle_rows(frames, dates):
        rows.append(row)
        events.extend(lifecycle.advance([row], bars, now, dates))
    return lifecycle, events, rows


@pytest.mark.parametrize('mirrored', [False, True])
def test_owned_polyline_versions_preserve_public_facts_and_append_prefix(mirrored):
    bars, dates, frames = _scenario(mirrored=mirrored)
    owned, public = PolylineLifecycleSource(dates, _owned_tail=True), PolylineLifecycleSource(dates)
    incremental, full = TrendLifecycle(), TrendLifecycle()
    previous = None
    previous_values = None
    for frame in frames:
        actual, expected = owned.advance(frame), public.advance(frame)
        assert actual == expected
        assert incremental.advance([actual], bars, frame.index, dates, _owned_structure=True) == full.advance(
            [expected], bars, frame.index, dates)
        assert incremental.checkpoint() == full.checkpoint()
        if previous is not None:
            assert list(previous) == previous_values
        if actual['strokes']:
            version = actual['strokes'][0]['points']
            assert type(version) is PointVersion and version._sealed
            if previous is not None and version.parent is previous.token:
                assert version.changed_from == len(previous)
                assert all(version[position] is point for position, point in enumerate(previous))
            previous, previous_values = version, deepcopy(list(version))


def test_owned_polyline_path_change_has_no_retained_point_version():
    _, dates, frames = _scenario()
    source = PolylineLifecycleSource(dates, _owned_tail=True)
    for frame in frames[:8]:
        source.advance(frame)
    previous = source._point_version
    assert previous is not None and len(previous)
    assert source.advance(ChartDrawingFrame(8, 8, [], {}))['strokes'] == []
    assert source._point_version is None or source._point_version.parent is None


def test_owned_polyline_truncation_then_append_rebuilds_only_a_new_version():
    _, dates, frames = _scenario()
    owned, public = PolylineLifecycleSource(dates, _owned_tail=True), PolylineLifecycleSource(dates)
    for frame in frames[:8]:
        assert owned.advance(frame) == public.advance(frame)
    previous = owned._point_version
    assert previous is not None and len(previous)
    saved = deepcopy(list(previous))
    shortened = ChartDrawingFrame(8, 0, deepcopy(frames[7].raw[:-3]), {})
    assert owned.advance(shortened) == public.advance(shortened)
    revised = owned._point_version
    assert revised is not None and revised.parent is None and revised.changed_from == 0
    assert len(revised) < len(previous) and list(previous) == saved
    for frame in frames[9:]:
        assert owned.advance(frame) == public.advance(frame)
        assert list(previous) == saved


@pytest.mark.parametrize('mirrored', [False, True])
def test_real_polyline_extrema_supply_full_six_state_cycle(mirrored):
    bars, dates, frames = _scenario(mirrored=mirrored)
    original = deepcopy(frames)
    lifecycle, events, rows = _replay(bars, dates, frames)
    states = [(event['bar_index'], event['state']) for event in events
              if event['event'] == 'trend_lifecycle_state']
    first = ['多翻空', '多空交替', '转空确认'] if mirrored else ['空翻多', '空多交替', '转多确认']
    second = ['空翻多', '空多交替', '转多确认'] if mirrored else ['多翻空', '多空交替', '转空确认']
    assert states == list(zip([6, 7, 8, 10, 11, 12], first + second))
    assert {event['trend_level'] for event in events} == {0}
    assert [point['index'] for point in rows[7]['strokes'][0]['points']] == [1, 2, 3, 4, 5, 6]
    assert frames == original
    assert TrendLifecycle.from_checkpoint(lifecycle.checkpoint()).checkpoint() == lifecycle.checkpoint()


def test_epoch_end_closes_polyline_before_a_later_price_cross():
    bars, dates, frames = _scenario()
    frames[8:] = [ChartDrawingFrame(now, 8, [], {}) for now in range(8, len(bars))]
    _, events, _ = _replay(bars, dates, frames)
    assert any(event.get('reason') == 'source_path_ended' and event['bar_index'] == 8 for event in events)
    assert not any(event.get('state') == '转多确认' for event in events)


def test_source_confirmation_is_not_used_before_its_observation_day():
    bars, dates, frames = _scenario()
    _, full, _ = _replay(bars, dates, frames)
    _, prefix, _ = _replay(bars[:7], dates, frames[:7])
    assert prefix == [event for event in full if event['bar_index'] < 7]
    assert not any(event.get('state') == '空多交替' for event in prefix)


def test_future_raw_neighbor_cannot_make_a_polyline_peak_known_today():
    _, dates, frames = _scenario()
    frame = deepcopy(frames[6])
    frame.raw.append(dict(frames[7].raw[-1]))
    with pytest.raises(ValueError, match='causal frame'):
        list(polyline_lifecycle_rows([frame], dates))


def test_containment_endpoint_kinds_use_shared_price_peak_projection():
    _, dates, frames = _scenario()
    for frame in frames:
        for point in frame.raw:
            if point['index'] == 5:
                point.update(kind='L', edge_kind='teaching')
    rows = list(polyline_lifecycle_rows(frames, dates))
    observed = next(point for point in rows[7][1]['strokes'][0]['points'] if point['index'] == 5)
    assert observed['kind'] == 'H'
    assert observed['available_at'] == 6
    assert observed['observations'][0]['title'] == '翻空为多'


def test_unconfirmed_tail_cannot_create_a_bull_flip():
    bars, dates, frames = _scenario()
    _, events, rows = _replay(bars[:6], dates, frames[:6])
    assert not any(event['event'] == 'trend_lifecycle_state' for event in events)
    assert [point['index'] for point in rows[-1]['strokes'][0]['points']] == [1, 2, 3, 4]
