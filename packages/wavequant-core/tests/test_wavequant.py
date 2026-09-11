from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from wavequant.backtest import run_backtest
from wavequant.config import StrategyConfig
from wavequant.features import compute_features
from wavequant.model import Bar, Signal
from wavequant.strategy import generate_signals


def bar(day: int, close: float, volume: float = 100.0, *, hour: int = 15) -> Bar:
    timestamp = datetime(2025, 1, 1, hour) + timedelta(days=day)
    return Bar(
        timestamp=timestamp,
        symbol="TEST",
        open=close - 0.05,
        high=close + 0.10,
        low=close - 0.15,
        close=close,
        volume=volume,
    )


class FeatureTests(unittest.TestCase):
    def test_previous_high_is_causal(self) -> None:
        config = StrategyConfig(
            breakout_lookback=3, impulse_lookback=3, atr_period=2,
            rvol_lookback=2, regime_lookback=2,
        )
        bars = [bar(0, 10.0), bar(1, 10.2), bar(2, 11.0), bar(3, 12.0)]
        features = compute_features(bars, config)
        self.assertIsNone(features[2].previous_high)
        self.assertAlmostEqual(features[3].previous_high or 0.0, 11.1)
        self.assertLess(features[3].previous_high or 0.0, bars[3].high)

    def test_rvol_uses_only_prior_same_slot(self) -> None:
        config = StrategyConfig(
            breakout_lookback=2, impulse_lookback=2, atr_period=2,
            rvol_lookback=2, regime_lookback=2,
        )
        bars = [bar(0, 10.0, 100), bar(1, 10.1, 100), bar(2, 10.2, 200)]
        feature = compute_features(bars, config)[2]
        self.assertAlmostEqual(feature.rvol or 0.0, 2.0)


class SignalTests(unittest.TestCase):
    def _config(self, **overrides: object) -> StrategyConfig:
        values = dict(
            breakout_lookback=5,
            impulse_lookback=5,
            atr_period=3,
            rvol_lookback=5,
            regime_lookback=5,
            confirm_window=3,
            min_rvol=1.2,
            min_close_location=0.60,
            max_retracement=0.50,
            require_non_bearish_regime=False,
        )
        values.update(overrides)
        return StrategyConfig(**values)

    def test_shallow_retrace_confirms_only_on_later_bar(self) -> None:
        closes = [10.0, 10.1, 10.0, 10.15, 10.1, 10.8, 10.72, 11.05]
        volumes = [100, 100, 100, 100, 100, 180, 100, 120]
        bars = [bar(i, price, volumes[i]) for i, price in enumerate(closes)]
        signals = generate_signals(compute_features(bars, self._config()), self._config())
        longs = [item for item in signals if item.side == "LONG"]
        self.assertEqual(len(longs), 1)
        self.assertEqual(longs[0].bar_index, 7)
        self.assertEqual(longs[0].trigger_timestamp, bars[5].timestamp)

    def test_deep_retrace_cancels_setup(self) -> None:
        closes = [10.0, 10.1, 10.0, 10.15, 10.1, 10.8, 9.9, 11.05]
        volumes = [100, 100, 100, 100, 100, 180, 100, 120]
        bars = [bar(i, price, volumes[i]) for i, price in enumerate(closes)]
        signals = generate_signals(compute_features(bars, self._config()), self._config())
        self.assertFalse(any(item.side == "LONG" for item in signals))


class BacktestTests(unittest.TestCase):
    def test_signal_executes_next_bar_and_t_plus_one_blocks_same_day_exit(self) -> None:
        day0 = datetime(2025, 1, 2, 10, 0)
        bars = [
            Bar(day0, "TEST", 10.0, 10.2, 9.9, 10.1, 100),
            Bar(day0.replace(hour=11), "TEST", 10.2, 10.4, 10.1, 10.3, 100),
            Bar(day0.replace(hour=14), "TEST", 10.3, 10.4, 9.0, 9.2, 100),
            Bar(day0 + timedelta(days=1), "TEST", 9.1, 9.3, 8.9, 9.0, 100),
            Bar(day0 + timedelta(days=2), "TEST", 9.0, 9.1, 8.8, 8.9, 100),
        ]
        signals = [
            Signal(day0, "TEST", 0, "LONG", 10.1, 9.8, "entry", day0, 0.1, 1.5, "range"),
            Signal(day0.replace(hour=11), "TEST", 1, "EXIT", 10.3, 10.5, "exit", day0, 0.1, 1.5, "range"),
        ]
        config = StrategyConfig(
            breakout_lookback=2, impulse_lookback=2, atr_period=2,
            rvol_lookback=2, regime_lookback=2,
            slippage_bps_per_side=0, commission_bps_per_side=0,
            allow_same_day_exit=False,
            lot_size=1, max_participation=1,
        )
        result = run_backtest(bars, signals, config)
        self.assertEqual(len(result.trades), 1)
        self.assertEqual(result.trades[0].entry_time, bars[1].timestamp)
        self.assertEqual(result.trades[0].exit_time, bars[3].timestamp)
        self.assertAlmostEqual(result.trades[0].exit_price, 9.1)


if __name__ == "__main__":
    unittest.main()
