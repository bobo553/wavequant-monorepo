import unittest

from wavequant.domain.market_structure.trend_landmarks import bear_to_bull_highs


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
