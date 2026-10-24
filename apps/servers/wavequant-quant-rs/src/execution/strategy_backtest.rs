//! 来源 analytics/backtest.py 的账户成交规则。策略提供带知识时点的真实决策；
//! 这里管理资金、数量和执行时钟，不识别另一套趋势或凭空构造入场信号。
use super::{ExecutionError, ExecutionResult, FeeConfig, Side, journal::require, opening_permissions,
    price_limit_bounds, session, transaction_fee};
use crate::analysis::trading::{RuleCandle, RuleDecision};
use crate::data::{adjustments::ExecutionBar as SourceBar, minute::{MinuteBar, verify_minute_day}};
use chrono::{DateTime, Duration, NaiveDate, Utc};
use rust_decimal::{Decimal, prelude::ToPrimitive};
use serde::{Deserialize, Serialize};
use std::{collections::{BTreeMap, BTreeSet}, str::FromStr};

fn dec(value:f64) -> ExecutionResult<Decimal> {
    require(value.is_finite(),"nonfinite_market_value")?;
    Decimal::from_str(&value.to_string()).map_err(|e|ExecutionError(e.to_string()))
}
fn stamp(value:&str) -> ExecutionResult<DateTime<Utc>> {
    DateTime::parse_from_rfc3339(value).map(|t|t.with_timezone(&Utc)).map_err(|e|ExecutionError(e.to_string()))
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all="snake_case")]
pub enum ExecutionMode { NextOpen, SameDayOpen, SameDayClose, Intraday5mNextOpen }
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all="snake_case")]
pub enum ObservationPhase { DailyOpen, CompletedMinute, MinuteOpen, DailyClose }
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BacktestConfig {
    pub initial_capital:Decimal, pub max_positions:usize, pub max_position_weight:Decimal,
    pub risk_fraction:Decimal, pub lot_size:u64, pub minimum_entry_shares:u64,
    pub liquidity_lookback:usize, pub max_participation:Decimal, pub slippage_bps:Decimal,
    pub take_profit_r:Decimal, pub max_entry_gap:Decimal, pub entry_ttl_bars:usize,
    pub allow_add_on:bool, pub allow_same_day_exit:bool, pub nonflat_limit_close_fill:bool,
    pub net_reward_risk_filter:bool, pub fees:FeeConfig,
    pub max_hold_bars:usize,pub exit_on_target:bool,
}
impl Default for BacktestConfig {
    fn default()->Self {Self {initial_capital:Decimal::from(1_000_000),max_positions:5,max_position_weight:Decimal::new(2,1),risk_fraction:Decimal::new(1,2),lot_size:100,minimum_entry_shares:1,liquidity_lookback:20,max_participation:Decimal::new(1,2),slippage_bps:Decimal::from(5),take_profit_r:Decimal::from(2),max_entry_gap:Decimal::new(5,2),entry_ttl_bars:1,allow_add_on:false,allow_same_day_exit:false,nonflat_limit_close_fill:false,net_reward_risk_filter:true,fees:FeeConfig::default(),max_hold_bars:20,exit_on_target:true}}
}
impl BacktestConfig {
    pub fn validate(&self)->ExecutionResult<()> {
        require(self.initial_capital>Decimal::ZERO && self.max_positions>0 && self.lot_size>0 && self.minimum_entry_shares>0 && self.liquidity_lookback>0 && self.entry_ttl_bars>0&&self.max_hold_bars>0,"invalid_backtest_configuration")?;
        require([self.max_position_weight,self.risk_fraction,self.max_participation].iter().all(|x|*x>Decimal::ZERO&&*x<=Decimal::ONE),"invalid_backtest_fraction")?;
        require(self.slippage_bps>=Decimal::ZERO&&self.slippage_bps<Decimal::from(10_000)&&self.take_profit_r>Decimal::ZERO&&self.max_entry_gap>=Decimal::ZERO,"invalid_backtest_costs")?;
        transaction_fee(Decimal::ZERO,NaiveDate::MIN,Side::Buy,&self.fees)?; Ok(())
    }
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EntryDecision {
    pub symbol:String, pub known_at:DateTime<Utc>, pub bar_index:usize, pub mode:ExecutionMode,
    pub reason:String, pub reference_price:Decimal, pub stop_price:Decimal,
    pub target_price:Option<Decimal>, pub minimum_reward_risk:Option<Decimal>, pub rvol:Option<Decimal>,
    /// 调用方已完成资格/同N轮次/目标/量价门禁；拒绝原因不可由执行器忽略。
    pub rejection:Option<String>, pub evidence:BTreeMap<String,crate::indexed_json::Value>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ExitDecision {
    pub symbol:String, pub known_at:DateTime<Utc>, pub bar_index:usize, pub mode:ExecutionMode,
    pub rule:RuleDecision, pub remaining_quantity:Option<Decimal>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StopDecision {pub symbol:String,pub known_at:DateTime<Utc>,pub stop:Decimal}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum StrategyDecision { Enter(EntryDecision), Exit(ExitDecision), RaiseStop(StopDecision) }
impl StrategyDecision {
    fn symbol(&self)->&str {match self {Self::Enter(d)=>&d.symbol,Self::Exit(d)=>&d.symbol,Self::RaiseStop(d)=>&d.symbol}}
    fn known_at(&self)->DateTime<Utc> {match self {Self::Enter(d)=>d.known_at,Self::Exit(d)=>d.known_at,Self::RaiseStop(d)=>d.known_at}}
    fn mode(&self)->ExecutionMode {match self {Self::Enter(d)=>d.mode,Self::Exit(d)=>d.mode,Self::RaiseStop(_)=>ExecutionMode::NextOpen}}
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BacktestPosition {
    pub symbol:String, pub entry_index:usize, pub entry_time:DateTime<Utc>, pub entry_price:Decimal,
    pub quantity:Decimal, pub entry_fee:Decimal, pub stop:Decimal, pub initial_stop:Decimal,
    pub target:Decimal, pub reason:String, pub initial_quantity:Decimal,
    pub initial_entry_notional:Decimal, pub initial_entry_fee:Decimal,
    pub last_buy_date:NaiveDate, pub bought_today_quantity:Decimal,
    pub exit_notional:Decimal, pub exit_fees:Decimal, pub realized_pnl:Decimal,
    pub pressure_warning:Option<BTreeMap<String,crate::indexed_json::Value>>,pub record_high_warning:Option<BTreeMap<String,crate::indexed_json::Value>>,
    pub wave_reduced:bool,pub trend_last_fall_high_reduced:bool,pub inverse_index:Option<usize>,
    pub intraday_add_on_prior_stop:Option<Decimal>,pub intraday_add_on_date:Option<NaiveDate>,
    pub entry_evidence:BTreeMap<String,crate::indexed_json::Value>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BacktestTrade {
    pub symbol:String, pub entry_time:DateTime<Utc>, pub exit_time:DateTime<Utc>,
    pub entry_price:Decimal, pub exit_price:Decimal, pub initial_stop:Decimal, pub quantity:Decimal,
    pub gross_return:Decimal, pub net_return:Decimal, pub hold_bars:usize,
    pub entry_reason:String, pub exit_reason:String, pub pnl:Decimal, pub fees:Decimal,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BacktestOrder {
    pub timestamp:DateTime<Utc>, pub symbol:String, pub side:Side, pub status:String, pub reason:String,
    pub mode:ExecutionMode, pub quantity:Option<Decimal>, pub price:Option<Decimal>, pub fee:Option<Decimal>,
    pub evidence:BTreeMap<String,crate::indexed_json::Value>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BacktestObservation {
    pub at:DateTime<Utc>, pub phase:ObservationPhase, pub bar_index:usize, pub candle:RuleCandle,
    pub execution_price:Decimal, pub buyable:bool, pub sellable:bool,
    pub observed_nonflat_buyable:bool, pub observed_nonflat_sellable:bool,
    pub lower_limit:Option<Decimal>, pub liquidity_capacity:Decimal,
    /// 当次刚完成的五分钟区间；只用于成交后极值，不包含后续分钟。
    pub completed_interval:Option<RuleCandle>,
}
/// 当前部分K线及之前已完成K线。未知的分钟后缀和当天最终高低量不向策略公开。
pub trait DecisionStrategy {
    /// 同时点匹配完成后发布实际账户，策略据此保留未成交的全清/剩余量请求。
    fn observe_account(&mut self,_account:&StrategyBacktest) {}
    fn after_observation(&mut self,_account:&StrategyBacktest,_observations:&[BacktestObservation])->ExecutionResult<()> {Ok(())}
    fn entry_rejection(&mut self,_visible:&[RuleCandle],_observation:&BacktestObservation,_entry:&EntryDecision)->ExecutionResult<Option<String>> {Ok(None)}
    /// 昨日冻结警告对今日开盘的条件退出，先于所有开盘买卖匹配受理。
    fn open_risks(&mut self,_visible:&[RuleCandle],_observation:&BacktestObservation,
        _positions:&BTreeMap<String,BacktestPosition>)->ExecutionResult<Vec<ExitDecision>> {Ok(vec![])}
    fn decide(&mut self, visible:&[RuleCandle], observation:&BacktestObservation,
        positions:&BTreeMap<String,BacktestPosition>)->ExecutionResult<Vec<StrategyDecision>>;
}
/// 主策略选择退出优先级前可调用的既有硬风控。入场当日必须传入成交后真实观察极值。
pub fn hard_exit_decision(position:&BacktestPosition,observation:&BacktestObservation,config:&BacktestConfig,
    post_entry_low:Decimal,post_entry_high:Decimal)->ExecutionResult<Option<RuleDecision>> {
    require(post_entry_low>Decimal::ZERO&&post_entry_low<=post_entry_high,"invalid_post_entry_extrema")?;
    let reason=if position.intraday_add_on_date==Some(session(observation.at))&&position.intraday_add_on_prior_stop.is_some_and(|s|post_entry_low<=s)||post_entry_low<=position.stop {Some("structural_stop_observed")}
        else if config.exit_on_target&&post_entry_high>=position.target{Some("target_observed")}
        else if observation.bar_index.saturating_sub(position.entry_index)>=config.max_hold_bars{Some("time_exit")}else{None};
    Ok(reason.map(|reason|RuleDecision::new(reason,observation.bar_index,Decimal::ONE)))
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SizedEntry {pub quantity:Decimal,pub price:Decimal,pub fee:Decimal,pub stop:Decimal,pub target:Decimal,pub expected_net_reward:Decimal,pub expected_net_loss:Decimal}

/// 四项约束和手续费后的权重/风险复查来自 execute_entry；数量使用复权单位。
pub fn size_entry(config:&BacktestConfig,signal:&EntryDecision,observation:&BacktestObservation,
    cash:Decimal,equity:Decimal,existing:Option<&BacktestPosition>,bought_today:Decimal,price:Decimal)->ExecutionResult<SizedEntry> {
    require(price>Decimal::ZERO&&signal.stop_price>Decimal::ZERO&&signal.reference_price>Decimal::ZERO,"invalid_entry_prices")?;
    let existing_quantity=existing.map_or(Decimal::ZERO,|p|p.quantity);
    let effective_stop=existing.map_or(signal.stop_price,|p|p.stop.max(signal.stop_price));
    require(price>effective_stop,"existing_stop_at_or_above_execution")?;
    let existing_risk=existing.map_or(Decimal::ZERO,|p|(p.entry_price-effective_stop).max(Decimal::ZERO)*p.quantity);
    let remaining_risk=(equity*config.risk_fraction-existing_risk).max(Decimal::ZERO);
    let risk=price-signal.stop_price;
    let target=signal.target_price.unwrap_or(price+config.take_profit_r*risk);
    require(target>price,"target_exhausted_at_execution")?;
    let limits=[((equity*config.max_position_weight-existing_quantity*price)/price).max(Decimal::ZERO),remaining_risk/(price-effective_stop),(observation.liquidity_capacity-bought_today).max(Decimal::ZERO),cash/price];
    let units=limits.into_iter().fold(Decimal::MAX,Decimal::min);
    let factor=observation.candle.adjustment_factor;
    require(factor>Decimal::ZERO,"invalid_adjustment_factor")?;
    let lot=Decimal::from(config.lot_size)/factor;
    let maximum=(units/lot).floor().max(Decimal::ZERO).to_u64().ok_or_else(||ExecutionError("sizing_quantity_out_of_range".into()))?;
    let date=session(observation.at);
    let feasible=|lots:u64|->ExecutionResult<bool> {
        let quantity=Decimal::from(lots)*lot; let fee=transaction_fee(price*quantity,date,Side::Buy,&config.fees)?;
        Ok(price*quantity+fee<=cash&&(existing_quantity+quantity)*price<=(equity-fee)*config.max_position_weight&&existing_risk+(price-effective_stop)*quantity<=(equity-fee)*config.risk_fraction)
    };
    // 单调约束的二分定位等价于原实现逐手扣减，避免大资金输入导致线性退让。
    let(mut low,mut high)=(0,maximum);
    while low<high {let mid=low+(high-low)/2+1;if feasible(mid)?{low=mid;}else{high=mid-1;}}
    let quantity=Decimal::from(low)*lot;
    let minimum=Decimal::from(if existing.is_some(){config.lot_size}else{config.minimum_entry_shares})/factor;
    require(low>0&&quantity>=minimum,"sizing_below_one_lot")?;
    let fee=transaction_fee(price*quantity,date,Side::Buy,&config.fees)?;
    let slip=config.slippage_bps/Decimal::from(10_000);
    let reward=(target*(Decimal::ONE-slip)-price)*quantity-fee-transaction_fee(target*(Decimal::ONE-slip)*quantity,date,Side::Sell,&config.fees)?;
    let loss=(price-signal.stop_price*(Decimal::ONE-slip))*quantity+fee+transaction_fee(signal.stop_price*(Decimal::ONE-slip)*quantity,date,Side::Sell,&config.fees)?;
    require(loss>Decimal::ZERO,"nonpositive_entry_risk")?;
    if config.net_reward_risk_filter {if let Some(required)=signal.minimum_reward_risk {require(reward/loss>=required,"insufficient_net_reward_risk")?;}}
    Ok(SizedEntry {quantity,price,fee,stop:effective_stop,target,expected_net_reward:reward,expected_net_loss:loss})
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PortfolioObservation {pub at:DateTime<Utc>,pub cash:Decimal,pub equity:Decimal,pub exposure:Decimal,pub positions:usize}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StrategyBacktest {
    pub config:BacktestConfig,pub cash:Decimal,pub positions:BTreeMap<String,BacktestPosition>,
    pub orders:Vec<BacktestOrder>,pub trades:Vec<BacktestTrade>,pub total_fees:Decimal,pub turnover:Decimal,
    pub marks:BTreeMap<String,Decimal>,pub pending_entries:BTreeMap<String,EntryDecision>,
    pub pending_exits:BTreeMap<String,ExitDecision>,pub equity:Vec<(DateTime<Utc>,Decimal)>,
    pub portfolio_days:Vec<PortfolioObservation>,
    #[serde(default)] observing_structure_exits:BTreeMap<String,(ExitDecision,Decimal)>,
    bought_today:BTreeMap<String,Decimal>,sold_today:BTreeSet<String>,current_session:Option<NaiveDate>,last_time:Option<DateTime<Utc>>,
}
impl StrategyBacktest {
    pub fn new(config:BacktestConfig)->ExecutionResult<Self>{config.validate()?;Ok(Self {cash:config.initial_capital,config,positions:BTreeMap::new(),orders:vec![],trades:vec![],total_fees:Decimal::ZERO,turnover:Decimal::ZERO,marks:BTreeMap::new(),pending_entries:BTreeMap::new(),pending_exits:BTreeMap::new(),equity:vec![],portfolio_days:vec![],observing_structure_exits:BTreeMap::new(),bought_today:BTreeMap::new(),sold_today:BTreeSet::new(),current_session:None,last_time:None})}
    pub fn equity_value(&self)->ExecutionResult<Decimal>{let mut value=self.cash;for(symbol,p)in &self.positions {value+=p.quantity * *self.marks.get(symbol).ok_or_else(||ExecutionError(format!("missing_mark:{symbol}")))?;}Ok(value)}
    fn log(&mut self,observation:&BacktestObservation,side:Side,status:&str,reason:&str,mode:ExecutionMode,evidence:BTreeMap<String,crate::indexed_json::Value>){self.orders.push(BacktestOrder {timestamp:observation.at,symbol:observation.candle.symbol.clone(),side,status:status.into(),reason:reason.into(),mode,quantity:None,price:None,fee:None,evidence});}
    fn entry(&mut self,signal:EntryDecision,observation:&BacktestObservation)->ExecutionResult<()> {
        let symbol=&signal.symbol;
        let existing=self.positions.get(symbol);
        let nonflat=self.config.nonflat_limit_close_fill&&observation.observed_nonflat_buyable&&matches!(signal.mode,ExecutionMode::SameDayClose|ExecutionMode::Intraday5mNextOpen);
        let buyable=observation.buyable||nonflat;
        let price=if !observation.buyable&&nonflat {observation.execution_price}else{observation.execution_price*(Decimal::ONE+self.config.slippage_bps/Decimal::from(10_000))};
        let rejected=signal.rejection.clone().or_else(||{
            let reason=if existing.is_some()&&!self.config.allow_add_on{Some("already_held")}
            else if existing.is_some()&&self.pending_exits.contains_key(symbol){Some("exit_pending")}
            else if existing.is_some()&&self.sold_today.contains(symbol){Some("same_day_exit_priority")}
            else if existing.is_none()&&self.positions.len()>=self.config.max_positions{Some("position_limit")}
            else if !buyable||observation.candle.raw_trading_active==Some(false){Some("not_buyable")}
            else if observation.execution_price<=signal.stop_price{Some("invalidated_at_execution")}
            else if signal.reference_price<=Decimal::ZERO{Some("invalid_reference_price")}
            else if observation.execution_price/signal.reference_price-Decimal::ONE>self.config.max_entry_gap{Some("entry_gap")}
            else {None};reason.map(str::to_owned)
        });
        if let Some(reason)=rejected{self.log(observation,Side::Buy,"cancelled",&reason,signal.mode,signal.evidence);return Ok(());}
        let equity=self.equity_value()?;
        let sized=match size_entry(&self.config,&signal,observation,self.cash,equity,existing,*self.bought_today.get(symbol).unwrap_or(&Decimal::ZERO),price){Ok(s)=>s,Err(error)=>{self.log(observation,Side::Buy,"cancelled",&error.to_string(),signal.mode,signal.evidence);return Ok(());}};
        let mut evidence=signal.evidence.clone();
        evidence.insert("expected_net_reward".into(),crate::indexed_json::json!(sized.expected_net_reward));evidence.insert("expected_net_loss".into(),crate::indexed_json::json!(sized.expected_net_loss));
        evidence.insert("raw_shares".into(),crate::indexed_json::json!(sized.quantity*observation.candle.adjustment_factor));
        if signal.mode==ExecutionMode::SameDayClose{evidence.insert("execution_assumption".into(),crate::indexed_json::json!("daily_close_simulation"));}
        if !observation.buyable&&nonflat{evidence.insert("fill_assumption".into(),crate::indexed_json::json!("observed_nonflat_limit_without_queue_verification"));}
        self.cash-=sized.price*sized.quantity+sized.fee;self.total_fees+=sized.fee;self.turnover+=sized.price*sized.quantity;
        *self.bought_today.entry(symbol.clone()).or_default()+=sized.quantity;
        if let Some(p)=self.positions.get_mut(symbol){
            if signal.mode==ExecutionMode::Intraday5mNextOpen&&p.intraday_add_on_date!=Some(session(observation.at)){p.intraday_add_on_prior_stop=Some(p.stop);p.intraday_add_on_date=Some(session(observation.at));}
            p.entry_price=(p.entry_price*p.quantity+sized.price*sized.quantity)/(p.quantity+sized.quantity);p.quantity+=sized.quantity;p.entry_fee+=sized.fee;
            p.initial_quantity+=sized.quantity;p.initial_entry_notional+=sized.price*sized.quantity;p.initial_entry_fee+=sized.fee;p.stop=sized.stop;p.target=p.target.max(sized.target);
            p.bought_today_quantity=if p.last_buy_date==session(observation.at){p.bought_today_quantity+sized.quantity}else{sized.quantity};p.last_buy_date=session(observation.at);
            p.entry_evidence=signal.evidence.clone();
        }else{self.positions.insert(symbol.clone(),BacktestPosition {symbol:symbol.clone(),entry_index:observation.bar_index,entry_time:observation.at,entry_price:sized.price,quantity:sized.quantity,entry_fee:sized.fee,stop:sized.stop,initial_stop:signal.stop_price,target:sized.target,reason:signal.reason.clone(),initial_quantity:sized.quantity,initial_entry_notional:sized.price*sized.quantity,initial_entry_fee:sized.fee,last_buy_date:session(observation.at),bought_today_quantity:sized.quantity,exit_notional:Decimal::ZERO,exit_fees:Decimal::ZERO,realized_pnl:Decimal::ZERO,pressure_warning:None,record_high_warning:None,wave_reduced:false,trend_last_fall_high_reduced:false,inverse_index:None,intraday_add_on_prior_stop:None,intraday_add_on_date:None,entry_evidence:signal.evidence.clone()});}
        self.orders.push(BacktestOrder {timestamp:observation.at,symbol:symbol.clone(),side:Side::Buy,status:"filled".into(),reason:signal.reason,mode:signal.mode,quantity:Some(sized.quantity),price:Some(sized.price),fee:Some(sized.fee),evidence});Ok(())
    }
    fn exit(&mut self,mut decision:ExitDecision,observation:&BacktestObservation)->ExecutionResult<()> {
        let Some(pos)=self.positions.get(&decision.symbol)else{self.pending_exits.remove(&decision.symbol);return Ok(());};
        let lot=Decimal::from(self.config.lot_size)/observation.candle.adjustment_factor;
        let fraction=decision.rule.fraction;
        require(fraction>Decimal::ZERO&&fraction<=Decimal::ONE,"invalid_exit_fraction")?;
        let mut quantity=if fraction==Decimal::ONE{pos.quantity}else{(pos.quantity*fraction/lot).floor()*lot};
        if let Some(remaining)=decision.remaining_quantity{quantity=pos.quantity.min(remaining);}
        else if decision.rule.cumulative {
            let desired=pos.initial_quantity*fraction-(pos.initial_quantity-pos.quantity);
            let round_up=matches!(decision.rule.reason.as_str(),"breakout_double_volume_bearish_limit_open_reduce_90"|"double_volume_bullish_breakout_next_bearish_lower_close_reduce_90");
            quantity=if round_up{(desired/lot).ceil()*lot}else{(desired/lot).floor()*lot};quantity=quantity.max(Decimal::ZERO).min(pos.quantity);
        }
        if quantity<=Decimal::ZERO {
            if decision.rule.cumulative||matches!(decision.rule.reason.as_str(),"pressure_gap_adverse_reduce"|"record_high_resistance_reduce"){self.log(observation,Side::Sell,"deferred","reduction_below_one_lot",decision.mode,decision.rule.evidence);self.pending_exits.remove(&decision.symbol);return Ok(());}
            quantity=pos.quantity;
        }
        let available=if !self.config.allow_same_day_exit&&session(observation.at)==pos.last_buy_date{pos.quantity-pos.bought_today_quantity}else{pos.quantity};
        if available<=Decimal::ZERO{self.log(observation,Side::Sell,"deferred","T+1",decision.mode,decision.rule.evidence.clone());decision.remaining_quantity=Some(quantity);decision.mode=ExecutionMode::NextOpen;self.pending_exits.insert(decision.symbol.clone(),decision);return Ok(());}
        let deferred=(quantity-available).max(Decimal::ZERO);quantity=quantity.min(available);
        let queue=self.config.nonflat_limit_close_fill&&!observation.sellable&&observation.observed_nonflat_sellable&&matches!(decision.mode,ExecutionMode::SameDayClose|ExecutionMode::Intraday5mNextOpen);
        let refusal=if !observation.sellable&&!queue||observation.candle.raw_trading_active==Some(false){Some("not_sellable")}else if quantity>observation.liquidity_capacity{Some("liquidity_capacity")}else{None};
        if let Some(reason)=refusal{self.log(observation,Side::Sell,"deferred",reason,decision.mode,decision.rule.evidence.clone());decision.mode=ExecutionMode::NextOpen;self.pending_exits.insert(decision.symbol.clone(),decision);return Ok(());}
        let mut price=if queue{observation.execution_price}else{observation.execution_price*(Decimal::ONE-self.config.slippage_bps/Decimal::from(10_000))};
        if let Some(lower)=observation.lower_limit{if observation.execution_price<lower{self.log(observation,Side::Sell,"deferred","execution_below_lower_limit",decision.mode,decision.rule.evidence.clone());decision.mode=ExecutionMode::NextOpen;self.pending_exits.insert(decision.symbol.clone(),decision);return Ok(());}price=price.max(lower);}
        let fee=transaction_fee(price*quantity,session(observation.at),Side::Sell,&self.config.fees)?;
        let allocated_fee=pos.entry_fee*quantity/pos.quantity;let pnl=price*quantity-fee-pos.entry_price*quantity-allocated_fee;
        let closed=quantity==pos.quantity;
        let pos=self.positions.get_mut(&decision.symbol).ok_or_else(||ExecutionError("position_disappeared".into()))?;
        pos.exit_notional+=price*quantity;pos.exit_fees+=fee;pos.realized_pnl+=pnl;pos.quantity-=quantity;pos.entry_fee-=allocated_fee;
        if !closed {
            if decision.rule.reason=="pressure_gap_adverse_reduce" {pos.pressure_warning=Some(decision.rule.evidence.clone());}
            if decision.rule.reason=="record_high_resistance_reduce" {pos.record_high_warning=Some(decision.rule.evidence.clone());}
            if matches!(decision.rule.reason.as_str(),"wave_volume_shadows_reduce"|"wave_gap_reversal_reduce"|"wave_upper_rejection_reduce"|"wave_target_upper_shadow_reduce"|"wave_ordinary_equal_upper_shadow_reduce"|"wave_c_0618_upper_shadow_reduce"|"wave_target_bearish_reduce"|"wave_two_t_resistance_reduce"|"wave_two_t_volume_bearish_reduce"|"wave_c_equal_near_resistance_reduce") {pos.wave_reduced=true;}
            if decision.rule.reason=="trend_last_fall_high_upper_shadow_reduce" {pos.trend_last_fall_high_reduced=true;}
            if decision.rule.reason=="inverse_n_close_reduce_90" {pos.inverse_index=decision.rule.evidence.get("inverse_observed_index").and_then(crate::indexed_json::Value::as_u64).and_then(|n|usize::try_from(n).ok());}
        }
        let mut evidence=decision.rule.evidence.clone();evidence.insert("fill_pnl".into(),crate::indexed_json::json!(pnl));evidence.insert("position_closed".into(),crate::indexed_json::json!(closed));evidence.insert("remaining_quantity".into(),crate::indexed_json::json!(pos.quantity));evidence.insert("t1_deferred_quantity".into(),crate::indexed_json::json!(deferred));
        if queue{evidence.insert("fill_assumption".into(),crate::indexed_json::json!("observed_nonflat_limit_without_queue_verification"));}
        if closed{let entry=pos.initial_entry_notional/pos.initial_quantity;let exit=pos.exit_notional/pos.initial_quantity;self.trades.push(BacktestTrade {symbol:decision.symbol.clone(),entry_time:pos.entry_time,exit_time:observation.at,entry_price:entry,exit_price:exit,initial_stop:pos.initial_stop,quantity:pos.initial_quantity,gross_return:exit/entry-Decimal::ONE,net_return:pos.realized_pnl/(pos.initial_entry_notional+pos.initial_entry_fee),hold_bars:observation.bar_index.saturating_sub(pos.entry_index),entry_reason:pos.reason.clone(),exit_reason:decision.rule.reason.clone(),pnl:pos.realized_pnl,fees:pos.exit_fees+pos.initial_entry_fee});self.positions.remove(&decision.symbol);}
        self.cash+=price*quantity-fee;self.total_fees+=fee;self.turnover+=price*quantity;self.sold_today.insert(decision.symbol.clone());
        self.orders.push(BacktestOrder {timestamp:observation.at,symbol:decision.symbol.clone(),side:Side::Sell,status:"filled".into(),reason:decision.rule.reason.clone(),mode:decision.mode,quantity:Some(quantity),price:Some(price),fee:Some(fee),evidence});
        self.pending_exits.remove(&decision.symbol);
        if deferred>Decimal::ZERO{decision.remaining_quantity=Some(deferred);decision.mode=ExecutionMode::NextOpen;self.pending_exits.insert(decision.symbol.clone(),decision);}Ok(())
    }
    /// 一组相同可知时点的观察，先更新报价、卖出，再按相对量和标的顺序买入。
    pub fn consume<S:DecisionStrategy>(&mut self,observations:&[BacktestObservation],histories:&BTreeMap<String,Vec<RuleCandle>>,strategy:&mut S)->ExecutionResult<()> {
        let Some(first)=observations.first()else{return Ok(());};
        require(self.last_time.is_none_or(|t|t<=first.at)&&observations.iter().all(|o|o.at==first.at&&o.phase==first.phase),"backtest_observation_clock_mismatch")?;
        let day=session(first.at);
        if self.current_session!=Some(day){self.current_session=Some(day);self.sold_today.clear();self.bought_today.clear();self.observing_structure_exits.clear();}
        self.last_time=Some(first.at);
        for o in observations {
            require(o.candle.adjustment_factor>Decimal::ZERO&&o.execution_price>Decimal::ZERO&&o.liquidity_capacity>=Decimal::ZERO,"invalid_execution_observation")?;
            self.marks.insert(o.candle.symbol.clone(),o.execution_price);
        }
        let matching=|mode:ExecutionMode|matches!((first.phase,mode),(ObservationPhase::DailyOpen,ExecutionMode::NextOpen)|(ObservationPhase::MinuteOpen,ExecutionMode::Intraday5mNextOpen));
        if first.phase==ObservationPhase::DailyOpen {
            for o in observations {
                let mut visible=histories.get(&o.candle.symbol).cloned().unwrap_or_default();visible.push(o.candle.clone());
                let exits=strategy.open_risks(&visible,o,&self.positions)?;
                require(exits.len()<=1,"strategy_must_resolve_open_exit_precedence")?;
                for exit in exits {
                    require(exit.symbol==o.candle.symbol&&exit.known_at<=o.at&&exit.mode==ExecutionMode::NextOpen
                        &&exit.bar_index<=o.bar_index&&exit.rule.bar_index<=o.bar_index,"invalid_open_risk_decision")?;
                    self.pending_entries.remove(&exit.symbol);
                    if self.pending_exits.get(&exit.symbol).is_some_and(|prior|prior.remaining_quantity.is_some()
                        ||prior.rule.fraction==Decimal::ONE&&exit.rule.fraction<Decimal::ONE){continue;}
                    self.pending_exits.insert(exit.symbol.clone(),exit);
                }
            }
        }
        for o in observations {
            if let Some(exit)=self.pending_exits.get(&o.candle.symbol).filter(|d|matching(d.mode)).cloned(){
                require(exit.known_at<=o.at,"future_exit_decision")?;
                if o.phase==ObservationPhase::DailyOpen&&exit.rule.reason=="strict_structure_unresolved" {
                    if let Some(previous)=histories.get(&o.candle.symbol).and_then(|rows|rows.last()) {
                        if o.candle.open>previous.close {
                            self.pending_exits.remove(&o.candle.symbol);
                            self.observing_structure_exits.insert(o.candle.symbol.clone(),(exit,previous.close));continue;
                        }
                    }
                }
                self.pending_exits.remove(&o.candle.symbol);self.exit(exit,o)?;
            }
        }
        let mut ranked:Vec<_>=observations.iter().collect();
        ranked.sort_by(|a,b|{
            let av=self.pending_entries.get(&a.candle.symbol).and_then(|d|d.rvol).unwrap_or(Decimal::ZERO);
            let bv=self.pending_entries.get(&b.candle.symbol).and_then(|d|d.rvol).unwrap_or(Decimal::ZERO);
            bv.cmp(&av).then_with(||a.candle.symbol.cmp(&b.candle.symbol))
        });
        for o in ranked {
            if let Some(mut signal)=self.pending_entries.get(&o.candle.symbol).filter(|d|matching(d.mode)).cloned(){
                require(signal.known_at<=o.at,"future_entry_decision")?;
                self.pending_entries.remove(&o.candle.symbol);
                let mut visible=histories.get(&o.candle.symbol).cloned().unwrap_or_default();visible.push(o.candle.clone());
                if signal.rejection.is_none(){signal.rejection=strategy.entry_rejection(&visible,o,&signal)?;}
                if o.bar_index.saturating_sub(signal.bar_index)>self.config.entry_ttl_bars {self.log(o,Side::Buy,"cancelled","expired",signal.mode,signal.evidence);}
                else {self.entry(signal,o)?;}
            }
        }
        strategy.observe_account(self);
        let mut immediate_exits=Vec::new();let mut immediate_entries=Vec::new();let mut trailing_stops=Vec::new();
        for o in observations {
            if o.phase==ObservationPhase::MinuteOpen{continue;}
            let mut visible=histories.get(&o.candle.symbol).cloned().unwrap_or_default();visible.push(o.candle.clone());
            let decisions=strategy.decide(&visible,o,&self.positions)?;
            let mut exit_count=0;
            for decision in decisions {
                require(decision.symbol()==o.candle.symbol&&decision.known_at()<=o.at,"decision_source_or_clock_mismatch")?;
                require(decision.mode()!=ExecutionMode::SameDayClose||o.phase==ObservationPhase::DailyClose,"same_close_decision_requires_completed_daily_bar")?;
                require(decision.mode()!=ExecutionMode::SameDayOpen||o.phase==ObservationPhase::DailyOpen,"same_open_decision_requires_daily_open")?;
                require(decision.mode()!=ExecutionMode::Intraday5mNextOpen||o.phase==ObservationPhase::CompletedMinute,"minute_decision_requires_completed_source_minute")?;
                match decision {
                    StrategyDecision::RaiseStop(stop)=>{
                        require(stop.stop>Decimal::ZERO&&stop.stop<o.candle.close,"invalid_trailing_stop")?;
                        trailing_stops.push(stop);
                    }
                    StrategyDecision::Enter(mut signal)=>{
                        require(signal.bar_index<=o.bar_index,"future_signal_bar")?;
                        if o.phase==ObservationPhase::DailyClose {
                            let rejection=crate::analysis::trading::entries::volume_upper_shadow_rejection(&o.candle,visible.iter().rev().nth(1))
                                .or_else(||crate::analysis::trading::entries::volume_bearish_entry_rejection(&visible,o.bar_index));
                            if signal.rejection.is_none(){signal.rejection=rejection.map(str::to_owned);}
                        }
                        if matches!(signal.mode,ExecutionMode::SameDayClose|ExecutionMode::SameDayOpen){immediate_entries.push((signal,o));}
                        else {self.pending_entries.insert(signal.symbol.clone(),signal);}
                    }
                    StrategyDecision::Exit(exit)=>{
                        exit_count+=1;require(exit_count==1,"strategy_must_resolve_exit_rule_precedence")?;
                        require(exit.bar_index<=o.bar_index&&exit.rule.bar_index<=o.bar_index,"future_exit_bar")?;
                        self.pending_entries.remove(&exit.symbol);
                        if self.pending_exits.get(&exit.symbol).is_some_and(|d|d.rule.fraction==Decimal::ONE&&exit.rule.fraction<Decimal::ONE){continue;}
                        if exit.mode==ExecutionMode::SameDayClose{immediate_exits.push((exit,o));}
                        else {self.pending_exits.insert(exit.symbol.clone(),exit);}
                    }
                }
            }
        }
        for(exit,o)in immediate_exits{self.exit(exit,o)?;}
        if first.phase==ObservationPhase::DailyClose {
            for o in observations {
                let Some((mut exit,previous_close))=self.observing_structure_exits.remove(&o.candle.symbol)else{continue;};
                if !self.positions.contains_key(&o.candle.symbol)||self.pending_exits.contains_key(&o.candle.symbol)
                    ||self.sold_today.contains(&o.candle.symbol)||o.candle.close>previous_close{continue;}
                self.pending_entries.remove(&o.candle.symbol);exit.mode=ExecutionMode::SameDayClose;exit.known_at=o.at;exit.bar_index=o.bar_index;exit.rule.bar_index=o.bar_index;
                exit.rule=exit.rule.with("execution_model","same_day_close").with("decision_source","gap_observed_structure_exit")
                    .with("previous_close",previous_close).with("observed_open",o.candle.open).with("observed_close",o.candle.close);
                self.exit(exit,o)?;
            }
        }
        for stop in trailing_stops{if let Some(position)=self.positions.get_mut(&stop.symbol){position.stop=position.stop.max(stop.stop);}}
        immediate_entries.sort_by(|(a,_),(b,_)|b.rvol.unwrap_or(Decimal::ZERO).cmp(&a.rvol.unwrap_or(Decimal::ZERO)).then_with(||a.symbol.cmp(&b.symbol)));
        for(signal,o)in immediate_entries{self.entry(signal,o)?;}
        require(self.cash>=Decimal::ZERO&&self.equity_value()?>Decimal::ZERO,"portfolio_cash_equity_invariant_failed")?;
        if first.phase==ObservationPhase::DailyClose{
            let equity=self.equity_value()?;
            self.equity.push((first.at,equity));
            self.portfolio_days.push(PortfolioObservation{at:first.at,cash:self.cash,equity,exposure:(equity-self.cash)/equity,positions:self.positions.len()});
        }
        strategy.after_observation(self,observations)?;
        Ok(())
    }
}

#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct BacktestSession {
    pub daily:SourceBar,pub source:String,pub data_version:String,
    pub minutes:Option<Vec<MinuteBar>>,pub minute_source:Option<String>,pub minute_data_version:Option<String>,
}
fn candle(bar:&SourceBar)->ExecutionResult<RuleCandle>{Ok(RuleCandle {symbol:bar.symbol.clone(),timestamp:bar.timestamp.clone(),open:dec(bar.open)?,high:dec(bar.high)?,low:dec(bar.low)?,close:dec(bar.close)?,volume:dec(bar.volume)?,adjustment_factor:dec(bar.adjustment_factor)?,raw_trading_active:bar.raw_trading_active,raw_is_st:bar.raw_is_st})}
pub(crate) fn source_candle(bar:&SourceBar)->ExecutionResult<RuleCandle>{candle(bar)}
fn daily_at(bar:&SourceBar,clock:&str)->ExecutionResult<DateTime<Utc>> {stamp(&format!("{}T{clock}:00+08:00",bar.timestamp))}
#[derive(Clone,Copy)]
struct ScheduledObservation {session_index:usize,phase:ObservationPhase,minute:usize,at:DateTime<Utc>,priority:u8}

/// 同源日线与完整五分钟输入驱动。没有分钟数据时只开放日开/日收观察，不制造分钟回退成交。
pub fn run_strategy_backtest<S:DecisionStrategy>(sessions:&[BacktestSession],config:BacktestConfig,strategy:&mut S)->ExecutionResult<StrategyBacktest> {
    let mut account=StrategyBacktest::new(config)?;
    let mut schedule=Vec::new();
    let mut prior=BTreeMap::<String,String>::new();
    for(index,source)in sessions.iter().enumerate(){
        let day=&source.daily;
        require(!source.source.is_empty()&&!source.data_version.is_empty(),"market_source_version_required")?;
        require(prior.get(&day.symbol).is_none_or(|previous|previous<&day.timestamp),"unordered_daily_source")?;
        prior.insert(day.symbol.clone(),day.timestamp.clone());
        crate::analysis::trading::validate_bars(&[candle(day)?]).map_err(ExecutionError)?;
        schedule.push(ScheduledObservation {session_index:index,phase:ObservationPhase::DailyOpen,minute:0,at:daily_at(day,"09:30")?,priority:1});
        schedule.push(ScheduledObservation {session_index:index,phase:ObservationPhase::DailyClose,minute:0,at:daily_at(day,"15:00")?,priority:3});
        if let Some(minutes)=&source.minutes{
            require(source.minute_source.as_deref()==Some(source.source.as_str())&&source.minute_data_version.as_deref()==Some(source.data_version.as_str()),"minute_daily_source_version_mismatch")?;
            verify_minute_day(minutes,day).map_err(|e|ExecutionError(e.to_string()))?;
            for(minute,row)in minutes.iter().enumerate(){
                if minute+1<minutes.len(){schedule.push(ScheduledObservation {session_index:index,phase:ObservationPhase::CompletedMinute,minute,at:stamp(&row.timestamp)?,priority:0});}
                if minute>0{schedule.push(ScheduledObservation {session_index:index,phase:ObservationPhase::MinuteOpen,minute,at:stamp(&row.timestamp)?-Duration::minutes(5),priority:2});}
            }
        }
    }
    schedule.sort_by(|a,b|(a.at,a.priority,&sessions[a.session_index].daily.symbol).cmp(&(b.at,b.priority,&sessions[b.session_index].daily.symbol)));
    let mut histories=BTreeMap::<String,Vec<RuleCandle>>::new();
    let mut offset=0;
    while offset<schedule.len(){
        let head=schedule[offset];let end=schedule[offset..].iter().position(|e|e.at!=head.at||e.priority!=head.priority).map_or(schedule.len(),|n|offset+n);
        let mut observations=Vec::new();
        for event in &schedule[offset..end]{
            let source=&sessions[event.session_index];let daily=&source.daily;let full=candle(daily)?;
            let history=histories.get(&daily.symbol).map(Vec::as_slice).unwrap_or(&[]);let index=history.len();
            let factor=full.adjustment_factor;
            let recent=&history[history.len().saturating_sub(account.config.liquidity_lookback)..];
            let capacity=if recent.is_empty(){Decimal::ZERO}else{recent.iter().map(|b|b.volume).sum::<Decimal>()/Decimal::from(recent.len() as u64)*account.config.max_participation/factor};
            let mut visible=full.clone();
            let execution_price;
            match event.phase{
                ObservationPhase::DailyOpen=>{visible.high=full.open;visible.low=full.open;visible.close=full.open;visible.volume=Decimal::ZERO;execution_price=full.open;}
                ObservationPhase::DailyClose=>{execution_price=full.close;}
                ObservationPhase::CompletedMinute|ObservationPhase::MinuteOpen=>{
                    let minutes=source.minutes.as_ref().ok_or_else(||ExecutionError("minute_source_required".into()))?;
                    let observed=if event.phase==ObservationPhase::MinuteOpen{event.minute}else{event.minute+1};
                    visible.high=full.open;visible.low=full.open;visible.close=full.open;visible.volume=Decimal::ZERO;
                    for row in &minutes[..observed]{visible.high=visible.high.max(dec(row.high)?*factor);visible.low=visible.low.min(dec(row.low)?*factor);visible.close=dec(row.close)?*factor;visible.volume+=dec(row.volume)?;}
                    execution_price=if event.phase==ObservationPhase::MinuteOpen{dec(minutes[event.minute].open)?*factor}else{visible.close};
                }
            }
            visible.timestamp=event.at.to_rfc3339();
            let(mut buyable,mut sellable)=if event.phase==ObservationPhase::DailyClose{(daily.close_buyable.unwrap_or(daily.buyable),daily.close_sellable.unwrap_or(daily.sellable))}else{(daily.buyable,daily.sellable)};
            let mut lower=None;let mut nonflat_buy=false;let mut nonflat_sell=false;
            if let Some(previous)=history.last(){
                let preclose=previous.close/factor;
                if let Some(is_st)=daily.raw_is_st.filter(|_|daily.raw_trading_active==Some(true)){
                    let bounds=price_limit_bounds(&daily.symbol,session(event.at),preclose,is_st)?;lower=Some(bounds.0*factor);
                    if matches!(event.phase,ObservationPhase::MinuteOpen|ObservationPhase::CompletedMinute){(buyable,sellable)=opening_permissions(&daily.symbol,session(event.at),execution_price/factor,preclose,true,is_st)?;}
                    if visible.volume>Decimal::ZERO&&visible.high>visible.low{
                        nonflat_buy=!is_st&&visible.low/factor<bounds.1;nonflat_sell=visible.high/factor>bounds.0;
                    }
                }
            }
            if daily.raw_trading_active==Some(false){buyable=false;sellable=false;}
            if event.phase==ObservationPhase::DailyClose{nonflat_buy=daily.nonflat_close_buyable.unwrap_or(false);nonflat_sell=daily.nonflat_close_sellable.unwrap_or(false);}
            let completed_interval=if matches!(event.phase,ObservationPhase::CompletedMinute|ObservationPhase::DailyClose){
                source.minutes.as_ref().and_then(|rows|if event.phase==ObservationPhase::DailyClose{rows.last()}else{rows.get(event.minute)})
                    .map(|row|Ok::<_,ExecutionError>(RuleCandle{symbol:daily.symbol.clone(),timestamp:row.timestamp.clone(),
                        open:dec(row.open)?*factor,high:dec(row.high)?*factor,low:dec(row.low)?*factor,close:dec(row.close)?*factor,
                        volume:dec(row.volume)?,adjustment_factor:factor,raw_trading_active:daily.raw_trading_active,raw_is_st:daily.raw_is_st})).transpose()?
            }else{None};
            observations.push(BacktestObservation {at:event.at,phase:event.phase,bar_index:index,candle:visible,execution_price,buyable,sellable,observed_nonflat_buyable:nonflat_buy,observed_nonflat_sellable:nonflat_sell,lower_limit:lower,liquidity_capacity:capacity,completed_interval});
        }
        account.consume(&observations,&histories,strategy)?;
        if head.phase==ObservationPhase::DailyClose{for event in &schedule[offset..end]{let day=&sessions[event.session_index].daily;histories.entry(day.symbol.clone()).or_default().push(candle(day)?);}}
        offset=end;
    }
    Ok(account)
}
