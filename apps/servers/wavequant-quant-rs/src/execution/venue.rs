use super::{account::AccountEvent, journal::{Journal, Record, require}, Account, AccountSnapshot,
    CorporateAction, ExecutionError, ExecutionResult, Fill, OpenOrder, Order, OrderStatus, Side, money};
use chrono::{DateTime, Utc};
use rust_decimal::Decimal;
use serde::{Deserialize, Serialize};
use std::{collections::BTreeMap, path::Path};

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct OpeningTick {
    pub tick_id: String, pub symbol: String, pub at: DateTime<Utc>, pub price: Decimal,
    pub available_volume: u64, pub buyable: bool, pub sellable: bool,
}
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct VenueSettings {
    pub capital: Decimal, pub commission_rate: Decimal, pub minimum_fee: Decimal,
    pub sell_tax_rate: Decimal, pub slippage_bps: Decimal,
}
impl Default for VenueSettings {
    fn default() -> Self { Self {capital:Decimal::from(1_000_000),commission_rate:Decimal::new(3,4),minimum_fee:Decimal::from(5),sell_tax_rate:Decimal::new(5,4),slippage_bps:Decimal::from(5)} }
}
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub enum VenueReport { Fill {fill:Fill,tick_id:String}, Canceled {order_id:String,at:DateTime<Utc>}, Rejected {order_id:String,at:DateTime<Utc>,reason:String} }
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
enum VenueEvent { Opened(VenueSettings), Accepted(Order), Tick(OpeningTick), Report(VenueReport), CorporateAction(CorporateAction) }

fn orders(records: &[Record<VenueEvent>]) -> ExecutionResult<BTreeMap<String,Order>> {
    let mut result=BTreeMap::<String,Order>::new();
    for record in records {
        match &record.payload {
            VenueEvent::Accepted(order) => { result.insert(order.request.order_id.clone(),order.clone()); }
            VenueEvent::Report(report) => match report {
                VenueReport::Fill {fill,..} => {
                    let o=result.get_mut(&fill.order_id).ok_or_else(|| ExecutionError("unknown_venue_order".into()))?;
                    o.filled += fill.quantity; o.fees += fill.fee;
                    o.status=if o.filled == o.request.quantity {OrderStatus::Filled} else {OrderStatus::Partial};
                }
                VenueReport::Canceled {order_id,..} | VenueReport::Rejected {order_id,..} => {
                    let o=result.get_mut(order_id).ok_or_else(|| ExecutionError("unknown_venue_order".into()))?;
                    o.status=if matches!(report,VenueReport::Canceled {..}) {OrderStatus::Canceled} else {OrderStatus::Rejected};
                }
            },
            _ => {}
        }
    }
    Ok(result)
}

