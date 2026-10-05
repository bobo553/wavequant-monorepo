from dataclasses import replace
from datetime import datetime, timedelta
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from wavequant.domain.market_state.candle_strength import strong_bullish_candle
from wavequant.domain.market_state.market_regime import MarketRegime
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals
from wavequant.domain.strategies.multilevel_squeeze import multilevel_squeeze
from wavequant.domain.strategies.strategy_profiles import whole_wave_profile


@pytest.mark.parametrize(
    "invalid",
    [
        None,
        "future_key",
        "different_neck",
        "higher_pressure",
        "broken_n",
        "lower_open",
        "touch_record",
        "low_volume",
        "broken_rolling",
        "too_early",
    ],
)
def test_multilevel_record_break_requires_both_scales_and_live_defenses(invalid):
    rows = [
        (8, 9, 7, 8),
        (10, 12, 9, 11),
        (10, 11, 9, 10),
        (11, 13, 10, 12.8),
        (12.7, 13.1, 12.5, 12.9),
        (13, 15, 12.6, 13.4),
    ]
    bars = [Bar(datetime(2020, 1, 1) + timedelta(days=i), "TEST", *r, 1000 + i) for i, r in enumerate(rows)]
    candidate = dict(
        attack=3,
        start=0,
        setup=NS(origin=NS(index=0), neckline=NS(index=1), pullback=NS(index=2)),
        force=NS(ratio=0.5),
        n=NS(completion=NS(defense=10)),
    )
    frame = NS(bar_index=5, first_defense_breach_index=None)
    keys = [dict(event="hierarchy_resistance_key", bar_index=2, trend_level=2, key=dict(index=1, value=12))]
    if invalid == "future_key":
        keys[0]["bar_index"] = 4
    elif invalid == "different_neck":
        keys[0]["key"]["index"] = 2
    elif invalid == "higher_pressure":
        keys.append(dict(keys[0], key=dict(index=2, value=14)))
    elif invalid == "broken_n":
        frame.first_defense_breach_index = 4
    elif invalid == "lower_open":
        bars[5] = replace(bars[5], open=12.8)
    elif invalid == "touch_record":
        bars[5] = replace(bars[5], close=13.1)
    elif invalid == "low_volume":
        bars[5] = replace(bars[5], volume=bars[4].volume)
    elif invalid == "broken_rolling":
        bars[5] = replace(bars[5], low=12.4)
    elif invalid == "too_early":
        frame.bar_index = 4
    assert (multilevel_squeeze(bars, candidate, frame, keys) is not None) is (invalid is None)


def test_xingwang_dual_squeeze_remains_causal_but_entry_waits_for_strong_candle():
    raw = json.loads((Path(__file__).parent / "fixtures/xingwang_2019_dual_squeeze.json").read_text())
    bars = [
        Bar(datetime.fromisoformat(day), raw["symbol"], *values) for day, *values in raw["bars"] if day <= "2019-04-15"
    ]
    config = SystemStrategy(
        **(whole_wave_profile({"scenarios": {"base": {"execution": {}}}})["strategy"] | {"volume_filter": False})
    )
    full = generate_system_signals(bars, config)
    dates = {str(bar.timestamp.date()): index for index, bar in enumerate(bars)}
    confirmation, attack = dates["2019-02-13"], dates["2019-02-11"]
    proof = next(e for e in full.audit if e["event"] == "multilevel_squeeze_confirmed"
                 and e["bar_index"] == confirmation and e["attack"] == attack)
    assert proof["buy_point_type"] == "multilevel_breakout_squeeze"
    assert str(bars[proof["key_source_index"]].timestamp.date()) == "2018-12-03"
    assert proof["higher_resistance_high"] == pytest.approx(19.5104980658)
    assert any(e["event"] == "regime_confirmation" and e["bar_index"] == confirmation
               and e["attack"] == attack and e["regime"] == MarketRegime.BULL.value for e in full.audit)
    assert not strong_bullish_candle(bars[confirmation])
    assert not any(s.side == "LONG" and s.bar_index == confirmation for s in full.signals)
    rejection = next(e for e in full.audit if e["event"] == "entry_rejected"
                     and e["bar_index"] == confirmation and e.get("attack") == attack
                     and e["reason"] == "entry_requires_strong_bullish_candle")
    assert rejection["confirmation_body_open_ratio"] == pytest.approx(0.010314595152140428)
    assert rejection["confirmation_body_range_ratio"] == pytest.approx(0.30769230769231265)
    assert rejection["confirmation_upper_shadow_ratio"] == pytest.approx(0.5230769230769196)
    # Historical independent combination entries do not own this squeeze. A
    # later buy still needs a fresh eligible event, rather than the old label.
    buy = next(s for s in full.signals if s.side == "LONG" and s.bar_index >= confirmation)
    assert str(buy.timestamp.date()) == "2019-03-22"
    assert str(buy.trigger_timestamp.date()) == "2019-03-05"
    assert strong_bullish_candle(bars[buy.bar_index])
    entry = next(e for e in full.audit if e["event"] == "long_signal" and e["bar_index"] == buy.bar_index)
    assert entry["confirmation_strong_bullish"] is True
    assert entry["squeeze_confirmation"] == "two_t_wave_push_gap"
    for day in ("2019-02-11", "2019-02-12", "2019-02-13", "2019-03-21", "2019-03-22"):
        cutoff = dates[day]
        prefix = generate_system_signals(bars[: cutoff + 1], config)
        assert prefix.signals == [s for s in full.signals if s.bar_index <= cutoff]
        assert prefix.audit == [e for e in full.audit if e["bar_index"] <= cutoff]
