"""An independent next drawing path cannot certify or extend an older N."""

from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from wavequant.domain.market_structure import lecture_trend
from wavequant.domain.market_structure.n_trend_confirmation import qualified_n_source, qualify_n_trend
from wavequant.domain.market_structure.n_trend_reversals import n_target_reversals
from wavequant.domain.market_structure.secondary_trend import secondary_trends
from wavequant.domain.market_structure.tertiary_trend import tertiary_trends
from wavequant.domain.market_structure.trend_publication import publish_uptrends
from wavequant.domain.models.model import Bar


START = datetime(2024, 1, 1)


def sample() -> tuple[list[Bar], dict[str, object]]:
    prices = [(30, 25, 28), (15, 10, 12), (20, 11, 18), (6, 5, 5.5), (6.3, 5.5, 6),
              (7, 6.4, 6.7), (6.8, 6.2, 6.5), (6.5, 6, 6.2), (7, 6.2, 6.6),
              (7.5, 6.6, 7.2), (7.2, 6.8, 7), (9.8, 7, 9.5), (10.1, 9, 9.9)]
    bars = [Bar(START + timedelta(days=index), "sz.test", (high + low) / 2, high, low, close, 100)
            for index, (high, low, close) in enumerate(prices)]

    def point(index: int, kind: str, value: float, known: int, state: str = "confirmed") -> dict[str, object]:
        return dict(index=index, ordinal=0, time=bars[index].timestamp.date().isoformat(), kind=kind, value=value,
                    available_at=bars[known].timestamp.date().isoformat(), state=state, label=kind)

    old = [point(index, kind, value, known, state) for index, kind, value, known, state in (
        (0, "H", 30, 0, "seed"), (1, "L", 10, 2, "confirmed"), (2, "H", 20, 3, "confirmed"),
        (3, "L", 5, 4, "confirmed"), (5, "H", 7, 6, "confirmed"), (7, "L", 6, 8, "confirmed"),
        (9, "H", 7.5, 10, "confirmed"), (10, "L", 6.8, 10, "developing"),
    )]
    new = [point(11, "L", 7, 11, "seed"), point(12, "H", 10.1, 12, "developing")]
    return bars, dict(strokes=[dict(id="old", points=old), dict(id="next", points=new)])


def _old_candidate(points: list[Mapping[str, object]]) -> list[dict[str, object]]:
    origin = next((point for point in points if point["index"] == 3 and point["kind"] == "L"), None)
    if origin is None:
        return []
    # Isolate publication from the unrelated ordered-direction geometry gate.
    # The source origin and every actual N proof remain unmodified.
    return [dict(origin, available_at="2024-01-11", source_turn_position=2,
                 confirmation_rule="known_structural_origin", wave_direction_before="down", wave_direction_after="up")]


def test_an_old_candidate_cannot_borrow_the_next_paths_market_target(monkeypatch: pytest.MonkeyPatch) -> None:
    bars, drawing = sample()
    monkeypatch.setattr(lecture_trend, "_wave_reversals", _old_candidate)
    result = lecture_trend.reversal_trends(drawing, bars)
    assert not any(point["index"] == 3 for path in result["strokes"] for point in path["points"])


def test_a_target_before_the_break_keeps_its_tail_inside_the_original_path(monkeypatch: pytest.MonkeyPatch) -> None:
    bars, drawing = sample()
    bars[10] = replace(bars[10], high=10.1)
    bars[12] = replace(bars[12], high=15)
    monkeypatch.setattr(lecture_trend, "_wave_reversals", _old_candidate)
    result = lecture_trend.reversal_trends(drawing, bars)
    origin = next(point for path in result["strokes"] for point in path["points"] if point["index"] == 3)
    assert origin["available_at"] == "2024-01-11"
    assert origin["trend_confirmation"]["confirmed_by"]["index"] == 10
    assert result["developing_strokes"]
    for path in result["developing_strokes"]:
        assert all(point["index"] < 11 for point in path["points"])


@pytest.mark.parametrize("invalid", ("future", "unknown", "display"))
def test_origin_failure_cannot_promote_an_unavailable_source_peak(invalid: str) -> None:
    prices = [(20, 19, 19.5), (6, 5, 5.5), (6.3, 5.5, 6), (7, 6.4, 6.7),
              (6.8, 6.2, 6.5), (6.5, 6, 6.2), (7, 6.2, 6.6), (7.5, 6.6, 7.2),
              (9.8, 7, 9.5), (10, 9, 9.8), (10.1, 9, 9.9), (11, 4.9, 8)]
    bars = [Bar(START + timedelta(days=index), "sz.test", (high + low) / 2, high, low, close, 100)
            for index, (high, low, close) in enumerate(prices)]
    source: list[dict[str, object]] = [dict(index=index, ordinal=0, kind=kind, value=value, available_at=known,
                                          time=bars[index].timestamp.date().isoformat(), state="confirmed", label=kind)
                                     for index, kind, value, known in ((0, "H", 20, 0), (1, "L", 5, 2),
                                                                       (3, "H", 7, 4), (5, "L", 6, 6),
                                                                       (10, "H", 10.1, 11))]
    if invalid == "future":
        source[-1]["available_at"] = 20
    elif invalid == "unknown":
        source[-1]["state"] = "unknown"
    else:
        source[-1]["display_only"] = True
    candidates = n_target_reversals([], source, bars, source_level=0)
    assert not any(point["index"] == 10 for point in candidates)
    for point in candidates:
        known = point["available_at"]
        assert type(known) is int and known <= 11


