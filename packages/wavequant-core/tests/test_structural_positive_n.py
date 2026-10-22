"""折线结构确认与交易收盘/资格条件分离。"""

from dataclasses import replace
from datetime import datetime, timedelta
import json
from pathlib import Path

import pytest

from wavequant.domain.models.model import Bar
from wavequant.domain.market_structure.n_shape import (
    BoxAnchorMode, MilestoneBasis, NSetup, NStatus, PivotRef, observe_n,
)
from wavequant.domain.market_structure.price_action import Direction
from wavequant.domain.strategies.integrated_strategy import SystemStrategy
from wavequant.domain.strategies.daily_strategy import daily_signals
from wavequant.interfaces.charts.visualization import ChartRepository


def bars(rows):
    return [Bar(datetime(2022, 1, 1)+timedelta(days=i), 'TEST', *row, 1000)
            for i, row in enumerate(rows)]


def setup(a=PivotRef(0, 0, 0), b=PivotRef(1, 1, 0), c=PivotRef(2, 2, 0)):
    return NSetup('TEST', '1d', Direction.UP, a, b, c, 'lecture_causal',
        BoxAnchorMode.ATTACK_VIRTUAL_EXTREME, polyline_structure=True, allow_confirmation_bar=True)


def observe(data, config=None):
    return observe_n(data, config or setup(), timeframe='1d', milestone_basis=MilestoneBasis.EXTREME)


def test_equal_origin_and_high_only_complete_structure_without_real_break():
    data = bars([(9,10,8,9), (11,12,10,11.5), (9,10,8,9), (11,12.5,9,11)])
    result = observe(data)
    assert result.status == NStatus.COMPLETED
    assert result.completion.bar_index == 3
    assert result.completion.real_break is False
    assert result.targets.one_p == 17
    assert result.targets.two_t == 21.5
    assert observe(data[:3]).completion is None
    with pytest.raises(ValueError, match='partial retracement'):
        observe(data, replace(setup(), polyline_structure=False))


def test_ordered_same_day_impulse_does_not_require_outside_candle():
    data = bars([(11,13,10,12), (10,12,9,11.5), (10,11.8,9.5,11), (11,12.5,10,11)])
    config = setup(PivotRef(1,2,0), PivotRef(1,2,1), PivotRef(2,2,0))
    result = observe(data, config)
    assert result.completion.bar_index == 3
    assert result.completion.real_break is False
    with pytest.raises(ValueError, match='ordered'):
        replace(config, origin=PivotRef(1,2,1), neckline=PivotRef(1,2,0))
    with pytest.raises(ValueError, match='ordered'):
        replace(config, origin=PivotRef(1,2))


@pytest.mark.parametrize('last,status', [
    ((11,12,9,11), NStatus.FORMING),
    ((11,12.5,7.9,11), NStatus.INVALIDATED),
])
def test_equality_and_broken_origin_never_complete(last, status):
    data = bars([(9,10,8,9), (11,12,10,11.5), (9,10,8,9), last])
    assert observe(data).status == status


def test_unknown_pivot_cannot_backdate_high_breakout():
    data = bars([(9,10,8,9), (11,12,10,11.5), (9,10,9,9.5),
                 (11,12.5,9.5,11), (11,13,10,11)])
    config = setup(c=PivotRef(2,4,0))
    assert observe(data[:4], config).status == NStatus.AWAIT_ANCHORS
    assert observe(data, config).completion is None


@pytest.mark.parametrize('filename,day,qualified,anchors,targets', [
    ('guilin_2022_positive_n.json', '2022-05-11', '2022-05-27', ('2022-04-27','2022-04-28','2022-05-09'),
     (6.763865636993591,7.535996417472312)),
    ('haiyangwang_2021_positive_n.json', '2021-11-02', '2021-12-01', ('2021-10-28','2021-10-28','2021-11-01'),
     (21.37847391591191,22.579655355299665)),
])
def test_original_bars_publish_high_break_structure_and_hover_levels(filename, day, qualified, anchors, targets):
    raw = json.loads((Path(__file__).parent/'fixtures'/filename).read_text('utf-8'))
    data = [Bar(**dict(row, timestamp=datetime.fromisoformat(row['timestamp'])))
            for row in raw['bars']]
    config = SystemStrategy(**raw['strategy'])
    dates = {bar.timestamp.date().isoformat(): i for i, bar in enumerate(data)}
    expected = tuple(dates[date] for date in anchors)
    intervals = []
    def on_day(current, analysis, generated, state):
        if current.frame.index < dates[qualified]:
            assert not any(row['event'] == 'n_structure_completed' and row['bar_index'] == dates[day]
                           for row in analysis.structural_n_events)
        intervals[:] = analysis.trading_intervals
    result = daily_signals(data, config, on_day=on_day)
    events = result.audit
    event = next(row for row in events if row['event'] == 'n_structure_completed'
                 and row['bar_index'] == dates[day]
                 and tuple(row[key] for key in ('origin','neckline','pullback')) == expected)
    window = next(interval for interval in intervals if interval.start_index == dates[qualified])
    assert event['scope_low_index'] == window.start_low_index
    assert event['qualification_window_start'] == dates[qualified]
    assert event['known_at'] == dates[qualified]
    assert event['real_break'] is (filename == 'guilin_2022_positive_n.json')
    assert event['target_eligible'] is False
    assert (event['one_p'],event['two_t']) == pytest.approx(targets)
    if anchors[0] == anchors[1]:
        assert event['origin_ordinal'] < event['neckline_ordinal']
    chart = ChartRepository.render_theory(None,data,config,result,data[-1].timestamp.date().isoformat(),
        geometry={'tertiary_trends': {'n_target_trend_confirmation_enabled': config.n_target_trend_confirmation_enabled}})
    published = next(row for row in chart['events'] if row.get('n_id') == event['n_id'])
    assert published['event'] == 'n_completed'
    assert published['available_at'] == qualified
    assert published['time'] == day
    assert published['structural_target'] is True
    assert [level['price'] for level in published['levels'] if level.get('stage') in ('one_p','two_t')] == pytest.approx(targets)
    assert not any(signal.side == 'LONG' and signal.bar_index == dates[day] for signal in result.signals)
    early = ChartRepository.render_theory(None,data[:dates[qualified]],config,result,
        data[dates[qualified]-1].timestamp.date().isoformat(),
        geometry={'tertiary_trends': {'n_target_trend_confirmation_enabled': config.n_target_trend_confirmation_enabled}})
    assert not any(row.get('n_id') == event['n_id'] for row in early['events'])
