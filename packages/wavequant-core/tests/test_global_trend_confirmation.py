"""One causal direction gate for first, second and third trend levels."""

from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timedelta
from typing import cast

import pytest

from wavequant.domain.market_structure.trend_confirmation import (
    SAME_LEVEL_KEY_BREAK, SOURCE_CYCLE_CONFIRMATION, prepare_confirmation_context, qualify_downtrend, qualify_uptrend,
)
from wavequant.domain.models.model import Bar


START = datetime(2024, 1, 1)


def evidence(proof: Mapping[str, object], name: str) -> Mapping[str, object]:
    value = proof[name]
    assert isinstance(value, Mapping)
    return cast(Mapping[str, object], value)


def reference(index: int, kind: str, value: float, known: int, *, dates: bool = False) -> dict[str, object]:
    return dict(index=index, ordinal=0, time=(START + timedelta(days=index)).date().isoformat(),
                kind=kind, value=value, available_at=(START + timedelta(days=known)).date().isoformat() if dates else known,
                state="confirmed", label=f"{kind}{index}")


def sample(*, dates: bool = False) -> tuple[list[Bar], list[dict[str, object]]]:
    values = [(10, 8, 9), (7, 5, 6), (9, 6, 8), (11, 8, 10.5), (10.8, 9, 10.2),
              (9.5, 8, 9), (10.5, 8.5, 10), (12, 9, 11.5), (11.8, 9.5, 11.3)]
    bars = [Bar(START + timedelta(days=index), "sz.test", (high + low) / 2, high, low, close, 100)
            for index, (high, low, close) in enumerate(values)]
    source = [reference(0, "H", 10, 0, dates=dates), reference(1, "L", 5, 2, dates=dates),
              reference(3, "H", 11, 4, dates=dates), reference(5, "L", 8, 6, dates=dates)]
    return bars, source


@pytest.mark.parametrize("level", (1, 2, 3))
@pytest.mark.parametrize("dates", (False, True))
def test_every_level_can_confirm_a_strict_known_same_level_key_break(level, dates):
    bars, source = sample(dates=dates)
    source[1]["trend_level"] = level
    proof = qualify_uptrend(source, source[1], 1, source[0], bars[:4], 3)
    assert proof is not None
    assert proof["direction"] == "up" and proof["confirmation_rule"] == SAME_LEVEL_KEY_BREAK
    assert proof["available_at"] == ("2024-01-04" if dates else 3)
    assert evidence(proof, "origin")["value"] == 5
    assert evidence(proof, "broken_key")["value"] == 10
    assert evidence(proof, "confirmed_by")["index"] == 3
    assert evidence(proof, "confirmed_by")["kind"] == "H"
    assert proof["alternation_low"] is None


@pytest.mark.parametrize("level", (1, 2, 3))
@pytest.mark.parametrize("dates", (False, True))
def test_every_level_requires_a_complete_source_cycle_when_own_key_is_not_broken(level, dates):
    bars, source = sample(dates=dates)
    source[1]["trend_level"] = level
    assert qualify_uptrend(source, source[1], 1, None, bars[:7], 6) is None
    proof = qualify_uptrend(source, source[1], 1, None, bars[:8], 7)
    assert proof is not None and proof["confirmation_rule"] == SOURCE_CYCLE_CONFIRMATION
    assert proof["available_at"] == ("2024-01-08" if dates else 7)
    assert evidence(proof, "broken_key")["index"] == 0
    assert evidence(proof, "flip_high")["index"] == 3
    assert evidence(proof, "alternation_low")["index"] == 5
    assert proof["retracement_ratio"] == .5
    assert evidence(proof, "confirmed_by")["kind"] == "K"
    assert evidence(proof, "confirmed_by")["value"] == 11.5
    assert evidence(proof, "confirmed_by")["label"] == "转多"


