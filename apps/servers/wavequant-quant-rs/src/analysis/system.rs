//! 原生系统策略装配。每个日线前缀只有一个分析器，账户、分钟观察和输出消费同一结果。
use std::{collections::{BTreeMap,BTreeSet},sync::{Arc,atomic::{AtomicBool,Ordering}},str::FromStr};
use chrono::{DateTime,Duration,Utc};
use rust_decimal::{Decimal,prelude::ToPrimitive};
use serde::{Deserialize,Serialize};
use crate::indexed_json::{Value,json};
use super::{analyzer::{AnalysisBar,AnalysisRequest,AnalysisResponse,AnalyzerConfig,DataIdentity,StructureCache,TrendAnalyzer},
    profiles::ExecutionParameters,intraday::{self,IntradayContext,IntradayDecision,IntradayState},trading::{self,RuleCandle,RuleDecision,risk::{self,HoldingRiskContext,HoldingRiskPolicy,HoldingRiskState}}};
use crate::execution::{self,BacktestConfig,BacktestObservation,BacktestPosition,BacktestSession,DecisionStrategy,
    EntryDecision,ExitDecision,ExecutionError,ExecutionMode,ExecutionResult,ObservationPhase,StopDecision,StrategyBacktest,StrategyDecision};

#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct SystemConfig {
    pub analyzer:AnalyzerConfig,pub execution:BacktestConfig,pub holding:HoldingRiskPolicy,pub parameters:ExecutionParameters,
    #[serde(default)]pub trading_start:Option<String>,#[serde(default)]pub trading_end:Option<String>,
}
impl Default for SystemConfig{fn default()->Self{Self{analyzer:Default::default(),execution:Default::default(),holding:Default::default(),parameters:Default::default(),trading_start:None,trading_end:None}}}
impl SystemConfig{fn trading_on(&self,at:DateTime<Utc>)->bool{let date=execution::session(at).to_string();
    self.trading_start.as_ref().is_none_or(|start|date.as_str()>=start.as_str())&&self.trading_end.as_ref().is_none_or(|end|date.as_str()<=end.as_str())}}
#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct SystemProgress {
    #[serde(default)]pub analysis_elapsed_seconds:f64,
    pub percent:u8,pub processed:usize,pub total:usize,pub symbol:String,pub asof:String,pub stage:String,
    pub analysis:Option<AnalysisResponse>,pub account:Option<StrategyBacktest>,pub audit:Vec<Value>,pub signals:Vec<Value>,
}
#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct SystemBacktest {
    #[serde(default)]pub analysis_timings:BTreeMap<String,f64>,
    #[serde(default)]pub analysis_elapsed_seconds:f64,
    pub account:StrategyBacktest,#[serde(default,skip_serializing_if="BTreeMap::is_empty")]
    pub indexed_analysis:BTreeMap<String,super::indexed::IndexedAnalysis>,
    #[serde(default,skip_serializing_if="BTreeMap::is_empty")]
    pub analysis:BTreeMap<String,AnalysisResponse>,pub bars:BTreeMap<String,Vec<RuleCandle>>,
    pub audit:Vec<Value>,pub signals:Vec<Value>,pub minute_fallbacks:Vec<Value>,pub unavailable_rules:Vec<String>,
}
#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct SystemSignals {pub signals:Vec<Value>,pub audit:Vec<Value>,pub analysis:AnalysisResponse}
/// 已完成日线的借用视图；记录器不得重新运行趋势或修改已发布结构。
#[derive(Serialize)]
pub struct SystemStep<'a>{pub symbol:&'a str,pub index:usize,pub bar:&'a RuleCandle,pub frames:&'a [super::kernel::KernelFrame],pub trading:&'a trading::day::TradingDayEvaluation}

/// 纯信号研究和扫描入口；不创建账户、不模拟成交，也不回写现有趋势结果。
pub fn run_system_signals(bars:&[RuleCandle],config:AnalyzerConfig)->Result<SystemSignals,String>{
    use sha2::{Sha256,Digest};
    let symbol=bars.first().ok_or("signals require market bars")?.symbol.clone();
    let digest=format!("{:x}",Sha256::digest(crate::indexed_json::to_vec(bars).map_err(|e|e.to_string())?));
    run_system_signals_with_cancel(bars,DataIdentity{symbol,source_version:digest,adjustment:"provided_price_basis".into(),timeframe:"1d".into()},config,None)
}
pub fn run_system_signals_with_cancel(bars:&[RuleCandle],identity:DataIdentity,config:AnalyzerConfig,cancelled:Option<&AtomicBool>)->Result<SystemSignals,String>{
    trading::validate_bars(bars)?;
    if bars.iter().any(|b|b.symbol!=identity.symbol){return Err("signal source contains a different symbol".into());}
    let mut analyzer=TrendAnalyzer::new(AnalysisRequest{identity,bars:vec![],config:config.clone(),state:None})?;
    let mut signals=vec![];let mut audit=vec![];
    for(now,bar)in bars.iter().enumerate(){
        if cancelled.is_some_and(|flag|flag.load(Ordering::Relaxed)){return Err("signals calculation cancelled".into());}
        analyzer.append(vec![as_analysis(bar,now).map_err(|e|e.to_string())?])?;analyzer.process_next()?;
        let daily=analyzer.daily_trading().ok_or("daily signal result missing")?;
        for mut event in daily.events.clone(){event["symbol"]=json!(bar.symbol);event["timestamp"]=json!(bar.timestamp);audit.push(event);}
        for candidate in daily.entry_candidates.iter().filter(|candidate|candidate.fully_executable){
            signals.push(json!({"symbol":bar.symbol,"timestamp":bar.timestamp,"bar_index":now,"side":"LONG","reason":candidate.reason,
                "reference_price":candidate.price,"invalidation_price":candidate.stop,"target_price":candidate.target,
                "minimum_reward_risk":config.trading.minimum_reward_risk,"rvol":candidate.evidence.get("rvol"),
                "execution_model":candidate.execution_model,"evidence":candidate.evidence}));
        }
        if let Some(exit)=current_strategy_exit(&daily.events,now){signals.push(json!({"symbol":bar.symbol,"timestamp":bar.timestamp,"bar_index":now,
            "side":"EXIT","reason":exit.reason,"reference_price":bar.close,"invalidation_price":bar.high,"target_price":Value::Null,
            "minimum_reward_risk":0,"execution_model":exit.evidence.get("execution_model"),"evidence":exit.evidence}));}
    }
    Ok(SystemSignals{signals,audit,analysis:analyzer.response()})
}

