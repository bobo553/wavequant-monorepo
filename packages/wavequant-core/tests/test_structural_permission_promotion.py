"""渲染器的策略交替升级不得重写已完成的二三级高低点阶段。"""

from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import datetime, timedelta
from typing import cast
from unittest.mock import patch

import pytest

import wavequant.domain.market_structure.alternation_breakout as alternation_breakout
import wavequant.domain.market_structure.trend_lines as trend_lines_module
import wavequant.interfaces.charts.visualization as visualization
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.integrated_strategy import SystemResult, SystemStrategy


START = datetime(2026, 1, 1)
SCOPE = 'structural_trend_points_with_independent_strategy_permissions'


def _day(index: int) -> str:
    return (START + timedelta(days=index)).date().isoformat()


def _records(value: object) -> list[Mapping[str, object]]:
    assert isinstance(value, (tuple, list))
    assert all(isinstance(item, Mapping) for item in value)
    return cast(list[Mapping[str, object]], list(value))


def _point(index: int, kind: str, value: float, known: int, level: int) -> dict[str, object]:
    return dict(index=index, ordinal=0, kind=kind, value=value, time=_day(index), available_at=_day(known),
                state='reversal', trend_level=level, source_level=level - 1,
                confirmation_rule='ordered_high_low_fixture', label=kind,
                source_confirmation=dict(index=index, kind=kind, value=value, available_at=_day(known)))


def _level(level: int, *, enabled: bool, high: float = 10, right_low: float = 6) -> dict[str, object]:
    left = dict(id=f'level-{level}-left', trend_level=level, points=[
        _point(0, 'L', 5, 1, level), _point(1, 'H', high, 2, level), _point(2, 'L', 7, 3, level)])
    right = dict(id=f'level-{level}-right', trend_level=level, points=[
        _point(5, 'L', right_low, 6, level), _point(7, 'H', 13, 8, level)])
    # 已完成几何含两条非空路径；只有后一条预先有许可证，前一路径须由本次 audit 升级。
    return dict(trend_level=level, scope=SCOPE, strokes=[left, right], qualified_strokes=[deepcopy(right)],
                source_strokes=[dict(id='upstream', points=[])], candidate_strokes=[], structure_strokes=[],
                developing_strokes=[], bear_to_bull_highs=[], bear_bull_alternation_lows=[],
                confirmed_wave_count=5, strategy_confirmed_wave_count=2,
                n_target_trend_confirmation_enabled=enabled)


def _fixture(*, enabled: bool, breakout: bool = True) -> tuple[list[Bar], dict[str, object], SystemResult]:
    high = 10 if breakout else 20
    right_low = 6 if breakout else 7
    prices = ((6, 7, 5, 6), (8, high, 8, 9), (8, 9, 7, 8), (8, 9, 8, 8),
              (8, 11, 8, 8), (7, 8, right_low, 7), (8, 9, 8, 8), (11, 13, 10, 12), (11, 12, 10, 11))
    bars = [Bar(START + timedelta(days=index), 'TEST', *row, 100) for index, row in enumerate(prices)]
    confirmations: list[dict[str, object]] = [dict(
        event='squeeze_alternation_confirmed', bar_index=3, candidate_index=3,
        a_origin_index=0, a_origin_price=5, a_high_index=1, a_high_price=high,
        b_low_index=2, b_low_price=7, trend_level=level, source_path=f'level-{level}-left',
        attack=2, anchor_known_index=2, key_source_index=0, key_price=7, retracement_ratio=.6,
    ) for level in (2, 3)]
    # 使用真实突破函数生成策略证据；闭合后最高价严格突破，收盘仍低于 A 高点。
    audit = [dict(event, timestamp=bars[event['bar_index']].timestamp.isoformat())
             for event in alternation_breakout.high_breakout_events(bars, confirmations)]
    result = SystemResult(signals=[], audit=audit, counts=dict(squeeze_alternation_breakout=2 if breakout else 0))
    geometry: dict[str, object] = dict(lecture_drawing=dict(strokes=[]),
        reversal_trends=_level(1, enabled=enabled, high=high, right_low=right_low),
        secondary_trends=_level(2, enabled=enabled, high=high, right_low=right_low),
        tertiary_trends=_level(3, enabled=enabled, high=high, right_low=right_low))
    return bars, geometry, result


