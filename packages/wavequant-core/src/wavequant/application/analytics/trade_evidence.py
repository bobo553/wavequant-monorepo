"""Attach causal decisions to an execution ledger without inventing fills."""
from collections import Counter


def enrich_ledger(bars, result, generated, strategy):
    by_time={b.timestamp.date().isoformat(): b for b in bars}
    signals={(s.timestamp.isoformat(),s.side):s for s in generated.signals}
    audit=getattr(generated,'audit',[])
    dated={}
    for event in audit:
        dated.setdefault(event['timestamp'],[]).append(event)
    active=None; serial=0
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
                            'origin_index','peak_index','minimum_close_index','eligibility_frozen_at'):
                    if isinstance(item.get(key),int) and 0<=item[key]<=signal.bar_index:
                        item[key+'_date']=bars[item[key]].timestamp.date().isoformat()
                evidence.append(item)
        order['decision_evidence']=evidence
        if order['side']=='BUY' and signal is not None:
            proof=next((e for e in evidence if e['event']=='long_transition_evidence'),None)
            def check(name,actual,required,passed):
                return dict(name=name,actual=actual,required=required,passed=passed)
            chain_ok=bool(proof) and all(isinstance(proof.get(k),int) for k in
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
                check('开盘费用后盈亏比',order.get('net_reward_risk'),signal.minimum_reward_risk,
                      order.get('net_reward_risk',-1)>=signal.minimum_reward_risk
                      if order.get('net_reward_risk') is not None else None)]
            if strategy.get('entry_policy')=='hierarchical_two_buy_points':
                second=bool(proof) and proof.get('buy_point_type')=='mature_shallow_squeeze'
                chain=bool(proof) and proof['flip_index']<=proof['alternation_index']<proof['attack']<=signal.bar_index
                if second:
                    chain=chain and (proof['alternation_index']<proof['maturity_index']<=proof['impulse_high_index']
                        <proof['pullback_index']<proof['attack'])
                order['entry_conditions'][0]=check('分级双买点证据',proof,
                    '交替 → 再破翻多高 → 浅回撤 → 新 N → 轧空' if second else '翻多 → 交替 → 新 N → 轧空（不限制回撤比例）',chain)
                order['entry_conditions'][2]=check('成熟多头回档比例' if second else '第一类回档比例（仅展示）',
                    signal.retracement,strategy.get('mature_shallow_ratio',1/3) if second else '不启用比例过滤',
                    signal.retracement<strategy.get('mature_shallow_ratio',1/3) if second else None)
                if proof and proof.get('definition')=='whole_flip_wave_v3':
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
            order['trigger_timestamp']=signal.trigger_timestamp.isoformat()
        if order['status']=='filled':
            if order['side']=='BUY': serial+=1;active=f'{bar.symbol}-trade-{serial}'
            order['trade_id']=active
            if order['side']=='SELL' and order.get('position_closed', True): active=None
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
        evidence=[e for e in result.get('audit',[]) if e['timestamp']==s['timestamp'] and e['event']=='long_transition_evidence']
        markers.append(dict(id=f'stock-signal-{i}',time=s['time'],kind='signal',side=s['side'],
            price=s['reference_price'],reason=s['reason'],regime=s['regime'],rvol=s['rvol'],
            stop=s['invalidation_price'] if s['side']=='LONG' else None,
            target=s['target_price'] if s['side']=='LONG' else None,source='single_stock_backtest',decision_evidence=evidence))
    return sorted(markers,key=lambda r:r['time'])
