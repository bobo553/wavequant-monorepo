"""Run python -m examples.wave_strength_turn_theory; synthetic observations only."""
from dataclasses import replace
from datetime import datetime, timedelta
import sys

from wavequant.model import Bar
from wavequant.polyline import LinePoint, PointKind as K, ReversalPoint
from wavequant.price_action import Direction as D, AttackBasis, KeyLevel, LevelKind
from wavequant.market_regime import MarketRegime
from wavequant.wave_strength import StrengthScale, measure_strength
from wavequant.market_turn import (TurnThreshold, TurnPolicy, TurnSetup, RegimeContext,
                                  freeze_minor_line, observe_market_turn)


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    scale = StrengthScale.EXACT_FRACTIONS
    print('合成定义核验；不推断真实主力，不产生订单或收益结论。')
    for move in (60, 100, 120, 150, 180, 200, 220):
        pull = measure_strength(100, 400, 400-move, impulse_direction=D.UP, scale=scale)
        bounce = measure_strength(400, 100, 100+move, impulse_direction=D.DOWN, scale=scale)
        print(f'反向比例 {pull.exact_ratio}: 回档={pull.counter_strength.value}；反弹={bounce.counter_strength.value}')
    print('图 011：回档 1/3，对应价格位于原波幅自低向上的 2/3。')
    rows = [(36, 40, 35, 38), (26, 27, 20, 22), (15, 20, 10, 12),
            (14, 23, 13, 22), (23, 27, 20, 26), (27, 30, 25, 29),
            (30, 32, 28, 31), (29, 30, 25, 26), (25, 27, 24, 25),
            (25, 28, 24.5, 27), (28, 30, 26, 29), (28, 29, 25, 28), (28, 31, 27, 30)]
    original = [Bar(datetime(2026, 1, 1)+timedelta(days=i), 'DEMO', *r, 1000) for i, r in enumerate(rows)]
    policy = TurnPolicy(scale, TurnThreshold.TWO_THIRDS, TurnThreshold.TWO_THIRDS,
                        AttackBasis.CLOSE, AttackBasis.CLOSE)
    for direction in (D.UP, D.DOWN):
        up = direction == D.UP
        bars = original if up else [replace(b, open=50-b.open, high=50-b.low,
                                           low=50-b.high, close=50-b.close) for b in original]
        def point(i, kind, price):
            return ReversalPoint(LinePoint(i, 0, kind if up else K.LOW if kind == K.HIGH else K.HIGH,
                                          price if up else 50-price), i+1, 'synthetic_confirmed_pivot')
        pts = (point(0, K.HIGH, 40), point(2, K.LOW, 10), point(6, K.HIGH, 32), point(8, K.LOW, 24))
        context = RegimeContext('DEMO', '1d', MarketRegime.BEAR if up else MarketRegime.BULL, 3,
                                'explicit_synthetic_background_not_automatic_regime_detection')
        key = KeyLevel('DEMO', '1d', LevelKind.RESISTANCE if up else LevelKind.SUPPORT,
                       40 if up else 10, 0, 3, 'frozen_last_fall_high_or_last_rise_low')
        setup = TurnSetup('DEMO', '1d', direction, *pts, context, key)
        line = freeze_minor_line((pts[2], pts[3], point(10, K.HIGH, 30)), symbol='DEMO',
            timeframe='1d', direction=direction, selected_at_index=11,
            source='synthetic_minor_pivots_explicitly_mapped_to_same_bar_axis')
        print('\n' + ('盘势正扭转' if up else '盘势负扭转'))
        for end in (6, 7, 9, 11, 12):
            r = observe_market_turn(bars, setup, policy=policy, minor_line=line, asof_index=end)
            print(f'  棒 {end}: {r.stage.value}')
        print(f'  第一反向幅度={r.first_strength.ratio:.2%}；第二反向幅度={r.second_strength.ratio:.2%}')
        print(f'  越线价位={r.line_break.line_price:g}；开空仓={r.opens_short_position}')


if __name__ == '__main__':
    main()
