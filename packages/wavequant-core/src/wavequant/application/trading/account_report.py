"""Build an as-of raw-share ledger report with FIFO cost and P&L conservation.

Not a tax report. Net corporate cash is supplied externally; stale marks are
unknown rather than silently carried forward as current NAV.
"""
from datetime import datetime

from wavequant.infrastructure.persistence.event_store import utc
from .order_service import dec, money, project, reserved_cash


def account_report(store, when):
    cutoff=utc(when)
    events=[e for e in store.events() if e['event_time']<=cutoff]
    if not any(e['kind']=='ACCOUNT_OPENED' for e in events):
        raise ValueError('account did not exist at requested time')
    state=project(events); lots={}; realized={}; dividends={}; fees={}; counts={}
    for e in events:
        p=e['payload']
        if e['kind']=='FILL':
            order=state['orders'][p['order_id']]; symbol=order['symbol']; q=p['quantity']
            fee=dec(p['fee']); value=money(dec(p['price'])*q)
            fees[symbol]=fees.get(symbol,dec(0))+fee
            counts[symbol]=counts.get(symbol,0)+1
            book=lots.setdefault(symbol,[])
            if order['side']=='BUY':
                book.append(dict(quantity=q,cost=value+fee))
            else:
                remaining=q; cost=dec(0)
                for lot in book:
                    used=min(remaining,lot['quantity'])
                    if not used: continue
                    part=lot['cost']*used/lot['quantity']
                    lot['cost']-=part; lot['quantity']-=used; cost+=part; remaining-=used
                    if not remaining: break
                if remaining: raise ValueError('sell exceeds FIFO inventory')
                realized[symbol]=realized.get(symbol,dec(0))+value-fee-cost
        elif e['kind']=='CORPORATE_ACTION':
            symbol=p['symbol']; ratio=dec(p['share_ratio'])
            dividends[symbol]=dividends.get(symbol,dec(0))+dec(p['net_cash'])
            for lot in lots.get(symbol,[]):
                lot['quantity']=int(lot['quantity']*ratio)
    opened=next(e for e in events if e['kind']=='ACCOUNT_OPENED')
    max_age=opened['payload']['limits']['max_quote_age_seconds']
    rows=[]; value=dec(0); total_unrealized=dec(0); missing=[]
    for symbol in sorted(set(lots)|set(dividends)):
        q=sum(l['quantity'] for l in lots.get(symbol,[]))
        cost=sum((l['cost'] for l in lots.get(symbol,[])),dec(0))
        if q!=state['positions'].get(symbol,0): raise ValueError('FIFO/ledger share mismatch')
        mark=state['quotes'].get(symbol)
        age=None if mark is None else (datetime.fromisoformat(cutoff)-datetime.fromisoformat(mark['time'])).total_seconds()
        usable=not q or (mark is not None and 0<=age<=max_age)
        mv=(q*mark['price'] if q else dec(0)) if usable else None
        unrealized=mv-cost if mv is not None else None
        if usable: value+=mv; total_unrealized+=unrealized
        else: missing.append(symbol)
        rows.append(dict(symbol=symbol,quantity=q,cost_basis=str(money(cost)),
                         market_value=None if mv is None else str(money(mv)),
                         unrealized_pnl=None if unrealized is None else str(money(unrealized)),
                         realized_pnl=str(money(realized.get(symbol,dec(0)))),
                         corporate_cash=str(money(dividends.get(symbol,dec(0)))),
                         fees=str(money(fees.get(symbol,dec(0)))),fill_count=counts.get(symbol,0)))
    total_realized=sum(realized.values(),dec(0)); total_dividends=sum(dividends.values(),dec(0))
    equity=None if missing else state['cash']+value
    pnl=None if equity is None else equity-state['initial_cash']
    attribution=None if equity is None else total_realized+total_unrealized+total_dividends
    if pnl is not None and money(pnl)!=money(attribution): raise ValueError('account P&L attribution does not conserve NAV')
    return dict(asof=cutoff,currency='CNY',basis='raw_shares_FIFO_including_commissions_not_tax_accounting',
                cash=str(money(state['cash'])),reserved_cash=str(money(reserved_cash(state))),
                equity=None if equity is None else str(money(equity)),
                total_pnl=None if pnl is None else str(money(pnl)),
                realized_pnl=str(money(total_realized)),corporate_cash=str(money(total_dividends)),
                fees=str(money(sum(fees.values(),dec(0)))),
                pnl_conserved=None if pnl is None else True,unmarked_symbols=missing,
                halted=state['halted'],reconciled=state['reconciled'],symbols=rows)
