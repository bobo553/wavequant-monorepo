"""桂林完整历史经真实趋势、资格、信号和逐日账户复现原目标交接后的漏拦截。"""

from datetime import datetime
import json
from pathlib import Path

import pytest

from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.two_t_entry_gate import TWO_T_ENTRY_REACHED
from wavequant.infrastructure.market_data.akshare_history import MinuteCoverageError
from wavequant.infrastructure.market_data.minute import MinuteBar
from wavequant.interfaces.research_tools.streaming_backtest import streaming_stock_result


def unavailable_minutes(bar: Bar) -> list[MinuteBar]:
    # 与该历史订单相同的缺分钟日线降级，不能伪造 2021 年分钟轨迹。
    raise MinuteCoverageError(bar.timestamp.date().isoformat(), None, None)


def test_original_august_n_blocks_september_close_after_measurement_handoff() -> None:
    raw = json.loads((Path(__file__).parent / 'fixtures/guilin_2021_two_t_entry.json').read_text('utf-8'))
    bars = [Bar(**dict(row, timestamp=datetime.fromisoformat(row['timestamp']))) for row in raw['bars']]
    generated, view = streaming_stock_result(bars, raw['strategy'], raw['execution'],
        minute_loader=unavailable_minutes)
    dates = {bar.timestamp.date().isoformat(): index for index, bar in enumerate(bars)}
    attack, buy_day = dates['2021-08-16'], dates['2021-09-10']
    source = next(row for row in generated.audit if row['event'] == 'n_completed'
        and row['bar_index'] == attack and row.get('target_eligible'))
    assert source['two_t'] == pytest.approx(6.959472101381529)
    assert source['one_p'] < bars[buy_day].open < bars[buy_day].close < source['two_t'] <= bars[buy_day].high
    assert any(row['event'] == 'n_target_source_retired' and row.get('n_id') == source['n_id']
        and row['bar_index'] == dates['2021-09-06'] and row['reason'] == 'new_confirmed_decline'
        for row in generated.audit)
    assert any(row['event'] == 'n_completed' and row['bar_index'] == dates['2021-09-08']
        and row.get('target_eligible') and row['two_t'] > bars[buy_day].high for row in generated.audit)
    rejections = [row for row in generated.audit if row['bar_index'] == buy_day
        and row.get('reason') == TWO_T_ENTRY_REACHED]
    assert rejections and all(row['target_source_id'] == source['n_id'] for row in rejections)
    assert any(row['candidate_channel'] == 'global_two_t_entry_guard' for row in rejections)
    assert not any(signal.bar_index == buy_day and signal.side == 'LONG' for signal in generated.signals)
    assert not any(order['timestamp'][:10] == '2021-09-10' and order['side'] == 'BUY'
        and order['status'] == 'filled' for order in view['orders'])
    # 仍有原资格建立后的正常成交，不能因全段禁交易而虚假通过。
    assert any(order['timestamp'][:10] == '2021-08-16' and order['side'] == 'BUY'
        and order['status'] == 'filled' for order in view['orders'])
