//! 所有已证明的N入口共用最后的量价、反N、目标与风险收益仲裁。
use super::{RuleCandle,RuleDecision,Direction,config::TradingDayConfig,candidates::PreparedN,squeeze::{self,SqueezeN,SqueezeNRound,InverseEpisode},entries,
    qualification::{index,price},day::EntryDecision};
use super::super::market_state::regime::{RegimeFrame,MarketRegime};
use rust_decimal::Decimal;
use serde::{Serialize,Deserialize};
use crate::indexed_json::{Value,json};
use std::collections::{BTreeMap,BTreeSet};
#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct CandidateProof{
    pub identity:String,pub frame:RegimeFrame,pub hierarchy:Option<Value>,pub hierarchy_rejection:Option<String>,
    pub multilevel:Option<Value>,pub nested:Option<Value>,pub wave:Option<Value>,pub consolidation:Option<Value>,
    pub reconfirmation:Option<Value>,pub reversal:Option<Value>,pub resumed:bool,
}
#[derive(Debug,Default,Clone,Serialize,Deserialize)]
pub struct EntryArbiterState{pub round:SqueezeNRound,pub emitted_attacks:BTreeSet<usize>,pub emitted_nested:BTreeSet<(usize,usize)>,pub emitted_waves:BTreeMap<String,(String,Decimal)>}
#[derive(Debug,Default,Clone,Serialize,Deserialize)]
pub struct EntryRisks{pub target:Option<RuleDecision>,pub five_top:Option<RuleDecision>,pub ten_full:Option<RuleDecision>,pub secondary:Option<Value>}
#[derive(Debug,Default,Clone,Serialize,Deserialize)]
pub struct EntryEvaluation{pub decision:Option<EntryDecision>,pub events:Vec<Value>}
fn round_event(now:usize,mut proof:Value)->Value{proof["event"]=json!("squeeze_round_changed");proof["bar_index"]=json!(now);proof["known_at"]=json!(now);proof}
fn source(n:&PreparedN)->Result<SqueezeN,String>{let c=n.measurement.observation.completion.as_ref().ok_or("N candidate is not complete")?;
    Ok(SqueezeN{origin:n.measurement.setup.origin.index,neckline:n.measurement.setup.neckline.index,pullback:n.measurement.setup.pullback.index,attack:c.bar_index,known_at:n.known_at(),defense:c.defense})}
pub fn inverse_episodes(candidates:&[PreparedN])->Vec<InverseEpisode>{candidates.iter().filter(|n|n.measurement.setup.direction==Direction::Down).filter_map(|n|{
    let c=n.measurement.observation.completion.as_ref()?;let a=n.measurement.observation.anchors.as_ref()?;
    Some(InverseEpisode{known_at:n.known_at(),attack:c.bar_index,b_index:n.measurement.setup.pullback.index,b_high:a.pullback,kill_high:Some(c.defense)})}).collect()}
fn retired(events:&[Value],now:usize)->BTreeSet<String>{events.iter().filter(|e|matches!(e.get("event").and_then(Value::as_str),Some("n_invalidated"|"n_target_source_retired"))
    &&index(e,"bar_index").zip(index(e,"known_at").or_else(||index(e,"bar_index"))).is_some_and(|(a,b)|a.max(b)<=now)).filter_map(|e|e.get("n_id").and_then(Value::as_str).map(str::to_string)).collect()}
pub fn live_target_event<'a>(events:&'a[Value],now:usize)->Option<&'a Value>{let retired=retired(events,now);events.iter().filter(|e|e.get("event").and_then(Value::as_str)==Some("n_completed")
    &&e.get("direction").and_then(Value::as_str)==Some("up")&&e.get("target_eligible").and_then(Value::as_bool)==Some(true)
    &&index(e,"bar_index").zip(index(e,"known_at")).is_some_and(|(a,b)|a.max(b)<=now)
    &&e.get("n_id").and_then(Value::as_str).is_some_and(|id|!retired.contains(id))).max_by_key(|e|(index(e,"known_at"),index(e,"bar_index")))}
