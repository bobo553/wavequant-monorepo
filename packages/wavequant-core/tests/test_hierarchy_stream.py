"""Streaming confirmations retain batch knowledge clocks without prefix replays."""

from copy import deepcopy
import unittest
from unittest.mock import patch

from wavequant.domain.market_structure.lecture_drawing import lecture_drawing
from wavequant.domain.market_structure.trend_market_index import _MarketRangeIndex
from wavequant.domain.strategies.hierarchical_entry import hierarchical_history
from wavequant.domain.strategies.hierarchy_stream import HierarchyHistoryStream
from tests.test_lecture_strategy import history


class HierarchyStreamTests(unittest.TestCase):
    def test_each_prefix_matches_independent_confirmations_with_both_n_modes(self):
        bars = history(18, 80)
        frames = []
        lecture_drawing(bars, on_step=lambda index, epoch, raw: frames.append((index, epoch, deepcopy(raw))))
        for enabled in (False, True):
            with self.subTest(n_enabled=enabled):
                stream = HierarchyHistoryStream(bars, n_enabled=enabled)
                saved = None
                for index, epoch, raw in frames:
                    stream.advance(index, epoch, raw)
                    if index == 30:
                        saved = deepcopy(stream.history)
                    if index in (30, 55, len(bars) - 1):
                        declines = {}
                        expected, epochs = hierarchical_history(bars[:index + 1], decline_sink=declines,
                            n_target_trend_confirmation_enabled=enabled)
                        self.assertEqual(stream.history, expected)
                        self.assertEqual(stream.epochs, epochs)
                        self.assertEqual(stream.declines, declines)
                self.assertEqual({index: row for index, row in stream.history.items() if index <= 30}, saved)

    def test_resume_retains_index_and_judgement_state_and_rejects_replay(self):
        bars = history(43, 45)
        frames = []
        lecture_drawing(bars, on_step=lambda index, epoch, raw: frames.append((index, epoch, deepcopy(raw))))
        with patch('wavequant.domain.strategies.hierarchy_stream._MarketRangeIndex.build',
                   wraps=_MarketRangeIndex.build) as build:
            stream = HierarchyHistoryStream(bars)
            for frame in frames[:20]:
                stream.advance(*frame)
            market_index = stream.market_index
            past = deepcopy(stream.history)
            for frame in frames[20:]:
                stream.advance(*frame)
            self.assertIs(stream.market_index, market_index)
            self.assertEqual(build.call_count, 1)
            self.assertEqual({index: row for index, row in stream.history.items() if index < 20}, past)
            with self.assertRaisesRegex(ValueError, 'consecutive'):
                stream.advance(*frames[-1])
            saved_history = deepcopy(stream.history)
            stream.close()
            self.assertEqual(stream._frozen_points.memory_used, 0)
            self.assertEqual(stream.history, saved_history)
            with self.assertRaisesRegex(RuntimeError, 'closed'):
                stream.advance(*frames[-1])
