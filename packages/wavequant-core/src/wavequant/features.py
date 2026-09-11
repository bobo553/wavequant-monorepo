from __future__ import annotations

from collections import defaultdict, deque
from statistics import median

from .config import StrategyConfig
from .model import Bar, BarFeature


def compute_features(bars: list[Bar], config: StrategyConfig) -> list[BarFeature]:
    """Compute causal features; rolling levels never include the current bar."""

    config.validate()
    if not bars:
        return []

    features: list[BarFeature] = []
    true_ranges: deque[float] = deque(maxlen=config.atr_period)
    slot_volumes: dict[object, deque[float]] = defaultdict(
        lambda: deque(maxlen=config.rvol_lookback)
    )
    closes: list[float] = []

    for index, bar in enumerate(bars):
        prior = bars[max(0, index - config.breakout_lookback):index]
        previous_high = max(item.high for item in prior) if len(prior) == config.breakout_lookback else None
        previous_low = min(item.low for item in prior) if len(prior) == config.breakout_lookback else None

        previous_close = bars[index - 1].close if index else bar.close
        true_range = max(
            bar.high - bar.low,
            abs(bar.high - previous_close),
            abs(bar.low - previous_close),
        )
        true_ranges.append(true_range)
        atr = sum(true_ranges) / len(true_ranges) if len(true_ranges) == config.atr_period else None

        slot = bar.timestamp.timetz().replace(tzinfo=None)
        history = slot_volumes[slot]
        rvol = None
        if len(history) >= config.rvol_lookback:
            baseline = median(history)
            rvol = bar.volume / baseline if baseline > 0 else None
        history.append(bar.volume)

        price_range = bar.high - bar.low
        close_location = (bar.close - bar.low) / price_range if price_range > 0 else 0.5

        closes.append(bar.close)
        efficiency = None
        move_in_atr = None
        regime = "unknown"
        if index >= config.regime_lookback and atr and atr > 0:
            start = closes[index - config.regime_lookback]
            move = bar.close - start
            path = sum(
                abs(closes[position] - closes[position - 1])
                for position in range(index - config.regime_lookback + 1, index + 1)
            )
            efficiency = abs(move) / path if path > 0 else 0.0
            move_in_atr = move / atr
            regime = _classify_regime(
                move_in_atr,
                efficiency,
                config.regime_min_efficiency,
                config.regime_strong_move_atr,
            )

        features.append(BarFeature(
            bar=bar,
            index=index,
            previous_high=previous_high,
            previous_low=previous_low,
            atr=atr,
            rvol=rvol,
            close_location=close_location,
            efficiency=efficiency,
            move_in_atr=move_in_atr,
            regime=regime,
        ))
    return features


def _classify_regime(
    move_in_atr: float,
    efficiency: float,
    min_efficiency: float,
    strong_move_atr: float,
) -> str:
    if efficiency < min_efficiency:
        return "range"
    if move_in_atr >= strong_move_atr:
        return "squeeze_up"
    if move_in_atr > 0:
        return "drift_up"
    if move_in_atr <= -strong_move_atr:
        return "chase_down"
    return "drift_down"
