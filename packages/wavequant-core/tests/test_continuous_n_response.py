from dataclasses import replace
from datetime import datetime, timedelta
import json
from pathlib import Path

import pytest

from wavequant.domain.market_state.market_regime import MarketRegime, RegimePolicy, WaveBoundary, observe_market_regime
from wavequant.domain.market_structure.n_shape import BoxAnchorMode, NSetup, PivotRef
from wavequant.domain.market_structure.price_action import Direction, ShadowPolicy
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals
from wavequant.domain.strategies.strategy_profiles import whole_wave_profile


@pytest.mark.parametrize("response_low,local_confirmed", [(11.7, False), (11.8, True)])
def test_local_response_cannot_restart_after_lowering_its_virtual_defense(response_low, local_confirmed):
    rows = [
        (8.5, 9, 8, 8.5),
        (10, 12, 9.5, 11),
        (10.7, 11, 10, 10.5),
        (10.8, 12.6, 10.4, 12.2),
        (12.4, 13.5, 11.8, 12.4),
        (12.5, 12.6, response_low, 11.9),
        (11.9, 13.1, 11.8, 12.8),
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
    assert (result.latest.regime == MarketRegime.BULL) is local_confirmed
    # A later close strictly exceeding the whole episode record is independent
    # evidence; equality and loss of the original N defense never qualify.
    for close, expected in [(13.5, False), (13.8, True)]:
        recovery = Bar(bars[-1].timestamp + timedelta(days=1), "TEST", 12.8, 13.9, 12.3, close, 1000)
        recovered = observe_market_regime([*bars, recovery], setup, timeframe="1d", policy=policy)
        if response_low < 11.8:
            assert (recovered.latest.regime == MarketRegime.BULL) is expected
    broken = bars[:5] + [replace(bars[5], low=10.3), *bars[6:]]
    assert observe_market_regime(broken, setup, timeframe="1d", policy=policy).latest.regime is None


def test_xingwang_may28_does_not_reuse_broken_may11_response():
    raw = json.loads((Path(__file__).parent / "fixtures/xingwang_2019_dual_squeeze.json").read_text())
    bars = [Bar(datetime.fromisoformat(day), raw["symbol"], *values) for day, *values in raw["bars"]]
    config = SystemStrategy(
        **(whole_wave_profile({"scenarios": {"base": {"execution": {}}}})["strategy"] | {"volume_filter": False})
    )
    full = generate_system_signals(bars, config)
    assert not any(s.side == "LONG" and str(s.timestamp.date()) == "2026-05-28" for s in full.signals)
    assert not any(
        e["event"] == "regime_confirmation"
        and e["timestamp"].startswith("2026-05-28")
        and e["regime"] in ("轧空", "强轧空")
        for e in full.audit
    )
    assert any(s.side == "LONG" and str(s.timestamp.date()) == "2019-02-13" for s in full.signals)
    cutoff = next(i for i, b in enumerate(bars) if str(b.timestamp.date()) == "2026-05-28")
    prefix = generate_system_signals(bars[: cutoff + 1], config)
    assert prefix.signals == [s for s in full.signals if s.bar_index <= cutoff]