#[derive(Default)]
struct EntryExtrema {identity:String,date:String,low:Option<Decimal>,high:Option<Decimal>}
struct SymbolState {
    analyzer:TrendAnalyzer,holding:HoldingRiskState,extrema:EntryExtrema,bars:Vec<RuleCandle>,
    intraday:IntradayState,intraday_audit:usize,
}
struct SystemStrategy<F:FnMut(&SystemProgress),G:FnMut(&SystemStep<'_>)> {
    config:SystemConfig,symbols:BTreeMap<String,SymbolState>,minutes:BTreeSet<(String,String)>,
    pending_full:BTreeSet<String>,audit:Vec<Value>,signals:Vec<Value>,minute_fallbacks:Vec<Value>,
    unavailable:BTreeSet<String>,cancelled:Option<Arc<AtomicBool>>,processed:usize,total:usize,last_percent:Option<u8>,on_progress:F,on_step:G,analysis_started:std::time::Instant,
    frozen_signals:Option<BTreeMap<(String,String),Vec<Value>>>,
}
fn failure(message:impl Into<String>)->ExecutionError{ExecutionError(message.into())}
fn price(value:&Value)->Option<Decimal>{match (value).kind() {Some(crate::indexed_json::Kind::String(s))=>Decimal::from_str(s).ok(),Some(crate::indexed_json::Kind::Number(n))=>Decimal::from_str(&n.to_string()).ok(),_=>None}}
fn evidence(value:&Value)->BTreeMap<String,Value>{value.as_object().map(|v|v.iter().map(|(k,v)|(k.clone(),v.clone())).collect()).unwrap_or_default()}
fn decimal_f64(value:Decimal)->ExecutionResult<f64>{value.to_f64().filter(|v|v.is_finite()).ok_or_else(||failure("analysis_price_out_of_range"))}
fn as_analysis(bar:&RuleCandle,index:usize)->ExecutionResult<AnalysisBar>{Ok(AnalysisBar{index,time:bar.timestamp.clone(),
    open:decimal_f64(bar.open)?,high:decimal_f64(bar.high)?,low:decimal_f64(bar.low)?,close:decimal_f64(bar.close)?,
    volume:bar.volume,adjustment_factor:bar.adjustment_factor,raw_trading_active:bar.raw_trading_active,raw_is_st:bar.raw_is_st})}
