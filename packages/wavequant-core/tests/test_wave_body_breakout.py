from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path
import pytest
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.integrated_strategy import generate_system_signals, pivot_history
from wavequant.domain.strategies.wave_continuation import wave_gap_entry
from wavequant.domain.market_structure.wave_projection import WaveProjectionSetup
from tests.test_completed_wave_recovery import config


@pytest.fixture(scope="module")
def sample():
    raw = json.loads((Path(__file__).parent / "fixtures/shilian_2026_wave.json").read_text())
    bars = [Bar(datetime.fromisoformat(day), raw["symbol"], *values) for day, *values in raw["bars"]]
    now = next(i for i, b in enumerate(bars) if str(b.timestamp.date()) == "2026-09-16")
    result = generate_system_signals(bars, config())
    return bars, now, result


def test_real_non_gap_body_breakout_and_prefix(sample):
    bars, now, result = sample
    signal = next(s for s in result.signals if s.bar_index == now and s.side == "LONG")
    assert signal.reason == "system_wave_push_gap"
    proof = next(e for e in result.audit if e["bar_index"] == now and e["event"] == "long_signal")
    assert proof["wave_gap_trigger"] == "volume_body_breakout"
    assert proof["wave_confirmation_phase"] == "body"
    assert proof["wave_b_low_date"] == "2026-09-16"
    assert proof["wave_breakout_date"] == "2026-09-14"
    gap = next(s for s in result.signals if s.side == "LONG" and s.bar_index == now + 1)
    assert gap.reason == "system_wave_push_gap"
    gap_proof = next(e for e in result.audit if e["bar_index"] == now + 1 and e["event"] == "long_signal")
    assert gap_proof["wave_confirmation_phase"] == "gap"
    assert (gap_proof["attack"], gap_proof["wave_a_high_index"], gap_proof["wave_b_low_index"]) == (
        proof["attack"],
        proof["wave_a_high_index"],
        proof["wave_b_low_index"],
    )
    assert gap_proof["wave_gap_high"] > proof["wave_gap_high"]
    prefix = generate_system_signals(bars[: now + 1], config())
    assert prefix.signals == [s for s in result.signals if s.bar_index <= now]


@pytest.mark.parametrize("case", ["equal_volume", "weak_body", "wick_only", "broken_defense", "future_pivot"])
def test_non_gap_requires_all_conditions(sample, case):
    bars, now, result = sample
    bars = list(bars)
    row = next(
        e
        for e in result.audit
        if e["event"] == "wave_continuation_ready" and str(bars[e["attack_index"]].timestamp.date()) == "2026-07-28"
    )
    setup = WaveProjectionSetup(**{k: row[k] for k in WaveProjectionSetup.__dataclass_fields__})
    pivots = pivot_history(bars, config())[0][now - 1]
    assert wave_gap_entry(bars, setup, now, pivots=pivots)
    if case == "equal_volume":
        bars[now] = replace(bars[now], volume=bars[now - 1].volume)
    elif case == "weak_body":
        bars[now] = replace(bars[now], open=bars[now].close - 0.01)
    elif case == "wick_only":
        bars[now] = replace(bars[now], close=2.34)
    elif case == "broken_defense":
        bars[now] = replace(bars[now], low=setup.defense - 0.01)
    else:
        pivots = [replace(p, confirmed_index=now) for p in pivots]
    assert wave_gap_entry(bars, setup, now, pivots=pivots) is None


@pytest.mark.parametrize("late_failure", [False, True])
def test_non_gap_minute_waits_for_actual_volume_and_uses_observed_b(sample, late_failure):
    from wavequant.application.analytics.intraday_entry import resolve_consolidation_entries
    from wavequant.infrastructure.market_data.minute import MinuteBar
    from wavequant.infrastructure.market_data.akshare_history import MinuteCoverageError

    bars, now, result = sample
    bars = bars[: now + 1]
    result = replace(
        result,
        signals=[s for s in result.signals if s.bar_index <= now],
        audit=[e for e in result.audit if e["bar_index"] <= now],
    )
    b = bars[-1]
    minute = [
        MinuteBar(b.timestamp.replace(hour=9, minute=35), b.open, 2.40, b.low, 2.40, bars[-2].volume),
        MinuteBar(b.timestamp.replace(hour=9, minute=40), 2.40, 2.41, 2.39, 2.40, 1),
        MinuteBar(b.timestamp.replace(hour=9, minute=45), 2.40, 2.45, 2.39, 2.45, 1000),
    ]
    if late_failure:
        bars[-1] = replace(bars[-1], low=2.0, close=2.0)
        minute[-1] = replace(minute[-1], low=2.0, close=2.0)

    def load(bar):
        if bar.timestamp == b.timestamp:
            return minute
        raise MinuteCoverageError(str(bar.timestamp.date()), None, None, "fixture")

    resolved, executions, _ = resolve_consolidation_entries(bars, result, config(), load, daily_fallback=True)
    entry = executions[b.symbol, now]
    assert entry["timing"]["decision_timestamp"].endswith("09:40:00")
    assert entry["timing"]["observed_volume"] == bars[-2].volume + 1
    assert entry["price"] == 2.40
    proof = next(e for e in resolved.audit if e["event"] == "long_signal" and e["bar_index"] == now)
    assert proof["wave_b_low_date"] == "2026-09-16"
    assert proof["wave_breakout_close"] == 2.40
