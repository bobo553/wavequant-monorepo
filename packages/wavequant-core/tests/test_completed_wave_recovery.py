from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
import json
from pathlib import Path
from typing import cast

import pytest

from wavequant.domain.market_structure.bottom_n_targets import DeclineStart
from wavequant.domain.market_structure.lecture_drawing import lecture_drawing
from wavequant.domain.market_structure.lecture_trend import reversal_trends
from wavequant.domain.market_structure.secondary_trend import secondary_trends
from wavequant.domain.market_structure.tertiary_trend import tertiary_trends
from wavequant.domain.market_structure.wave_projection import WaveProjectionSetup
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.integrated_strategy import SystemResult, SystemStrategy, generate_system_signals
from wavequant.domain.strategies.completed_wave_recovery import secondary_wave_recovery, inverse_wave_recovery
from wavequant.domain.strategies.hierarchical_entry import EntryContext
from wavequant.domain.strategies.wave_continuation import wave_gap_entry


Evidence = dict[str, str | int | float]
Levels = dict[int, dict[int, tuple[dict[str, object], ...]]]
Permissions = dict[int, tuple[EntryContext, ...]]
ChartBuild = Callable[..., tuple[Permissions, list[dict[str, object]]]]
LevelBuild = Callable[..., tuple[Levels, dict[int, int]]]
RecoveryEvidence = tuple[list[Bar], dict[str, int], int, Evidence, Evidence, Evidence]
_draw = cast(Callable[[Sequence[Bar]], dict[str, object]], lecture_drawing)


def sample() -> tuple[list[Bar], dict[str, int]]:
    raw = json.loads((Path(__file__).parent / "fixtures/shilian_wave_continuation.json").read_text(encoding="utf-8"))
    bars = [Bar(datetime.fromisoformat(day), raw["symbol"], *values) for day, *values in raw["bars"]]
    dates = {str(b.timestamp.date()): i for i, b in enumerate(bars)}
    return bars, dates


def config() -> SystemStrategy:
    return SystemStrategy(
        pivot_mode="lecture_causal",
        entry_policy="hierarchical_two_buy_points",
        buy_point_definition="whole_flip_wave_v3",
        first_pullback_threshold=None,
        strict_n_attack_quality=False,
        preflight_reward_risk=False,
        volume_filter=False,
    )


@dataclass
class IntegratedEvidence:
    bars: list[Bar]
    dates: dict[str, int]
    result: SystemResult
    permissions: Permissions
    hierarchy_events: list[dict[str, object]]
    levels: Levels
    epochs: dict[int, int]
    declines: dict[int, tuple[DeclineStart, ...]]
    chart_sinks: dict[str, dict[int, object]]

    def replay(self, bars: Sequence[Bar], strategy: SystemStrategy) -> SystemResult:
        """复用真实生成时的逐日前缀历史，重跑策略门禁而不重新归并全部结构。

        所有几何快照均在对应交易日独立计算；这里只按截止日截取历史和事件，
        不能把最终几何倒填到早期。输入只允许改symbol或最后一棒volume；价格
        与日期仍必须和已验证行情一致。来源的冷前缀重算另由集成测试验证。
        """
        from wavequant.domain.strategies import chart_entry_history as chart_module
        from wavequant.domain.strategies import hierarchical_entry as hierarchy_module

        end = len(bars) - 1
        assert all(replace(bar, symbol=original.symbol, volume=original.volume) == original
                   for bar, original in zip(bars, self.bars))

        def chart_prefix(_bars: Sequence[Bar], **kwargs: object) -> tuple[Permissions, list[dict[str, object]]]:
            assert len(_bars) == len(bars)
            for field, values in self.chart_sinks.items():
                sink = kwargs.get(field)
                if isinstance(sink, dict):
                    sink.update({index: value for index, value in values.items() if index <= end})
            return ({index: value for index, value in self.permissions.items() if index <= end},
                    [event for event in self.hierarchy_events if cast(int, event['bar_index']) <= end])

        def level_prefix(_bars: Sequence[Bar], **kwargs: object) -> tuple[Levels, dict[int, int]]:
            assert len(_bars) == len(bars)
            sink = kwargs.get('decline_sink')
            if isinstance(sink, dict):
                sink.clear()
                sink.update({index: value for index, value in self.declines.items() if index <= end})
            return ({index: value for index, value in self.levels.items() if index <= end},
                    {index: value for index, value in self.epochs.items() if index <= end})

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(chart_module, 'chart_entry_history', chart_prefix)
            patch.setattr(hierarchy_module, 'hierarchical_history', level_prefix)
            return generate_system_signals(bars, strategy)


