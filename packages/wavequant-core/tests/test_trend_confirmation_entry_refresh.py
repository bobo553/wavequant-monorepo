from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Callable, TypedDict, cast
import unittest
from unittest.mock import patch

from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.chart_entry_history import _has_confirmation_price_cross, chart_entry_history
from wavequant.domain.strategies.hierarchical_entry import hierarchical_history


class _SourcePoint(TypedDict):
    index: int
    ordinal: int
    kind: str
    value: float
    state: str
    available_at: str


def bars_fixture() -> list[Bar]:
    rows = [(4.5, 5.5, 4.0, 5.0), (5.5, 6.0, 5.2, 5.8),
            (5.4, 6.3, 5.0, 5.8), (5.5, 6.3, 5.1, 6.2)]
    return [Bar(datetime(2026, 1, 1) + timedelta(days=index), "TEST", *row, 100)
            for index, row in enumerate(rows)]


def raw_fixture() -> list[_SourcePoint]:
    return [dict(index=index, ordinal=0, kind=kind, value=value, state="confirmed",
                 available_at=f"2026-01-{index + 1:02d}")
            for index, kind, value in [(0, "L", 4.0), (1, "H", 6.0), (2, "L", 5.0)]]


def fake_drawing(bars: Sequence[Bar], **kwargs: object) -> dict[str, object]:
    callback = cast(Callable[[int, int, list[_SourcePoint]], None], kwargs["on_step"])
    for now in range(2, len(bars)):
        callback(now, 0, raw_fixture())
    return {"strokes": []}


def empty_level(*args: object, **kwargs: object) -> dict[str, object]:
    return dict(strokes=[], candidate_strokes=[], bear_bull_alternation_lows=[])


def fake_first(_drawing: object, bars: Sequence[Bar], *,
               n_target_trend_confirmation_enabled: bool = False) -> dict[str, object]:
    lows: list[dict[str, object]] = []
    if len(bars) > 3 and bars[-1].close > 6.0:
        def ref(index: int, kind: str, price: float) -> dict[str, object]:
            return dict(index=index, ordinal=0, kind=kind, value=price,
                        available_at=f"2026-01-{index + 1:02d}")

        lows.append(dict(ref(2, "L", 5.0), id="causal-low", trend_level=1, source_path="first",
                         available_at=bars[-1].timestamp.date().isoformat(),
                         confirmed_flip_high=ref(1, "H", 6.0), confirmed_bear_low=ref(0, "L", 4.0),
                         broken_key=ref(0, "H", 5.5)))
    return dict(strokes=[dict(id="first", points=[])], candidate_strokes=[], bear_bull_alternation_lows=lows)


