"""A completed N grants one trend only after a strict frozen-target break."""

from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from wavequant.domain.market_structure.n_trend_confirmation import (
    N_TARGET_CONFIRMATION, prepare_n_confirmation_context, qualify_n_trend,
)
from wavequant.domain.models.model import Bar


START = datetime(2024, 1, 1)


def sample(*, dates: bool = False, up: bool = True) -> tuple[list[Bar], list[dict[str, object]]]:
    prices = [(20, 19, 19.5), (6, 5, 5.5), (6.3, 5.5, 6), (7, 6.4, 6.7),
              (6.8, 6.2, 6.5), (6.5, 6, 6.2), (7, 6.2, 6.6), (7.5, 6.6, 7.2),
              (9.8, 7, 9.5), (10, 9, 9.8), (10.1, 9, 9.9), (11, 6.1, 8)]
    bars = [Bar(START + timedelta(days=index), "sz.test", (high + low) / 2, high, low, close, 100)
            for index, (high, low, close) in enumerate(prices)]
    source: list[dict[str, object]] = [dict(index=index, ordinal=0, time=(START + timedelta(days=index)).date().isoformat(),
                   kind=kind, value=value, available_at=(START + timedelta(days=known)).date().isoformat() if dates else known,
                   state="confirmed", label=f"{kind}{index}")
              for index, kind, value, known in ((0, "H", 20, 0), (1, "L", 5, 2), (3, "H", 7, 4), (5, "L", 6, 6))]
    if up:
        return bars, source
    mirrored = [Bar(bar.timestamp, bar.symbol, 30 - bar.open, 30 - bar.low, 30 - bar.high,
                    30 - bar.close, bar.volume) for bar in bars]
    downward: list[dict[str, object]] = []
    for point in source:
        value = point["value"]
        assert isinstance(value, (float, int))
        downward.append(dict(point, kind="H" if point["kind"] == "L" else "L", value=30 - value))
    return mirrored, downward


def evidence(proof: Mapping[str, object], field: str) -> Mapping[str, object]:
    item = proof[field]
    assert isinstance(item, Mapping)
    return item


@pytest.mark.parametrize("dates", (False, True))
@pytest.mark.parametrize("up", (False, True))
def test_n_target_is_strict_and_uses_the_actual_completion_box(dates: bool, up: bool) -> None:
    bars, source = sample(dates=dates, up=up)
    assert qualify_n_trend(source, source[1], 1, bars, 9, up=up) is None
    proof = qualify_n_trend(source, source[1], 1, bars, 10, up=up)
    assert proof is not None and proof["confirmation_rule"] == N_TARGET_CONFIRMATION
    assert proof["direction"] == ("up" if up else "down")
    assert proof["available_at"] == ("2024-01-11" if dates else 10)
    assert proof["box_anchor"] == (7.5 if up else 22.5)
    assert proof["one_p_target"] == (10 if up else 20)
    assert proof["defense"] == pytest.approx(6.2 if up else 23.8)
    assert evidence(proof, "n_completion")["index"] == 7
    assert evidence(proof, "confirmed_by")["index"] == 10
    assert evidence(proof, "n_neckline")["index"] == 3
    assert evidence(proof, "n_pullback")["index"] == 5
    assert evidence(proof, "origin")["index"] == 1
    assert proof["target_basis"] == "market_extreme"


@pytest.mark.parametrize("up", (False, True))
@pytest.mark.parametrize("failure", ("defense", "origin"))
def test_same_day_failure_outranks_the_target_extreme(up: bool, failure: str) -> None:
    bars, source = sample(up=up)
    if up:
        bars[10] = replace(bars[10], low=6.1 if failure == "defense" else 4.9)
    else:
        bars[10] = replace(bars[10], high=23.9 if failure == "defense" else 25.1)
    assert qualify_n_trend(source, source[1], 1, bars, 10, up=up) is None


@pytest.mark.parametrize("up", (False, True))
def test_a_first_certificate_survives_later_failure_and_longer_prefixes(up: bool) -> None:
    bars, source = sample(up=up)
    proof = qualify_n_trend(source, source[1], 1, bars[:11], 10, up=up)
    assert proof is not None
    assert qualify_n_trend(source, source[1], 1, bars, 11, up=up) == proof


@pytest.mark.parametrize("state", ("seed", "developing"))
@pytest.mark.parametrize("position", (1, 2, 3))
def test_unconfirmed_source_anchors_never_supply_an_n(state: str, position: int) -> None:
    bars, source = sample()
    source[position]["state"] = state
    assert qualify_n_trend(source, source[1], 1, bars, 10, up=True) is None


