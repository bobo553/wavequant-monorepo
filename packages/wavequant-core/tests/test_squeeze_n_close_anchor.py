from datetime import datetime
import json
from pathlib import Path

import pytest

from wavequant.domain.market_state.market_regime import MarketRegime, RegimePolicy, WaveBoundary, observe_market_regime
from wavequant.domain.market_structure.n_shape import BoxAnchorMode, NSetup, PivotRef
from wavequant.domain.market_structure.price_action import Direction, ShadowPolicy
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals
from wavequant.domain.strategies.strategy_profiles import whole_wave_profile


@pytest.mark.parametrize("closing,confirmed", [(12.1, False), (12.2, False), (12.3, True)])
def test_local_bounce_must_strictly_recover_original_n_close(closing, confirmed):
    rows = [
        (8.5, 9, 8, 8.5),
        (10, 12, 9.5, 11),
        (10.7, 11, 10, 10.5),
        (10.8, 12.6, 10.4, 12.2),
        (12.4, 12.5, 11.5, 11.8),
        (11.8, 12.4, 11.6, closing),
    ]
    bars = [Bar(datetime(2022, 1, i + 1), "TEST", *row, 1000) for i, row in enumerate(rows)]
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
    assert (result.latest.regime == MarketRegime.BULL) is confirmed


def test_lexin_january21_cannot_reuse_december29_n_for_bounce_entry():
    raw = json.loads((Path(__file__).parent / "fixtures/lexin_2026_squeeze.json").read_text(encoding="utf-8"))
    bars = [
        Bar(datetime.fromisoformat(day), raw["symbol"], *values) for day, *values in raw["bars"] if day <= "2022-02-10"
    ]
    config = SystemStrategy(
        **(whole_wave_profile({"scenarios": {"base": {"execution": {}}}})["strategy"] | {"volume_filter": False})
    )
    result = generate_system_signals(bars, config)
    inverse = next(
        e
        for e in result.audit
        if e["event"] == "n_completed" and e["direction"] == "down" and e["timestamp"].startswith("2022-01-18")
    )
    assert str(bars[inverse["pullback"]].timestamp.date()) == "2022-01-17"
    assert not any(s.side == "LONG" and str(s.timestamp.date()) == "2022-01-21" for s in result.signals)
    for signal in result.signals:
        if signal.side == "LONG":
            attack = next(b for b in bars if b.timestamp == signal.trigger_timestamp)
            assert bars[signal.bar_index].close > attack.close
    cutoff = next(i for i, b in enumerate(bars) if str(b.timestamp.date()) == "2022-01-21")
    prefix = generate_system_signals(bars[: cutoff + 1], config)
    assert prefix.signals == [s for s in result.signals if s.bar_index <= cutoff]
