"""Replay defended-N gap entries using only completed same-source minute bars."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from typing import Callable, TypedDict

from wavequant.domain.models.model import Bar, Signal
from wavequant.domain.strategies.integrated_strategy import (
    SystemResult,
    SystemStrategy,
    generate_system_signals,
    pivot_history,
)
from wavequant.domain.strategies.n_consolidation import consolidation_gap
from wavequant.domain.strategies.wave_continuation import (
    wave_confirmation_is_new,
    wave_confirmation_state,
    wave_gap_entry,
    wave_pullback_context,
)
from wavequant.domain.market_structure.wave_projection import WaveProjectionSetup
from wavequant.domain.market_structure.polyline import PointKind
from wavequant.infrastructure.market_data.akshare_history import MinuteCoverageError
from wavequant.infrastructure.market_data.data import opening_permissions
from wavequant.infrastructure.market_data.minute import MinuteBar


class EntryExecution(TypedDict):
    signal: Signal
    price: float
    buyable: bool
    timing: dict[str, object]
    remaining_low: float
    remaining_high: float


def resolve_consolidation_entries(
    bars: list[Bar],
    generated: SystemResult,
    strategy: SystemStrategy,
    minute_loader: Callable[[Bar], list[MinuteBar]] | None,
    *,
    daily_fallback: bool,
) -> tuple[SystemResult, dict[tuple[str, int], EntryExecution], list[dict[str, object]]]:
    """Keep daily-close candidates when no earlier fully verified minute decision exists."""
    signals = list(generated.signals)
    audit = list(generated.audit)
    executions: dict[tuple[str, int], EntryExecution] = {}
    fallbacks: list[dict[str, object]] = []
    candidates = [
        e for e in audit if e["event"] == "n_completed" and e.get("direction") == "up" and e.get("n_level", 0) >= 1
    ]
    wave_candidates = [
        (
            e["bar_index"],
            WaveProjectionSetup(**{key: e[key] for key in WaveProjectionSetup.__dataclass_fields__}),
            e.get("wave_epoch"),
        )
        for e in audit
        if e["event"] == "wave_continuation_ready"
    ]
    snapshots, epochs = pivot_history(bars, strategy)[:2] if wave_candidates else ({}, {})
    daily_waves = {e["bar_index"]: e for e in audit if e["event"] == "long_signal" and e.get("wave_entry_path")}
    consumed_waves: dict[tuple[int, int, int], tuple[str, float]] = {}
    # Every minute prefix takes all earlier bars from this fixed full-history
    # input. The strategy may therefore reuse observations ending before the
    # current session across later sessions as well as later minutes.
    chart_history_cache: dict = {"source_bars": tuple(bars)}
    for i, bar in enumerate(bars):
        preceding_daily = daily_waves.get(i - 1)
        if preceding_daily is not None:
            key = (preceding_daily["attack"], preceding_daily["wave_a_high_index"], preceding_daily["wave_b_low_index"])
            if wave_confirmation_is_new(preceding_daily, consumed_waves.get(key)):
                consumed_waves[key] = wave_confirmation_state(preceding_daily)
        current_daily = daily_waves.get(i)
        current_key = (
            (
                (
                    int(current_daily["attack"]),
                    int(current_daily["wave_a_high_index"]),
                    int(current_daily["wave_b_low_index"]),
                )
            )
            if current_daily is not None
            else None
        )
        if (
            current_daily is not None
            and current_key is not None
            and not wave_confirmation_is_new(current_daily, consumed_waves.get(current_key))
        ):
            # A prior minute body may be followed by one higher gap, while the
            # same phase remains owned by its first available confirmation.
            signals = [s for s in signals if not (s.bar_index == i and s.reason == "system_wave_push_gap")]
            audit = [
                e
                for e in audit
                if not (
                    e["bar_index"] == i
                    and e["event"] in ("long_signal", "long_transition_evidence")
                    and e.get("attack") == current_daily["attack"]
                )
            ]
        # Eligibility uses prior sessions and today's open, never today's final range/volume.
        eligible = [
            e
            for e in candidates
            if max(e["bar_index"], e.get("known_at", e["bar_index"])) < i
            and e["bar_index"] < i - 2
            and bar.open > bars[i - 1].high
            and min(b.low for b in bars[e["bar_index"] + 1 : i]) >= e["defense"]
            and bars[i - 1].close <= bars[e["bar_index"]].high
            and bars[i - 2].close <= bars[e["bar_index"]].high
        ]
        wave_eligible = []
        for known, setup, epoch in wave_candidates:
            # Match the global strategy's structural episode boundary, using
            # yesterday only: today's final candle must not invalidate a minute entry.
            epoch = epochs.get(setup.attack_index) if epoch is None else epoch
            if known >= i or bar.open < setup.defense or epoch != epochs.get(i - 1):
                continue
            context = wave_pullback_context(bars, setup, i)
            if context is not None and bar.open <= bars[i - 1].high:
                highs = [
                    p
                    for p in snapshots.get(i - 1, ())
                    if p.point.kind == PointKind.HIGH
                    and int(context["wave_a_high_index"]) < p.point.index < i
                    and p.confirmed_index < i
                ]
                resistance = max(highs, key=lambda p: (p.point.index, p.confirmed_index), default=None)
                # Final cumulative volume/high are upper bounds on every prefix.
                # They can prove a trigger impossible, never confirm its timing.
                if (
                    (
                        bar.volume <= bars[i - 1].volume
                        and (context["wave_a_class"] != "ordinary" or strategy.volume_filter)
                    )
                    or bar.high < bar.open * (1.0 if context["wave_a_class"] == "ordinary" else 1.03)
                    or resistance is None
                    or bar.high <= resistance.point.price
                ):
                    continue
            if context is not None:
                key = (setup.attack_index, int(context["wave_a_high_index"]), int(context["wave_b_low_index"]))
                previous = consumed_waves.get(key)
                if previous is None or (
                    previous[0] == "body" and bar.open > bars[i - 1].high and bar.high > previous[1]
                ):
                    wave_eligible.append(setup)
        if not eligible and not wave_eligible:
            continue
        try:
            minute = minute_loader(bar) if minute_loader is not None else None
            if minute is None:
                raise MinuteCoverageError(bar.timestamp.date().isoformat(), None, None, "未配置")
        except MinuteCoverageError as exc:
            if not daily_fallback:
                raise
            fallbacks.append(
                dict(
                    symbol=bar.symbol,
                    date=bar.timestamp.date().isoformat(),
                    reason=exc.coverage.get("reason", "minute_history_missing"),
                    coverage=exc.coverage,
                    purpose="wave_continuation_entry" if wave_eligible else "consolidation_entry",
                    execution_model="same_day_close",
                )
            )
            continue
        high, low, volume = 0.0, float("inf"), 0.0
        attempted: set[tuple[float, float, float, float]] = set()
        for offset, observed in enumerate(minute[:-1]):
            high = max(high, observed.high * bar.adjustment_factor)
            low = min(low, observed.low * bar.adjustment_factor)
            volume += observed.volume
            partial = replace(bar, high=high, low=low, close=observed.close * bar.adjustment_factor, volume=volume)
            prefix = bars[:i] + [partial]
            consolidation_confirmed = any(
                consolidation_gap(prefix, attack=e["bar_index"], now=i, defense=e["defense"]) for e in eligible
            )
            wave_confirmations = [
                wave_observation
                for setup in wave_eligible
                if (wave_observation := wave_gap_entry(prefix, setup, i, pivots=snapshots.get(i - 1, ()))) is not None
            ]
            if not consolidation_confirmed and not wave_confirmations:
                continue
            # Ordinary-A price geometry does not imply the optional volume
            # gate passed. Do not replay years of structure for every under-volume
            # minute. Only a verified strong-A gap price-break may waive volume.
            if (
                strategy.buy_point_definition == "whole_flip_wave_v3"
                and strategy.volume_filter
                and volume <= bars[i - 1].volume
                and not any(
                    p.get("wave_gap_trigger") in ("breakout", "breakout_and_volume") for p in wave_confirmations
                )
            ):
                continue
            volume_state = (
                float(volume > bars[i - 1].volume) if strategy.buy_point_definition == "whole_flip_wave_v3" else volume
            )
            observation = (high, low, partial.close, volume_state)
            if observation in attempted:
                continue
            attempted.add(observation)
            replay = (
                generate_system_signals(prefix, strategy, chart_history_cache=chart_history_cache)
                if strategy.buy_point_definition == "whole_flip_wave_v3"
                else generate_system_signals(prefix, strategy)
            )
            proof = next(
                (
                    e
                    for e in replay.audit
                    if e["bar_index"] == i
                    and e["event"] == "long_signal"
                    and e.get("squeeze_confirmation")
                    in ("defended_n_consolidation_gap", "two_t_wave_push_gap", "one_p_wave_rebound")
                ),
                None,
            )
            if proof is None:
                continue
            wave_key = (
                (proof["attack"], proof["wave_a_high_index"], proof["wave_b_low_index"])
                if proof.get("wave_entry_path")
                else None
            )
            if wave_key is not None and not wave_confirmation_is_new(proof, consumed_waves.get(wave_key)):
                continue
            if wave_key is not None:
                consumed_waves[wave_key] = wave_confirmation_state(proof)
            signal = next(s for s in replay.signals if s.bar_index == i and s.side == "LONG")
            following = minute[offset + 1]
            execution_at = following.timestamp - timedelta(minutes=5)
            reference = bars[i - 1].close / bar.adjustment_factor
            buyable, _ = opening_permissions(
                dict(
                    date=bar.timestamp.date().isoformat(),
                    isST="0",
                    tradestatus="1" if following.volume > 0 else "0",
                    preclose=str(reference),
                    open=str(following.open),
                ),
                bar.symbol,
            )
            permission_row = dict(
                date=bar.timestamp.date().isoformat(),
                isST="0",
                tradestatus="1" if following.volume > 0 else "0",
                preclose=str(reference),
            )
            below_limit, _ = opening_permissions(
                dict(permission_row, open=str(low / bar.adjustment_factor)), bar.symbol
            )
            previous_tick_buyable, _ = opening_permissions(
                dict(permission_row, open=str(Decimal(str(following.open)) - Decimal("0.01"))), bar.symbol
            )
            timing: dict[str, object] = dict(
                execution_model="intraday_5m_next_open",
                decision_source="completed_five_minute_bar",
                decision_timestamp=observed.timestamp.isoformat(),
                execution_timestamp=execution_at.isoformat(),
                observed_volume=volume,
                previous_volume=bars[i - 1].volume,
                observed_high=high,
                observed_low=low,
                observed_close=partial.close,
                minute_next_open_raw=following.open,
                observed_nonflat_limit_buyable=bool(
                    not buyable and previous_tick_buyable and below_limit and high > low and volume > 0
                ),
            )
            executions[bar.symbol, i] = dict(
                signal=signal,
                price=following.open * bar.adjustment_factor,
                buyable=bool(buyable),
                timing=timing,
                remaining_low=min(m.low for m in minute[offset + 1 :]) * bar.adjustment_factor,
                remaining_high=max(m.high for m in minute[offset + 1 :]) * bar.adjustment_factor,
            )
            signals = [s for s in signals if not (s.bar_index == i and s.side == "LONG")] + [signal]
            audit = [
                e
                for e in audit
                if not (e["bar_index"] == i and e["event"] in ("long_signal", "long_transition_evidence"))
            ]
            audit.extend(
                {**e, **timing}
                for e in replay.audit
                if e["bar_index"] == i and e["event"] in ("long_signal", "long_transition_evidence")
            )
            break
    counts = dict(generated.counts, long_signals=sum(s.side == "LONG" for s in signals))
    return (
        SystemResult(
            sorted(signals, key=lambda s: (s.timestamp, s.side)),
            sorted(audit, key=lambda e: (e["bar_index"], e["event"])),
            counts,
        ),
        executions,
        fallbacks,
    )
