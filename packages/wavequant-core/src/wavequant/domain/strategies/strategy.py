"""Baseline signal rules used as controls for the integrated WaveQuant strategy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..models.config import StrategyConfig
from ..models.model import BarFeature, Signal


@dataclass
class _Pending:
    direction: str
    trigger_index: int
    trigger_high: float
    trigger_low: float
    breakout_level: float
    impulse_size: float
    extreme: float
    counter_extreme: float
    max_counter_move: float
    trigger_timestamp: datetime
    trigger_rvol: float | None
    trigger_regime: str


def generate_signals(features: list[BarFeature], config: StrategyConfig) -> list[Signal]:
    """Generate causal confirmation signals for both bullish and bearish setups.

    The breakout bar creates a pending event.  A signal only appears on a later
    closed bar after shallow counter-movement and renewed price progress.
    """

    config.validate()
    signals: list[Signal] = []
    bullish: _Pending | None = None
    bearish: _Pending | None = None
    buffer = config.breakout_buffer_bps / 10_000.0

    for feature in features:
        bar = feature.bar

        if bullish is not None:
            prior_peak = bullish.extreme
            # Use the peak known before this bar when measuring its pullback.
            # ``max(all highs) - min(all lows)`` is wrong: after a successful
            # renewal it would reinterpret the old pullback as deeper.
            current_pullback = max(0.0, bullish.extreme - bar.low) / bullish.impulse_size
            bullish.max_counter_move = max(bullish.max_counter_move, current_pullback)
            bullish.counter_extreme = min(bullish.counter_extreme, bar.low)
            bullish.extreme = max(bullish.extreme, bar.high)
            age = feature.index - bullish.trigger_index
            retracement = bullish.max_counter_move
            expired = age > config.confirm_window or retracement > config.max_retracement
            invalid = bullish.counter_extreme <= bullish.breakout_level
            confirmed = (
                age >= config.min_confirm_bars
                and bar.close > prior_peak * (1.0 + buffer)
                and not expired
                and not invalid
            )
            if confirmed:
                atr_buffer = (feature.atr or 0.0) * config.stop_atr_buffer
                signals.append(Signal(
                    timestamp=bar.timestamp,
                    symbol=bar.symbol,
                    bar_index=feature.index,
                    side="LONG",
                    reference_price=bar.close,
                    invalidation_price=bullish.counter_extreme - atr_buffer,
                    reason="breakout_then_shallow_retrace_then_new_high",
                    trigger_timestamp=bullish.trigger_timestamp,
                    retracement=retracement,
                    rvol=bullish.trigger_rvol,
                    regime=bullish.trigger_regime,
                ))
                bullish = None
            elif expired or invalid:
                bullish = None

        if bearish is not None:
            prior_trough = bearish.extreme
            current_rebound = max(0.0, bar.high - bearish.extreme) / bearish.impulse_size
            bearish.max_counter_move = max(bearish.max_counter_move, current_rebound)
            bearish.counter_extreme = max(bearish.counter_extreme, bar.high)
            bearish.extreme = min(bearish.extreme, bar.low)
            age = feature.index - bearish.trigger_index
            rebound = bearish.max_counter_move
            expired = age > config.confirm_window or rebound > config.max_retracement
            invalid = bearish.counter_extreme >= bearish.breakout_level
            confirmed = (
                age >= config.min_confirm_bars
                and bar.close < prior_trough * (1.0 - buffer)
                and not expired
                and not invalid
            )
            if confirmed:
                atr_buffer = (feature.atr or 0.0) * config.stop_atr_buffer
                signals.append(Signal(
                    timestamp=bar.timestamp,
                    symbol=bar.symbol,
                    bar_index=feature.index,
                    side="EXIT",
                    reference_price=bar.close,
                    invalidation_price=bearish.counter_extreme + atr_buffer,
                    reason="breakdown_then_weak_rebound_then_new_low",
                    trigger_timestamp=bearish.trigger_timestamp,
                    retracement=rebound,
                    rvol=bearish.trigger_rvol,
                    regime=bearish.trigger_regime,
                ))
                bearish = None
            elif expired or invalid:
                bearish = None

        enough_history = (
            feature.previous_high is not None
            and feature.previous_low is not None
            and feature.atr is not None
            and feature.rvol is not None
            and feature.index >= config.impulse_lookback
            and feature.index >= config.regime_lookback
        )
        if not enough_history:
            continue

        recent_start = max(0, feature.index - config.impulse_lookback)
        recent = features[recent_start:feature.index]

        non_bearish = feature.regime in {"range", "drift_up", "squeeze_up"}
        can_start_long = not config.require_non_bearish_regime or non_bearish
        if bullish is None and can_start_long:
            long_trigger = (
                bar.close > feature.previous_high * (1.0 + buffer)
                and feature.close_location >= config.min_close_location
                and feature.rvol >= config.min_rvol
            )
            if long_trigger:
                anchor = min((item.bar.low for item in recent), default=feature.previous_low)
                impulse = bar.close - anchor
                if impulse > 0:
                    bullish = _Pending(
                        direction="LONG",
                        trigger_index=feature.index,
                        trigger_high=bar.high,
                        trigger_low=bar.low,
                        breakout_level=anchor,
                        impulse_size=impulse,
                        extreme=bar.high,
                        counter_extreme=bar.low,
                        max_counter_move=0.0,
                        trigger_timestamp=bar.timestamp,
                        trigger_rvol=feature.rvol,
                        trigger_regime=feature.regime,
                    )

        if bearish is None:
            short_trigger = (
                bar.close < feature.previous_low * (1.0 - buffer)
                and feature.close_location <= 1.0 - config.min_close_location
                and feature.rvol >= config.min_rvol
            )
            if short_trigger:
                anchor = max((item.bar.high for item in recent), default=feature.previous_high)
                impulse = anchor - bar.close
                if impulse > 0:
                    bearish = _Pending(
                        direction="EXIT",
                        trigger_index=feature.index,
                        trigger_high=bar.high,
                        trigger_low=bar.low,
                        breakout_level=anchor,
                        impulse_size=impulse,
                        extreme=bar.low,
                        counter_extreme=bar.high,
                        max_counter_move=0.0,
                        trigger_timestamp=bar.timestamp,
                        trigger_rvol=feature.rvol,
                        trigger_regime=feature.regime,
                    )

    return signals


def breakout_signals(features: list[BarFeature], config: StrategyConfig) -> list[Signal]:
    """Plain breakout control: same warm-up and risk engine, no confirmation/volume/regime filter."""
    signals = []
    warmup = max(config.breakout_lookback, config.impulse_lookback, config.regime_lookback)
    for feature in features:
        if feature.index < warmup or feature.atr is None or feature.rvol is None:
            continue
        if feature.previous_high is None or feature.previous_low is None:
            continue
        b = feature.bar
        if b.close > feature.previous_high * (1 + config.breakout_buffer_bps / 10000):
            signals.append(Signal(b.timestamp, b.symbol, feature.index, "LONG", b.close,
                                  b.low - feature.atr * config.stop_atr_buffer,
                                  "plain_breakout", b.timestamp, 0.0, feature.rvol, feature.regime))
        elif b.close < feature.previous_low * (1 - config.breakout_buffer_bps / 10000):
            signals.append(Signal(b.timestamp, b.symbol, feature.index, "EXIT", b.close,
                                  b.high + feature.atr * config.stop_atr_buffer,
                                  "plain_breakdown", b.timestamp, 0.0, feature.rvol, feature.regime))
    return signals
