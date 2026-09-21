from datetime import datetime
from dataclasses import replace
import json
from pathlib import Path

from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals
from wavequant.domain.strategies.strategy_profiles import whole_wave_profile
from wavequant.domain.strategies.whole_wave_entry import select_wave_entry
from tests.test_whole_wave_entry import context, bars


def test_joint_confirmation_requires_same_n_and_available_live_context():
    ctx = context(alternation_index=17, confirmation_attack=15)
    options = dict(bars=bars(), attack=15, low_index=12, asof=17, allow_confirming_n=True)
    proof, reason = select_wave_entry([ctx], [], **options)
    assert not reason and proof["joint_alternation_confirmation"]
    assert proof["eligibility_frozen_at"] == 17
    for changed in (
        replace(ctx, confirmation_attack=14),
        replace(ctx, alternation_index=18),
        replace(ctx, trend_level=1),
    ):
        assert select_wave_entry([changed], [], **options)[0] is None
    assert select_wave_entry([ctx], [], **(options | {"allow_confirming_n": False}))[0] is None
    earlier = replace(ctx, alternation_index=16)
    retained, rejected = select_wave_entry([earlier], [], **options)
    assert not rejected and retained["eligibility_frozen_at"] == 16


def test_lexin_february18_joint_confirmation_can_enter_causally():
    raw = json.loads((Path(__file__).parent / "fixtures/lexin_2026_squeeze.json").read_text(encoding="utf-8"))
    data = [Bar(datetime.fromisoformat(day), raw["symbol"], *values) for day, *values in raw["bars"]]
    config = SystemStrategy(
        **(whole_wave_profile({"scenarios": {"base": {"execution": {}}}})["strategy"] | {"volume_filter": False})
    )
    full = generate_system_signals(data, config)
    signal = next(s for s in full.signals if s.side == "LONG" and str(s.timestamp.date()) == "2019-02-18")
    assert str(signal.trigger_timestamp.date()) == "2019-02-14"
    prefix = generate_system_signals(data[: signal.bar_index + 1], config)
    assert prefix.signals == [s for s in full.signals if s.bar_index <= signal.bar_index]
    assert not any(s.side == "LONG" and str(s.timestamp.date()) == "2026-07-02" for s in full.signals)
    assert any(s.side == "LONG" and str(s.timestamp.date()) == "2026-08-04" for s in full.signals)
