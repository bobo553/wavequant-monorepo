"""Model a deterministic paper venue and recoverable local dispatch bridge.

Only next opening observations are matched. No intrabar high/low inference,
real broker connection, or claim that this model reproduces actual liquidity.
"""
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR
from typing import Protocol

from wavequant.infrastructure.persistence.event_store import utc
from .order_service import ACTIVE, dec, money


@dataclass(frozen=True)
class OpeningTick:
    tick_id: str
    symbol: str
    when: str
    price: str
    available_volume: int
    buyable: bool
    sellable: bool

    def __post_init__(self):
        utc(self.when)
        if not self.tick_id or not self.symbol or dec(self.price)<=0:
            raise ValueError('identified positive raw-price observation required')
        if type(self.available_volume) is not int or self.available_volume<0:
            raise ValueError('nonnegative observed raw-share capacity required')
        if type(self.buyable) is not bool or type(self.sellable) is not bool:
            raise ValueError('explicit tradability required')


class BrokerAdapter(Protocol):
    def accept(self, order_event): ...
    def snapshot(self): ...
    def reports(self): ...


class PaperVenue:
    def __init__(self, store, *, capital='1000000', opened_at, commission_rate='.0003',
                 minimum_fee='5', sell_tax_rate='.0005', slippage_bps='5'):
        self.store=store
        self.settings=dict(capital=str(money(capital)),commission_rate=str(dec(commission_rate)),
            minimum_fee=str(money(minimum_fee)),sell_tax_rate=str(dec(sell_tax_rate)),
            slippage_bps=str(dec(slippage_bps)),mode='synthetic_paper_only')
        if dec(capital)<=0 or any(dec(self.settings[k])<0 for k in self.settings if k not in ('capital','mode')):
            raise ValueError('invalid venue settings')
        with store.transaction():
            old=store.get('venue:open')
            if old and old['payload']!=self.settings: raise ValueError('venue settings changed')
            if not old: store.append('venue:open','VENUE_OPENED',opened_at,self.settings)

    def orders(self):
        orders={}
        for e in self.store.events():
            p=e['payload']
            if e['kind']=='VENUE_ACCEPTED':
                orders[p['order_id']]={**p,'filled':0,'status':'ACCEPTED'}
            elif e['kind']=='VENUE_FILL':
                o=orders[p['order_id']]; o['filled']+=p['quantity']
                o['status']='FILLED' if o['filled']==o['quantity'] else 'PARTIAL'
            elif e['kind']=='VENUE_CANCELED': orders[p['order_id']]['status']='CANCELED'
            elif e['kind']=='VENUE_REJECTED': orders[p['order_id']]['status']='REJECTED'
        return orders

    def accept(self, order_event):
        if order_event['kind']!='ORDER_ACCEPTED': raise ValueError('accepted local order required')
        p=dict(order_event['payload'],accepted_at=order_event['event_time'])
        with self.store.transaction():
            self.store.append('venue:order:'+p['order_id'],'VENUE_ACCEPTED',order_event['event_time'],p)

    def match(self, tick):
        """Commit venue fills first; a crash before local ingestion is recoverable."""
        from dataclasses import asdict
        with self.store.transaction():
            key='venue:tick:'+tick.tick_id
            old=self.store.get(key)
            if old:
                self.store.append(key,'VENUE_TICK',tick.when,asdict(tick))
                return [e for e in self.reports() if e['payload'].get('tick_id')==tick.tick_id]
            prior=[e['event_time'] for e in self.store.events() if e['kind']=='VENUE_TICK' and e['payload']['symbol']==tick.symbol]
            if prior and utc(tick.when)<=max(prior): raise ValueError('out_of_order_venue_tick')
            self.store.append(key,'VENUE_TICK',tick.when,asdict(tick))
            remaining=tick.available_volume
            fills=[]
            for oid,o in self.orders().items():
                if o['symbol']!=tick.symbol or o['status'] not in ACTIVE: continue
                # The market observation must strictly follow the causal signal
                # and cannot precede order acceptance.
                if utc(tick.when)<o['accepted_at'] or utc(tick.when)<=o['signal_at']: continue
                buy=o['side']=='BUY'
                if not (tick.buyable if buy else tick.sellable): continue
                slip=dec(self.settings['slippage_bps'])/10000
                step=dec(o['tick_size'])
                raw=dec(tick.price)*(1+slip if buy else 1-slip)
                price=(raw/step).to_integral_value(rounding=ROUND_CEILING if buy else ROUND_FLOOR)*step
                if price<=0 or (buy and price>dec(o['limit_price'])) or (not buy and price<dec(o['limit_price'])): continue
                q=min(remaining,o['quantity']-o['filled'])
                if q<=0: continue
                value=money(price*q)
                fee=money(max(dec(self.settings['minimum_fee']),value*dec(self.settings['commission_rate']))
                          +(0 if buy else value*dec(self.settings['sell_tax_rate'])))
                p=dict(order_id=oid,quantity=q,price=str(price),fee=str(fee),tick_id=tick.tick_id)
                e=self.store.append('venue:fill:'+tick.tick_id+':'+oid,'VENUE_FILL',tick.when,p)
                fills.append(e); remaining-=q
            return fills

    def cancel(self, order_id, when):
        with self.store.transaction():
            key='venue:cancel:'+order_id
            if self.store.get(key):
                self.store.append(key,'VENUE_CANCELED',when,dict(order_id=order_id)); return
            o=self.orders().get(order_id)
            if not o or o['status'] not in ACTIVE: raise ValueError('venue order is terminal or unknown')
            prior=[e['event_time'] for e in self.store.events() if e['payload'].get('order_id')==order_id]
            if utc(when)<max(prior): raise ValueError('cancel precedes venue activity')
            self.store.append(key,'VENUE_CANCELED',when,dict(order_id=order_id))

    def reports(self):
        return [e for e in self.store.events() if e['kind'] in ('VENUE_FILL','VENUE_CANCELED','VENUE_REJECTED')]

    def reject(self, order_id, when, *, reason):
        if not isinstance(reason,str) or not reason.strip(): raise ValueError('rejection reason required')
        with self.store.transaction():
            key='venue:reject:'+order_id
            payload=dict(order_id=order_id,reason=reason)
            if self.store.get(key):
                self.store.append(key,'VENUE_REJECTED',when,payload); return
            o=self.orders().get(order_id)
            if not o or o['status'] not in ACTIVE: raise ValueError('venue order is terminal or unknown')
            prior=[e['event_time'] for e in self.store.events() if e['payload'].get('order_id')==order_id]
            if utc(when)<max(prior): raise ValueError('rejection precedes venue activity')
            self.store.append(key,'VENUE_REJECTED',when,payload)

    def corporate_action(self, action_id, symbol, share_ratio, net_cash, when, *, known_at, source):
        """Independent explicit entitlement application, not inferred from prices.

        Caller separately sends the same verified entitlement to OMS then
        reconciles. Pending orders, fractional share lots and backwards account
        mutations are rejected. No rights subscriptions or tax calculation.
        """
        ratio,cash=dec(share_ratio),money(net_cash)
        if ratio<=0 or cash<0 or utc(known_at)>utc(when) or not source:
            raise ValueError('explicit known corporate-action entitlements required')
        payload=dict(symbol=symbol,share_ratio=str(ratio),net_cash=str(cash),known_at=utc(known_at),source=source)
        with self.store.transaction():
            key='venue:action:'+action_id
            if self.store.get(key):
                self.store.append(key,'VENUE_ACTION',when,payload); return
            if any(o['symbol']==symbol and o['status'] in ACTIVE for o in self.orders().values()):
                raise ValueError('resolve_open_orders_before_corporate_action')
            if utc(when)<max(e['event_time'] for e in self.store.events()):
                raise ValueError('backdated venue corporate action')
            for lot in self._ledger()[2]:
                if lot['symbol']==symbol and lot['quantity']*ratio!=int(lot['quantity']*ratio):
                    raise ValueError('fractional_entitlement_requires_external_resolution')
            self.store.append(key,'VENUE_ACTION',when,payload)

    def _ledger(self):
        cash=dec(self.settings['capital']); positions={}; lots=[]; orders=self.orders()
        for e in self.store.events():
            p=e['payload']
            if e['kind']=='VENUE_ACTION':
                symbol=p['symbol']; ratio=dec(p['share_ratio'])
                cash+=dec(p['net_cash'])
                positions[symbol]=int(positions.get(symbol,0)*ratio)
                for lot in lots:
                    if lot['symbol']==symbol: lot['quantity']=int(lot['quantity']*ratio)
            elif e['kind']=='VENUE_FILL':
                o=orders[p['order_id']]; sign=1 if o['side']=='BUY' else -1
                cash-=sign*money(dec(p['price'])*p['quantity'])+dec(p['fee'])
                positions[o['symbol']]=positions.get(o['symbol'],0)+sign*p['quantity']
                if sign==1: lots.append(dict(symbol=o['symbol'],quantity=p['quantity']))
                else:
                    remaining=p['quantity']
                    for lot in lots:
                        if lot['symbol']!=o['symbol']: continue
                        used=min(remaining,lot['quantity']); remaining-=used; lot['quantity']-=used
                        if not remaining: break
                    if remaining: raise ValueError('independent venue ledger has negative shares')
        return cash,positions,lots

    def snapshot(self):
        # Deliberately independent ledger implementation; no OMS project() reuse.
        cash,positions,_=self._ledger(); orders=self.orders()
        return dict(cash=str(cash),positions={s:q for s,q in positions.items() if q},
            open_orders={oid:dict(status=o['status'],quantity=o['quantity'],filled=o['filled'])
                         for oid,o in orders.items() if o['status'] in ACTIVE})


