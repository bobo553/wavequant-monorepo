"""Run ``python -m examples.figure008_foundations`` to audit Figure 008's chain.

The sample preserves relative pivot geometry. Prices, OHLC and confirmation dates
are synthetic engineering evidence, because the drawing supplies no price axis,
candle sequence or timestamps. No observation here is a trading recommendation.
"""

from io import TextIOWrapper
import sys

from examples.figure008_fixture import FIGURE008_LABELS, build_figure008_sample
from wavequant.domain.market_structure.price_action import AttackBasis
from wavequant.domain.market_structure.trend_foundations import observe_trend_foundations
from wavequant.domain.market_structure.trend_primitives import (
    is_bear_trend,
    is_bull_trend,
    last_fall_high,
    last_rise_low,
    weak_countermove,
)


def main() -> None:
    """Print keys, strict windows and causal transition events in Chinese."""

    if isinstance(sys.stdout, TextIOWrapper):
        sys.stdout.reconfigure(encoding='utf-8')
    bars, points = build_figure008_sample()
    print('示意样本，不固定段数或段长；Lx / Hx 只用于解释本图标签，计算入口消费实际已确认拐点。')
    print('图 008：标签顺序与相对高低来自示意图；数值、K 棒和工程日期均为合成样本。')
    print('点价没有真实价格轴，确认采用下一棒；尾棒 17 只用于确认 L8。固定量能不代表图中量增。')
    print(' → '.join(f'{label}({point.point.price:g})' for label, point in zip(FIGURE008_LABELS, points)))

    complete = observe_trend_foundations(bars, points, symbol='FIG008', timeframe='1d', asof_index=17).structure
    print('\n关键位（从指定极点向左找最近的相反已确认拐点）：')
    for target, expected in (('L0', 'H1'), ('L7', 'H6'), ('H0', 'L4'), ('H4', 'L3')):
        selected = points[FIGURE008_LABELS.index(target)]
        key = last_fall_high(complete, selected) if target.startswith('L') else last_rise_low(complete, selected)
        if key is None:
            raise RuntimeError(f'{target} lacks its expected left key {expected}')
        name = '末跌高' if target.startswith('L') else '末升低'
        print(f'  {target} 的{name} = {FIGURE008_LABELS[key.source_index]}，样本价 {key.price:g}')

    left = observe_trend_foundations(bars, points, symbol='FIG008', timeframe='1d', asof_index=3).structure
    bull = observe_trend_foundations(
        bars, points, symbol='FIG008', timeframe='1d', window_start=2, asof_index=10).structure
    bear = observe_trend_foundations(
        bars, points, symbol='FIG008', timeframe='1d', window_start=9, asof_index=15).structure
    mixed = observe_trend_foundations(
        bars, points, symbol='FIG008', timeframe='1d', window_start=9, asof_index=17).structure
    print('\n严格区间趋势：')
    print(f'  左背景截至 L0：{left.window_trend.value}，不能从截断画面猜初态。')
    print(f'  L0 → H0：每个高点和低点都抬高，多头趋势={is_bull_trend(bull)}。')
    print(f'  H0 → L7：每个高点和低点都降低，空头趋势={is_bear_trend(bear)}。')
    print(f'  加入 L8 后：{mixed.window_trend.value}，L8 > L7 使整个区间不再严格空头。')

    initial_bear = observe_trend_foundations(bars, points, symbol='FIG008', timeframe='1d', asof_index=4).structure
    up = observe_trend_foundations(
        bars, points, symbol='FIG008', timeframe='1d', transition_context=initial_bear,
        attack_basis=AttackBasis.CLOSE, asof_index=9,
    )
    down = observe_trend_foundations(
        bars, points, symbol='FIG008', timeframe='1d', transition_context=bull,
        attack_basis=AttackBasis.CLOSE, asof_index=16,
    )
    bottom = observe_trend_foundations(
        bars, points, symbol='FIG008', timeframe='1d', transition_context=bear,
        attack_basis=AttackBasis.CLOSE, asof_index=17,
    )
    up_transition, down_transition, bottom_transition = up.transition, down.transition, bottom.transition
    up_signals, down_signals, bottom_signals = up.signals, down.signals, bottom.signals
    if (up_transition is None or down_transition is None or bottom_transition is None
            or up_signals is None or down_signals is None or bottom_signals is None):
        raise RuntimeError('an explicit frozen context must yield transition and signal evidence')
    if (up_transition.attack is None or up_transition.retracement is None
            or down_transition.attack is None or down_transition.retracement is None):
        raise RuntimeError('synthetic transition chain lacks expected causal attack or countermove evidence')
    print('\n冻结关键位的确认链路（攻击口径显式选择收盘价）：')
    print(f'  截至 H2 已确认空头 → H4 > H1，棒 {up_transition.attack.bar_index} 翻空为多={up_signals.flip_to_bull}。')
    print(f'  L4 绘点 8，棒 {up_transition.alternation_confirmed_index} 确认空多交替={up_signals.bear_to_bull_alternation}；'
          f'回档 H4−L4 / H4−L3 = {up_transition.retracement.ratio:.2%}。')
    print(f'  H5 绘点 11，棒 {down_transition.suspicion_index} 确认头部疑虑={down_signals.head_suspicion}。')
    print(f'  L7 < L4，棒 {down_transition.attack.bar_index} 翻多为空={down_signals.flip_to_bear}，'
          f'已有疑虑的头部成形={down_signals.head_formed}。')
    print(f'  H7 绘点 15，棒 {down_transition.alternation_confirmed_index} 确认多空交替={down_signals.bull_to_bear_alternation}；'
          f'反弹 H7−L7 / H6−L7 = {down_transition.retracement.ratio:.2%}。')
    print(f'  L8 > L7，棒 {bottom_transition.suspicion_index} 仅有底部疑虑={bottom_signals.bottom_suspicion}；'
          f'尚未突破 H6，底部成形={bottom_signals.bottom_formed}。')
    print(f'  两次示意反向幅度均 < 67%，但弱势 < 33% 分别为 '
          f'{weak_countermove(up_transition.retracement)} / {weak_countermove(down_transition.retracement)}；'
          '图片本身无法量化该比例。')
    print('疑虑、攻击与交替是不同确认事件；历史事件可保留，当前阶段与买卖权限仍需独立判断。')


if __name__ == '__main__':
    main()