def test_source_confirmation_cannot_borrow_an_earlier_close_before_the_flip_or_counter_is_known():
    bars, source = sample()
    bars[5] = replace(bars[5], high=12, close=11.5)
    assert qualify_uptrend(source, source[1], 1, None, bars[:7], 6) is None
    source[3]["available_at"] = 8
    assert qualify_uptrend(source, source[1], 1, None, bars[:8], 7) is None


@pytest.mark.parametrize("dates", (False, True))
def test_the_first_known_cycle_waits_for_a_new_later_close_cross(dates):
    bars, source = sample(dates=dates)
    source[2]["available_at"] = "2024-01-07" if dates else 6
    for index in (4, 5, 6, 7, 8):
        bars[index] = replace(bars[index], high=12, close=11.5)
    assert qualify_uptrend(source, source[1], 1, None, bars, 8) is None
    bars[7] = replace(bars[7], close=10.5)
    proof = qualify_uptrend(source, source[1], 1, None, bars, 8)
    assert proof is not None and proof["available_at"] == ("2024-01-09" if dates else 8)
    assert evidence(proof, "confirmed_by")["kind"] == "K"


@pytest.mark.parametrize("state", ("seed", "developing"))
def test_unconfirmed_source_flip_or_counter_never_supplies_the_lower_route(state):
    bars, source = sample()
    for position in (2, 3):
        changed = [dict(point) for point in source]
        changed[position]["state"] = state
        assert qualify_uptrend(changed, changed[1], 1, None, bars, 8) is None


def test_equal_key_equal_confirmation_and_exact_two_thirds_are_not_breaks():
    bars, source = sample()
    equal = [dict(point) for point in source]
    equal[2]["value"] = 10
    assert qualify_uptrend(equal, equal[1], 1, None, bars, 8) is None
    bars[7] = replace(bars[7], close=11)
    bars[8] = replace(bars[8], close=11)
    assert qualify_uptrend(source, source[1], 1, None, bars, 8) is None
    bars, source = sample()
    source[3]["value"] = 7
    bars[5] = replace(bars[5], low=7)
    assert qualify_uptrend(source, source[1], 1, None, bars, 8) is None
    bars, source = sample()
    bars[3] = replace(bars[3], high=10, close=10)
    assert qualify_uptrend(source, source[1], 1, source[0], bars[:4], 3) is None


def test_unknown_same_level_key_does_not_confirm_the_breaking_prefix():
    bars, source = sample()
    unknown = dict(source[0], available_at=3)
    assert qualify_uptrend(source, source[1], 1, unknown, bars[:4], 3) is None
    unknown.pop("available_at")
    assert qualify_uptrend(source, source[1], 1, unknown, bars[:4], 3) is None


def test_origin_or_alternation_failure_before_confirmation_cannot_revive():
    bars, source = sample()
    bars[2] = replace(bars[2], low=4)
    assert qualify_uptrend(source, source[1], 1, source[0], bars, 8) is None
    assert qualify_uptrend(source, source[1], 1, None, bars, 8) is None
    bars, source = sample()
    bars[6] = replace(bars[6], low=7.9)
    assert qualify_uptrend(source, source[1], 1, None, bars, 8) is None


def test_first_certificate_survives_later_failure_and_prefix_extension():
    bars, source = sample()
    direct = qualify_uptrend(source, source[1], 1, source[0], bars[:4], 3)
    cycle = qualify_uptrend(source, source[1], 1, None, bars[:8], 7)
    bars[8] = replace(bars[8], low=4)
    assert qualify_uptrend(source, source[1], 1, source[0], bars, 8) == direct
    assert qualify_uptrend(source, source[1], 1, None, bars, 8) == cycle