class PaperBridge:
    def __init__(self, oms, venue): self.oms,self.venue=oms,venue

    def dispatch(self):
        # Stable IDs turn local accepted-order events into a recoverable outbox.
        # This retry policy is valid for THIS idempotent local venue only.
        for e in self.oms.store.events():
            if e['kind']=='ORDER_ACCEPTED': self.venue.accept(e)
            elif e['kind']=='ORDER_CANCEL_REQUESTED':
                o=self.venue.orders().get(e['payload']['order_id'])
                # A fill may win the cancel race; deliver its real outcome in
                # receive(), do not fabricate a successful cancellation.
                if o and o['status'] in ACTIVE:
                    self.venue.cancel(e['payload']['order_id'],e['event_time'])

    def receive(self):
        for e in self.venue.reports():
            p=e['payload']
            try:
                if e['kind']=='VENUE_FILL':
                    self.oms.fill(e['event_id'],p['order_id'],p['quantity'],p['price'],p['fee'],e['event_time'])
                elif e['kind']=='VENUE_CANCELED': self.oms.cancel(p['order_id'],e['event_time'],confirmed=True)
                else: self.oms.reject(p['order_id'],e['event_time'],reason=p['reason'])
            except ValueError as exc:
                self.oms.halt(e['event_time'],str(exc),event_id='venue-report-incident:'+e['event_id'])
                raise

    def reconcile(self, when, *, event_id):
        return self.oms.reconcile(self.venue.snapshot(),when,event_id=event_id)
