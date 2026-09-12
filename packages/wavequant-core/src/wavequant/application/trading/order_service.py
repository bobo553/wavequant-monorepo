"""Coordinate a durable single-account paper OMS and raw-share cash ledger.

No live adapter is enabled. Financial mutations are validated and journaled in
one SQLite transaction. Projections are rebuilt from events, not trusted caches.
"""
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from wavequant.infrastructure.persistence.event_store import canonical, utc


def dec(value):
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError('finite numeric value required')
    return result


def money(value):
    return dec(value).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)


def session(when):
    return datetime.fromisoformat(utc(when)).astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat()


@dataclass(frozen=True)
class PortfolioLimits:
    max_positions: int = 5
    max_symbol_weight: str = '.20'
    max_sector_weight: str = '.40'
    max_gross_weight: str = '.80'
    max_order_risk: str = '.005'
    max_drawdown: str = '.20'
    max_quote_age_seconds: int = 86400

    def __post_init__(self):
        for key in ('max_symbol_weight','max_sector_weight','max_gross_weight','max_order_risk','max_drawdown'):
            if not 0 < dec(getattr(self,key)) <= 1:
                raise ValueError('risk fractions must be in (0,1]')
        if type(self.max_positions) is not int or self.max_positions<=0:
            raise ValueError('positive position limit required')
        if type(self.max_quote_age_seconds) is not int or self.max_quote_age_seconds<=0:
            raise ValueError('positive quote age required')


ACTIVE = {'ACCEPTED','PARTIAL','CANCEL_PENDING'}


def project(events):
    state = dict(cash=Decimal(0), initial_cash=Decimal(0), orders={}, positions={}, quotes={},
                 lots=[], halted=False, incidents=[], reconciled=False)
    for e in events:
        p, kind = e['payload'], e['kind']
        if kind=='ACCOUNT_OPENED':
            state['cash']=state['initial_cash']=dec(p['capital'])
        elif kind=='QUOTE':
            state['quotes'][p['symbol']]={'price':dec(p['price']),'time':e['event_time']}
        elif kind=='ORDER_ACCEPTED':
            state['orders'][p['order_id']]={**p,'status':'ACCEPTED','filled':0,'fees':Decimal(0)}
        elif kind=='ORDER_CANCEL_REQUESTED':
            state['orders'][p['order_id']]['status']='CANCEL_PENDING'
        elif kind in ('ORDER_CANCELED','ORDER_REJECTED'):
            state['orders'][p['order_id']]['status']='CANCELED' if kind=='ORDER_CANCELED' else 'REJECTED'
        elif kind=='FILL':
            o=state['orders'][p['order_id']]
            q,price,fee=p['quantity'],dec(p['price']),dec(p['fee'])
            o['filled']+=q; o['fees']+=fee
            o['status']='FILLED' if o['filled']==o['quantity'] else ('CANCEL_PENDING' if o['status']=='CANCEL_PENDING' else 'PARTIAL')
            symbol=o['symbol']
            if o['side']=='BUY':
                state['cash']-=money(q*price)+fee
                state['lots'].append(dict(symbol=symbol,quantity=q,session=session(e['event_time']),
                                          settlement_days=o['settlement_days']))
                state['positions'][symbol]=state['positions'].get(symbol,0)+q
            else:
                state['cash']+=money(q*price)-fee
                state['positions'][symbol]-=q
                remaining=q
                for lot in state['lots']:
                    if lot['symbol']!=symbol or (lot['settlement_days'] and lot['session']>=session(e['event_time'])):
                        continue
                    used=min(lot['quantity'],remaining); lot['quantity']-=used; remaining-=used
                    if not remaining: break
        elif kind=='HALT':
            state['halted']=True; state['reconciled']=False
            state['incidents'].append(p)
        elif kind=='RECONCILIATION':
            state['reconciled']=p['matched']
            if not p['matched']:
                state['halted']=True; state['incidents'].append(p)
        elif kind=='RESUMED':
            state['halted']=False
        elif kind=='CORPORATE_ACTION':
            symbol=p['symbol']; ratio=dec(p['share_ratio'])
            state['cash']+=dec(p['net_cash'])
            for lot in state['lots']:
                if lot['symbol']==symbol: lot['quantity']=int(lot['quantity']*ratio)
            state['positions'][symbol]=int(state['positions'].get(symbol,0)*ratio)
            state['quotes'].pop(symbol,None)
        if kind in ('ORDER_ACCEPTED','ORDER_CANCEL_REQUESTED','ORDER_CANCELED','ORDER_REJECTED','FILL','CORPORATE_ACTION'):
            state['reconciled']=False
    return state


