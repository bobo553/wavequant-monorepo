"""A real completed N qualifies its whole impulse once it strictly exceeds one P."""

from collections.abc import Mapping, Sequence
from datetime import datetime
import json
from pathlib import Path
from typing import cast

import pytest

from wavequant.domain.market_structure.lecture_drawing import lecture_drawing
from wavequant.domain.market_structure.lecture_trend import reversal_trends
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals
from wavequant.domain.strategies.strategy_profiles import whole_wave_profile


@pytest.fixture(scope="module")
def xiangyang() -> tuple[Bar, ...]:
    raw = json.loads(
        (Path(__file__).parent / "fixtures/xiangyang_2025_trend_break.json").read_text(encoding="utf-8")
    )
    assert raw["price_basis"] == "raw_unadjusted"
    assert raw["volume_unit"] == "shares"
    return tuple(Bar(datetime.fromisoformat(day), raw["symbol"], *prices) for day, *prices in raw["rows"])


def _records(value: object) -> list[Mapping[str, object]]:
    assert isinstance(value, (list, tuple))
    assert all(isinstance(item, dict) for item in value)
    return cast(list[Mapping[str, object]], list(value))


def _points(level: Mapping[str, object], field: str = "strokes") -> list[Mapping[str, object]]:
    return [point for stroke in _records(level.get(field, [])) for point in _records(stroke["points"])]


def _first_level(bars: Sequence[Bar], asof: str) -> Mapping[str, object]:
    prefix = [bar for bar in bars if bar.timestamp.date().isoformat() <= asof]
    return cast(Mapping[str, object], reversal_trends(lecture_drawing(prefix), prefix))


def _origin(level: Mapping[str, object]) -> Mapping[str, object]:
    return next(point for point in _points(level) if point["time"] == "2018-09-11" and point["kind"] == "L")


def test_real_n_uses_its_completion_attack_extreme_for_the_strict_one_p_target(
    xiangyang: tuple[Bar, ...],
) -> None:
    bars = tuple(bar for bar in xiangyang if bar.timestamp.date().isoformat() <= "2018-09-25")
    dates = {bar.timestamp.date().isoformat(): index for index, bar in enumerate(bars)}
    first, last = bars[dates["2018-09-11"]], bars[-1]
    assert (first.open, first.high, first.low, first.close, first.volume) == (5.04, 5.11, 4.94, 5.01, 2_084_663)
    assert (last.open, last.high, last.low, last.close, last.volume) == (5.78, 5.78, 5.30, 5.32, 8_665_930)
    strategy = SystemStrategy(**whole_wave_profile({"scenarios": {"base": {"execution": {}}}})["strategy"])
    result = generate_system_signals(bars, strategy)
    source = next(event for event in result.audit if event["event"] == "n_completed"
                  and event["direction"] == "up" and event["origin"] == dates["2018-09-11"])

    assert (source["origin"], source["neckline"], source["pullback"], source["bar_index"]) == (
        dates["2018-09-11"], dates["2018-09-12"], dates["2018-09-13"], dates["2018-09-14"],
    )
    assert source["box_anchor"] == 5.13
    assert source["one_p"] == pytest.approx(5.32)
    reached = next(bar for bar in bars[source["bar_index"] + 1:] if bar.high > source["one_p"])
    assert reached.timestamp.date().isoformat() == "2018-09-20"
    assert reached.high == 5.34 and reached.close == 5.31


def test_real_one_p_target_qualifies_the_origin_on_the_first_strict_hit(
    xiangyang: tuple[Bar, ...],
) -> None:
    before = _first_level(xiangyang, "2018-09-19")
    known = _first_level(xiangyang, "2018-09-20")
    assert not any(point["time"] == "2018-09-11" for point in _points(before))
    origin = _origin(known)
    proof = origin["trend_confirmation"]
    assert isinstance(proof, dict)
    assert origin["value"] == 4.94
    assert origin["available_at"] == "2018-09-20"
    assert origin["trend_confirmation_route"] == "source_n_strict_one_p_target"
    assert proof["available_at"] == "2018-09-20"
    assert proof["one_p_target"] == pytest.approx(5.32)


def test_real_qualified_impulse_keeps_its_unconfirmed_peak_developing(
    xiangyang: tuple[Bar, ...],
) -> None:
    level = _first_level(xiangyang, "2018-09-25")
    assert not any(point["time"] == "2018-09-25" and point["kind"] == "H" for point in _points(level))
    tail = next(stroke for stroke in _records(level.get("developing_strokes", []))
                if _records(stroke["points"])[0]["time"] == "2018-09-11")
    points = _records(tail["points"])
    assert [(point["time"], point["kind"], point["value"]) for point in points] == [
        ("2018-09-11", "L", 4.94), ("2018-09-25", "H", 5.78),
    ]
    proof = tail["confirmation"]
    assert isinstance(proof, dict)
    assert proof["available_at"] == "2018-09-20"
    assert proof["one_p_target"] == pytest.approx(5.32)


def test_real_one_p_impulse_remains_one_formal_segment_in_later_history(
    xiangyang: tuple[Bar, ...],
) -> None:
    level = _first_level(xiangyang, "2019-02-26")
    leg = next((left, right) for stroke in _records(level["strokes"])
               for left, right in zip(_records(stroke["points"]), _records(stroke["points"])[1:])
               if left["time"] == "2018-09-11" and right["time"] == "2018-09-25")
    assert [(point["kind"], point["value"]) for point in leg] == [("L", 4.94), ("H", 5.78)]
    assert leg[0]["available_at"] == "2018-09-20"
    assert cast(str, leg[1]["available_at"]) >= "2018-09-26"


@pytest.mark.parametrize("asof", ["2018-09-20", "2018-09-25", "2018-10-11"])
def test_real_n_confirmation_is_not_rewritten_by_later_history(
    xiangyang: tuple[Bar, ...], asof: str,
) -> None:
    prefix = _origin(_first_level(xiangyang, asof))
    later = _origin(_first_level(xiangyang, "2019-02-26"))
    assert prefix["trend_confirmation"] == later["trend_confirmation"]
    assert prefix["available_at"] == later["available_at"] == "2018-09-20"
