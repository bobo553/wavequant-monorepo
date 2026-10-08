import copy
from dataclasses import replace
from datetime import datetime, timedelta
from typing import NotRequired, TypedDict, cast
import unittest

from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.secondary_pullback_entry import (
    DIRECT_PROMOTION_RULE,
    SecondaryPullbackCandidate,
    secondary_pullback_candidates,
    secondary_pullback_history,
)


class _TestPoint(TypedDict):
    index: int
    kind: str
    value: float
    time: str
    available_at: str
    state: str
    confirmation_rule: NotRequired[str]
    broken_key: NotRequired["_TestPoint"]


class _TestPath(TypedDict):
    id: str
    points: list[_TestPoint]
    source_path: NotRequired[str]
    source_paths: NotRequired[list[str]]
    display_only: NotRequired[bool]


class _TestLevel(TypedDict):
    strokes: list[_TestPath]


def fixture() -> tuple[list[Bar], _TestLevel, _TestLevel, dict[str, int]]:
    rows = [
        (5.50, 5.76, 5.17, 5.50, 30), (5.40, 5.60, 5.10, 5.20, 20),
        (4.20, 4.40, 4.06, 4.30, 20), (4.40, 4.70, 4.20, 4.60, 30),
        (6.00, 6.67, 5.89, 6.03, 70), (5.60, 5.80, 5.30, 5.40, 40),
        (5.30, 5.50, 5.10, 5.20, 30), (5.10, 5.19, 4.99, 5.05, 20),
        (5.00, 5.08, 4.97, 5.02, 15), (4.96, 5.06, 4.90, 4.98, 12),
        (4.89, 5.08, 4.81, 5.06, 10), (5.20, 5.57, 5.20, 5.55, 90),
        (5.65, 5.95, 5.65, 5.92, 120),
    ]
    bars = [Bar(datetime(2022, 7, 1) + timedelta(days=index), "TEST", *row)
            for index, row in enumerate(rows)]
    dates = {bar.timestamp.date().isoformat(): index for index, bar in enumerate(bars)}

    def point(index: int, kind: str, known: int) -> _TestPoint:
        return dict(index=index, kind=kind, value=bars[index].high if kind == "H" else bars[index].low,
                    time=bars[index].timestamp.date().isoformat(),
                    available_at=bars[known].timestamp.date().isoformat(), state="confirmed")

    key = point(0, "H", 1)
    origin = point(2, "L", 3)
    high = point(4, "H", 5)
    high["confirmation_rule"] = DIRECT_PROMOTION_RULE
    high["broken_key"] = key
    second: _TestLevel = dict(strokes=[dict(id="secondary", source_path="first", points=[key, origin, high])])
    drawing: _TestLevel = dict(strokes=[dict(id="base", points=[point(7, "H", 8), point(10, "L", 10)])])
    return bars, drawing, second, dates


def first_fixture(second: _TestLevel) -> _TestLevel:
    return dict(strokes=[dict(id="first", source_path="base", points=second["strokes"][0]["points"])])


def candidate_fixture() -> tuple[list[Bar], SecondaryPullbackCandidate]:
    bars, drawing, second, dates = fixture()
    candidates = secondary_pullback_candidates(bars, drawing, second, dates, 10, first=first_fixture(second))
    assert len(candidates) == 1
    return bars, candidates[0]