@pytest.mark.parametrize('enabled', (False, True))
def test_renderer_promotes_real_audit_landmarks_only_into_permissions_without_rejudging_tertiary(enabled: bool) -> None:
    bars, geometry, result = _fixture(enabled=enabled)
    before = deepcopy(geometry)
    original_audit = deepcopy(result.audit)
    config = SystemStrategy(pivot_mode='lecture_causal', n_target_trend_confirmation_enabled=enabled)
    assert bars[4].high > 10 > bars[4].close
    assert [(event['trend_level'], event['bar_index']) for event in result.audit
            if event['event'] == 'squeeze_alternation_breakout'] == [(2, 4), (3, 4)]
    promotion_calls: list[int] = []
    real_promote = alternation_breakout.promote_alternation_segments

    def promote(permission_view: dict[str, object], market: Sequence[Bar],
                audit: Sequence[dict[str, object]], level: int) -> dict[str, object]:
        source = cast(Mapping[str, object], before['secondary_trends' if level == 2 else 'tertiary_trends'])
        assert permission_view['strokes'] == source['qualified_strokes']
        assert permission_view['strokes'] != source['strokes']
        landmarks = _records(permission_view['bear_bull_alternation_lows'])
        assert len(landmarks) == 1
        assert (landmarks[0]['available_at'], landmarks[0]['breakout_at']) == (_day(3), _day(4))
        promotion_calls.append(level)
        return cast(dict[str, object], real_promote(permission_view, market, audit, level))

    def forbid_rejudging(*args: object, **kwargs: object) -> object:
        raise AssertionError('已完成的趋势高低点阶段不可在资格渲染时重新判断')

    # 只提供 renderer 的结构和策略输出端口；折线、landmarks 和升级规则均实际执行。
    repository = ChartRepositoryFixture()
    with patch.object(alternation_breakout, 'promote_alternation_segments', side_effect=promote), patch.object(
            visualization, 'tertiary_trends', side_effect=forbid_rejudging), patch.object(
            trend_lines_module, 'judge_trend', side_effect=forbid_rejudging), patch.object(
            visualization, 'calculate_trend_hierarchy', side_effect=forbid_rejudging):
        rendered = cast(dict[str, object], repository.render_theory(bars, config, result, _day(8), geometry=geometry))

    assert promotion_calls == [2, 3]
    assert geometry == before
    assert result.audit == original_audit
    assert rendered['asof'] == _day(8)
    assert rendered['counts'] == result.counts
    assert rendered['n_target_trend_confirmation_enabled'] is enabled
    assert rendered['computed_from'] == 'current_engine_on_selected_prefix'
    assert rendered['reversal_trends'] == before['reversal_trends']
    for name in ('secondary_trends', 'tertiary_trends'):
        source = cast(Mapping[str, object], before[name])
        response = cast(Mapping[str, object], rendered[name])
        assert response['strokes'] == source['strokes']
        assert len(_records(response['strokes'])) == 2
        assert response['scope'] == SCOPE
        assert response['confirmed_wave_count'] == 5
        assert response['strategy_confirmed_wave_count'] == 4
        qualified = _records(response['qualified_strokes'])
        assert qualified[0] == _records(source['qualified_strokes'])[0]
        assert len(qualified) == 2
        promoted = qualified[1]
        assert promoted['source_path'] == f"level-{response['trend_level']}-left"
        assert promoted['available_at'] == _day(4)
        points = _records(promoted['points'])
        assert [(point['index'], point['kind'], point['value'], point['available_at']) for point in points] == [
            (1, 'H', 10, _day(4)), (2, 'L', 7, _day(4))]
        for point in points:
            assert point['confirmation_rule'] == 'confirmed_alternation_then_high_breakout'
            assert cast(Mapping[str, object], point['confirmed_by']) == dict(
                index=4, time=_day(4), kind='K', value=11, available_at=_day(4))
        landmarks = _records(response['bear_bull_alternation_lows'])
        assert len(landmarks) == 1
        assert (landmarks[0]['index'], landmarks[0]['available_at'], landmarks[0]['breakout_at']) == (2, _day(3), _day(4))
    events = _records(rendered['events'])
    assert [(event['trend_level'], event['time'], event['available_at']) for event in events
            if event['event'] == 'squeeze_alternation_breakout'] == [(2, _day(4), _day(4)), (3, _day(4), _day(4))]