fn normalise(visible:&[RuleCandle],o:&BacktestObservation)->ExecutionResult<Vec<RuleCandle>>{
    if visible.len()!=o.bar_index+1||visible.iter().any(|b|b.symbol!=o.candle.symbol){return Err(failure("system_visible_prefix_mismatch"));}
    let mut bars=visible.to_vec();
    let last=bars.last_mut().ok_or_else(||failure("system_empty_prefix"))?;
    last.timestamp=execution::session(o.at).to_string();
    Ok(bars)
}
fn mode(name:&str,close_default:bool)->Result<ExecutionMode,String>{match name{
    "same_day_open"=>Ok(ExecutionMode::SameDayOpen),
    "same_day_close"=>Ok(ExecutionMode::SameDayClose),"intraday_5m_next_open"=>Ok(ExecutionMode::Intraday5mNextOpen),
    "next_open"|""=>Ok(if close_default{ExecutionMode::SameDayClose}else{ExecutionMode::NextOpen}),
    _=>Err(format!("unknown execution model:{name}")),
}}
fn event_index(value:&Value,key:&str)->Option<usize>{value.get(key).and_then(Value::as_u64).and_then(|n|usize::try_from(n).ok())}
fn current_strategy_exit(events:&[Value],now:usize)->Option<RuleDecision>{
    events.iter().filter(|e|event_index(e,"bar_index")==Some(now)
        &&matches!(e.get("event").and_then(Value::as_str),Some("exit_signal"|"strategy_exit")))
        .filter_map(|event|{
            let reason=event.get("reason")?.as_str()?;
            let mut d=RuleDecision::new(reason,now,event.get("exit_fraction").and_then(price).unwrap_or(Decimal::ONE));
            d.evidence=evidence(event);d.cumulative=event.get("exit_target_fraction").is_some();Some(d)
        }).next()
}
fn strategy_execution(mut decision:RuleDecision)->RuleDecision{
    let clear=decision.reason.split('|').find(|reason|matches!(*reason,
        "limit_up_distribution_midpoint_clear"|"bearish_body_volume_clear"|"wave_two_t_body_volume_clear"|
        "volume_shadow_below_bearish_midpoint_clear"|"wave_two_t_resistance_volume_clear"|"wave_two_t_next_volume_clear"|
        "wave_five_top_child_volume_clear"|"wave_five_top_gap_volume_clear"|"wave_five_top_body_upper_shadow_clear"|
        "wave_five_top_gap_upper_shadow_clear"|"wave_c_equal_near_volume_clear")).map(str::to_owned);
    if let Some(reason)=clear{
        let nested=decision.evidence.get("decisions").and_then(Value::as_array).and_then(|rows|rows.iter().find(|d|d["reason"].as_str()==Some(reason.as_str()))).cloned();
        if let Some(nested)=nested{decision.evidence.extend(evidence(&nested["evidence"]));}
        decision.evidence.insert("original_signal_reason".into(),json!(decision.reason));
        decision.reason=reason;decision.fraction=Decimal::ONE;decision.cumulative=false;
        decision.evidence.insert("execution_model".into(),json!("same_day_close"));
        decision.evidence.insert("exit_fraction".into(),json!(1));
    }
    decision
}
fn frozen_daily(rows:&[Value],bar:&RuleCandle,now:usize,config:&SystemConfig)->ExecutionResult<(Vec<trading::day::EntryDecision>,Option<RuleDecision>)>{
    let mut entries=Vec::new();let mut exit=None;
    for row in rows{
        if row["symbol"].as_str()!=Some(bar.symbol.as_str())||event_index(row,"bar_index")!=Some(now){return Err(failure("frozen signal does not match its source bar"));}
        let reason=row["reason"].as_str().filter(|s|!s.is_empty()).ok_or_else(||failure("frozen signal reason missing"))?;
        let mut proof=evidence(&row["evidence"]);
        for key in ["trigger_timestamp","retracement","rvol","regime","execution_model"]{if let Some(value)=row.get(key){proof.insert(key.into(),value.clone());}}
        proof.insert("signal_source".into(),json!("frozen_daily_signals"));
        match row["side"].as_str(){
            Some("LONG")=>{
                if !entries.is_empty(){return Err(failure("duplicate frozen LONG for symbol/date"));}
                let entry_price=price(&row["reference_price"]).ok_or_else(||failure("frozen entry price missing"))?;
                let stop=price(&row["invalidation_price"]).ok_or_else(||failure("frozen invalidation price missing"))?;
                let target=match row.get("target_price").filter(|v|!v.is_null()){Some(value)=>Some(price(value).ok_or_else(||failure("invalid frozen target"))?),None=>None};
                let minimum=price(&row["minimum_reward_risk"]).ok_or_else(||failure("frozen reward/risk missing"))?;
                if entry_price<=Decimal::ZERO||stop<=Decimal::ZERO||stop>=entry_price||minimum<Decimal::ZERO||target.is_some_and(|p|p<=entry_price){return Err(failure("invalid frozen LONG economics"));}
                proof.insert("minimum_reward_risk".into(),json!(minimum));
                entries.push(trading::day::EntryDecision{bar_index:now,reason:reason.into(),price:entry_price,stop,target,
                    qualification_index:proof.get("qualification_index").and_then(Value::as_u64).and_then(|v|usize::try_from(v).ok()).unwrap_or(now),
                    trend_level:config.analyzer.trading.buy_interval_level,source_path:row["evidence"]["source_path"].as_str().unwrap_or("frozen_signal").into(),
                    execution_model:row["execution_model"].as_str().unwrap_or(if matches!(reason,"qualified_volume_gap_follow_through_close"|"qualified_double_volume_breakout_close"|"qualified_volume_high_open_rising_close"){"same_day_close"}else{"next_open"}).into(),fully_executable:true,evidence:json!(proof)});
            },
            Some("EXIT")=>{if exit.is_some(){return Err(failure("duplicate frozen EXIT for symbol/date"));}let mut decision=RuleDecision::new(reason,now,Decimal::ONE);decision.evidence=proof;exit=Some(decision);},
            _=>return Err(failure("frozen signal side must be LONG or EXIT")),
        }
    }
    Ok((entries,exit))
}
fn owned_wave_events(events:&[Value],position:&BacktestPosition,now:usize)->Vec<Value>{
    let owned=position.entry_evidence.get("n_id").and_then(Value::as_str)
        .or_else(||position.entry_evidence.get("target_source_id").and_then(Value::as_str));
    let signal=position.entry_evidence.get("signal_index").and_then(Value::as_u64).and_then(|i|usize::try_from(i).ok()).unwrap_or(position.entry_index);
    events.iter().filter(|event|event_index(event,"bar_index").is_some_and(|i|i<=now)
        &&event_index(event,"known_at").is_none_or(|i|i<=now)
        &&(owned.is_some_and(|id|["source_id","n_id","target_source_id"].iter().any(|k|event.get(k).and_then(Value::as_str)==Some(id)))
            ||event_index(event,"signal_index")==Some(signal)||event_index(event,"owner_signal_index")==Some(signal)
            ||matches!(event["event"].as_str(),Some("wave_ordinary_entry"|"wave_c_equal_target"))&&event_index(event,"bar_index")==Some(signal))).cloned().collect()
}
impl EntryExtrema {
    fn observe(&mut self,position:&BacktestPosition,o:&BacktestObservation,has_minutes:bool)->ExecutionResult<(Decimal,Decimal)>{
        let key=format!("{}:{}",position.symbol,position.entry_time);let date=execution::session(o.at).to_string();
        if self.identity!=key||self.date!=date{*self=Self{identity:key,date,low:None,high:None};}
        let same_day=execution::session(position.entry_time)==execution::session(o.at);
        let range=if !same_day{Some((o.candle.low,o.candle.high))}
            else if let Some(interval)=&o.completed_interval{
                let end=DateTime::parse_from_rfc3339(&interval.timestamp).map_err(|e|failure(e.to_string()))?.with_timezone(&Utc);
                if end>o.at{return Err(failure("future_post_entry_interval"));}
                (end-Duration::minutes(5)>=position.entry_time).then_some((interval.low,interval.high))
            }else if !has_minutes&&position.entry_time<o.at{Some((o.candle.low,o.candle.high))}else{None};
        if let Some((low,high))=range{self.low=Some(self.low.map_or(low,|v|v.min(low)));self.high=Some(self.high.map_or(high,|v|v.max(high)));}
        // 新仓开盘到首个完成分钟之前只知道成交价，不能使用该分钟稍后的高低点。
        Ok((self.low.unwrap_or(position.entry_price),self.high.unwrap_or(position.entry_price)))
    }
}
fn observe_minutes(state:&mut SymbolState,bars:&[RuleCandle],observation:&BacktestObservation,position:Option<&BacktestPosition>,
    config:&SystemConfig,minutes:bool)->ExecutionResult<(Vec<StrategyDecision>,Vec<Value>)>{
    let mut observation=observation.clone();observation.candle.timestamp=bars[observation.bar_index].timestamp.clone();
    let context=IntradayContext{cached_through:state.analyzer.get_current_index().checked_sub(1),frames:state.analyzer.levels(),
        eligibilities:state.analyzer.eligibilities(),trading:state.analyzer.trading_state(),config:&config.analyzer.trading,
        execution:&config.parameters,holding:&config.holding,minutes_available:minutes,
        daily_fallback:!minutes&&config.parameters.missing_minute_daily_fallback};
    state.intraday.staged=state.holding.staged.clone();
    let decisions=intraday::observe_intraday(bars,&observation,position,&context,&mut state.intraday).map_err(failure)?;
    state.holding.staged=state.intraday.staged.clone();
    let audit=state.intraday.audit[state.intraday_audit..].to_vec();state.intraday_audit=state.intraday.audit.len();
    let decisions=decisions.into_iter().map(|decision|match decision{
        IntradayDecision::Enter{known_at,bar_index,execution_model,reason,reference_price,stop_price,target_price,minimum_reward_risk,evidence:proof}=>{
            let mut proof=evidence(&proof);proof.insert("signal_index".into(),json!(bar_index));
            StrategyDecision::Enter(EntryDecision{symbol:observation.candle.symbol.clone(),known_at,bar_index,mode:execution_model,
                reason,reference_price,stop_price,target_price,minimum_reward_risk:Some(minimum_reward_risk),rvol:proof.get("rvol").and_then(price),rejection:None,evidence:proof})
        },
        IntradayDecision::Exit{known_at,bar_index,execution_model,rule}=>StrategyDecision::Exit(ExitDecision{symbol:observation.candle.symbol.clone(),known_at,bar_index,mode:execution_model,rule,remaining_quantity:None}),
    }).collect();Ok((decisions,audit))
}
impl<F:FnMut(&SystemProgress),G:FnMut(&SystemStep<'_>)> SystemStrategy<F,G> {
    fn check_cancelled(&self)->ExecutionResult<()>{if self.cancelled.as_ref().is_some_and(|f|f.load(Ordering::Relaxed)){Err(failure("system calculation cancelled"))}else{Ok(())}}
    fn record_decision(&mut self,o:&BacktestObservation,d:&StrategyDecision){
        let signal=match d{
            StrategyDecision::Enter(entry)=>json!({"symbol":entry.symbol,"timestamp":execution::session(o.at).to_string(),"bar_index":entry.bar_index,
                "known_at":entry.known_at,"side":"LONG","reason":entry.reason,"reference_price":entry.reference_price,
                "invalidation_price":entry.stop_price,"target_price":entry.target_price,"execution_model":entry.mode,"evidence":entry.evidence}),
            StrategyDecision::Exit(exit)=>json!({"symbol":exit.symbol,"timestamp":execution::session(o.at).to_string(),"bar_index":exit.bar_index,
                "known_at":exit.known_at,"side":"EXIT","reason":exit.rule.reason,"reference_price":o.candle.close,
                "execution_model":exit.mode,"exit_fraction":exit.rule.fraction,"evidence":exit.rule.evidence}),
            StrategyDecision::RaiseStop(_)=>return,
        };self.signals.push(signal);
    }
}
impl<F:FnMut(&SystemProgress),G:FnMut(&SystemStep<'_>)> DecisionStrategy for SystemStrategy<F,G>{
    fn entry_rejection(&mut self,visible:&[RuleCandle],o:&BacktestObservation,entry:&EntryDecision)->ExecutionResult<Option<String>>{
        self.check_cancelled()?;
        let Some(events)=entry.evidence.get("target_guard_events").and_then(Value::as_array)else{return Ok(None);};
        let bars=normalise(visible,o)?;
        Ok(intraday::execution_entry_rejection(&bars,o.bar_index,events,o.candle.high,o.execution_price).map(|rule|rule.reason))
    }
    fn observe_account(&mut self,account:&StrategyBacktest){
        self.pending_full=account.pending_exits.iter().filter(|(_,exit)|exit.rule.fraction==Decimal::ONE||exit.remaining_quantity.is_some()).map(|(symbol,_)|symbol.clone()).collect();
    }
    fn open_risks(&mut self,_visible:&[RuleCandle],o:&BacktestObservation,positions:&BTreeMap<String,BacktestPosition>)->ExecutionResult<Vec<ExitDecision>>{
        self.check_cancelled()?;
        let Some(position)=positions.get(&o.candle.symbol)else{return Ok(vec![]);};
        let state=self.symbols.get(&o.candle.symbol).ok_or_else(||failure("unknown system symbol"))?;
        let rule=if self.config.holding.trend_flip_adverse_exit{risk::observe_open_breakout_risk(&state.analyzer.trading_state().risk,o.bar_index,o.candle.open,position.entry_index)}else{None};
        Ok(rule.into_iter().map(|rule|ExitDecision{symbol:o.candle.symbol.clone(),known_at:o.at,bar_index:o.bar_index,mode:ExecutionMode::NextOpen,rule,remaining_quantity:None}).collect())
    }
    fn decide(&mut self,visible:&[RuleCandle],o:&BacktestObservation,positions:&BTreeMap<String,BacktestPosition>)->ExecutionResult<Vec<StrategyDecision>>{
        self.check_cancelled()?;
        let bars=normalise(visible,o)?;let symbol=&o.candle.symbol;let now=o.bar_index;
        let frozen=self.frozen_signals.as_ref().map(|signals|signals.get(&(symbol.clone(),execution::session(o.at).to_string())).cloned().unwrap_or_default());
        let has_minutes=self.minutes.contains(&(symbol.clone(),execution::session(o.at).to_string()));
        let state=self.symbols.get_mut(symbol).ok_or_else(||failure("unknown system symbol"))?;
        if let Some(position)=positions.get(symbol){state.extrema.observe(position,o,has_minutes)?;}
        if super::analyzer::STRUCTURE_ONLY&&o.phase!=ObservationPhase::DailyClose{return Ok(Vec::new());}
        if o.phase!=ObservationPhase::DailyClose{
            let(mut decisions,audit)=observe_minutes(state,&bars,o,positions.get(symbol),&self.config,has_minutes)?;
            if frozen.is_some()&&!self.config.parameters.consolidation_entry_intraday{decisions.retain(|decision|!matches!(decision,StrategyDecision::Enter(_)));}
            for mut event in audit{event["symbol"]=json!(symbol);self.audit.push(event);}
            for decision in &decisions{self.record_decision(o,decision);}if !self.config.trading_on(o.at){decisions.clear();}return Ok(decisions);
        }
        if state.analyzer.get_current_index()!=now{return Err(failure("system day must advance exactly once"));}
        if !state.intraday.audit.is_empty(){state.analyzer.accept_intraday(now,&state.intraday.arbiter,&state.intraday.audit).map_err(failure)?;}
        state.analyzer.append(vec![as_analysis(&bars[now],now)?]).map_err(failure)?;
        state.analyzer.process_next().map_err(failure)?;state.bars.push(bars[now].clone());
        let mut daily=state.analyzer.daily_trading().cloned().ok_or_else(||failure("shared trading day missing"))?;
        (self.on_step)(&SystemStep{symbol,index:now,bar:&bars[now],frames:state.analyzer.levels(),trading:&daily});
        if super::analyzer::STRUCTURE_ONLY{self.processed+=1;return Ok(Vec::new());}
        let frozen_exit=if let Some(rows)=frozen.as_ref(){let(entries,exit)=frozen_daily(rows,&bars[now],now,&self.config)?;daily.entry_candidates=entries;exit}else{None};
        for issue in &daily.unavailable_rules{self.unavailable.insert(issue.clone());}
        for event in &daily.events{
            let mut event=event.clone();if let Some(fields)=event.as_object_mut(){fields.insert("symbol".into(),json!(symbol));fields.insert("timestamp".into(),json!(bars[now].timestamp));}
            self.audit.push(event);
        }
        let(mut decisions,minute_audit)=observe_minutes(state,&bars,o,positions.get(symbol),&self.config,has_minutes)?;
        for mut event in minute_audit{event["symbol"]=json!(symbol);self.audit.push(event);}
        let eligible=self.config.analyzer.trading.buy_interval_level==0||daily.frames.get(usize::from(self.config.analyzer.trading.buy_interval_level)).is_some_and(|g|g.in_qualification_range);
        let global=daily.frames.first().map(|frame|frame.exit_decisions.as_slice()).unwrap_or(&[]);
        let strategy_exit=if frozen.is_some(){frozen_exit}else{current_strategy_exit(&daily.events,now)};
        if let Some(position)=positions.get(symbol){
            let mut context=HoldingRiskContext::from_position(position);
            context.positive_n_index=state.analyzer.trading_state().risk.positive_n_index;
            context.trading_eligible=eligible;context.pending_full_exit=self.pending_full.contains(symbol);
            context.wave_events=owned_wave_events(&state.analyzer.trading_state().events,position,now);
            context.c_equal_events=context.wave_events.iter().filter(|e|e.get("event").and_then(Value::as_str)==Some("wave_c_equal_target")).cloned().collect();
            context.target_risk=state.analyzer.trading_state().entry_risk_state().history.get(&now).and_then(|r|r.target.clone()).map(|mut decision|{
                if decision.fraction<Decimal::ONE{decision.fraction=self.config.holding.risk.wave_exhaustion_reduction;
                    decision.evidence.insert("exit_fraction".into(),json!(decision.fraction));
                    if decision.cumulative{decision.evidence.insert("exit_target_fraction".into(),json!(decision.fraction));}}
                decision
            });
            context.daily_fallback=self.config.parameters.staged_exit_intraday&&!has_minutes&&self.config.parameters.missing_minute_daily_fallback;
            if context.daily_fallback{
                let fallback=json!({"symbol":symbol,"date":bars[now].timestamp,"reason":"minute_history_missing","execution_model":"same_day_close","purpose":"staged_exit"});
                self.minute_fallbacks.push(fallback.clone());self.audit.push(json!({"event":"minute_daily_fallback","bar_index":now,"evidence":fallback}));
            }
            let (low,high)=state.extrema.observe(position,o,has_minutes)?;
            let mut hard_config=self.config.execution.clone();hard_config.exit_on_target=hard_config.exit_on_target&&eligible;
            context.hard_exit=execution::hard_exit_decision(position,o,&hard_config,low,high)?;
            if strategy_exit.as_ref().is_some_and(|d|d.reason.split('|').any(|r|r=="mother_child_inverse_n_low_break")){
                context.mother_child_inverse=intraday::mother_child_inverse_n_break(&bars,now);
            }
            if self.config.parameters.volume_inverse_n_clear&&now>0&&strategy_exit.as_ref().is_some_and(|d|d.reason.split('|').any(|r|r=="inverse_n_risk_exit")){
                let reference=bars[..now].iter().rev().find(|b|b.close<b.open);
                let volume_bearish=trading::entries::is_volume_bearish(&bars[now],bars.get(now-1),reference);
                if bars[now].volume>bars[now-1].volume||volume_bearish{context.volume_inverse=Some(RuleDecision::new("volume_inverse_n_clear",now,Decimal::ONE)
                    .with("execution_model","same_day_close").with("observed_volume",bars[now].volume).with("previous_volume",bars[now-1].volume));}
            }
            let evaluation=risk::evaluate_holding_risks(&bars,now,global,&context,&mut state.holding,&self.config.holding).map_err(failure)?;
            self.unavailable.extend(evaluation.unavailable_rules);
            let mut chosen=risk::resolve_exit_precedence(&evaluation.decisions,&context).map_err(failure)?;
            if chosen.is_none()&&!context.pending_full_exit{chosen=strategy_exit.map(strategy_execution);}
            if let Some(mut rule)=chosen{
                if rule.reason=="inverse_n_risk_exit"&&(self.config.parameters.inverse_n_close_reduce
                    ||self.config.parameters.inverse_n_after_reduction&&state.holding.staged.breakdown_index.is_some()&&position.quantity<position.initial_quantity){
                    rule.reason=if self.config.parameters.inverse_n_close_reduce{"inverse_n_close_reduce_90"}else{"inverse_n_after_reduction_90"}.into();
                    rule.fraction=Decimal::new(9,1);rule.cumulative=true;rule.evidence.insert("inverse_observed_index".into(),json!(now));
                    if self.config.parameters.inverse_n_close_reduce{rule.evidence.insert("execution_model".into(),json!("same_day_close"));}
                }
                let execution_model=mode(rule.evidence.get("execution_model").and_then(Value::as_str).unwrap_or("next_open"),false).map_err(failure)?;
                decisions.push(StrategyDecision::Exit(ExitDecision{symbol:symbol.clone(),known_at:o.at,bar_index:now,mode:execution_model,rule,remaining_quantity:None}));
            }
        }
        if !decisions.iter().any(|d|matches!(d,StrategyDecision::Exit(_)|StrategyDecision::Enter(_))){
            for candidate in &daily.entry_candidates{
                if !candidate.fully_executable{self.audit.push(json!({"event":"entry_not_executable","symbol":symbol,"bar_index":now,"candidate":candidate,"unavailable_rules":daily.unavailable_rules}));continue;}
                let mut proof=evidence(&candidate.evidence);proof.insert("signal_index".into(),json!(now));
                proof.insert("qualification_index".into(),json!(candidate.qualification_index));proof.insert("trend_level".into(),json!(candidate.trend_level));proof.insert("source_path".into(),json!(candidate.source_path));
                if let Some(position)=positions.get(symbol){if candidate.stop>position.stop&&candidate.stop<bars[now].close{
                    decisions.push(StrategyDecision::RaiseStop(StopDecision{symbol:symbol.clone(),known_at:o.at,stop:candidate.stop}));}
                    if !self.config.parameters.allow_add_on{continue;}
                }
                let execution_model=mode(&candidate.execution_model,self.config.parameters.entry_at_close).map_err(failure)?;
                if execution_model==ExecutionMode::Intraday5mNextOpen{return Err(failure("close candidate cannot authorize an earlier minute entry"));}
                let required=proof.get("minimum_reward_risk").and_then(price).unwrap_or(self.config.analyzer.trading.minimum_reward_risk);
                decisions.push(StrategyDecision::Enter(EntryDecision{symbol:symbol.clone(),known_at:o.at,bar_index:now,mode:execution_model,
                    reason:candidate.reason.clone(),reference_price:candidate.price,stop_price:candidate.stop,target_price:candidate.target,
                    minimum_reward_risk:Some(required),rvol:proof.get("rvol").and_then(price),rejection:None,evidence:proof}));
                break;
            }
        }
        self.processed+=1;
        for decision in &decisions{self.record_decision(o,decision);}
        if !self.config.trading_on(o.at){decisions.clear();}
        Ok(decisions)
    }
    fn after_observation(&mut self,account:&StrategyBacktest,observations:&[BacktestObservation])->ExecutionResult<()>{
        self.check_cancelled()?;
        let Some(observation)=observations.first().filter(|o|o.phase==ObservationPhase::DailyClose)else{return Ok(());};
        let percent=if self.total==0{100}else{(self.processed.saturating_mul(100)/self.total).min(100) as u8};
        if self.last_percent==Some(percent){return Ok(());}self.last_percent=Some(percent);
        let progress=SystemProgress{analysis_elapsed_seconds:self.analysis_started.elapsed().as_secs_f64(),percent,processed:self.processed,total:self.total,symbol:observation.candle.symbol.clone(),
            asof:execution::session(observation.at).to_string(),stage:"strategy_execution".into(),
            analysis:self.symbols.get(&observation.candle.symbol).map(|s|s.analyzer.response()),account:Some(account.clone()),audit:self.audit.clone(),signals:self.signals.clone()};
        (self.on_progress)(&progress);Ok(())
    }
}