def test_a_higher_source_attack_retries_with_new_pullback_origin_but_keeps_original_defense_and_key():
    values = [(5, 4.5, 4.7), (4.5, 4, 4.2), (5, 4.1, 4.8), (7, 5, 6), (6, 5, 5.5),
              (5, 4.5, 4.7), (6, 5, 5.8), (8, 5.5, 7.7), (7.5, 6.5, 7),
              (7, 6, 6.5), (7.9, 6.5, 7.5), (8.5, 7, 8.3)]
    bars = [Bar(START + timedelta(days=index), "sz.test", (high + low) / 2, high, low, close, 100)
            for index, (high, low, close) in enumerate(values)]
    source = [reference(0, "H", 5, 0), reference(1, "L", 4, 2), reference(3, "H", 7, 4),
              reference(5, "L", 4.5, 6), reference(7, "H", 8, 8), reference(9, "L", 6, 10)]
    assert qualify_uptrend(source, source[1], 1, None, bars[:11], 10) is None
    proof = qualify_uptrend(source, source[1], 1, None, bars, 11)
    assert proof is not None and proof["available_at"] == 11
    assert evidence(proof, "origin")["value"] == 4 and evidence(proof, "broken_key")["value"] == 5
    assert evidence(proof, "retracement_origin")["value"] == 4.5
    assert evidence(proof, "flip_high")["value"] == 8 and evidence(proof, "alternation_low")["value"] == 6
    assert proof["retracement_ratio"] == pytest.approx(2 / 3.5)


def test_reversed_source_order_cannot_be_repaired_by_sorting_or_skipping_points():
    bars, source = sample()
    reordered = [source[0], source[1], source[3], source[2]]
    assert qualify_uptrend(reordered, reordered[1], 1, None, bars, 8) is None
    assert qualify_uptrend(source, source[1], 2, None, bars, 8) is None


@pytest.mark.parametrize("dates", (False, True))
def test_without_bars_only_a_confirmed_own_key_source_break_can_qualify(dates):
    _, source = sample(dates=dates)
    assert qualify_uptrend(source, source[1], 1, None, None, 8) is None
    proof = qualify_uptrend(source, source[1], 1, source[0], None, 8)
    assert proof is not None and proof["confirmation_rule"] == SAME_LEVEL_KEY_BREAK
    assert proof["available_at"] == ("2024-01-05" if dates else 4)
    source[2]["state"] = "developing"
    assert qualify_uptrend(source, source[1], 1, source[0], None, 8) is None


def test_downward_development_uses_the_mirrored_complete_cycle():
    bars, source = sample()
    mirrored = [Bar(bar.timestamp, bar.symbol, 20 - bar.open, 20 - bar.low, 20 - bar.high,
                    20 - bar.close, bar.volume) for bar in bars]
    downward = []
    for point in source:
        value = point["value"]
        assert isinstance(value, (int, float))
        downward.append(dict(point, kind="L" if point["kind"] == "H" else "H", value=20 - value))
    assert qualify_downtrend(downward, downward[1], 1, None, mirrored[:7], 6) is None
    proof = qualify_downtrend(downward, downward[1], 1, None, mirrored[:8], 7)
    assert proof is not None and proof["direction"] == "down"
    assert evidence(proof, "flip_low")["value"] == 9 and evidence(proof, "alternation_high")["value"] == 12
    assert evidence(proof, "confirmed_by")["kind"] == "K"
    assert evidence(proof, "confirmed_by")["value"] == 8.5
    assert evidence(proof, "confirmed_by")["label"] == "转空"


@pytest.mark.parametrize("binding", ("source", "bars", "cutoff"))
def test_a_prepared_context_cannot_be_reused_for_a_different_source_market_or_prefix(binding):
    bars, source = sample()
    context = prepare_confirmation_context(source, bars, 7)
    assert qualify_uptrend(source, source[1], 1, None, bars, 7, context=context) == (
        qualify_uptrend(source, source[1], 1, None, bars, 7))
    cutoff = 7
    if binding == "source":
        source = [dict(point) for point in source]
    elif binding == "bars":
        bars = list(bars)
    else:
        cutoff = 6
    with pytest.raises(ValueError, match="context"):
        qualify_uptrend(source, source[1], 1, None, bars, cutoff, context=context)


