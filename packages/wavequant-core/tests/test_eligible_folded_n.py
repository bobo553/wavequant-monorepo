"""资格来源保留内部折返之外的 N；行情夹具与合成资格分别标注。"""

from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path

import pytest

from wavequant.domain.market_state.market_regime import (
    MarketRegime, RegimePolicy, WaveBoundary, observe_market_regime,
)
from wavequant.domain.market_structure.n_shape import (
    BoxAnchorMode, MilestoneBasis, NSetup, PivotRef, observe_n,
)
from wavequant.domain.market_structure.polyline import LinePoint, PointKind, ReversalPoint
from wavequant.domain.market_structure.price_action import Direction, ShadowPolicy
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.attack_quality import squeeze_buy_rejection
from wavequant.domain.strategies.eligible_polyline_n import EligibleNSource, EligiblePolylineN
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals
from wavequant.domain.strategies.lecture_strategy import lecture_pivot_history
from wavequant.domain.strategies.strategy_profiles import whole_wave_profile


def opening(now: int, low: int = 0, *, level: int = 2, path: str = 'test') -> dict[str, object]:
    """仅来源单元测试注入资格；真实策略用例不使用此事件。"""
    return dict(event='trend_lifecycle_state', bar_index=now, trend_level=level, source_path=path,
        state='空多交替', flip_confirmed=True, flip_index=0, flip_known_index=0, counter_index=low,
        counter_ordinal=0, counter_price=5.0, alternation_index=now)


def nested_points(*, inverse: bool = False, last_price: float = 6.0) -> tuple[ReversalPoint, ...]:
    rows = ((0, 'L', 5.0), (1, 'H', 10.0), (2, 'L', 7.0), (3, 'H', 9.0), (4, 'L', last_price))
    return tuple(ReversalPoint(LinePoint(index, 0,
        PointKind(('H' if kind == 'L' else 'L') if inverse else kind),
        20 - price if inverse else price), index + 1, 'test') for index, kind, price in rows)


@pytest.mark.parametrize('level', (1, 2, 3))
@pytest.mark.parametrize('inverse', (False, True))
def test_internal_turns_preserve_envelope_and_first_knowledge(level: int, inverse: bool) -> None:
    points = nested_points(inverse=inverse)
    stream = EligiblePolylineN(level, folded_window=30)
    emitted: list[EligibleNSource] = []
    for now in range(6):
        visible = tuple(point for point in points if point.confirmed_index <= now)
        emitted.extend(stream.advance(now, visible, (opening(now, level=level),) if now == 1 else ()))
    envelope = [source for source in emitted if source.points == (points[0], points[1], points[4])]
    assert len(envelope) == 1
    assert envelope[0].known_at == 5
    assert envelope[0].window_start == 1
    assert tuple(point.confirmed_index for point in envelope[0].points) == (1, 2, 5)
    assert stream.advance(6, points, ()) == ()
    assert stream.advance(7, points, ()) == ()
    assert len({source.points for source in emitted}) == len(emitted)


@pytest.mark.parametrize('last_price', (5.0, 4.9))
def test_origin_touch_or_break_cannot_create_folded_n(last_price: float) -> None:
    points = nested_points(last_price=last_price)
    stream = EligiblePolylineN(2, folded_window=30)
    for now in range(5):
        stream.advance(now, (), ())
    sources = stream.advance(5, points, (opening(5),))
    assert not any(source.points == (points[0], points[1], points[4]) for source in sources)


@pytest.mark.parametrize('low,window', ((2, 30), (0, 3)))
def test_envelope_cannot_borrow_before_qualification_low_or_structural_window(low: int, window: int) -> None:
    points = nested_points()
    stream = EligiblePolylineN(2, folded_window=window)
    for now in range(5):
        stream.advance(now, (), ())
    sources = stream.advance(5, points, (opening(5, low),))
    assert not any(source.points == (points[0], points[1], points[4]) for source in sources)


