"""特殊守低是前提，不是绕过原有交替结束规则的许可证。"""

from dataclasses import replace
from datetime import timedelta

import pytest

from tests.test_abc_candidate import sample
from wavequant.domain.market_structure.bull_alternation_confirmation import confirm_recovery_alternation
from wavequant.domain.market_structure.trend_lifecycle import _Pivot
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.recovery_entry_history import merge_recovery_entry_history
from wavequant.domain.strategies.whole_wave_entry import select_wave_entry


def observe(bars, now, *, low=7, rebreak=None, polyline=(), level=2):
    points = [_Pivot(5, 0, 'L', low, 6)] if low is not None else []
    if rebreak is not None:
        points.append(_Pivot(8, 0, 'H', rebreak, 9))
    return confirm_recovery_alternation(bars, now, origin=_Pivot(0, 0, 'L', 4, 0),
        high=_Pivot(2, 0, 'H', 16, 3), broken=_Pivot(1, 0, 'H', 10, 1),
        points=points, polyline=polyline, level=level, path='path')


@pytest.mark.parametrize('level', [0, 1, 2, 3])
def test_deep_confirmed_low_alone_is_insufficient_but_rebreak_can_confirm(level):
    bars, _, _ = sample()
    assert observe(bars, 9, level=level) is None
    assert observe(bars, 9, rebreak=16, level=level) is None
    proof = observe(bars, 9, rebreak=17, level=level)
    assert proof is not None and proof.low.index == 5
    assert proof.rule == 'confirmed_higher_pullback_then_confirmed_flip_high_rebreak'
    assert [(point.index, point.kind) for point in proof.trend_source] == [(5, 'L'), (8, 'H')]


def test_shallow_confirmation_and_origin_guard_keep_their_strict_boundaries():
    bars, _, _ = sample()
    assert observe(bars, 6, low=9).rule == 'confirmed_higher_pullback_strictly_below_two_thirds'
    assert observe(bars, 6, low=8) is None  # 恰好2/3不能冒充严格小于。
    changed = list(bars)
    changed[6] = replace(changed[6], low=3.99)
    assert observe(changed, 6, low=9) is None
    with pytest.raises(ValueError, match='causal'):
        observe(bars, 5, low=9)


def n_case():
    bars, _, _ = sample()
    bars[9] = replace(bars[9], open=12, volume=200)
    bars[10] = replace(bars[10], open=13, low=13, volume=250)
    bars.append(Bar(bars[-1].timestamp + timedelta(days=1), bars[0].symbol, 17, 20, 17, 19, 300))
    source = (_Pivot(5, 0, 'L', 7, 5), _Pivot(6, 0, 'H', 12, 6), _Pivot(7, 0, 'L', 9, 7))
    return bars, source


@pytest.mark.parametrize('level', [1, 2, 3])
def test_polyline_n_squeeze_can_confirm_before_formal_trend_low_exists(level):
    bars, source = n_case()
    assert observe(bars, 10, low=None, polyline=source, level=level) is None
    proof = observe(bars, 11, low=None, polyline=source, level=level)
    assert proof is not None and proof.rule == 'positive_n_and_squeeze_after_qualified_b'
    assert proof.low.index == 5 and proof.evidence['attack'] == 9
    assert proof.source == source
    assert observe(bars[:11], 10, low=None, polyline=source, level=level) is None
    assert observe(bars, 11, low=None, polyline=()) is None


def test_defended_n_consolidation_gap_reuses_original_gap_and_volume_rules():
    bars, source = n_case()
    bars = bars[:10]
    for opening, high, low, close, volume in [
        (13, 14, 12.2, 13.5, 100), (13, 13.8, 12.3, 13.4, 100),
        (13.2, 13.7, 12.4, 13.5, 100), (14.9, 15, 13.9, 15, 300),
    ]:
        bars.append(Bar(bars[-1].timestamp + timedelta(days=1), bars[0].symbol,
                        opening, high, low, close, volume))
    proof = observe(bars, 13, low=None, polyline=source)
    assert proof is not None and proof.rule == 'positive_n_consolidation_gap_after_qualified_b'
    assert proof.evidence['confirmation_source'] == 'defended_n_consolidation_gap'
    assert proof.low.index == 5


@pytest.mark.parametrize('level', [1, 2, 3])
@pytest.mark.parametrize('special', [False, True])
def test_confirming_n_context_is_available_only_on_confirmation_day_and_closes_with_source(level, special):
    bars, _, _ = sample()
    event = dict(event='trend_lifecycle_state', bar_index=9, available_index=9, trend_level=level,
        source_path='path', special_bull_recovery=special, flip_confirmed=True, state='空多交替', direction='up',
        cycle_started_index=3, origin_index=0, origin_price=4, recovery_key_index=1, recovery_key_price=10,
        flip_index=2, flip_known_index=3, breakout_level=16, counter_index=5, counter_ordinal=0,
        counter_price=7, alternation_index=9, confirmation_rule='positive_n_and_squeeze_after_qualified_b',
        confirmation_evidence=dict(attack=8, key_index=1, key_price=10))
    if not special:
        event.update(recovery_key_index=None, recovery_key_price=None)
    invalidated = dict(event='trend_lifecycle_invalidated', bar_index=10, trend_level=level,
                       source_path='path', reason='source_evidence_revised')
    history = merge_recovery_entry_history(bars, {}, [event, invalidated])
    assert history[8] == () and history[10] == ()
    context = history[9][0]
    assert context.confirmation_attack == 8 and context.alternation_low_index == 5
    selected, reason = select_wave_entry(history[9], history[8], bars=bars, attack=8,
        low_index=7, asof=9, allow_confirming_n=True, buy_interval_level=level)
    assert selected is not None and reason == ''
    assert merge_recovery_entry_history(bars[:9], {}, [event])[8] == ()
