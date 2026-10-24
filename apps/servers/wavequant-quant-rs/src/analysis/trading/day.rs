//! 每个已完成行情前缀推进一次交易上下文，复用四级结构及原始确认时钟。
use super::{RuleCandle,FrameTradeEvaluation,TradingEligibility,evaluate_frame_with_config,entries,qualification,targets,target_lifecycle,one_p,risk,candidates,arbiter,hierarchy,waves,secondary,entry_risk,shallow};
use super::super::market_state::{regime::{self,MarketRegime,RegimePolicy,WaveBoundary},price_action::ShadowPolicy};
use super::super::kernel::{KernelFrame,PointKind};
use rust_decimal::Decimal;
use serde::{Serialize,Deserialize};
use crate::indexed_json::{Value,json};
use std::collections::{BTreeMap,BTreeSet};

pub use super::config::TradingDayConfig;
#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct EntryDecision{
    pub bar_index:usize,pub reason:String,pub price:Decimal,pub stop:Decimal,pub target:Option<Decimal>,
    pub qualification_index:usize,
    /// 此处保留 Python buy_interval_level 的资格过滤选择；0 表示不限资格。具体入场结构级别见 evidence.hierarchy.trend_level。
    pub trend_level:u8,pub source_path:String,pub execution_model:String,pub fully_executable:bool,
    pub evidence:Value,
}
#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct TradingDayEvaluation{pub frames:Vec<FrameTradeEvaluation>,pub entry_candidates:Vec<EntryDecision>,pub events:Vec<Value>,pub unavailable_rules:Vec<String>,pub counts:BTreeMap<String,usize>,pub trading_applicable:bool}
#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct TradingDayState{
    last:Option<usize>,config:Option<TradingDayConfig>,pub events:Vec<Value>,
    #[serde(default)] pub diagnostics:super::diagnostics::TradingDiagnostics,
    structural_sources:targets::EligiblePolylineN,trading_sources:targets::EligiblePolylineN,
    structural:targets::StructuralNStream,targets:targets::PolylineTargetStream,target_lifecycle:target_lifecycle::TargetLifecycle,
    one_p:one_p::OnePBreakoutStream,pub risk:risk::RiskState,gap:entries::GapEntryGate,
    declines:BTreeMap<usize,usize>,seen_declines:BTreeSet<usize>,formal_decline_seen:bool,seed_decline:Option<usize>,
    preparation_events:BTreeSet<String>,pub prepared:Vec<candidates::PreparedN>,
    seen_washouts:BTreeSet<String>,
    pub waves:waves::WaveState,pub wave_output:waves::WaveDailyOutput,pub secondary:secondary::SecondaryDayState,pub arbiter:arbiter::EntryArbiterState,
    contexts:BTreeMap<usize,Vec<hierarchy::EntryContext>>,resistance:hierarchy::ResistanceLedger,
    derived_regimes:BTreeMap<String,BTreeMap<usize,regime::RegimeFrame>>,
    pub turns:super::turns::TurnState,pub minor_points:Vec<super::super::market_state::strength::ConfirmedPivot>,
    #[serde(default)] minor_data_supplied:bool,
    pub entry_risk:entry_risk::EntryRiskState,shallow:shallow::ShallowBaseState,
    permissions:hierarchy::BullPermissionState,permission_days:BTreeMap<usize,hierarchy::BullPermissionDay>,
    emitted_pullbacks:BTreeSet<(usize,usize)>,emitted_reclaims:BTreeSet<usize>,previous_polyline:Option<KernelFrame>,
}
impl Default for TradingDayState{fn default()->Self{
    let mut structural_sources=targets::EligiblePolylineN::default();structural_sources.folded_window=Some(120);structural_sources.structural=true;
    let mut trading_sources=targets::EligiblePolylineN::default();trading_sources.folded_window=Some(120);
    Self{last:None,config:None,events:vec![],diagnostics:Default::default(),structural_sources,trading_sources,
    structural:targets::StructuralNStream::new(60,true),targets:Default::default(),target_lifecycle:Default::default(),one_p:Default::default(),risk:Default::default(),gap:Default::default(),
    declines:BTreeMap::new(),seen_declines:BTreeSet::new(),formal_decline_seen:false,seed_decline:None,preparation_events:BTreeSet::new(),prepared:vec![],seen_washouts:BTreeSet::new(),
    waves:Default::default(),wave_output:Default::default(),secondary:Default::default(),arbiter:Default::default(),contexts:BTreeMap::new(),resistance:Default::default(),derived_regimes:BTreeMap::new(),turns:Default::default(),minor_points:vec![],minor_data_supplied:false,
    entry_risk:Default::default(),shallow:Default::default(),permissions:Default::default(),permission_days:BTreeMap::new(),emitted_pullbacks:BTreeSet::new(),emitted_reclaims:BTreeSet::new(),previous_polyline:None}}}
impl TradingDayState{
    pub fn counts(&self)->&BTreeMap<String,usize>{&self.diagnostics.counts}
    pub fn set_minor_points(&mut self,points:Option<Vec<super::super::market_state::strength::ConfirmedPivot>>){
        self.minor_data_supplied=points.is_some();self.minor_points=points.unwrap_or_default();
    }
    pub fn minor_data_available(&self)->bool{self.minor_data_supplied||!self.minor_points.is_empty()}
    pub fn contexts_at(&self,index:usize)->&[hierarchy::EntryContext]{self.contexts.get(&index).map(Vec::as_slice).unwrap_or(&[])}
    pub fn entry_risk_state(&self)->&entry_risk::EntryRiskState{&self.entry_risk}
    pub fn permission_at(&self,index:usize)->Option<&hierarchy::BullPermission>{self.permission_days.get(&index).and_then(|d|d.permission.as_ref())}
    pub fn preview_one_p(&self,bars:&[RuleCandle],frames:&[KernelFrame],now:usize)->Result<Vec<Value>,String>{
        let mut stream=self.one_p.clone();stream.advance(bars,now,&one_p_levels(frames,now),&self.structural.events)
    }
}

