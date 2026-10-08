"""Attach causal decisions to an execution ledger without inventing fills."""
from collections import Counter
from typing import Any


def enrich_ledger(bars, result, generated, strategy):
    by_time={b.timestamp.date().isoformat(): b for b in bars}
    signals={(s.timestamp.isoformat(),s.side):s for s in generated.signals}
    audit=getattr(generated,'audit',[])
    dated: dict[str, list[dict[str, Any]]] = {}
    for event in audit:
        dated.setdefault(event['timestamp'],[]).append(event)
    active={}; serial=0
    holding_by_cycle = {(e['symbol'], e.get('cycle_index')): e for e in getattr(result, 'holding_drawdowns', [])}
    cycles: Counter[str] = Counter()
    quantities: dict[str, float] = {}
    for order in result.orders:
        bar=by_time[order['timestamp'][:10]]
        order.update(price_basis='causal_adjusted_equivalent',adjustment_factor=bar.adjustment_factor)
        if order.get('price') is not None: order['raw_price']=order['price']/bar.adjustment_factor
        if order.get('quantity') is not None: order['raw_shares']=order['quantity']*bar.adjustment_factor
        stamp=order.get('signal_timestamp')
        signal=signals.get((stamp,'LONG' if order['side']=='BUY' else 'EXIT'))
        evidence=[]
        if signal is not None:
            for event in dated.get(stamp,[]):
                if event['event'] not in ('long_signal','long_transition_evidence','exit_signal'): continue
                if max(event['bar_index'],event.get('known_at',event['bar_index']))>signal.bar_index: continue
                item=dict(event)
                for key in ('context_index','key_source_index','flip_index','alternation_index',
                            'bullish_index','bullish_confirmation_index','attack','maturity_index',
                            'flip_high_index','alternation_low_index','pullback_index','impulse_high_index','impulse_origin_index',
                            'origin_index','peak_index','minimum_close_index','eligibility_frozen_at',
                            'key_known_index','higher_breakout_index','higher_confirmation_index',
                            'candidate_known_index','base_start_index','base_end_index'):
                    if isinstance(item.get(key),int) and 0<=item[key]<=signal.bar_index:
                        item[key+'_date']=bars[item[key]].timestamp.date().isoformat()
                evidence.append(item)
        order['decision_evidence']=evidence
        if order['side']=='BUY' and signal is not None:
            proof=next((e for e in evidence if e['event']=='long_transition_evidence'),None)
            def check(name,actual,required,passed):
                return dict(name=name,actual=actual,required=required,passed=passed)
            chain_ok=proof is not None and bool(proof) and all(isinstance(proof.get(k),int) for k in
                ('flip_index','alternation_index','bullish_index','attack')) and (
                proof['flip_index']<proof['alternation_index']<=proof['bullish_index']<proof['attack']<=signal.bar_index)
            order['entry_conditions']=[
                check('趋势交替证据',proof,'翻空为多 → 空多交替 → 多头确认 → 新 N 攻击',
                      chain_ok if strategy.get('entry_policy','transitioned_squeeze')=='transitioned_squeeze' else None),
                check('入场盘态',signal.regime,'轧空 / 强轧空',signal.regime in ('轧空','强轧空')),
                check('回档比例',signal.retracement,strategy.get('max_counter_ratio',2/3),
                      signal.retracement<strategy.get('max_counter_ratio',2/3)),
                check('攻击相对量',signal.rvol,strategy.get('minimum_rvol',1.2),
                      signal.rvol is not None and signal.rvol>=strategy.get('minimum_rvol',1.2)
                      if strategy.get('volume_filter',True) else None),
                check('成交价费用后盈亏比',order.get('net_reward_risk'),signal.minimum_reward_risk,
                      order.get('net_reward_risk',-1)>=signal.minimum_reward_risk
                      if order.get('net_reward_risk') is not None and order.get('net_reward_risk_filter',True) else None)]
            if strategy.get('buy_point_definition') == 'whole_flip_wave_v3':
                volume_proof = next((e for e in evidence if e['event'] == 'long_signal'), {})
                price_alternative = volume_proof.get('wave_gap_trigger') in ('breakout', 'breakout_and_volume')
                order['entry_conditions'][3] = check(
                    '确认时量 / 前日量', signal.rvol,
                    '跳空突破回调折线高点或成交量 > 前日全天量' if price_alternative else '> 1（前日全天量）',
                    volume_proof.get('volume_pass', False) if strategy.get('volume_filter', True) else None)
            if strategy.get('entry_policy')=='hierarchical_two_buy_points':
                second=proof is not None and bool(proof) and proof.get('buy_point_type')=='mature_shallow_squeeze'
                chain=proof is not None and bool(proof) and all(isinstance(proof.get(k),int) for k in ('flip_index','alternation_index')) and proof['flip_index']<=proof['alternation_index']<proof['attack']<=signal.bar_index
                if second and proof is not None:
                    chain=chain and (proof['alternation_index']<proof['maturity_index']<=proof['impulse_high_index']
                        <proof['pullback_index']<proof['attack'])
                order['entry_conditions'][0]=check('分级双买点证据',proof,
                    '交替 → 再破翻多高 → 浅回撤 → 新 N → 轧空' if second else '翻多 → 交替 → 新 N → 轧空（不限制回撤比例）',chain)
                order['entry_conditions'][2]=check('成熟多头回档比例' if second else '第一类回档比例（仅展示）',
                    signal.retracement,strategy.get('mature_shallow_ratio',1/3) if second else '不启用比例过滤',
                    signal.retracement<strategy.get('mature_shallow_ratio',1/3) if second else None)
                if proof and proof.get('definition')=='whole_flip_wave_v3':
                    if not second and proof.get('joint_alternation_confirmation'):
                        chain=(proof.get('trend_level') in (2,3)
                            and proof.get('confirmation_attack')==proof['attack']
                            and proof['flip_index']<=proof['attack']<proof['alternation_index']<=signal.bar_index)
                    from wavequant.domain.strategies.whole_wave_entry import threshold
                    from fractions import Fraction
                    ratio=Fraction(proof['counter_exact_ratio'])
                    limit=threshold(proof['counter_limit']) if proof['counter_limit'] is not None else None
                    order['entry_conditions'][0]=check('分级双买点证据',proof,
                        '交替 → 收盘再破 H0 → 阶段最高 H1 → 收盘浅回撤 → 正 N → 轧空' if second else
                        '二级或三级空多交替 → 新正 N → 轧空 / 强轧空（攻击时冻结类别）',chain)
                    order['entry_conditions'][2]=check('整段收盘回撤' if second else
                        '交替回撤（仅展示）' if limit is None else
                        '交替收盘深回撤' if proof['counter_basis']=='minimum_close_from_flip_high_to_alternation' else
                        '交替深回撤',
                        proof['counter_ratio'],f"{proof['counter_operator']} {proof['counter_limit']}" if limit is not None else '不附加深回撤过滤',
                        None if limit is None else (ratio<=limit if proof['counter_operator']=='<=' else
                         ratio<limit if proof['counter_operator']=='<' else ratio>limit))
                if proof and proof.get('buy_point_type')=='multilevel_breakout_squeeze':
                    chain=(proof['key_known_index']<proof['attack']<proof['higher_confirmation_index']<=signal.bar_index)
                    order['entry_conditions'][0]=check('分级双买点证据',proof,
                        '新正N突破已知二/三级波段高 → 守防守、放量收盘突破抵抗阶段高 → 双重轧空',chain)
                    order['entry_conditions'][2]=check('局部N回撤（仅展示）',signal.retracement,'不附加交替回撤过滤',None)
                if proof and proof.get('buy_point_type')=='nested_alternation_breakout':
                    chain=(proof['secondary_low_index']<proof['primary_low_index']
                        and proof['secondary_known_index']<proof['primary_known_index']<signal.bar_index
                        and proof['primary_low_index']==proof['alternation_low_index'])
                    order['entry_conditions'][0]=check('二级后一级交替低点',proof,
                        '已确认二级交替低点 → 已确认一级交替低点 → 放量阳线突破（当日正N可入场）',chain)
                    order['entry_conditions'][1]=check('放量阳线突破',proof['confirmation_close'],
                        f"> {proof['breakout_high']}（一级翻多高及此前整理高）",
                        proof['confirmation_close']>proof['breakout_high'])
                    order['entry_conditions'][2]=check('一级交替回撤（仅展示）',
                        signal.retracement,'不附加回撤比例过滤，仍须守住两级低点',None)
                if proof and proof.get('buy_point_type')=='shallow_base_breakout':
                    ratio=proof['counter_ratio']
                    chain=(proof['origin_index']<proof['flip_high_index']<proof['alternation_low_index']
                        and proof['candidate_known_index']<proof['attack']==signal.bar_index)
                    order['entry_conditions'][0]=check('浅回撤交替待选',proof,
                        '已确认高低锚点 → 来源级回撤低点待选 → 横盘突破',chain)
                    order['entry_conditions'][1]=check('横盘突破',proof['breakout_close'],
                        f"> {proof['base_high']}（此前区间高）",proof['breakout_close']>proof['base_high'])
                    order['entry_conditions'][2]=check('待选回撤比例',ratio,'≥ 0.618 且 < 2/3',
                        0.618<=ratio<2/3)
                    order['entry_conditions'][3]=check('突破量 / 前20日均量',proof['breakout_volume_multiple'],
                        '≥ 2 且 > 前日量',proof['breakout_volume_multiple']>=2
                        and proof['breakout_volume']>proof['previous_volume'])
                if proof and proof.get('buy_point_type')=='secondary_resistance_reclaim':
                    gap = proof['secondary_reclaim_type'] == 'gap'
                    body = proof['secondary_reclaim_body_fraction']
                    order['entry_conditions'][0]=check('已知二级突破抵抗',proof,
                        '已确认二级高 → 突破当笔或次笔空头抵抗 → 后续放量收复',
                        proof['secondary_high_known_date']<proof['secondary_attack_date']
                        and proof['secondary_resistance_known_date']<bar.timestamp.date().isoformat())
                    order['entry_conditions'][1]=check('跳空中大阳收复' if gap else '阳线实体收复',
                        proof['secondary_reclaim_close'],f"> {proof['secondary_resistance_high']}",
                        proof['secondary_reclaim_close']>proof['secondary_resistance_high']
                        and (body>=.03 and proof['secondary_reclaim_body_range_fraction']>=.6
                             and proof['secondary_reclaim_unfilled_gap'] if gap else body>.02))
                    order['entry_conditions'][2]=check('回调防守',proof['secondary_pullback_low'],
                        f"≥ 原起点 {proof['secondary_origin_low']}",
                        proof['secondary_pullback_low']>=proof['secondary_origin_low'])
                    order['entry_conditions'][3]=check('放量 / 前日量',signal.rvol,'> 1（必需）',
                        proof['previous_volume']>0 and proof['breakout_volume']>proof['previous_volume'])
                if proof and proof.get('buy_point_type')=='combined_a_pullback_breakout':
                    sessions = proof['combined_a_pullback_sessions']
                    internal = proof['combined_a_internal_pullback_sessions']
                    child = proof.get('combined_a_child_pullback_sessions')
                    order['entry_conditions'][0]=check('已确认组合A',proof,
                        '已知同源ABC → C顶后重叠回调 → 放量突破',
                        proof['combined_a_known_date']<bar.timestamp.date().isoformat())
                    order['entry_conditions'][1]=check('中大阳线突破',proof['confirmation_close'],
                        f"> {proof['combined_a_breakout_price']}，实体≥开盘3%且≥振幅60%",
                        proof['confirmation_close']>proof['combined_a_breakout_price']
                        and proof['breakout_body_pct']>=0.03 and proof['breakout_body_ratio']>=0.6)
                    order['entry_conditions'][2]=check('回调收盘守2/3',proof['combined_a_minimum_close'],
                        f"≥ {proof['combined_a_two_thirds_price']}",
                        proof['combined_a_minimum_close']>=proof['combined_a_two_thirds_price'])
                    order['entry_conditions'][3]=check('放量 / 前日量',signal.rvol,'> 1（必需）',
                        proof['previous_volume']>0 and proof['breakout_volume']>proof['previous_volume'])
                    order['entry_conditions'].append(check('回调及整理交易日',sessions,
                        f"> 内部回调{internal}日" +
                        (f" 或 子级回调{child}日" if child is not None else "（子级回调时长未提供）"),
                        sessions>internal or (child is not None and sessions>child)))
            order['trigger_timestamp']=signal.trigger_timestamp.isoformat()
        if order['status']=='filled':
            if order['side']=='BUY' and bar.symbol not in active:
                serial+=1
                active[bar.symbol]=f'{bar.symbol}-trade-{serial}'
                cycles[bar.symbol] += 1
                holding = holding_by_cycle.get((bar.symbol, cycles[bar.symbol]))
                if holding is not None:
                    holding['trade_id'] = active[bar.symbol]
            order['trade_id']=active.get(bar.symbol)
            amount = order.get('quantity')
            if isinstance(amount, (int, float)):
                quantities[bar.symbol] = max(0.0, quantities.get(bar.symbol, 0.0)
                                             + (amount if order['side'] == 'BUY' else -amount))
            closed = quantities.get(bar.symbol, 0.0) < 1e-8 if amount is not None else order.get('position_closed', True)
            if order['side']=='SELL' and closed:
                active.pop(bar.symbol,None)
                quantities[bar.symbol] = 0.0
    for position in result.open_positions:
        if position['symbol'] in active:
            position['trade_id']=active[position['symbol']]
    rejected=Counter(e.get('reason','unknown') for e in audit
                     if e['event'] in ('entry_rejected','entry_preflight_rejected'))
    return dict(rejection_reasons=dict(rejected),events=Counter(e['event'] for e in audit))


def result_markers(result):
    markers=[]
    for i,o in enumerate(result['orders']):
        markers.append(dict(o,id=f'stock-order-{i}',time=o['timestamp'][:10],
            kind='fill' if o['status']=='filled' else 'order',stop=o.get('stop_price'),
            target=o.get('target_price'),signal_time=o.get('signal_timestamp','')[:10],
            source='single_stock_backtest'))
    for i,s in enumerate(result['signals']):
        dated=[e for e in result.get('audit',[]) if e['timestamp']==s['timestamp']]
        evidence=[e for e in dated if e['event']=='long_transition_evidence']
        evidence.extend(e for e in dated if e['event']=='long_signal'
                        and e.get('channel') and s['reason']=='system_'+e['channel'])
        markers.append(dict(id=f'stock-signal-{i}',time=s['time'],kind='signal',side=s['side'],
            price=s['reference_price'],reason=s['reason'],regime=s['regime'],rvol=s['rvol'],
            stop=s['invalidation_price'] if s['side']=='LONG' else None,
            target=s['target_price'] if s['side']=='LONG' else None,source='single_stock_backtest',decision_evidence=evidence))
    return sorted(markers,key=lambda r:r['time'])
