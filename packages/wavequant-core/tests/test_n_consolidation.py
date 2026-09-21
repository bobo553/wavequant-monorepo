from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.n_consolidation import consolidation_gap


def bars():
    values = [
        (9, 11, 9, 11, 100),
        (11, 12, 10, 11.5, 120),
        (10.7, 10.9, 10.4, 10.6, 60),
        (10.5, 10.8, 10.2, 10.7, 70),
        (11.1, 12, 11, 11.9, 150),
    ]
    return [Bar(datetime(2020, 1, 1) + timedelta(days=i), "test", *v) for i, v in enumerate(values)]


def test_consolidation_preserves_larger_defense_not_upper_containment():
    data = bars()
    proof = consolidation_gap(data, attack=0, now=4, defense=9)
    assert proof is not None
    assert proof["gap_low"] > proof["gap_previous_high"]
    assert consolidation_gap(data[:4], attack=0, now=3, defense=9) is None


@pytest.mark.parametrize(
    "index,changes",
    [
        (2, dict(low=8.9)),
        (4, dict(volume=70)),
        (4, dict(low=10.8)),
        (4, dict(open=10.8)),
        (4, dict(close=11.1)),
        (3, dict(high=11.3, close=11.2)),
        (2, dict(high=11.3, close=11.2)),
    ],
)
def test_gap_requires_defense_volume_bullish_body_and_return_below_high(index, changes):
    data = bars()
    data[index] = replace(data[index], **changes)
    assert consolidation_gap(data, attack=0, now=4, defense=9) is None


def test_gap_can_form_new_n_after_opening_below_original_high():
    data = bars()
    data[4] = replace(data[4], open=10.9, low=10.85)
    assert consolidation_gap(data, attack=0, now=4, defense=9) is not None
