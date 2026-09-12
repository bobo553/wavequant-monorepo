"""Run: python -m examples.six_market_regimes (synthetic, not market returns)."""
from dataclasses import replace
from datetime import datetime, timedelta
import sys

from wavequant.domain.models.model import Bar
from wavequant.domain.market_structure.n_shape import BoxAnchorMode, NSetup, PivotRef
from wavequant.domain.market_structure.price_action import Direction, ShadowPolicy
from wavequant.domain.market_state.market_regime import RegimePolicy, WaveBoundary, observe_market_regime


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    base = [(8.5, 9, 8, 8.5), (10, 12, 9.5, 11), (10.7, 11, 10, 10.5),
            (10.8, 12.6, 10.4, 12.2)]
    paths = {
        '无抵抗连续推进': [(12.2, 13.5, 12, 13.5), (13.5, 14.5, 13, 14.5)],
        '抵抗失败后延续': [(12.4, 12.5, 11, 11.5), (11.5, 13.5, 11.2, 13.5)],
        '局部抵抗成功后延续': [(12.4, 12.5, 10.1, 11), (11, 11.8, 9.5, 10),
                             (10, 12, 9.6, 11.8), (11.8, 13.5, 11.5, 13.5)],
    }
    print('六态合成样本核验；不是通达信实测，不产生买卖或收益。')
    print('长影阈值 0.5 是演示值；波段边界选择 N 起点。')
    for name, tail in paths.items():
        original = [Bar(datetime(2026, 1, 1) + timedelta(days=i), 'DEMO', *row, 1000)
                    for i, row in enumerate(base + tail)]
        for direction in (Direction.UP, Direction.DOWN):
            bars = original if direction == Direction.UP else [
                replace(b, open=40-b.open, high=40-b.low, low=40-b.high, close=40-b.close)
                for b in original]
            setup = NSetup('DEMO', '1d', direction, PivotRef(0, 0), PivotRef(1, 1),
                           PivotRef(2, 2), 'synthetic_confirmed_pivots',
                           BoxAnchorMode.ATTACK_VIRTUAL_EXTREME)
            result = observe_market_regime(bars, setup, timeframe='1d',
                policy=RegimePolicy(ShadowPolicy(.5), WaveBoundary.ORIGIN))
            print(f'\n{name} / {direction.value} → {result.latest.regime.value}')
            print(f'N 防守={result.n_defense:g}；波段边界={result.wave_boundary:g}')
            for frame in result.frames:
                resistance = frame.resistance.detected if frame.resistance else '未观察'
                label = frame.regime.value if frame.regime else frame.phase.value
                print(f'  棒 {frame.bar_index}: 抵抗={resistance}；{label}')


if __name__ == '__main__':
    main()
