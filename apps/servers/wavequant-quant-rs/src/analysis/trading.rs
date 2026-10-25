//! 因果交易规则：结构、资格、买点和退出保持独立的可知时钟。
//!
//! 对应 Python domain/market_structure/n_shape.py 与 domain/strategies。
//! 调用者必须提供同一复权口径、单品种且时间递增的行情。
use rust_decimal::Decimal;
use serde::{Deserialize, Serialize};

pub mod entries;
pub mod exits;
pub mod n_shape;
pub mod qualification;
pub mod confirmation;
pub mod targets;
pub mod one_p;
pub mod day;
pub mod risk;
pub mod reformation;
pub mod target_lifecycle;
pub mod config;
pub mod hierarchy;
pub mod waves;
pub mod secondary;
pub mod turns;
pub mod squeeze;
pub mod candidates;
pub mod arbiter;
pub mod n_alternation;
pub mod single_n;
pub mod entry_risk;
pub mod shallow;
pub mod abc;
pub mod diagnostics;
pub mod legacy_candidates;
pub use n_shape::*;
pub use qualification::TradingEligibility;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RuleCandle {
    pub symbol: String,
    pub timestamp: String,
    pub open: Decimal,
    pub high: Decimal,
    pub low: Decimal,
    pub close: Decimal,
    pub volume: Decimal,
    #[serde(default = "one")]
    pub adjustment_factor: Decimal,
    #[serde(default)]
    pub raw_trading_active: Option<bool>,
    #[serde(default)]
    pub raw_is_st: Option<bool>,
}

fn one() -> Decimal { Decimal::ONE }
pub(crate) fn ratio(n: i64, d: i64) -> Decimal { Decimal::from(n) / Decimal::from(d) }

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Direction { Up, Down }
impl Direction {
    pub fn sign(self) -> Decimal { if self == Self::Up { Decimal::ONE } else { -Decimal::ONE } }
    pub fn extreme(self, bar: &RuleCandle) -> Decimal { if self == Self::Up { bar.high } else { bar.low } }
    pub fn adverse(self, bar: &RuleCandle) -> Decimal { if self == Self::Up { bar.low } else { bar.high } }
}

pub fn validate_bars(bars: &[RuleCandle]) -> Result<(), String> {
    for (index, bar) in bars.iter().enumerate() {
        if bar.symbol.trim().is_empty() || bar.timestamp.trim().is_empty()
            || bar.low <= Decimal::ZERO || bar.low > bar.open.min(bar.close)
            || bar.high < bar.open.max(bar.close) || bar.volume < Decimal::ZERO
            || bar.adjustment_factor <= Decimal::ZERO {
            return Err(format!("invalid OHLCV at index {index}"));
        }
        if index > 0 && (bars[index - 1].symbol != bar.symbol || bars[index - 1].timestamp >= bar.timestamp) {
            return Err("rules require ordered single-symbol bars".into());
        }
    }
    Ok(())
}

