"""末跌高后的最低点贯穿测幅、交替确认、资格和交易上下文。"""

from copy import deepcopy
from dataclasses import replace
from datetime import timedelta

import pytest

from tests.test_lifecycle_frozen_context import guofang_geometry
from tests.test_lifecycle_impulse_defense import replay
from tests.test_recovery_alternation_confirmation import n_case
from wavequant.domain.market_structure.trend_lines import _annotate_geometry
from wavequant.domain.market_structure.trend_lifecycle import TrendLifecycle, _facts
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.recovery_entry_history import merge_recovery_entry_history
from wavequant.domain.strategies.trading_intervals import confirmed_trading_intervals, interval_days


@pytest.mark.parametrize('level', [1, 2, 3])
def test_guofang_2020_deep_pullback_uses_november_origin_not_march_local_low(level):
    # 已保存几何价格的缩减回归：11=2019-08-28，12=11-18，16=2020-03-13，
    # 17=03-20，18=04-28。缩减索引不用于真实交易日长度或确认日断言。
    points, dates = guofang_geometry(level)
    facts = _facts(points, dates, 18)
    flip = next(fact for fact in facts if fact.title == '翻空为多' and fact.point.index == 17)
    assert flip.origin.index == 12 and flip.broken.index == 11
    observation = next(item for item in points[18]['observations']
                       if item['title'] == '回档未通过交替条件')
    expected = (points[17]['value'] - points[18]['value']) / (points[17]['value'] - points[12]['value'])
    assert observation['ratio'] == pytest.approx(expected)
    assert 2 / 3 < observation['ratio'] < 1
    assert not any(fact.title == '空多交替' and fact.point.index == 18 for fact in facts)


@pytest.mark.parametrize('level', [1, 2, 3])
def test_same_flip_high_extension_does_not_replace_the_frozen_origin(level):
    points, dates = guofang_geometry(level)
    points = deepcopy(points[:21])
    points[-1]['value'] = 6.3
    _annotate_geometry(points, 'sh.601086', dates, trend_level=level)
    observation = next(item for item in points[-1]['observations'] if item['title'] == '空多交替')
    assert observation['flip_high']['index'] == 19
    assert observation['origin']['index'] == observation['confirmed_bear_low']['index'] == 12
    assert observation['broken_key']['index'] == 11


def ordinary_n_case(level):
    market, turns = n_case()
    first = market[0]
    # 最邻近的H2不是冻结的末跌高H0；最低点L3仍属于H0之后的同一背景。
    prefix = [Bar(first.timestamp - timedelta(days=3 - i), first.symbol, *row)
              for i, row in enumerate([(9, 10, 8.5, 9, 100), (8.2, 8.5, 8, 8.2, 100),
                                        (8.5, 9, 8.4, 8.6, 100)])]
    bars = prefix + market

    def point(index, kind, price, known):
        return dict(index=index, ordinal=0, kind=kind, value=price, available_at=known,
                    time=bars[index].timestamp.date().isoformat(), state='confirmed')

    key, low, local, origin, high = (point(0, 'H', 10, 0), point(1, 'L', 8, 1),
        point(2, 'H', 9, 2), point(3, 'L', 4, 3), point(5, 'H', 16, 6))
    high['observations'] = [dict(title='翻空为多', available_at=6, key=dict(key), confirmed_low=dict(origin))]
    row = dict(trend_level=level, strokes=[dict(id='path', points=[key, low, local, origin, high])])
    polyline = [dict(high), *(point(p.index + 3, p.kind, p.price, p.known + 3) for p in turns)]
    return bars, row, polyline


@pytest.mark.parametrize('level', [0, 1, 2, 3])
@pytest.mark.parametrize('restore_at', [None, 12, 14])
def test_ordinary_flip_can_confirm_polyline_n_before_formal_counter_and_open_permission(level, restore_at):
    bars, row, polyline = ordinary_n_case(level)
    engine, events = replay(bars, row, polyline, restore_at=restore_at)
    confirmations = [event for event in events if event.get('state') == '空多交替']
    assert len(confirmations) == 1
    event = confirmations[0]
    assert event['bar_index'] == 14 and event['counter_index'] == 8
    assert event['origin_index'] == 3 and event['special_bull_recovery'] is False
    assert event['confirmation_rule'] == 'positive_n_and_squeeze_after_qualified_b'
    assert event['confirmation_evidence']['key_index'] == 0
    assert event['confirmation_evidence']['attack'] == 12
    assert engine.checkpoint() == TrendLifecycle.from_checkpoint(engine.checkpoint()).checkpoint()
    if level:
        windows = confirmed_trading_intervals(events, len(bars), level)
        assert interval_days(windows, len(bars)) == (False,) * 14 + (True,)
        history = merge_recovery_entry_history(bars, {}, events)
        assert history[13] == ()
        assert history[14][0].origin_index == 3
        assert history[14][0].alternation_low_index == 8
        assert history[14][0].confirmation_attack == 12
    _, prefix = replay(bars[:14], row, polyline)
    assert not any(event.get('state') == '空多交替' for event in prefix)


@pytest.mark.parametrize('level', [1, 2, 3])
def test_n_without_a_real_same_level_flip_cannot_grant_permission(level):
    bars, row, polyline = ordinary_n_case(level)
    row['strokes'][0]['points'][-1]['observations'] = []
    _, events = replay(bars, row, polyline)
    assert confirmed_trading_intervals(events, len(bars), level) == ()


def test_ordinary_n_permission_closes_when_confirmation_source_is_revised():
    bars, row, polyline = ordinary_n_case(2)
    engine, events = replay(bars, row, polyline)
    bars.append(replace(bars[-1], timestamp=bars[-1].timestamp + timedelta(days=1)))
    polyline[1]['value'] = 7.1
    dates = {bar.timestamp.date().isoformat(): i for i, bar in enumerate(bars)}
    events.extend(engine.advance([row], bars, 15, dates, polyline=polyline))
    assert any(event.get('reason') == 'source_evidence_revised' for event in events)
    assert confirmed_trading_intervals(events, len(bars), 2)[0].end_index == 15
