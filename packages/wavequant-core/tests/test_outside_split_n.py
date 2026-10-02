"""An outside pullback can test the virtual neckline before a later real break."""

from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path

import pytest

from wavequant.domain.market_structure.n_shape import (
    BoxAnchorMode, MilestoneBasis, NSetup, NStatus, PivotRef, observe_n,
)
from wavequant.domain.market_structure.price_action import Direction
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals
from wavequant.domain.strategies.strategy_profiles import whole_wave_profile
from wavequant.interfaces.charts.visualization import ChartRepository


def sample():
    rows = [
        ("2025-09-23", 3.44, 3.45, 3.25, 3.34, 24652900),
        ("2025-09-24", 3.36, 3.43, 3.33, 3.41, 17280900),
        ("2025-09-25", 3.41, 3.43, 3.34, 3.35, 13310900),
        ("2025-09-26", 3.32, 3.44, 3.29, 3.39, 17667000),
        ("2025-09-29", 3.39, 3.53, 3.31, 3.51, 25299400),
        ("2025-09-30", 3.54, 3.58, 3.47, 3.55, 19423100),
    ]
    bars = [Bar(datetime.fromisoformat(day), "sz.300163", *values) for day, *values in rows]
    setup = NSetup(
        "sz.300163", "1d", Direction.UP, PivotRef(0, 1), PivotRef(1, 2), PivotRef(3, 3),
        "lecture_causal", BoxAnchorMode.ATTACK_VIRTUAL_EXTREME,
        allow_confirmation_bar=True, allow_outside_close=True, staged_defense=True,
    )
    return bars, setup


def observe(bars, setup):
    return observe_n(bars, setup, timeframe="1d", milestone_basis=MilestoneBasis.EXTREME)


def test_outside_virtual_test_then_real_break_freezes_completion_box():
    bars, setup = sample()
    forming = observe(bars[:4], setup)
    assert forming.status == NStatus.FORMING
    assert forming.virtual_break_now and not forming.real_break_now
    completed = observe(bars, setup)
    assert completed.completion.bar_index == 4
    assert completed.completion.defense == 3.29
    assert completed.targets.box_anchor == 3.53
    assert completed.targets.equal_wave == 3.47
    assert completed.targets.one_p == 3.81
    assert completed.targets.two_t == 4.09
    assert observe(bars[:5], setup).completion == completed.completion
    assert observe(bars[:5], setup).targets == completed.targets


def test_mirrored_bearish_outside_turn_completes_inverse_n():
    bars, setup = sample()
    inverse = [replace(b, open=10-b.open, high=10-b.low, low=10-b.high, close=10-b.close) for b in bars]
    result = observe(inverse, replace(setup, direction=Direction.DOWN))
    assert result.completion.bar_index == 4
    assert result.completion.defense == pytest.approx(6.71)
    assert result.targets.one_p == pytest.approx(6.19)
    assert result.targets.two_t == pytest.approx(5.91)


def test_outside_split_requires_explicit_convention_and_directional_body():
    bars, setup = sample()
    for altered in (replace(setup, allow_outside_close=False), replace(setup, source="strict_polyline")):
        with pytest.raises(ValueError, match="neckline is not the extreme"):
            observe(bars, altered)
    for opening in (3.39, 3.40):
        changed = [*bars[:3], replace(bars[3], open=opening), *bars[4:]]
        with pytest.raises(ValueError, match="neckline is not the extreme"):
            observe(changed, setup)


def test_outside_split_cannot_use_future_confirmation_or_ignore_intervening_high():
    bars, setup = sample()
    assert observe(bars[:4], replace(setup, pullback=PivotRef(3, 4))).status == NStatus.AWAIT_ANCHORS
    with pytest.raises(ValueError, match="neckline is not the extreme"):
        observe(bars, replace(setup, pullback=PivotRef(3, 4)))
    changed = [*bars[:2], replace(bars[2], high=3.44), *bars[3:]]
    with pytest.raises(ValueError, match="neckline is not the extreme"):
        observe(changed, setup)
    broken = [*bars[:4], replace(bars[4], low=3.28), *bars[5:]]
    assert observe(broken, setup).status == NStatus.PULLBACK_EXTENDED


def test_real_daily_pipeline_preserves_september_n_and_prefix():
    raw = json.loads((Path(__file__).parent / "fixtures/xianfeng_2025_split_n.json").read_text(encoding="utf-8"))
    bars = [Bar(datetime.fromisoformat(day), raw["symbol"], *values) for day, *values in raw["bars"]]
    dates = {str(bar.timestamp.date()): i for i, bar in enumerate(bars)}
    config = SystemStrategy(**whole_wave_profile({"scenarios": {"base": {"execution": {}}}})["strategy"])
    result = generate_system_signals(bars, config)
    attack = dates["2025-09-29"]
    event = next(e for e in result.audit if e["event"] == "n_completed" and e["bar_index"] == attack)
    assert event["direction"] == "up"
    assert [event[k] for k in ("origin", "neckline", "pullback")] == [dates[d] for d in ("2025-09-23", "2025-09-24", "2025-09-26")]
    assert (event["one_p"], event["two_t"], event["defense"]) == (3.81, 4.09, 3.29)
    prefix = generate_system_signals(bars[:attack+1], config)
    assert prefix.audit == [e for e in result.audit if e["bar_index"] <= attack]
    assert prefix.signals == [s for s in result.signals if s.bar_index <= attack]
    five = next(e for e in result.audit if e["event"] == "wave_projection_target_reached"
                and e.get("attack") == attack and e.get("reached_stage") == "five_top")
    assert five["timestamp"].startswith("2025-10-28")
    assert (five["reached_target"], five["target_stage"], five["target"]) == (4.93, "ten_full", 6.73)
    theory = ChartRepository.render_theory(
        None, bars, config, result, str(bars[-1].timestamp.date()), geometry={"tertiary_trends": {}},
    )
    displayed = next(e for e in theory["events"] if e["event"] == "n_completed" and e["bar_index"] == attack)
    assert displayed["available_at"] == "2025-09-29"
    assert [p["time"] for p in displayed["shape"]] == ["2025-09-23", "2025-09-24", "2025-09-26", "2025-09-29"]
    levels = {p["stage"]: p for p in displayed["levels"] if "stage" in p}
    assert (levels["five_top"]["price"], levels["five_top"]["status"]) == (4.93, "已满足")
    assert levels["ten_full"]["price"] == 6.22
    prefix_theory = ChartRepository.render_theory(
        None, bars[:attack+1], config, prefix, "2025-09-29", geometry={"tertiary_trends": {}},
    )
    prefix_event = next(e for e in prefix_theory["events"] if e["event"] == "n_completed" and e["bar_index"] == attack)
    assert not any("stage" in level for level in prefix_event["levels"])
