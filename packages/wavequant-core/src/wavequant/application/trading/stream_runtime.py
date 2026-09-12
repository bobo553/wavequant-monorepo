"""Coordinate a restartable causal strategy without duplicating trading theory.

This correctness-first adapter recomputes the known prefix; it is not a
high-frequency engine. Orders remain a separate consumer of durable intents.
"""
from dataclasses import asdict
from datetime import datetime
import hashlib

from wavequant.domain.strategies.integrated_strategy import generate_system_signals
from wavequant.domain.models.model import Bar
from wavequant.infrastructure.persistence.event_store import canonical, utc
from wavequant.infrastructure.market_data.io import _validate_bar


class StrategyRuntime:
    def __init__(self, store, config):
        config.validate()
        self.store,self.config=store,config
        self.version=hashlib.sha256(canonical(asdict(config)).encode()).hexdigest()

    def on_bar(self, bar, *, available_at):
        _validate_bar(bar,0)
        if bar.timestamp.tzinfo is None: raise ValueError('runtime bars must be timezone-aware')
        if utc(available_at)<utc(bar.timestamp): raise ValueError('bar not yet available')
        payload=asdict(bar); payload['timestamp']=bar.timestamp.isoformat()
        identity='bar:'+bar.symbol+':'+utc(bar.timestamp)
        with self.store.transaction():
            old=self.store.get(identity)
            if old is None:
                times=[e['payload']['timestamp'] for e in self.store.events()
                       if e['kind']=='BAR' and e['payload']['symbol']==bar.symbol]
                if times and utc(bar.timestamp)<=max(map(utc,times)): raise ValueError('out_of_order_bar')
            self.store.append(identity,'BAR',available_at,payload)
        # Intent computation can crash after bar commit. Re-delivery re-runs
        # computation, and atomic unique intent IDs prevent duplicate dispatch.
        bars=[]
        for e in self.store.events():
            p=e['payload']
            if e['kind']=='BAR' and p['symbol']==bar.symbol and utc(p['timestamp'])<=utc(bar.timestamp):
                bars.append(Bar(**dict(p,timestamp=datetime.fromisoformat(p['timestamp']))))
        result=generate_system_signals(bars,self.config)
        emitted=[]
        with self.store.transaction():
            for s in result.signals:
                if s.timestamp!=bar.timestamp: continue
                row=asdict(s)
                row['timestamp']=s.timestamp.isoformat(); row['trigger_timestamp']=s.trigger_timestamp.isoformat()
                row['strategy_version']=self.version
                event_id='intent:'+hashlib.sha256(canonical([self.version,s.symbol,row['timestamp'],s.side]).encode()).hexdigest()
                self.store.append(event_id,'SIGNAL_INTENT',available_at,row)
                emitted.append(event_id)
            self.store.append('evaluated:'+self.version+':'+identity,'STRATEGY_EVALUATED',available_at,
                              dict(bar_id=identity,intent_ids=emitted))
        return emitted

    def pending_intents(self):
        ack={e['payload']['intent_id'] for e in self.store.events() if e['kind']=='INTENT_ACK'}
        return [e for e in self.store.events() if e['kind']=='SIGNAL_INTENT'
                and e['payload']['strategy_version']==self.version and e['event_id'] not in ack]

    def acknowledge(self, intent_id, when, *, disposition, order_id=None):
        if disposition not in ('ORDERED','FILTERED','EXPIRED','RISK_EXIT_OBSERVED'):
            raise ValueError('explicit terminal opportunity disposition required')
        with self.store.transaction():
            event=self.store.get(intent_id)
            if event is None or event['kind']!='SIGNAL_INTENT': raise ValueError('unknown intent')
            if utc(when)<event['event_time']: raise ValueError('ack precedes intent')
            if disposition=='ORDERED' and (not order_id or self.store.get('order:'+order_id) is None):
                raise ValueError('durable accepted order required before acknowledgement')
            if event['payload']['strategy_version']!=self.version: raise ValueError('wrong strategy version')
            if disposition=='ORDERED':
                order=self.store.get('order:'+order_id)
                expected='BUY' if event['payload']['side']=='LONG' else 'SELL'
                if (order['payload']['symbol']!=event['payload']['symbol'] or order['payload']['side']!=expected
                        or order['payload']['signal_at']!=utc(event['payload']['timestamp'])
                        or order['event_time']>utc(when)):
                    raise ValueError('order does not match intent')
            self.store.append('ack:'+intent_id,'INTENT_ACK',when,
                              dict(intent_id=intent_id,disposition=disposition,order_id=order_id))
