from dataclasses import replace
from datetime import datetime, timedelta
import json
from pathlib import Path

import pytest

from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals
from wavequant.domain.strategies.inverse_reentry import fresh_strong_squeeze_recovery
from wavequant.domain.strategies.strategy_profiles import whole_wave_profile


@pytest.mark.parametrize("invalid", [None, "ordinary", "old_origin", "same_day", "later_inverse", "touch", "future"])
def test_only_fully_new_strong_squeeze_retires_previous_inverse(invalid):
    bars = [Bar(datetime(2026, 1, 1) + timedelta(days=i), "TEST", 12, 16, 11, 15, 1000) for i in range(9)]
    args = dict(
        now=8, attack=6, origin=4, inverse=[dict(known_at=3, attack=3, b_high=19, kill_high=14)], strong_squeeze=True
    )
    if invalid == "ordinary":
        args["strong_squeeze"] = False
    elif invalid == "old_origin":
        args["origin"] = 3
    elif invalid == "same_day":
        args["attack"] = 8
    elif invalid == "later_inverse":
        args["inverse"].append(dict(known_at=7, attack=7, b_high=19, kill_high=14))
    elif invalid == "touch":
        bars[8] = replace(bars[8], close=14)
    elif invalid == "future":
        args["inverse"][0]["known_at"] = 9
    result = fresh_strong_squeeze_recovery(bars, **args)
    assert (result is not None) is (invalid is None)


def test_xianfeng_first_strong_squeeze_enters_august4_with_prefix_consistency():
    raw = json.loads((Path(__file__).parent / "fixtures/xianfeng_2026_strong_squeeze.json").read_text(encoding="utf-8"))
    bars = [Bar(datetime.fromisoformat(day), raw["symbol"], *values) for day, *values in raw["bars"]]
    config = SystemStrategy(
        **(whole_wave_profile({"scenarios": {"base": {"execution": {}}}})["strategy"] | {"volume_filter": False})
    )
    full = generate_system_signals(bars, config)
    signals = [s for s in full.signals if s.side == "LONG" and str(s.timestamp.date()).startswith("2026-08")]
    assert signals[0].timestamp.date().isoformat() == "2026-08-04"
    assert signals[0].trigger_timestamp.date().isoformat() == "2026-07-31"
    assert not any(str(s.timestamp.date()) == "2026-08-07" for s in signals)
    proof = next(
        e for e in full.audit if e["event"] == "long_transition_evidence" and e["timestamp"].startswith("2026-08-04")
    )
    assert proof["inverse_reentry_path"] == "fresh_n_uninterrupted_strong_squeeze"
    assert proof["recovery_previous_b_high"] == 6.4
    for day in ("2026-08-03", "2026-08-04", "2026-08-07"):
        cutoff = next(i for i, b in enumerate(bars) if str(b.timestamp.date()) == day)
        prefix = generate_system_signals(bars[: cutoff + 1], config)
        assert prefix.signals == [s for s in full.signals if s.bar_index <= cutoff]