/// 账户输入保留真实来源身份；这里只初始化标识，不把将来的行情交给分析器。
pub fn run_system_backtest<F:FnMut(&SystemProgress)>(sessions:&[BacktestSession],config:SystemConfig,
    cancellation:Option<Arc<AtomicBool>>,on_progress:F)->Result<SystemBacktest,String>{
    run_system_backtest_with_steps(sessions,config,cancellation,on_progress,|_|{})
}
pub fn run_system_backtest_with_steps<F:FnMut(&SystemProgress),G:FnMut(&SystemStep<'_>)>(sessions:&[BacktestSession],config:SystemConfig,
    cancellation:Option<Arc<AtomicBool>>,on_progress:F,on_step:G)->Result<SystemBacktest,String>{
    run_system_backtest_with_cache(sessions,config,cancellation,on_progress,on_step,None)
}
/// 只复用已封存的同周期结构前缀；日策略、分钟观察和账户仍按原时钟推进。
pub fn run_system_backtest_with_cache<F:FnMut(&SystemProgress),G:FnMut(&SystemStep<'_>)>(sessions:&[BacktestSession],config:SystemConfig,
    cancellation:Option<Arc<AtomicBool>>,on_progress:F,on_step:G,structure_cache:Option<Arc<dyn StructureCache>>)->Result<SystemBacktest,String>{
    run_system_inputs(sessions,config,cancellation,on_progress,on_step,None,structure_cache)
}
/// 已冻结的日信号只控制日收买卖意图；账户仍消费同一持仓、开盘与分钟风险流程。
pub fn run_system_backtest_with_signals<F:FnMut(&SystemProgress)>(sessions:&[BacktestSession],config:SystemConfig,
    signals:&[Value],cancellation:Option<Arc<AtomicBool>>,on_progress:F)->Result<SystemBacktest,String>{
    let mut grouped=BTreeMap::<(String,String),Vec<Value>>::new();
    let identities:BTreeSet<_>=sessions.iter().map(|s|(s.daily.symbol.clone(),s.daily.timestamp.get(..10).unwrap_or(&s.daily.timestamp).to_string())).collect();
    let mut seen=BTreeSet::new();
    for signal in signals{
        let symbol=signal["symbol"].as_str().ok_or("frozen signal symbol missing")?;
        let stamp=signal["timestamp"].as_str().ok_or("frozen signal timestamp missing")?;
        let date=stamp.get(..10).unwrap_or(stamp);chrono::NaiveDate::parse_from_str(date,"%Y-%m-%d").map_err(|e|e.to_string())?;
        if config.trading_end.as_ref().is_some_and(|end|date>end.as_str()){continue;}
        let key=(symbol.to_string(),date.to_string());
        if !identities.contains(&key){return Err("frozen signal has no source session".into());}
        let side=signal["side"].as_str().filter(|side|matches!(*side,"LONG"|"EXIT")).ok_or("frozen signal side invalid")?;
        if !seen.insert((key.clone(),side.to_string())){return Err("duplicate frozen signal identity".into());}
        grouped.entry(key).or_default().push(signal.clone());
    }
    run_system_inputs(sessions,config,cancellation,on_progress,|_|{},Some(grouped),None)
}
fn run_system_inputs<F:FnMut(&SystemProgress),G:FnMut(&SystemStep<'_>)>(sessions:&[BacktestSession],mut config:SystemConfig,
    cancellation:Option<Arc<AtomicBool>>,on_progress:F,on_step:G,frozen_signals:Option<BTreeMap<(String,String),Vec<Value>>>,
    structure_cache:Option<Arc<dyn StructureCache>>)->Result<SystemBacktest,String>{
    let analysis_started=std::time::Instant::now();
    config.parameters.validate()?;config.execution.validate().map_err(|e|e.to_string())?;config.analyzer.trading.validate()?;
    config.analyzer.risk=config.holding.risk.clone();
    if sessions.is_empty(){return Err("system execution requires market sessions".into());}
    for date in [&config.trading_start,&config.trading_end].into_iter().flatten(){chrono::NaiveDate::parse_from_str(date,"%Y-%m-%d").map_err(|e|e.to_string())?;}
    if config.trading_start.as_ref().zip(config.trading_end.as_ref()).is_some_and(|(start,end)|start>end){return Err("trading range is reversed".into());}
    let mut identities=BTreeMap::<String,(String,String)>::new();let mut minutes=BTreeSet::new();
    for session in sessions{
        if config.trading_end.as_ref().is_some_and(|end|session.daily.timestamp.as_str()>end.as_str()){return Err("input must end at the requested trading end; future sessions are not execution inputs".into());}
        let symbol=&session.daily.symbol;let identity=(session.source.clone(),session.data_version.clone());
        if let Some(prior)=identities.get(symbol){if prior!=&identity{return Err(format!("mixed source versions for {symbol}"));}}
        else{identities.insert(symbol.clone(),identity);}
        if session.minutes.is_some(){minutes.insert((symbol.clone(),session.daily.timestamp.clone()));}
    }
    if super::analyzer::STRUCTURE_ONLY{
        return run_polyline_arrays(sessions,config,identities,cancellation,on_progress);
    }
    let mut symbols=BTreeMap::new();
    for(symbol,(source,version))in identities{
        let mut analyzer=TrendAnalyzer::new(AnalysisRequest{identity:DataIdentity{symbol:symbol.clone(),source_version:format!("{source}:{version}"),adjustment:"causal_forward".into(),timeframe:"1d".into()},
            bars:vec![],config:config.analyzer.clone(),state:None})?;
        if let Some(cache)=&structure_cache{analyzer.set_structure_cache(Arc::clone(cache))?;}
        symbols.insert(symbol,SymbolState{analyzer,holding:Default::default(),extrema:Default::default(),bars:vec![],intraday:Default::default(),intraday_audit:0});
    }
    let mut strategy=SystemStrategy{config:config.clone(),symbols,minutes,pending_full:BTreeSet::new(),audit:vec![],signals:vec![],minute_fallbacks:vec![],
        unavailable:BTreeSet::new(),cancelled:cancellation,processed:0,total:sessions.len(),last_percent:None,on_progress,on_step,frozen_signals,analysis_started};
    let mut account=execution::run_strategy_backtest(sessions,config.execution.clone(),&mut strategy).map_err(|e|e.to_string())?;
    account.equity.retain(|(at,_)|config.trading_on(*at));account.portfolio_days.retain(|day|config.trading_on(day.at));
    let mut analysis=BTreeMap::new();let mut bars=BTreeMap::new();
    for(symbol,state)in strategy.symbols{let response=state.analyzer.response();strategy.unavailable.extend(response.unavailable_rules.iter().cloned());analysis.insert(symbol.clone(),response);bars.insert(symbol,state.bars);}
    Ok(SystemBacktest{analysis_timings:BTreeMap::new(),analysis_elapsed_seconds:analysis_started.elapsed().as_secs_f64(),account,indexed_analysis:BTreeMap::new(),analysis,bars,audit:strategy.audit,signals:strategy.signals,minute_fallbacks:strategy.minute_fallbacks,unavailable_rules:strategy.unavailable.into_iter().collect()})
}