def test_source_identity_clock_and_display_evidence_are_required() -> None:
    bars, source = sample()
    assert qualify_n_trend(source, dict(source[1], value=5.1), 1, bars, 10, up=True) is None
    assert qualify_n_trend(source, source[1], 2, bars, 10, up=True) is None
    assert qualify_n_trend(source, source[1], 1, None, 10, up=True) is None
    source[3]["display_only"] = True
    assert qualify_n_trend(source, source[1], 1, bars, 10, up=True) is None


def test_late_pullback_knowledge_cannot_replay_an_earlier_attack() -> None:
    bars, source = sample()
    source[3]["available_at"] = 8
    assert qualify_n_trend(source, source[1], 1, bars, 10, up=True) is None
    source[3]["available_at"] = 11
    assert qualify_n_trend(source, source[1], 1, bars, 10, up=True) is None


def test_reordered_source_knowledge_and_zero_length_anchors_are_rejected() -> None:
    bars, source = sample()
    source[2]["available_at"] = 7
    assert qualify_n_trend(source, source[1], 1, bars, 10, up=True) is None
    bars, source = sample()
    source[3].update(index=3, ordinal=0, time="2024-01-04", value=6.4)
    assert qualify_n_trend(source, source[1], 1, bars, 10, up=True) is None


def test_a_later_pullback_cannot_replace_the_first_completed_ns_defense() -> None:
    bars, source = sample()
    bars[8] = replace(bars[8], high=7, low=6.1, close=6.5)
    bars[9] = replace(bars[9], high=7.8, low=6.5, close=7.5)
    bars[10] = replace(bars[10], high=10.7, low=7, close=10)
    source.append(dict(index=8, ordinal=0, time="2024-01-09", kind="L", value=6.1,
                       available_at=9, state="confirmed", label="新 C"))
    assert qualify_n_trend(source, source[1], 1, bars, 10, up=True) is None


def test_confirmed_inside_child_preserves_two_ordered_anchors_on_one_session() -> None:
    values = [(7.9, 7.94, 7.32, 7.8), (7.72, 7.74, 7.55, 7.6), (7.7, 7.9, 7.6, 7.85),
              (8.1, 8.48, 7.7, 8.4), (8.2, 8.5, 8, 8.4)]
    bars = [Bar(START + timedelta(days=index), "sz.test", *value, 100) for index, value in enumerate(values)]
    source: list[dict[str, object]] = [dict(index=index, ordinal=ordinal, kind=kind, value=value,
                                          available_at=index, state="confirmed", time=bars[index].timestamp.date().isoformat())
                                     for index, ordinal, kind, value in ((0, 0, "L", 7.32), (1, 0, "H", 7.74),
                                                                         (1, 1, "L", 7.55))]
    assert qualify_n_trend(source, source[0], 0, bars, 3, up=True) is None
    proof = qualify_n_trend(source, source[0], 0, bars, 4, up=True)
    assert proof is not None and proof["one_p_target"] == 8.48
    assert evidence(proof, "n_neckline")["ordinal"] == 0
    assert evidence(proof, "n_pullback")["ordinal"] == 1
    source[2]["ordinal"] = 0
    assert qualify_n_trend(source, source[0], 0, bars, 4, up=True) is None


@pytest.mark.parametrize("dates", (False, True))
@pytest.mark.parametrize("up", (False, True))
def test_prepared_n_context_preserves_the_same_first_certificate(dates: bool, up: bool) -> None:
    bars, source = sample(dates=dates, up=up)
    context = prepare_n_confirmation_context(source, bars, 11)
    assert qualify_n_trend(source, source[1], 1, bars, 11, up=up, context=context) == (
        qualify_n_trend(source, source[1], 1, bars, 11, up=up))


@pytest.mark.parametrize("binding", ("source", "bars", "cutoff"))
def test_prepared_n_context_cannot_borrow_another_source_market_or_clock(binding: str) -> None:
    bars, source = sample()
    context = prepare_n_confirmation_context(source, bars, 10)
    cutoff = 10
    if binding == "source":
        source = [dict(point) for point in source]
    elif binding == "bars":
        bars = list(bars)
    else:
        cutoff = 9
    with pytest.raises(ValueError, match="context"):
        qualify_n_trend(source, source[1], 1, bars, cutoff, up=True, context=context)