class TrendConfirmationEntryRefreshTests(unittest.TestCase):
    def test_source_close_recross_refreshes_without_a_new_high_or_vertex(self) -> None:
        bars = bars_fixture()
        self.assertEqual(bars[2].high, bars[3].high)
        self.assertTrue(_has_confirmation_price_cross(raw_fixture(), (), bars[2], bars[3], "2026-01-04", 3))

    def test_extreme_breaks_and_bearish_close_recrosses_are_symmetric(self) -> None:
        bars = bars_fixture()
        points: list[dict[str, object]] = [dict(kind="H", value=6.3, available_at="2026-01-02")]
        self.assertTrue(_has_confirmation_price_cross(points, (), bars[2], replace(bars[3], high=6.31),
                                                     "2026-01-04", 3))
        points = [dict(kind="L", value=5.0, available_at="2026-01-02")]
        self.assertTrue(_has_confirmation_price_cross(points, (), bars[2], replace(bars[3], low=4.99),
                                                     "2026-01-04", 3))
        self.assertTrue(_has_confirmation_price_cross(points, (), bars[2], replace(bars[3], close=4.99, low=4.9),
                                                     "2026-01-04", 3))

    def test_touch_unknown_and_developing_thresholds_do_not_refresh(self) -> None:
        bars = bars_fixture()
        for point in [dict(kind="H", value=6.3, available_at="2026-01-02"),
                      dict(kind="H", value=6.0, available_at="2026-01-05"),
                      dict(kind="H", value=6.0, available_at="2026-01-02", state="developing"),
                      dict(kind="H", value=6.0, available_at="2026-01-02", display_only=True)]:
            with self.subTest(point=point):
                self.assertFalse(_has_confirmation_price_cross([point], (), bars[2], replace(bars[3], close=6.0),
                                                              "2026-01-04", 3))

    def test_private_candidate_key_and_newly_known_evidence_refresh(self) -> None:
        bars = bars_fixture()
        level: dict[str, object] = dict(candidate_strokes=[dict(points=[
            dict(kind="H", value=6.0, available_at="2026-01-02")])])
        self.assertTrue(_has_confirmation_price_cross([], ((level, {}),), bars[2], bars[3], "2026-01-04", 3))
        points: list[dict[str, object]] = [dict(kind="H", value=6.0, available_at=3)]
        self.assertTrue(_has_confirmation_price_cross(points, (), bars[2], replace(bars[3], close=5.8),
                                                     "2026-01-04", 3))

    @patch("wavequant.domain.strategies.chart_entry_history.tertiary_trends", side_effect=empty_level)
    @patch("wavequant.domain.strategies.chart_entry_history.secondary_trends", side_effect=empty_level)
    @patch("wavequant.domain.strategies.chart_entry_history.reversal_trends", side_effect=fake_first)
    @patch("wavequant.domain.strategies.chart_entry_history.lecture_drawing", side_effect=fake_drawing)
    def test_chart_observes_market_certificate_on_unchanged_raw_source(self, *_mocks: object) -> None:
        bars = bars_fixture()
        history, events = chart_entry_history(bars)
        self.assertEqual(history[2], ())
        self.assertEqual(len(history[3]), 1)
        self.assertEqual(history[3][0].alternation_index, 3)
        self.assertEqual([(event["bar_index"], event["event"]) for event in events],
                         [(3, "hierarchy_alternation_ready")])

    @patch("wavequant.domain.strategies.chart_entry_history.tertiary_trends", side_effect=empty_level)
    @patch("wavequant.domain.strategies.chart_entry_history.secondary_trends", side_effect=empty_level)
    @patch("wavequant.domain.strategies.chart_entry_history.reversal_trends", side_effect=fake_first)
    @patch("wavequant.domain.strategies.chart_entry_history.lecture_drawing", side_effect=fake_drawing)
    def test_changed_unfinished_bar_replays_certificate_without_cache_contamination(self, *_mocks: object) -> None:
        bars = bars_fixture()
        cache: dict[str, object] = {}
        chart_entry_history(bars, prefix_cache=cache)
        checkpoint = cache["checkpoint"]
        equal_close = [*bars[:3], replace(bars[3], close=6.0)]
        self.assertEqual(chart_entry_history(equal_close, prefix_cache=cache), chart_entry_history(equal_close))
        self.assertEqual(cache["checkpoint"], checkpoint)
        self.assertEqual(chart_entry_history(bars, prefix_cache=cache), chart_entry_history(bars))

    @patch("wavequant.domain.strategies.chart_entry_history.tertiary_trends", side_effect=empty_level)
    @patch("wavequant.domain.strategies.chart_entry_history.secondary_trends", side_effect=empty_level)
    @patch("wavequant.domain.strategies.chart_entry_history.reversal_trends", side_effect=fake_first)
    @patch("wavequant.domain.strategies.chart_entry_history.lecture_drawing", side_effect=fake_drawing)
    def test_prequalification_cache_is_not_reused(self, *_mocks: object) -> None:
        bars = bars_fixture()
        old: dict[str, object] = dict(key=(tuple(bars[:-1]), frozenset(), False, False, False), checkpoint="old")
        self.assertEqual(chart_entry_history(bars, prefix_cache=old), chart_entry_history(bars))

    @patch("wavequant.domain.strategies.hierarchical_entry.lecture_drawing", side_effect=fake_drawing)
    def test_integer_history_uses_qualified_reducers_with_exact_prefixes(self, _drawing: object) -> None:
        bars = bars_fixture()
        first_lengths: list[int] = []
        higher_lengths: list[tuple[int, int]] = []
        first_calls: list[int] = []
        higher_calls: list[int] = []

        def first(source: Sequence[dict[str, object]]) -> list[dict[str, object]]:
            first_calls.append(len(source))
            self.assertTrue(all(type(point["available_at"]) is int for point in source))
            return []

        def higher(_source: object, *, source_level: int) -> list[dict[str, object]]:
            higher_calls.append(source_level)
            return []

        def publish(_candidates: object, source: Sequence[dict[str, object]], prefix: Sequence[Bar], *,
                    source_level: int, qualified_source: object = None,
                    n_target_trend_confirmation_enabled: bool = False) -> list[dict[str, object]]:
            self.assertTrue(all(type(point["available_at"]) is int for point in source))
            if source_level == 0:
                first_lengths.append(len(prefix))
            else:
                higher_lengths.append((source_level, len(prefix)))
            return []

        with patch("wavequant.domain.strategies.hierarchical_entry._wave_reversals", side_effect=first), \
                patch("wavequant.domain.strategies.hierarchical_entry._candidate_structural_reversals", side_effect=higher), \
                patch("wavequant.domain.strategies.hierarchical_entry.publish_uptrends", side_effect=publish):
            history, _ = hierarchical_history(bars)
        self.assertEqual(len(first_calls), 2)
        self.assertEqual(higher_calls, [1, 2, 1, 2])
        self.assertEqual(first_lengths, [3, 4])
        self.assertEqual(higher_lengths, [(1, 3), (2, 3), (1, 4), (2, 4)])
        self.assertTrue(all(not points for levels in history.values() for points in levels.values()))


if __name__ == "__main__":
    unittest.main()
