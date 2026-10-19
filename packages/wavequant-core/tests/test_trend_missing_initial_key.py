"""首个整窗极值没有前驱时，不能永久阻塞后续真实翻向。"""

from copy import deepcopy
from datetime import date, timedelta
from datetime import datetime
import json
from pathlib import Path

import pytest

from wavequant.domain.market_structure.trend_lines import _annotate_geometry
from wavequant.domain.market_structure.trend_lifecycle import _facts
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.market_calculations import market_calculations


@pytest.mark.parametrize('level', [1, 2, 3])
@pytest.mark.parametrize('mirror', [False, True])
@pytest.mark.parametrize('attack', [8.5, 9, 11])
def test_incomplete_background_can_acquire_the_later_opposite_key(level, mirror, attack):
    # 首H12缺少左侧L；局部多头没有有效末升低。随后H9/L4/H6确立空头，
    # 冻结H9而非更小H6，最后攻击必须严格超过H9才是翻多；镜像同理。
    prices = [12, 5, 7, 5.2, 9, 4, 6, 4.5, attack]
    days = [(date(2020, 1, 1) + timedelta(days=i)).isoformat() for i in range(len(prices))]
    dates = {day: i for i, day in enumerate(days)}
    source = [dict(index=i, ordinal=0, time=days[i], available_at=days[i],
                   kind=('L' if i % 2 == 0 else 'H') if mirror else ('H' if i % 2 == 0 else 'L'),
                   value=20-price if mirror else price, state='confirmed') for i, price in enumerate(prices)]
    title = '翻多为空' if mirror else '翻空为多'
    for count in range(1, len(source) + 1):
        points = deepcopy(source[:count])
        _annotate_geometry(points, 'TEST', dates, trend_level=level)
        facts = [fact for fact in _facts(points, dates, count-1) if fact.title == title]
        if count == len(source) and attack > 9:
            assert len(facts) == 1
            assert facts[0].point.index == 8
            assert facts[0].origin.index == 5
            assert facts[0].broken.index == 4
            assert facts[0].known == 8
        else:
            assert facts == []


@pytest.mark.parametrize('level', [1, 2, 3])
def test_guofang_sparse_geometry_retains_2020_key_for_2023_flip(level):
    prices = [11.58, 4.657089031357015, 6.423208573722655, 4.667601647680621,
              8.97474528141774, 3.811046142228825, 5.3676706228575, 4.050031354268106,
              7.261939342406007, 4.437287627806584, 11.024346589744814]
    days = [(date(2020, 1, 1) + timedelta(days=i)).isoformat() for i in range(len(prices))]
    points = [dict(index=i, ordinal=0, time=days[i], available_at=days[i], kind='H' if i % 2 == 0 else 'L',
                   value=price, state='confirmed') for i, price in enumerate(prices)]
    dates = {day: i for i, day in enumerate(days)}
    _annotate_geometry(points, 'TEST', dates, trend_level=level)
    flip = next(fact for fact in _facts(points, dates, 10) if fact.title == '翻空为多')
    assert (flip.broken.index, flip.origin.index, flip.point.index) == (4, 5, 10)


def test_guofang_causal_three_level_pipeline_opens_after_february_low_confirmation():
    fixture = json.loads((Path(__file__).parent / 'fixtures' / 'guofang_2024_impulse_defense.json').read_text('utf-8'))
    bars = tuple(Bar(datetime.fromisoformat(day), fixture['symbol'], *values)
                 for day, *values in fixture['bars'][:1492])
    events = []

    def collect(day):
        events.extend(event for event in day.lifecycle_events if event['trend_level'] == 2)

    with market_calculations(bars, on_day=collect) as work:
        work.calculate_trend_line(3)
        windows = work.trading_intervals(2)
    window = next(window for window in windows if window.start_flip_index == 1354)
    assert window.start_low_index == 1484
    assert window.start_index == 1491
    assert bars[window.start_index].timestamp.date().isoformat() == '2024-02-27'
    assert window.end_index is None
    confirmation = next(event for event in events if event.get('state') == '空多交替'
                        and event.get('flip_index') == 1354)
    assert confirmation['bar_index'] == 1491
    assert confirmation['origin_index'] == 755
    assert confirmation['confirmation_evidence']['key_index'] == 636
    assert confirmation['confirmation_rule'] == 'positive_n_and_squeeze_after_qualified_b'
