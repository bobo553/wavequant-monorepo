"""海洋王同源日线：保留后续倒 N 身份隔离回归。"""

from datetime import datetime
import json
from pathlib import Path

import pytest

from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals


def frozen_input() -> tuple[list[Bar], SystemStrategy]:
    raw = json.loads((Path(__file__).parent / 'fixtures/haiyangwang_2021_squeeze.json').read_text('utf-8'))
    bars = [Bar(**dict(row, timestamp=datetime.fromisoformat(row['timestamp']))) for row in raw['bars']]
    return bars, SystemStrategy(**raw['strategy'])


@pytest.mark.parametrize('with_next_day', [False, True])
def test_daily_backtest_keeps_inverse_n_without_positive_identity(with_next_day: bool) -> None:
    bars, strategy = frozen_input()
    if not with_next_day:
        bars = bars[:-1]
    result = generate_system_signals(bars, strategy)
    completed = [row for row in result.audit if row['event'] == 'n_completed']
    assert any(row['direction'] == 'down' for row in completed)
    assert any(row['direction'] == 'up' for row in completed)
    positive_attacks = {row['bar_index'] for row in completed if row['direction'] == 'up'}
    for row in result.audit:
        if row['event'] == 'squeeze_round_changed' and row.get('squeeze_owner_attack') is not None:
            assert row['squeeze_owner_attack'] in positive_attacks
