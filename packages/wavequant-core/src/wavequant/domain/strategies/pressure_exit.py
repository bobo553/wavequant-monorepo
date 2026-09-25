"""Causal exits at unbroken supply candles and resisted former record highs."""

from statistics import mean

from ..market_structure.price_action import Direction, ShadowPolicy, observe_resistance
from ..models.config import StrategyConfig
from ..models.model import Bar


def _pressure_evidence(bars: list[Bar], source: int) -> dict:
    supply = bars[source]
    return dict(
        pressure_date=supply.timestamp.date().isoformat(),
        pressure_low=(supply.open + supply.close) / 2,
        pressure_high=supply.high,
        pressure_volume=supply.volume,
        pressure_volume_multiple=supply.volume / mean(b.volume for b in bars[source - 20 : source]),
    )


def record_high_resistance_history(bars: list[Bar], config: StrategyConfig) -> dict[int, dict]:
    """Reduce on a resisted intraday break of an old 120-session record high.

    The source must precede the attack by at least 20 sessions. Both the prior
    record and resistance are known at the attack close, without swing labels.
    """
    risks: dict[int, dict] = {}
    for index in range(20, len(bars)):
        prior = bars[index - 1]
        bar = bars[index]
        start = max(0, index - config.pressure_lookback)
        source = max(range(start, index), key=lambda j: (bars[j].high, -j))
        record = bars[source]
        if (
            index - source < 20
            or record.close <= record.open
            or not prior.close <= record.high < bar.high
            or bar.close > record.high
        ):
            continue
        if (
            source == 0
            or observe_resistance(
                bars[source - 1], record, attack_direction=Direction.UP, shadow_policy=ShadowPolicy(0.5)
            ).detected
            is True
        ):
            continue
        resistance = observe_resistance(prior, bar, attack_direction=Direction.UP, shadow_policy=ShadowPolicy(0.5))
        if resistance.detected is not True:
            continue
        span = bar.high - bar.low
        upper = bar.high - max(bar.open, bar.close)
        adverse = []
        if bar.close < bar.open:
            adverse.append("bearish_body")
        if bar.close < prior.close:
            adverse.append("close_below_previous")
        if bar.low < prior.low:
            adverse.append("low_below_previous")
        if span > 0 and upper / span >= 0.5:
            adverse.append("long_upper_shadow")
        if not adverse:
            continue
        risks[index] = dict(
            reason="record_high_resistance_reduce",
            exit_fraction=0.5,
            execution_model="same_day_close",
            record_high_date=record.timestamp.date().isoformat(),
            record_high=record.high,
            record_high_age=index - source,
            record_breakout_date=bar.timestamp.date().isoformat(),
            record_breakout_index=index,
            record_resistance_patterns=list(resistance.reasons),
            record_adverse_patterns=adverse,
            record_upper_shadow_fraction=upper / span if span else 0,
            observed_open=bar.open,
            observed_close=bar.close,
            observed_low=bar.low,
            previous_close=prior.close,
            previous_high=prior.high,
            previous_low=prior.low,
        )
    return risks


def pressure_exit_history(bars: list[Bar], n_context: list[int | None], config: StrategyConfig) -> dict[int, dict]:
    """Only completed bars qualify supply; no future swing confirmation is used.

    A supply candle is a 20-session high with a bearish body >= 5% of open
    and half its range, on >= 2 times its preceding 20-session mean volume.
    Defaults are explicit strategy parameters, not statistically fitted values.
    Its upper half-body through high is the pressure zone. A close above that
    high invalidates the retest rule. A resisted breakout after a rise of at
    least 20% from the post-supply low awaits its first later adverse candle.
    """
    candidates: list[int] = []
    armed: tuple[int, int] | None = None
    breakout: tuple[int, int, int] | None = None
    result: dict[int, dict] = {}
    for index, bar in enumerate(bars):
        if breakout is not None:
            source, attack, rally_low = breakout
            if index > attack:
                prior = bars[index - 1]
                span = bar.high - bar.low
                upper = bar.high - max(bar.open, bar.close)
                adverse = []
                if bar.close < bar.open:
                    adverse.append("bearish_body")
                if bar.close < prior.close:
                    adverse.append("close_below_previous")
                if bar.low < prior.low:
                    adverse.append("low_below_previous")
                if span > 0 and upper / span >= 0.5:
                    adverse.append("long_upper_shadow")
                if adverse:
                    result[index] = dict(
                        reason="pressure_breakout_adverse_clear",
                        exit_fraction=1.0,
                        execution_model="same_day_close",
                        **_pressure_evidence(bars, source),
                        pressure_breakout_date=bars[attack].timestamp.date().isoformat(),
                        pressure_breakout_index=attack,
                        pressure_breakout_high=bars[attack].high,
                        pressure_rally_low_date=bars[rally_low].timestamp.date().isoformat(),
                        pressure_rally_low=bars[rally_low].low,
                        pressure_rally_fraction=bars[attack].high / bars[rally_low].low - 1,
                        pressure_adverse_patterns=adverse,
                        observed_open=bar.open,
                        observed_close=bar.close,
                        observed_low=bar.low,
                        previous_close=prior.close,
                        previous_high=prior.high,
                        previous_low=prior.low,
                    )
                    breakout = None
        if index > 0 and breakout is None:
            prior = bars[index - 1]
            resisted = (
                observe_resistance(prior, bar, attack_direction=Direction.UP, shadow_policy=ShadowPolicy(0.25)).detected
                is True
            )
            crossing = [
                source
                for source in candidates
                if source < index - 1
                and prior.close <= bars[source].high < bar.high
                and index - source <= config.pressure_lookback
            ]
            if resisted and crossing:
                qualified = []
                for source in crossing:
                    rally_low = min(range(source + 1, index), key=lambda j: (bars[j].low, j))
                    if bar.high / bars[rally_low].low >= 1.2:
                        qualified.append((source, rally_low))
                if qualified:
                    source, rally_low = min(qualified, key=lambda pair: (bars[pair[0]].high, -pair[0]))
                    breakout = source, index, rally_low
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
                gap_unfilled = bar.low > prior.high and bar.close > prior.close
                result[index] = dict(
                    reason="pressure_gap_adverse_reduce" if gap_unfilled else "pressure_adverse_clear",
                    exit_fraction=0.5 if gap_unfilled else 1.0,
                    execution_model="same_day_close",
                    **_pressure_evidence(bars, source),
                    pressure_n_date=bars[attack].timestamp.date().isoformat(),
                    pressure_adverse_patterns=reasons,
                    pressure_upper_shadow_fraction=upper / span if span else 0,
                    pressure_gap_unfilled=gap_unfilled,
                    observed_close=bar.close,
                    observed_low=bar.low,
                    previous_close=prior.close,
                    previous_high=prior.high,
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
