"""延伸回调使用当前已确认最低点，保留原翻向与真实确认时钟。"""

from dataclasses import replace
from datetime import datetime, timedelta
import json
from pathlib import Path

import pytest

from wavequant.domain.market_structure.bull_alternation_confirmation import AlternationProof, confirm_bull_alternation
from wavequant.domain.market_structure.trend_lifecycle import TrendLifecycle, _Pivot, _facts
from wavequant.domain.market_structure.trend_lines import _annotate_geometry
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.market_calculations import market_calculations
from wavequant.domain.strategies.trading_intervals import confirmed_trading_intervals


def counter_case() -> tuple[list[Bar], tuple[_Pivot, ...]]:
    # 仅借用用户报价构造缩减结构，不把缩减日期当作实股确认日。
    rows = [(35, 36.33, 34, 35), (16, 18, 15.34, 17), (20, 25, 19, 24),
            (35, 38.06, 34, 37), (32, 35, 31, 33), (30, 31, 29, 30),
            (31, 32, 30, 31), (24, 25, 22.63, 24), (25, 26, 24, 25),
            (35, 39, 34, 38), (38, 38.5, 37, 38)]
    bars = [Bar(datetime(2020, 1, 1) + timedelta(days=index), 'TEST', *row, 100)
            for index, row in enumerate(rows)]
    points = (_Pivot(0, 0, 'H', 36.33, 0), _Pivot(1, 0, 'L', 15.34, 1),
              _Pivot(3, 0, 'H', 38.06, 4), _Pivot(5, 0, 'L', 29, 6),
              _Pivot(6, 0, 'H', 32, 7), _Pivot(7, 0, 'L', 22.63, 8),
              _Pivot(9, 0, 'H', 39, 10))
    return bars, points


@pytest.mark.parametrize('level', [1, 2, 3])
@pytest.mark.parametrize('mirror', [False, True])
@pytest.mark.parametrize('attack', [35, 36, 38])
def test_failed_pending_flip_records_strict_opposite_break_with_frozen_extremes(
    level: int, mirror: bool, attack: float,
) -> None:
    prices = [20, 30, 26, 36, 15, 33, 23, attack]
    points = [dict(index=index, ordinal=0, kind=('H' if index % 2 == 0 else 'L') if mirror
                   else ('L' if index % 2 == 0 else 'H'), value=60 - price if mirror else price,
                   time=str(index), available_at=index if index < 7 else 8, state='confirmed')
              for index, price in enumerate(prices)]
    title = '翻多为空' if mirror else '翻空为多'
    for size in range(1, len(points) + 1):
        prefix = [dict(point) for point in points[:size]]
        _annotate_geometry(prefix, 'TEST', {index: index for index in range(10)}, trend_level=level)
        assert not any(fact.title == title and fact.point.index == 7 for fact in _facts(prefix, {}, 7))
        flips = [fact for fact in _facts(prefix, {}, 8) if fact.title == title and fact.point.index == 7]
        if size == 8 and attack > 36:
            assert len(flips) == 1
            assert (flips[0].broken.index, flips[0].origin.index, flips[0].known) == (3, 4, 8)
            context = prefix[-1]['lifecycle_context']
            assert (context['key']['index'], context['origin']['index']) == (3, 4)
            assert context['direction'] == ('down' if mirror else 'up')
        else:
            assert flips == []


@pytest.mark.parametrize('level', [1, 2, 3])
@pytest.mark.parametrize('mirror', [False, True])
def test_opposite_break_cannot_use_a_key_first_known_after_attack(level: int, mirror: bool) -> None:
    prices = [20, 30, 26, 36, 15, 33, 23, 38]
    points = [dict(index=index, ordinal=0, kind=('H' if index % 2 == 0 else 'L') if mirror
                   else ('L' if index % 2 == 0 else 'H'), value=60 - price if mirror else price,
                   time=str(index), available_at=8 if index >= 3 else index, state='confirmed')
              for index, price in enumerate(prices)]
    _annotate_geometry(points, 'TEST', {index: index for index in range(10)}, trend_level=level)
    title = '翻多为空' if mirror else '翻空为多'
    assert not any(fact.title == title and fact.point.index == 7 for fact in _facts(points, {}, 8))


@pytest.mark.parametrize('level', [0, 1, 2, 3])
def test_extended_counter_requires_its_own_confirmed_rebreak(level: int) -> None:
    bars, points = counter_case()

    def observe(now: int) -> AlternationProof | None:
        return confirm_bull_alternation(bars, now, broken=points[0], origin=points[1], high=points[2],
            points=tuple(point for point in points if point.known <= now), polyline=(), level=level, path='path')

    first = observe(6)
    assert first is not None and first.low == points[3]
    assert observe(7) is None  # 新低尚未确认，已经破位的旧低不能复活。
    assert observe(8) is None  # 深回撤低点确认本身不授予交替。
    assert observe(9) is None  # 再突破高点尚未确认，不能用未来点。
    proof = observe(10)
    assert proof is not None and proof.low == points[5]
    assert proof.rule == 'confirmed_higher_pullback_then_confirmed_flip_high_rebreak'
    assert proof.trend_source == (points[5], points[6])