def test_close_reopen_and_revision_keep_separate_knowledge_clocks() -> None:
    points = nested_points()
    stream = EligiblePolylineN(2, folded_window=30)
    for now in range(5):
        stream.advance(now, (), ())
    first = stream.advance(5, points, (opening(5),))
    closed = dict(opening(6), state='多空交替')
    assert stream.advance(6, points, (closed,)) == ()
    assert stream.advance(7, points, ()) == ()
    reopened = stream.advance(8, points, (opening(8),))
    old = next(source for source in first if source.points == (points[0], points[1], points[4]))
    new = next(source for source in reopened if source.points == old.points)
    assert (old.known_at, new.known_at) == (5, 8)
    assert old.scope != new.scope
    revised = (*points[:-1], replace(points[-1], confirmed_index=9,
                                    point=replace(points[-1].point, price=5.8)))
    updates = stream.advance(9, revised, ())
    changed = next(source for source in updates if source.points == (points[0], points[1], revised[-1]))
    assert changed.known_at == 9
    assert old.points[-1].point.price == 6.0


def test_disabled_folded_route_preserves_adjacent_sources() -> None:
    points = nested_points()
    stream = EligiblePolylineN(2)
    for now in range(5):
        stream.advance(now, (), ())
    assert [source.points for source in stream.advance(5, points, (opening(5),))] == [
        points[0:3], points[1:4], points[2:5],
    ]


def xingwang(through: str) -> list[Bar]:
    # 复用仓库同源完整行情，不以三根用户报价拼造中间交易日。
    raw = json.loads((Path(__file__).parent / 'fixtures/xingwang_2019_dual_squeeze.json').read_text(encoding='utf-8'))
    return [Bar(datetime.fromisoformat(day), raw['symbol'], *values)
            for day, *values in raw['bars'] if day <= through]


def test_xingwang_real_polyline_exposes_january9_n_and_january13_squeeze() -> None:
    bars = xingwang('2020-01-22')
    dates = {bar.timestamp.date().isoformat(): index for index, bar in enumerate(bars)}
    snapshots, _, _, _ = lecture_pivot_history(bars)
    config = SystemStrategy(**whole_wave_profile({'scenarios': {'base': {'execution': {}}}})['strategy'])
    stream = EligiblePolylineN(2, folded_window=config.structure_window)
    sources: list[EligibleNSource] = []
    # 这里只隔离来源与 N/六态链路；下面的生产入口用例验证真实资格。
    for now in range(len(bars)):
        events = (opening(now),) if now == dates['2019-12-09'] else ()
        sources.extend(stream.advance(now, snapshots[now], events))
    source = next(source for source in sources
        if source.points[1].point.index == dates['2020-01-03']
        and source.points[2].point.index == dates['2020-01-08'])
    assert source.known_at == dates['2020-01-09']
    a, b, c = source.points
    setup = NSetup(bars[0].symbol, '1d', Direction.UP,
        PivotRef(a.point.index, a.confirmed_index), PivotRef(b.point.index, b.confirmed_index),
        PivotRef(c.point.index, c.confirmed_index), 'lecture_causal', BoxAnchorMode.ATTACK_VIRTUAL_EXTREME,
        allow_confirmation_bar=True, allow_outside_close=True, staged_defense=True)
    attack, confirmed = dates['2020-01-09'], dates['2020-01-13']
    before = observe_n(bars[:attack], setup, timeframe='1d', milestone_basis=MilestoneBasis.EXTREME)
    assert before.completion is None
    policy = RegimePolicy(ShadowPolicy(config.shadow_fraction), WaveBoundary.ORIGIN, local_resistance_failure=True)
    for cutoff in (attack, dates['2020-01-10'], confirmed, dates['2020-01-20'], len(bars) - 1):
        n = observe_n(bars[:cutoff + 1], setup, timeframe='1d', milestone_basis=MilestoneBasis.EXTREME)
        assert n.completion is not None and n.completion.bar_index == attack
        regime = observe_market_regime(bars[:cutoff + 1], setup, timeframe='1d', policy=policy, _prepared_n=n)
        squeezes = [frame for frame in regime.frames
                    if frame.regime in (MarketRegime.BULL, MarketRegime.STRONG_BULL)]
        if cutoff < confirmed:
            assert squeezes == []
        else:
            assert squeezes[0].bar_index == confirmed
    assert bars[attack].close > bars[dates['2020-01-03']].close
    assert bars[attack].high > bars[dates['2020-01-03']].high
    assert bars[attack].close < bars[dates['2020-01-03']].high
    assert bars[confirmed].volume < bars[confirmed - 1].volume
    assert bars[confirmed].volume > bars[attack - 1].volume
    assert squeeze_buy_rejection(bars[confirmed], bars[confirmed - 1], bars[attack - 1]) is None


