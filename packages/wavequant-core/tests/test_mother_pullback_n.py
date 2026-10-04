"""A bearish outside mother can hold the ordered B/C of a lecture positive N."""

from dataclasses import replace
from datetime import datetime
from typing import TypedDict

import pytest

from wavequant.domain.market_state.market_regime import (
    MarketRegime, RegimePolicy, WaveBoundary, observe_market_regime,
)
from wavequant.domain.market_structure.n_shape import (
    BoxAnchorMode, MilestoneBasis, NSetup, NStatus, PivotRef, observe_n,
)
from wavequant.domain.market_structure.price_action import Direction, ShadowPolicy
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals


class CandleChange(TypedDict, total=False):
    open: float
    high: float
    low: float
    close: float


def sample() -> list[Bar]:
    rows = [
        ("2024-02-28", 4.50, 4.60, 4.40, 4.45, 1000),
        ("2024-02-29", 4.38, 4.45, 4.26, 4.37, 1000),
        ("2024-03-01", 4.47, 4.52, 4.35, 4.51, 1000),
        ("2024-03-04", 4.53, 4.53, 4.33, 4.50, 1500),
        ("2024-03-05", 4.46, 4.75, 4.38, 4.56, 2000),
        ("2024-03-06", 4.58, 4.72, 4.46, 4.68, 1600),
        ("2024-03-07", 4.70, 4.83, 4.55, 4.80, 2000),
    ]
    return [Bar(datetime.fromisoformat(day), "TEST", o, h, l, c, volume) for day, o, h, l, c, volume in rows]


def setup() -> NSetup:
    return NSetup(
        "TEST", "1d", Direction.UP, PivotRef(1, 2), PivotRef(3, 3), PivotRef(3, 4),
        "lecture_causal", BoxAnchorMode.ATTACK_VIRTUAL_EXTREME,
        allow_confirmation_bar=True, allow_mother_pullback=True, staged_defense=True,
    )


def test_bearish_mother_pullback_completes_after_its_low_is_known() -> None:
    bars = sample()
    original = setup()
    waiting = observe_n(bars[:4], original, timeframe="1d", milestone_basis=MilestoneBasis.EXTREME)
    assert waiting.status == NStatus.AWAIT_ANCHORS
    completed = observe_n(bars[:5], original, timeframe="1d", milestone_basis=MilestoneBasis.EXTREME)
    assert completed.status == NStatus.COMPLETED
    assert completed.completion is not None and completed.completion.bar_index == 4
    assert completed.completion.defense == 4.38
    assert completed.targets is not None
    assert (completed.targets.equal_wave, completed.targets.one_p, completed.targets.two_t) == pytest.approx(
        (4.60, 5.24, 5.73)
    )
    full = observe_n(bars, original, timeframe="1d", milestone_basis=MilestoneBasis.EXTREME)
    assert (full.completion, full.targets) == (completed.completion, completed.targets)


def test_mother_pullback_requires_explicit_positive_lecture_policy() -> None:
    original = setup()
    for change in (
        {"allow_mother_pullback": False},
        {"source": "strict_polyline"},
        {"direction": Direction.DOWN},
        {"origin": PivotRef(3, 3)},
        {"allow_mother_pullback": 1},
    ):
        with pytest.raises(ValueError):
            replace(original, **change)


@pytest.mark.parametrize("change", [
    {"open": 4.51, "high": 4.52},
    {"low": 4.35},
    {"close": 4.53},
    {"high": 4.55, "close": 4.54},
])
def test_mother_pullback_rejects_equal_boundary_doji_and_bullish_candles(change: CandleChange) -> None:
    bars = sample()
    bars[3] = replace(bars[3], **change)
    with pytest.raises(ValueError):
        observe_n(bars, setup(), timeframe="1d", milestone_basis=MilestoneBasis.EXTREME)


@pytest.mark.parametrize("change", [{"high": 4.53, "close": 4.52}, {"close": 4.50}])
def test_mother_pullback_still_requires_same_bar_real_and_virtual_breaks(change: CandleChange) -> None:
    bars = sample()[:5]
    bars[-1] = replace(bars[-1], **change)
    result = observe_n(bars, setup(), timeframe="1d", milestone_basis=MilestoneBasis.EXTREME)
    assert result.status == NStatus.FORMING
    assert result.completion is None


def test_mother_pullback_cannot_backdate_a_future_low_confirmation() -> None:
    bars = sample()
    delayed = replace(setup(), pullback=PivotRef(3, 5))
    waiting = observe_n(bars[:5], delayed, timeframe="1d", milestone_basis=MilestoneBasis.EXTREME)
    assert waiting.status == NStatus.AWAIT_ANCHORS
    assert observe_n(bars, delayed, timeframe="1d", milestone_basis=MilestoneBasis.EXTREME).completion is None
    broken = [*bars[:4], replace(bars[4], low=4.32), *bars[5:]]
    with pytest.raises(ValueError, match="exceeded before its confirmation"):
        observe_n(broken, setup(), timeframe="1d", milestone_basis=MilestoneBasis.EXTREME)


def test_mother_pullback_keeps_third_bar_squeeze_and_original_defense() -> None:
    bars = sample()
    policy = RegimePolicy(ShadowPolicy(0.5), WaveBoundary.ORIGIN, local_resistance_failure=True)
    full = observe_market_regime(bars, setup(), timeframe="1d", policy=policy)
    assert [frame.regime for frame in full.frames[:2]] == [None, None]
    assert full.latest is not None and full.latest.regime == MarketRegime.BULL
    prefix = observe_market_regime(bars[:6], setup(), timeframe="1d", policy=policy)
    assert prefix.frames == full.frames[:2]
    broken = [*bars[:5], replace(bars[5], low=4.37), bars[6]]
    latest = observe_market_regime(broken, setup(), timeframe="1d", policy=policy).latest
    assert latest is not None and latest.first_defense_breach_index == 5
    assert latest.regime != MarketRegime.BULL


def test_daily_pipeline_accepts_ordered_mother_pullback_only_in_v3() -> None:
    bars = sample()
    config = SystemStrategy(
        pivot_mode="lecture_causal", entry_policy="hierarchical_two_buy_points",
        buy_point_definition="whole_flip_wave_v3", strict_n_attack_quality=False,
        volume_filter=False, preflight_reward_risk=False,
    )
    full = generate_system_signals(bars, config)
    completed = next(event for event in full.audit if event["event"] == "n_completed" and event["bar_index"] == 4)
    assert completed["direction"] == "up"
    assert [completed[key] for key in ("origin", "neckline", "pullback", "known_at")] == [1, 3, 3, 4]
    prefix = generate_system_signals(bars[:5], config)
    assert prefix.audit == [event for event in full.audit if event["bar_index"] <= 4]
    assert prefix.signals == [signal for signal in full.signals if signal.bar_index <= 4]
    legacy = generate_system_signals(bars, replace(config, buy_point_definition="legacy_v2"))
    assert not any(event["event"] == "n_completed" and event["bar_index"] == 4 for event in legacy.audit)
