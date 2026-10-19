"""下级翻空后续下降破前低；结构证据与六态的原攻击低点分开。"""

from copy import deepcopy
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path

import pytest

from tests.test_source_bear_exit import opening, parent
import wavequant.domain.market_structure.trend_lifecycle as lifecycle_module
from wavequant.domain.market_structure.source_bear_exit import (
    SOURCE_BEAR_EVENT, SOURCE_BEAR_EXIT, STRUCTURE_BEAR_EVENT,
    bearish_structure_breaks, confirmed_source_bear, source_bearish_exits,
)
from wavequant.domain.market_structure.trend_lifecycle import TrendLifecycle
from wavequant.domain.market_structure.failed_breakout_exit import FAILED_BREAKOUT_EVENT
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.market_calculations import market_calculations
from wavequant.domain.strategies.trading_intervals import TradingEligibility, confirmed_trading_intervals


def sample():
    root = Path(__file__).parent / 'fixtures'
    data = json.loads((root / 'xianfeng_2020_bear_exit_bars.json').read_text(encoding='utf-8'))
    bars = [Bar(datetime.fromisoformat(day), data['symbol'], *prices) for day, *prices in data['bars']]
    points = json.loads((root / 'xianfeng_2020_bear_exit_geometry.json').read_text(encoding='utf-8'))['points']
    return bars, dict(id='lower', source_path='source', points=points)


@pytest.mark.parametrize('level', [1, 2, 3])
def test_same_structure_rule_has_no_level_specific_threshold(level):
    bars, stroke = sample()
    dates = {bar.timestamp.date().isoformat(): i for i, bar in enumerate(bars)}
    now = dates['2020-11-26']
    # 坐标/实体完全相同；只换所属级别，不重新解释结构规则。
    for point in stroke['points']:
        point['trend_level'], point['source_level'] = level, level - 1
    # 用同一真实 H/L 及旧低构成严格破位证明；零级专属四点身份证书不能改名跨级。
    rebound = stroke['points'][3]
    completion = rebound['wave_completion_evidence']
    high, _, old_low, low = completion['definition_evidence']
    rebound['wave_completion_evidence'] = {
        **{key: completion[key] for key in ('index', 'ordinal', 'kind', 'value', 'time',
                                           'available_at', 'wave_direction_before', 'wave_direction_after')},
        'upgrade_evidence': dict(direction='down', role='origin', available_at=completion['available_at'],
                                 origin=high, broken_key=old_low, confirmed_by=low),
    }
    before = deepcopy(stroke)
    assert bearish_structure_breaks(stroke, level, bars, now - 1, dates) == []
    events = bearish_structure_breaks(stroke, level, bars, now, dates)
    assert stroke == before and len(events) == 1
    proof = events[0]
    assert proof['breakout_level'] == 4.29 and proof['market_low'] == 4.25
    assert proof['flip_index'] == dates['2020-09-10']
    assert proof['prior_flip_index'] == dates['2020-08-21']
    assert confirmed_source_bear(proof, now)
    assert not confirmed_source_bear(proof, now - 1)
    if level < 3:
        gate = TradingEligibility(level + 1)
        gate.observe(opening(level + 1))
        exits = source_bearish_exits([parent(level + 1)], events, now)
        assert gate.observe(exits[0]) is False
        assert gate.intervals[0].end_key_price == 4.29


@pytest.mark.parametrize('change', ['no_flip', 'future_low', 'future_completion', 'developing',
                                    'touch', 'accepted_high', 'later_high', 'missing_completion'])
def test_incomplete_or_superseded_structure_does_not_close_permission(change):
    bars, stroke = sample()
    dates = {bar.timestamp.date().isoformat(): i for i, bar in enumerate(bars)}
    now = dates['2020-11-26']
    _, anchor, flip, rebound, key = stroke['points']
    if change == 'no_flip':
        flip['observations'] = []
    elif change == 'future_low':
        key['available_at'] = now + 1
    elif change == 'future_completion':
        rebound['wave_completed_at'] = now + 1
        rebound['wave_completion_evidence']['available_at'] = now + 1
    elif change == 'developing':
        key['state'] = 'developing'
    elif change == 'touch':
        bars[now] = replace(bars[now], low=4.29, close=4.30)
    elif change == 'accepted_high':
        rebound['price_body']['close'] = anchor['price_body']['close'] + .01
    elif change == 'later_high':
        bars[now - 3] = replace(bars[now - 3], high=rebound['value'] + .01)
    else:
        rebound.pop('wave_completion_evidence')
    assert bearish_structure_breaks(stroke, 1, bars, now, dates) == []


