from datetime import datetime
import unittest

from wavequant.domain.models.model import Bar
from wavequant.domain.market_structure.trend_landmarks import (
    bear_bull_alternation_lows,
    bear_to_bull_highs,
    bullish_turn_signals,
    post_alternation_bull_highs,
)


def point(index, kind, value, available_at, **extra):
    return {
        "index": index,
        "ordinal": 0,
        "time": f"2026-01-{index + 1:02d}",
        "kind": kind,
        "value": value,
        "available_at": available_at,
        "label": f"{kind}{index}",
        **extra,
    }


class TrendLandmarkTests(unittest.TestCase):
    def test_first_strict_close_cross_after_confirmed_alternation_is_bullish_turn(self):
        frozen_key = point(1, "H", 20, "2026-01-03")
        bear_low = point(2, "L", 10, "2026-01-04")
        flip_high = point(3, "H", 30, "2026-01-05")
        alternation_low = point(
            4,
            "L",
            18,
            "2026-01-08",
            observations=[{
                "title": "空多交替",
                "available_at": "2026-01-08",
                "ratio": 0.6,
                "flip_high": flip_high,
                "confirmed_bear_low": bear_low,
                "broken_key": frozen_key,
                "origin": bear_low,
            }],
        )
        bars = [
            Bar(datetime(2026, 1, day), "sh.600000", 28, high, 27, close, 1000)
            for day, high, close in ((7, 31, 29), (8, 32, 31), (9, 31, 30), (10, 33, 30), (11, 35, 31))
        ]

        signals = bullish_turn_signals(
            [{"id": "level-one", "points": [bear_low, flip_high, alternation_low]}],
            bars,
            trend_level=1,
        )

        self.assertEqual([(item["time"], item["value"]) for item in signals], [("2026-01-11", 31)])
        self.assertEqual(signals[0]["previous_close"], 30)
        self.assertEqual(signals[0]["breakout_level"], 30)
        self.assertEqual(signals[0]["confirmed_flip_high"]["time"], "2026-01-04")
        self.assertEqual(signals[0]["confirmed_alternation_low"]["time"], "2026-01-05")

    def test_intraday_touch_equality_and_pre_confirmation_cross_do_not_turn_bullish(self):
        frozen_key = point(1, "H", 20, "2026-01-03")
        bear_low = point(2, "L", 10, "2026-01-04")
        flip_high = point(3, "H", 30, "2026-01-05")
        alternation_low = point(
            4,
            "L",
            18,
            "2026-01-10",
            observations=[{
                "title": "空多交替",
                "available_at": "2026-01-10",
                "ratio": 0.6,
                "flip_high": flip_high,
                "confirmed_bear_low": bear_low,
                "broken_key": frozen_key,
                "origin": bear_low,
            }],
        )
        bars = [
            Bar(datetime(2026, 1, day), "sh.600000", 28, high, 27, close, 1000)
            for day, high, close in ((8, 29, 29), (9, 35, 31), (10, 31, 30), (11, 35, 30), (12, 35, 29))
        ]

        self.assertEqual(
            bullish_turn_signals(
                [{"id": "level-one", "points": [bear_low, flip_high, alternation_low]}],
                bars,
                trend_level=1,
            ),
            [],
        )

    def test_first_confirmed_high_after_alternation_ends_the_first_bull_leg(self):
        frozen_key = point(1, "H", 18.28, "2022-05-26")
        bear_low = point(2, "L", 13.16, "2022-06-01")
        flip_high = point(3, "H", 49.56, "2022-08-09")
        alternation_low = point(
            4,
            "L",
            29.75,
            "2022-09-01",
            observations=[
                {
                    "title": "空多交替",
                    "available_at": "2022-09-01",
                    "ratio": 0.5442,
                    "flip_high": flip_high,
                    "confirmed_bear_low": bear_low,
                    "broken_key": frozen_key,
                    "origin": bear_low,
                }
            ],
        )
        first_bull_high = point(5, "H", 33.89, "2022-09-08")
        later_high = point(7, "H", 38.28, "2022-10-14")
        strokes = [
            {
                "id": "level-one",
                "points": [bear_low, flip_high, alternation_low, first_bull_high, point(6, "L", 28.76, "2022-09-15"), later_high],
            }
        ]

        landmarks = post_alternation_bull_highs(strokes, trend_level=1)

        self.assertEqual([(item["index"], item["value"]) for item in landmarks], [(5, 33.89)])
        self.assertEqual(landmarks[0]["available_at"], "2022-09-08")
        self.assertEqual(landmarks[0]["confirmed_alternation_low"]["value"], 29.75)
        self.assertEqual(landmarks[0]["confirmed_flip_high"]["value"], 49.56)
        self.assertEqual(landmarks[0]["broken_key"]["value"], 18.28)

    def test_post_alternation_high_does_not_skip_an_invalid_next_vertex(self):
        frozen_key = point(1, "H", 20, "2026-01-03")
        bear_low = point(2, "L", 10, "2026-01-08")
        flip_high = point(3, "H", 30, "2026-01-09")
        alternation_low = point(
            4,
            "L",
            18,
            "2026-01-12",
            observations=[
                {
                    "title": "空多交替",
                    "available_at": "2026-01-12",
                    "ratio": 0.6,
                    "flip_high": flip_high,
                    "confirmed_bear_low": bear_low,
                    "broken_key": frozen_key,
                    "origin": bear_low,
                }
            ],
        )
        invalid_next = point(5, "L", 17, "2026-01-13")
        later_high = point(6, "H", 25, "2026-01-15")

        self.assertEqual(
            post_alternation_bull_highs(
                [{"id": "malformed", "points": [bear_low, flip_high, alternation_low, invalid_next, later_high]}],
                trend_level=1,
            ),
            [],
        )

    def test_confirmed_alternation_low_keeps_the_full_bear_to_bull_chain(self):
        frozen_key = point(1, "H", 27.71, "2026-01-03")
        bear_low = point(2, "L", 13.16, "2026-01-08")
        flip_high = point(3, "H", 49.56, "2026-01-09")
        pullback_origin = point(2, "L", 13.16, "2026-01-08")
        alternation_low = point(
            4,
            "L",
            29.75,
            "2026-01-12",
            observations=[
                {
                    "title": "空多交替",
                    "available_at": "2026-01-12",
                    "ratio": 0.5442,
                    "weak_countermove": False,
                    "flip_high": flip_high,
                    "confirmed_bear_low": bear_low,
                    "broken_key": frozen_key,
                    "origin": pullback_origin,
                }
            ],
        )

        landmarks = bear_bull_alternation_lows(
            [{"id": "level-one", "points": [bear_low, flip_high, alternation_low]}],
            trend_level=1,
        )

        self.assertEqual([(item["time"], item["value"]) for item in landmarks], [("2026-01-05", 29.75)])
        self.assertEqual(landmarks[0]["available_at"], "2026-01-12")
        self.assertEqual(landmarks[0]["confirmed_flip_high"]["value"], 49.56)
        self.assertEqual(landmarks[0]["confirmed_bear_low"]["value"], 13.16)
        self.assertEqual(landmarks[0]["broken_key"]["value"], 27.71)
        self.assertAlmostEqual(landmarks[0]["retracement_ratio"], 0.5442)

    def test_unconfirmed_or_malformed_alternation_low_is_rejected(self):
        frozen_key = point(1, "H", 30, "2026-01-03")
        bear_low = point(2, "L", 18, "2026-01-08")
        valid_high = point(3, "H", 35, "2026-01-09")

        def candidate(index, title="空多交替", **overrides):
            event = {
                "title": title,
                "available_at": "2026-01-12",
                "ratio": 0.4,
                "flip_high": valid_high,
                "confirmed_bear_low": bear_low,
                "broken_key": frozen_key,
                "origin": bear_low,
                **overrides,
            }
            return point(index, "L", 24, "2026-01-12", observations=[event])

        invalid = [
            candidate(4, title="回档未通过交替条件"),
            candidate(5, flip_high=point(3, "H", 30, "2026-01-09")),
            candidate(6, broken_key=None),
            candidate(7, ratio=2 / 3),
            candidate(8, origin=None),
            candidate(9, flip_high={"index": 3, "kind": "H", "value": 35}),
            candidate(10, confirmed_bear_low={"index": 2, "kind": "L", "value": 18}),
        ]

        self.assertEqual(
            bear_bull_alternation_lows([{"id": "invalid", "points": invalid}], trend_level=1),
            [],
        )

    def test_bear_to_bull_high_uses_the_confirming_high_and_formal_low_availability(self):
        first_high = point(3, "H", 35, "2026-01-05")
        second_high = point(7, "H", 49.56, "2026-01-09")
        strokes = [
            {
                "id": "secondary-sample",
                "points": [
                    point(
                        2,
                        "L",
                        13.16,
                        "2026-01-08",
                        flip="翻空为多",
                        wave_direction_before="down",
                        wave_direction_after="up",
                        broken_key=point(1, "H", 27.71, "2026-01-03"),
                        confirmed_by=first_high,
                    ),
                    point(4, "H", 45, "2026-01-06", flip="翻多为空", confirmed_by=point(5, "L", 29, "2026-01-07")),
                    point(
                        6,
                        "L",
                        22.06,
                        "2026-01-12",
                        wave_direction_before="down",
                        wave_direction_after="up",
                        broken_key=point(5, "H", 41, "2026-01-07"),
                        confirmed_by=[point(5, "H", 41, "2026-01-07"), second_high, point(8, "L", 31, "2026-01-10")],
                    ),
                ],
            }
        ]

        landmarks = bear_to_bull_highs(strokes, trend_level=2)

        self.assertEqual([(item["time"], item["value"]) for item in landmarks], [("2026-01-04", 35)])
        self.assertEqual([item["available_at"] for item in landmarks], ["2026-01-08"])
        self.assertEqual(landmarks[0]["confirmed_low"]["value"], 13.16)
        self.assertEqual(landmarks[0]["broken_key"]["value"], 27.71)
        self.assertEqual(landmarks[0]["source_path"], "secondary-sample")

        renamed_source = {**strokes[0], "id": "secondary-rebuilt-for-a-later-asof"}
        self.assertEqual(
            bear_to_bull_highs([renamed_source], trend_level=2)[0]["id"],
            landmarks[0]["id"],
        )
        self.assertEqual(landmarks[0]["trend_level"], 2)

    def test_level_one_uses_the_real_key_break_observation(self):
        frozen_key = point(3, "H", 30, "2026-01-05")
        bear_low = point(4, "L", 18, "2026-01-06")
        breakout = point(
            7,
            "H",
            35,
            "2026-01-09",
            observations=[
                {
                    "title": "翻空为多",
                    "available_at": "2026-01-09",
                    "key": frozen_key,
                    "confirmed_low": bear_low,
                }
            ],
        )

        landmarks = bear_to_bull_highs([{"id": "level-one", "points": [bear_low, breakout]}], trend_level=1)

        self.assertEqual(len(landmarks), 1)
        self.assertEqual(landmarks[0]["value"], 35)
        self.assertEqual(landmarks[0]["broken_key"]["value"], 30)
        self.assertEqual(landmarks[0]["confirmed_low"]["value"], 18)

    def test_later_same_level_lower_low_invalidates_bull_flip_at_every_level(self):
        frozen_key = point(1, "H", 27.71, "2026-01-03")
        confirmed_low = point(2, "L", 13.16, "2026-01-08")
        flip_high = point(
            3,
            "H",
            35,
            "2026-01-09",
            observations=[
                {
                    "title": "翻空为多",
                    "available_at": "2026-01-09",
                    "key": frozen_key,
                    "confirmed_low": confirmed_low,
                }
            ],
        )
        equal_low = point(4, "L", 13.16, "2026-01-10")
        lower_low = point(5, "L", 12.8, "2026-01-12")

        for trend_level in (1, 2, 3):
            with self.subTest(trend_level=trend_level):
                if trend_level == 1:
                    valid_points = [confirmed_low, flip_high, equal_low]
                else:
                    structural_low = {
                        **confirmed_low,
                        "flip": "翻空为多",
                        "broken_key": frozen_key,
                        "confirmed_by": flip_high,
                    }
                    valid_points = [structural_low, flip_high, equal_low]

                self.assertEqual(
                    [item["time"] for item in bear_to_bull_highs([{"id": "sample", "points": valid_points}], trend_level=trend_level)],
                    ["2026-01-04"],
                )
                self.assertEqual(
                    bear_to_bull_highs(
                        [{"id": "sample", "points": [*valid_points, lower_low]}],
                        trend_level=trend_level,
                    ),
                    [],
                )
                if trend_level > 1:
                    converted_high = {
                        **flip_high,
                        "flip": "翻多为空",
                        "broken_key": equal_low,
                        "confirmed_by": lower_low,
                    }
                    self.assertEqual(
                        bear_to_bull_highs(
                            [{"id": "sample", "points": [valid_points[0], converted_high]}],
                            trend_level=trend_level,
                        ),
                        [],
                    )

    def test_missing_equal_or_unbroken_last_fall_high_is_rejected(self):
        invalid_lows = [
            point(
                2,
                "L",
                18,
                "2026-01-08",
                flip="翻空为多",
                confirmed_by=point(5, "H", 35, "2026-01-09"),
            ),
            point(
                4,
                "L",
                17,
                "2026-01-10",
                flip="翻空为多",
                broken_key=point(3, "H", 35, "2026-01-05"),
                confirmed_by=point(5, "H", 35, "2026-01-09"),
            ),
            point(
                6,
                "L",
                16,
                "2026-01-12",
                flip="翻空为多",
                broken_key=point(5, "H", 40, "2026-01-09"),
                confirmed_by=point(7, "H", 39, "2026-01-13"),
            ),
        ]

        self.assertEqual(bear_to_bull_highs([{"id": "invalid-breaks", "points": invalid_lows}], trend_level=2), [])

    def test_non_bull_flip_and_malformed_confirmation_are_ignored(self):
        strokes = [
            {
                "id": "invalid",
                "points": [
                    point(1, "H", 30, "2026-01-03", flip="翻多为空", confirmed_by=point(2, "L", 20, "2026-01-04")),
                    point(3, "L", 18, "2026-01-05", flip="翻空为多", confirmed_by=point(4, "L", 19, "2026-01-06")),
                ],
            }
        ]

        self.assertEqual(bear_to_bull_highs(strokes, trend_level=2), [])


if __name__ == "__main__":
    unittest.main()
