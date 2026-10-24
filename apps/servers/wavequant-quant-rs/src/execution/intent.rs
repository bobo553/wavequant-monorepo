//! intent_execution.py：显式行情假设、手续费后仓位和可恢复终态；退出失败继续待下一次开盘。
use super::{Account,SecurityFact,Side,OrderRequest,ExecutionError,ExecutionResult,StreamSignalStrategy,StrategyRuntime,SignalSide,PendingIntent,
    IntentAcknowledgement,IntentDisposition,account::AccountEvent,stream::RuntimeEvent,journal::{Record,require},money,session};
use chrono::{DateTime,NaiveDate,Utc};
use rust_decimal::{Decimal,prelude::ToPrimitive};
use serde::{Serialize,Deserialize};
use std::collections::BTreeMap;

#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct IntentExecutionOptions{pub when:DateTime<Utc>,pub raw_open:Decimal,pub adjustment_factor:Decimal,pub fee_budget:Decimal,
    pub slippage_bps:Decimal,pub max_entry_gap:Decimal,pub expires_on:NaiveDate}
#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct IntentExecutionResult{pub acknowledgement:Option<IntentAcknowledgement>,pub disposition:String,pub reason:Option<String>}
fn acknowledged(ack:IntentAcknowledgement)->IntentExecutionResult{IntentExecutionResult{disposition:match ack.disposition{IntentDisposition::Ordered=>"ORDERED",IntentDisposition::Filtered=>"FILTERED",IntentDisposition::Expired=>"EXPIRED",IntentDisposition::RiskExitObserved=>"RISK_EXIT_OBSERVED"}.into(),acknowledgement:Some(ack),reason:None}}
fn economic_rejection(reason:&str)->bool{reason.starts_with("missing_quote:")||[
    "unknown_point_in_time_security","entry_gap_limit","entry_below_invalidation","account_health_gate","risk_budget_below_one_lot","missing_structural_target","net_reward_risk_below_threshold",
    "security_not_tradable","invalid_security_constraints","price_off_tick","outstanding_order_for_symbol","stale_or_future_quote","account_halted","ST_entries_disabled","buy_quantity_off_lot",
    "long_stop_not_below_entry","position_limit","symbol_weight_limit","gross_weight_limit","unknown_sector_exposure","sector_weight_limit","order_risk_limit","insufficient_unreserved_cash",
    "T_plus_1_or_insufficient_position","odd_lot_sell_must_liquidate_position","invalid_order_or_nonprior_signal",
].contains(&reason)}
impl<S:StreamSignalStrategy> StrategyRuntime<S>{
    pub fn execute_intent(&self,account:&mut Account,intent_id:&str,options:&IntentExecutionOptions,securities:&BTreeMap<String,SecurityFact>)->ExecutionResult<IntentExecutionResult>{
        let records=account.events()?;let event=records.iter().find(|r|r.id==intent_id).ok_or_else(||ExecutionError("unknown_intent".into()))?;
        let AccountEvent::Runtime(RuntimeEvent::Intent{strategy_version,signal})=&event.payload else{return Err(ExecutionError("unknown_intent".into()));};
        require(*strategy_version==self.version,"wrong_strategy_version")?;
        if let Some(ack)=records.iter().find_map(|r|match &r.payload{AccountEvent::Runtime(RuntimeEvent::Acknowledged(ack)) if ack.intent_id==intent_id=>Some(ack.clone()),_=>None}){return Ok(acknowledged(ack));}
        require(options.when>event.at,"execution_must_follow_observable_signal")?;
        require(options.adjustment_factor>Decimal::ZERO&&options.raw_open>Decimal::ZERO&&options.slippage_bps>=Decimal::ZERO&&options.slippage_bps<Decimal::from(10_000)
            &&money(options.fee_budget)>=Decimal::ZERO&&options.max_entry_gap>=Decimal::ZERO,"invalid_explicit_execution_assumptions")?;
        let order_id=format!("paper:{intent_id}");
        if records.iter().any(|r|r.id==format!("order:{order_id}")){return Ok(acknowledged(self.acknowledge(account,intent_id,options.when,IntentDisposition::Ordered,Some(order_id))?));}
        let rejected=records.iter().find_map(|r|match &r.payload{AccountEvent::Runtime(RuntimeEvent::Rejected{intent_id:id,reason}) if id==intent_id=>Some(reason.clone()),_=>None});
        if signal.side==SignalSide::Long{if let Some(reason)=rejected{let mut result=acknowledged(self.acknowledge(account,intent_id,options.when,IntentDisposition::Filtered,None)?);result.reason=Some(reason);return Ok(result);}
            if session(options.when)>options.expires_on{return Ok(acknowledged(self.acknowledge(account,intent_id,options.when,IntentDisposition::Expired,None)?));}}
        require(session(options.when)>session(signal.timestamp),"daily_signal_requires_later_session")?;
        let pending=PendingIntent{id:intent_id.into(),available_at:event.at,strategy_version:strategy_version.clone(),signal:signal.clone()};
        let result=(||{let request=prepare_intent_order(account,&pending,&order_id,options,securities)?;
            if let Some(request)=request{account.submit(request,options.when,securities)?;Ok(IntentDisposition::Ordered)}else{Ok(IntentDisposition::RiskExitObserved)}})();
        match result{
            Ok(disposition)=>Ok(acknowledged(self.acknowledge(account,intent_id,options.when,disposition,(disposition==IntentDisposition::Ordered).then_some(order_id))?)),
            Err(ExecutionError(reason)) if economic_rejection(&reason)=>{
                account.journal.transact(|_|Ok((vec![Record{id:format!("intent-reject:{intent_id}:{}",options.when.to_rfc3339()),at:options.when,payload:AccountEvent::Runtime(RuntimeEvent::Rejected{intent_id:intent_id.into(),reason:reason.clone()})}],())))?;
                if signal.side==SignalSide::Long{let mut result=acknowledged(self.acknowledge(account,intent_id,options.when,IntentDisposition::Filtered,None)?);result.reason=Some(reason);Ok(result)}
                else{Ok(IntentExecutionResult{acknowledgement:None,disposition:"DEFERRED".into(),reason:Some(reason)})}
            },Err(error)=>Err(error),
        }
    }
}
fn prepare_intent_order(account:&Account,intent:&PendingIntent,order_id:&str,options:&IntentExecutionOptions,securities:&BTreeMap<String,SecurityFact>)->ExecutionResult<Option<OrderRequest>>{
    let signal=&intent.signal;let fact=securities.get(&signal.symbol).filter(|f|f.symbol==signal.symbol&&f.visible(options.when)).ok_or_else(||ExecutionError("unknown_point_in_time_security".into()))?;
    require(fact.tick_size>Decimal::ZERO&&fact.buy_lot>0,"invalid_security_constraints")?;
    let buy=signal.side==SignalSide::Long;let slip=options.slippage_bps/Decimal::from(10_000);let raw=options.raw_open*if buy{Decimal::ONE+slip}else{Decimal::ONE-slip};
    let price=if buy{(raw/fact.tick_size).ceil()}else{(raw/fact.tick_size).floor()}*fact.tick_size;
    let stop=signal.invalidation_price/options.adjustment_factor;let state=account.state()?;let fee=money(options.fee_budget);
    let quantity=if buy{
        let reference=signal.reference_price/options.adjustment_factor;require(reference>Decimal::ZERO&&(options.raw_open/reference-Decimal::ONE).abs()<=options.max_entry_gap,"entry_gap_limit")?;
        require(price>stop,"entry_below_invalidation")?;let health=account.health(options.when)?;require(health.healthy&&health.equity.is_some(),"account_health_gate")?;
        let equity=health.equity.ok_or_else(||ExecutionError("account_health_gate".into()))?;
        let risk=(equity*account.limits.max_order_risk-fee).max(Decimal::ZERO);let cash=(state.cash-state.reserved_cash(None)-fee).max(Decimal::ZERO);
        let current=Decimal::from(*state.positions.get(&signal.symbol).unwrap_or(&0))*options.raw_open;
        let room=(equity*account.limits.max_symbol_weight-current).max(Decimal::ZERO);
        let quantity=([risk/(price-stop),cash/price,room/price].into_iter().fold(Decimal::MAX,Decimal::min)/Decimal::from(fact.buy_lot)).floor().to_u64()
            .and_then(|lots|lots.checked_mul(fact.buy_lot)).ok_or_else(||ExecutionError("intent_quantity_out_of_range".into()))?;
        require(quantity>0,"risk_budget_below_one_lot")?;
        if signal.minimum_reward_risk>Decimal::ZERO{let target=signal.target_price.ok_or_else(||ExecutionError("missing_structural_target".into()))?/options.adjustment_factor;
            let reward=(target-price)*Decimal::from(quantity)-fee*Decimal::TWO;let risk_cost=(price-stop)*Decimal::from(quantity)+fee*Decimal::TWO;
            require(reward/risk_cost>=signal.minimum_reward_risk,"net_reward_risk_below_threshold")?;}quantity
    }else{let quantity=*state.positions.get(&signal.symbol).unwrap_or(&0);if quantity==0{return Ok(None);}quantity};
    Ok(Some(OrderRequest{order_id:order_id.into(),symbol:signal.symbol.clone(),side:if buy{Side::Buy}else{Side::Sell},quantity,limit_price:price,stop_price:stop,signal_at:signal.timestamp,fee_budget:fee}))
}