def test_checkpoint_and_separate_level_replay_preserve_single_exit():
    bars, stroke = sample()
    dates = {bar.timestamp.date().isoformat(): i for i, bar in enumerate(bars)}
    now = dates['2020-11-26']
    # 再次上穿/下穿同一前低不得让旧下降证明关闭一个新窗口。
    bars[now + 1] = replace(bars[now + 1], low=4.30, high=4.50, close=4.40)
    bars[now + 2] = replace(bars[now + 2], low=4.20, high=4.50, close=4.25)
    lower, upper = dict(trend_level=1, strokes=[stroke]), parent(2)
    combined, child, owner = TrendLifecycle(), TrendLifecycle(), TrendLifecycle()
    all_exits = []
    for index in range(len(bars)):
        together = combined.advance([lower, upper], bars, index, dates)
        source = child.advance([lower], bars, index, dates)
        separate = owner.advance([upper], bars, index, dates, source_events=source)
        exits = [row for row in together if row['event'] == SOURCE_BEAR_EVENT]
        assert exits == [row for row in separate if row['event'] == SOURCE_BEAR_EVENT]
        all_exits.extend(exits)
        if index in (now - 1, now):
            combined = TrendLifecycle.from_checkpoint(combined.checkpoint())
            child = TrendLifecycle.from_checkpoint(child.copy().checkpoint())
            owner = TrendLifecycle.from_checkpoint(owner.checkpoint())
    assert [event['bar_index'] for event in all_exits] == [dates['2020-09-09'], now]
    assert [event['source_confirmation']['event'] for event in all_exits] == [
        FAILED_BREAKOUT_EVENT, STRUCTURE_BEAR_EVENT]


@pytest.mark.parametrize('level', [1, 2, 3])
def test_owned_bear_candidate_matches_daily_parse_and_still_confirms_on_new_price(level, monkeypatch):
    bars, stroke = sample()
    dates = {bar.timestamp.date().isoformat(): index for index, bar in enumerate(bars)}
    row = dict(trend_level=level, strokes=[stroke])
    expected = TrendLifecycle()
    baseline = [expected.advance([row], bars, index, dates) for index in range(len(bars))]
    original = lifecycle_module._prepare_bearish_structure
    prepared = []

    def record(stroke, now, dates, *, facts=None):
        prepared.append(now)
        return original(stroke, now, dates, facts=facts)

    monkeypatch.setattr(lifecycle_module, '_prepare_bearish_structure', record)
    reused = TrendLifecycle()
    actual = [reused.advance([row], bars, index, dates, _owned_structure=True)
              for index in range(len(bars))]
    assert actual == baseline
    breaks = [event for day in actual for event in day if event['event'] == STRUCTURE_BEAR_EVENT]
    assert [event['bar_index'] for event in breaks] == [dates['2020-11-26']]
    # 行情每天推进，但结构只在知识时钟变化时解析；没有按日重新扫描旧趋势。
    assert len(prepared) < len(bars) // 2


def test_owned_candidate_rebuilds_for_revised_or_late_completion_and_checkpoint():
    bars, original = sample()
    dates = {bar.timestamp.date().isoformat(): index for index, bar in enumerate(bars)}
    now = dates['2020-11-26']
    delayed = deepcopy(original)
    delayed['points'][3]['wave_completed_at'] = now + 1
    delayed['points'][3]['wave_completion_evidence']['available_at'] = now + 1
    ordinary, owned = TrendLifecycle(), TrendLifecycle()
    observed = []
    for index in range(len(bars)):
        # 旧版本不可原地改写；证据修订必须使用新结构版本。
        stroke = delayed if index >= now - 2 else original
        row = dict(trend_level=1, strokes=[stroke])
        expected = ordinary.advance([row], bars, index, dates)
        actual = owned.advance([row], bars, index, dates, _owned_structure=True)
        assert actual == expected
        observed.extend(actual)
        if index == now - 1:
            owned = TrendLifecycle.from_checkpoint(owned.checkpoint())
    assert not any(event['event'] == STRUCTURE_BEAR_EVENT and event['bar_index'] == now for event in observed)


@pytest.mark.parametrize('through', ['2020-09-09', '2020-11-30'])
def test_xianfeng_actual_causal_prefix_uses_earliest_exit_without_future_low(through):
    bars, _ = sample()
    dates = {bar.timestamp.date().isoformat(): i for i, bar in enumerate(bars)}
    bars = bars[:dates[through] + 1]
    with market_calculations(bars) as calculations:
        calculations.calculate_trend_line(2)
        windows = calculations.trading_intervals(2)
        window = next(row for row in windows if row.start_low_index == dates['2020-04-28'])
        assert window.end_index == dates['2020-09-09']
        assert window.end_reason == SOURCE_BEAR_EXIT
        assert (window.end_key_index, window.end_key_price) == (dates['2020-08-11'], 5.48)
        events = calculations.history_events(2)
        assert confirmed_trading_intervals(events, len(bars), 2) == windows
        exit_event = next(row for row in events if row['event'] == SOURCE_BEAR_EVENT
                          and row['bar_index'] == dates['2020-09-09'])
        assert exit_event['buy_eligible'] is False
        proof = exit_event['source_confirmation']
        assert isinstance(proof, Mapping) and proof['event'] == FAILED_BREAKOUT_EVENT