@pytest.fixture(scope='module')
def integrated() -> IntegratedEvidence:
    """只做一次完整真实归并；其他策略回放共享这次逐日冻结的历史证据。"""
    from wavequant.domain.strategies import chart_entry_history as chart_module
    from wavequant.domain.strategies import hierarchical_entry as hierarchy_module

    bars, dates = sample()
    chart = cast(ChartBuild, chart_module.chart_entry_history)
    hierarchy = cast(LevelBuild, hierarchy_module.hierarchical_history)
    permissions: Permissions = {}
    hierarchy_events: list[dict[str, object]] = []
    levels: Levels = {}
    epochs: dict[int, int] = {}
    declines: dict[int, tuple[DeclineStart, ...]] = {}
    chart_sinks: dict[str, dict[int, object]] = {}

    def capture_chart(_bars: Sequence[Bar], **kwargs: object) -> tuple[Permissions, list[dict[str, object]]]:
        observed, events = chart(_bars, **kwargs)
        permissions.update(observed)
        hierarchy_events.extend(events)
        for field in ('shallow_candidate_sink', 'combined_candidate_sink', 'secondary_pullback_sink'):
            sink = kwargs.get(field)
            if isinstance(sink, dict):
                chart_sinks[field] = dict(sink)
        return observed, events

    def capture_levels(_bars: Sequence[Bar], **kwargs: object) -> tuple[Levels, dict[int, int]]:
        observed, observed_epochs = hierarchy(_bars, **kwargs)
        levels.update(observed)
        epochs.update(observed_epochs)
        sink = kwargs.get('decline_sink')
        if isinstance(sink, dict):
            declines.update(sink)
        return observed, observed_epochs

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(chart_module, 'chart_entry_history', capture_chart)
        patch.setattr(hierarchy_module, 'hierarchical_history', capture_levels)
        full = generate_system_signals(bars, config())
    return IntegratedEvidence(bars, dates, full, permissions, hierarchy_events, levels, epochs, declines, chart_sinks)


def test_completed_a_b_gap_cannot_waive_unconfirmed_higher_level_alternation(integrated: IntegratedEvidence) -> None:
    bars, dates, full = integrated.bars, integrated.dates, integrated.result
    day = dates["2020-07-20"]
    buys = [s for s in full.signals if s.side == "LONG" and s.bar_index == day]
    assert not buys
    # 原6/1 N来自不符合正式下降底部的局部原点。正式一级来源递归到二级后，
    # 二级末跌高仍是2/26的3.5911；7/6最高3.5564未突破它。独立C攻击可恢复
    # A/B内压力，但不能豁免尚未确认的二/三级空多交替，更不能借8/7资格。
    rejected = [e for e in full.audit if e['event'] == 'entry_rejected' and e['bar_index'] == day]
    assert {e['attack'] for e in rejected if e.get('reason') == 'wave_no_alternation_at_attack'} == {
        dates['2020-07-01'], dates['2020-07-02']}
    proof = next(e for e in full.audit if e['event'] == 'wave_gap_observed'
                 and e['bar_index'] == day and e['attack'] == dates['2020-07-01'])
    assert proof["wave_entry_n_date"] == "2020-07-01"
    assert proof["wave_entry_two_t_date"] == "2020-07-03"
    assert proof["wave_a_high_date"] == "2020-07-06"
    assert proof["wave_b_low_date"] == "2020-07-17"
    assert proof['wave_equal_target'] == pytest.approx(3.8612645122653366)
    assert not integrated.permissions[day]
    assert not integrated.permissions[dates['2020-07-01']]
    assert not integrated.permissions[dates['2020-07-02']]
    later_first = reversal_trends(_draw(bars), bars)
    later_second = secondary_trends(later_first, bars)
    later_points = [point for stroke in later_second['strokes'] for point in stroke['points']]
    floor = next(point for point in later_points if point['index'] == dates['2020-04-28'])
    # 双向证书分别冻结：4/28下行端点保留4/29公开日，7/21才独立突破2/26
    # H3.5911得到上涨许可；7/28后又有8/7的独立上涨证书。两份后续证书
    # 都不能授权7/20或回填当时尚不存在的二/三级交替。
    future = floor['trend_confirmation']
    assert future['available_at'] == '2020-07-21'
    assert (future['broken_key']['time'], future['broken_key']['value']) == (
        '2020-02-26', pytest.approx(3.5911216904044236))
    assert future['confirmed_by']['value'] == pytest.approx(3.6377176194499756)
    future_low = next(point for point in later_points if point['index'] == dates['2020-07-28'])
    assert future_low['trend_confirmation']['available_at'] == '2020-08-07'
    for end in (dates["2020-07-17"], day):
        prefix = integrated.replay(bars[: end + 1], config())
        assert prefix.signals == [s for s in full.signals if s.bar_index <= end]
        assert prefix.audit == [e for e in full.audit if e["bar_index"] <= end]
        # 单独冷算正式几何，验证快照的本级key和可知资格，不能仅把full截短
        # 就声称完成了冷前缀验证。两种验证分别承担来源和策略职责。
        visible = bars[:end + 1]
        first = reversal_trends(_draw(visible), visible)
        second = secondary_trends(first, visible)
        third = tertiary_trends(second, visible)
        points = [point for stroke in second['strokes'] for point in stroke['points']]
        assert [(point['index'], point['kind']) for point in points[-2:]] == [
            (dates['2020-02-26'], 'H'), (dates['2020-04-28'], 'L')]
        assert points[-2]['value'] == pytest.approx(3.5911216904044236)
        assert points[-1]['value'] == pytest.approx(2.5563917118133186)
        assert not any(point['time'] >= '2020-04-28' for point in second['bear_bull_alternation_lows'])
        assert not any(point['time'] >= '2020-04-28' for point in third['bear_bull_alternation_lows'])
        assert points[-1].get('trend_confirmation') is None


