from dataclasses import replace
from datetime import datetime, timedelta
import random
import tempfile
from pathlib import Path
import unittest

from wavequant.model import Bar
from wavequant.config import StrategyConfig
from wavequant.backtest import run_portfolio
from wavequant.integrated_strategy import SystemStrategy, generate_system_signals, pivot_history
from wavequant.system_research import run_system_research


def fixture():
    rows = [(10, 11, 9, 10), (11, 12, 10, 11), (10, 11, 8, 9), (9, 10, 7, 8),
            (12, 13, 11.5, 12.5), (11, 12, 10, 11), (12, 13, 11, 12.8),
            (13, 14, 12, 14), (14, 15, 13, 15), (15, 16, 14, 16),
            (16, 16.5, 15, 16), (16.2, 17, 16, 17), (17, 18, 16.5, 17.5)]
    return [Bar(datetime(2026, 1, 1)+timedelta(days=i), 'TEST', *r, 2000000 if i == 7 else 1000000)
            for i, r in enumerate(rows)]


def config(**kw):
    return replace(SystemStrategy(volume_lookback=3, minimum_reward_risk=0,
                                  entry_policy='legacy_n_continuation', preflight_reward_risk=False,
                                  squeeze_pullback_entries=False), **kw)


class IntegratedStrategyTests(unittest.TestCase):
    def test_strict_end_to_end_n_regime_long(self):
        r = generate_system_signals(fixture(), config())
        longs = [s for s in r.signals if s.side == 'LONG']
        self.assertEqual(len(longs), 1)
        self.assertEqual(longs[0].bar_index, 9)
        self.assertEqual(longs[0].trigger_timestamp, fixture()[7].timestamp)
        self.assertEqual(longs[0].invalidation_price, 12)
        self.assertGreater(longs[0].target_price, 16)
        self.assertTrue(any(e['event'] == 'regime_confirmation' for e in r.audit))

    def test_actual_execution_next_open_and_time_exit(self):
        bars = fixture()
        signals = generate_system_signals(bars, config()).signals
        r = run_portfolio({'TEST': bars}, signals, replace(StrategyConfig(), max_hold_bars=1))
        self.assertEqual(r.metrics['trades'], 1)
        self.assertEqual(r.trades[0].entry_time, bars[10].timestamp)
        self.assertEqual(r.trades[0].exit_time, bars[12].timestamp)
        self.assertGreater(r.metrics['fees'], 0)

    def test_volume_ablation_is_explicit(self):
        bars = [replace(b, volume=1000000) for b in fixture()]
        self.assertFalse(any(s.side == 'LONG' for s in generate_system_signals(bars, config()).signals))
        self.assertTrue(any(s.side == 'LONG' for s in generate_system_signals(bars, config(volume_filter=False)).signals))

    def test_regime_ablation_changes_confirmation_timing(self):
        r = generate_system_signals(fixture(), config(regime_filter=False))
        self.assertEqual(next(s.bar_index for s in r.signals if s.side == 'LONG'), 7)

    def test_strict_ambiguity_stops_episode_and_exits(self):
        bars = fixture()
        bars[8] = replace(bars[8], open=13, high=13.8, low=12.5, close=13.5)
        r = generate_system_signals(bars, config())
        self.assertTrue(any(s.bar_index == 8 and s.side == 'EXIT' for s in r.signals))
        self.assertFalse(any(s.side == 'LONG' for s in r.signals))

    def test_confirmed_fractal_has_right_side_delay(self):
        bars = fixture()
        snapshots, _, _, _ = pivot_history(bars, config(pivot_mode='confirmed_fractal_proxy'))
        for i, points in snapshots.items():
            self.assertTrue(all(p.point.index+2 == p.confirmed_index <= i for p in points))

    def test_prefix_invariance_synthetic_both_modes(self):
        for mode in ('strict_polyline', 'confirmed_fractal_proxy'):
            bars = fixture()
            full = generate_system_signals(bars, config(pivot_mode=mode))
            for i in range(len(bars)):
                short = generate_system_signals(bars[:i+1], config(pivot_mode=mode))
                self.assertEqual(short.signals, [s for s in full.signals if s.bar_index <= i])

    def test_random_prefix_invariance_and_long_only(self):
        rng = random.Random(812)
        bars, previous = [], 50
        for i in range(140):
            close = max(5, previous+rng.uniform(-4, 4))
            bar = Bar(datetime(2020, 1, 1)+timedelta(days=i), 'TEST', previous,
                      max(previous, close)+rng.uniform(.1, 2), min(previous, close)-rng.uniform(.1, 2), close, 1000000)
            bars.append(bar)
            previous = close
        for mode in ('strict_polyline', 'confirmed_fractal_proxy'):
            c = config(pivot_mode=mode, volume_filter=False)
            full = generate_system_signals(bars, c)
            self.assertTrue(all(s.side in ('LONG', 'EXIT') for s in full.signals))
            for end in (25, 49, 79, 109, 129):
                short = generate_system_signals(bars[:end+1], c)
                self.assertEqual(short.signals, [s for s in full.signals if s.bar_index <= end])

    def test_insufficient_history_and_bad_inputs(self):
        self.assertEqual(generate_system_signals([], config()).signals, [])
        self.assertEqual(generate_system_signals(fixture()[:3], config()).counts.get('long_signals'), 0)
        with self.assertRaises(ValueError):
            generate_system_signals(fixture(), config(pivot_mode='guess_intrabar'))
        with self.assertRaises(ValueError):
            generate_system_signals(fixture(), config(max_counter_ratio=1))

    def test_research_requires_tests_and_preserves_reports(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            with self.assertRaises(ValueError):
                run_system_research(Path('missing.csv'), out, Path('missing.json'), {'status': 'failed'})
            (out/'report.json').touch()
            with self.assertRaises(ValueError):
                run_system_research(Path('missing.csv'), out, Path('missing.json'), {'status': 'passed'})


if __name__ == '__main__':
    unittest.main()
