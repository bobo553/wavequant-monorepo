"""V3 breakout-bar qualification is separate from optional relative-volume filtering."""

from datetime import datetime

import pytest

from wavequant.domain.market_state.control_bar import AttackVolume
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.attack_quality import v3_positive_n_attack_rejection
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals
from tests.test_ruiling_squeeze_entry import ruiling_bars


def attack(*, open_price=100, high=104, low=100, close=102, volume=101, previous_volume=100):
    bar = Bar(datetime(2026, 1, 2), "TEST", open_price, high, low, close, volume)
    evidence = AttackVolume(20, 20, volume, previous_volume, volume > previous_volume, 100, volume / 100, True)
    return v3_positive_n_attack_rejection(bar, evidence)


@pytest.mark.parametrize("volume", [99, 100])
def test_breakout_volume_must_strictly_exceed_previous_session(volume):
    assert attack(volume=volume) == "v3_attack_volume_not_above_previous"


@pytest.mark.parametrize("close", [99, 100])
def test_breakout_requires_bullish_body(close):
    assert attack(low=98, close=close) == "v3_attack_not_bullish"


def test_small_absolute_or_range_body_is_not_a_qualified_breakout():
    assert attack(high=102, close=101.99) == "v3_attack_body_too_small"
    assert attack(high=106, low=99, close=102.5) == "v3_attack_body_too_small"


def test_body_and_volume_thresholds_include_exact_boundaries():
    assert attack() is None  # 2% of open and 50% of the day's range.


def test_ruiling_2019_october_attack_is_disqualified_even_when_relative_volume_filter_is_off():
    prices = dict(
        open_price=6.202267250173722,
        high=6.288559664089182,
        low=6.180694146694857,
        close=6.266986560610317,
    )
    assert attack(**prices, volume=2_832_300, previous_volume=3_231_602) == "v3_attack_volume_not_above_previous"
    assert attack(**prices) == "v3_attack_body_too_small"


def test_v3_rejection_prevents_completed_n_evidence_even_when_optional_volume_filter_is_off():
    bars = ruiling_bars()  # Equal daily volumes; geometry itself completes on July 27.
    legacy = generate_system_signals(
        bars,
        SystemStrategy(
            pivot_mode="lecture_causal", entry_policy="legacy_n_continuation",
            volume_filter=False, squeeze_pullback_entries=False,
        ),
    )
    assert any(e["event"] == "n_completed" and e["timestamp"].startswith("2026-07-27") for e in legacy.audit)
    v3 = generate_system_signals(
        bars,
        SystemStrategy(
            pivot_mode="lecture_causal", entry_policy="hierarchical_two_buy_points",
            buy_point_definition="whole_flip_wave_v3", volume_filter=False,
        ),
    )
    assert any(
        e["event"] == "n_attack_rejected" and e["timestamp"].startswith("2026-07-27")
        and e["reason"] == "v3_attack_volume_not_above_previous"
        for e in v3.audit
    )
    assert not any(e["event"] == "n_completed" and e["timestamp"].startswith("2026-07-27") for e in v3.audit)