/// 仅折线入口一次装入行情，之后只推进索引；不进入逐日账户、分钟或诊断快照框架。
fn run_polyline_arrays<F:FnMut(&SystemProgress)>(sessions:&[BacktestSession],config:SystemConfig,
    identities:BTreeMap<String,(String,String)>,cancel:Option<Arc<AtomicBool>>,mut progress:F)->Result<SystemBacktest,String>{
    let analysis_started=std::time::Instant::now();
    let total=sessions.len();let mut processed=0usize;let mut last_percent=None;
    let mut timings=BTreeMap::<String,f64>::new();
    let mut outputs=BTreeMap::new();let mut market=BTreeMap::new();
    for(symbol,(source,version))in identities{
        let phase=std::time::Instant::now();
        let bars=sessions.iter().filter(|session|session.daily.symbol==symbol)
            .map(|session|execution::source_candle(&session.daily).map_err(|error|error.to_string())).collect::<Result<Vec<_>,_>>()?;
        let input=bars.iter().enumerate().map(|(index,bar)|as_analysis(bar,index).map_err(|error|error.to_string())).collect::<Result<Vec<_>,_>>()?;
        let mut analyzer=TrendAnalyzer::new(AnalysisRequest{identity:DataIdentity{symbol:symbol.clone(),source_version:format!("{source}:{version}"),
            adjustment:"causal_forward".into(),timeframe:"1d".into()},bars:input,config:config.analyzer.clone(),state:None})?;
        *timings.entry("input_preparation".into()).or_default()+=phase.elapsed().as_secs_f64();
        let phase=std::time::Instant::now();
        while analyzer.get_current_index()<bars.len(){
            if cancel.as_ref().is_some_and(|flag|flag.load(Ordering::Acquire)){return Err("polyline calculation cancelled".into());}
            let index=analyzer.get_current_index();analyzer.advance_polyline_index()?;processed+=1;
            let percent=(processed.saturating_mul(100)/total).min(100) as u8;
            if last_percent!=Some(percent){last_percent=Some(percent);
                progress(&SystemProgress{analysis_elapsed_seconds:analysis_started.elapsed().as_secs_f64(),percent,processed,total,symbol:symbol.clone(),asof:bars[index].timestamp.clone(),
                    stage:format!("折线 → 一级 → 二级 → 三级趋势计算 · 已处理{processed}/{total}个交易日 · 截至{}",bars[index].timestamp),
                    analysis:None,account:None,audit:Vec::new(),signals:Vec::new()});
            }
        }
        *timings.entry("polyline_scan".into()).or_default()+=phase.elapsed().as_secs_f64();
        let phase=std::time::Instant::now();analyzer.finish_polyline_array()?;
        *timings.entry("polyline_finalize".into()).or_default()+=phase.elapsed().as_secs_f64();
        for level in 1..=3{
            let phase=std::time::Instant::now();analyzer.finish_level_array(level)?;
            *timings.entry(format!("level{level}_total")).or_default()+=phase.elapsed().as_secs_f64();
        }
        progress(&SystemProgress{analysis_elapsed_seconds:analysis_started.elapsed().as_secs_f64(),percent:100,processed,total,
            symbol:symbol.clone(),asof:bars.last().map_or_else(String::new,|b|b.timestamp.clone()),
            stage:"趋势已完成，计算已开启级别的资格确认与结束区间".into(),analysis:None,account:None,audit:Vec::new(),signals:Vec::new()});
        analyzer.finish_qualification_arrays(cancel.as_deref())?;
        for(key,seconds)in analyzer.analysis_timings(){*timings.entry(key.clone()).or_default()+=seconds;}
        let phase=std::time::Instant::now();outputs.insert(symbol.clone(),analyzer.indexed_response());market.insert(symbol,bars);
        *timings.entry("indexed_result".into()).or_default()+=phase.elapsed().as_secs_f64();
    }
    // 兼容结果容器，保持空账户；仅折线模式不产生收益或成交。
    let account=StrategyBacktest::new(config.execution).map_err(|error|error.to_string())?;
    Ok(SystemBacktest{analysis_timings:timings,analysis_elapsed_seconds:analysis_started.elapsed().as_secs_f64(),account,indexed_analysis:outputs,analysis:BTreeMap::new(),bars:market,audit:Vec::new(),signals:Vec::new(),
        minute_fallbacks:Vec::new(),unavailable_rules:Vec::new()})
}