class SecondaryPullbackEntryTests(unittest.TestCase):
    def test_deep_b_is_an_observed_base_low_with_original_a_target(self) -> None:
        bars, drawing, second, dates = fixture()
        before = copy.deepcopy((bars, drawing, second, dates))

        candidates = secondary_pullback_candidates(bars, drawing, second, dates, 10, first=first_fixture(second))

        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual((candidate.origin_price, candidate.high_price, candidate.key_price), (4.06, 6.67, 5.76))
        self.assertEqual((candidate.low_index, candidate.low_price, candidate.known_index), (10, 4.81, 10))
        self.assertAlmostEqual(candidate.retracement_ratio, (6.67 - 4.81) / (6.67 - 4.06))
        self.assertEqual((bars, drawing, second, dates), before)

        events, proofs = secondary_pullback_history(bars[:12], {10: candidates})
        self.assertEqual([(event["bar_index"], event["event"]) for event in events],
                         [(10, "secondary_pullback_observed"), (11, "secondary_pullback_candidate")])
        proof = proofs[11]
        self.assertEqual((proof["stop"], proof["target"], proof["reclaim_type"]), (4.81, 6.67, "gap"))
        self.assertTrue(proof["observed_low"])
        self.assertFalse(proof["formal_alternation"])
        self.assertFalse(proof["requires_positive_n"])
        self.assertEqual(proof["buy_point_type"], "secondary_deep_pullback_reclaim")
        self.assertEqual(proof["secondary_pullback_low_known_date"], "2022-07-11")
        self.assertAlmostEqual(cast(float, proof["gross_reward_risk"]), (6.67 - 5.55) / (5.55 - 4.81))

    def test_current_bar_b_publication_cannot_confirm_an_entry(self) -> None:
        bars, candidate = candidate_fixture()
        _, proofs = secondary_pullback_history(bars[:12], {11: (replace(candidate, low_known_index=11),)})
        self.assertEqual(proofs, {})

    def test_future_points_do_not_repaint_the_requested_prefix(self) -> None:
        bars, drawing, second, dates = fixture()
        first = first_fixture(second)
        self.assertEqual(secondary_pullback_candidates(bars, drawing, second, dates, 9, first=first), ())
        self.assertEqual(secondary_pullback_candidates(bars, drawing, second, dates, 10, first=first),
                         secondary_pullback_candidates(bars[:11], drawing, second, dates, 10, first=first))
        candidate = candidate_fixture()[1]
        full_events, full_proofs = secondary_pullback_history(bars, {10: (candidate,)})
        for end in range(1, len(bars) + 1):
            events, proofs = secondary_pullback_history(bars[:end], {10: (candidate,)})
            self.assertEqual(events, [event for event in full_events if cast(int, event["bar_index"]) < end])
            self.assertEqual(proofs, {index: proof for index, proof in full_proofs.items() if index < end})

    def test_only_direct_formal_secondary_upgrades_are_eligible(self) -> None:
        bars, drawing, second, dates = fixture()
        for mutation in ["ordinary", "developing", "display_only", "unknown_key", "equal_key"]:
            changed = copy.deepcopy(second)
            path = changed["strokes"][0]
            high = path["points"][-1]
            if mutation == "ordinary":
                high["confirmation_rule"] = "level1_structural_key_break"
            elif mutation == "developing":
                high["state"] = "developing"
            elif mutation == "display_only":
                path["display_only"] = True
            elif mutation == "unknown_key":
                high["broken_key"]["available_at"] = high["time"]
            else:
                high["broken_key"]["value"] = high["value"]
            with self.subTest(mutation=mutation):
                self.assertEqual(secondary_pullback_candidates(bars, drawing, changed, dates, 10,
                                                             first=first_fixture(second)), ())

    def test_freezes_known_h_before_b_and_never_uses_a_cross_path_h(self) -> None:
        bars, drawing, second, dates = fixture()
        for mutation in ["unknown_h", "same_day_h", "developing_b", "wrong_price", "split_path"]:
            changed = copy.deepcopy(drawing)
            points = changed["strokes"][0]["points"]
            if mutation == "unknown_h":
                points[0]["available_at"] = bars[11].timestamp.date().isoformat()
            elif mutation == "same_day_h":
                points[0]["available_at"] = points[1]["time"]
            elif mutation == "developing_b":
                points[1]["state"] = "developing"
            elif mutation == "wrong_price":
                points[1]["value"] = 4.90
            else:
                changed["strokes"] = [dict(id="first", points=points[:1]), dict(id="next", points=points[1:])]
            with self.subTest(mutation=mutation):
                self.assertEqual(secondary_pullback_candidates(bars, changed, second, dates, 10,
                                                             first=first_fixture(second)), ())

    def test_b_must_be_the_actual_deepest_low_after_a_and_origin_must_hold(self) -> None:
        bars, drawing, second, dates = fixture()
        for index, low in [(8, 4.70), (10, 3.99), (10, 5.00)]:
            changed_bars = list(bars)
            changed_bars[index] = replace(bars[index], low=low)
            changed_drawing = copy.deepcopy(drawing)
            if index == 10:
                changed_drawing["strokes"][0]["points"][1]["value"] = low
            with self.subTest(index=index, low=low):
                self.assertEqual(secondary_pullback_candidates(changed_bars, changed_drawing, second, dates, 10,
                                                             first=first_fixture(second)), ())

    def test_requires_explicit_same_source_ancestry_and_source_endpoint_proof(self) -> None:
        bars, drawing, second, dates = fixture()
        first = first_fixture(second)
        for mutation in ["missing_source", "other_epoch", "missing_endpoint", "developing_source"]:
            changed = copy.deepcopy(first)
            if mutation == "missing_source":
                changed["strokes"] = []
            elif mutation == "other_epoch":
                changed["strokes"][0]["source_path"] = "other-base"
            elif mutation == "missing_endpoint":
                changed["strokes"][0]["points"] = changed["strokes"][0]["points"][:-1]
            else:
                changed["strokes"][0]["display_only"] = True
            with self.subTest(mutation=mutation):
                self.assertEqual(secondary_pullback_candidates(bars, drawing, second, dates, 10, first=changed), ())
        connected = copy.deepcopy(first)
        connected["strokes"][0]["source_path"] = "declared-continuity"
        connected["strokes"][0]["source_paths"] = ["previous-base", "base"]
        self.assertEqual(len(secondary_pullback_candidates(bars, drawing, second, dates, 10, first=connected)), 1)

    def test_close_volume_and_body_are_strict_and_target_must_remain_unhit(self) -> None:
        bars, candidate = candidate_fixture()
        replacements = [
            replace(bars[11], close=5.19, open=5.00, low=5.00),
            replace(bars[11], volume=10),
            replace(bars[11], open=5.20, close=5.304, high=5.40),
            replace(bars[11], open=5.20, close=5.40, high=6.20, low=4.90),
            replace(bars[11], open=5.55, close=5.20),
            replace(bars[11], high=6.67),
            replace(bars[11], low=4.80),
        ]
        for current in replacements:
            with self.subTest(bar=current):
                changed = [*bars[:11], current]
                self.assertEqual(secondary_pullback_history(changed, {10: (candidate,)})[1], {})
        no_previous_volume = list(bars[:12])
        no_previous_volume[10] = replace(bars[10], volume=0)
        self.assertEqual(secondary_pullback_history(no_previous_volume, {10: (candidate,)})[1], {})

    def test_recovery_must_clear_every_high_since_the_frozen_h(self) -> None:
        bars, candidate = candidate_fixture()
        changed = list(bars[:12])
        changed[9] = replace(bars[9], high=5.56)
        self.assertEqual(secondary_pullback_history(changed, {10: (candidate,)})[1], {})
        changed[11] = replace(bars[11], high=5.80, close=5.78)
        proof = secondary_pullback_history(changed, {10: (candidate,)})[1][11]
        self.assertEqual((proof["reclaim_ceiling"], proof["reclaim_ceiling_date"]), (5.56, "2022-07-10"))

    def test_filled_gap_can_only_use_the_normal_body_route(self) -> None:
        bars, candidate = candidate_fixture()
        changed = [*bars[:11], replace(bars[11], low=5.08)]
        proof = secondary_pullback_history(changed, {10: (candidate,)})[1][11]
        self.assertEqual(proof["reclaim_type"], "body")
        self.assertFalse(proof["unfilled_gap"])

    def test_b_break_retires_that_observation_but_a_new_b_may_recover(self) -> None:
        bars, candidate = candidate_fixture()
        changed = list(bars)
        changed[11] = replace(bars[11], open=4.95, high=5.10, low=4.70, close=5.00)
        changed[12] = replace(bars[12], open=5.30, high=5.95, low=5.30, close=5.92)
        stale_events, stale_proofs = secondary_pullback_history(changed, {10: (candidate,), 11: (candidate,)})
        self.assertEqual(stale_proofs, {})
        self.assertIn("observed_b_low_broken", [event.get("reason") for event in stale_events])
        new_b = replace(candidate, low_index=11, low_price=4.70, low_known_index=11,
                        retracement_ratio=(6.67 - 4.70) / (6.67 - 4.06))
        self.assertIn(12, secondary_pullback_history(changed, {10: (candidate,), 11: (new_b,)})[1])

    def test_a_new_high_and_origin_break_retire_the_whole_old_episode(self) -> None:
        bars, candidate = candidate_fixture()
        for current, reason in [(replace(bars[11], high=6.68), "secondary_a_new_high"),
                                (replace(bars[11], low=4.05), "secondary_origin_low_broken")]:
            changed = [*bars[:11], current, bars[12]]
            events, proofs = secondary_pullback_history(changed, {10: (candidate,), 11: (candidate,)})
            self.assertEqual(proofs, {})
            self.assertIn(reason, [event.get("reason") for event in events])

    def test_proof_does_not_consume_a_before_the_strategy_emits_a_long(self) -> None:
        bars, candidate = candidate_fixture()
        _, proofs = secondary_pullback_history(bars, {10: (candidate,), 11: (candidate,)})
        self.assertEqual(set(proofs), {11, 12})


if __name__ == "__main__":
    unittest.main()
