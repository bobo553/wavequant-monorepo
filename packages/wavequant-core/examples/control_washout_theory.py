"""Run python -m examples.control_washout_theory; synthetic, never orders."""
from dataclasses import replace
from datetime import datetime, timedelta
import sys

from wavequant.domain.models.model import Bar
from wavequant.domain.market_structure.price_action import Direction, ShadowPolicy
from wavequant.domain.market_structure.n_shape import NSetup, PivotRef, BoxAnchorMode, MilestoneBasis
from wavequant.domain.market_state.market_regime import RegimePolicy, WaveBoundary, observe_market_regime
from wavequant.domain.market_state.control_bar import observe_control_bar
from wavequant.domain.market_state.washout import WashoutPolicy, observe_washout


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    rows = [(8.5, 9, 8, 8.5), (10, 12, 9.5, 11), (10.7, 11, 10, 10.5),
            (10.8, 12.6, 10.4, 12.2), (13, 19, 12.5, 18), (15, 16, 13, 14),
            (14.5, 16.5, 14, 16), (15.5, 16, 14, 15), (15, 17, 14.5, 16.8),
            (16.8, 18, 16, 18), (18, 19, 17.5, 19)]
    original = [Bar(datetime(2026, 1, 1)+timedelta(days=i), 'DEMO', *row,
                    2000 if i == 3 else 1000) for i, row in enumerate(rows)]
    print('合成定义核验：不是实际主力成本、不是 70% 胜率，也不生成订单。')
    for direction in (Direction.UP, Direction.DOWN):
        bars = original if direction == Direction.UP else [
            replace(b, open=50-b.open, high=50-b.low, low=50-b.high, close=50-b.close)
            for b in original]
        def setup(a, b, c):
            return NSetup('DEMO', '1d', direction, PivotRef(a, a), PivotRef(b, b), PivotRef(c, c),
                          'synthetic_known_pivots', BoxAnchorMode.ATTACK_VIRTUAL_EXTREME)
        first, second = setup(0, 1, 2), setup(5, 6, 7)
        control = observe_control_bar(bars, first, timeframe='1d', volume_lookback=3,
                                      shadow_policy=ShadowPolicy(.5))
        result = observe_washout(bars, first, timeframe='1d',
            policy=WashoutPolicy(MilestoneBasis.CLOSE), reattack_setup=second)
        regime = observe_market_regime(bars, second, timeframe='1d',
            policy=RegimePolicy(ShadowPolicy(.5), WaveBoundary.ORIGIN))
        print('\n' + ('底部洗盘价格模板' if direction == Direction.UP else '头部洗盘镜像价格模板'))
        print(f'  {control.completion.defense_name}={control.defense_level.price:g}；'
              f'攻击量／前三棒均量={control.volume.relative_volume:g}')
        print(f'  原颈线={result.neckline:g}；1P={result.one_p:g}；2T={result.two_t:g}')
        previous_stage = None
        for f in result.frames:
            if f.stage != previous_stage:
                print(f'  棒 {f.bar_index}: {f.stage.value}')
                previous_stage = f.stage
        print(f'  新 N 防守={result.latest.new_n_defense:g}；新 N 后续六态={regime.latest.regime.value}')
        print(f'  用途={result.review_role}；开空仓={result.opens_short_position}')


if __name__ == '__main__':
    main()