fn decline_arrivals(state:&mut TradingDayState,frames:&[KernelFrame],now:usize)->Vec<targets::DeclineStart>{
    if let Some(frame)=frames.iter().find(|f|f.level==1){
        for point in super::super::kernel::confirmed_decline_starts(frame,now){
            state.formal_decline_seen=true;state.declines.entry(point.index).and_modify(|known|*known=(*known).min(point.known_index)).or_insert(point.known_index);
        }
    }
    if !state.formal_decline_seen{
        if state.seed_decline.is_none(){state.seed_decline=frames.iter().find(|f|f.level==0).and_then(|f|f.points.iter().find(|p|p.kind==PointKind::H&&p.is_closed()&&p.known_index<=now)).map(|p|p.index);}
        if let Some(index)=state.seed_decline{state.declines.entry(index).or_insert(now);}
    }
    state.declines.iter().filter_map(|(&index,&known)|{if known<=now&&state.seen_declines.insert(index){Some(targets::DeclineStart{index,known_at:now})}else{None}}).collect()
}
fn one_p_levels(frames:&[KernelFrame],now:usize)->Vec<Value>{
    frames.iter().filter(|f|(1..=3).contains(&f.level)).map(|frame|{
        let mut paths:BTreeMap<String,Vec<Value>>=BTreeMap::new();
        for event in &frame.events{
            let Some(proof)=event.evidence.get("lifecycle_event")else{continue;};
            if event.known_index>now{continue;}
            let Some(path)=proof.get("source_path").and_then(Value::as_str)else{continue;};
            let Some(key)=&event.key else{continue;};
            paths.entry(path.into()).or_default().push(json!({"index":event.point.index,"available_at":event.known_index,
                "state":"confirmed","wave_identity_state":"owned","lifecycle_context":{"available_at":event.known_index,
                    "direction":proof.get("direction"),"key":{"index":key.index,"ordinal":key.ordinal,"kind":key.kind,"value":key.value,"available_at":key.known_index}}}));
        }
        json!({"trend_level":frame.level,"strokes":paths.into_iter().map(|(path,points)|json!({"id":path,"source_path":path,"points":points})).collect::<Vec<_>>()})
    }).collect()
}
fn prepare_waves(bars:&[RuleCandle],now:usize,points:&[super::super::kernel::KernelPoint],state:&mut TradingDayState,config:&TradingDayConfig)->Result<(),String>{
    let mut seeds=vec![];let mut candidates=vec![];let measurements=state.target_lifecycle.current_measurements();
    for event in state.events.iter().filter(|e|e.get("event").and_then(Value::as_str)==Some("n_completed")&&e.get("target_eligible").and_then(Value::as_bool)==Some(true)){
        let(Some(identity),Some(origin),Some(attack),Some(known),Some(one_p),Some(two_t),Some(defense),Some(box_anchor))=(event.get("n_id").and_then(Value::as_str),qualification::index(event,"origin"),
            qualification::index(event,"bar_index"),qualification::index(event,"known_at"),qualification::price(event,"one_p"),qualification::price(event,"two_t"),qualification::price(event,"defense"),qualification::price(event,"box_anchor"))else{continue;};
        if known>now||attack>known{continue;}let window=qualification::index(event,"window_start").unwrap_or(known).max(known);
        let invalidated=state.events.iter().filter(|e|matches!(e.get("event").and_then(Value::as_str),Some("n_invalidated"|"n_target_source_retired"))
            &&e.get("n_id").and_then(Value::as_str)==Some(identity)).filter_map(|e|qualification::index(e,"bar_index").map(|i|i.max(qualification::index(e,"known_at").unwrap_or(i)))).min();
        let retired=state.events.iter().filter(|e|e.get("event").and_then(Value::as_str)==Some("n_target_source_retired")&&e.get("n_id").and_then(Value::as_str)==Some(identity))
            .filter_map(|e|qualification::index(e,"bar_index")).min();
        state.waves.source_windows.insert(identity.into(),window);
        seeds.push(waves::AWaveSeed{source_id:identity.into(),origin_index:origin,attack_index:attack,known_at:known,one_p,two_t,defense,confirm_before:invalidated});
        let prepared=state.prepared.iter().find(|n|n.identity==identity);
        let history=if let Some(prepared)=prepared{Some(prepared.regime.clone())}else if let Some(m)=measurements.iter().find(|m|targets::trading_n_identity(m).as_deref()==Some(identity)){
            Some(regime::observe_prepared_regime(bars,&m.setup,&RegimePolicy{shadow_policy:Some(ShadowPolicy{minimum_range_fraction:config.shadow_fraction}),
                wave_boundary:WaveBoundary::NOrigin,local_resistance_failure:true},Some(m.source.known_at),&m.observation)?)}else{None};
        let Some(squeeze)=history.as_ref().and_then(|r|r.frames.iter().find(|f|matches!(f.regime,Some(MarketRegime::Bull|MarketRegime::StrongBull)))).map(|f|f.bar_index)else{continue;};
        candidates.push(waves::WaveCandidate{source_id:identity.into(),setup:waves::WaveProjectionSetup{origin_index:origin,attack_index:attack,squeeze_index:squeeze,
            origin:bars[origin].low,box_anchor,two_t,defense},known_at:known,window_start:window,retired_at:retired,
            source_fields:json!({"source_path":event.get("source_path"),"qualification_window_start":event.get("qualification_window_start"),"window_start":window}),require_a_confirmation:true});
    }
    let turns=state.declines.iter().map(|(&index,&known_at)|waves::AWaveTurn{index,known_at}).collect::<Vec<_>>();
    state.wave_output=waves::advance_daily_waves(bars,now,&seeds,&turns,&candidates,points,&mut state.waves)?;
    state.events.extend(state.wave_output.events.clone());
    for(identity,event)in &state.wave_output.entry_projections{state.events.push(json!({"event":"n_entry_projection","bar_index":now,"n_id":identity,"projection":event}));}
    Ok(())
}
fn build_candidate_proofs(bars:&[RuleCandle],now:usize,state:&TradingDayState,config:&TradingDayConfig)->Result<(Vec<arbiter::CandidateProof>,Vec<Value>),String>{
    let current=state.contexts.get(&now).map(Vec::as_slice).unwrap_or(&[]);let before=now.checked_sub(1).and_then(|i|state.contexts.get(&i)).map(Vec::as_slice).unwrap_or(&[]);
    let resistance=hierarchy::MultilevelResistanceIndex::from_events(&state.events);let whole=config.buy_point_definition=="whole_flip_wave_v3";
    let inverse=arbiter::inverse_episodes(&state.prepared);let mut proofs=vec![];let mut events=vec![];
    for n in state.prepared.iter().filter(|n|n.measurement.setup.direction==super::Direction::Up){
        if !state.events.iter().any(|e|e.get("event").and_then(Value::as_str)==Some("n_completed")&&e.get("n_id").and_then(Value::as_str)==Some(n.identity.as_str())){continue;}
        let m=&n.measurement;let attack=n.attack();let c=m.observation.completion.as_ref().ok_or("N completion missing")?;
        let anchors=m.observation.anchors.as_ref().ok_or("N anchors missing")?;
        let wave=state.wave_output.proofs.get(&n.identity).cloned();
        let consolidation=state.events.iter().rev().find(|e|qualification::index(e,"bar_index")==Some(now)
            &&e.get("confirmation_rule").and_then(Value::as_str)==Some("positive_n_consolidation_gap_after_qualified_b")
            &&e.get("confirmation_evidence").is_some_and(|p|qualification::index(p,"attack")==Some(attack)))
            .and_then(|_|super::squeeze::consolidation_gap(bars,attack,now,c.defense));
        let current_frame=n.regime.frames.iter().find(|f|f.bar_index==now);let frozen_wave_frame=if wave.is_some(){n.regime.frames.iter().find(|f|matches!(f.regime,Some(MarketRegime::Bull|MarketRegime::StrongBull)))}else{None};
        let Some(mut frame)=current_frame.or(frozen_wave_frame).cloned()else{continue;};
        let invalidated=whole&&inverse.iter().any(|e|e.known_at<=now&&e.attack>attack);
        let reconfirmation=if whole&&(invalidated||!matches!(frame.regime,Some(MarketRegime::Bull|MarketRegime::StrongBull))){state.prepared.iter().find(|fresh|fresh.attack()==now&&fresh.measurement.setup.direction==super::Direction::Up)
            .and_then(|fresh|super::squeeze::n_record_reconfirmation(bars,attack,c.defense,now,fresh.measurement.setup.origin.index,fresh.measurement.setup.neckline.index,fresh.measurement.setup.pullback.index,fresh.known_at()))}else{None};
        if invalidated&&reconfirmation.is_none()&&wave.is_none(){continue;}
        let reversal=if whole&&frame.bar_index==now{regime::volume_reversal_squeeze(bars,attack,now,anchors.origin,c.defense,&frame,0).map(|proof|crate::indexed_json::to_value(proof).map_err(|e|e.to_string())).transpose()?}else{None};
        if reconfirmation.is_some()||reversal.is_some(){frame.regime=Some(MarketRegime::Bull);frame.phase="confirmed_on_this_bar".into();frame.close_continuation=true;frame.last_confirmed_regime=Some(MarketRegime::Bull);frame.last_confirmed_index=Some(now);}
        let candidate=hierarchy::HierarchyCandidate::from(n);
        let nested=if whole&&frame.bar_index==now&&frame.first_defense_breach_index.is_none(){hierarchy::nested_alternation_breakout(bars,before,current,now,m.setup.origin.index,m.setup.pullback.index)?}else{None};
        let multilevel=if whole&&frame.bar_index==now{hierarchy::multilevel_squeeze(bars,&candidate,&frame,&resistance)}else{None};
        if nested.is_some()||multilevel.is_some()&&frame.regime.is_none(){frame.regime=Some(MarketRegime::Bull);frame.phase="confirmed_on_this_bar".into();frame.close_continuation=true;frame.last_confirmed_regime=Some(MarketRegime::Bull);frame.last_confirmed_index=Some(now);}
        let resumed=config.squeeze_pullback_entries&&frame.bar_index==now&&regime::observe_squeeze_resumption(bars,now,&frame,0,attack,c.defense)?.is_some();
        if config.regime_filter&&frame.regime.is_none()&&!resumed&&wave.is_none()&&consolidation.is_none(){continue;}
        if !config.regime_filter&&attack!=now{continue;}
        let at_attack=state.contexts.get(&attack).map(Vec::as_slice).unwrap_or(&[]);
        let selected=if let Some(proof)=&nested{hierarchy::EntrySelection{entry:Some(proof.clone()),reason:String::new()}}
            else if config.entry_policy=="hierarchical_two_buy_points"&&whole{let mut options=hierarchy::WaveEntryOptions::from_config(config);options.allow_confirming_n=true;
                options.allow_same_bar_pullback=m.setup.allow_outside_close&&m.setup.pullback.index==attack;
                let selected=hierarchy::select_wave_entry(current,at_attack,bars,&candidate,now,&options)?;
                if selected.entry.is_none(){if let Some(proof)=&multilevel{hierarchy::EntrySelection{entry:Some(proof.clone()),reason:String::new()}}else{selected}}else{selected}}
            else if config.entry_policy=="hierarchical_two_buy_points"{hierarchy::select_entry(current,at_attack,&candidate,now,config.mature_shallow_ratio,(config.buy_interval_level>0).then_some(config.buy_interval_level))?}
            else if config.entry_policy=="transitioned_squeeze"{let permission=state.permission_at(now);let attack_permission=state.permission_at(attack);
                let reason=match permission{
                    None=>"bullish_transition_not_ready",Some(p)if !p.permits(attack)=>"n_attack_not_after_bullish_confirmation",
                    Some(p)if !attack_permission.is_some_and(|old|old.context_index==p.context_index&&old.key_source_index==p.key_source_index
                        &&old.key_price==p.key_price&&old.flip_index==p.flip_index&&old.alternation_index==p.alternation_index&&old.bullish_index==p.bullish_index)=>"attack_not_in_same_bullish_episode",
                    _=>"",
                };
                hierarchy::EntrySelection{entry:permission.map(|p|crate::indexed_json::to_value(p).map_err(|e|e.to_string())).transpose()?,reason:reason.into()}}
            else{hierarchy::EntrySelection{entry:None,reason:String::new()}};
        for(kind,proof)in [("n_record_reconfirmed",reconfirmation.as_ref()),("volume_reversal_squeeze_confirmed",reversal.as_ref()),("nested_alternation_breakout_confirmed",nested.as_ref()),("multilevel_squeeze_confirmed",multilevel.as_ref())]{
            if let Some(proof)=proof{events.push(json!({"event":kind,"bar_index":now,"attack":attack,"proof":proof}));}}
        proofs.push(arbiter::CandidateProof{identity:n.identity.clone(),frame,hierarchy:selected.entry,hierarchy_rejection:(!selected.reason.is_empty()).then_some(selected.reason),
            multilevel,nested,wave,consolidation,reconfirmation,reversal,resumed});
    }Ok((proofs,events))
}
fn strategy_exits(bars:&[RuleCandle],now:usize,polyline:&KernelFrame,eligibility:&TradingEligibility,state:&TradingDayState,
    risks:&entry_risk::EntryRisks,config:&TradingDayConfig)->Result<Vec<super::RuleDecision>,String>{
    let mut out=vec![];let whole=config.buy_point_definition=="whole_flip_wave_v3";
    if whole{out.extend(super::exits::observe_limit_up_distribution_exit(bars,now));}
    out.extend(risks.target.iter().filter(|d|d.fraction==Decimal::ONE).cloned());
    if whole{
        out.extend(super::super::intraday::mother_child_inverse_n_break(bars,now));
        let equal=risk::observe_c_equal_near_risk(bars,now,&state.events,Decimal::new(5,1));
        out.extend(equal.iter().filter(|d|d.reason=="wave_c_equal_near_volume_clear").cloned());
        let five=risk::observe_five_top_risks(bars,now,&state.events);
        let chosen=five.iter().find(|d|d.reason=="wave_five_top_child_volume_clear")
            .or_else(||five.iter().find(|d|d.reason=="wave_five_top_gap_volume_clear"))
            .or_else(||{if risks.target.as_ref().is_some_and(|d|d.fraction==Decimal::ONE)||equal.as_ref().is_some_and(|d|d.fraction>=Decimal::ONE){None}
                else{five.iter().find(|d|matches!(d.reason.as_str(),"wave_five_top_body_upper_shadow_clear"|"wave_five_top_gap_upper_shadow_clear"))}});
        out.extend(chosen.cloned());
    }
    if polyline.blocked_at==Some(now){out.push(super::RuleDecision::new("strict_structure_unresolved",now,Decimal::ONE).with("blocked_index",now));}
    for n in state.prepared.iter().filter(|n|n.measurement.setup.direction==super::Direction::Down&&n.attack()==now&&n.known_at()<=now){
        out.push(super::RuleDecision::new("inverse_n_risk_exit",now,Decimal::ONE).with("n_id",&n.identity).with("attack",now).with("known_at",n.known_at()));
    }
    for event in state.events.iter().filter(|e|e.get("event").and_then(Value::as_str)==Some("turn_confirmed")&&qualification::index(e,"bar_index")==Some(now)
        &&e.get("direction").and_then(Value::as_str)==Some("down")){out.push(super::RuleDecision::new("negative_turn_risk_exit",now,Decimal::ONE).with("turn",event));}
    for window in eligibility.intervals.iter().filter(|w|config.buy_interval_level>0&&w.end_index==Some(now)){
        if let Some(reason)=&window.end_reason{out.push(super::RuleDecision::new(reason,now,Decimal::ONE).with("qualification",window));}
    }
    if now>0&&polyline.blocked_at!=Some(now){if let Some(previous)=&state.previous_polyline{
        let epoch=polyline.candidate.as_ref().or_else(||polyline.points.last()).map_or(0,|p|p.path);let start=epoch.max(now.saturating_sub(config.structure_window));
        let points:Vec<_>=previous.points.iter().filter(|p|p.is_closed()&&p.index>=start&&p.known_index<now).collect();
        let high=points.iter().filter(|p|p.kind==PointKind::H).max_by(|a,b|a.value.total_cmp(&b.value).then_with(||b.order().cmp(&a.order())));
        if let Some(high)=high{if let Some(low)=points.iter().rev().find(|p|p.kind==PointKind::L&&p.order()<high.order()){
            let price=low.value.to_string().parse::<Decimal>().map_err(|e|e.to_string())?;
            if bars[now].close<price&&price<=bars[now-1].close{out.push(super::RuleDecision::new("last_rise_low_close_broken",now,Decimal::ONE)
                .with("last_rise_low",low).with("window_high",high).with("previous_close",bars[now-1].close).with("market_close",bars[now].close));}
        }}
    }}
    let mut seen=BTreeSet::new();out.retain(|d|seen.insert(d.reason.clone()));Ok(out)
}
fn supplemental_entry(bars:&[RuleCandle],frames:&[KernelFrame],now:usize,eligibility:&TradingEligibility,state:&mut TradingDayState,
    secondary:&secondary::SecondaryDayEvaluation,shallow:Option<&Value>,risks:&arbiter::EntryRisks,config:&TradingDayConfig,fully_executable:bool)->Result<Option<EntryDecision>,String>{
    if config.buy_interval_level!=0&&!eligibility.active()||now==0{return Ok(None);}
    if config.buy_point_definition=="whole_flip_wave_v3"&&entries::squeeze_buy_rejection(&bars[now],Some(&bars[now-1]),None).is_some(){return Ok(None);}
    let inverse=arbiter::inverse_episodes(&state.prepared);
    let(window_start,window_path)=if config.buy_interval_level==0{let epoch=frames.iter().find(|f|f.level==0).and_then(|f|f.candidate.as_ref().or_else(||f.points.last())).map_or(0,|p|p.path);(epoch,format!("unrestricted-{epoch}"))}
        else{let window=eligibility.active_intervals().into_iter().max_by_key(|w|w.start_index).ok_or("entry window missing")?;(window.start_index,window.source_path.clone())};
    for(channel,proof)in[("combined_a_pullback_breakout",secondary.combined.as_ref()),("secondary_deep_pullback_reclaim",secondary.pullback.as_ref()),
        ("secondary_resistance_reclaim",secondary.reclaim.as_ref()),("shallow_base_breakout",shallow)]{
        let Some(source)=proof else{continue;};let mut proof=source.clone();
        let pullback_key=qualification::index(&proof,"origin_index").zip(qualification::index(&proof,"flip_high_index"));
        let reclaim_key=qualification::index(&proof,"secondary_attack_index");
        if channel=="secondary_deep_pullback_reclaim"&&pullback_key.is_some_and(|id|state.emitted_pullbacks.contains(&id))
            ||channel=="secondary_resistance_reclaim"&&reclaim_key.is_some_and(|id|state.emitted_reclaims.contains(&id)){continue;}
        let mut rejection=risks.five_top.as_ref().map(|d|d.reason.clone());
        if channel!="shallow_base_breakout"{
            rejection=risks.target.as_ref().map(|d|d.reason.clone()).or(rejection);
            if channel=="secondary_resistance_reclaim"&&config.buy_point_definition=="whole_flip_wave_v3"&&state.arbiter.round.emitted_at.is_some(){rejection=Some("first_n_squeeze_already_emitted".into());}
            if let Some(pressure)=&secondary.pressure{if pressure.get("secondary_resistance_resolved").and_then(Value::as_bool)!=Some(true)
                &&(channel=="combined_a_pullback_breakout"||qualification::price(pressure,"secondary_resistance_high").is_some_and(|p|bars[now].close<=p)){
                rejection=rejection.or(Some("secondary_breakout_resistance_unresolved".into()));}}
            let gap=match channel{"combined_a_pullback_breakout"=>proof.get("combined_a_breakout_type"),"secondary_deep_pullback_reclaim"=>proof.get("reclaim_type"),_=>proof.get("secondary_reclaim_type")}.and_then(Value::as_str)==Some("gap");
            rejection=rejection.or_else(||super::squeeze::inverse_reentry_rejection(bars,now,now,&inverse,gap).map(|d|d.reason));
        }
        if let Some(reason)=rejection{state.events.push(json!({"event":"entry_rejected","bar_index":now,"candidate_channel":channel,"reason":reason}));continue;}
        let stop=qualification::price(&proof,"stop").ok_or("supplemental entry stop missing")?;
        let mut target=qualification::price(&proof,"target");
        if channel=="secondary_resistance_reclaim"{
            let peak=qualification::price(&proof,"secondary_observed_peak").ok_or("reclaim peak missing")?;let mut choices=vec![];
            if let Some(frame)=frames.iter().find(|f|f.level==2){for point in frame.points.iter().filter(|p|p.kind==PointKind::H&&p.is_closed()&&p.known_index<now){
                let value=point.value.to_string().parse::<Decimal>().map_err(|e|e.to_string())?;if value>peak{choices.push(value);}
            }}
            if let Some(live)=arbiter::live_target_event(&state.events,now){if let Some(known)=qualification::index(live,"known_at"){
                for field in ["equal_wave","one_p","two_t"]{if let Some(price)=qualification::price(live,field){if price>bars[now].high&&!bars[known..=now].iter().enumerate().any(|(o,b)|if o==0{b.close>=price}else{b.high>=price}){choices.push(price);}}}
                if let Some(projection)=live.get("n_id").and_then(Value::as_str).and_then(|id|state.wave_output.entry_projections.get(id)){
                    if let Some(target)=projection.target.filter(|p|*p>bars[now].high){choices.push(target);}}
            }}target=choices.into_iter().min();proof["target_policy"]=json!("nearest_unhit_known_secondary_or_n_target_or_confirmed_projection");
        }
        let Some(target)=target.filter(|p|stop<bars[now].close&&bars[now].close<*p)else{state.events.push(json!({"event":"entry_rejected","bar_index":now,"candidate_channel":channel,"reason":"no_live_structural_risk_reward"}));continue;};
        let rr=(target-bars[now].close)/(bars[now].close-stop);
        if channel!="combined_a_pullback_breakout"&&config.preflight_reward_risk&&rr<config.minimum_reward_risk{state.events.push(json!({"event":"entry_preflight_rejected","bar_index":now,"candidate_channel":channel,"reason":"insufficient_close_gross_reward_risk"}));continue;}
        proof["target"]=json!(target);proof["gross_reward_risk"]=json!(rr);
        if fully_executable{if channel=="secondary_deep_pullback_reclaim"{if let Some(id)=pullback_key{state.emitted_pullbacks.insert(id);}}
            if channel=="secondary_resistance_reclaim"{if let Some(id)=reclaim_key{state.emitted_reclaims.insert(id);}}}
        proof["trigger_index"]=json!(now);proof["trigger_timestamp"]=json!(bars[now].timestamp);
        proof["rvol"]=proof.get("breakout_volume_multiple").cloned().unwrap_or(Value::Null);
        proof["regime"]=json!(match channel{"combined_a_pullback_breakout"=>"组合A回调放量突破","secondary_deep_pullback_reclaim"=>"二级深回调放量收复",
            "secondary_resistance_reclaim"=>"二级突破抵抗放量收复",_=>"待选交替横盘突破"});
        return Ok(Some(EntryDecision{bar_index:now,reason:format!("system_{channel}"),price:bars[now].close,stop,target:Some(target),
            qualification_index:window_start,trend_level:config.buy_interval_level,source_path:window_path.clone(),execution_model:"next_open".into(),fully_executable,evidence:proof}));
    }Ok(None)
}
pub fn prepare_trading_day(bars:&[RuleCandle],frames:&[KernelFrame],asof:usize,eligibilities:&mut[TradingEligibility],
    timeframe:&str,state:&mut TradingDayState,config:&TradingDayConfig)->Result<TradingDayEvaluation,String>{
    if asof>=bars.len()||state.last.map_or(0,|i|i+1)!=asof||config.buy_interval_level>3||config.structure_window==0||config.pattern_ttl==0{return Err("trading day requires consecutive dates and valid policy".into());}
    config.validate()?;
    if let Some(prior)=&state.config{if prior!=config{return Err("trading policy changed inside an existing calculation identity".into());}}
    else{state.config=Some(config.clone());state.structural.ttl=config.pattern_ttl;state.structural_sources.folded_window=Some(config.structure_window);
        state.trading_sources.folded_window=(config.buy_point_definition=="whole_flip_wave_v3"&&config.pivot_mode=="lecture_causal").then_some(config.structure_window);}
    let polyline=frames.iter().find(|f|f.level==0).ok_or("day requires shared level-zero frame")?;
    if timeframe!="1d"{
        let event=json!({"event":"trading_not_applicable","bar_index":asof,"timeframe":timeframe,"reason":"daily_strategy_requires_daily_bars"});
        state.events.push(event.clone());state.diagnostics.increment("rows");state.last=Some(asof);state.previous_polyline=Some(polyline.clone());
        let evaluations=frames.iter().map(|_|FrameTradeEvaluation{alternation_ended:false,in_qualification_range:false,trade_point:false,
            unavailable_rules:vec![],n_observations:vec![],events:vec![],entry_rejections:vec!["daily_trading_not_applicable".into()],exit_decisions:vec![]}).collect();
        return Ok(TradingDayEvaluation{frames:evaluations,entry_candidates:vec![],events:vec![event],unavailable_rules:vec![],counts:state.counts().clone(),trading_applicable:false});
    }
    let event_start=state.events.len();let mut evaluations=vec![];
    for frame in frames{
        let eligibility=eligibilities.get_mut(usize::from(frame.level)).ok_or("eligibility state missing for frame")?;
        let result=evaluate_frame_with_config(bars,frame,asof,eligibility,timeframe,polyline,config)?;
        state.events.extend(result.events.iter().filter(|e|e.get("event").and_then(Value::as_str)!=Some("long_signal")).cloned());evaluations.push(result);
    }
    let eligibility=eligibilities.get(usize::from(config.buy_interval_level)).ok_or("selected eligibility unavailable")?;
    if config.buy_interval_level>0{
        for window in &eligibility.intervals{
            if window.start_index==asof{state.events.push(json!({"event":"trading_interval_opened","bar_index":asof,
                "trend_level":window.trend_level,"source_path":window.source_path,"flip_index":window.start_flip_index,
                "start_low_index":window.start_low_index,"start_low_price":window.start_low_price,"qualification_index":window.start_index,
                "confirmation_gap_index":window.confirmation_gap.as_ref().map(|g|g.index),"confirmation_gap_lower":window.confirmation_gap.as_ref().map(|g|g.lower),
                "confirmation_gap_upper":window.confirmation_gap.as_ref().map(|g|g.upper),"confirmation_gap_kind":window.confirmation_gap.as_ref().map(|g|g.kind.as_str())}));}
            if window.end_index==Some(asof){state.events.push(json!({"event":"trading_interval_closed","bar_index":asof,
                "trend_level":window.trend_level,"source_path":window.source_path,"start_index":window.start_index,
                "flip_index":window.end_flip_index,"reason":window.end_reason,"reversal_low_index":window.end_key_index,"reversal_low_price":window.end_key_price,
                "confirmation_gap_index":window.confirmation_gap.as_ref().map(|g|g.index),"confirmation_gap_lower":window.confirmation_gap.as_ref().map(|g|g.lower),
                "confirmation_gap_upper":window.confirmation_gap.as_ref().map(|g|g.upper),"confirmation_gap_kind":window.confirmation_gap.as_ref().map(|g|g.kind.as_str()),
                "display_end_inclusive":matches!(window.end_reason.as_deref(),Some("reversal_low_broken"|"confirmation_gap_filled"|"source_bear_trend_confirmed"))}));}
        }
    }
    if config.entry_policy=="transitioned_squeeze"{let permission=state.permissions.advance(bars,polyline,asof,config.structure_window,(config.pivot_mode=="lecture_causal").then_some(config.max_counter_ratio))?;
        state.events.extend(permission.events.clone());state.permission_days.insert(asof,permission);}
    let windows:Vec<_>=eligibility.active_intervals().into_iter().cloned().collect();
    let mut scopes:BTreeSet<_>=windows.iter().map(|w|format!("{}:{}",w.source_path,w.start_index)).collect();
    let epoch=polyline.candidate.as_ref().or_else(||polyline.points.last()).map_or(0,|p|p.path);
    let points:Vec<_>=polyline.points.iter().filter(|p|p.path==epoch&&p.is_closed()&&p.known_index<=asof).cloned().collect();
    let unrestricted=config.buy_interval_level==0;let buy_allowed=unrestricted||eligibility.active();
    if unrestricted{scopes.clear();scopes.insert(format!("unrestricted-{epoch}:{epoch}"));}
    let structural_sources=if unrestricted{state.structural_sources.advance_unrestricted(asof,&points,epoch)?}else{state.structural_sources.advance(asof,&points,&windows)?};
    let trading_sources=if unrestricted{state.trading_sources.advance_unrestricted(asof,&points,epoch)?}else{state.trading_sources.advance(asof,&points,&windows)?};
    let declines=decline_arrivals(state,frames,asof);
    let interrupted=polyline.blocked_at==Some(asof);
    let structural_events=state.structural.advance(bars,asof,&structural_sources,&scopes,interrupted,&declines,epoch)?;
    state.events.extend(structural_events);
    state.events.extend(state.targets.advance_with_policy(bars,asof,&trading_sources,&scopes,interrupted,config.structure_window,config.pattern_ttl,
        &config.pivot_mode,config.buy_point_definition=="whole_flip_wave_v3")?);
    let(prepared,admitted,mut preparation_events)=candidates::prepare_candidates(bars,&state.targets.measurements,config,&mut state.preparation_events)?;
    state.prepared=prepared;
    let completed_down=state.prepared.iter().filter(|n|n.measurement.setup.direction==super::Direction::Down).count();
    if config.buy_point_definition=="whole_flip_wave_v3"{
        state.events.extend(state.target_lifecycle.advance(bars,asof,&admitted,&scopes,&declines,&state.declines)?);
        let canonical:BTreeSet<_>=state.target_lifecycle.events.iter().filter(|e|e.get("event").and_then(Value::as_str)==Some("n_completed")
            &&e.get("reformed_from").is_none_or(Value::is_null)).filter_map(|e|e.get("n_id").and_then(Value::as_str).map(str::to_owned)).collect();
        let mut negative_attacks=BTreeSet::new();
        state.prepared.retain(|n|if n.measurement.setup.direction==super::Direction::Up{canonical.contains(&n.identity)}else{negative_attacks.insert(n.attack())});
        let retained:BTreeSet<_>=state.prepared.iter().map(|n|n.identity.as_str()).collect();
        preparation_events.retain(|e|e.get("event").and_then(Value::as_str)!=Some("n_completed")
            ||e.get("n_id").and_then(Value::as_str).is_some_and(|id|retained.contains(id)));
    }
    state.events.extend(preparation_events);
    for candidate in &mut state.prepared{if let Some(derived)=state.derived_regimes.get(&candidate.identity){for frame in &mut candidate.regime.frames{if let Some(saved)=derived.get(&frame.bar_index){*frame=saved.clone();}}}}
    let levels=one_p_levels(frames,asof);let one_p_events=state.one_p.advance(bars,asof,&levels,&state.structural.events)?;state.events.extend(one_p_events);
    let context=hierarchy::contexts_from_eligibilities(eligibilities,&state.events,bars,asof)?;
    state.events.extend(context.events);state.contexts.insert(asof,context.contexts);
    for frame in frames.iter().filter(|f|matches!(f.level,2|3)){if let Some(event)=state.resistance.advance(frame,asof)?{state.events.push(event);}}
    state.wave_output=Default::default();let(mut proofs,proof_events)=build_candidate_proofs(bars,asof,state,config)?;state.events.extend(proof_events);
    for proof in &proofs{if proof.frame.bar_index==asof&&(proof.reconfirmation.is_some()||proof.reversal.is_some()||proof.nested.is_some()||proof.multilevel.is_some()){
        state.derived_regimes.entry(proof.identity.clone()).or_default().insert(asof,proof.frame.clone());
        if let Some(n)=state.prepared.iter_mut().find(|n|n.identity==proof.identity){if let Some(frame)=n.regime.frames.iter_mut().find(|f|f.bar_index==asof){*frame=proof.frame.clone();}}
    }}
    if config.buy_point_definition=="whole_flip_wave_v3"{
        let inverse=arbiter::inverse_episodes(&state.prepared);
        for candidate in state.prepared.iter_mut().filter(|n|n.measurement.setup.direction==super::Direction::Up){
            let attack=candidate.attack();
            if let Some(failure)=inverse.iter().filter(|n|n.attack>attack&&n.known_at<=asof).min_by_key(|n|(n.known_at,n.attack)){
                let derived=state.derived_regimes.get(&candidate.identity);
                candidate.regime.frames.retain(|frame|frame.bar_index<failure.known_at||derived.is_some_and(|history|history.contains_key(&frame.bar_index)));
                if state.preparation_events.insert(format!("squeeze-invalidated:{}",candidate.identity)){
                    state.events.push(json!({"event":"n_squeeze_invalidated","bar_index":failure.known_at,"known_at":failure.known_at,
                        "n_id":candidate.identity,"attack":attack,"inverse_attack":failure.attack,"reason":if candidate.n_level>=1{
                            "later_inverse_n_ends_local_bounce_larger_defense_still_observed"}else{"later_confirmed_inverse_n_requires_new_positive_n"}}));
                }
            }
        }
    }
    state.events.extend(candidates::observe_candidate_regimes(bars,&state.prepared,config,&mut state.preparation_events)?);
    state.events.extend(super::turns::washout_reentry_events(bars,asof,&state.prepared,config,&mut state.seen_washouts)?);
    let minor_available=state.minor_data_available();
    state.events.extend(state.turns.advance(bars,asof,polyline,&state.prepared,config,minor_available.then_some(state.minor_points.as_slice()))?);
    if config.buy_point_definition=="whole_flip_wave_v3"{prepare_waves(bars,asof,&points,state,config)?;}
    for(identity,wave)in &state.wave_output.proofs{
        if let Some(proof)=proofs.iter_mut().find(|p|&p.identity==identity){proof.wave=Some(wave.clone());continue;}
        let Some(n)=state.prepared.iter().find(|n|&n.identity==identity)else{continue;};
        let Some(frame)=n.regime.frames.iter().find(|f|matches!(f.regime,Some(MarketRegime::Bull|MarketRegime::StrongBull))).cloned()else{continue;};
        let mut options=hierarchy::WaveEntryOptions::from_config(config);options.allow_confirming_n=true;options.allow_same_bar_pullback=n.measurement.setup.allow_outside_close&&n.measurement.setup.pullback.index==n.attack();
        let selected=hierarchy::select_wave_entry(state.contexts.get(&asof).map(Vec::as_slice).unwrap_or(&[]),state.contexts.get(&n.attack()).map(Vec::as_slice).unwrap_or(&[]),bars,&hierarchy::HierarchyCandidate::from(n),asof,&options)?;
        proofs.push(arbiter::CandidateProof{identity:identity.clone(),frame,hierarchy:selected.entry,hierarchy_rejection:(!selected.reason.is_empty()).then_some(selected.reason),
            wave:Some(wave.clone()),multilevel:None,nested:None,consolidation:None,reconfirmation:None,reversal:None,resumed:false});
    }
    let secondary=secondary::evaluate_secondary_day(bars,frames,asof,&state.events,buy_allowed,&mut state.secondary,config)?;state.events.extend(secondary.events.clone());
    let shallow=state.shallow.advance(bars,asof,shallow::shallow_candidate_from_frames(frames,asof),config.minimum_reward_risk,
        buy_allowed&&config.shallow_base_breakout_enabled)?;state.events.extend(shallow.events);
    let entry_risk=entry_risk::evaluate_entry_risks(bars,asof,&state.events,&mut state.entry_risk,config)?;state.events.extend(entry_risk.events.clone());
    state.risk.positive_n_index=arbiter::live_target_event(&state.events,asof).and_then(|e|qualification::index(e,"bar_index"));
    state.risk.projection_events=state.events.iter().filter(|e|e.get("event").and_then(Value::as_str).is_some_and(|kind|kind.starts_with("wave_projection_")||matches!(kind,"wave_c_equal_target"|"wave_ordinary_entry"))).cloned().collect();
    let risk=risk::evaluate_daily_risks(bars,frames,asof,&mut state.risk)?;
    let mut unavailable=risk.unavailable_rules;if config.entry_policy=="hierarchical_two_buy_points"{unavailable.extend(context.unavailable_rules);}
    let mut candidates=vec![];
    let hard_exits=strategy_exits(bars,asof,polyline,eligibility,state,&entry_risk,config)?;
    if !hard_exits.is_empty(){state.events.push(json!({"event":"exit_signal","bar_index":asof,"price":bars[asof].close,"stop":bars[asof].high,
        "reason":hard_exits.iter().map(|d|d.reason.as_str()).collect::<Vec<_>>().join("|"),"decisions":hard_exits,"execution_model":"next_open"}));}
    let whole=config.buy_point_definition=="whole_flip_wave_v3";
    let reject=entries::volume_upper_shadow_rejection(&bars[asof],asof.checked_sub(1).map(|i|&bars[i])).map(str::to_string)
        .or_else(||whole.then(||entries::volume_bearish_entry_rejection(bars,asof)).flatten().map(str::to_string))
        .or_else(||whole.then(||entries::inverse_n_low_entry_risk(bars,asof,&points)).flatten().map(|d|d.reason))
        .or_else(||if whole{eligibility.polyline_gate.restrictions.get(&asof).map(|d|d.reason.clone())}else{None});
    let reject=reject.or(state.gap.advance(bars,asof)?.map(|d|d.reason));
    let reject=reject.or_else(||entry_risk.ten_full.as_ref().map(|d|d.reason.clone()))
        .or_else(||entries::two_t_entry_rejection(bars,asof,&entries::entry_targets_from_events(&state.events,asof,true),bars[asof].high,bars[asof].close,true).map(|d|d.reason))
        .or_else(||if config.buy_point_definition=="whole_flip_wave_v3"{entries::one_p_breakout_entry_rejection(&state.events,asof,bars[asof].high).map(|d|d.reason)}else{None})
        .or_else(||(!hard_exits.is_empty()).then(||"same_day_strategy_exit".into()));
    let entry_risks=arbiter::EntryRisks{secondary:secondary.pressure.clone(),target:entry_risk.target,five_top:entry_risk.five_top,ten_full:entry_risk.ten_full};
    let eligible_proofs=if reject.is_none()&&buy_allowed{proofs.as_slice()}else{&[]};
    let result=arbiter::arbitrate_n_entries(bars,asof,&state.prepared,eligible_proofs,&state.events,&scopes,epoch,config,&entry_risks,&mut state.arbiter,unavailable.is_empty())?;
    state.events.extend(result.events);if let Some(candidate)=result.decision{candidates.push(candidate);}
    if reject.is_none()&&candidates.is_empty(){if let Some(decision)=supplemental_entry(bars,frames,asof,eligibility,state,&secondary,shallow.breakout.as_ref(),&entry_risks,config,unavailable.is_empty())?{candidates.push(decision);}}
    if reject.is_none()&&candidates.is_empty()&&entry_risks.target.is_none()&&entry_risks.five_top.is_none(){
        if let Some(mut decision)=entries::qualified_close_entry(bars,asof,eligibility,&eligibility.polyline_gate,&state.events){
            let proof=crate::indexed_json::to_value(&decision.evidence).map_err(|error|error.to_string())?;
            let price=qualification::price(&proof,"price").ok_or("entry price missing")?;
            let stop=qualification::price(&proof,"stop").ok_or("entry stop missing")?;
            let window=eligibility.active_intervals().into_iter().max_by_key(|w|w.start_index).ok_or("entry window missing")?;
            let candidate=EntryDecision{bar_index:asof,reason:std::mem::take(&mut decision.reason),price,stop,target:None,
                qualification_index:window.start_index,trend_level:config.buy_interval_level,source_path:window.source_path.clone(),execution_model:"same_day_close".into(),fully_executable:unavailable.is_empty(),evidence:proof};
            state.events.push(json!({"event":"entry_candidate","bar_index":asof,"candidate":candidate}));candidates.push(candidate);
        }
    }
    for candidate in &candidates{if candidate.fully_executable{if let Some(wave)=candidate.evidence.get("wave").filter(|v|v.is_object()){
        let attack=qualification::index(&candidate.evidence,"attack").ok_or("wave entry attack missing")?;
        state.events.extend(waves::wave_entry_events(asof,attack,candidate.stop,wave)?);
    }}state.events.push(json!({"event":if candidate.fully_executable{"long_signal"}else{"entry_candidate"},"bar_index":asof,"candidate":candidate}));}
    if let Some(reason)=reject{state.events.push(json!({"event":"entry_rejected","bar_index":asof,"reason":reason}));}
    for evaluation in &mut evaluations{evaluation.exit_decisions=risk.decisions.clone();evaluation.exit_decisions.extend(hard_exits.iter().cloned().map(|d|d.with("risk_family","hard")));
        evaluation.unavailable_rules.retain(|r|!r.starts_with("trading_day:"));
        evaluation.unavailable_rules.extend(unavailable.iter().cloned());}
    state.previous_polyline=Some(polyline.clone());
    state.diagnostics.consume_day(&state.events[event_start..],asof,polyline.blocked_at==Some(asof),minor_available,!scopes.is_empty(),buy_allowed,points.len()<3);
    state.diagnostics.counts.insert("trading_interval_count".into(),if unrestricted{0}else{eligibility.intervals.len()});
    state.diagnostics.counts.insert("completed_up_n".into(),state.events.iter().filter(|e|e.get("event").and_then(Value::as_str)==Some("n_completed")
        &&e.get("direction").and_then(Value::as_str)==Some("up")).count());
    state.diagnostics.counts.insert("completed_down_n".into(),completed_down);
    state.diagnostics.observe_pending(&state.targets.measurements);
    state.last=Some(asof);
    Ok(TradingDayEvaluation{frames:evaluations,entry_candidates:candidates,events:state.events[event_start..].to_vec(),unavailable_rules:unavailable,counts:state.counts().clone(),trading_applicable:true})
}
