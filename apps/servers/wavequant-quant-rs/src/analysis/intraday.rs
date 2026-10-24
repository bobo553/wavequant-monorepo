//! Python analytics/intraday_entry.py、intraday_wave_exit.py 的因果分钟消费层。
//! 只读取上一日已完成的结构；当前日仅用调用方已观察的部分行情。
use super::{kernel::{KernelFrame,KernelPoint},profiles::ExecutionParameters,trading::{self,RuleCandle,RuleDecision,TradingEligibility,
    day::TradingDayState,config::TradingDayConfig,entries,exits,risk::{self,HoldingRiskPolicy},hierarchy,waves,squeeze,arbiter,entry_risk,
    qualification::{self,index,price}}};
use crate::execution::{BacktestObservation,BacktestPosition,ObservationPhase,ExecutionMode};
use chrono::{DateTime,Utc,Duration,Timelike};
use rust_decimal::Decimal;
use serde::{Serialize,Deserialize};
use crate::indexed_json::{Value,json};
use std::collections::BTreeSet;

pub struct IntradayContext<'a> {
    pub cached_through:Option<usize>,pub frames:&'a[KernelFrame],pub eligibilities:&'a[TradingEligibility],
    pub trading:&'a TradingDayState,pub config:&'a TradingDayConfig,pub execution:&'a ExecutionParameters,
    pub holding:&'a HoldingRiskPolicy,pub minutes_available:bool,pub daily_fallback:bool,
}
#[derive(Debug,Clone,Serialize,Deserialize)]
pub enum IntradayDecision {
    Enter{known_at:DateTime<Utc>,bar_index:usize,execution_model:ExecutionMode,reason:String,reference_price:Decimal,
        stop_price:Decimal,target_price:Option<Decimal>,minimum_reward_risk:Decimal,evidence:Value},
    Exit{known_at:DateTime<Utc>,bar_index:usize,execution_model:ExecutionMode,rule:RuleDecision},
}
#[derive(Debug,Default,Clone,Serialize,Deserialize)]
pub struct IntradayState {
    pub audit:Vec<Value>,pub arbiter:arbiter::EntryArbiterState,pub staged:exits::StagedExitState,
    symbol:Option<String>,day:Option<usize>,last_observation:Option<DateTime<Utc>>,last_phase:Option<ObservationPhase>,position_key:Option<String>,
    emitted_entry_day:Option<usize>,emitted_exit:BTreeSet<String>,attempted:BTreeSet<(Decimal,Decimal,Decimal,Decimal)>,
    audit_keys:BTreeSet<String>,gap_checkpoint:entries::GapEntryGate,
}
fn merge(target:&mut Value,source:&Value){if let(Some(t),Some(s))=(target.as_object_mut(),source.as_object()){for(k,v)in s{t.insert(k.clone(),v.clone());}}}
fn emit(state:&mut IntradayState,value:Value)->Result<(),String>{
    let key=crate::indexed_json::to_string(&value).map_err(|e|e.to_string())?;
    if state.audit_keys.insert(key){state.audit.push(value);}Ok(())
}
fn known_events(context:&IntradayContext<'_>,cutoff:usize)->Vec<Value>{context.trading.events.iter().filter(|event|
    index(event,"bar_index").is_some_and(|i|i.max(index(event,"known_at").unwrap_or(i))<=cutoff)).cloned().collect()}
fn entry_rejected(state:&mut IntradayState,observation:&BacktestObservation,reason:&str,proof:Value)->Result<(),String>{
    emit(state,json!({"event":"entry_rejected","bar_index":observation.bar_index,"decision_timestamp":observation.at,
        "candidate_channel":"global_minute_entry","reason":reason,"evidence":proof}))
}
fn observed(observation:&BacktestObservation)->Value{let b=&observation.candle;json!({"decision_timestamp":observation.at,
    "decision_source":if observation.phase==ObservationPhase::DailyOpen{"missing_minute_open_fallback"}else if observation.phase==ObservationPhase::DailyClose{"daily_close"}else{"completed_five_minute_bar"},
    "observed_open":b.open,"observed_high":b.high,"observed_low":b.low,"observed_close":b.close,"observed_volume":b.volume,
    "candle_filter_model":"observed_prefix_candle_filter","adjustment_factor":b.adjustment_factor})}
