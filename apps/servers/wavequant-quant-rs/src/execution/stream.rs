//! stream_runtime.py 的可恢复因果输入与持久意图；账户和意图使用同一事务日志。
use super::{Account,Side,ExecutionError,ExecutionResult,account::AccountEvent,journal::{Record,require}};
use chrono::{DateTime,Utc};
use rust_decimal::{Decimal,prelude::ToPrimitive};
use serde::{Serialize,Deserialize};
use crate::indexed_json::{Value,json};
use std::collections::{BTreeMap,BTreeSet};
use crate::analysis::analyzer::{AnalysisBar,AnalysisRequest,AnalyzerConfig,DataIdentity,TrendAnalyzer};

#[derive(Debug,Clone,PartialEq,Serialize,Deserialize)]
pub struct StreamBar{pub timestamp:DateTime<Utc>,pub symbol:String,pub open:Decimal,pub high:Decimal,pub low:Decimal,pub close:Decimal,pub volume:Decimal,
    pub adjustment_factor:Decimal,pub raw_trading_active:Option<bool>,pub raw_is_st:Option<bool>}
impl StreamBar{
    pub fn validate(&self)->ExecutionResult<()>{require(!self.symbol.is_empty()&&self.symbol.len()<=128&&self.low>Decimal::ZERO&&self.low<=self.open.min(self.close)
        &&self.high>=self.open.max(self.close)&&self.low<=self.high&&self.volume>=Decimal::ZERO&&self.adjustment_factor>Decimal::ZERO,"invalid_stream_bar")}
    fn analysis(&self,index:usize)->ExecutionResult<AnalysisBar>{let number=|v:Decimal|v.to_f64().filter(|x|x.is_finite()).ok_or_else(||ExecutionError("stream_price_out_of_range".into()));
        Ok(AnalysisBar{index,time:self.timestamp.to_rfc3339(),open:number(self.open)?,high:number(self.high)?,low:number(self.low)?,close:number(self.close)?,volume:self.volume,
            adjustment_factor:self.adjustment_factor,raw_trading_active:self.raw_trading_active,raw_is_st:self.raw_is_st})}
}
#[derive(Debug,Clone,Copy,PartialEq,Eq,Serialize,Deserialize)]
#[serde(rename_all="SCREAMING_SNAKE_CASE")]
pub enum SignalSide{Long,Exit}
#[derive(Debug,Clone,PartialEq,Serialize,Deserialize)]
pub struct DurableSignal{
    pub symbol:String,pub timestamp:DateTime<Utc>,pub trigger_timestamp:DateTime<Utc>,pub side:SignalSide,pub reason:String,
    pub reference_price:Decimal,pub invalidation_price:Decimal,pub target_price:Option<Decimal>,pub minimum_reward_risk:Decimal,
    pub evidence:Value,
}
#[derive(Debug,Clone,PartialEq,Serialize,Deserialize)]
pub struct PendingIntent{pub id:String,pub available_at:DateTime<Utc>,pub strategy_version:String,pub signal:DurableSignal}
#[derive(Debug,Clone,Copy,PartialEq,Eq,Serialize,Deserialize)]
#[serde(rename_all="SCREAMING_SNAKE_CASE")]
pub enum IntentDisposition{Ordered,Filtered,Expired,RiskExitObserved}
#[derive(Debug,Clone,PartialEq,Serialize,Deserialize)]
pub struct IntentAcknowledgement{pub intent_id:String,pub disposition:IntentDisposition,pub order_id:Option<String>}
#[derive(Debug,Clone,PartialEq,Serialize,Deserialize)]
pub(super) enum RuntimeEvent{
    Bar(StreamBar),Intent{strategy_version:String,signal:DurableSignal},
    Evaluated{strategy_version:String,bar_id:String,intent_ids:Vec<String>},
    Acknowledged(IntentAcknowledgement),Rejected{intent_id:String,reason:String},
}
pub trait StreamSignalStrategy{
    fn configuration(&self)->ExecutionResult<Value>;
    fn generate(&mut self,known_prefix:&[StreamBar])->ExecutionResult<Vec<DurableSignal>>;
}
fn digest(value:&Value)->ExecutionResult<String>{crate::persistence::canonical(value).map(crate::persistence::sha256).map_err(|e|ExecutionError(e.to_string()))}
pub struct StrategyRuntime<S:StreamSignalStrategy>{strategy:S,pub version:String}
impl<S:StreamSignalStrategy> StrategyRuntime<S>{
    pub fn new(strategy:S)->ExecutionResult<Self>{let version=digest(&strategy.configuration()?)?;Ok(Self{strategy,version})}
    pub fn on_bar(&mut self,account:&mut Account,bar:StreamBar,available_at:DateTime<Utc>)->ExecutionResult<Vec<String>>{
        bar.validate()?;require(available_at>=bar.timestamp,"bar_not_yet_available")?;
        require(digest(&self.strategy.configuration()?)?==self.version,"runtime_strategy_configuration_changed")?;
        let bar_id=format!("bar:{}:{}",bar.symbol,bar.timestamp.to_rfc3339());let evaluated_id=format!("evaluated:{}:{bar_id}",self.version);
        account.journal.transact(|records|{if !records.iter().any(|r|r.id==bar_id){
            require(records.iter().filter_map(|r|match &r.payload{AccountEvent::Runtime(RuntimeEvent::Bar(old)) if old.symbol==bar.symbol=>Some(old.timestamp),_=>None}).all(|t|t<bar.timestamp),"out_of_order_bar")?;}
            Ok((vec![Record{id:bar_id.clone(),at:available_at,payload:AccountEvent::Runtime(RuntimeEvent::Bar(bar.clone()))}],()))})?;
        let records=account.events()?;
        if let Some(record)=records.iter().find(|r|r.id==evaluated_id){if let AccountEvent::Runtime(RuntimeEvent::Evaluated{intent_ids,..})=&record.payload{return Ok(intent_ids.clone());}return Err(ExecutionError("runtime_evaluation_identity_conflict".into()));}
        let mut prefix:Vec<_>=records.iter().filter_map(|r|match &r.payload{AccountEvent::Runtime(RuntimeEvent::Bar(old)) if old.symbol==bar.symbol&&old.timestamp<=bar.timestamp=>Some(old.clone()),_=>None}).collect();
        prefix.sort_by_key(|b|b.timestamp);let signals=self.strategy.generate(&prefix)?;
        let mut pending=Vec::new();let mut emitted=Vec::new();let mut sides=BTreeSet::new();
        for signal in signals.into_iter().filter(|s|s.timestamp==bar.timestamp){
            require(signal.symbol==bar.symbol&&signal.trigger_timestamp<=signal.timestamp&&signal.reference_price>Decimal::ZERO&&signal.invalidation_price>Decimal::ZERO
                &&signal.minimum_reward_risk>=Decimal::ZERO,"invalid_strategy_signal")?;
            require(sides.insert(if signal.side==SignalSide::Long{0}else{1}),"multiple_canonical_signals_for_same_side_and_bar")?;
            let id=format!("intent:{}",digest(&json!([self.version,signal.symbol,signal.timestamp,signal.side]))?);
            pending.push(Record{id:id.clone(),at:available_at,payload:AccountEvent::Runtime(RuntimeEvent::Intent{strategy_version:self.version.clone(),signal})});emitted.push(id);
        }
        pending.push(Record{id:evaluated_id,at:available_at,payload:AccountEvent::Runtime(RuntimeEvent::Evaluated{strategy_version:self.version.clone(),bar_id,intent_ids:emitted.clone()})});
        account.journal.transact(|_|Ok((pending,())))?;Ok(emitted)
    }
    pub fn pending_intents(&self,account:&Account)->ExecutionResult<Vec<PendingIntent>>{let records=account.events()?;
        let acknowledged:BTreeSet<_>=records.iter().filter_map(|r|match &r.payload{AccountEvent::Runtime(RuntimeEvent::Acknowledged(ack))=>Some(ack.intent_id.as_str()),_=>None}).collect();
        Ok(records.iter().filter_map(|r|match &r.payload{AccountEvent::Runtime(RuntimeEvent::Intent{strategy_version,signal}) if *strategy_version==self.version&&!acknowledged.contains(r.id.as_str())=>Some(PendingIntent{id:r.id.clone(),available_at:r.at,strategy_version:strategy_version.clone(),signal:signal.clone()}),_=>None}).collect())}
    pub fn acknowledge(&self,account:&mut Account,intent_id:&str,when:DateTime<Utc>,disposition:IntentDisposition,order_id:Option<String>)->ExecutionResult<IntentAcknowledgement>{
        let ack=IntentAcknowledgement{intent_id:intent_id.into(),disposition,order_id};account.journal.transact(|records|{
            let event=records.iter().find(|e|e.id==intent_id).ok_or_else(||ExecutionError("unknown_intent".into()))?;
            let AccountEvent::Runtime(RuntimeEvent::Intent{strategy_version,signal})=&event.payload else{return Err(ExecutionError("unknown_intent".into()));};
            require(*strategy_version==self.version,"wrong_strategy_version")?;require(when>=event.at,"ack_precedes_intent")?;
            if disposition==IntentDisposition::Ordered{let id=ack.order_id.as_deref().ok_or_else(||ExecutionError("durable_accepted_order_required".into()))?;
                let order=records.iter().find(|r|r.id==format!("order:{id}")).ok_or_else(||ExecutionError("durable_accepted_order_required".into()))?;
                let AccountEvent::Accepted(order)=&order.payload else{return Err(ExecutionError("durable_accepted_order_required".into()));};
                require(order.request.symbol==signal.symbol&&order.request.side==if signal.side==SignalSide::Long{Side::Buy}else{Side::Sell}
                    &&order.request.signal_at==signal.timestamp&&order.accepted_at<=when,"order_does_not_match_intent")?;
            }
            Ok((vec![Record{id:format!("ack:{intent_id}"),at:when,payload:AccountEvent::Runtime(RuntimeEvent::Acknowledged(ack.clone()))}],ack.clone()))})
    }
}

