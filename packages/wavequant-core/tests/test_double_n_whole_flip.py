"""整段回撤资格；用户报价之外的中间 K 线明确为合成边界输入。"""
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

import pytest

from wavequant.domain.models.model import Bar
from wavequant.domain.market_structure.bull_alternation_confirmation import confirm_double_n_pullback


@dataclass(frozen=True)
class Turn:
    index: int
    price: float
    kind: str
    known: int
    ordinal: int = 0


def scenario():
    bars = [Bar(datetime(2026, 9, 20) + timedelta(days=i), 'TEST',
                9.0, 9.5, 8.5, 9.2, 20000000) for i in range(8)]
    bars[0] = replace(bars[0], open=6.59, high=6.59, low=6.32, close=6.53)
    bars[1] = replace(bars[1], open=9.77, high=10.63, low=9.58, close=10.63)
    bars[2] = replace(bars[2], low=8.0)
    bars[4] = replace(bars[4], open=9.04, high=9.90, low=8.85, close=9.60, volume=48752900)
    bars[6] = replace(bars[6], open=9.5, high=9.8, low=9.3, close=9.6, volume=38860400)
    bars[7] = replace(bars[7], open=9.52, high=10.37, low=9.16, close=10.37, volume=47021100)
    return bars, Turn(0, 6.32, 'L', 0), Turn(1, 10.63, 'H', 2), Turn(2, 8.0, 'L', 3)


def test_double_n_uses_whole_flip_and_non_double_squeeze_close():
    bars, origin, high, low = scenario()
    proof = confirm_double_n_pullback(bars, 7, attack=4, origin=origin, high=high, polyline=[low])
    assert proof is not None
    assert proof.evidence['attack'] == 4
    assert proof.evidence['bar_index'] == 7
    assert proof.evidence['two_thirds_price'] == pytest.approx(7.7566666667)
    assert proof.evidence['counter_basis'] == 'minimum_low_over_whole_flip'
    assert bars[7].volume < 2 * bars[6].volume


@pytest.mark.parametrize('change', ['deep', 'late_low', 'weak', 'not_double', 'future_high'])
def test_invalid_or_unavailable_evidence_cannot_qualify(change):
    bars, origin, high, low = scenario()
    if change == 'deep':
        low = replace(low, price=7.75)
        bars[2] = replace(bars[2], low=7.75)
    elif change == 'late_low':
        bars[5] = replace(bars[5], low=7.9)
    elif change == 'weak':
        bars[7] = replace(bars[7], close=9.52)
    elif change == 'not_double':
        bars[4] = replace(bars[4], volume=39999999)
    else:
        high = replace(high, known=5)
    assert confirm_double_n_pullback(bars, 7, attack=4, origin=origin, high=high, polyline=[low]) is None


def test_exact_two_thirds_is_inclusive():
    bars, origin, high, low = scenario()
    origin, high, low = replace(origin, price=6.0), replace(high, price=12.0), replace(low, price=8.0)
    bars[0], bars[1] = replace(bars[0], low=6.0), replace(bars[1], high=12.0)
    proof = confirm_double_n_pullback(bars, 7, attack=4, origin=origin, high=high, polyline=[low])
    assert proof is not None
    assert proof.evidence['retracement_ratio'] == pytest.approx(2 / 3)


def test_qualification_scan_keeps_outer_n_and_original_knowledge():
    from wavequant.domain.market_structure.bull_alternation_confirmation import alternation_n_sources

    origin = Turn(1, 8.0, 'L', 2)
    neckline = Turn(2, 9.8, 'H', 3)
    pullback = Turn(3, 8.5, 'L', 4)
    inside_high = Turn(4, 9.0, 'H', 5)
    inside_low = Turn(5, 8.7, 'L', 6)
    sources = alternation_n_sources(
        [origin, neckline, pullback, inside_high, inside_low], earliest=1)
    assert (origin, neckline, pullback) in sources
    assert sources[0][0] is origin
    assert (pullback, inside_high, inside_low) in sources
    assert all(point.index >= 3 for triple in alternation_n_sources(
        [origin, neckline, pullback, inside_high, inside_low], earliest=3) for point in triple)


def test_qualification_scan_includes_nonadjacent_outer_neckline():
    from wavequant.domain.market_structure.bull_alternation_confirmation import alternation_n_sources

    origin = Turn(1, 8.0, 'L', 2)
    inner_high = Turn(2, 9.0, 'H', 3)
    inner_low = Turn(3, 8.5, 'L', 4)
    neckline = Turn(4, 9.8, 'H', 5)
    pullback = Turn(5, 8.7, 'L', 6)
    sources = alternation_n_sources([origin, inner_high, inner_low, neckline, pullback], earliest=1)
    assert (origin, neckline, pullback) in sources
    assert (inner_low, neckline, pullback) in sources
    assert next(row for row in sources if row == (origin, neckline, pullback))[0] is origin