fn active_window<'a>(eligibility:&'a TradingEligibility,now:usize,cutoff:usize)->Option<&'a qualification::TradingInterval>{
    eligibility.intervals.iter().filter(|w|w.start_index<=cutoff&&w.start_index<=now&&w.end_index.is_none_or(|end|now<end)).max_by_key(|w|w.start_index)
}
fn qualification_proof<'a>(events:&'a[Value],window:&qualification::TradingInterval)->Option<&'a Value>{events.iter().rev().find(|e|
    index(e,"bar_index")==Some(window.start_index)&&index(e,"trend_level")==Some(usize::from(window.trend_level))
    &&e.get("source_path").and_then(Value::as_str)==Some(window.source_path.as_str())
    &&e.get("state").and_then(Value::as_str)==Some("空多交替")&&qualification::confirmed_alternation_event(e))}
fn target_rejection(bars:&[RuleCandle],now:usize,events:&[Value],at_close:bool)->Option<RuleDecision>{let b=&bars[now];
    entries::one_p_breakout_entry_rejection(events,now,b.high).or_else(||entries::two_t_entry_rejection(bars,now,
        &entries::entry_targets_from_events(events,now,at_close),b.high,b.close,at_close))}

/// 母棒非十字、内包阳子棒，随后同时跌破子棒最低与收盘；只返回当时可见证据。
pub fn mother_child_inverse_n_break(bars:&[RuleCandle],now:usize)->Option<RuleDecision>{
    if now<2||now>=bars.len(){return None;}let(mother,child,current)=(&bars[now-2],&bars[now-1],&bars[now]);
    if mother.open==mother.close||child.close<=child.open||!trading::teaching_inside(mother,child)
        ||current.low>=child.low||current.close>=child.close{return None;}
    Some(RuleDecision::new("mother_child_inverse_n_low_break",now,Decimal::ONE)
        .with("mother_date",trading::secondary::session_date(mother)).with("mother_high",mother.high).with("child_date",trading::secondary::session_date(child))
        .with("child_low",child.low).with("child_high",child.high).with("child_close",child.close)
        .with("observed_low",current.low).with("observed_close",current.close))
}
/// 当日开盘相对昨日实体的位置及此前目标触达，只决定是否值得观察分钟退出。
pub fn five_top_intraday_eligible(bars:&[RuleCandle],now:usize,events:&[Value])->bool{
    if now==0||now>=bars.len(){return false;}let(mother,current)=(&bars[now-1],&bars[now]);
    let(lo,hi)=(mother.open.min(mother.close),mother.open.max(mother.close));
    if current.open>=mother.close&&!(lo<hi&&lo<=current.open&&current.open<=hi){return false;}
    let invalidated:BTreeSet<_>=events.iter().filter(|e|index(e,"bar_index").is_some_and(|i|i<now)
        &&e.get("event").and_then(Value::as_str)==Some("wave_projection_invalidated"))
        .map(|e|(index(e,"attack"),index(e,"origin_index"))).collect();
    events.iter().any(|e|index(e,"bar_index").is_some_and(|i|i<now)&&e.get("event").and_then(Value::as_str)==Some("wave_projection_target_reached")
        &&matches!(e.get("reached_stage").and_then(Value::as_str),Some("five_top"|"ten_full"))
        &&!invalidated.contains(&(index(e,"attack"),index(e,"origin_index"))))
}
pub fn observe_intraday_five_top_exit(bars:&[RuleCandle],now:usize,events:&[Value])->Option<RuleDecision>{
    if !five_top_intraday_eligible(bars,now,events){return None;}
    let mut known:Vec<_>=events.iter().filter(|e|index(e,"bar_index").is_some_and(|i|i<now)).cloned().collect();
    let mut revoked=vec![];
    for e in &known{let origin=price(e,"a_origin").or_else(||price(e,"origin")).or_else(||index(e,"origin_index").filter(|i|*i<now).map(|i|bars[i].low));
        if origin.is_some_and(|p|bars[now].low<p){let mut row=e.clone();row["event"]=json!("wave_projection_invalidated");row["bar_index"]=json!(now);revoked.push(row);}}
    known.extend(revoked);
    risk::observe_five_top_risks(bars,now,&known).into_iter().find(|d|matches!(d.reason.as_str(),"wave_five_top_body_upper_shadow_clear"|"wave_five_top_gap_upper_shadow_clear"))
}
fn exit_decision(state:&mut IntradayState,observation:&BacktestObservation,mut rule:RuleDecision)->Result<Option<IntradayDecision>,String>{
    let key=format!("{}:{}:{}:{}",state.position_key.as_deref().unwrap_or(""),observation.bar_index,rule.reason,rule.fraction);
    if !state.emitted_exit.insert(key){return Ok(None);}
    rule.evidence.insert("execution_model".into(),json!("intraday_5m_next_open"));rule.evidence.insert("decision_timestamp".into(),json!(observation.at));
    rule.evidence.insert("decision_source".into(),json!("completed_five_minute_bar"));
    emit(state,json!({"event":"intraday_exit_observed","bar_index":observation.bar_index,"decision":rule}))?;
    Ok(Some(IntradayDecision::Exit{known_at:observation.at,bar_index:observation.bar_index,execution_model:ExecutionMode::Intraday5mNextOpen,rule}))
}
fn qualified_entry(bars:&[RuleCandle],observation:&BacktestObservation,context:&IntradayContext<'_>,events:&[Value],
    eligibility:&TradingEligibility,gate:&entries::PolylineBuyGate,cutoff:usize)->Option<IntradayDecision>{
    // 日收候选由唯一日调度完成门禁与选择；分钟层不得再选一次覆盖其结果.
    let now=observation.bar_index;if now==0||observation.phase==ObservationPhase::DailyClose||context.config.buy_point_definition!="whole_flip_wave_v3"{return None;}
    let window=active_window(eligibility,now,cutoff)?;let confirmation=qualification_proof(events,window)?;
    let stop=window.start_low_price?;let (bar,previous)=(&bars[now],&bars[now-1]);
    if stop>=bar.close||gate.restrictions.contains_key(&now){return None;}
    if bar.open<=previous.high{return None;}
    let fallback=observation.phase==ObservationPhase::DailyOpen&&!context.minutes_available;
    if !fallback&&(bar.low<=previous.high||bar.close<=bar.open){return None;}
    if fallback&&!(context.daily_fallback&&context.execution.missing_minute_daily_fallback){return None;}
    let reason="资格区间跳空买入".into();
    let mode=if fallback{ExecutionMode::SameDayOpen}else{ExecutionMode::Intraday5mNextOpen};
    let mut proof=json!({"buy_point_type":"qualified_gap_open","minute_fallback":fallback,"minute_coverage":if fallback{"missing"}else{"available"}});
    merge(&mut proof,&observed(observation));merge(&mut proof,&json!({"qualification_index":window.start_index,"trend_level":window.trend_level,
        "source_path":window.source_path,"qualification_breakout_level":confirmation.get("breakout_level"),"qualification_confirmation":confirmation,
        "stop":stop,"stop_basis":"qualification_start_low","stop_source_index":window.start_low_index,"target_policy":"account_r_multiple",
        "account_take_profit_r":context.execution.take_profit_r,"minimum_reward_risk":context.config.minimum_reward_risk,
        "price_basis":"adjusted_observed_price","quantity_and_net_reward_risk":"execution_account_at_actual_fill",
        "cached_through":context.cached_through,"target_guard_events":execution_guard_events(events),"execution_model":mode,"polyline_breakout_evidence":gate.breakouts.get(&now)}));
    Some(IntradayDecision::Enter{known_at:observation.at,bar_index:now,execution_model:mode,reason,reference_price:bar.close,
        stop_price:stop,target_price:None,minimum_reward_risk:context.config.minimum_reward_risk,evidence:proof})
}

