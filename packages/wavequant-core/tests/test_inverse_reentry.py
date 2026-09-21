from datetime import datetime, timedelta

import pytest

from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.inverse_reentry import inverse_reentry_rejection


@pytest.mark.parametrize(
    "attack,close,gap,allowed",
    [
        (1, 15, False, False),
        (4, 13, False, False),
        (4, 14, False, False),
        (4, 15, False, True),
        (1, 15, True, True),
        (1, 14, True, False),
    ],
)
def test_inverse_requires_new_n_above_b_or_verified_volume_gap(attack, close, gap, allowed):
    bars = [Bar(datetime(2022, 1, 1) + timedelta(days=i), "TEST", 12, 16, 11, close, 1000) for i in range(7)]
    inverse = [dict(known_at=3, attack=3, b_index=2, b_high=14)]
    result = inverse_reentry_rejection(bars, now=6, attack=attack, inverse=inverse, gap=gap)
    assert (result is None) is allowed
    if result:
        assert result["inverse_b_high"] == 14
    assert inverse_reentry_rejection(bars, now=2, attack=1, inverse=inverse) is None


def test_newer_inverse_rearms_gate_and_same_day_cannot_reenter():
    bars = [Bar(datetime(2022, 1, 1) + timedelta(days=i), "TEST", 12, 16, 11, 15, 1000) for i in range(7)]
    inverse = [dict(known_at=3, attack=3, b_index=2, b_high=14), dict(known_at=6, attack=5, b_index=4, b_high=14)]
    assert inverse_reentry_rejection(bars, now=5, attack=4, inverse=inverse) is None
    assert inverse_reentry_rejection(bars, now=6, attack=4, inverse=inverse, gap=True) is not None
