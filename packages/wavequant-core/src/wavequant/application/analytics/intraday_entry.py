"""Replay defended-N gap entries using only completed same-source minute bars."""

from dataclasses import replace
from datetime import timedelta
from typing import Callable, TypedDict

from wavequant.domain.models.model import Bar, Signal
from wavequant.domain.strategies.integrated_strategy import SystemResult, SystemStrategy, generate_system_signals
from wavequant.domain.strategies.n_consolidation import consolidation_gap
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
    for i, bar in enumerate(bars):
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
        if not eligible:
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
                    purpose="consolidation_entry",
                    execution_model="same_day_close",
                )
            )
            continue
        high, low, volume = 0.0, float("inf"), 0.0
        for offset, observed in enumerate(minute[:-1]):
            high = max(high, observed.high * bar.adjustment_factor)
            low = min(low, observed.low * bar.adjustment_factor)
            volume += observed.volume
            partial = replace(bar, high=high, low=low, close=observed.close * bar.adjustment_factor, volume=volume)
            prefix = bars[:i] + [partial]
            if not any(consolidation_gap(prefix, attack=e["bar_index"], now=i, defense=e["defense"]) for e in eligible):
                continue
            replay = generate_system_signals(prefix, strategy)
            proof = next(
                (
                    e
                    for e in replay.audit
                    if e["bar_index"] == i
                    and e["event"] == "long_signal"
                    and e.get("squeeze_confirmation") == "defended_n_consolidation_gap"
                ),
                None,
            )
            if proof is None:
                continue
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
                dict(e, **timing)
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