@pytest.mark.parametrize('failure', ['equal_rebreak', 'broken_counter', 'broken_origin', 'missing_counter'])
def test_counter_extension_does_not_waive_original_confirmation_gates(failure: str) -> None:
    bars, original = counter_case()
    points = list(original)
    if failure == 'equal_rebreak':
        points[-1] = replace(points[-1], price=original[2].price)
    elif failure == 'broken_counter':
        bars[8] = replace(bars[8], low=22.62)
    elif failure == 'broken_origin':
        bars[8] = replace(bars[8], low=15.33)
    else:
        points.pop(5)
    assert confirm_bull_alternation(bars, 10, broken=original[0], origin=original[1], high=original[2],
        points=points, polyline=(), level=2, path='path') is None


def test_equal_later_counter_keeps_earliest_low_and_knowledge() -> None:
    bars, original = counter_case()
    bars[8] = replace(bars[8], low=22.63)
    later = _Pivot(8, 0, 'L', 22.63, 9)
    points = (*original[:-1], later, original[-1])
    proof = confirm_bull_alternation(bars, 10, broken=points[0], origin=points[1], high=points[2],
        points=points, polyline=(), level=2, path='path')
    assert proof is not None and proof.low == original[5]


def test_old_first_counter_checkpoint_cannot_hide_a_new_confirmation() -> None:
    checkpoint = TrendLifecycle().checkpoint()
    with pytest.raises(ValueError, match='incompatible lifecycle checkpoint'):
        TrendLifecycle.from_checkpoint(('six_state_strict_key_background_v26', *checkpoint[1:]))


@pytest.mark.parametrize('level', [1, 2, 3])
@pytest.mark.parametrize('restore_at', [None, 8, 10])
def test_lifecycle_reconfirms_extended_counter_with_frozen_flip(level: int, restore_at: int | None) -> None:
    bars, pivots = counter_case()
    dates = {bar.timestamp.date().isoformat(): index for index, bar in enumerate(bars)}
    points: list[dict[str, object]] = [dict(index=point.index, ordinal=point.ordinal, kind=point.kind, value=point.price,
                   available_at=point.known, time=bars[point.index].timestamp.date().isoformat(), state='confirmed')
              for point in pivots]
    points[2]['observations'] = [dict(title='翻空为多', available_at=4,
                                     key=dict(points[0]), confirmed_low=dict(points[1]))]
    row = dict(trend_level=level, strokes=[dict(id='path', points=points)])
    engine, events = TrendLifecycle(), []
    for now in range(len(bars)):
        if now == restore_at:
            engine = TrendLifecycle.from_checkpoint(engine.checkpoint())
        events.extend(engine.advance([row], bars, now, dates))
    confirmations = [event for event in events if event['event'] == 'trend_lifecycle_state'
                     and event.get('state') == '空多交替']
    assert [(event['bar_index'], event['counter_index']) for event in confirmations] == [(6, 5), (10, 7)]
    assert all(event['origin_index'] == 1 and event['flip_index'] == 3 for event in confirmations)
    assert confirmations[-1]['flip_known_index'] == 4
    assert confirmed_trading_intervals(events, len(bars), level)[0].start_index == 6


def test_xidian_reported_wave_reaches_shared_secondary_alternation_and_permission() -> None:
    # 真实行情端到端回归；确认日期由因果六态给出，不用价格发生日回填。
    fixture = json.loads((Path(__file__).parent / 'fixtures/xidian_2026_deep_squeeze.json').read_text('utf8'))
    bars = tuple(Bar(datetime.fromisoformat(day), fixture['symbol'], *values) for day, *values in fixture['bars'])
    dates = {bar.timestamp.date().isoformat(): index for index, bar in enumerate(bars)}
    events, permissions = [], []

    def collect(day):
        events.extend(event for event in day.lifecycle_events if event['trend_level'] == 2)
        permissions.append(day.permits_buy(2))

    with market_calculations(bars, on_day=collect) as work:
        work.calculate_trend_line(3)
        windows = work.trading_intervals(2)
    target = [event for event in events if event['event'] == 'trend_lifecycle_state'
              and event.get('state') == '空多交替' and event.get('origin_index') == dates['2024-02-08']
              and event.get('flip_index') == dates['2025-08-11']
              and event.get('counter_index') == dates['2026-07-21']]
    assert target, 'reported secondary wave must produce a causal alternation confirmation'
    first = target[0]
    assert first['confirmation_evidence']['key_index'] == dates['2023-08-11']
    assert first['bar_index'] == dates['2026-08-07']
    assert first['flip_known_index'] == dates['2025-08-12']
    assert first['confirmation_rule'] == 'body_gap_volume_bullish_breakout_after_defended_low'
    assert not any(event.get('state') == '空多交替' and event.get('counter_index') == dates['2026-07-21']
                   and event['bar_index'] < dates['2026-08-07'] for event in events)
    assert permissions[first['bar_index']]
    assert any(window.start_index <= first['bar_index']
               and (window.end_index is None or first['bar_index'] < window.end_index) for window in windows)
