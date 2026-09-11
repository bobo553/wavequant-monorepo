"""Paper-only signal -> raw-share order boundary with explicit price basis.

Call once per observed opening, after publishing a fresh raw quote. The caller
owns the exchange calendar and must expire stale entry opportunities explicitly.
"""
from decimal import ROUND_CEILING, ROUND_FLOOR
from .event_store import utc
from .order_service import dec, money, reserved_cash, session


class PaperIntentExecutor:
    def __init__(self, runtime, oms):
        if runtime.store is not oms.store: raise ValueError('intent and order must share atomic journal')
        self.runtime,self.oms=runtime,oms

    def execute(self, intent_id, *, when, raw_open, adjustment_factor, fee_budget='20',
                slippage_bps='5', max_entry_gap='.05', expires_on):
        store=self.oms.store
        event=store.get(intent_id)
        if event is None or event['kind']!='SIGNAL_INTENT': raise ValueError('unknown intent')
        if event['payload']['strategy_version']!=self.runtime.version: raise ValueError('wrong strategy version')
        ack=store.get('ack:'+intent_id)
        if ack: return ack['payload']
        if utc(when)<=event['event_time']: raise ValueError('execution must follow observable signal')
        factor,opening,slip=dec(adjustment_factor),dec(raw_open),dec(slippage_bps)/10000
        if factor<=0 or opening<=0 or not 0<=slip<1 or money(fee_budget)<0 or dec(max_entry_gap)<0:
            raise ValueError('invalid explicit execution assumptions')
        from datetime import date
        date.fromisoformat(expires_on)
        signal=event['payload']; oid='paper:'+intent_id
        old=store.get('order:'+oid)
        if old:
            self.runtime.acknowledge(intent_id,when,disposition='ORDERED',order_id=oid)
            return store.get('ack:'+intent_id)['payload']
        prior_rejections=[e for e in store.events() if e['kind']=='INTENT_REJECTED'
                          and e['payload']['intent_id']==intent_id]
        if signal['side']=='LONG' and prior_rejections:
            self.runtime.acknowledge(intent_id,when,disposition='FILTERED')
            return dict(disposition='FILTERED',reason=prior_rejections[0]['payload']['reason'])
        if signal['side']=='LONG' and session(when)>expires_on:
            self.runtime.acknowledge(intent_id,when,disposition='EXPIRED')
            return store.get('ack:'+intent_id)['payload']
        if session(when)<=session(signal['timestamp']): raise ValueError('daily signal requires later session')
        try:
            fact=self.oms.master.at(signal['symbol'],session(when),when)
            if fact is None: raise ValueError('unknown_point_in_time_security')
            tick=dec(fact.tick_size); buy=signal['side']=='LONG'
            raw_price=opening*(1+slip if buy else 1-slip)
            price=(raw_price/tick).to_integral_value(rounding=ROUND_CEILING if buy else ROUND_FLOOR)*tick
            stop=dec(signal['invalidation_price'])/factor
            state=self.oms.state()
            if buy:
                reference=dec(signal['reference_price'])/factor
                if abs(opening/reference-1)>dec(max_entry_gap): raise ValueError('entry_gap_limit')
                if price<=stop: raise ValueError('entry_below_invalidation')
                h=self.oms.health(when)
                if h['equity'] is None or not h['healthy']: raise ValueError('account_health_gate')
                equity=dec(h['equity']); fee=money(fee_budget)
                risk=max(dec(0),equity*dec(self.oms.limits.max_order_risk)-fee)
                cash=max(dec(0),state['cash']-reserved_cash(state)-fee)
                current=state['positions'].get(signal['symbol'],0)*opening
                symbol_room=max(dec(0),equity*dec(self.oms.limits.max_symbol_weight)-current)
                q=int(min(risk/(price-stop),cash/price,symbol_room/price)/fact.buy_lot)*fact.buy_lot
                if q<=0: raise ValueError('risk_budget_below_one_lot')
                minimum=dec(signal.get('minimum_reward_risk',0))
                if minimum>0:
                    if signal.get('target_price') is None: raise ValueError('missing_structural_target')
                    target=dec(signal['target_price'])/factor
                    reward=(target-price)*q-fee*2
                    risk_cost=(price-stop)*q+fee*2
                    if reward/risk_cost<minimum: raise ValueError('net_reward_risk_below_threshold')
            else:
                q=state['positions'].get(signal['symbol'],0)
                if not q:
                    self.runtime.acknowledge(intent_id,when,disposition='RISK_EXIT_OBSERVED')
                    return store.get('ack:'+intent_id)['payload']
            self.oms.submit(oid,signal['symbol'],'BUY' if buy else 'SELL',q,price,stop,when,
                            signal_at=signal['timestamp'],fee_budget=fee_budget)
        except ValueError as exc:
            # Entry is one opportunity; rejected exits remain pending for the
            # next tradable opening, while the failure evidence is durable.
            with store.transaction():
                store.append('intent-reject:'+intent_id+':'+utc(when),'INTENT_REJECTED',when,
                             dict(intent_id=intent_id,reason=str(exc)))
            if signal['side']=='LONG': self.runtime.acknowledge(intent_id,when,disposition='FILTERED')
            return dict(disposition='FILTERED' if signal['side']=='LONG' else 'DEFERRED',reason=str(exc))
        self.runtime.acknowledge(intent_id,when,disposition='ORDERED',order_id=oid)
        return store.get('ack:'+intent_id)['payload']