pub fn teaching_inside(mother: &RuleCandle, child: &RuleCandle) -> bool {
    child.high <= mother.high && child.low >= mother.low
        && (child.high < mother.high || child.low > mother.low)
}
pub fn teaching_outside(child: &RuleCandle, mother: &RuleCandle) -> bool {
    mother.high >= child.high && mother.low <= child.low
        && (mother.high > child.high || mother.low < child.low)
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RuleDecision {
    pub reason: String,
    pub bar_index: usize,
    pub fraction: Decimal,
    pub cumulative: bool,
    pub evidence: std::collections::BTreeMap<String, crate::indexed_json::Value>,
}
impl RuleDecision {
    pub fn new(reason: &str, bar_index: usize, fraction: Decimal) -> Self {
        Self { reason: reason.into(), bar_index, fraction, cumulative: false, evidence: Default::default() }
    }
    pub fn with(mut self, key: &str, value: impl Serialize) -> Self {
        // 所有传入值为本模块有限结构；不丢弃证据序列化错误。
        match crate::indexed_json::to_value(value) {
            Ok(value) => { self.evidence.insert(key.into(), value); }
            Err(error) => { self.evidence.insert(key.into(), crate::indexed_json::Value::String(format!("serialization_error: {error}"))); }
        }
        self
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FrameTradeEvaluation {
    pub alternation_ended: bool,
    pub in_qualification_range: bool,
    pub trade_point: bool,
    pub unavailable_rules: Vec<String>,
    pub n_observations: Vec<NObservation>,
    pub events: Vec<crate::indexed_json::Value>,
    pub entry_rejections: Vec<String>,
    pub exit_decisions: Vec<RuleDecision>,
}

/// 统一结构结果的交易消费者；不重画结构，也不把结构交替升级为正式确认。
pub fn evaluate_frame(bars: &[RuleCandle], frame: &super::kernel::KernelFrame, asof: usize,
    eligibility: &mut TradingEligibility) -> Result<FrameTradeEvaluation, String> {
    evaluate_frame_with_timeframe(bars,frame,asof,eligibility,"1d")
}

pub fn evaluate_frame_with_context(bars:&[RuleCandle],frame:&super::kernel::KernelFrame,asof:usize,
    eligibility:&mut TradingEligibility,timeframe:&str,polyline_frame:&super::kernel::KernelFrame)->Result<FrameTradeEvaluation,String> {
    evaluate_frame_with_config(bars,frame,asof,eligibility,timeframe,polyline_frame,&config::TradingDayConfig::lecture())
}
pub fn evaluate_frame_with_config(bars:&[RuleCandle],frame:&super::kernel::KernelFrame,asof:usize,
    eligibility:&mut TradingEligibility,timeframe:&str,polyline_frame:&super::kernel::KernelFrame,config:&config::TradingDayConfig)->Result<FrameTradeEvaluation,String> {
    let recovery_events=if timeframe=="1d"{eligibility.advance_same_level_recovery(frame,bars,asof)?}else{vec![]};
    let mut enriched=frame.clone();
    if timeframe=="1d"{
        let pair=if polyline_frame.structure_direction==Some(super::kernel::Direction::Down){polyline_frame.key.as_ref().zip(polyline_frame.anchor.as_ref())}else{None};
        let mut flips=std::collections::BTreeMap::new();
        for event in &frame.events{let Some(proof)=event.evidence.get("lifecycle_event")else{continue;};
            if event.known_index>asof{continue;}let Some(path)=proof.get("source_path").and_then(crate::indexed_json::Value::as_str)else{continue;};
            if proof.get("state").and_then(crate::indexed_json::Value::as_str)==Some("空翻多"){flips.insert(path,event);}
            if matches!(proof.get("event").and_then(crate::indexed_json::Value::as_str),Some("trend_lifecycle_invalidated"|"trading_source_bear_confirmed"))||proof.get("direction").and_then(crate::indexed_json::Value::as_str)==Some("down"){flips.remove(path);}
        }
        for(path,source)in flips{
            let original=&source.evidence["lifecycle_event"];
            let active=eligibility.active_intervals().into_iter().find(|w|w.source_path==path);
            if active.is_some_and(|w|w.confirmation_gap.is_none()||w.gap_replaced_index.is_some()){continue;}
            let gap=active.and_then(|w|w.confirmation_gap.as_ref()).map(|g|g.index);
            let after=eligibility.intervals.iter().filter(|w|w.source_path==path).filter_map(|w|w.end_index).max();
            let(Some(origin_index),Some(high_index))=(qualification::index(original,"origin_index"),qualification::index(original,"flip_index"))else{continue;};
            let origin=source.origin.as_ref().filter(|p|p.index==origin_index).or_else(||frame.points.iter().find(|p|p.index==origin_index&&p.kind==super::kernel::PointKind::L));
            let high=if source.point.index==high_index{Some(&source.point)}else{frame.points.iter().find(|p|p.index==high_index&&p.kind==super::kernel::PointKind::H)};
            let(Some(origin),Some(high),Some(key))=(origin,high,source.key.as_ref())else{continue;};
            let known:Vec<_>=polyline_frame.points.iter().filter(|p|p.is_closed()&&p.known_index<=asof).cloned().collect();
            let Some(confirmed)=n_alternation::confirm_n_squeeze_alternation(bars,asof,origin,high,key,&known,pair,frame.level,path,config,after,gap.is_none())?else{continue;};
            let mut proof=original.clone();proof["event"]=crate::indexed_json::json!("trend_lifecycle_state");proof["state"]=crate::indexed_json::json!("空多交替");
            proof["bar_index"]=crate::indexed_json::json!(asof);proof["available_index"]=crate::indexed_json::json!(asof);proof["alternation_index"]=crate::indexed_json::json!(asof);
            proof["counter_index"]=crate::indexed_json::json!(confirmed.low.index);proof["counter_ordinal"]=crate::indexed_json::json!(confirmed.low.ordinal);proof["counter_price"]=crate::indexed_json::json!(confirmed.low.value);
            proof["confirmation_rule"]=crate::indexed_json::json!(confirmed.rule);proof["confirmation_evidence"]=confirmed.evidence;
            if let Some(gap)=gap{proof["replaces_gap_confirmation_index"]=crate::indexed_json::json!(gap);}
            enriched.events.retain(|e|!(e.known_index==asof&&e.evidence.get("lifecycle_event").is_some_and(|p|p.get("source_path").and_then(crate::indexed_json::Value::as_str)==Some(path)&&qualification::confirmed_alternation_event(p))));
            let mut event=source.clone();event.known_index=asof;event.title="空多交替".into();event.evidence["lifecycle_event"]=proof;enriched.events.push(event);
        }
    }
    let frame=&enriched;
    let mut evaluation=evaluate_frame_with_timeframe(bars,frame,asof,eligibility,timeframe)?;
    evaluation.events.extend(recovery_events);
    if timeframe!="1d" {return Ok(evaluation);}
    {
        let pair=if polyline_frame.structure_direction==Some(super::kernel::Direction::Down) {
            polyline_frame.key.as_ref().zip(polyline_frame.anchor.as_ref())
        }else{None};
        let mut latest=std::collections::BTreeMap::new();
        for event in &frame.events {
            let Some(proof)=event.evidence.get("lifecycle_event")else{continue;};
            let Some(now)=qualification::index(proof,"bar_index")else{continue;};
            let Some(path)=proof.get("source_path").and_then(crate::indexed_json::Value::as_str)else{continue;};
            if now<=asof {latest.insert(path,proof);}
        }
        for proof in latest.values() {
            if proof.get("state").and_then(crate::indexed_json::Value::as_str)!=Some("空翻多")
                || proof.get("event").and_then(crate::indexed_json::Value::as_str)==Some("trend_lifecycle_invalidated") {continue;}
            let(Some(origin_index),Some(high_index))=(qualification::index(proof,"origin_index"),qualification::index(proof,"flip_index"))else{continue;};
            let origin=frame.points.iter().find(|p|p.index==origin_index&&p.ordinal==qualification::index(proof,"origin_ordinal").unwrap_or(0));
            let high=frame.points.iter().find(|p|p.index==high_index&&p.ordinal==qualification::index(proof,"flip_ordinal").unwrap_or(0));
            let(Some(origin),Some(high))=(origin,high)else{continue;};
            let confirmed=confirmation::confirm_volume_breakout_alternation(bars,asof,origin,high,pair,false)?
                .or(confirmation::confirm_body_gap_alternation(bars,asof,origin,high,&polyline_frame.points,pair)?);
            if let Some(confirmed)=confirmed {
                let mut event:crate::indexed_json::Value=(**proof).clone();
                event["event"]=crate::indexed_json::json!("trend_lifecycle_state");event["state"]=crate::indexed_json::json!("空多交替");
                event["bar_index"]=crate::indexed_json::json!(asof);event["available_index"]=crate::indexed_json::json!(asof);
                event["alternation_index"]=crate::indexed_json::json!(asof);event["counter_index"]=crate::indexed_json::json!(confirmed.low.index);
                event["counter_ordinal"]=crate::indexed_json::json!(confirmed.low.ordinal);event["counter_price"]=crate::indexed_json::json!(confirmed.low.value);
                event["confirmation_rule"]=crate::indexed_json::json!(confirmed.rule);event["confirmation_evidence"]=confirmed.evidence;
                evaluation.alternation_ended|=qualification::confirmed_alternation_event(&event);
                eligibility.observe(&event)?;evaluation.events.push(event);
            }
        }
        let closures:Vec<_>=eligibility.active_intervals().into_iter().filter_map(|window| {
            let low=frame.points.iter().filter(|p|p.is_closed()&&p.kind==super::kernel::PointKind::L&&p.known_index<asof
                &&p.index>=window.start_low_index&&format!("lecture-{}",p.path)==window.source_path).max_by_key(|p|p.order())?;
            let price=low.value.to_string().parse::<Decimal>().ok()?;
            (bars[asof].low<price).then(||crate::indexed_json::json!({"event":"trading_reversal_low_broken","bar_index":asof,"available_index":asof,
                "trend_level":frame.level,"source_path":window.source_path,"reversal_low_index":low.index,
                "reversal_low_known_index":low.known_index,"reversal_low_price":price,"market_low":bars[asof].low}))
        }).collect();
        for event in closures {eligibility.observe(&event)?;evaluation.events.push(event);}
        evaluation.in_qualification_range=eligibility.active();
        evaluation.alternation_ended|=evaluation.in_qualification_range;
        if evaluation.alternation_ended{evaluation.entry_rejections.retain(|rule|rule!="formal_alternation_not_confirmed");}
        if evaluation.in_qualification_range {evaluation.entry_rejections.retain(|r|r!="outside_confirmed_qualification_range");}
        else if !evaluation.entry_rejections.iter().any(|r|r=="outside_confirmed_qualification_range"){evaluation.entry_rejections.push("outside_confirmed_qualification_range".into());}
    }
    eligibility.polyline_gate.advance(bars,asof,polyline_frame)?;
    if let Some(rejection)=eligibility.polyline_gate.restrictions.get(&asof){evaluation.entry_rejections.push(rejection.reason.clone());}
    if let Some(rejection)=entries::inverse_n_low_entry_risk(bars,asof,&polyline_frame.points){evaluation.entry_rejections.push(rejection.reason);}
    if evaluation.entry_rejections.is_empty() {
        if let Some(decision)=entries::qualified_close_entry(bars,asof,eligibility,&eligibility.polyline_gate,&eligibility.events){
            evaluation.trade_point=true;
            evaluation.events.push(crate::indexed_json::json!({"event":"qualified_entry_observed","bar_index":asof,"trend_level":frame.level,"decision":decision}));
        }
    }
    Ok(evaluation)
}

pub fn evaluate_frame_with_timeframe(bars: &[RuleCandle], frame: &super::kernel::KernelFrame, asof: usize,
    eligibility: &mut TradingEligibility, timeframe: &str) -> Result<FrameTradeEvaluation, String> {
    use super::kernel::{PointKind, TrendState};
    if asof >= bars.len() || frame.level > 3 { return Err("frame index or trend level unavailable".into()); }
    validate_bars(&bars[..=asof])?;
    let mut result = FrameTradeEvaluation { alternation_ended: false, in_qualification_range: false,
        trade_point: false, unavailable_rules: vec![], n_observations: vec![], events: vec![], entry_rejections: vec![], exit_decisions: vec![] };
    if timeframe != "1d" {
        result.entry_rejections.push("daily_trading_not_applicable".into());
        result.events.push(crate::indexed_json::json!({"event":"trading_not_applicable","bar_index":asof,"timeframe":timeframe,
            "trend_level":frame.level,"reason":"daily_strategy_requires_daily_bars"}));
        return Ok(result);
    }
    if !eligibility.initialized { *eligibility = TradingEligibility::new(frame.level)?; }
    if eligibility.level != frame.level { return Err("qualification belongs to a different trend level".into()); }
    for event in &frame.events {
        if event.known_index != asof { continue; }
        // 内核只有提供真实生命周期证明时才能写入资格账本；标签不能补全缺失时钟。
        let proof = event.evidence.get("lifecycle_event").unwrap_or(&event.evidence);
        if qualification::index(proof, "trend_level") != Some(usize::from(frame.level))
            || qualification::index(proof, "bar_index") != Some(asof) { continue; }
        result.alternation_ended |= qualification::confirmed_alternation_event(proof);
        eligibility.observe(proof)?;
        result.events.push(proof.clone());
    }
    result.alternation_ended|=eligibility.active();
    if matches!(frame.state, Some(TrendState::BearBullAlternation | TrendState::BullBearAlternation))
        && !result.alternation_ended {
        result.entry_rejections.push("formal_alternation_not_confirmed".into());
    }
    let gaps: Vec<_> = eligibility.active_intervals().into_iter().filter_map(|v| {
        v.confirmation_gap.as_ref().filter(|g| v.gap_replaced_index.is_none() && asof > v.start_index && bars[asof].low <= g.lower)
            .map(|g| crate::indexed_json::json!({"event":"trend_lifecycle_confirmation_revoked","reason":"confirmation_gap_filled",
                "bar_index":asof,"available_index":asof,"trend_level":frame.level,"source_path":v.source_path,
                "qualification_index":v.start_index,"confirmation_gap_index":g.index,"confirmation_gap_lower":g.lower,
                "confirmation_gap_upper":g.upper,"confirmation_gap_kind":g.kind,"market_low":bars[asof].low}))
    }).collect();
    for event in gaps { eligibility.observe(&event)?; result.events.push(event); }
    result.in_qualification_range = eligibility.active();
    let points: Vec<_> = frame.points.iter().filter(|p| p.is_closed() && p.known_index <= asof && p.index <= asof).collect();
    for triple in points.windows(3) {
        let [a,b,c] = [triple[0],triple[1],triple[2]];
        if a.path != b.path || b.path != c.path { continue; }
        let direction = match (a.kind,b.kind,c.kind) {
            (PointKind::L,PointKind::H,PointKind::L) => Direction::Up,
            (PointKind::H,PointKind::L,PointKind::H) => Direction::Down,
            _ => continue,
        };
        // 来源价必须等于同一复权行情上的实际锚点，不能借另一缓存版本的坐标。
        let decimal = |value:f64| -> Result<Decimal,String> { value.to_string().parse::<Decimal>().map_err(|e|e.to_string()) };
        if decimal(a.value)? != direction.adverse(&bars[a.index]) || decimal(b.value)? != direction.extreme(&bars[b.index])
            || decimal(c.value)? != direction.adverse(&bars[c.index]) { continue; }
        let setup = NSetup { symbol:bars[asof].symbol.clone(),timeframe:"1d".into(),direction,
            origin:PivotRef{index:a.index,confirmed_index:a.known_index,ordinal:Some(a.ordinal)},
            neckline:PivotRef{index:b.index,confirmed_index:b.known_index,ordinal:Some(b.ordinal)},
            pullback:PivotRef{index:c.index,confirmed_index:c.known_index,ordinal:Some(c.ordinal)},source:"lecture_causal".into(),
            box_anchor_mode:BoxAnchorMode::AttackVirtualExtreme,allow_confirmation_bar:true,allow_outside_close:true,
            allow_mother_impulse:direction==Direction::Up && a.index==b.index,staged_defense:true,
            allow_mother_pullback:false,allow_inside_pullback:false,polyline_structure:direction==Direction::Up };
        let options=NOptions{timeframe:"1d".into(),milestone_basis:MilestoneBasis::Extreme,asof_index:Some(asof),measurement_start:None,measure_targets:true};
        match observe_n(bars,&setup,&options) {
            Ok(observation)=>result.n_observations.push(observation),
            Err(reason)=>result.events.push(crate::indexed_json::json!({"event":"n_candidate_rejected","bar_index":asof,
                "trend_level":frame.level,"origin_index":a.index,"neckline_index":b.index,"pullback_index":c.index,"reason":reason})),
        }
    }
    if let Some(reason)=entries::common_entry_rejection(bars,asof) {result.entry_rejections.push(reason.into());}
    let mut gap_gate=entries::GapEntryGate::default();
    if let Some(decision)=gap_gate.advance(bars,asof)? {result.entry_rejections.push(decision.reason);}
    if !result.in_qualification_range {result.entry_rejections.push("outside_confirmed_qualification_range".into());}
    result.unavailable_rules.push("trading_day: requires shared target, OneP and risk arbitration context".into());
    for decision in [exits::observe_double_volume_lower_close_exit(bars,asof), exits::observe_gap_bearish_exit(bars,asof),
        exits::volume_shadow_next_decline(bars,asof,None), exits::observe_double_volume_fade(bars,asof),
        exits::observe_limit_up_distribution_exit(bars,asof)].into_iter().flatten() { result.exit_decisions.push(decision); }
    Ok(result)
}
