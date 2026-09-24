from datetime import datetime
from pathlib import Path
import json
from dataclasses import replace
import pytest
from wavequant.domain.models.model import Bar
from wavequant.domain.market_structure.n_shape import NSetup, PivotRef, BoxAnchorMode, MilestoneBasis, observe_n
from wavequant.domain.market_structure.price_action import Direction
from wavequant.domain.strategies.n_record_reconfirmation import n_record_reconfirmation


def sample():
    raw = json.loads((Path(__file__).parent / "fixtures/xinhuawenxuan_2026_trend.json").read_text(encoding="utf-8"))
    bars = [Bar(datetime.fromisoformat(d), raw["symbol"], *v) for d, *v in raw["bars"] if d >= "2026-06-26"]
    dates = {str(b.timestamp.date()): i for i, b in enumerate(bars)}
    return bars, dates


def test_split_breakout_freezes_first_response_defense_not_completion_low():
    bars, d = sample()
    setup = NSetup(
        bars[0].symbol,
        "1d",
        Direction.UP,
        PivotRef(1, 1),
        PivotRef(1, 2),
        PivotRef(2, 3),
        "lecture_causal",
        BoxAnchorMode.ATTACK_VIRTUAL_EXTREME,
        allow_confirmation_bar=True,
        allow_mother_impulse=True,
        staged_defense=True,
    )
    n = observe_n(bars, setup, timeframe="1d", milestone_basis=MilestoneBasis.EXTREME)
    assert n.completion.bar_index == d["2026-07-03"]
    assert n.completion.defense == pytest.approx(14.869348749941786)
    assert n.completion.defense == min(
        bars[d["2026-07-02"]].low, bars[d["2026-07-03"]].low, bars[d["2026-07-01"]].close
    )
    assert bars[d["2026-08-31"]].low > n.completion.defense
    assert n.targets.two_t == pytest.approx(17.50288463208406)
    assert (
        observe_n(bars[: d["2026-07-03"]], setup, timeframe="1d", milestone_basis=MilestoneBasis.EXTREME).completion
        is None
    )
    assert (
        observe_n(bars[: d["2026-07-03"] + 1], setup, timeframe="1d", milestone_basis=MilestoneBasis.EXTREME).completion
        == n.completion
    )
    old = observe_n(bars, replace(setup, staged_defense=False), timeframe="1d", milestone_basis=MilestoneBasis.EXTREME)
    assert old.completion.defense > bars[d["2026-08-31"]].low
    with pytest.raises(ValueError):
        replace(setup, allow_mother_impulse=False)
    with pytest.raises(ValueError):
        observe_n(
            [bars[0], replace(bars[1], open=bars[1].close)] + bars[2:],
            setup,
            timeframe="1d",
            milestone_basis=MilestoneBasis.EXTREME,
        )


def test_new_n_record_break_can_reconfirm_old_defended_n_not_an_ordinary_bounce():
    bars, d = sample()
    args = dict(
        attack=d["2026-07-03"],
        defense=14.869348749941786,
        now=d["2026-07-14"],
        new_origin=d["2026-06-29"],
        new_neckline=d["2026-07-06"],
        new_pullback=d["2026-07-10"],
        new_known=d["2026-07-13"],
    )
    assert n_record_reconfirmation(bars, **args)["reconfirmed_n_date"] == "2026-07-14"
    for altered in [dict(new_known=d["2026-07-15"]), dict(new_neckline=d["2026-07-02"]), dict(defense=15.3)]:
        assert n_record_reconfirmation(bars, **(args | altered)) is None
    changed = list(bars)
    changed[args["now"]] = replace(changed[args["now"]], close=bars[d["2026-07-06"]].high)
    assert n_record_reconfirmation(changed, **args) is None


def test_xinhua_full_pipeline_preserves_abc_and_prefix():
    from wavequant.domain.strategies.integrated_strategy import generate_system_signals
    from .test_completed_wave_recovery import config

    raw = json.loads((Path(__file__).parent / "fixtures/xinhuawenxuan_2026_trend.json").read_text(encoding="utf-8"))
    bars = [Bar(datetime.fromisoformat(day), raw["symbol"], *values) for day, *values in raw["bars"]]
    dates = {str(b.timestamp.date()): i for i, b in enumerate(bars)}
    full = generate_system_signals(bars, config())
    n = next(e for e in full.audit if e["event"] == "n_completed" and e["bar_index"] == dates["2026-07-03"])
    assert n["defense"] == pytest.approx(14.869348749941786)
    ready = next(
        e for e in full.audit if e["event"] == "wave_continuation_ready" and e["attack_index"] == dates["2026-07-03"]
    )
    assert ready["squeeze_index"] == dates["2026-07-14"]
    assert any(s.side == "LONG" and s.bar_index == dates["2026-07-14"] for s in full.signals)
    now = dates["2026-09-17"]
    proof = next(e for e in full.audit if e["event"] == "long_signal" and e["bar_index"] == now)
    assert proof["wave_entry_n_date"] == "2026-07-03"
    assert proof["wave_a_high_date"] == "2026-08-03"
    assert proof["wave_b_low_date"] == "2026-08-31"
    assert proof["wave_a_class"] == "ordinary"
    assert proof["wave_equal_target"] == pytest.approx(17.610927129812975)
    assert proof["wave_defense"] == pytest.approx(n["defense"])
    assert "wave_c_1618_target" not in proof
    prefix = generate_system_signals(bars[: now + 1], config())
    assert prefix.signals == [s for s in full.signals if s.bar_index <= now]
    assert prefix.audit == [e for e in full.audit if e["bar_index"] <= now]


def test_strict_extreme_then_close_break_uses_pre_break_close_and_keeps_targets():
    rows = [(8.1, 8.5, 8, 8.2), (9.5, 10, 9.4, 9.8), (9.3, 9.7, 9, 9.1), (9.3, 10.1, 9.2, 9.7), (10, 10.2, 9.9, 10.1)]
    bars = [Bar(datetime(2020, 1, i + 1), "TEST", *v, 100) for i, v in enumerate(rows)]
    setup = NSetup(
        "TEST",
        "1d",
        Direction.UP,
        PivotRef(0, 0),
        PivotRef(1, 1),
        PivotRef(2, 2),
        "lecture_causal",
        BoxAnchorMode.ATTACK_VIRTUAL_EXTREME,
        staged_defense=True,
    )
    kwargs = dict(timeframe="1d", milestone_basis=MilestoneBasis.EXTREME)
    actual = observe_n(bars, setup, **kwargs)
    legacy = observe_n(bars, replace(setup, staged_defense=False), **kwargs)
    assert actual.completion.bar_index == 4
    assert actual.completion.defense == 9.1
    assert actual.targets == legacy.targets
    assert observe_n(bars[:4], setup, **kwargs).completion is None
