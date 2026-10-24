use super::{market::*, journal::{Journal, Record, require}, ExecutionError, ExecutionResult};
use chrono::{DateTime, NaiveDate, Utc};
use rust_decimal::{Decimal, prelude::ToPrimitive};
use serde::{Deserialize, Serialize};
use std::{collections::BTreeMap, path::Path};

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PortfolioLimits {
    pub max_positions: usize, pub max_symbol_weight: Decimal, pub max_sector_weight: Decimal,
    pub max_gross_weight: Decimal, pub max_order_risk: Decimal, pub max_drawdown: Decimal,
    pub max_quote_age_seconds: i64,
}
impl Default for PortfolioLimits {
    fn default() -> Self { Self { max_positions: 5, max_symbol_weight: Decimal::new(20,2), max_sector_weight: Decimal::new(40,2), max_gross_weight: Decimal::new(80,2), max_order_risk: Decimal::new(5,3), max_drawdown: Decimal::new(20,2), max_quote_age_seconds: 86400 } }
}
impl PortfolioLimits {
    fn validate(&self) -> ExecutionResult<()> {
        require(self.max_positions > 0 && self.max_quote_age_seconds > 0 && [self.max_symbol_weight,self.max_sector_weight,self.max_gross_weight,self.max_order_risk,self.max_drawdown].iter().all(|v| *v > Decimal::ZERO && *v <= Decimal::ONE), "invalid_portfolio_limits")
    }
}
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct OrderRequest {
    pub order_id: String, pub symbol: String, pub side: Side, pub quantity: u64,
    pub limit_price: Decimal, pub stop_price: Decimal, pub signal_at: DateTime<Utc>, pub fee_budget: Decimal,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum OrderStatus { Accepted, Partial, CancelPending, Filled, Canceled, Rejected }
impl OrderStatus { pub fn active(self) -> bool { matches!(self, Self::Accepted | Self::Partial | Self::CancelPending) } }
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Order {
    pub request: OrderRequest, pub security: SecurityFact, pub accepted_at: DateTime<Utc>,
    pub status: OrderStatus, pub filled: u64, pub fees: Decimal,
}
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Fill { pub fill_id: String, pub order_id: String, pub quantity: u64, pub price: Decimal, pub fee: Decimal, pub at: DateTime<Utc> }
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Lot { pub symbol: String, pub quantity: u64, pub session: NaiveDate, pub settlement_days: u8 }
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Quote { pub price: Decimal, pub at: DateTime<Utc> }
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct AccountState {
    pub cash: Decimal, pub initial_cash: Decimal, pub orders: BTreeMap<String, Order>,
    pub positions: BTreeMap<String, u64>, pub quotes: BTreeMap<String, Quote>, pub lots: Vec<Lot>,
    pub halted: bool, pub reconciled: bool, pub incidents: Vec<String>,
}
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct OpenOrder { pub status: OrderStatus, pub quantity: u64, pub filled: u64 }
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct AccountSnapshot { pub cash: Decimal, pub positions: BTreeMap<String, u64>, pub open_orders: BTreeMap<String, OpenOrder> }
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Health { pub healthy: bool, pub reason: String, pub equity: Option<Decimal>, pub drawdown: Option<Decimal>, pub halted: bool }
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CorporateAction { pub action_id: String, pub symbol: String, pub share_ratio: Decimal, pub net_cash: Decimal, pub known_at: DateTime<Utc>, pub source: String }
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SymbolAccountReport {
    pub symbol:String, pub quantity:u64, pub cost_basis:Decimal, pub market_value:Option<Decimal>,
    pub unrealized_pnl:Option<Decimal>, pub realized_pnl:Decimal, pub corporate_cash:Decimal,
    pub fees:Decimal, pub fill_count:usize,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AccountReport {
    pub asof:DateTime<Utc>, pub currency:String, pub basis:String, pub cash:Decimal, pub reserved_cash:Decimal,
    pub equity:Option<Decimal>, pub total_pnl:Option<Decimal>, pub realized_pnl:Decimal,
    pub corporate_cash:Decimal, pub fees:Decimal, pub pnl_conserved:Option<bool>,
    pub unmarked_symbols:Vec<String>, pub halted:bool, pub reconciled:bool, pub symbols:Vec<SymbolAccountReport>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub(super) enum AccountEvent {
    Opened { capital: Decimal, limits: PortfolioLimits }, Quote { symbol: String, price: Decimal },
    Accepted(Order), Fill(Fill), CancelRequested(String), Canceled(String), Rejected { order_id: String, reason: String },
    Halt(String), Reconciled { external: AccountSnapshot, matched: bool }, Resumed(String),
    CorporateAction(CorporateAction), Health(Health), Runtime(super::stream::RuntimeEvent),
}

pub(super) fn project(records: &[Record<AccountEvent>]) -> ExecutionResult<AccountState> {
    let mut state = AccountState::default();
    for record in records {
        match &record.payload {
            AccountEvent::Opened { capital, .. } => { state.cash = *capital; state.initial_cash = *capital; }
            AccountEvent::Quote { symbol, price } => { state.quotes.insert(symbol.clone(), Quote { price: *price, at: record.at }); }
            AccountEvent::Accepted(order) => { state.orders.insert(order.request.order_id.clone(), order.clone()); state.reconciled = false; }
            AccountEvent::CancelRequested(id) | AccountEvent::Canceled(id) | AccountEvent::Rejected { order_id: id, .. } => {
                let order = state.orders.get_mut(id).ok_or_else(|| ExecutionError("unknown_journal_order".into()))?;
                order.status = match record.payload { AccountEvent::CancelRequested(_) => OrderStatus::CancelPending, AccountEvent::Canceled(_) => OrderStatus::Canceled, _ => OrderStatus::Rejected };
                state.reconciled = false;
            }
            AccountEvent::Fill(fill) => {
                let order = state.orders.get_mut(&fill.order_id).ok_or_else(|| ExecutionError("unknown_journal_order".into()))?;
                order.filled += fill.quantity; order.fees += fill.fee;
                order.status = if order.filled == order.request.quantity { OrderStatus::Filled } else if order.status == OrderStatus::CancelPending { OrderStatus::CancelPending } else { OrderStatus::Partial };
                let symbol = &order.request.symbol;
                let position = state.positions.entry(symbol.clone()).or_default();
                if order.request.side == Side::Buy {
                    state.cash -= money(Decimal::from(fill.quantity)*fill.price)+fill.fee;
                    *position += fill.quantity;
                    state.lots.push(Lot { symbol: symbol.clone(), quantity: fill.quantity, session: session(record.at), settlement_days: order.security.settlement_days });
                } else {
                    require(*position >= fill.quantity, "negative_position_in_journal")?;
                    *position -= fill.quantity;
                    state.cash += money(Decimal::from(fill.quantity)*fill.price)-fill.fee;
                    let mut remaining = fill.quantity;
                    for lot in &mut state.lots {
                        if lot.symbol != *symbol || (lot.settlement_days != 0 && lot.session >= session(record.at)) { continue; }
                        let used = remaining.min(lot.quantity); lot.quantity -= used; remaining -= used;
                        if remaining == 0 { break; }
                    }
                    require(remaining == 0, "unsettled_sell_in_journal")?;
                }
                state.reconciled = false;
            }
            AccountEvent::Halt(reason) => { state.halted = true; state.reconciled = false; state.incidents.push(reason.clone()); }
            AccountEvent::Reconciled { matched, .. } => { state.reconciled = *matched; if !matched { state.halted = true; state.incidents.push("reconciliation_mismatch".into()); } }
            AccountEvent::Resumed(_) => state.halted = false,
            AccountEvent::CorporateAction(action) => {
                state.cash += action.net_cash;
                for lot in &mut state.lots { if lot.symbol == action.symbol { lot.quantity = (Decimal::from(lot.quantity)*action.share_ratio).to_u64().ok_or_else(|| ExecutionError("share_quantity_overflow".into()))?; } }
                if let Some(quantity) = state.positions.get_mut(&action.symbol) { *quantity = (Decimal::from(*quantity)*action.share_ratio).to_u64().ok_or_else(|| ExecutionError("share_quantity_overflow".into()))?; }
                state.quotes.remove(&action.symbol); state.reconciled = false;
            }
            AccountEvent::Health(_) | AccountEvent::Runtime(_) => {}
        }
    }
    Ok(state)
}
impl AccountState {
    pub fn snapshot(&self) -> AccountSnapshot {
        AccountSnapshot { cash: self.cash, positions: self.positions.iter().filter(|(_,q)| **q > 0).map(|(s,q)| (s.clone(),*q)).collect(), open_orders: self.orders.iter().filter(|(_,o)| o.status.active()).map(|(id,o)| (id.clone(), OpenOrder { status:o.status, quantity:o.request.quantity, filled:o.filled })).collect() }
    }
    pub fn reserved_cash(&self, exclude: Option<&str>) -> Decimal {
        self.orders.iter().filter(|(id,o)| exclude != Some(id.as_str()) && o.status.active() && o.request.side == Side::Buy).map(|(_,o)| o.request.limit_price*Decimal::from(o.request.quantity-o.filled)+(o.request.fee_budget-o.fees).max(Decimal::ZERO)).sum()
    }
    pub fn sellable(&self, symbol: &str, at: DateTime<Utc>) -> u64 {
        self.lots.iter().filter(|lot| lot.symbol == symbol && (lot.settlement_days == 0 || lot.session < session(at))).map(|lot| lot.quantity).sum()
    }
    fn mark(&self, symbol: &str, at: DateTime<Utc>, limits: &PortfolioLimits) -> ExecutionResult<Decimal> {
        let quote = self.quotes.get(symbol).ok_or_else(|| ExecutionError(format!("missing_quote:{symbol}")))?;
        require(quote.at <= at && at.signed_duration_since(quote.at).num_milliseconds() <= limits.max_quote_age_seconds*1000, "stale_or_future_quote")?;
        Ok(quote.price)
    }
    pub fn equity(&self, at: DateTime<Utc>, limits: &PortfolioLimits) -> ExecutionResult<Decimal> {
        let mut equity = self.cash;
        for (symbol, quantity) in &self.positions { if *quantity > 0 { equity += Decimal::from(*quantity)*self.mark(symbol,at,limits)?; } }
        Ok(equity)
    }
}
fn financial_time(records: &[Record<AccountEvent>], at: DateTime<Utc>) -> ExecutionResult<()> {
    require(records.iter().filter(|r| !matches!(r.payload, AccountEvent::Quote {..} | AccountEvent::Health(_) | AccountEvent::Runtime(_))).all(|r| r.at <= at), "backdated_account_mutation_requires_reconciliation")
}
fn health(state: &AccountState, records: &[Record<AccountEvent>], at: DateTime<Utc>, limits: &PortfolioLimits) -> Health {
    let equity = match state.equity(at,limits) { Ok(value) => value, Err(error) => return Health { healthy:false, reason:error.to_string(), equity:None, drawdown:None, halted:state.halted } };
    let peak = records.iter().filter(|r| r.at <= at).filter_map(|r| match &r.payload { AccountEvent::Health(h) => h.equity, _ => None }).fold(state.initial_cash.max(equity), Decimal::max);
    let drawdown = Decimal::ONE-equity/peak;
    let healthy = !state.halted && drawdown < limits.max_drawdown;
    Health { healthy, reason:if healthy {"ok"} else {"halt_or_drawdown"}.into(), equity:Some(equity), drawdown:Some(drawdown), halted:state.halted }
}

pub struct Account { pub(super) journal: Journal<AccountEvent>, pub limits: PortfolioLimits }
impl Account {
    pub fn open_existing_readonly(path:&Path)->ExecutionResult<Self>{
        let journal=Journal::open_readonly(path)?;let records=journal.records()?;
        let limits=match records.first().map(|r|&r.payload){Some(AccountEvent::Opened{limits,..})=>limits.clone(),_=>return Err(ExecutionError("account_opening_record_required".into()))};
        limits.validate()?;Ok(Self{journal,limits})
    }
    pub fn open(path: &Path, capital: Decimal, limits: PortfolioLimits, at: DateTime<Utc>) -> ExecutionResult<Self> {
        limits.validate()?; require(capital > Decimal::ZERO && money(capital) > Decimal::ZERO, "positive_initial_capital_required")?;
        let mut journal = Journal::open(path)?;
        let event = AccountEvent::Opened { capital:money(capital), limits:limits.clone() };
        journal.transact(|records| {
            if let Some(old) = records.iter().find(|r| r.id == "account:open") { require(old.payload == event, "account_configuration_changed_on_restart")?; return Ok((vec![],())); }
            require(records.is_empty(), "account_journal_kind_mismatch")?;
            Ok((vec![Record { id:"account:open".into(), at, payload:event }],()))
        })?;
        Ok(Self { journal, limits })
    }
    pub fn state(&self) -> ExecutionResult<AccountState> { project(&self.journal.records()?) }
    pub(super) fn events(&self) -> ExecutionResult<Vec<Record<AccountEvent>>> { self.journal.records() }
    pub fn snapshot(&self) -> ExecutionResult<AccountSnapshot> { Ok(self.state()?.snapshot()) }
    pub fn health(&self, at: DateTime<Utc>) -> ExecutionResult<Health> { let records=self.journal.records()?; Ok(health(&project(&records)?,&records,at,&self.limits)) }
    fn append(&mut self, id: String, at: DateTime<Utc>, event: AccountEvent, check: impl FnOnce(&AccountState,&[Record<AccountEvent>]) -> ExecutionResult<()>) -> ExecutionResult<()> {
        self.journal.transact(|records| {
            let record = Record { id, at, payload:event };
            if let Some(old)=records.iter().find(|r| r.id == record.id) { require(*old == record,"event_identity_conflict")?; return Ok((vec![],())); }
            check(&project(records)?,records)?;
            Ok((vec![record],()))
        })
    }
    pub fn quote(&mut self, id: &str, symbol: &str, price: Decimal, at: DateTime<Utc>) -> ExecutionResult<()> {
        require(price > Decimal::ZERO,"positive_quote_required")?;
        self.append(id.into(),at,AccountEvent::Quote {symbol:symbol.into(),price},|state,_| require(state.quotes.get(symbol).is_none_or(|q| q.at <= at),"out_of_order_quote"))
    }
    pub fn submit(&mut self, mut request: OrderRequest, at: DateTime<Utc>, securities: &BTreeMap<String,SecurityFact>) -> ExecutionResult<Order> {
        require(!request.order_id.is_empty() && request.quantity > 0 && request.limit_price > Decimal::ZERO && request.stop_price > Decimal::ZERO && request.fee_budget >= Decimal::ZERO && request.signal_at < at,"invalid_order_or_nonprior_signal")?;
        request.fee_budget=money(request.fee_budget);
        let limits=&self.limits;
        self.journal.transact(|records| {
            let state=project(records)?;
            if let Some(old)=state.orders.get(&request.order_id) { require(old.request == request && old.accepted_at == at,"order_identity_conflict")?; return Ok((vec![],old.clone())); }
            financial_time(records,at)?;
            let fact=securities.get(&request.symbol).filter(|s| s.symbol == request.symbol && s.visible(at)).ok_or_else(|| ExecutionError("unknown_point_in_time_security".into()))?;
            require(fact.active && !fact.suspended,"security_not_tradable")?;
            require(fact.buy_lot > 0 && fact.tick_size > Decimal::ZERO && fact.settlement_days <= 1,"invalid_security_constraints")?;
            require(request.limit_price % fact.tick_size == Decimal::ZERO,"price_off_tick")?;
            require(!state.orders.values().any(|o| o.status.active() && o.request.symbol == request.symbol),"outstanding_order_for_symbol")?;
            state.mark(&request.symbol,at,limits)?;
            let equity=state.equity(at,limits)?;
            for order in state.orders.values().filter(|o| o.status.active()) { state.mark(&order.request.symbol,at,limits)?; }
            if request.side == Side::Buy {
                require(!state.halted,"account_halted")?;
                require(health(&state,records,at,limits).healthy,"account_health_gate")?;
                require(!fact.st,"ST_entries_disabled")?;
                require(request.quantity % fact.buy_lot == 0,"buy_quantity_off_lot")?;
                require(request.stop_price < request.limit_price,"long_stop_not_below_entry")?;
                let mut exposure=BTreeMap::<String,Decimal>::new();
                for (symbol,q) in &state.positions { if *q > 0 { exposure.insert(symbol.clone(),Decimal::from(*q)*state.mark(symbol,at,limits)?); } }
                for order in state.orders.values().filter(|o| o.status.active() && o.request.side == Side::Buy) { *exposure.entry(order.request.symbol.clone()).or_default() += order.request.limit_price*Decimal::from(order.request.quantity-order.filled); }
                *exposure.entry(request.symbol.clone()).or_default() += request.limit_price*Decimal::from(request.quantity);
                require(exposure.len() <= limits.max_positions,"position_limit")?;
                require(exposure[&request.symbol] <= equity*limits.max_symbol_weight,"symbol_weight_limit")?;
                require(exposure.values().copied().sum::<Decimal>() <= equity*limits.max_gross_weight,"gross_weight_limit")?;
                let mut sector=Decimal::ZERO;
                for (symbol,value) in &exposure { let current=securities.get(symbol).filter(|s| s.visible(at)).ok_or_else(|| ExecutionError("unknown_sector_exposure".into()))?; if current.sector == fact.sector {sector += value;} }
                require(sector <= equity*limits.max_sector_weight,"sector_weight_limit")?;
                require((request.limit_price-request.stop_price)*Decimal::from(request.quantity)+money(request.fee_budget) <= equity*limits.max_order_risk,"order_risk_limit")?;
                require(request.limit_price*Decimal::from(request.quantity)+money(request.fee_budget) <= state.cash-state.reserved_cash(None),"insufficient_unreserved_cash")?;
            } else {
                require(request.quantity <= state.sellable(&request.symbol,at),"T_plus_1_or_insufficient_position")?;
                require(request.quantity % fact.buy_lot == 0 || state.positions.get(&request.symbol) == Some(&request.quantity),"odd_lot_sell_must_liquidate_position")?;
            }
            let order=Order {request:request.clone(),security:fact.clone(),accepted_at:at,status:OrderStatus::Accepted,filled:0,fees:Decimal::ZERO};
            Ok((vec![Record {id:format!("order:{}",request.order_id),at,payload:AccountEvent::Accepted(order.clone())}],order))
        })
    }
    pub fn fill(&mut self, mut fill: Fill) -> ExecutionResult<()> {
        require(fill.quantity > 0 && fill.price > Decimal::ZERO && fill.fee >= Decimal::ZERO,"invalid_fill")?;
        fill.fee=money(fill.fee);
        let at=fill.at;
        self.append(format!("fill:{}",fill.fill_id),at,AccountEvent::Fill(fill.clone()),|state,records| {
            financial_time(records,at)?;
            let order=state.orders.get(&fill.order_id).filter(|o| o.status.active()).ok_or_else(|| ExecutionError("unknown_or_terminal_order_fill_requires_reconciliation".into()))?;
            require(at >= order.accepted_at,"fill_precedes_order")?;
            require(fill.quantity <= order.request.quantity-order.filled,"overfill")?;
            require(order.fees+fill.fee <= money(order.request.fee_budget),"fill_fee_budget_exceeded")?;
            if order.request.side == Side::Buy {
                require(fill.price <= order.request.limit_price,"buy_fill_above_limit")?;
                require(money(Decimal::from(fill.quantity)*fill.price)+fill.fee <= state.cash-state.reserved_cash(Some(&fill.order_id)),"fill_exceeds_unreserved_cash")?;
            } else {
                require(fill.price >= order.request.limit_price,"sell_fill_below_limit")?;
                require(fill.quantity <= state.sellable(&order.request.symbol,at),"T_plus_1_or_insufficient_position")?;
                require(fill.fee <= money(Decimal::from(fill.quantity)*fill.price)+state.cash,"sell_fee_exceeds_cash")?;
            }
            Ok(())
        })
    }
    pub fn cancel(&mut self, order_id: &str, at: DateTime<Utc>, confirmed: bool) -> ExecutionResult<()> {
        let event=if confirmed {AccountEvent::Canceled(order_id.into())} else {AccountEvent::CancelRequested(order_id.into())};
        self.append(format!("cancel:{confirmed}:{order_id}"),at,event,|state,records| { financial_time(records,at)?; require(state.orders.get(order_id).is_some_and(|o| o.status.active()),"cannot_cancel_terminal_order") })
    }
    pub fn reject(&mut self, order_id: &str, at: DateTime<Utc>, reason: &str) -> ExecutionResult<()> {
        require(!reason.trim().is_empty(),"rejection_reason_required")?;
        self.append(format!("reject:{order_id}"),at,AccountEvent::Rejected {order_id:order_id.into(),reason:reason.into()},|state,records| {financial_time(records,at)?; require(state.orders.get(order_id).is_some_and(|o| o.status.active()),"cannot_reject_terminal_order")})
    }
    pub fn halt(&mut self, id: &str, at: DateTime<Utc>, reason: &str) -> ExecutionResult<()> { self.append(id.into(),at,AccountEvent::Halt(reason.into()),|_,_| Ok(())) }
    pub fn reconcile(&mut self, id: &str, at: DateTime<Utc>, external: AccountSnapshot) -> ExecutionResult<bool> {
        self.journal.transact(|records| {
            if let Some(old)=records.iter().find(|r| r.id == id) { if let AccountEvent::Reconciled {external:previous,matched}=&old.payload { require(old.at == at && *previous == external,"reconciliation_identity_conflict")?; return Ok((vec![],*matched)); } return Err(ExecutionError("reconciliation_identity_conflict".into())); }
            financial_time(records,at)?;
            let matched=project(records)?.snapshot() == external;
            Ok((vec![Record {id:id.into(),at,payload:AccountEvent::Reconciled {external,matched}}],matched))
        })
    }
    pub fn resume(&mut self, id: &str, at: DateTime<Utc>, reason: &str) -> ExecutionResult<()> {
        require(!reason.trim().is_empty(),"operator_reason_required")?;
        self.append(id.into(),at,AccountEvent::Resumed(reason.into()),|state,records| {financial_time(records,at)?; require(state.reconciled,"successful_reconciliation_required")})
    }
    pub fn corporate_action(&mut self, mut action: CorporateAction, at: DateTime<Utc>) -> ExecutionResult<()> {
        require(action.share_ratio > Decimal::ZERO && action.net_cash >= Decimal::ZERO && action.known_at <= at && !action.source.is_empty(),"explicit_known_entitlements_required")?;
        action.net_cash=money(action.net_cash);
        self.append(format!("action:{}",action.action_id),at,AccountEvent::CorporateAction(action.clone()),|state,records| {
            financial_time(records,at)?;
            require(!state.orders.values().any(|o| o.request.symbol == action.symbol && o.status.active()),"resolve_open_orders_before_corporate_action")?;
            for lot in state.lots.iter().filter(|l| l.symbol == action.symbol) { let q=Decimal::from(lot.quantity)*action.share_ratio; require(q.fract() == Decimal::ZERO && q.to_u64().is_some(),"fractional_entitlement_requires_external_resolution")?; }
            Ok(())
        })
    }
    pub fn monitor(&mut self, id: &str, at: DateTime<Utc>) -> ExecutionResult<Health> {
        let limits=&self.limits;
        self.journal.transact(|records| {
            if let Some(old)=records.iter().find(|r| r.id == id) { if let AccountEvent::Health(h)=&old.payload {require(old.at == at,"monitor_identity_conflict")?; return Ok((vec![],h.clone()));} return Err(ExecutionError("monitor_identity_conflict".into())); }
            let h=health(&project(records)?,records,at,limits);
            let mut events=vec![Record {id:id.into(),at,payload:AccountEvent::Health(h.clone())}];
            if !h.healthy {events.push(Record {id:format!("{id}:halt"),at,payload:AccountEvent::Halt(h.reason.clone())});}
            Ok((events,h))
        })
    }
    /// 来源 account_report.py：已知时点 FIFO 成本与现金分红归因，过期报价返回未知净值。
    pub fn report(&self, at:DateTime<Utc>) -> ExecutionResult<AccountReport> {
        let records:Vec<_>=self.journal.records()?.into_iter().filter(|r| r.at <= at).collect();
        require(records.iter().any(|r| matches!(r.payload,AccountEvent::Opened {..})),"account_did_not_exist_at_requested_time")?;
        let state=project(&records)?;
        let mut lots=BTreeMap::<String,Vec<(u64,Decimal)>>::new();
        let mut realized=BTreeMap::<String,Decimal>::new();
        let mut dividends=BTreeMap::<String,Decimal>::new();
        let mut fees=BTreeMap::<String,Decimal>::new();
        let mut counts=BTreeMap::<String,usize>::new();
        for record in records {
            match record.payload {
                AccountEvent::Fill(fill) => {
                    let order=&state.orders[&fill.order_id]; let symbol=&order.request.symbol;
                    *fees.entry(symbol.clone()).or_default() += fill.fee;
                    *counts.entry(symbol.clone()).or_default() += 1;
                    let value=money(fill.price*Decimal::from(fill.quantity));
                    let book=lots.entry(symbol.clone()).or_default();
                    if order.request.side == Side::Buy {book.push((fill.quantity,value+fill.fee));}
                    else {
                        let mut remaining=fill.quantity; let mut cost=Decimal::ZERO;
                        for (quantity,basis) in book {
                            let used=remaining.min(*quantity); if used == 0 {continue;}
                            let part=*basis*Decimal::from(used)/Decimal::from(*quantity);
                            *basis -= part; *quantity -= used; cost += part; remaining -= used;
                            if remaining == 0 {break;}
                        }
                        require(remaining == 0,"sell_exceeds_FIFO_inventory")?;
                        *realized.entry(symbol.clone()).or_default() += value-fill.fee-cost;
                    }
                }
                AccountEvent::CorporateAction(action) => {
                    *dividends.entry(action.symbol.clone()).or_default() += action.net_cash;
                    if let Some(book)=lots.get_mut(&action.symbol) {for (q,_) in book {*q=(Decimal::from(*q)*action.share_ratio).to_u64().ok_or_else(|| ExecutionError("share_quantity_overflow".into()))?;}}
                }
                _ => {}
            }
        }
        let symbols:std::collections::BTreeSet<_>=lots.keys().chain(dividends.keys()).cloned().collect();
        let mut rows=Vec::new(); let mut missing=Vec::new(); let mut value=Decimal::ZERO; let mut unrealized_total=Decimal::ZERO;
        for symbol in symbols {
            let quantity:u64=lots.get(&symbol).into_iter().flatten().map(|(q,_)| *q).sum();
            let cost:Decimal=lots.get(&symbol).into_iter().flatten().map(|(_,basis)| *basis).sum();
            require(quantity == *state.positions.get(&symbol).unwrap_or(&0),"FIFO_ledger_share_mismatch")?;
            let market_value=if quantity == 0 {Some(Decimal::ZERO)} else {state.mark(&symbol,at,&self.limits).ok().map(|mark| mark*Decimal::from(quantity))};
            let unrealized=market_value.map(|mv| mv-cost);
            if let Some(mv)=market_value {value += mv; unrealized_total += mv-cost;} else {missing.push(symbol.clone());}
            rows.push(SymbolAccountReport {symbol:symbol.clone(),quantity,cost_basis:money(cost),market_value:market_value.map(money),unrealized_pnl:unrealized.map(money),realized_pnl:money(*realized.get(&symbol).unwrap_or(&Decimal::ZERO)),corporate_cash:money(*dividends.get(&symbol).unwrap_or(&Decimal::ZERO)),fees:money(*fees.get(&symbol).unwrap_or(&Decimal::ZERO)),fill_count:*counts.get(&symbol).unwrap_or(&0)});
        }
        let total_realized:Decimal=realized.values().copied().sum(); let total_dividends:Decimal=dividends.values().copied().sum();
        let equity=missing.is_empty().then_some(state.cash+value);
        let pnl=equity.map(|nav| nav-state.initial_cash);
        if let Some(pnl)=pnl {require(money(pnl) == money(total_realized+unrealized_total+total_dividends),"account_PnL_does_not_conserve_NAV")?;}
        Ok(AccountReport {asof:at,currency:"CNY".into(),basis:"raw_shares_FIFO_including_commissions_not_tax_accounting".into(),cash:money(state.cash),reserved_cash:money(state.reserved_cash(None)),equity:equity.map(money),total_pnl:pnl.map(money),realized_pnl:money(total_realized),corporate_cash:money(total_dividends),fees:money(fees.values().copied().sum()),pnl_conserved:pnl.map(|_| true),unmarked_symbols:missing,halted:state.halted,reconciled:state.reconciled,symbols:rows})
    }
}