def competing_routes_sample(*, dates: bool, own_break: int) -> tuple[list[Bar], list[dict[str, object]]]:
    values = [(13, 11, 12), (11, 9, 10), (10, 9.5, 9.8), (8, 5, 6), (9, 6, 8),
              (11, 8, 10.5), (10.8, 9, 10.2), (9.5, 8, 9), (10.5, 8.5, 10),
              (12, 9, 11.5), (14, 10, 13.5)]
    bars = [Bar(START + timedelta(days=index), "sz.test", (high + low) / 2, high, low, close, 100)
            for index, (high, low, close) in enumerate(values)]
    bars[own_break] = replace(bars[own_break], high=14)
    source = [reference(index, kind, value, known, dates=dates)
              for index, kind, value, known in ((0, "H", 13, 1), (1, "L", 9, 2), (2, "H", 10, 3),
                                                 (3, "L", 5, 4), (5, "H", 11, 6), (7, "L", 8, 8))]
    return bars, source


@pytest.mark.parametrize("dates", (False, True))
@pytest.mark.parametrize("own_break", (8, 9, 10))
def test_source_cycle_competes_with_earlier_same_day_or_later_own_key_proof(dates: bool, own_break: int) -> None:
    bars, source = competing_routes_sample(dates=dates, own_break=own_break)
    cycle = qualify_uptrend(source, source[3], 3, None, bars, 10)
    assert cycle is not None and cycle["confirmation_rule"] == SOURCE_CYCLE_CONFIRMATION
    assert evidence(cycle, "confirmed_by")["index"] == 9
    proof = qualify_uptrend(source, source[3], 3, source[0], bars, 10)
    assert proof is not None
    assert evidence(proof, "confirmed_by")["index"] == min(own_break, 9)
    if own_break == 10:
        assert proof == cycle
    else:
        assert proof["confirmation_rule"] == SAME_LEVEL_KEY_BREAK
        assert proof["alternation_low"] is None


@pytest.mark.parametrize("dates", (False, True))
def test_later_known_stronger_source_flips_cannot_replace_an_earlier_own_key_certificate(dates: bool) -> None:
    bars, source = competing_routes_sample(dates=dates, own_break=8)
    first = qualify_uptrend(source, source[3], 3, source[0], bars[:9], 8)
    assert first is not None and evidence(first, "confirmed_by")["index"] == 8
    cycle = qualify_uptrend(source, source[3], 3, None, bars, 10)
    assert cycle is not None and evidence(cycle, "confirmed_by")["index"] == 9
    bars[10] = replace(bars[10], low=11)
    bars.extend([Bar(START + timedelta(days=11), "sz.test", 12, 13, 11, 12, 100),
                 Bar(START + timedelta(days=12), "sz.test", 13, 15, 12, 14.5, 100)])
    extended = [*source, reference(8, "H", 14, 11, dates=dates), reference(10, "L", 11, 11, dates=dates)]
    assert qualify_uptrend(extended, extended[3], 3, extended[0], bars, 12) == first
    assert qualify_uptrend(extended, extended[3], 3, None, bars, 12) == cycle


@pytest.mark.parametrize("dates", (False, True))
def test_a_flip_first_known_on_the_origin_failure_session_cannot_confirm_later(dates: bool) -> None:
    bars, source = sample(dates=dates)
    bars[4] = replace(bars[4], low=4)
    assert source[2]["available_at"] == ("2024-01-05" if dates else 4)
    assert qualify_uptrend(source, source[1], 1, None, bars, 8) is None


@pytest.mark.parametrize("dates", (False, True))
def test_a_cutoff_cannot_hide_nonmonotone_later_source_knowledge(dates: bool) -> None:
    bars, source = competing_routes_sample(dates=dates, own_break=8)
    source[5]["available_at"] = "2024-01-11" if dates else 10
    invalid = [*source, reference(8, "H", 14, 9, dates=dates)]
    context = prepare_confirmation_context(invalid, bars, 10)
    assert context.ordered is False
    assert qualify_uptrend(invalid, invalid[3], 3, invalid[0], bars, 10, context=context) is None
