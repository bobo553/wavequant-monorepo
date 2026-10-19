"""用户两根报价配合合成前序行情，不冒充完整实股回放。"""

from dataclasses import replace
from datetime import datetime

import pytest

from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.gap_entry_gate import GapEntryGate


def sample():
    rows = [('2024-01-09', 5.44, 5.69, 5.16, 5.25, 155973716),
            ('2024-01-10', 5.01, 5.13, 4.84, 4.94, 104073946),
            ('2024-01-25', 4.85, 4.99, 4.80, 4.95, 60000000),
            ('2024-01-26', 5.05, 5.29, 4.94, 5.12, 106265742)]
    return [Bar(datetime.fromisoformat(day), 'sh.600825', *values) for day, *values in rows]


def test_opening_rejection_does_not_use_later_high_to_erase_gap():
    bars = sample()
    proof = GapEntryGate().advance(bars, 3)
    assert proof is not None
    assert proof['down_gap_width'] == .15
    assert proof['up_gap_width'] == .06
    assert proof['down_gap_date'] == '2024-01-10'
    altered = [*bars[:3], replace(bars[3], high=6, close=5.9, volume=1)]
    assert GapEntryGate().advance(altered, 3) == proof
    gate = GapEntryGate()
    for index in range(len(bars)):
        assert gate.advance(bars, index) == GapEntryGate().advance(bars[:index + 1], index)


@pytest.mark.parametrize('case', ['filled', 'equal_width', 'above', 'below', 'no_up_gap'])
def test_gap_gate_strict_boundaries(case):
    bars = sample()
    if case == 'filled':
        bars[2] = replace(bars[2], high=5.16)
    elif case == 'equal_width':
        bars[3] = replace(bars[3], open=5.14)
    elif case == 'above':
        bars[3] = replace(bars[3], open=5.16)
    elif case == 'below':
        bars[3] = replace(bars[3], open=5.00)
    else:
        bars[3] = replace(bars[3], open=4.99)
    assert GapEntryGate().advance(bars, 3) is None
