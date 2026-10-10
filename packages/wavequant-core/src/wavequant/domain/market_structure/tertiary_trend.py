"""Derive formal reversals and immediately confirmed live level-3 trends.

Confirmed level-3 points remain immutable structural reversals. A separate
display-only path exposes confirmed nested level-2 turns plus the unresolved
tail after the latest reversal. After a market-confirmed extreme, a countertrend
tail waits for a strict break of level 2's frozen pre-extreme key. Strict market breaks of the
same-level key confirm a solid trend independently of the live endpoint.
"""
from zoneinfo import ZoneInfo
from collections.abc import Mapping

from .hierarchical_development import hierarchical_developing_path
from .lecture_trend import _annotate
from .trend_levels import trend_reversals
from .trend_level_upgrades import promote_trend_key_breaks
from .trend_publication import LEG_CONFIRMATION_POLICY, OPTIONAL_LEG_CONFIRMATION_POLICY, WAVE_DISPLAY_POLICY, confirmed_trend_legs, publish_uptrends
from .n_trend_reversals import n_target_reversals
from .trend_landmarks import (
    bear_bull_alternation_lows,
    bear_to_bull_highs,
    bullish_turn_signals,
    post_alternation_bull_highs,
)


def tertiary_trends(level2,bars, *, n_target_trend_confirmation_enabled: bool = False):
    """三级以正式二级点为唯一来源，方向与升级分别保留独立证据。

    等级参数只选择来源及结果元数据，不改变正/负反转、最近左侧关键位和
    严格多空比较公式。二级私有候选或显示尾端不参与三级。严格突破此前
    已知三级高点/低点仍可直接升级，不等待后来回调，也不把升级高/低点
    的确认当作此前反向走势的许可。所有发布及发展路径共用原有因果门禁。
    """
    dates={(b.timestamp.astimezone(ZoneInfo('Asia/Shanghai')) if b.timestamp.tzinfo else b.timestamp).date().isoformat():i
           for i,b in enumerate(bars)}
    strokes=[]; developing_strokes=[]; candidate_strokes=[]
    n_targets: list[dict[str,object]]=[]
    source_strokes=level2['strokes']
    for source_index,source in enumerate(source_strokes):
        next_sources=[later for later in source_strokes[source_index+1:] if later['points']]
        end_index=next_sources[0]['points'][0]['index']-1 if next_sources else len(bars)-1
        public_source: list[dict[str,object]]=next((item['points'] for item in level2['strokes'] if item['id']==source['id']),[])
        candidates=promote_trend_key_breaks(source['points'],trend_reversals(source['points'],trend_level=3),
                                           trend_level=3)
        if n_target_trend_confirmation_enabled:
            candidates=n_target_reversals(candidates,source['points'],bars[:end_index+1],source_level=2,
                                         qualified_source=public_source,target_sink=n_targets,
                                         n_target_trend_confirmation_enabled=True)
        # 仅在发布门禁整理出正式交替点后进行反转标注，保留候选各自原始证据。
        candidate_strokes.append(dict(id='tertiary-'+source['id'],source_path=source['id'],points=candidates))
        points=publish_uptrends(candidates,source['points'],bars[:end_index+1],source_level=2,
                               qualified_source=public_source,
                               n_target_trend_confirmation_enabled=n_target_trend_confirmation_enabled)
        if not points:
            continue
        _annotate(points,bars[0].symbol,dates,trend_level=3)
        for p in points:
            levels=p.get('levels'); broken_key=p.get('broken_key')
            if not isinstance(levels,list) or not isinstance(broken_key,Mapping):
                raise ValueError('annotated trend points require levels and a frozen key')
            key_name=('已突破的旧三级'+('末跌高' if broken_key['kind']=='H' else '末升低')
                      if p.get('upgrade_evidence') is not None else
                      '二级'+('末升低' if p['kind']=='H' else '末跌高'))
            levels.insert(0,dict(name=key_name,price=broken_key['value']))
        strokes.append(dict(id='tertiary-'+source['id'],source_path=source['id'],kind='tertiary',
                            trend_level=3,points=points,input_turn_count=len(source['points']),confirmation_policy='trend_routes_v110',
                            n_target_trend_confirmation_enabled=n_target_trend_confirmation_enabled,
                            leg_confirmation_policy=OPTIONAL_LEG_CONFIRMATION_POLICY if n_target_trend_confirmation_enabled else LEG_CONFIRMATION_POLICY,
                            wave_display_policy=WAVE_DISPLAY_POLICY,
                            confirmed_legs=confirmed_trend_legs(points,trend_level=3,source_path=source['id'])))
        tail=hierarchical_developing_path(source,points,trend_level=3,source_level=2,kind='tertiary',
                                         bars=bars,end_index=end_index,structural=candidates,qualified_source_points=public_source,
                                         n_target_trend_confirmation_enabled=n_target_trend_confirmation_enabled)
        if tail:
            developing_strokes.append(tail)
    return dict(name='三级趋势线',trend_level=3,source_level=2,strokes=strokes,
                bear_to_bull_highs=bear_to_bull_highs(strokes,trend_level=3),
                bear_bull_alternation_lows=bear_bull_alternation_lows(strokes,trend_level=3,source_strokes=level2['strokes'],bars=bars),
                post_alternation_bull_highs=post_alternation_bull_highs(strokes,trend_level=3,source_strokes=level2['strokes'],bars=bars),
                bullish_turn_signals=bullish_turn_signals(strokes,bars,trend_level=3,source_strokes=level2['strokes']),
                developing_strokes=developing_strokes,candidate_strokes=candidate_strokes,structure_strokes=candidate_strokes,
                n_target_observations=n_targets,
                n_target_trend_confirmation_enabled=n_target_trend_confirmation_enabled,
                input_turn_count=sum(len(s['points']) for s in level2['strokes']),
                confirmed_wave_count=sum(len(s['points']) for s in strokes),
                developing_wave_count=len(developing_strokes),
                developing_point_count=sum(len(s['points']) for s in developing_strokes),
                aggregation_rule=('same_level_key_break_or_lower_level_break_alternation_turn_or_n_strict_one_p'
                                  if n_target_trend_confirmation_enabled else
                                  'same_level_key_break_or_lower_level_break_alternation_turn'),scope='lecture_level3_not_strategy_confirmation',
                trend_definition_rule='shared_six_definitions_and_strict_same_level_extreme_upgrade',
                note='六个定义与一级、二级共用，本级只读取正式二级趋势点；'
                     '同类高低点均严格升/降才成立多头/空头定义，局部正负反转不单独改变趋势方向；'
                     '已确认二级高点或低点严格突破此前已知三级同类极点时直接升级，不等待后续回调；'
                     '上涨须突破三级末跌高，或二级末跌高突破、空多交替、后续收盘转多完整证据；'
                     +('二级来源正N/倒N完成后严格超过攻击箱测算的一饱，也独立确认三级对应趋势；'
                       if n_target_trend_confirmation_enabled else '')+
                     '市场最高价突破已知同级前高或最低价跌破已知同级前低时，趋势线立即确认并画实线，末端继续延伸；'
                     '没有新同级突破时，反向虚线须有冻结的二级关键位突破、交替、后续收盘转向完整证据；'
                     '最后一个正式三级点之后的已确认二级演化另作纯显示发展路径，不进入正式点、策略或回测；'
                     '仅突破下级自身关键位不单独升级；突破本级已知关键位直接确认，不跨断点。')