def _n_source_sample(*, dated: bool = False) -> tuple[list[Bar], list[dict[str, object]]]:
    prices = [(20, 19, 19.5), (6, 5, 5.5), (6.3, 5.5, 6), (7, 6.4, 6.7), (6.8, 6.2, 6.5),
              (6.5, 6, 6.2), (7, 6.2, 6.6), (7.5, 6.6, 7.2), (9.8, 7, 9.5), (10, 9, 9.8), (10.1, 9, 9.9)]
    bars = [Bar(START + timedelta(days=index), "sz.test", (high + low) / 2, high, low, close, 100)
            for index, (high, low, close) in enumerate(prices)]
    source: list[dict[str, object]] = [dict(index=index, ordinal=0, kind=kind, value=value,
                                          available_at=bars[known].timestamp.date().isoformat() if dated else known,
                                          time=bars[index].timestamp.date().isoformat(), state="reversal", label=kind)
                                     for index, kind, value, known in ((0, "H", 20, 0), (1, "L", 5, 2),
                                                                       (3, "H", 7, 4), (5, "L", 6, 6))]
    return bars, source


@pytest.mark.parametrize("dated", (False, True))
def test_n_source_mask_preserves_positions_and_waits_for_formal_publication(dated: bool) -> None:
    bars, source = _n_source_sample(dated=dated)
    public = [dict(point) for point in source]
    public[2]["available_at"] = "2024-01-06" if dated else 5
    masked = qualified_n_source(source, public)
    assert len(masked) == len(source)
    assert [(point["index"], point["ordinal"]) for point in masked] == [(point["index"], point["ordinal"]) for point in source]
    assert masked[2]["available_at"] == public[2]["available_at"]
    assert source[2]["available_at"] == ("2024-01-05" if dated else 4)
    assert qualify_n_trend(masked, masked[1], 1, bars, 10, up=True) is not None
    public[3]["available_at"] = "2024-01-10" if dated else 9
    later = qualified_n_source(source, public)
    assert qualify_n_trend(later, later[1], 1, bars, 10, up=True) is None


@pytest.mark.parametrize("invalid", ("absent", "future", "unknown", "display", "clock", "value"))
def test_n_source_mask_never_borrows_an_unpublished_or_unmatched_anchor(invalid: str) -> None:
    bars, source = _n_source_sample()
    public = [dict(point) for point in source]
    if invalid == "absent":
        public = public[:3]
    elif invalid == "future":
        public[3]["available_at"] = 20
    elif invalid == "unknown":
        public[3]["state"] = "unknown"
    elif invalid == "display":
        public[3]["display_only"] = True
    elif invalid == "clock":
        public[3]["available_at"] = "2024-01-07"
    else:
        public[3]["value"] = 6.01
    masked = qualified_n_source(source, public)
    assert len(masked) == len(source)
    assert qualify_n_trend(masked, masked[1], 1, bars, 10, up=True) is None


@pytest.mark.parametrize("level", (2, 3))
def test_higher_n_route_cannot_use_the_lower_levels_unpublished_abc(level: int) -> None:
    bars, source = _n_source_sample(dated=True)
    lower = dict(strokes=[dict(id="same", points=[source[0]])], structure_strokes=[dict(id="same", points=source)])
    result = secondary_trends(lower, bars) if level == 2 else tertiary_trends(lower, bars)
    assert not any(point["index"] == 1 for path in result["strokes"] for point in path["points"])
    assert not any(point["index"] == 1 for path in result["developing_strokes"] for point in path["points"])


def test_an_origin_breach_endpoint_waits_for_its_public_source_peak() -> None:
    bars, source = _n_source_sample()
    bars.append(Bar(START + timedelta(days=11), "sz.test", 8, 11, 4.9, 8, 100))
    bars.extend(Bar(START + timedelta(days=index), "sz.test", 8, 9, 6, 8, 100) for index in range(12, 21))
    source.append(dict(index=10, ordinal=0, kind="H", value=10.1, available_at=11,
                       time=bars[10].timestamp.date().isoformat(), state="reversal", label="H10"))
    public = [dict(point) for point in source]
    public[-1]["available_at"] = 20
    before_candidates = n_target_reversals([], source, bars[:12], source_level=1, qualified_source=public)
    later_candidates = n_target_reversals([], source, bars, source_level=1, qualified_source=public)
    before = publish_uptrends(before_candidates, source, bars[:12], source_level=1, qualified_source=public)
    later = publish_uptrends(later_candidates, source, bars, source_level=1, qualified_source=public)
    endpoint = next(point for point in later if point["index"] == 10)
    assert endpoint["available_at"] == 20
    assert [point for point in later if isinstance(point["available_at"], int) and point["available_at"] <= 11] == before