def wave_setup(bars: Sequence[Bar], dates: Mapping[str, int]) -> WaveProjectionSetup:
    # 这些原点、N箱顶和防守位属于实际已确认的7/1重组N。固定领域证据用于
    # 恢复单元测试，不能将恢复函数的正确性和策略是否授予交易资格混为一谈。
    origin = bars[dates['2020-06-12']].low
    box_anchor = bars[dates['2020-07-01']].high
    return WaveProjectionSetup(dates['2020-06-12'], dates['2020-07-01'], dates['2020-07-03'],
                               origin, box_anchor, 3 * box_anchor - 2 * origin, 2.8248198273941147)


@pytest.fixture(scope="module")
def evidence() -> RecoveryEvidence:
    bars, dates = sample()
    now = dates["2020-07-20"]
    wave = wave_gap_entry(bars, wave_setup(bars, dates), now, a_confirmed_at=dates['2020-07-02'])
    assert wave is not None
    assert (wave['wave_entry_n_date'], wave['wave_a_high_date'], wave['wave_b_low_date']) == (
        '2020-07-01', '2020-07-06', '2020-07-17')
    pressure: Evidence = dict(
        secondary_attack_date="2020-07-02",
        secondary_current_resistance=False,
        secondary_high=3.154911993547389,
        secondary_resistance_high=3.5564278402443894,
        secondary_defense=2.936593273801796,
    )
    inverse: Evidence = dict(
        known_at=dates["2020-07-15"], attack=dates["2020-07-15"], b_index=dates["2020-07-14"], b_high=3.444654393836709
    )
    return bars, dates, now, wave, pressure, inverse


@pytest.mark.parametrize(
    "case",
    [
        "no_wave",
        "ordinary_gap",
        "new_attack",
        "old_attack",
        "higher_record",
        "higher_level",
        "broken_pressure_defense",
        "unrecovered_level",
        "current_resistance",
    ],
)
def test_completed_wave_does_not_waive_independent_pressure(evidence: RecoveryEvidence, case: str) -> None:
    bars, _, now, original_wave, pressure, _ = evidence
    wave = dict(original_wave)
    pressure = dict(pressure)
    assert secondary_wave_recovery(bars, now, wave, pressure)
    if case == "no_wave":
        assert secondary_wave_recovery(bars, now, None, pressure) is None
        return
    elif case == "ordinary_gap":
        wave["wave_entry_path"] = "defended_n_consolidation_gap"
    elif case == "new_attack":
        pressure["secondary_attack_date"] = "2020-07-20"
    elif case == "old_attack":
        pressure["secondary_attack_date"] = "2020-05-29"
    elif case == "higher_record":
        pressure["secondary_resistance_high"] = float(wave["wave_a_high"]) + 0.01
    elif case == "higher_level":
        pressure["secondary_high"] = float(wave["wave_a_high"]) + 0.01
    elif case == "broken_pressure_defense":
        pressure["secondary_defense"] = float(wave["wave_b_low"]) + 0.01
    elif case == "unrecovered_level":
        pressure["secondary_high"] = bars[now].close
    else:
        pressure["secondary_current_resistance"] = True
    assert secondary_wave_recovery(bars, now, wave, pressure) is None