/// 独立于 OMS 的模拟撮合日志：先提交成交，再由桥接器投递，支持中断后重复接收。
pub struct PaperVenue { journal:Journal<VenueEvent>, settings:VenueSettings }
impl PaperVenue {
    pub fn open(path:&Path, mut settings:VenueSettings, at:DateTime<Utc>) -> ExecutionResult<Self> {
        require(settings.capital > Decimal::ZERO && [settings.commission_rate,settings.minimum_fee,settings.sell_tax_rate,settings.slippage_bps].iter().all(|v| *v >= Decimal::ZERO),"invalid_venue_settings")?;
        settings.capital=money(settings.capital); settings.minimum_fee=money(settings.minimum_fee);
        let mut journal=Journal::open(path)?;
        journal.transact(|records| {
            if let Some(old)=records.iter().find(|r| r.id == "venue:open") {require(old.payload == VenueEvent::Opened(settings.clone()),"venue_settings_changed")?; return Ok((vec![],()));}
            require(records.is_empty(),"venue_journal_kind_mismatch")?;
            Ok((vec![Record {id:"venue:open".into(),at,payload:VenueEvent::Opened(settings.clone())}],()))
        })?;
        Ok(Self {journal,settings})
    }
    pub fn orders(&self) -> ExecutionResult<BTreeMap<String,Order>> {orders(&self.journal.records()?)}
    pub fn accept(&mut self, order:&Order) -> ExecutionResult<()> {
        require(order.status == OrderStatus::Accepted && order.filled == 0,"accepted_local_order_required")?;
        self.journal.transact(|_| Ok((vec![Record {id:format!("venue:order:{}",order.request.order_id),at:order.accepted_at,payload:VenueEvent::Accepted(order.clone())}],())))
    }
    pub fn match_open(&mut self, tick:&OpeningTick) -> ExecutionResult<Vec<Fill>> {
        require(!tick.tick_id.is_empty() && !tick.symbol.is_empty() && tick.price > Decimal::ZERO,"invalid_opening_tick")?;
        let settings=&self.settings;
        self.journal.transact(|records| {
            let key=format!("venue:tick:{}",tick.tick_id);
            if let Some(old)=records.iter().find(|r| r.id == key) {
                require(old.payload == VenueEvent::Tick(tick.clone()) && old.at == tick.at,"tick_identity_conflict")?;
                let fills=records.iter().filter_map(|r| match &r.payload {VenueEvent::Report(VenueReport::Fill {fill,tick_id}) if *tick_id == tick.tick_id => Some(fill.clone()), _ => None}).collect();
                return Ok((vec![],fills));
            }
            require(records.iter().all(|r| match &r.payload {VenueEvent::Tick(old) if old.symbol == tick.symbol => old.at < tick.at, _ => true}),"out_of_order_venue_tick")?;
            let current=orders(records)?;
            let mut events=vec![Record {id:key,at:tick.at,payload:VenueEvent::Tick(tick.clone())}];
            let mut remaining=tick.available_volume;
            let mut fills=Vec::new();
            // Python dict 顺序是订单接受顺序；不能改为按订单 ID 排序争夺流动性。
            for accepted in records.iter().filter_map(|r| match &r.payload {VenueEvent::Accepted(o) => Some(&o.request.order_id), _ => None}) {
                let o=&current[accepted];
                if o.request.symbol != tick.symbol || !o.status.active() || tick.at < o.accepted_at || tick.at <= o.request.signal_at {continue;}
                let buy=o.request.side == Side::Buy;
                if !(if buy {tick.buyable} else {tick.sellable}) {continue;}
                let slip=settings.slippage_bps/Decimal::from(10_000);
                let raw=tick.price*(if buy {Decimal::ONE+slip} else {Decimal::ONE-slip});
                let step=o.security.tick_size;
                require(step > Decimal::ZERO,"invalid_tick_size")?;
                let price=(if buy {(raw/step).ceil()} else {(raw/step).floor()})*step;
                if price <= Decimal::ZERO || (buy && price > o.request.limit_price) || (!buy && price < o.request.limit_price) {continue;}
                let quantity=remaining.min(o.request.quantity-o.filled);
                if quantity == 0 {continue;}
                let value=money(price*Decimal::from(quantity));
                let fee=money(settings.minimum_fee.max(value*settings.commission_rate)+if buy {Decimal::ZERO} else {value*settings.sell_tax_rate});
                let fill=Fill {fill_id:format!("venue:fill:{}:{accepted}",tick.tick_id),order_id:accepted.clone(),quantity,price,fee,at:tick.at};
                events.push(Record {id:fill.fill_id.clone(),at:tick.at,payload:VenueEvent::Report(VenueReport::Fill {fill:fill.clone(),tick_id:tick.tick_id.clone()})});
                fills.push(fill); remaining -= quantity;
            }
            Ok((events,fills))
        })
    }
    fn terminal(&mut self, id:String, order_id:&str, at:DateTime<Utc>, report:VenueReport) -> ExecutionResult<()> {
        self.journal.transact(|records| {
            let record=Record {id,at,payload:VenueEvent::Report(report)};
            if let Some(old)=records.iter().find(|r| r.id == record.id) {require(*old == record,"venue_event_identity_conflict")?; return Ok((vec![],()));}
            require(orders(records)?.get(order_id).is_some_and(|o| o.status.active()),"venue_order_terminal_or_unknown")?;
            require(records.iter().all(|r| match &r.payload {
                VenueEvent::Accepted(o) if o.request.order_id == order_id => r.at <= at,
                VenueEvent::Report(VenueReport::Fill {fill,..}) if fill.order_id == order_id => r.at <= at,
                _ => true,
            }),"terminal_event_precedes_venue_activity")?;
            Ok((vec![record],()))
        })
    }
    pub fn cancel(&mut self, order_id:&str, at:DateTime<Utc>) -> ExecutionResult<()> {self.terminal(format!("venue:cancel:{order_id}"),order_id,at,VenueReport::Canceled {order_id:order_id.into(),at})}
    pub fn reject(&mut self, order_id:&str, at:DateTime<Utc>, reason:&str) -> ExecutionResult<()> {
        require(!reason.trim().is_empty(),"rejection_reason_required")?;
        self.terminal(format!("venue:reject:{order_id}"),order_id,at,VenueReport::Rejected {order_id:order_id.into(),at,reason:reason.into()})
    }
    pub fn reports(&self) -> ExecutionResult<Vec<VenueReport>> {
        Ok(self.journal.records()?.into_iter().filter_map(|r| match r.payload {VenueEvent::Report(report) => Some(report), _ => None}).collect())
    }
    pub fn corporate_action(&mut self, mut action:CorporateAction, at:DateTime<Utc>) -> ExecutionResult<()> {
        use rust_decimal::prelude::ToPrimitive;
        require(action.share_ratio > Decimal::ZERO && action.net_cash >= Decimal::ZERO && action.known_at <= at && !action.source.is_empty(),"explicit_known_entitlements_required")?;
        action.net_cash=money(action.net_cash);
        self.journal.transact(|records| {
            let record=Record {id:format!("venue:action:{}",action.action_id),at,payload:VenueEvent::CorporateAction(action.clone())};
            if let Some(old)=records.iter().find(|r| r.id == record.id) {require(*old == record,"action_identity_conflict")?; return Ok((vec![],()));}
            let current=orders(records)?;
            require(!current.values().any(|o| o.request.symbol == action.symbol && o.status.active()),"resolve_open_orders_before_corporate_action")?;
            require(records.iter().all(|r| r.at <= at),"backdated_venue_corporate_action")?;
            let mut lots=Vec::<(String,u64)>::new();
            for r in records {
                match &r.payload {
                    VenueEvent::Report(VenueReport::Fill {fill,..}) => {
                        let o=&current[&fill.order_id];
                        if o.request.side == Side::Buy {lots.push((o.request.symbol.clone(),fill.quantity));}
                        else {let mut remaining=fill.quantity; for (symbol,q) in &mut lots {if *symbol == o.request.symbol {let used=remaining.min(*q); remaining -= used; *q -= used;}} require(remaining == 0,"negative_venue_position")?;}
                    }
                    VenueEvent::CorporateAction(previous) => {for (symbol,q) in &mut lots {if *symbol == previous.symbol {*q=(Decimal::from(*q)*previous.share_ratio).to_u64().ok_or_else(|| ExecutionError("share_quantity_overflow".into()))?;}}}
                    _ => {}
                }
            }
            for (symbol,q) in lots {if symbol == action.symbol {let adjusted=Decimal::from(q)*action.share_ratio; require(adjusted.fract() == Decimal::ZERO && adjusted.to_u64().is_some(),"fractional_entitlement_requires_external_resolution")?;}}
            Ok((vec![record],()))
        })
    }
    pub fn snapshot(&self) -> ExecutionResult<AccountSnapshot> {
        let records=self.journal.records()?;
        let orders=orders(&records)?;
        let mut cash=self.settings.capital;
        let mut positions=BTreeMap::<String,u64>::new();
        for record in &records {
            match &record.payload {
                VenueEvent::Report(VenueReport::Fill {fill,..}) => {
                    let order=&orders[&fill.order_id];
                    let q=positions.entry(order.request.symbol.clone()).or_default();
                    let value=money(fill.price*Decimal::from(fill.quantity));
                    if order.request.side == Side::Buy {*q += fill.quantity; cash -= value+fill.fee;}
                    else {require(*q >= fill.quantity,"negative_venue_position")?; *q -= fill.quantity; cash += value-fill.fee;}
                }
                VenueEvent::CorporateAction(action) => {
                    use rust_decimal::prelude::ToPrimitive;
                    cash += action.net_cash;
                    if let Some(q)=positions.get_mut(&action.symbol) {*q=(Decimal::from(*q)*action.share_ratio).to_u64().ok_or_else(|| ExecutionError("share_quantity_overflow".into()))?;}
                }
                _ => {}
            }
        }
        Ok(AccountSnapshot {cash,positions:positions.into_iter().filter(|(_,q)| *q > 0).collect(),open_orders:orders.into_iter().filter(|(_,o)| o.status.active()).map(|(id,o)| (id,OpenOrder {status:o.status,quantity:o.request.quantity,filled:o.filled})).collect()})
    }
    pub fn dispatch(&mut self, account:&Account) -> ExecutionResult<()> {
        for event in account.events()? {
            match event.payload {
                AccountEvent::Accepted(order) => self.accept(&order)?,
                AccountEvent::CancelRequested(id) => {if self.orders()?.get(&id).is_some_and(|o| o.status.active()) {self.cancel(&id,event.at)?;}},
                _ => {}
            }
        }
        Ok(())
    }
    pub fn receive(&self, account:&mut Account) -> ExecutionResult<()> {
        for report in self.reports()? {
            let (id,at,result)=match report {
                VenueReport::Fill {fill,..} => (fill.fill_id.clone(),fill.at,account.fill(fill)),
                VenueReport::Canceled {order_id,at} => (format!("venue:cancel:{order_id}"),at,account.cancel(&order_id,at,true)),
                VenueReport::Rejected {order_id,at,reason} => (format!("venue:reject:{order_id}"),at,account.reject(&order_id,at,&reason)),
            };
            if let Err(error)=result {account.halt(&format!("venue-report-incident:{id}"),at,&error.to_string())?; return Err(error);}
        }
        Ok(())
    }
}