struct SymbolAnalyzer{prefix:Vec<StreamBar>,analyzer:TrendAnalyzer,last_signals:Vec<DurableSignal>}
pub struct TrendStreamStrategy{config:AnalyzerConfig,source_version:String,symbols:BTreeMap<String,SymbolAnalyzer>}
impl TrendStreamStrategy{pub fn new(config:AnalyzerConfig,source_version:String)->ExecutionResult<Self>{
    require(!source_version.is_empty()&&source_version.len()<=256,"bounded_stream_source_version_required")?;
    config.trading.validate().map_err(ExecutionError)?;Ok(Self{config,source_version,symbols:BTreeMap::new()})}
    pub fn audit(&self,symbol:&str)->Vec<Value>{self.symbols.get(symbol).map(|s|s.analyzer.trading_state().events.clone()).unwrap_or_default()}
}
impl StreamSignalStrategy for TrendStreamStrategy{
    fn configuration(&self)->ExecutionResult<Value>{Ok(json!({"engine_version":crate::analysis::analyzer::VERSION,"analyzer":self.config,"source_version":self.source_version}))}
    fn generate(&mut self,known_prefix:&[StreamBar])->ExecutionResult<Vec<DurableSignal>>{
        let last=known_prefix.last().ok_or_else(||ExecutionError("stream_prefix_empty".into()))?;
        require(known_prefix.iter().all(|b|b.symbol==last.symbol)&&known_prefix.windows(2).all(|b|b[0].timestamp<b[1].timestamp),"invalid_stream_prefix")?;
        let symbol=last.symbol.clone();let result=(||->ExecutionResult<Vec<DurableSignal>>{
        if let Some(cache)=self.symbols.get(&last.symbol){if cache.prefix==known_prefix{return Ok(cache.last_signals.clone());}}
        let reusable=self.symbols.get(&last.symbol).is_some_and(|s|s.prefix.len()<known_prefix.len()&&known_prefix.starts_with(&s.prefix));
        if reusable{let cache=self.symbols.get_mut(&last.symbol).ok_or_else(||ExecutionError("stream_cache_missing".into()))?;
            let more=known_prefix.iter().enumerate().skip(cache.prefix.len()).map(|(i,b)|b.analysis(i)).collect::<ExecutionResult<Vec<_>>>()?;
            cache.analyzer.append(more).map_err(ExecutionError)?;
        }else{let bars=known_prefix.iter().enumerate().map(|(i,b)|b.analysis(i)).collect::<ExecutionResult<_>>()?;
            let analyzer=TrendAnalyzer::new(AnalysisRequest{identity:DataIdentity{symbol:last.symbol.clone(),source_version:self.source_version.clone(),adjustment:"explicit_stream_factor".into(),timeframe:"daily".into()},bars,config:self.config.clone(),state:None}).map_err(ExecutionError)?;
            self.symbols.insert(last.symbol.clone(),SymbolAnalyzer{prefix:vec![],analyzer,last_signals:vec![]});}
        let cache=self.symbols.get_mut(&last.symbol).ok_or_else(||ExecutionError("stream_cache_missing".into()))?;let mut signals=vec![];
        while cache.analyzer.process_next().map_err(ExecutionError)?{let now=cache.analyzer.get_current_index()-1;if now+1!=known_prefix.len(){continue;}
            if let Some(day)=cache.analyzer.daily_trading(){require(day.unavailable_rules.is_empty(),"stream_strategy_rules_unavailable")?;
                for entry in &day.entry_candidates{require(entry.fully_executable,"stream_entry_not_fully_executable")?;
                    let trigger=if let Some(value)=entry.evidence.get("trigger_timestamp").and_then(Value::as_str){
                        DateTime::parse_from_rfc3339(value).map(|v|v.with_timezone(&Utc)).or_else(|_|crate::data::dataset::market_close(value).map_err(|e|ExecutionError(e.to_string())))
                            .map_err(|e|ExecutionError(format!("invalid_signal_trigger_time:{e}")))?
                    }else{let index=entry.evidence.get("trigger_index").or_else(||entry.evidence.get("attack")).and_then(Value::as_u64)
                        .and_then(|n|usize::try_from(n).ok()).ok_or_else(||ExecutionError("signal_trigger_identity_missing".into()))?;
                        known_prefix.get(index).ok_or_else(||ExecutionError("signal_trigger_out_of_range".into()))?.timestamp};
                    require(trigger<=last.timestamp,"future_signal_trigger")?;
                    signals.push(DurableSignal{symbol:last.symbol.clone(),timestamp:last.timestamp,trigger_timestamp:trigger,side:SignalSide::Long,reason:entry.reason.clone(),reference_price:entry.price,
                        invalidation_price:entry.stop,target_price:entry.target,minimum_reward_risk:self.config.trading.minimum_reward_risk,evidence:entry.evidence.clone()});}
                for event in day.events.iter().filter(|e|e.get("bar_index").and_then(Value::as_u64)==Some(now as u64)&&matches!(e.get("event").and_then(Value::as_str),Some("exit_signal"|"strategy_exit"))){
                    // generate_system_signals 的风险 EXIT 以当根 high 作为失效价。
                    let invalidation=event.get("invalidation_price").or_else(||event.get("stop")).and_then(decimal_value).unwrap_or(last.high);
                    signals.push(DurableSignal{symbol:last.symbol.clone(),timestamp:last.timestamp,trigger_timestamp:last.timestamp,side:SignalSide::Exit,reason:event.get("reason").and_then(Value::as_str).ok_or_else(||ExecutionError("strategy_exit_reason_missing".into()))?.into(),reference_price:last.close,
                        invalidation_price:invalidation,target_price:None,minimum_reward_risk:Decimal::ZERO,evidence:event.clone()});}
            }}
        cache.prefix=known_prefix.to_vec();cache.last_signals=signals.clone();Ok(signals)
        })();
        // 任一阶段失败后不保留半推进的实例；已提交的 BAR 可在重投时恢复。
        if result.is_err(){self.symbols.remove(&symbol);}result
    }
}
fn decimal_value(value:&Value)->Option<Decimal>{match (value).kind() {Some(crate::indexed_json::Kind::String(s))=>s.parse().ok(),Some(crate::indexed_json::Kind::Number(n))=>n.to_string().parse().ok(),_=>None}}