fn wave_new(wave:&Value,previous:Option<&(String,Decimal)>)->bool{let Some((phase,high))=previous else{return true;};phase=="body"&&wave.get("wave_confirmation_phase").and_then(Value::as_str)==Some("gap")&&price(wave,"wave_gap_high").is_some_and(|p|p>*high)}
pub fn secondary_wave_recovery(bars:&[RuleCandle],now:usize,wave:&Value,pressure:&Value)->Option<Value>{
    super::secondary::secondary_wave_recovery(bars,now,wave,pressure)
}
pub fn inverse_wave_recovery(bars:&[RuleCandle],now:usize,wave:&Value,inverse:&[InverseEpisode])->Option<Value>{
    let episodes=inverse.iter().map(|e|json!({"known_at":e.known_at,"attack":e.attack,"b_index":e.b_index,"b_high":e.b_high,"kill_high":e.kill_high})).collect::<Vec<_>>();
    super::secondary::inverse_wave_recovery(bars,now,wave,&episodes)
}
fn reject(out:&mut EntryEvaluation,now:usize,attack:usize,reason:&str){out.events.push(json!({"event":"entry_rejected","bar_index":now,"candidate_attack":attack,"reason":reason}));}
fn text_event(value:&Value,event:&str)->bool{value.get("event").and_then(Value::as_str)==Some(event)}

