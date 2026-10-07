"""Derive formal reversals and immediately confirmed live level-3 trends.

Confirmed level-3 points remain immutable structural reversals. A separate
display-only path exposes confirmed nested level-2 turns plus the unresolved
tail after the latest reversal. After a market-confirmed extreme, a countertrend
tail waits for a strict break of level 2's frozen pre-extreme key. Strict market breaks of the
same-level key confirm a solid trend independently of the live endpoint.
"""
from zoneinfo import ZoneInfo

from .hierarchical_development import hierarchical_developing_path
from .lecture_trend import _annotate
from .secondary_trend import _structural_reversals
from .trend_landmarks import (
    bear_bull_alternation_lows,
    bear_to_bull_highs,
    bullish_turn_signals,
    post_alternation_bull_highs,
)


def tertiary_trends(level2,bars):
    dates={(b.timestamp.astimezone(ZoneInfo('Asia/Shanghai')) if b.timestamp.tzinfo else b.timestamp).date().isoformat():i
           for i,b in enumerate(bars)}
    strokes=[]; developing_strokes=[]
    for source_index,source in enumerate(level2['strokes']):
        points=_structural_reversals(source['points'],source_level=2)
        if not points:
            continue
        _annotate(points,bars[0].symbol,dates)
        for p in points:
            p['levels'].insert(0,dict(name='二级'+('末升低' if p['flip']=='翻多为空' else '末跌高'),price=p['broken_key']['value']))
        strokes.append(dict(id='tertiary-'+source['id'],source_path=source['id'],kind='tertiary',
                            trend_level=3,points=points,input_turn_count=len(source['points'])))
        next_sources=[later for later in level2['strokes'][source_index+1:] if later['points']]
        end_index=next_sources[0]['points'][0]['index']-1 if next_sources else len(bars)-1
        tail=hierarchical_developing_path(source,points,trend_level=3,source_level=2,kind='tertiary',
                                         bars=bars,end_index=end_index)
        if tail:
            developing_strokes.append(tail)
    return dict(name='三级趋势线',trend_level=3,source_level=2,strokes=strokes,
                bear_to_bull_highs=bear_to_bull_highs(strokes,trend_level=3),
                bear_bull_alternation_lows=bear_bull_alternation_lows(strokes,trend_level=3,source_strokes=level2['strokes'],bars=bars),
                post_alternation_bull_highs=post_alternation_bull_highs(strokes,trend_level=3,source_strokes=level2['strokes'],bars=bars),
                bullish_turn_signals=bullish_turn_signals(strokes,bars,trend_level=3,source_strokes=level2['strokes']),
                developing_strokes=developing_strokes,
                input_turn_count=sum(len(s['points']) for s in level2['strokes']),
                confirmed_wave_count=sum(len(s['points']) for s in strokes),
                developing_wave_count=len(developing_strokes),
                developing_point_count=sum(len(s['points']) for s in developing_strokes),
                aggregation_rule='level2_structural_key_break',scope='lecture_level3_not_strategy_confirmation',
                note='仅以已确认二级点为输入；突破二级末跌高确认整段低点，跌破二级末升低确认整段高点；'
                     '市场最高价突破已知同级前高或最低价跌破已知同级前低时，趋势线立即确认并画实线，末端继续延伸；'
                     '没有新同级突破时，反向虚线须等极值之前冻结的二级末升低或末跌高严格破位确认趋势扭转；'
                     '最后一个正式三级点之后的已确认二级演化另作纯显示发展路径，不进入正式点、策略或回测；'
                     '不等待67%交替，不跨断点。')
