import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
import pytest
from wavequant.domain.market_state.volume_reversal import volume_reversal_squeeze
from wavequant.domain.market_state.market_regime import RegimePolicy, WaveBoundary, observe_market_regime
from wavequant.domain.market_structure.n_shape import BoxAnchorMode, NSetup, PivotRef
from wavequant.domain.market_structure.price_action import Direction, ShadowPolicy
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals
from wavequant.domain.strategies.strategy_profiles import whole_wave_profile


def test_xianfeng_may18_reversal_passes_strict_half_context():
    raw = json.loads((Path(__file__).parent / "fixtures/xianfeng_2026_resistance.json").read_text())
    bars = [Bar(datetime.fromisoformat(d), raw["symbol"], *v) for d, *v in raw["bars"] if d <= "2026-05-18"]
    config = SystemStrategy(
        **(whole_wave_profile({"scenarios": {"base": {"execution": {}}}}, "lecture_v3_c50")["strategy"] | {"volume_filter": False})
    )
    result = generate_system_signals(bars, config)
    event = next(
        e
        for e in result.audit
        if e["event"] == "n_completed" and e["direction"] == "up" and e["timestamp"].startswith("2026-05-11")
    )
    setup = NSetup(
        raw["symbol"],
        "1d",
        Direction.UP,
        PivotRef(event["origin"], event["origin"] + 1),
        PivotRef(event["neckline"], event["neckline"] + 1),
        PivotRef(event["pullback"], event["known_at"]),
        "lecture_causal",
        BoxAnchorMode.ATTACK_VIRTUAL_EXTREME,
        allow_confirmation_bar=True,
    )
    observed = observe_market_regime(
        bars,
        setup,
        timeframe="1d",
        policy=RegimePolicy(ShadowPolicy(0.5), WaveBoundary.ORIGIN, local_resistance_failure=True),
    )
    proof = volume_reversal_squeeze(
        bars,
        attack=event["bar_index"],
        now=len(bars) - 1,
        origin_low=bars[event["origin"]].low,
        attack_defense=event["defense"],
        frame=observed.latest,
    )
    assert proof["reversal_record_high"] == 4.85
    assert proof["reversal_close"] == 5.10
    close_break = next(b for b in bars[event["bar_index"] :] if b.close > bars[event["neckline"]].high)
    assert str(close_break.timestamp.date()) == "2026-05-12"
    assert any(s.side == "LONG" and s.timestamp == bars[-1].timestamp for s in result.signals)
    prefix = generate_system_signals(bars[:-1], config)
    assert prefix.signals == [s for s in result.signals if s.bar_index < len(bars) - 1]


def reversal_case():
    rows = [
        (8.5, 9, 8, 8.5),
        (10, 12, 9.5, 11),
        (10.7, 11, 10, 10.5),
        (10.8, 12.6, 10.4, 12.2),
        (12.1, 12.5, 10.5, 11.5),
        (10.2, 13.4, 10.1, 13.1),
    ]
    bars = [Bar(datetime(2022, 1, i + 1), "TEST", *row, 2000 if i == 5 else 1000) for i, row in enumerate(rows)]
    setup = NSetup(
        "TEST",
        "1d",
        Direction.UP,
        PivotRef(0, 0),
        PivotRef(1, 1),
        PivotRef(2, 2),
        "test",
        BoxAnchorMode.ATTACK_VIRTUAL_EXTREME,
    )
    return bars, setup


def reversal_proof(bars, setup):
    result = observe_market_regime(
        bars,
        setup,
        timeframe="1d",
        policy=RegimePolicy(ShadowPolicy(0.5), WaveBoundary.ORIGIN, local_resistance_failure=True),
    )
    return volume_reversal_squeeze(bars, attack=3, now=5, origin_low=8, attack_defense=10.4, frame=result.latest)


def test_volume_reversal_requires_record_close_not_merely_a_higher_close():
    bars, setup = reversal_case()
    proof = reversal_proof(bars, setup)
    assert proof["reversal_record_high"] == 12.6
    assert proof["reversal_low"] == 10.1
    assert proof["reversal_structural_low"] == 8


@pytest.mark.parametrize(
    "changes",
    [
        {"volume": 1000},
        {"volume": 999},
        {"close": 12.6},
        {"close": 11.7},
        {"low": 7.9},
        {"open": 11.5},
        {"high": 17},
    ],
)
def test_volume_reversal_rejects_no_volume_no_record_broken_origin_or_small_body(changes):
    bars, setup = reversal_case()
    bars[-1] = replace(bars[-1], **changes)
    assert reversal_proof(bars, setup) is None


def test_volume_reversal_cannot_revive_an_earlier_broken_defense():
    bars, setup = reversal_case()
    bars[-2] = replace(bars[-2], low=10.3)
    assert reversal_proof(bars, setup) is None
