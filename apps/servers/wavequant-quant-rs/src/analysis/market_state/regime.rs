//! 单个已完成 N 的六种量价市场状态；确认只属于当日，不延长为持续买入许可。
use crate::analysis::trading::{Direction,RuleCandle,NSetup,NOptions,NObservation,NStatus,MilestoneBasis,observe_n};
use super::{price_action::{ShadowPolicy,ResistanceEvidence,observe_resistance},strong_bullish_candle};
use rust_decimal::Decimal;
use serde::{Deserialize,Serialize};
mod incremental;
pub(crate) use incremental::IncrementalRegime;
#[derive(Debug,Clone,Copy,PartialEq,Eq,Serialize,Deserialize)]
pub enum MarketRegime {#[serde(rename="强轧空")] StrongBull,#[serde(rename="轧空")] Bull,
    #[serde(rename="盘坚")] GrindUp,#[serde(rename="盘跌")] GrindDown,
    #[serde(rename="追杀")] Bear,#[serde(rename="强追杀")] StrongBear}
#[derive(Debug,Clone,Copy,PartialEq,Eq,Serialize,Deserialize)]
#[serde(rename_all="snake_case")]
pub enum WaveBoundary { NOrigin,NPullback }
#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct RegimePolicy {pub shadow_policy:Option<ShadowPolicy>,pub wave_boundary:WaveBoundary,pub local_resistance_failure:bool}
#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct RegimeFrame {pub bar_index:usize,pub observed_at:String,pub phase:String,pub regime:Option<MarketRegime>,
    pub resistance:Option<ResistanceEvidence>,pub resistance_outcome:String,pub first_resistance_index:Option<usize>,
    pub unknown_resistance_seen:bool,pub first_defense_breach_index:Option<usize>,pub first_wave_breach_index:Option<usize>,
    pub continuation_level:Decimal,pub close_continuation:bool,pub rolling_virtual_defense:Decimal,
    pub rolling_defense_held:Option<bool>,pub uninterrupted_progress:bool,
    pub last_confirmed_regime:Option<MarketRegime>,pub last_confirmed_index:Option<usize>}
#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct RegimeObservation {pub asof_index:usize,pub n_status:NStatus,pub policy:RegimePolicy,
    pub attack_index:Option<usize>,pub n_defense:Option<Decimal>,pub wave_boundary:Option<Decimal>,
    pub frames:Vec<RegimeFrame>,pub rule_version:String}
pub fn observe_market_regime(bars:&[RuleCandle],setup:&NSetup,timeframe:&str,policy:&RegimePolicy,
    asof:usize,measurement_start:Option<usize>)->Result<RegimeObservation,String> {
    let n=observe_n(bars,setup,&NOptions{timeframe:timeframe.into(),milestone_basis:MilestoneBasis::Close,
        asof_index:Some(asof),measurement_start,measure_targets:true})?;
    observe_prepared_regime(bars,setup,policy,measurement_start,&n)
}
pub fn observe_prepared_regime(bars:&[RuleCandle],setup:&NSetup,policy:&RegimePolicy,
    measurement_start:Option<usize>,n:&NObservation)->Result<RegimeObservation,String> {
    #[cfg(feature="qualification-timing")]
    let _profile_scope=crate::analysis::kernel::qualification_profile::enter("regime_history");

    if n.asof_index>=bars.len() || measurement_start.is_some_and(|i|i>n.asof_index) {return Err("regime prefix unavailable".into());}
    if let Some(policy)=policy.shadow_policy {policy.validate()?;}
    let mut result=RegimeObservation{asof_index:n.asof_index,n_status:n.status,policy:policy.clone(),
        attack_index:None,n_defense:None,wave_boundary:None,frames:vec![],rule_version:"six_regimes_v3_n_attack_response_window_attack_bar_break".into()};
    let Some(completion)=&n.completion else{return Ok(result);};
    let anchors=n.anchors.as_ref().ok_or("completed N has no anchors")?;
    let attack=completion.bar_index;
    if attack==0 || attack>n.asof_index {return Err("completed N attack is not available".into());}
    let defense=completion.defense;
    let boundary=if policy.wave_boundary==WaveBoundary::NOrigin {anchors.origin}else{anchors.pullback};
    let mut cursor=IncrementalRegime::new(bars,attack,defense,boundary,setup.direction,policy.clone(),measurement_start)?;
    let mut frames=vec![cursor.frame().clone()];
    for i in (attack+1).max(measurement_start.unwrap_or(0))..=n.asof_index {
        frames.push(cursor.step(bars,i)?.clone());
    }
    result.attack_index=Some(attack);result.n_defense=Some(defense);result.wave_boundary=Some(boundary);
    result.frames=frames.into_iter().filter(|f|measurement_start.is_none_or(|i|f.bar_index>=i)).collect();Ok(result)
}
#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct SqueezeResumption {pub bar_index:usize,pub prior_confirmation_index:usize,pub attack_index:usize,
    pub defense:Decimal,pub local_resistance:Decimal,pub regime:MarketRegime}
