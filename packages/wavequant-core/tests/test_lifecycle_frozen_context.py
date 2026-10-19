"""局部升级不替代本级尚未突破的末跌高；使用国芳缓存的缩减结构。"""

from datetime import datetime, timedelta

import pytest

from wavequant.domain.market_structure.trend_lifecycle import _facts
from wavequant.domain.market_structure.trend_lines import _annotate_geometry


def guofang_geometry(level, high_price=5.0680720225540785):
    # 只保留已缓存二级几何的价格顺序，不声称这是完整行情回测。
    prices = [7.95, 11.58, 8.47, 11.5, 4.909509, 6.134356,
              5.06135, 6.772086, 6.07362, 6.923926, 4.905695, 5.7611,
              4.657089, 5.550661, 4.919904, 5.760914, 5.203745, 6.423209,
              4.667602, 8.97474528141774, 3.811046, 5.367671,
              4.07943, 4.380019, 4.050031, high_price]
    days = [(datetime(2021, 1, 1) + timedelta(days=index)).date().isoformat()
            for index in range(len(prices))]
    points = [dict(index=index, ordinal=0, kind='L' if index % 2 == 0 else 'H',
                   value=price, time=days[index], available_at=days[index], state='confirmed')
              for index, price in enumerate(prices)]
    dates = {day: index for index, day in enumerate(days)}
    _annotate_geometry(points, 'sh.601086', dates, trend_level=level)
    high = points[-1]
    high['upgrade_evidence'] = dict(direction='up', role='endpoint', available_at=days[-1],
        origin=dict(points[-2]), broken_key=dict(points[-3]), confirmed_by=dict(high))
    return points, dates


@pytest.mark.parametrize('level', [1, 2, 3])
@pytest.mark.parametrize('high_price', [5.0680720225540785, 7.261939342406007, 8.97474528141774])
def test_later_local_origin_cannot_bypass_earlier_frozen_key(level, high_price):
    points, dates = guofang_geometry(level, high_price)
    assert points[22]['observations'][0]['title'] == '底部疑虑'
    assert points[24]['observations'] == []
    context = points[-1]['lifecycle_context']
    assert context['key']['index'] == 19
    assert context['origin']['index'] == 20
    assert not any(fact.title == '翻空为多' and fact.point.index == 25
                   for fact in _facts(points, dates, 25))


@pytest.mark.parametrize('level', [1, 2, 3])
def test_real_key_break_uses_same_level_bearish_origin(level):
    points, dates = guofang_geometry(level, 9.1)
    high = points[-1]
    formal = [fact for fact in _facts(points, dates, 25)
              if fact.title == '翻空为多' and fact.point.index == 25]
    assert len(formal) == 1 and formal[0].origin.index == 20
    # 没有展示标签时也必须有本级关键位凭据，不能把局部起点24冒充空头低点20。
    high['observations'] = []
    high['upgrade_evidence']['broken_key'] = dict(points[19])
    fallback = [fact for fact in _facts(points, dates, 25)
                if fact.title == '翻空为多' and fact.point.index == 25]
    assert len(fallback) == 1 and fallback[0].origin.index == 20


@pytest.mark.parametrize('context_kind', ['missing', 'future', 'wrong_direction'])
def test_upgrade_requires_current_same_level_context(context_kind):
    points, dates = guofang_geometry(2, 9.1)
    high = points[-1]
    high['observations'] = []
    high['upgrade_evidence']['broken_key'] = dict(points[19])
    if context_kind == 'missing':
        high.pop('lifecycle_context')
    elif context_kind == 'future':
        high['lifecycle_context']['available_at'] = 26
    else:
        high['lifecycle_context']['direction'] = 'down'
    assert not any(fact.title == '翻空为多' and fact.point.index == 25
                   for fact in _facts(points, dates, 25))
