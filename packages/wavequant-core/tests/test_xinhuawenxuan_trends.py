from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path

from wavequant.domain.market_structure.alternation_breakout import promote_alternation_segments
from wavequant.domain.market_structure.secondary_trend import _structural_reversals
from wavequant.domain.market_structure.tertiary_trend import tertiary_trends
from wavequant.domain.models.model import Bar


def sample():
    raw = json.loads((Path(__file__).parent / "fixtures/xinhuawenxuan_2026_trend.json").read_text(encoding="utf-8"))
    bars = [Bar(datetime.fromisoformat(day), raw["symbol"], *values) for day, *values in raw["bars"]]
    base = dict(strokes=[raw["secondary_stroke"]], developing_strokes=[])
    return bars, base, raw["breakouts"]


def test_real_secondary_events_form_one_nonoverlapping_ordered_path_and_tertiary_develops():
    bars, base, breakouts = sample()
    before = deepcopy(base)
    result = promote_alternation_segments(base, bars, breakouts, 2)
    assert base == before
    assert len(result["strokes"]) == 1
    assert result["confirmed_alternation_segment_count"] == 5
    points = result["strokes"][0]["points"]
    assert len(points) == 48
    assert all(left["index"] < right["index"] for left, right in zip(points, points[1:]))
    assert all(
        left["kind"] != right["kind"]
        and (left["value"] < right["value"] if left["kind"] == "L" else left["value"] > right["value"])
        for left, right in zip(points, points[1:])
    )
    assert [(p["time"], p["kind"]) for p in points if p["time"] in ("2024-10-08", "2024-11-01")] == [
        ("2024-10-08", "H"),
        ("2024-11-01", "L"),
    ]
    assert all(s["id"] != "secondary-alternation-1502-2056" for s in result["strokes"])
    third = tertiary_trends(result, bars)
    assert [[(p["time"], p["kind"]) for p in s["points"]] for s in third["strokes"]] == [
        [("2019-03-13", "H"), ("2021-02-04", "L")]
    ]
    development = third["developing_strokes"][0]
    assert development["points"][0]["time"] == "2021-02-04"
    assert ("2023-05-05", "H") in [(p["time"], p["kind"]) for p in development["points"]]
    assert ("2023-05-05", "H") not in [(p["time"], p["kind"]) for s in third["strokes"] for p in s["points"]]


def test_first_source_extreme_without_preceding_key_can_initialize_later_valid_structure():
    bars, base, _ = sample()
    points = base["strokes"][0]["points"]
    assert points[0]["kind"] == "L"
    assert points[0]["value"] < min(p["value"] for p in points[1:] if p["kind"] == "L")
    assert [(p["time"], p["kind"]) for p in _structural_reversals(points, source_level=2)] == [("2021-02-04", "L")]


def test_prefix_does_not_use_later_alternation_or_future_third_point():
    bars, base, breakouts = sample()
    full = tertiary_trends(promote_alternation_segments(base, bars, breakouts, 2), bars)
    cutoff = "2023-08-09"
    prefix_bars = [bar for bar in bars if str(bar.timestamp.date()) <= cutoff]
    source = deepcopy(base)
    source["strokes"][0]["points"] = [p for p in source["strokes"][0]["points"] if p["available_at"] <= cutoff]
    prefix_events = [e for e in breakouts if str(bars[e["bar_index"]].timestamp.date()) <= cutoff]
    prefix = tertiary_trends(promote_alternation_segments(source, prefix_bars, prefix_events, 2), prefix_bars)
    assert [(p["time"], p["kind"]) for s in prefix["strokes"] for p in s["points"]] == [
        (p["time"], p["kind"]) for s in full["strokes"] for p in s["points"] if p["available_at"] <= cutoff
    ]