pub fn observe_squeeze_resumption(bars:&[RuleCandle],index:usize,frame:&RegimeFrame,offset:usize,attack:usize,defense:Decimal)->Result<Option<SqueezeResumption>,String> {
    if frame.bar_index.checked_add(offset)!=Some(index)||index>=bars.len(){return Err("regime frame must belong to observation date".into());}
    if frame.regime.is_some()||!matches!(frame.last_confirmed_regime,Some(MarketRegime::Bull|MarketRegime::StrongBull))
        ||frame.first_defense_breach_index.is_some()||frame.first_wave_breach_index.is_some(){return Ok(None);}
    let Some(known)=frame.last_confirmed_index.and_then(|i|i.checked_add(offset))else{return Ok(None);};
    if known<attack+2||known+1>=index{return Ok(None);}
    let (bar,prev,confirmed)=(&bars[index],&bars[index-1],&bars[known]);
    if prev.close>=confirmed.close||prev.low<defense||bar.low<defense||bar.low<prev.low||bar.close<=prev.high||bar.close<=bar.open{return Ok(None);}
    Ok(Some(SqueezeResumption{bar_index:index,prior_confirmation_index:known,attack_index:attack,defense,local_resistance:prev.high,regime:MarketRegime::Bull}))
}
#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct VolumeReversalEvidence {pub reversal_record_high:Decimal,pub reversal_structural_low:Decimal,
    pub reversal_attack_defense:Decimal,pub reversal_low:Decimal,pub reversal_close:Decimal,
    pub reversal_volume:Decimal,pub reversal_previous_volume:Decimal}
pub fn volume_reversal_squeeze(bars:&[RuleCandle],attack:usize,now:usize,origin_low:Decimal,attack_defense:Decimal,frame:&RegimeFrame,offset:usize)->Option<VolumeReversalEvidence> {
    if now>=bars.len()||now<attack+2||frame.first_wave_breach_index.is_some()
        ||!frame.first_resistance_index.is_some_and(|i|i.checked_add(offset).is_some_and(|r|r<now))
        ||frame.first_defense_breach_index.and_then(|i|i.checked_add(offset))!=Some(now){return None;}
    let (bar,prev)=(&bars[now],&bars[now-1]);let record=bars[attack..now].iter().map(|b|b.high).max()?;
    if !(bar.open<prev.close&&bar.low>=origin_low&&bar.close>record&&bar.close>bar.open
        &&(bar.close-bar.open)*Decimal::TWO>bar.high-bar.low&&bar.volume>prev.volume){return None;}
    Some(VolumeReversalEvidence{reversal_record_high:record,reversal_structural_low:origin_low,reversal_attack_defense:attack_defense,
        reversal_low:bar.low,reversal_close:bar.close,reversal_volume:bar.volume,reversal_previous_volume:prev.volume})
}
