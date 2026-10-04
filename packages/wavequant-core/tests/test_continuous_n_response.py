from dataclasses import replace
from datetime import datetime, timedelta
import json
from pathlib import Path

import pytest

from wavequant.domain.market_state.market_regime import MarketRegime, RegimePolicy, WaveBoundary, observe_market_regime
from wavequant.domain.market_state.candle_strength import strong_bullish_candle
from wavequant.domain.market_structure.n_shape import BoxAnchorMode, NSetup, PivotRef
from wavequant.domain.market_structure.price_action import Direction, ShadowPolicy
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals
from wavequant.domain.strategies.strategy_profiles import whole_wave_profile


@pytest.mark.parametrize("response_low", [11.7, 11.8])
def test_late_response_compares_original_attack_without_resetting_its_defense(response_low):
    rows = [
        (8.5, 9, 8, 8.5),
        (10, 12, 9.5, 11),
        (10.7, 11, 10, 10.5),
        (10.8, 12.6, 10.4, 12.2),
        (12.4, 13.5, 11.8, 12.4),
        (12.5, 12.6, response_low, 11.9),
        (11.9, 13.0, 11.8, 12.8),
    ]
    bars = [Bar(datetime(2020, 1, 1) + timedelta(days=i), "TEST", *row, 1000) for i, row in enumerate(rows)]
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
    policy = RegimePolicy(ShadowPolicy(0.5), WaveBoundary.ORIGIN, local_resistance_failure=True)
    result = observe_market_regime(bars, setup, timeframe="1d", policy=policy)
    assert result.latest.rolling_defense_held
    assert result.latest.first_defense_breach_index is None
    assert result.latest.first_resistance_index == 4
    assert result.latest.regime == MarketRegime.BULL
    # Progress beyond both original attack prices need not exceed the later
    # episode record. It still cannot confirm below the original attack close.
    for close, expected in [(12.2, False), (13.49, True), (13.5, True), (13.8, True)]:
        recovery = Bar(bars[-1].timestamp + timedelta(days=1), "TEST", 12.0, 13.9, 11.5, close, 1000)
        recovered = observe_market_regime([*bars, recovery], setup, timeframe="1d", policy=policy)
        assert (recovered.latest.regime == MarketRegime.BULL) is expected
    broken = bars[:5] + [replace(bars[5], low=10.3), *bars[6:]]
    assert observe_market_regime(broken, setup, timeframe="1d", policy=policy).latest.regime is None
    equal_attack_high = bars[:-1] + [replace(bars[-1], high=12.6, close=12.5)]
    assert observe_market_regime(equal_attack_high, setup, timeframe="1d", policy=policy).latest.regime is None


def test_xingwang_may28_requires_strong_candle_and_preserves_february13_buy():
    raw = json.loads((Path(__file__).parent / "fixtures/xingwang_2019_dual_squeeze.json").read_text())
    bars = [Bar(datetime.fromisoformat(day), raw["symbol"], *values) for day, *values in raw["bars"]]
    config = SystemStrategy(
        **(whole_wave_profile({"scenarios": {"base": {"execution": {}}}})["strategy"] | {"volume_filter": False})
    )
    full = generate_system_signals(bars, config)
    assert not any(s.side == "LONG" and str(s.timestamp.date()) == "2026-05-28" for s in full.signals)
    attack = next(e for e in full.audit if e["event"] == "n_completed" and e["direction"] == "up"
                  and e["timestamp"].startswith("2026-05-11"))
    now = next(i for i, bar in enumerate(bars) if str(bar.timestamp.date()) == "2026-05-28")
    assert bars[now].high > bars[attack["bar_index"]].high
    assert bars[now].close > bars[attack["bar_index"]].close
    assert min(bar.low for bar in bars[attack["bar_index"] + 1:now + 1]) >= attack["defense"]
    assert bars[now].volume < bars[now-1].volume
    assert not strong_bullish_candle(bars[now])
    assert not any(
        e["event"] == "regime_confirmation"
        and e["attack"] == attack["bar_index"]
        and e["timestamp"].startswith("2026-05-28")
        and e["regime"] in ("轧空", "强轧空")
        for e in full.audit
    )
    assert any(s.side == "LONG" and str(s.timestamp.date()) == "2019-02-13" for s in full.signals)
    cutoff = next(i for i, b in enumerate(bars) if str(b.timestamp.date()) == "2026-05-28")
    prefix = generate_system_signals(bars[: cutoff + 1], config)
    assert prefix.signals == [s for s in full.signals if s.bar_index <= cutoff]
