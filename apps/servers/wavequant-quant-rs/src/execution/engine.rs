use super::{Account, AccountState, ExecutionResult, Fill, Health, OpeningTick, OrderRequest, PaperVenue,
    SecurityFact, journal::require, opening_permissions, session};
use chrono::{DateTime, Utc};
use rust_decimal::Decimal;
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

/// 原始股数和未复权价格。开盘容量必须来自已观察委托/成交容量，不能填当日最终成交量。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ExecutionBar {
    pub symbol: String, pub open_at: DateTime<Utc>, pub close_at: DateTime<Utc>,
    pub open: Decimal, pub high: Decimal, pub low: Decimal, pub close: Decimal,
    pub previous_close: Decimal, pub volume: u64, pub opening_capacity: u64,
    pub security: SecurityFact,
}

/// 仅提供已收盘前缀。策略负责真实资格与风险规则，执行器不创建或猜测买卖信号。
pub trait Strategy {
    fn on_close(&mut self, visible: &[ExecutionBar], account: &AccountState) -> ExecutionResult<Vec<OrderRequest>>;
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ExecutionRejection { pub order_id: String, pub at: DateTime<Utc>, pub reason: String }
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EquityObservation { pub at: DateTime<Utc>, pub cash: Decimal, pub health: Health }
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ReplayResult {
    pub fills: Vec<Fill>, pub rejected: Vec<ExecutionRejection>, pub equity: Vec<EquityObservation>,
    pub final_account: AccountState, pub pending_signals: Vec<OrderRequest>,
}

/// Python PaperVenue 的逐开盘驱动：多标的共享账户，严格先接收成交再消费收盘信号。
/// 不等价于 analytics/backtest.py 的分钟/同收盘策略调度器。
pub struct ReplayEngine<'a> { pub account: &'a mut Account, pub venue: &'a mut PaperVenue }
impl ReplayEngine<'_> {
    pub fn run<S: Strategy>(&mut self, bars:&[ExecutionBar], strategy:&mut S) -> ExecutionResult<ReplayResult> {
        let mut last=BTreeMap::<String,DateTime<Utc>>::new();
        let mut events=Vec::with_capacity(bars.len()*2);
        for (index,bar) in bars.iter().enumerate() {
            require(bar.open_at < bar.close_at && bar.open > Decimal::ZERO && bar.low > Decimal::ZERO && bar.low <= bar.open && bar.low <= bar.close && bar.high >= bar.open && bar.high >= bar.close && bar.previous_close > Decimal::ZERO,"invalid_execution_bar")?;
            require(bar.security.symbol == bar.symbol && bar.security.visible(bar.open_at),"unknown_point_in_time_security")?;
            require(last.get(&bar.symbol).is_none_or(|at| *at < bar.open_at),"overlapping_or_out_of_order_symbol_bars")?;
            last.insert(bar.symbol.clone(),bar.close_at);
            events.push((bar.open_at,0_u8,index)); events.push((bar.close_at,1_u8,index));
        }
        events.sort_by(|a,b| (a.0,a.1,&bars[a.2].symbol).cmp(&(b.0,b.1,&bars[b.2].symbol)));
        let mut visible=Vec::new();
        let mut pending=Vec::<OrderRequest>::new();
        let mut securities=BTreeMap::<String,SecurityFact>::new();
        let mut result=ReplayResult {fills:vec![],rejected:vec![],equity:vec![],final_account:self.account.state()?,pending_signals:vec![]};
        self.venue.dispatch(self.account)?;
        self.venue.receive(self.account)?;
        for (at,phase,index) in events {
            let bar=&bars[index];
            if phase == 0 {
                securities.insert(bar.symbol.clone(),bar.security.clone());
                self.account.quote(&format!("replay:open:{}:{at}",bar.symbol),&bar.symbol,bar.open,at)?;
                let (buyable,sellable)=opening_permissions(&bar.symbol,session(at),bar.open,bar.previous_close,bar.security.active && !bar.security.suspended,bar.security.st)?;
                let mut retained=Vec::new();
                for request in pending.drain(..) {
                    if request.symbol != bar.symbol || request.signal_at >= at {retained.push(request); continue;}
                    match self.account.submit(request.clone(),at,&securities) {
                        Ok(order) => self.venue.accept(&order)?,
                        Err(error) => result.rejected.push(ExecutionRejection {order_id:request.order_id,at,reason:error.to_string()}),
                    }
                }
                pending=retained;
                result.fills.extend(self.venue.match_open(&OpeningTick {tick_id:format!("{}:{at}",bar.symbol),symbol:bar.symbol.clone(),at,price:bar.open,available_volume:bar.opening_capacity,buyable,sellable})?);
                self.venue.receive(self.account)?;
            } else {
                self.account.quote(&format!("replay:close:{}:{at}",bar.symbol),&bar.symbol,bar.close,at)?;
                visible.push(bar.clone());
                let state=self.account.state()?;
                for request in strategy.on_close(&visible,&state)? {
                    require(request.signal_at <= at,"strategy_signal_uses_future_time")?;
                    require(visible.iter().any(|known| known.symbol == request.symbol && known.close_at >= request.signal_at),"strategy_signal_has_no_visible_source")?;
                    pending.push(request);
                }
                let health=self.account.monitor(&format!("replay:health:{}:{at}",bar.symbol),at)?;
                result.equity.push(EquityObservation {at,cash:state.cash,health});
            }
        }
        result.final_account=self.account.state()?; result.pending_signals=pending;
        Ok(result)
    }
}
