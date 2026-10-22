"""低开放量中大阳轧空确认不套用独立突破的倍量门槛。"""

from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path

import pytest

from wavequant.domain.market_state.candle_strength import bullish_breakout_rejection
from wavequant.domain.market_state.market_regime import MarketRegime, RegimePolicy, WaveBoundary, observe_market_regime
from wavequant.domain.market_structure.n_shape import BoxAnchorMode, MilestoneBasis, NSetup, PivotRef, observe_n
from wavequant.domain.market_structure.price_action import Direction, ShadowPolicy
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.attack_quality import squeeze_buy_rejection
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals
from wavequant.domain.strategies.lecture_strategy import lecture_pivot_history
from wavequant.domain.strategies.same_level_recovery import RECOVERY_CLEARED, RECOVERY_REQUIRED


def frozen_input() -> tuple[list[Bar], SystemStrategy]:
    raw = json.loads((Path(__file__).parent / 'fixtures/guofang_2024_squeeze_confirmation.json').read_text('utf-8'))
    bars = [Bar(**dict(row, timestamp=datetime.fromisoformat(row['timestamp']))) for row in raw['bars']]
    return bars, SystemStrategy(**raw['strategy'])


def test_actual_confirmation_candle_needs_increased_volume_not_double_volume() -> None:
    bars, _ = frozen_input()
    previous, current = bars[-2:]
    assert current.timestamp.date().isoformat() == '2024-11-29'
    assert current.open < previous.close and current.close > previous.high
    assert previous.volume < current.volume < 2 * previous.volume
    assert bullish_breakout_rejection(current, previous) == 'breakout_low_open'
    assert squeeze_buy_rejection(current, previous, bars[-4]) is None


@pytest.mark.parametrize('change', ['equal_volume', 'below_volume', 'equal_high', 'below_high', 'weak_body', 'future'])
def test_low_open_exception_keeps_volume_shape_and_price_boundaries(change: str) -> None:
    previous = Bar(datetime(2024, 11, 28), 'sh.600000', 10, 10.5, 9.8, 10.2, 100)
    current = Bar(datetime(2024, 11, 29), previous.symbol, 10, 11, 9.9, 11, 148)
    if change == 'equal_volume':
        current = replace(current, volume=100)
    elif change == 'below_volume':
        current = replace(current, volume=99)
    elif change == 'equal_high':
        current = replace(current, close=previous.high)
    elif change == 'below_high':
        current = replace(current, close=previous.high - .01)
    elif change == 'weak_body':
        current = replace(current, low=8)
    else:
        previous = replace(previous, timestamp=current.timestamp)
    assert squeeze_buy_rejection(current, previous) is not None


def test_actual_polyline_n_and_low_open_squeeze_remain_valid_structure() -> None:
    # 结构观察不依赖交易资格；顶点来自截至攻击日的真实折线，不注入买点。
    bars, strategy = frozen_input()
    attack = len(bars) - 3
    snapshots, _, _, _ = lecture_pivot_history(bars[:attack + 1])
    origin, neckline, pullback = snapshots[attack][-3:]
    assert [bars[point.point.index].timestamp.date().isoformat() for point in (origin, neckline, pullback)] == [
        '2024-11-22', '2024-11-26', '2024-11-27']
    setup = NSetup(bars[0].symbol, '1d', Direction.UP,
        PivotRef(origin.point.index, origin.confirmed_index),
        PivotRef(neckline.point.index, neckline.confirmed_index),
        PivotRef(pullback.point.index, pullback.confirmed_index),
        strategy.pivot_mode, BoxAnchorMode.ATTACK_VIRTUAL_EXTREME,
        allow_confirmation_bar=True, allow_outside_close=True, staged_defense=True)
    n = observe_n(bars, setup, timeframe='1d', milestone_basis=MilestoneBasis.EXTREME)
    assert n.completion is not None and n.completion.bar_index == attack
    before = observe_n(bars[:attack], setup, timeframe='1d', milestone_basis=MilestoneBasis.EXTREME)
    assert before.completion is None
    regime = observe_market_regime(bars, setup, timeframe='1d', _prepared_n=n,
        policy=RegimePolicy(ShadowPolicy(strategy.shadow_fraction), WaveBoundary.ORIGIN,
                            local_resistance_failure=True))
    assert regime.attack_index == attack
    assert regime.latest is not None and regime.latest.bar_index == len(bars) - 1
    assert regime.latest.regime == MarketRegime.BULL
    assert regime.latest.last_confirmed_index == len(bars) - 1
    assert squeeze_buy_rejection(bars[-1], bars[-2], bars[attack - 1]) is None


def test_full_history_recovery_barrier_blocks_otherwise_valid_squeeze() -> None:
    # 同级末升低破位后的恢复规则优先于局部轧空，不能沿用新增规则前的买入预期。
    bars, strategy = frozen_input()
    result = generate_system_signals(bars, strategy)
    dates = {bar.timestamp.date().isoformat(): index for index, bar in enumerate(bars)}
    blocked_at = dates['2024-02-06']
    recovery_high = dates['2023-08-01']
    broken_low = dates['2022-10-31']
    barrier = next(row for row in result.audit if row['event'] == RECOVERY_REQUIRED
                   and row['trend_level'] == 2 and row['bar_index'] == blocked_at)
    assert barrier['recovery_high']['index'] == recovery_high
    assert barrier['recovery_high']['value'] == bars[recovery_high].high
    assert barrier['broken_low']['index'] == broken_low
    assert barrier['broken_low']['value'] == bars[broken_low].low
    assert bars[blocked_at].low < bars[broken_low].low
    assert max(bar.high for bar in bars[blocked_at:]) < bars[recovery_high].high
    path = barrier['source_path']
    assert not any(row['event'] == RECOVERY_CLEARED and row['trend_level'] == 2
                   and row['source_path'] == path and row['bar_index'] >= blocked_at for row in result.audit)
    assert not any(row['event'] == 'trading_interval_opened' and row['trend_level'] == 2
                   and row['bar_index'] >= blocked_at for row in result.audit)
    assert not any(signal.side == 'LONG' and signal.bar_index >= blocked_at for signal in result.signals)
    # 无资格期间不运行交易N扫描；独立结构证据由上一个用例保留。
    assert not any(row['event'] == 'n_completed' and row.get('direction') == 'up'
                   and row['bar_index'] == dates['2024-11-27'] for row in result.audit)
