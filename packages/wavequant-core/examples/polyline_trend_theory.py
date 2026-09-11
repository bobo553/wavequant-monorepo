"""Run python -m examples.polyline_trend_theory; synthetic theory audit only."""
from dataclasses import replace
from datetime import datetime, timedelta
import sys

from wavequant.model import Bar
from wavequant.polyline import (LinePoint, PointKind as K, ReversalPoint,
    child_mother_path, n_setup_from_polyline, observe_polyline)
from wavequant.price_action import AttackBasis, Direction
from wavequant.n_shape import BoxAnchorMode, MilestoneBasis, observe_n
from wavequant.market_regime import RegimePolicy, WaveBoundary, observe_market_regime
from wavequant.trend_structure import observe_structure, observe_trend_transition


def make_bars(rows):
    return [Bar(datetime(2026, 1, 1)+timedelta(days=i), 'DEMO', *row, 1000)
            for i, row in enumerate(rows)]


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    print('合成案例：验证定义与确认时序，不是市场收益测试。')
    data = make_bars([(10, 11, 9, 10), (11, 12, 10, 11), (10, 11, 8, 9),
        (9, 10, 7, 8), (10, 13, 9, 12), (10, 12, 8, 10), (11, 13, 10, 12.5),
        (13, 15, 12, 15), (15, 16, 14, 16), (16, 17, 15.5, 17)])
    line = observe_polyline(data, symbol='DEMO', timeframe='1d', initial_direction=Direction.UP,
                            start_index=0, asof_index=6)
    print('\n折线 → 已确认 ABC → N → 六态：')
    for p in line.reversals:
        print(f'  {p.reversal} {p.point.kind.value}={p.point.price:g}，来源棒 {p.point.index}，确认棒 {p.confirmed_index}')
    setup = n_setup_from_polyline(line, box_anchor_mode=BoxAnchorMode.ATTACK_VIRTUAL_EXTREME)
    n = observe_n(data, setup, timeframe='1d', milestone_basis=MilestoneBasis.CLOSE)
    regime = observe_market_regime(data, setup, timeframe='1d',
                                   policy=RegimePolicy(None, WaveBoundary.ORIGIN))
    print(f'  N 完成于棒 {n.completion.bar_index}，防守={n.completion.defense:g}')
    print(f'  棒 {regime.latest.bar_index} 确认 {regime.latest.regime.value}')

    rows = [(24, 30, 23, 25), (24, 26, 22, 23), (22, 23, 20, 21),
        (22, 24, 21, 23), (23, 25, 22, 24), (22, 23, 15, 16), (13, 15, 10, 12),
        (13, 16, 12, 15), (16, 18, 15, 17), (16, 17, 14, 15), (15, 16, 13, 14),
        (16, 24, 15, 23), (24, 28, 23, 27), (26, 27, 23, 24), (24, 25, 22, 23), (24, 27, 23, 26)]
    values = [(K.HIGH, 30), (K.LOW, 20), (K.HIGH, 25), (K.LOW, 10),
              (K.HIGH, 18), (K.LOW, 13), (K.HIGH, 28), (K.LOW, 22)]
    for mirrored in (False, True):
        bars = make_bars(rows)
        if mirrored:
            bars = [replace(b, open=50-b.open, high=50-b.low, low=50-b.high, close=50-b.close) for b in bars]
        points = tuple(ReversalPoint(LinePoint(2*i, 0,
            (K.LOW if kind == K.HIGH else K.HIGH) if mirrored else kind,
            50-price if mirrored else price), 2*i+1, 'synthetic_confirmed_pivots')
            for i, (kind, price) in enumerate(values))
        context = observe_structure(points, symbol='DEMO', timeframe='1d', window_start=0, asof_index=7)
        print(f'\n冻结背景：{context.trend.value}，本例拐点由显式样本提供：')
        for end in (10, 11, 12, 14, 15):
            r = observe_trend_transition(bars, points, context=context,
                                         attack_basis=AttackBasis.CLOSE, asof_index=end)
            print(f'  棒 {end}: {r.stage.value}，冻结关键位={r.key.price:g}')
        print(f'  {r.suspicion_name}棒 {r.suspicion_index} → {r.break_name}棒 {r.attack.bar_index}')
        print(f'  {r.alternation_name}棒 {r.alternation_confirmed_index}；反向幅度={r.retracement.ratio:.0%}')

    child, mother = make_bars([(9, 11, 8, 10), (8, 13, 7, 12)])
    path = child_mother_path(child, mother, child_index=0)
    print('\n阳子＋阳母教学连接：'+' → '.join(f'{p.kind.value} {p.price:g}' for p in path.vertices))
    print('以上子母连接是讲义约定，不是日内真实路径；默认折线遇内外包会暂停等待证据。')


if __name__ == '__main__':
    main()