def test_xingwang_production_pipeline_retains_earlier_n_and_squeeze() -> None:
    bars = xingwang('2020-01-22')
    dates = {bar.timestamp.date().isoformat(): index for index, bar in enumerate(bars)}
    config = SystemStrategy(**whole_wave_profile({'scenarios': {'base': {'execution': {}}}})['strategy'])
    full = generate_system_signals(bars, config)
    attack, confirmed = dates['2020-01-09'], dates['2020-01-13']
    n = next(event for event in full.audit if event['event'] == 'n_completed'
             and event['direction'] == 'up' and event['bar_index'] == attack)
    assert n['neckline'] == dates['2020-01-03']
    assert n['pullback'] == dates['2020-01-08']
    assert n['known_at'] == attack
    squeeze = next(event for event in full.audit if event['event'] == 'regime_confirmation'
                   and event['attack'] == attack and event['bar_index'] == confirmed)
    assert squeeze['regime'] in ('轧空', '强轧空')
    # 结构确认不授予底部测幅资格；本N的等幅目标已过，不能为提前买入伪造目标。
    assert n['target_eligible'] is False
    assert n['target_qualification_reason'] == 'origin_is_not_decline_floor'
    assert n['one_p'] is None and n['two_t'] is None
    equal_target = bars[n['pullback']].low + bars[n['neckline']].high - bars[n['origin']].low
    assert equal_target == pytest.approx(38.52134357588946)
    assert bars[confirmed].close > equal_target
    rejection = next(event for event in full.audit if event['event'] == 'entry_rejected'
                     and event.get('attack') == attack and event['bar_index'] == confirmed)
    # 本轮首 N 已在 1/10 发出买点；后续结构继续识别，但不能另开轧空轮次。
    assert rejection['reason'] == 'first_n_squeeze_already_emitted'
    assert not any(signal.side == 'LONG' and signal.bar_index == confirmed
                   and signal.trigger_timestamp == bars[attack].timestamp for signal in full.signals)
    bought, repeated = dates['2020-01-10'], dates['2020-01-20']
    first_buy = next(signal for signal in full.signals if signal.side == 'LONG' and signal.bar_index == bought)
    assert first_buy.reason == 'system_multilevel_breakout_squeeze'
    first_entry = next(row for row in full.audit if row['event'] == 'long_signal' and row['bar_index'] == bought)
    assert first_entry['squeeze_owner_attack'] == first_entry['attack'] == dates['2019-12-06']
    assert first_entry['squeeze_owner_defense'] == pytest.approx(32.91419056983502)
    assert not any(signal.side == 'LONG' and signal.bar_index == repeated for signal in full.signals)
    denied = next(row for row in full.audit if row['event'] == 'entry_rejected'
                  and row['bar_index'] == repeated and row.get('attack') == dates['2020-01-15'])
    assert denied['reason'] == 'first_n_squeeze_already_emitted'
    assert denied['squeeze_owner_attack'] == first_entry['attack']
    assert denied['squeeze_owner_emitted_at'] == bought
    assert denied['squeeze_owner_defense'] == first_entry['squeeze_owner_defense']
    assert min(bar.low for bar in bars[bought + 1:repeated + 1]) > float(first_entry['squeeze_owner_defense'])
    for cutoff in (attack, bought, confirmed, repeated):
        prefix = generate_system_signals(bars[:cutoff + 1], config)
        assert n in prefix.audit
        if cutoff == confirmed:
            assert squeeze in prefix.audit
            assert rejection in prefix.audit
        if cutoff == repeated:
            assert denied in prefix.audit
        assert prefix.signals == [signal for signal in full.signals if signal.bar_index <= cutoff]