pub fn arbitrate_n_entries(bars:&[RuleCandle],now:usize,candidates:&[PreparedN],proofs:&[CandidateProof],events:&[Value],
    scopes:&BTreeSet<String>,epoch:usize,config:&TradingDayConfig,risks:&EntryRisks,state:&mut EntryArbiterState,fully_executable:bool)->Result<EntryEvaluation,String>{
    let mut out=EntryEvaluation::default();if now>=bars.len(){return Err("entry arbitration date unavailable".into());}let bar=&bars[now];
    let whole=config.buy_point_definition=="whole_flip_wave_v3";let hierarchical=config.entry_policy=="hierarchical_two_buy_points";
    if whole{if let Some(proof)=state.round.observe_low(now,bar.low){out.events.push(round_event(now,proof));}
        if scopes.is_empty(){if let Some(proof)=state.round.close_unemitted(){out.events.push(round_event(now,proof));}return Ok(out);}
        let joint=squeeze::joint_squeeze_confirmations(events,config.buy_interval_level);let invalid=retired(events,now);
        let sources=candidates.iter().filter(|n|n.measurement.setup.direction==Direction::Up&&n.epoch==epoch&&scopes.contains(&n.measurement.source.scope())&&!invalid.contains(&n.identity)
            &&events.iter().any(|e|e.get("event").and_then(Value::as_str)==Some("n_completed")&&e.get("n_id").and_then(Value::as_str)==Some(n.identity.as_str())))
            .filter_map(|n|source(n).ok().map(|s|(n,s))).filter(|(n,s)|n.attack()>=n.measurement.source.window_start||joint.contains(&(n.measurement.source.source_path.clone(),n.measurement.source.window_start,s.identity())))
            .map(|(_,s)|s).collect::<Vec<_>>();
        if let Some(proof)=state.round.select(now,&sources){out.events.push(round_event(now,proof));}
    }
    if now==0{return Ok(out);}let previous=&bars[now-1];let inverse=inverse_episodes(candidates);
    if let Some(risk)=&risks.ten_full{reject(&mut out,now,now,&risk.reason);return Ok(out);}
    if let Some(risk)=entries::two_t_entry_rejection(bars,now,&entries::entry_targets_from_events(events,now,true),bar.high,bar.close,true){reject(&mut out,now,now,&risk.reason);return Ok(out);}
    if whole{if let Some(risk)=entries::one_p_breakout_entry_rejection(events,now,bar.high){reject(&mut out,now,now,&risk.reason);return Ok(out);}}
    let mut choices=proofs.iter().collect::<Vec<_>>();choices.sort_by_key(|p|{
        let attack=candidates.iter().find(|n|n.identity==p.identity).map_or(0,PreparedN::attack);
        (p.hierarchy.as_ref().and_then(|p|index(p,"priority")).unwrap_or(0),p.reconfirmation.is_none()||p.wave.is_some(),attack)});choices.reverse();
    for proof in choices{
        let Some(n)=candidates.iter().find(|n|n.identity==proof.identity)else{return Err("entry proof references an unknown N".into());};
        let m=&n.measurement;let attack=n.attack();let frame=&proof.frame;
        if m.setup.direction!=Direction::Up||(frame.bar_index!=now&&proof.wave.is_none())||n.known_at()>now||(!scopes.contains(&m.source.scope())&&proof.wave.is_none())
            ||(state.emitted_attacks.contains(&attack)&&proof.wave.is_none()&&proof.consolidation.is_none())||(epoch!=n.epoch&&proof.wave.is_none()){continue;}
        let wave_key=proof.wave.as_ref().and_then(|w|Some(format!("{}:{}:{}",attack,index(w,"wave_a_high_index")?,index(w,"wave_b_low_index")?)));
        if proof.wave.as_ref().zip(wave_key.as_ref()).is_some_and(|(w,key)|!wave_new(w,state.emitted_waves.get(key))){continue;}
        let nested_key=proof.nested.as_ref().and_then(|p|Some((index(p,"secondary_low_index")?,index(p,"primary_low_index")?)));
        if nested_key.is_some_and(|k|state.emitted_nested.contains(&k)){continue;}
        let source=source(n)?;
        if whole&&proof.wave.is_none(){if let Some(reason)=state.round.rejection(&source){reject(&mut out,now,attack,reason);continue;}}
        out.events.push(json!({"event":"entry_candidate_evaluated","bar_index":now,"candidate_attack":attack,"n_id":n.identity}));
        if whole{if let Some(reason)=entries::squeeze_buy_rejection(bar,Some(previous),attack.checked_sub(1).and_then(|i|bars.get(i))){reject(&mut out,now,attack,reason);continue;}}
        if let Some(risk)=risks.target.as_ref().or(risks.five_top.as_ref()){reject(&mut out,now,attack,&risk.reason);continue;}
        let recovered_pressure=proof.wave.as_ref().zip(risks.secondary.as_ref()).and_then(|(w,p)|secondary_wave_recovery(bars,now,w,p));
        if let Some(pressure)=&risks.secondary{let same=proof.multilevel.as_ref().is_some_and(|p|price(pressure,"secondary_high").zip(price(p,"key_price")).is_some_and(|(a,b)|a<=b)
            &&price(pressure,"secondary_resistance_high").zip(price(p,"higher_resistance_high")).is_some_and(|(a,b)|a<=b)
            &&pressure.get("secondary_attack_date").and_then(Value::as_str)==Some(super::secondary::session_date(&bars[attack]).as_str()));
            let same=same||proof.nested.as_ref().is_some_and(|p|price(pressure,"secondary_high").zip(price(p,"breakout_high")).is_some_and(|(a,b)|a<=b)
                &&pressure.get("secondary_attack_date").and_then(Value::as_str)==Some(super::secondary::session_date(&bars[now]).as_str()));
            if pressure.get("secondary_resistance_resolved").and_then(Value::as_bool)!=Some(true)&&!same&&recovered_pressure.is_none(){reject(&mut out,now,attack,"secondary_breakout_resistance_unresolved");continue;}}
        let regime=if proof.resumed||proof.consolidation.is_some()||proof.wave.is_some(){Some(MarketRegime::Bull)}else{frame.regime};
        if whole&&proof.nested.is_none()&&bar.close<=bars[attack].close{reject(&mut out,now,attack,"squeeze_below_original_n_close");continue;}
        let volume=entries::squeeze_volume_pass(bar,Some(previous),attack.checked_sub(1).and_then(|i|bars.get(i)));
        let attack_failure=whole&&frame.regime==Some(MarketRegime::Bull)&&frame.first_resistance_index.is_some_and(|i|i<=attack+1)&&now>=attack+2
            &&frame.first_defense_breach_index.is_none()&&bar.high>bars[attack].high&&bar.close>bars[attack].close&&bar.close>previous.close&&entries::strong_bullish_candle(bar);
        let record=regime==Some(MarketRegime::Bull)&&frame.first_defense_breach_index.is_none()&&frame.resistance.as_ref().is_some_and(|r|r.detected==Some(false)||now>attack+1)&&bar.close>frame.continuation_level&&volume;
        if whole&&n.attack_quality_warning.is_some()&&regime!=Some(MarketRegime::StrongBull)&&proof.consolidation.is_none()&&proof.wave.is_none()
            &&!record&&!(attack_failure&&volume)&&proof.multilevel.is_none()&&proof.nested.is_none()&&proof.reversal.is_none()&&proof.reconfirmation.is_none(){reject(&mut out,now,attack,"weak_n_requires_uninterrupted_squeeze");continue;}
        if hierarchical||config.entry_policy=="transitioned_squeeze"{
            if !matches!(regime,Some(MarketRegime::Bull|MarketRegime::StrongBull)){reject(&mut out,now,attack,"not_squeeze_regime");continue;}
            if let Some(reason)=&proof.hierarchy_rejection{reject(&mut out,now,attack,reason);continue;}
            if proof.hierarchy.is_none(){reject(&mut out,now,attack,"bullish_transition_not_ready");continue;}
        }
        if whole{let inverse_rejection=squeeze::inverse_reentry_rejection(bars,now,if proof.reconfirmation.is_some(){now}else{attack},&inverse,proof.consolidation.is_some()||proof.wave.is_some());
            let recovery=squeeze::fresh_strong_squeeze_recovery(bars,now,attack,m.setup.origin.index,&inverse,frame.regime==Some(MarketRegime::StrongBull))
                .or_else(||proof.hierarchy.as_ref().and_then(|p|squeeze::deep_pullback_recovery(bars,now,attack,&inverse,p,frame.first_defense_breach_index.is_none()
                    &&matches!(frame.regime,Some(MarketRegime::Bull|MarketRegime::StrongBull))&&bar.close>frame.continuation_level,attack_failure)))
                .or_else(||proof.wave.as_ref().and_then(|w|inverse_wave_recovery(bars,now,w,&inverse)));
            if let Some(risk)=inverse_rejection.filter(|_|recovery.is_none()){reject(&mut out,now,attack,&risk.reason);continue;}
        }
        let anchors=m.observation.anchors.as_ref().ok_or("completed N anchors missing")?;let targets=m.observation.targets.as_ref().ok_or("completed N targets missing")?;
        if !hierarchical&&anchors.retracement_ratio>=config.max_counter_ratio{reject(&mut out,now,attack,"countermove_too_deep");continue;}
        let rvol=if whole{(previous.volume>Decimal::ZERO).then(||bar.volume/previous.volume)}else{n.control.volume.as_ref().and_then(|v|v.relative_volume)};
        let volume_pass=(if whole{volume}else{rvol.is_some_and(|v|v>=config.minimum_rvol)})||proof.wave.as_ref().is_some_and(|w|matches!(w.get("wave_gap_trigger").and_then(Value::as_str),Some("breakout"|"breakout_and_volume")));
        if config.volume_filter&&!volume_pass{reject(&mut out,now,attack,"attack_volume_unavailable_or_low");continue;}
        let mut stop=if frame.first_defense_breach_index.is_some(){anchors.origin}else{source.defense};
        let live=if whole{live_target_event(events,now)}else{None};let mut target=None;
        let fields=if whole&&live.is_none(){vec!["equal_wave"]}else{vec!["equal_wave","one_p","two_t"]};
        for field in fields{let value=live.and_then(|e|price(e,field)).or_else(||match field{"equal_wave"=>targets.equal_wave,"one_p"=>targets.one_p,_=>targets.two_t});
            let Some(value)=value else{continue;};let known=live.and_then(|e|index(e,"known_at")).unwrap_or(n.known_at());
            if value>bar.close&&!bars[known..=now].iter().enumerate().any(|(offset,b)|if offset==0{b.close>=value}else{b.high>=value}){target=Some(value);break;}}
        let measured_id=live.and_then(|e|e.get("n_id")).and_then(Value::as_str).unwrap_or(n.identity.as_str());
        let two_t=live.and_then(|e|price(e,"two_t")).or(targets.two_t);let known=live.and_then(|e|index(e,"known_at")).unwrap_or(n.known_at());
        if target.is_none()&&two_t.is_some_and(|target|bars[known..=now].iter().enumerate().any(|(offset,b)|if offset==0{b.close>=target}else{b.high>=target})){
            target=events.iter().rev().find(|e|e.get("event").and_then(Value::as_str)==Some("n_entry_projection")&&index(e,"bar_index").is_some_and(|i|i<=now)
                &&e.get("n_id").and_then(Value::as_str)==Some(measured_id)).and_then(|e|e.get("projection")).and_then(|p|price(p,"target")).filter(|p|*p>bar.close);
        }
        if let Some(w)=&proof.wave{stop=price(w,"wave_defense").ok_or("wave proof defense missing")?;
            target=if w.get("wave_entry_path").and_then(Value::as_str)==Some("two_t_strong_a_resistance_rebreak"){price(w,"wave_c_0618_target").filter(|v|*v>bar.close)}else{None}
                .or_else(||price(w,"wave_equal_target").filter(|v|*v>bar.close));}
        let Some(target)=target.filter(|_|stop<bar.close)else{reject(&mut out,now,attack,"no_live_structural_risk_reward");continue;};
        let reward_risk=(target-bar.close)/(bar.close-stop);if config.preflight_reward_risk&&reward_risk<config.minimum_reward_risk{reject(&mut out,now,attack,"insufficient_close_gross_reward_risk");continue;}
        let reason=if proof.wave.is_some(){"wave_push_gap"}else if let Some(reason)=proof.hierarchy.as_ref().and_then(|p|p.get("buy_point_type")).and_then(Value::as_str){reason}
            else if proof.resumed{"squeeze_pullback_resume"}else if events.iter().any(|e|text_event(e,"washout_reattack")&&index(e,"attack").or_else(||index(e,"bar_index"))==Some(attack)&&index(e,"bar_index").is_some_and(|i|i<=now)&&e.get("direction").and_then(Value::as_str)==Some("up")){"washout_reattack"}
            else if events.iter().any(|e|text_event(e,"turn_confirmed")&&e.get("direction").and_then(Value::as_str)==Some("up")&&index(e,"bar_index").is_some_and(|t|t<attack&&attack-t<=config.pattern_ttl)){"positive_turn_then_n"}else{"n_continuation"};
        let counter=if whole{proof.hierarchy.as_ref().and_then(|p|price(p,"counter_ratio")).unwrap_or(anchors.retracement_ratio)}else{anchors.retracement_ratio};
        let signal_regime=regime.map_or(json!("n_only_ablation"),|regime|json!(regime));
        let confirmation_source=if let Some(wave)=&proof.wave{
            if wave.get("wave_a_class").and_then(Value::as_str)==Some("ordinary"){"one_p_wave_rebound"}
            else if wave.get("wave_entry_path").and_then(Value::as_str)==Some("two_t_strong_a_resistance_rebreak"){"strong_a_resistance_rebreak"}else{"two_t_wave_push_gap"}
        }else if proof.nested.is_some(){"secondary_primary_volume_breakout"}else if proof.reconfirmation.is_some(){"fresh_n_defeats_old_n_resistance"}
        else if proof.reversal.is_some(){"volume_reversal_record_break"}else if proof.consolidation.is_some(){"defended_n_consolidation_gap"}
        else if proof.resumed{"pullback_resumption"}else if whole&&regime==Some(MarketRegime::Bull)&&frame.resistance.as_ref().is_some_and(|r|r.long_shadow==Some(true))
            &&bar.open>previous.high&&bar.close>frame.continuation_level{"gap_up_bullish_record"}
        else if whole&&regime==Some(MarketRegime::Bull)&&bar.close>frame.continuation_level{"resistance_record_break"}
        else if attack_failure{"resistance_attack_bar_break"}else if whole&&regime==Some(MarketRegime::Bull){"local_resistance_failure"}
        else if whole{"uninterrupted_squeeze"}else{"canonical_record_event"};
        let evidence=json!({"n_id":n.identity,"attack":attack,"trigger_index":attack,"trigger_timestamp":bars[attack].timestamp,"counter_ratio":counter,"regime":signal_regime,
            "squeeze_confirmation":confirmation_source,"regime_evidence":frame,"hierarchy":proof.hierarchy,"wave":proof.wave,"multilevel":proof.multilevel,"nested":proof.nested,
            "reconfirmation":proof.reconfirmation,"reversal":proof.reversal,"consolidation":proof.consolidation,"rvol":rvol,"gross_reward_risk":reward_risk,
            "target_source_id":live.and_then(|e|e.get("n_id")),"source_path":m.source.source_path,"qualification_window_start":m.source.window_start});
        out.decision=Some(EntryDecision{bar_index:now,reason:format!("system_{reason}"),price:bar.close,stop,target:Some(target),qualification_index:m.source.window_start,
            trend_level:config.buy_interval_level,source_path:m.source.source_path.clone(),execution_model:"next_open".into(),fully_executable,evidence});
        if fully_executable{state.emitted_attacks.insert(attack);if whole&&proof.wave.is_none(){out.events.push(round_event(now,state.round.consume(&source,now)?));}
            if let Some(key)=nested_key{state.emitted_nested.insert(key);}if let Some(key)=wave_key{if let Some(w)=&proof.wave{state.emitted_waves.insert(key,(w.get("wave_confirmation_phase").and_then(Value::as_str).unwrap_or("").into(),price(w,"wave_gap_high").ok_or("wave high missing")?));}}}
        break;
    }Ok(out)
}
