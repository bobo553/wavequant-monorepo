"""Real history must qualify a hierarchy before publishing its trend line."""

from collections.abc import Mapping, Sequence
from datetime import datetime
import json
from pathlib import Path
from typing import cast

import pytest

from wavequant.domain.market_structure.lecture_drawing import lecture_drawing
from wavequant.domain.market_structure.lecture_trend import reversal_trends
from wavequant.domain.market_structure.secondary_trend import secondary_trends
from wavequant.domain.market_structure.tertiary_trend import tertiary_trends
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.secondary_pullback_entry import (
    secondary_pullback_candidates,
    secondary_pullback_history,
)


HISTORICAL_PREFIXES = (
    "2018-11-23", "2018-12-13", "2018-12-26", "2019-01-15",
    "2019-01-25", "2019-02-11", "2019-02-12", "2019-02-26",
)


@pytest.fixture(scope="module")
def xiangyang() -> tuple[Bar, ...]:
    raw = json.loads(
        (Path(__file__).parent / "fixtures/xiangyang_2025_trend_break.json").read_text(encoding="utf-8")
    )
    return tuple(Bar(datetime.fromisoformat(day), raw["symbol"], *prices) for day, *prices in raw["rows"])


def _records(value: object) -> list[Mapping[str, object]]:
    assert isinstance(value, (list, tuple))
    result = []
    for item in value:
        assert isinstance(item, dict)
        result.append(cast(Mapping[str, object], item))
    return result


def _pipeline(
    bars: Sequence[Bar], asof: str,
) -> tuple[list[Bar], Mapping[str, object], Mapping[str, object], Mapping[str, object], Mapping[str, object]]:
    prefix = [bar for bar in bars if bar.timestamp.date().isoformat() <= asof]
    drawing = lecture_drawing(prefix)
    first = reversal_trends(drawing, prefix)
    second = secondary_trends(first, prefix)
    third = tertiary_trends(second, prefix)
    return prefix, drawing, first, second, third


def _points(level: Mapping[str, object], field: str = "strokes") -> list[Mapping[str, object]]:
    return [point for stroke in _records(level[field]) for point in _records(stroke["points"])]


def _assert_evidence_known(value: object, asof: str, end_index: int) -> None:
    if isinstance(value, dict):
        evidence = cast(Mapping[str, object], value)
        known = evidence.get("available_at")
        if isinstance(known, str):
            assert known <= asof
        elif type(known) is int:
            assert 0 <= known <= end_index
        for item in evidence.values():
            _assert_evidence_known(item, asof, end_index)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _assert_evidence_known(item, asof, end_index)


def _formal_signature(level: Mapping[str, object], asof: str) -> list[tuple[object, ...]]:
    return sorted(
        (point["index"], point.get("ordinal", 0), point["kind"], point["value"], point["available_at"])
        for point in _points(level) if cast(str, point["available_at"]) <= asof
    )


def _assert_trend_certificate(proof: object, bars: Sequence[Bar], asof: str) -> None:
    assert isinstance(proof, dict)
    if proof['confirmation_rule']=='source_n_strict_one_p_target':
        up=proof['direction']=='up'
        sign=1 if up else -1
        origin,neckline,pullback,completion,trigger=(proof[name] for name in
            ('origin','n_neckline','n_pullback','n_completion','confirmed_by'))
        assert all(isinstance(point,dict) for point in (origin,neckline,pullback,completion,trigger))
        assert (origin['kind'],neckline['kind'],pullback['kind'])==(('L','H','L') if up else ('H','L','H'))
        assert origin['index']<neckline['index']<=pullback['index']<=completion['index']<=trigger['index']
        assert 0<sign*(pullback['value']-origin['value'])<sign*(neckline['value']-origin['value'])
        assert max(origin['available_at'],neckline['available_at'],pullback['available_at'])<=completion['available_at']
        assert completion['available_at']<=trigger['available_at']==proof['available_at']<=asof
        assert proof['one_p_target']==pytest.approx(2*proof['box_anchor']-origin['value'])
        _assert_evidence_known(proof,proof['available_at'],len(bars)-1)
        bar=bars[trigger['index']]
        observed=bar.close if trigger['index']==completion['index'] else bar.high if up else bar.low
        assert trigger['value']==observed
        assert sign*(observed-proof['one_p_target'])>0
        for prior in bars[completion['index']+1:trigger['index']+1]:
            adverse=prior.low if up else prior.high
            assert sign*(adverse-origin['value'])>=0
            assert sign*(adverse-proof['defense'])>=0
        return
    assert proof["direction"] == "up"
    key, origin, turn = proof["broken_key"], proof["origin"], proof["confirmed_by"]
    assert all(isinstance(point, dict) for point in (key, origin, turn))
    assert (key["kind"], origin["kind"]) == ("H", "L")
    assert key["index"] < origin["index"] < turn["index"]
    _assert_evidence_known(proof, cast(str, proof["available_at"]), len(bars) - 1)
    assert proof["available_at"] <= asof
    assert key["available_at"] < turn["time"]
    bar = bars[turn["index"]]
    assert bar.timestamp.date().isoformat() == turn["time"]
    if proof["confirmation_rule"] == "strict_same_level_market_key_break":
        assert proof.get("alternation_low") is None
        assert turn["value"] == bar.high > key["value"]
        return
    assert proof["confirmation_rule"] == "source_key_break_alternation_then_market_turn"
    flip, low = proof["flip_high"], proof["alternation_low"]
    assert isinstance(flip, dict) and isinstance(low, dict)
    assert (flip["kind"], low["kind"], turn["kind"]) == ("H", "L", "K")
    assert origin["index"] < flip["index"] < low["index"] < turn["index"]
    assert flip["value"] > key["value"]
    assert origin["value"] < low["value"] < flip["value"]
    assert max(flip["available_at"], low["available_at"]) < turn["time"]
    assert bars[turn["index"] - 1].close <= flip["value"] < bar.close
    assert turn["value"] == bar.close


