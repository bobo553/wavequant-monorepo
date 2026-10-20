"""Body gaps may confirm an alternation without a whole-candle gap or a future N."""

from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from wavequant.domain.market_structure.bull_alternation_confirmation import confirm_body_gap_alternation
from wavequant.domain.market_structure.trend_lifecycle import _Pivot
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.recovery_entry_history import merge_recovery_entry_history


def sample():
    # Compact structural fixture; the last two OHLCV rows use the user supplied values.
    rows = [(4, 4.09, 3.96, 4.07, 100), (5.51, 5.78, 5.32, 5.32, 200),
            (4.12, 4.15, 4, 4.09, 100), (4.10, 4.21, 4.06, 4.21, 150),
            (4.19, 4.25, 4.19, 4.22, 3364310), (4.25, 4.64, 4.22, 4.64, 13786411)]
    bars = [Bar(datetime(2019, 8, 14) + timedelta(days=i), 'sh.600812', *row) for i, row in enumerate(rows)]
    return bars, _Pivot(0, 0, 'L', 3.96, 0), _Pivot(1, 0, 'H', 5.78, 1), (_Pivot(2, 0, 'L', 4, 3),)


def observe(bars, origin, high, points):
    return confirm_body_gap_alternation(bars, 5, origin=origin, high=high, polyline=points)


def test_body_gap_confirms_before_n_and_preserves_actual_confirmation_clock():
    bars, origin, high, points = sample()
    assert bars[5].low < bars[4].high
    proof = observe(bars, origin, high, points)
    assert proof is not None and proof.low.index == 2
    assert proof.rule == 'body_gap_volume_bullish_breakout_after_defended_low'
    assert proof.evidence['candidate_index'] == 5
    assert proof.evidence['breakout_resistance'] == 4.25
    assert 'attack' not in proof.evidence, 'a candle breakout does not manufacture an N'
    assert confirm_body_gap_alternation(bars[:5], 4, origin=origin, high=high, polyline=points) is None


@pytest.mark.parametrize('failure', ['equal_gap', 'equal_volume', 'small_body', 'no_break', 'origin_broken', 'future_low'])
def test_missing_independent_conditions_cannot_confirm(failure):
    bars, origin, high, points = sample()
    if failure == 'equal_gap': bars[5] = replace(bars[5], open=4.22)
    elif failure == 'equal_volume': bars[5] = replace(bars[5], volume=bars[4].volume)
    elif failure == 'small_body': bars[5] = replace(bars[5], open=4.60)
    elif failure == 'no_break': bars[3] = replace(bars[3], high=4.64)
    elif failure == 'origin_broken': bars[3] = replace(bars[3], low=3.95)
    else: points = (replace(points[0], known=6),)
    assert observe(bars, origin, high, points) is None


@pytest.mark.parametrize('level', [1, 2, 3])
@pytest.mark.parametrize('special', [False, True])
def test_all_levels_publish_permission_on_the_confirmation_day_only(level, special):
    bars, _, _, _ = sample()
    event = dict(event='trend_lifecycle_state', state='空多交替', direction='up', bar_index=5,
        trend_level=level, source_path='p', special_bull_recovery=special,
        confirmation_rule='body_gap_volume_bullish_breakout_after_defended_low',
        cycle_started_index=1, origin_index=0, origin_price=3.96,
        recovery_key_index=0 if special else None, recovery_key_price=4.09 if special else None,
        flip_index=1, flip_confirmed=True, flip_known_index=1,
        breakout_level=5.78, counter_index=2, counter_price=4,
        alternation_index=5, confirmation_evidence=dict(key_index=0, key_price=4.09))
    history = merge_recovery_entry_history(bars, {}, [event])
    assert all(not history[index] for index in range(5))
    assert history[5][0].alternation_low_index == 2
    assert history[5][0].confirmation_attack is None
