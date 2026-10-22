"""同源完整行情覆盖桂林一饱后突破；仅验证策略，不伪造分钟或成交权限。"""

from datetime import datetime
import json
from pathlib import Path

import pytest

from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.daily_strategy import daily_signals
from wavequant.domain.strategies.integrated_strategy import SystemStrategy
from wavequant.domain.strategies.one_p_breakout_entry import one_p_breakout_entry_rejection


def test_original_guilin_may_n_blocks_june_breakout_and_two_following_sessions():
    fixtures = Path(__file__).parent / 'fixtures'
    original = json.loads((fixtures / 'guilin_2022_positive_n.json').read_text('utf-8'))
    continuation = json.loads((fixtures / 'guilin_2022_hierarchy.json').read_text('utf-8'))
    bars = [Bar(**dict(row, timestamp=datetime.fromisoformat(row['timestamp'])))
            for row in original['bars']]
    by_date = {bar.timestamp.date().isoformat(): bar for bar in bars}
    # 拼接前要求所有重叠OHLCV一致，不能将不同复权口径当成连续价格。
    for day, *values in continuation['bars']:
        if day in by_date:
            prior = by_date[day]
            assert values == pytest.approx([prior.open, prior.high, prior.low, prior.close, prior.volume])
    last = bars[-1].timestamp.date().isoformat()
    bars.extend(Bar(datetime.fromisoformat(day), continuation['symbol'], *values)
                for day, *values in continuation['bars'] if last < day <= '2022-07-04')
    dates = {bar.timestamp.date().isoformat(): index for index, bar in enumerate(bars)}
    june30 = bars[dates['2022-06-30']]
    assert tuple(round(value, 2) for value in (june30.open, june30.high, june30.low, june30.close)) == (
        7.73, 8.16, 7.64, 8.16)
    result = daily_signals(bars, SystemStrategy(**original['strategy']))
    source = next(event for event in result.audit if event['event'] == 'n_structure_completed'
                  and event['bar_index'] == dates['2022-05-11'])
    assert source['target_eligible'] is False
    for offset, day in enumerate(('2022-06-29', '2022-06-30', '2022-07-01'), 1):
        index = dates[day]
        matching = [event for event in result.audit if event.get('n_date') == '2022-05-11'
                    and event.get('key_date') == '2022-04-13']
        proof = one_p_breakout_entry_rejection(matching, index, observed_high=bars[index].high)
        assert proof is not None
        assert proof['n_date'] == '2022-05-11'
        assert proof['key_date'] == '2022-04-13'
        assert proof['breakout_date'] == '2022-06-29'
        assert proof['observation_day'] == offset
        assert proof['one_p_reached_index'] < dates['2022-06-29']
        assert not any(signal.bar_index == index and signal.side == 'LONG' for signal in result.signals)
    assert one_p_breakout_entry_rejection(matching, dates['2022-07-04'],
        observed_high=bars[dates['2022-07-04']].high) is None
