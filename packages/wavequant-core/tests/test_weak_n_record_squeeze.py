from datetime import datetime
from dataclasses import replace
import json
from pathlib import Path

import pytest

from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals
from wavequant.domain.strategies.strategy_profiles import whole_wave_profile


def test_lexin_existing_n_enters_on_august4_without_waiting_for_new_n():
    raw = json.loads((Path(__file__).parent / "fixtures/lexin_2026_squeeze.json").read_text(encoding="utf-8"))
    bars = [Bar(datetime.fromisoformat(day), raw["symbol"], *values) for day, *values in raw["bars"]]
    config = SystemStrategy(
        **(whole_wave_profile({"scenarios": {"base": {"execution": {}}}})["strategy"] | {"volume_filter": False})
    )
    result = generate_system_signals(bars, config)
    signal = next(
        (s for s in result.signals if s.side == "LONG" and s.timestamp.date().isoformat() == "2026-08-04"), None
    )
    assert signal is not None
    assert signal.trigger_timestamp.date().isoformat() == "2026-07-24"
    prefix = generate_system_signals(bars[: signal.bar_index + 1], config)
    assert prefix.signals == [s for s in result.signals if s.bar_index <= signal.bar_index]


@pytest.mark.parametrize("missing", ["volume", "record"])
def test_weak_n_requires_both_new_record_and_expanding_volume(missing):
    raw = json.loads((Path(__file__).parent / "fixtures/lexin_2026_squeeze.json").read_text(encoding="utf-8"))
    bars = [
        Bar(datetime.fromisoformat(day), raw["symbol"], *values) for day, *values in raw["bars"] if day <= "2026-08-04"
    ]
    if missing == "volume":
        bars[-1] = replace(bars[-1], volume=bars[-2].volume)
    else:
        bars[-1] = replace(bars[-1], close=15.450914302467432)
    config = SystemStrategy(
        **(whole_wave_profile({"scenarios": {"base": {"execution": {}}}})["strategy"] | {"volume_filter": False})
    )
    result = generate_system_signals(bars, config)
    assert not any(
        s.side == "LONG"
        and s.timestamp == bars[-1].timestamp
        and s.trigger_timestamp.date().isoformat() == "2026-07-24"
        for s in result.signals
    )
