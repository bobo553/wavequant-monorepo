"""Streaming confirmations retain batch knowledge clocks without prefix replays."""

from copy import deepcopy
import unittest
from unittest.mock import patch

from wavequant.domain.strategies import hierarchy_stream as stream_module
from wavequant.domain.market_structure.lecture_drawing import lecture_drawing
from wavequant.domain.market_structure.trend_market_index import _MarketRangeIndex
from wavequant.domain.market_structure.trend_lines import _TrendJudgementCache
from wavequant.domain.strategies.hierarchical_entry import hierarchical_history
from wavequant.domain.strategies.hierarchy_stream import HierarchyHistoryStream
from tests.test_lecture_strategy import history


class HierarchyStreamTests(unittest.TestCase):
    def test_every_daily_prefix_matches_batch_with_reconstructed_raw_objects(self):
        for enabled in (False, True):
            with self.subTest(n_enabled=enabled):
                bars = history(43, 32)
                frames = []
                lecture_drawing(bars, on_step=lambda index, epoch, raw:
                    frames.append((index, epoch, deepcopy(raw))))
                stream = HierarchyHistoryStream(bars, n_enabled=enabled)
                try:
                    for index, epoch, raw in frames:
                        stream.advance(index, epoch, deepcopy(raw))
                        declines = {}
                        expected, epochs = hierarchical_history(bars[:index + 1], decline_sink=declines,
                            n_target_trend_confirmation_enabled=enabled)
                        self.assertEqual(stream.history, expected, f'prefix {index + 1}')
                        self.assertEqual(stream.epochs, epochs)
                        self.assertEqual(stream.declines, declines)
                finally:
                    stream.close()

    def test_unchanged_structure_skips_recursive_judgements_but_keeps_daily_qualification(self):
        for enabled in (False, True):
            with self.subTest(n_enabled=enabled):
                bars = history(18, 8)
                stream = HierarchyHistoryStream(bars, n_enabled=enabled)
                judged_levels = []
                judge = _TrendJudgementCache.judge

                def capture(cache, source, **options):
                    judged_levels.append(options['trend_level'])
                    return judge(cache, source, **options)

                try:
                    with patch.object(_TrendJudgementCache, 'judge', autospec=True, side_effect=capture), \
                            patch.object(stream_module, 'qualify_trend_structure',
                                         wraps=stream_module.qualify_trend_structure) as qualify, \
                            patch.object(stream, '_freeze', wraps=stream._freeze) as freeze:
                        for index in range(8):
                            # A new path must restart the same source even when both are empty.
                            stream.advance(index, 0 if index < 4 else 4, [])
                        self.assertEqual(judged_levels.count(1), 8)
                        self.assertEqual(judged_levels.count(2), 2)
                        self.assertEqual(judged_levels.count(3), 2)
                        self.assertEqual(qualify.call_count, 24)
                        self.assertEqual([len(call.args[2]) for call in qualify.call_args_list],
                                         [day for day in range(1, 9) for _ in range(3)])
                        self.assertEqual(sum(call.args[1] == 0 for call in freeze.call_args_list), 8)
                        self.assertEqual(sum(call.args[1] != 0 for call in freeze.call_args_list), 6)
                finally:
                    stream.close()
                self.assertEqual(stream._prepared, {})
                self.assertEqual(stream._point_admission.memory_used, 0)

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

    def test_uncertified_market_retains_each_daily_structure_calculation(self):
        with patch.object(stream_module._MarketRangeIndex, 'build', return_value=None):
            stream = HierarchyHistoryStream(history(18, 5))
        try:
            with patch.object(stream_module, 'with_price_bodies', wraps=stream_module.with_price_bodies) as bodies:
                for index in range(5):
                    stream.advance(index, 0, [])
                self.assertEqual(bodies.call_count, 15)
        finally:
            stream.close()

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


    def test_live_chain_passes_current_price_bodies_to_the_shared_judgement(self):
        bars = history(18, 80)
        frames = []
        lecture_drawing(bars, on_step=lambda index, epoch, raw: frames.append((index, epoch, deepcopy(raw))))
        judge = _TrendJudgementCache.judge
        seen = []

        def capture(cache, source, **options):
            for point in source:
                bar = bars[point['index']]
                self.assertEqual(point['price_body'], dict(index=point['index'], time=bar.timestamp.date().isoformat(),
                    open=bar.open, high=bar.high, low=bar.low, close=bar.close))
                seen.append(options['trend_level'])
            return judge(cache, source, **options)

        stream = HierarchyHistoryStream(bars)
        try:
            with patch.object(_TrendJudgementCache, 'judge', autospec=True, side_effect=capture):
                for frame in frames:
                    stream.advance(*frame)
        finally:
            stream.close()
        self.assertIn(1, seen)