def reserved_cash(state, exclude=None):
    return sum((dec(o['limit_price'])*(o['quantity']-o['filled'])+
                max(Decimal(0),dec(o['fee_budget'])-o['fees']))
               for oid,o in state['orders'].items()
               if oid!=exclude and o['status'] in ACTIVE and o['side']=='BUY')


class OrderService:
    def __init__(self, store, master, *, capital='1000000', limits=None, opened_at):
        self.store,self.master,self.limits=store,master,limits or PortfolioLimits()
        payload=dict(capital=str(money(capital)),limits=asdict(self.limits),mode='paper_only',ledger='raw_shares_CNY')
        if dec(capital)<=0:
            raise ValueError('positive initial capital required')
        with store.transaction():
            old=store.get('account:open')
            if old is not None:
                if old['payload']!=payload: raise ValueError('account configuration changed on restart')
            else:
                store.append('account:open','ACCOUNT_OPENED',opened_at,payload)

    def state(self):
        return project(self.store.events())

    def _financial_time(self, when):
        kinds={'ACCOUNT_OPENED','ORDER_ACCEPTED','FILL','ORDER_CANCEL_REQUESTED','ORDER_CANCELED',
               'ORDER_REJECTED','CORPORATE_ACTION','HALT','RESUMED','RECONCILIATION'}
        times=[e['event_time'] for e in self.store.events() if e['kind'] in kinds]
        if times and utc(when)<max(times):
            raise ValueError('backdated_account_mutation_requires_reconciliation')

    def quote(self, symbol, price, when, *, event_id):
        if dec(price)<=0: raise ValueError('positive quote required')
        with self.store.transaction():
            payload=dict(symbol=symbol,price=str(dec(price)))
            if self.store.get(event_id):
                self.store.append(event_id,'QUOTE',when,payload); return
            old=self.state()['quotes'].get(symbol)
            if old and utc(when)<old['time']: raise ValueError('out-of-order quote')
            self.store.append(event_id,'QUOTE',when,payload)

    def _marks(self, state, when, symbols):
        result={}
        for symbol in symbols:
            q=state['quotes'].get(symbol)
            if q is None: raise ValueError('missing_quote:'+symbol)
            age=(datetime.fromisoformat(utc(when))-datetime.fromisoformat(q['time'])).total_seconds()
            if not 0<=age<=self.limits.max_quote_age_seconds: raise ValueError('stale_or_future_quote:'+symbol)
            result[symbol]=q['price']
        return result

    def submit(self, order_id, symbol, side, quantity, limit_price, stop_price, when, *,
               signal_at, fee_budget='10'):
        if side not in ('BUY','SELL') or type(quantity) is not int or quantity<=0:
            raise ValueError('long-only positive integer raw-share order required')
        price,stop,fees=dec(limit_price),dec(stop_price),money(fee_budget)
        if price<=0 or stop<=0 or fees<0 or utc(signal_at)>=utc(when):
            raise ValueError('positive prices, nonnegative fees and prior signal time required')
        request=dict(order_id=order_id,symbol=symbol,side=side,quantity=quantity,
            limit_price=str(price),stop_price=str(stop),signal_at=utc(signal_at),fee_budget=str(fees))
        with self.store.transaction():
            existing=self.store.get('order:'+order_id)
            if existing:
                if any(existing['payload'].get(k)!=v for k,v in request.items()) or existing['event_time']!=utc(when):
                    raise ValueError('order identity conflict')
                return self.state()['orders'][order_id]
            self._financial_time(when)
            state=self.state()
            fact=self.master.at(symbol,session(when),when)
            if fact is None: raise ValueError('unknown_point_in_time_security')
            if not fact.active or fact.suspended: raise ValueError('security_not_tradable')
            if price % dec(fact.tick_size): raise ValueError('price_off_tick')
            positions={s:q for s,q in state['positions'].items() if q}
            pending=[o for o in state['orders'].values() if o['status'] in ACTIVE]
            if any(o['symbol']==symbol for o in pending): raise ValueError('outstanding_order_for_symbol')
            marks=self._marks(state,when,set(positions)|{symbol}|{o['symbol'] for o in pending})
            equity=state['cash']+sum(q*marks[s] for s,q in positions.items())
            if side=='BUY':
                if state['halted']: raise ValueError('account_halted')
                if not self.health(when)['healthy']: raise ValueError('account_health_gate')
                if fact.st: raise ValueError('ST_entries_disabled')
                if quantity % fact.buy_lot: raise ValueError('buy_quantity_off_lot')
                if stop>=price: raise ValueError('long_stop_not_below_entry')
                exposure={s:q*marks[s] for s,q in positions.items()}
                for o in pending:
                    if o['side']=='BUY':
                        exposure[o['symbol']]=exposure.get(o['symbol'],Decimal(0))+dec(o['limit_price'])*(o['quantity']-o['filled'])
                exposure[symbol]=exposure.get(symbol,Decimal(0))+price*quantity
                if len(exposure)>self.limits.max_positions: raise ValueError('position_limit')
                if exposure[symbol]>equity*dec(self.limits.max_symbol_weight): raise ValueError('symbol_weight_limit')
                if sum(exposure.values())>equity*dec(self.limits.max_gross_weight): raise ValueError('gross_weight_limit')
                sector_exposure=Decimal(0)
                for s,value in exposure.items():
                    f=self.master.at(s,session(when),when)
                    if f is None: raise ValueError('unknown_sector_exposure')
                    if f.sector==fact.sector: sector_exposure+=value
                if sector_exposure>equity*dec(self.limits.max_sector_weight): raise ValueError('sector_weight_limit')
                if (price-stop)*quantity+fees>equity*dec(self.limits.max_order_risk): raise ValueError('order_risk_limit')
                if price*quantity+fees>state['cash']-reserved_cash(state): raise ValueError('insufficient_unreserved_cash')
            else:
                available=sum(l['quantity'] for l in state['lots'] if l['symbol']==symbol
                    and (not l['settlement_days'] or l['session']<session(when)))
                if quantity>available: raise ValueError('T_plus_1_or_insufficient_position')
                if quantity % fact.buy_lot and quantity!=positions.get(symbol,0):
                    raise ValueError('odd_lot_sell_must_liquidate_position')
            payload={**request, 'sector':fact.sector,'settlement_days':fact.settlement_days,
                     'security_source':fact.source,'tick_size':fact.tick_size}
            self.store.append('order:'+order_id,'ORDER_ACCEPTED',when,payload)
            return self.state()['orders'][order_id]

    def fill(self, fill_id, order_id, quantity, price, fee, when):
        if type(quantity) is not int or quantity<=0 or dec(price)<=0 or money(fee)<0:
            raise ValueError('invalid fill')
        payload=dict(order_id=order_id,quantity=quantity,price=str(dec(price)),fee=str(money(fee)))
        with self.store.transaction():
            if self.store.get('fill:'+fill_id):
                self.store.append('fill:'+fill_id,'FILL',when,payload)
                return self.state()
            self._financial_time(when)
            state=self.state(); o=state['orders'].get(order_id)
            if o is None or o['status'] not in ACTIVE: raise ValueError('unknown_or_terminal_order_fill_requires_reconciliation')
            created=self.store.get('order:'+order_id)['event_time']
            if utc(when)<created: raise ValueError('fill_precedes_order')
            prior=[e['event_time'] for e in self.store.events() if e['kind']=='FILL' and e['payload']['order_id']==order_id]
            if prior and utc(when)<max(prior): raise ValueError('out_of_order_fill_requires_reconciliation')
            if quantity>o['quantity']-o['filled']: raise ValueError('overfill')
            if o['fees']+money(fee)>dec(o['fee_budget']): raise ValueError('fill_fee_budget_exceeded')
            if o['side']=='BUY':
                if dec(price)>dec(o['limit_price']): raise ValueError('buy_fill_above_limit')
                if money(quantity*dec(price))+money(fee)>state['cash']-reserved_cash(state,order_id):
                    raise ValueError('fill_exceeds_unreserved_cash')
            else:
                if dec(price)<dec(o['limit_price']): raise ValueError('sell_fill_below_limit')
                if money(fee)>money(quantity*dec(price))+state['cash']: raise ValueError('sell_fee_exceeds_cash')
            self.store.append('fill:'+fill_id,'FILL',when,payload)
            return self.state()

    def cancel(self, order_id, when, *, confirmed=False):
        kind='ORDER_CANCELED' if confirmed else 'ORDER_CANCEL_REQUESTED'
        with self.store.transaction():
            old=self.store.get(kind+':'+order_id)
            if old:
                self.store.append(kind+':'+order_id,kind,when,dict(order_id=order_id)); return
            self._financial_time(when)
            o=self.state()['orders'].get(order_id)
            if o is None or o['status'] not in ACTIVE: raise ValueError('cannot_cancel_terminal_order')
            prior=[e['event_time'] for e in self.store.events() if e['payload'].get('order_id')==order_id]
            if prior and utc(when)<max(prior): raise ValueError('cancel_precedes_order_activity')
            self.store.append(kind+':'+order_id,kind,when,dict(order_id=order_id))

    def snapshot(self):
        s=self.state()
        return dict(cash=str(s['cash']),positions={k:v for k,v in s['positions'].items() if v},
                    open_orders={k:dict(status=o['status'],quantity=o['quantity'],filled=o['filled'])
                                 for k,o in s['orders'].items() if o['status'] in ACTIVE})

    def reject(self, order_id, when, *, reason):
        """Venue rejects the unfilled remainder; already filled shares persist."""
        if not isinstance(reason,str) or not reason.strip(): raise ValueError('rejection reason required')
        payload=dict(order_id=order_id,reason=reason)
        with self.store.transaction():
            key='reject:'+order_id
            if self.store.get(key):
                self.store.append(key,'ORDER_REJECTED',when,payload); return
            self._financial_time(when)
            o=self.state()['orders'].get(order_id)
            if o is None or o['status'] not in ACTIVE: raise ValueError('cannot_reject_terminal_order')
            self.store.append(key,'ORDER_REJECTED',when,payload)

    def reconcile(self, external, when, *, event_id):
        with self.store.transaction():
            old=self.store.get(event_id)
            if old:
                if old['kind']!='RECONCILIATION' or old['event_time']!=utc(when) or canonical(old['payload']['external'])!=canonical(external):
                    raise ValueError('reconciliation identity conflict')
                return old['payload']['matched']
            self._financial_time(when)
            internal=self.snapshot()
            matched=canonical(internal)==canonical(external)
            self.store.append(event_id,'RECONCILIATION',when,
                              dict(matched=matched,internal=internal,external=external))
            return matched

    def corporate_action(self, action_id, symbol, share_ratio, net_cash, when, *, known_at, source):
        ratio=dec(share_ratio); cash=money(net_cash)
        if ratio<=0 or cash<0 or utc(known_at)>utc(when) or not source:
            raise ValueError('explicit known corporate-action entitlements required')
        payload=dict(symbol=symbol,share_ratio=str(ratio),net_cash=str(cash),known_at=utc(known_at),source=source)
        with self.store.transaction():
            if self.store.get('action:'+action_id):
                self.store.append('action:'+action_id,'CORPORATE_ACTION',when,payload); return
            self._financial_time(when)
            state=self.state()
            if any(o['symbol']==symbol and o['status'] in ACTIVE for o in state['orders'].values()):
                raise ValueError('resolve_open_orders_before_corporate_action')
            for lot in state['lots']:
                if lot['symbol']==symbol and lot['quantity']*ratio != int(lot['quantity']*ratio):
                    raise ValueError('fractional_entitlement_requires_external_resolution')
            self.store.append('action:'+action_id,'CORPORATE_ACTION',when,payload)

    def halt(self, when, reason, *, event_id):
        with self.store.transaction():
            self.store.append(event_id,'HALT',when,dict(reason=reason))

    def resume(self, when, *, event_id, operator_reason):
        if not operator_reason.strip(): raise ValueError('explicit operator reason required')
        with self.store.transaction():
            if self.store.get(event_id):
                self.store.append(event_id,'RESUMED',when,dict(operator_reason=operator_reason)); return
            self._financial_time(when)
            if not self.state()['reconciled']: raise ValueError('successful reconciliation required before resume')
            latest=max(e['event_time'] for e in self.store.events() if e['kind']=='RECONCILIATION')
            if utc(when)<latest: raise ValueError('resume precedes reconciliation')
            self.store.append(event_id,'RESUMED',when,dict(operator_reason=operator_reason))

    def health(self, when):
        state=self.state()
        symbols={s for s,q in state['positions'].items() if q}
        try:
            marks=self._marks(state,when,symbols)
            equity=state['cash']+sum(state['positions'][s]*marks[s] for s in symbols)
        except ValueError as exc:
            return dict(healthy=False,reason=str(exc),equity=None,halted=state['halted'])
        # High-watermark based on prior explicitly observed health snapshots.
        peaks=[dec(e['payload']['equity']) for e in self.store.events()
               if e['kind']=='HEALTH' and e['payload']['equity'] is not None and e['event_time']<=utc(when)]
        peak=max([state['initial_cash'],equity]+peaks)
        drawdown=1-equity/peak
        healthy=not state['halted'] and drawdown<dec(self.limits.max_drawdown)
        return dict(healthy=healthy,reason='ok' if healthy else 'halt_or_drawdown',
                    equity=str(equity),drawdown=str(drawdown),halted=state['halted'])

    def monitor(self, when, *, event_id):
        with self.store.transaction():
            old=self.store.get(event_id)
            if old:
                if old['kind']!='HEALTH' or old['event_time']!=utc(when): raise ValueError('monitor identity conflict')
                return old['payload']
            h=self.health(when)
            self.store.append(event_id,'HEALTH',when,h)
            if not h['healthy']: self.store.append(event_id+':halt','HALT',when,dict(reason=h['reason']))
            return h