fn wave_setup(event:&Value)->Option<waves::WaveProjectionSetup>{Some(waves::WaveProjectionSetup{
    origin_index:index(event,"origin_index")?,attack_index:index(event,"attack_index")?,squeeze_index:index(event,"squeeze_index")?,
    origin:price(event,"origin")?,box_anchor:price(event,"box_anchor")?,two_t:price(event,"two_t")?,defense:price(event,"defense")?})}
fn minute_proofs(bars:&[RuleCandle],now:usize,context:&IntradayContext<'_>,events:&[Value],points:&[KernelPoint],epoch:usize)
    ->Result<Vec<arbiter::CandidateProof>,String>{
    let mut proofs=vec![];let current=context.trading.contexts_at(now-1);
    for n in context.trading.prepared.iter().filter(|n|n.measurement.setup.direction==trading::Direction::Up&&n.known_at()<now&&n.n_level>=1){
        let attack=n.attack();let measurement=&n.measurement;let Some(completion)=&measurement.observation.completion else{continue;};
        let Some(event)=events.iter().rev().find(|e|e.get("event").and_then(Value::as_str)==Some("n_completed")&&e.get("n_id").and_then(Value::as_str)==Some(n.identity.as_str()))else{continue;};
        if index(event,"known_at").unwrap_or(attack)>=now{continue;}
        let consolidation=if n.epoch==epoch{squeeze::consolidation_gap(bars,attack,now,completion.defense)}else{None};
        let mut wave=None;
        for ready in events.iter().rev().filter(|e|e.get("event").and_then(Value::as_str)==Some("wave_continuation_ready")
            &&e.get("source_id").or_else(||e.get("n_id")).and_then(Value::as_str)==Some(n.identity.as_str())){
            let Some(known)=index(ready,"bar_index")else{continue;};
            if known>=now||known<measurement.source.window_start||n.epoch!=epoch{continue;}
            let Some(setup)=wave_setup(ready)else{continue;};if bars[now].open<setup.defense{continue;}
            if context.config.buy_point_definition=="whole_flip_wave_v3"{
                let live:BTreeSet<_>=current.iter().map(hierarchy::EntryContext::episode).collect();
                if !hierarchy::wave_entry_contexts(current,context.trading.contexts_at(attack),attack,now-1,true).iter().any(|c|live.contains(&c.episode())){continue;}
            }
            let confirmed=context.trading.waves.a_confirmations.get(&n.identity).copied().filter(|i|*i<now);
            if let Some(mut proof)=waves::wave_gap_entry(bars,&setup,now,points,confirmed){proof["wave_a_source_id"]=json!(n.identity);wave=Some(proof);break;}
        }
        if consolidation.is_none()&&wave.is_none(){continue;}
        let Some(mut frame)=n.regime.frames.iter().rev().find(|f|f.bar_index<now).cloned()else{continue;};
        // 此布尔只由上面的完整 defended-N gap 证明产生，不把旧六态标签当当日确认。
        if consolidation.is_some(){frame.bar_index=now;frame.observed_at=bars[now].timestamp.clone();frame.regime=Some(super::market_state::regime::MarketRegime::Bull);
            frame.phase="confirmed_on_this_bar".into();frame.close_continuation=true;frame.last_confirmed_regime=frame.regime;frame.last_confirmed_index=Some(now);}
        let candidate=hierarchy::HierarchyCandidate::from(n);
        let selection=if context.config.entry_policy=="hierarchical_two_buy_points"{
            if context.config.buy_point_definition=="whole_flip_wave_v3"{let mut options=hierarchy::WaveEntryOptions::from_config(context.config);options.allow_confirming_n=true;
                options.allow_same_bar_pullback=measurement.setup.allow_outside_close&&measurement.setup.pullback.index==attack;
                hierarchy::select_wave_entry(current,context.trading.contexts_at(attack),bars,&candidate,now,&options)?
            }else{hierarchy::select_entry(current,context.trading.contexts_at(attack),&candidate,now,context.config.mature_shallow_ratio,
                (context.config.buy_interval_level>0).then_some(context.config.buy_interval_level))?}
        }else if context.config.entry_policy=="transitioned_squeeze"{
            if let Some(permission)=context.trading.permission_at(now-1).filter(|permission|permission.permits(attack)){
                hierarchy::EntrySelection{entry:Some(json!({"buy_point_type":"transitioned_squeeze","permission":permission})),reason:String::new()}
            }else{hierarchy::EntrySelection{entry:None,reason:"bullish_transition_not_ready".into()}}
        }else{hierarchy::EntrySelection{entry:None,reason:String::new()}};
        proofs.push(arbiter::CandidateProof{identity:n.identity.clone(),frame,hierarchy:selection.entry,hierarchy_rejection:(!selection.reason.is_empty()).then_some(selection.reason),
            multilevel:None,nested:None,wave,consolidation,reconfirmation:None,reversal:None,resumed:false});
    }Ok(proofs)
}

/// 单品种状态只接收递增观察时钟；所有价、量均为已经完成的分钟聚合。
pub fn observe_intraday(bars:&[RuleCandle],observation:&BacktestObservation,position:Option<&BacktestPosition>,
    context:&IntradayContext<'_>,state:&mut IntradayState)->Result<Vec<IntradayDecision>,String>{
    let now=observation.bar_index;let Some(bar)=bars.get(now)else{return Err("minute daily prefix unavailable".into());};
    if bars.len()!=now+1||bar.symbol!=observation.candle.symbol||bar.timestamp!=observation.candle.timestamp
        ||bar.open!=observation.candle.open||bar.high!=observation.candle.high||bar.low!=observation.candle.low||bar.close!=observation.candle.close||bar.volume!=observation.candle.volume{
        return Err("minute observation must match the visible prefix exactly".into());}
    if observation.phase==ObservationPhase::MinuteOpen{return Ok(vec![]);}
    let cutoff=if observation.phase==ObservationPhase::DailyClose{Some(now)}else{now.checked_sub(1)};
    if context.cached_through!=cutoff{return Err("minute consumers require the matching completed daily cache".into());}
    if state.symbol.as_ref().is_some_and(|s|s!=&bar.symbol)||state.last_observation.is_some_and(|last|observation.at<last)
        ||state.day.is_some_and(|day|now<day){return Err("minute state cannot cross symbols or reverse its observation clock".into());}
    if state.last_observation==Some(observation.at)&&state.day==Some(now)&&state.last_phase==Some(observation.phase){return Ok(vec![]);}
    state.symbol=Some(bar.symbol.clone());state.last_observation=Some(observation.at);state.last_phase=Some(observation.phase);
    let Some(cutoff)=cutoff else{state.day=Some(now);return Ok(vec![]);};
    let mut events=known_events(context,cutoff);
    if state.day!=Some(now){
        state.day=Some(now);state.attempted.clear();
        let prior=state.arbiter.clone();state.arbiter=context.trading.arbiter.clone();
        state.arbiter.emitted_attacks.extend(prior.emitted_attacks);state.arbiter.emitted_nested.extend(prior.emitted_nested);
        for(k,v)in prior.emitted_waves{if state.arbiter.emitted_waves.get(&k).is_none_or(|existing|waves::wave_confirmation_is_new(&json!({"wave_confirmation_phase":v.0,"wave_gap_high":v.1}),Some(existing))){state.arbiter.emitted_waves.insert(k,v);}}
        let mut round_events=events.clone();round_events.extend(state.audit.iter().filter(|e|index(e,"bar_index").is_some_and(|i|i<now)).cloned());
        state.arbiter.round=squeeze::SqueezeNRound::before(&round_events,now)?;
        if now>0{state.gap_checkpoint.advance(&bars[..now],now-1)?;}
    }
    let key=position.map(|p|format!("{}:{}",p.symbol,p.entry_time));
    if state.position_key!=key{state.position_key=key;state.staged=Default::default();state.emitted_exit.clear();}
    let mut output=vec![];
    if let Some(position)=position{
        if observation.phase==ObservationPhase::CompletedMinute&&context.holding.staged_exit_intraday{
            if position.entry_index<now{if let Some(rule)=exits::volume_shadow_next_decline(bars,now,Some(bar)){
                if let Some(decision)=exit_decision(state,observation,rule)?{output.push(decision);}return Ok(output);
            }}
            let trading_eligible=context.config.buy_interval_level==0||context.eligibilities.iter().find(|s|s.level==context.config.buy_interval_level)
                .is_some_and(|s|active_window(s,now,cutoff).is_some());
            if context.holding.wave_exhaustion_exit&&trading_eligible{if let Some(rule)=observe_intraday_five_top_exit(bars,now,&events){
                if let Some(decision)=exit_decision(state,observation,rule)?{output.push(decision);}return Ok(output);
            }}
            let local=observation.at+Duration::hours(8);let minute=local.hour()*60+local.minute();
            if position.entry_index<now&&context.holding.staged_exit_enabled&&context.holding.staged_exit_intraday&&(870..900).contains(&minute){
                if let Some(rule)=exits::observe_intraday_staged_exit(bars,now,&mut state.staged,bar.low,bar.close)?{
                    if let Some(decision)=exit_decision(state,observation,rule)?{output.push(decision);}
                }
            }
        }
        if !context.execution.allow_add_on||!output.is_empty(){return Ok(output);}
    }
    if now==0||state.emitted_entry_day==Some(now){return Ok(output);}
    if observation.phase==ObservationPhase::DailyOpen&&context.minutes_available{return Ok(output);}
    if !context.minutes_available&&!(context.daily_fallback&&context.execution.missing_minute_daily_fallback){
        emit(state,json!({"event":"minute_coverage_unavailable","bar_index":now,"reason":"minute_history_missing","daily_fallback":false}))?;
        return Ok(output);
    }
    let Some(eligibility)=context.eligibilities.iter().find(|s|s.level==context.config.buy_interval_level)else{return Err("minute qualification level unavailable".into());};
    let unrestricted=context.config.buy_interval_level==0;let window=active_window(eligibility,now,cutoff);
    if !unrestricted&&window.is_none(){return Ok(output);}
    if let Some(window)=window.filter(|_|!unrestricted){if window.gap_replaced_index.is_none(){if let Some(gap)=window.confirmation_gap.as_ref().filter(|gap|gap.index<now&&bar.low<=gap.lower){
        entry_rejected(state,observation,"confirmation_gap_filled",json!({"confirmation_gap_index":gap.index,"confirmation_gap_lower":gap.lower,
            "confirmation_gap_upper":gap.upper,"confirmation_gap_kind":gap.kind,"market_low":bar.low,"source_path":window.source_path}))?;return Ok(output);
    }}}
    let Some(frame)=context.frames.iter().find(|f|f.level==0)else{return Err("minute shared polyline cache unavailable".into());};
    let whole=context.config.buy_point_definition=="whole_flip_wave_v3";
    let mut gate=if whole{eligibility.polyline_gate.clone()}else{entries::PolylineBuyGate::default()};
    if whole&&gate.index!=Some(now){gate.advance(bars,now,frame)?;}
    if let Some(rejection)=gate.restrictions.get(&now){entry_rejected(state,observation,&rejection.reason,json!(rejection.evidence))?;return Ok(output);}
    if observation.phase!=ObservationPhase::DailyClose{events.extend(context.trading.preview_one_p(bars,context.frames,now)?);}
    if let Some(rejection)=target_rejection(bars,now,&events,observation.phase==ObservationPhase::DailyClose){entry_rejected(state,observation,&rejection.reason,json!(rejection.evidence))?;return Ok(output);}
    // 资格缺口是 Python 独立入场入口；只使用它明确要求的折线、阴棒和 1P/2T 门禁。
    if !unrestricted&&whole&&bar.open>bars[now-1].high{
        if let Some(reason)=entries::volume_bearish_entry_rejection(bars,now){entry_rejected(state,observation,reason,observed(observation))?;return Ok(output);}
        if let Some(decision)=qualified_entry(bars,observation,context,&events,eligibility,&gate,cutoff){
            state.emitted_entry_day=Some(now);emit(state,json!({"event":"intraday_entry_observed","bar_index":now,"decision":decision}))?;output.push(decision);return Ok(output);
        }
    }
    let rejection=entries::volume_upper_shadow_rejection(bar,Some(&bars[now-1]))
        .or_else(||whole.then(||entries::volume_bearish_entry_rejection(bars,now)).flatten());
    if let Some(reason)=rejection{entry_rejected(state,observation,reason,observed(observation))?;return Ok(output);}
    let epoch=frame.candidate.as_ref().or_else(||frame.points.last()).map_or(0,|p|p.path);
    let points:Vec<_>=frame.points.iter().filter(|p|p.path==epoch&&p.is_closed()&&p.known_index<now&&p.index<now).cloned().collect();
    if whole{if let Some(rejection)=entries::inverse_n_low_entry_risk(bars,now,&points){entry_rejected(state,observation,&rejection.reason,json!(rejection.evidence))?;return Ok(output);}}
    let mut gap=state.gap_checkpoint.clone();
    if let Some(rejection)=gap.advance(bars,now)?{entry_rejected(state,observation,&rejection.reason,json!(rejection.evidence))?;return Ok(output);}
    if observation.phase==ObservationPhase::DailyOpen||!context.execution.consolidation_entry_intraday{return Ok(output);}
    // 完整分钟存在时不得在收盘重发已拒绝的分钟信号；普通日级信号由日调度消费。
    if observation.phase==ObservationPhase::DailyClose&&context.minutes_available{return Ok(output);}
    let volume_state=if context.config.buy_point_definition=="whole_flip_wave_v3"{if bar.volume>bars[now-1].volume{Decimal::ONE}else{Decimal::ZERO}}else{bar.volume};
    if !state.attempted.insert((bar.high,bar.low,bar.close,volume_state)){return Ok(output);}

    let proofs=minute_proofs(bars,now,context,&events,&points,epoch)?;if proofs.is_empty(){return Ok(output);}
    let scopes=if unrestricted{BTreeSet::from([format!("unrestricted-{epoch}:{epoch}")])}else{eligibility.intervals.iter().filter(|w|w.start_index<=cutoff&&w.end_index.is_none_or(|end|now<end))
        .map(|w|format!("{}:{}",w.source_path,w.start_index)).collect()};
    let mut risk_state=context.trading.entry_risk_state().clone();
    let entry_risks=entry_risk::evaluate_entry_risks(bars,now,&events,&mut risk_state,context.config)?;
    let risks=arbiter::EntryRisks{target:entry_risks.target,five_top:entry_risks.five_top,ten_full:entry_risks.ten_full,secondary:context.trading.secondary.preview_pressure(bars,now)?};
    let mut trial=state.arbiter.clone();let result=arbiter::arbitrate_n_entries(bars,now,&context.trading.prepared,&proofs,&events,&scopes,epoch,
        context.config,&risks,&mut trial,true)?;
    for event in result.events{emit(state,event)?;}
    state.arbiter=trial;let Some(candidate)=result.decision else{return Ok(output);};
    let fallback=!context.minutes_available;let mode=if fallback{ExecutionMode::SameDayClose}else{ExecutionMode::Intraday5mNextOpen};
    let mut proof=candidate.evidence;let stop_basis=if proof.get("wave").is_some_and(|v|!v.is_null()){"frozen_wave_defense"}else{"frozen_n_defense_or_origin"};merge(&mut proof,&observed(observation));merge(&mut proof,&json!({"execution_model":mode,
        "qualification_index":window.filter(|_|!unrestricted).map(|w|w.start_index),"trend_level":context.config.buy_interval_level,"source_path":window.filter(|_|!unrestricted).map(|w|w.source_path.clone()),
        "qualification_filter_enabled":!unrestricted,"source_scope":if unrestricted{Some(format!("unrestricted-{epoch}:{epoch}"))}else{None},"minute_fallback":fallback,"minute_coverage":if fallback{"missing"}else{"available"},"cached_through":context.cached_through,
        "stop":candidate.stop,"target":candidate.target,"stop_basis":stop_basis,
        "target_policy":"unreached_structural_target","minimum_reward_risk":context.config.minimum_reward_risk,
        "target_guard_events":execution_guard_events(&events),"execution_reward_risk_requires_recheck":true}));
    let decision=IntradayDecision::Enter{known_at:observation.at,bar_index:now,execution_model:mode,reason:candidate.reason,
        reference_price:bar.close,stop_price:candidate.stop,target_price:candidate.target,minimum_reward_risk:context.config.minimum_reward_risk,evidence:proof};
    state.emitted_entry_day=Some(now);emit(state,json!({"event":"intraday_entry_observed","bar_index":now,"decision":decision}))?;output.push(decision);Ok(output)
}
fn execution_guard_events(events:&[Value])->Vec<Value>{events.iter().filter(|e|matches!(e.get("event").and_then(Value::as_str),
    Some("n_completed"|"n_invalidated"|"n_target_source_retired"|"one_p_breakout_watch"))).cloned().collect()}
/// 下一分钟开盘的真实成交价也必须通过全局 1P/2T 门禁；调用方只传原决策已经知道的事件。
pub fn execution_entry_rejection(bars:&[RuleCandle],now:usize,events:&[Value],observed_high:Decimal,execution_price:Decimal)->Option<RuleDecision>{
    entries::one_p_breakout_entry_rejection(events,now,observed_high.max(execution_price))
        .or_else(||entries::two_t_entry_rejection(bars,now,&entries::entry_targets_from_events(events,now,false),
            observed_high.max(execution_price),execution_price,false))
}