@pytest.mark.parametrize(
    "case", ["no_wave", "ordinary_gap", "before_a", "after_b", "same_day", "higher_b", "b_before_a", "no_inverse"]
)
def test_completed_wave_does_not_waive_unrelated_inverse(evidence: RecoveryEvidence, case: str) -> None:
    bars, dates, now, original_wave, _, inverse = evidence
    wave = dict(original_wave)
    inverse = dict(inverse)
    assert inverse_wave_recovery(bars, now, wave, [inverse])
    if case == "no_wave":
        assert inverse_wave_recovery(bars, now, None, [inverse]) is None
        return
    elif case == "ordinary_gap":
        wave["wave_entry_path"] = "defended_n_consolidation_gap"
    elif case == "before_a":
        inverse.update(attack=dates["2020-07-03"], known_at=dates["2020-07-03"], b_index=dates["2020-07-02"])
    elif case == "after_b":
        inverse.update(attack=now, known_at=now)
    elif case == "same_day":
        inverse["known_at"] = now
    elif case == "higher_b":
        inverse["b_high"] = float(wave["wave_a_high"]) + 0.01
    elif case == "b_before_a":
        inverse["b_index"] = dates["2020-07-03"]
    assert inverse_wave_recovery(bars, now, wave, [] if case == "no_inverse" else [inverse]) is None


def test_latest_inverse_wins_and_future_inverse_is_not_known(evidence: RecoveryEvidence) -> None:
    bars, _, now, wave, _, inverse = evidence
    future = dict(inverse, attack=now + 1, known_at=now + 1)
    assert inverse_wave_recovery(bars, now, wave, [inverse, future])
    latest = dict(inverse, attack=now, known_at=now)
    assert inverse_wave_recovery(bars, now, wave, [inverse, latest]) is None


def test_optional_filters_remain_effective_after_isolated_entry_permission(
    integrated: IntegratedEvidence, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from wavequant.domain.strategies import whole_wave_entry

    bars, dates = integrated.bars, integrated.dates
    day = dates["2020-07-20"]
    bars = [replace(b, symbol="sh.600000") for b in bars[: day + 1]]
    original = cast(Callable[..., tuple[dict[str, object] | None, str]], whole_wave_entry.select_wave_entry)

    def approved(current: object, at_attack: object, **kwargs: object) -> tuple[dict[str, object] | None, str]:
        # 本用例只验证许可之后的量能/盈亏比可选门禁。显式替换分类器返回值，
        # 不伪造正式来源或把真实7/20当作合法交易；真实资格由上面的集成用例
        # 保持严格拒绝。其他交易日仍运行实际分类器。
        if kwargs.get('asof') == day:
            return dict(buy_point_type='transition_squeeze', counter_ratio=0.5), ''
        return original(current, at_attack, **kwargs)

    monkeypatch.setattr(whole_wave_entry, 'select_wave_entry', approved)
    assert any(s.side == "LONG" and s.bar_index == day for s in integrated.replay(bars, config()).signals)
    assert any(
        s.side == "LONG" and s.bar_index == day
        for s in integrated.replay(bars, replace(config(), volume_filter=True)).signals
    )
    result = integrated.replay(bars, replace(config(), preflight_reward_risk=True))
    assert not any(s.side == "LONG" and s.bar_index == day for s in result.signals)
    assert any(e.get("reason") == 'insufficient_close_gross_reward_risk' and e["bar_index"] == day for e in result.audit)


@pytest.mark.parametrize("increment, expected", [(0, False), (1, True)])
def test_v3_gap_volume_is_strictly_above_yesterday_not_twenty_day_mean(increment: int, expected: bool) -> None:
    bars, dates = sample()
    now = dates["2020-07-20"]
    bars = bars[: now + 1]
    bars[-1] = replace(bars[-1], volume=bars[-2].volume + increment)
    # 直接验证独立C缺口的量能证据，无高点突破替代路径；实际交易仍额外需要
    # 本级方向许可。与昨日相等不能轧空确认，严格多1即可，不依赖20日均量。
    wave = wave_gap_entry(bars, wave_setup(bars, dates), now, a_confirmed_at=dates['2020-07-02'])
    assert bool(wave) is expected
    if expected:
        assert wave is not None
        assert wave['wave_gap_trigger'] == 'volume'
        rvol = bars[-1].volume / bars[-2].volume
        assert 1 < rvol < 1.2
        assert bars[-1].volume / (sum(bar.volume for bar in bars[-21:-1]) / 20) < 1.2