@pytest.mark.parametrize('enabled', (False, True))
def test_renderer_landmark_without_real_breakout_keeps_permission_points_and_count(enabled: bool) -> None:
    bars, geometry, result = _fixture(enabled=enabled, breakout=False)
    before = deepcopy(geometry)
    original_audit = deepcopy(result.audit)
    config = SystemStrategy(pivot_mode='lecture_causal', n_target_trend_confirmation_enabled=enabled)
    assert max(bar.high for bar in bars[3:]) < 20
    assert [(event['trend_level'], event['bar_index']) for event in result.audit] == [(2, 3), (3, 3)]
    assert all(event['event'] == 'squeeze_alternation_confirmed' for event in result.audit)
    promotion_calls: list[int] = []
    real_promote = alternation_breakout.promote_alternation_segments

    def promote(permission_view: dict[str, object], market: Sequence[Bar],
                audit: Sequence[dict[str, object]], level: int) -> dict[str, object]:
        source = cast(Mapping[str, object], before['secondary_trends' if level == 2 else 'tertiary_trends'])
        assert permission_view['strokes'] == source['qualified_strokes']
        landmarks = _records(permission_view['bear_bull_alternation_lows'])
        assert len(landmarks) == 1
        assert landmarks[0]['available_at'] == _day(3)
        assert 'breakout_at' not in landmarks[0]
        promotion_calls.append(level)
        return cast(dict[str, object], real_promote(permission_view, market, audit, level))

    def forbid_rejudging(*args: object, **kwargs: object) -> object:
        raise AssertionError('未突破的资格地标不可再次判断趋势高低点')

    # 真实地标已经确认，真实突破函数仍无突破；应进入升级边界但不增加任何资格点。
    with patch.object(alternation_breakout, 'promote_alternation_segments', side_effect=promote), patch.object(
            visualization, 'tertiary_trends', side_effect=forbid_rejudging), patch.object(
            trend_lines_module, 'judge_trend', side_effect=forbid_rejudging), patch.object(
            visualization, 'calculate_trend_hierarchy', side_effect=forbid_rejudging):
        rendered = cast(dict[str, object], ChartRepositoryFixture().render_theory(
            bars, config, result, _day(8), geometry=geometry))

    assert promotion_calls == [2, 3]
    assert geometry == before
    assert result.audit == original_audit
    assert rendered['asof'] == _day(8)
    assert rendered['counts'] == result.counts
    assert rendered['n_target_trend_confirmation_enabled'] is enabled
    assert rendered['reversal_trends'] == before['reversal_trends']
    assert all(event['event'] != 'squeeze_alternation_breakout' for event in _records(rendered['events']))
    for name in ('secondary_trends', 'tertiary_trends'):
        source = cast(Mapping[str, object], before[name])
        response = cast(Mapping[str, object], rendered[name])
        assert response['strokes'] == source['strokes']
        assert response['qualified_strokes'] == source['qualified_strokes']
        assert response['confirmed_wave_count'] == 5
        assert response['strategy_confirmed_wave_count'] == source['strategy_confirmed_wave_count'] == 2
        assert response['scope'] == SCOPE


class ChartRepositoryFixture(visualization.ChartRepository):
    def __init__(self) -> None:
        # render_theory 不读取封存运行目录，避免为边界测试初始化行情和数据库供应者。
        pass
