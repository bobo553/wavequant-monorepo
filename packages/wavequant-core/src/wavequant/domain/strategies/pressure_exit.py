"""Causal exits after a positive N encounters an unbroken supply candle."""

from statistics import mean

from ..models.config import StrategyConfig
from ..models.model import Bar


def pressure_exit_history(bars: list[Bar], n_context: list[int | None], config: StrategyConfig) -> dict[int, dict]:
    """Only completed bars qualify supply; no future swing confirmation is used.

    A supply candle is a 20-session high with a bearish body >= 5% of open
    and half its range, on >= 2 times its preceding 20-session mean volume.
    Defaults are explicit strategy parameters, not statistically fitted values.
    Its upper half-body through high is the pressure zone. A close above that
    high permanently invalidates the candle, even if price later falls back.
    """
    candidates: list[int] = []
    armed: tuple[int, int] | None = None
    result: dict[int, dict] = {}
    for index, bar in enumerate(bars):
        candidates = [
            source
            for source in candidates
            if index - source <= config.pressure_lookback and bar.close <= bars[source].high
        ]
        if armed is not None and armed[0] not in candidates:
            armed = None
        attack = n_context[index]
        if armed is not None and attack is not None and attack != armed[1]:
            armed = None
        if armed is None and attack is not None:
            touching = [
                source
                for source in candidates
                if source < attack
                and bar.high >= (bars[source].open + bars[source].close) / 2
                and bar.low <= bars[source].high
            ]
            if touching:
                # Select the closest overhead zone, with newest as the tie-breaker.
                source = min(touching, key=lambda j: ((bars[j].open + bars[j].close) / 2, -j))
                armed = source, attack
        if armed is not None and index > armed[1]:
            source, attack = armed
            prior = bars[index - 1]
            span = bar.high - bar.low
            upper = bar.high - max(bar.open, bar.close)
            reasons = []
            if bar.close < prior.close:
                reasons.append("close_below_previous")
            if bar.low < prior.low:
                reasons.append("low_below_previous")
            if span > 0 and upper / span >= 0.5:
                reasons.append("long_upper_shadow")
            if reasons:
                supply = bars[source]
                result[index] = dict(
                    reason="pressure_adverse_clear",
                    exit_fraction=1.0,
                    execution_model="same_day_close",
                    pressure_date=supply.timestamp.date().isoformat(),
                    pressure_low=(supply.open + supply.close) / 2,
                    pressure_high=supply.high,
                    pressure_volume=supply.volume,
                    pressure_volume_multiple=supply.volume / mean(b.volume for b in bars[source - 20 : source]),
                    pressure_n_date=bars[attack].timestamp.date().isoformat(),
                    pressure_adverse_patterns=reasons,
                    pressure_upper_shadow_fraction=upper / span if span else 0,
                    observed_close=bar.close,
                    observed_low=bar.low,
                    previous_close=prior.close,
                    previous_low=prior.low,
                )
        # The invalidating bar still gets its exit; later N episodes must
        # encounter the zone again rather than inherit a stale armed context.
        if n_context[index] is None:
            armed = None
        if index >= 20:
            preceding = bars[index - 20 : index]
            volume_mean = mean(b.volume for b in preceding)
            body = bar.open - bar.close
            if (
                body >= config.pressure_body_min_fraction * bar.open
                and body >= (bar.high - bar.low) * 0.5
                and bar.high >= max(b.high for b in preceding)
                and volume_mean > 0
                and bar.volume >= config.pressure_volume_ratio * volume_mean
            ):
                candidates.append(index)
    return result