def test_lower_key_break_alone_does_not_publish_the_october_secondary_origin(
    xiangyang: tuple[Bar, ...],
) -> None:
    _, _, _, second, _ = _pipeline(xiangyang, "2018-12-13")

    assert not any(
        point["time"] == "2018-10-19" and point["kind"] == "L" and point["value"] == 4.40
        for point in _points(second)
    )


def test_lower_key_break_alone_does_not_draw_the_october_november_secondary_tail(
    xiangyang: tuple[Bar, ...],
) -> None:
    _, _, _, second, _ = _pipeline(xiangyang, "2018-12-13")

    for stroke in _records(second["developing_strokes"]):
        points = _records(stroke["points"])
        assert not any(
            left["time"] == "2018-10-19" and left["value"] == 4.40
            and right["time"] == "2018-11-23" and right["value"] == 5.85
            for left, right in zip(points, points[1:])
        )


@pytest.mark.parametrize("asof", HISTORICAL_PREFIXES)
def test_every_hierarchy_keeps_its_proof_known_and_formal_points_prefix_invariant(
    xiangyang: tuple[Bar, ...], asof: str,
) -> None:
    prefix, _, *levels = _pipeline(xiangyang, asof)
    _, _, *later_levels = _pipeline(xiangyang, HISTORICAL_PREFIXES[-1])

    for level, later in zip(levels, later_levels):
        _assert_evidence_known(level, asof, len(prefix) - 1)
        dates = {bar.timestamp.date().isoformat(): index for index, bar in enumerate(prefix)}
        for point in _points(level):
            published = cast(str, point["available_at"])
            _assert_evidence_known(point, published, dates[published])
            if point.get("trend_confirmation") is not None:
                _assert_trend_certificate(point["trend_confirmation"], prefix, asof)
        for stroke in _records(level.get("developing_strokes", [])):
            proof = stroke.get("confirmation")
            if isinstance(proof, dict) and proof.get("direction") == "up":
                _assert_trend_certificate(proof, prefix, asof)
        assert _formal_signature(level, asof) == _formal_signature(later, asof)


def test_same_level_last_fall_high_break_still_directly_promotes_the_2022_high(
    xiangyang: tuple[Bar, ...],
) -> None:
    _, _, _, before, _ = _pipeline(xiangyang, "2022-06-24")
    known_bars, _, _, first_known, _ = _pipeline(xiangyang, "2022-07-04")
    _, _, _, later, _ = _pipeline(xiangyang, "2022-07-19")

    assert not any(point["time"] == "2022-06-24" and point["kind"] == "H" for point in _points(before))
    high = next(point for point in _points(first_known) if point["time"] == "2022-06-24")
    assert (high["kind"], high["value"], high["available_at"]) == ("H", 6.67, "2022-07-04")
    key = high["broken_key"]
    assert isinstance(key, dict)
    assert key["available_at"] < high["time"]
    assert _formal_signature(first_known, "2022-07-04") == _formal_signature(later, "2022-07-04")
    _assert_evidence_known(high, "2022-07-04", len(known_bars) - 1)
    assert (key["time"], key["kind"], key["value"]) == ("2020-11-20", "H", 5.76)


def test_the_july_2022_recovery_keeps_a_real_deep_pullback_and_frozen_secondary_target(
    xiangyang: tuple[Bar, ...],
) -> None:
    observed, drawing, first, second, _ = _pipeline(xiangyang, "2022-07-18")
    recovery, _, _, _, _ = _pipeline(xiangyang, "2022-07-19")
    dates = {bar.timestamp.date().isoformat(): index for index, bar in enumerate(observed)}
    candidates = secondary_pullback_candidates(observed, drawing, second, dates, len(observed) - 1, first=first)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert (candidate.high_price, candidate.low_price) == (6.67, 4.81)
    assert observed[candidate.high_known_index].timestamp.date().isoformat() == "2022-07-04"
    assert observed[candidate.low_known_index].timestamp.date().isoformat() == "2022-07-18"
    assert candidate.resistance_known_index < candidate.low_index
    _, proofs = secondary_pullback_history(recovery, {len(observed) - 1: candidates})
    proof = proofs[len(recovery) - 1]
    assert (proof["stop"], proof["target"]) == (4.81, 6.67)
    assert proof["observed_low"] is True
    assert proof["formal_alternation"] is False
    assert proof["requires_positive_n"] is False
    assert candidate.key_price == 5.76
