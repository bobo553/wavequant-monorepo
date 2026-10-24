use crate::analysis::trading::{Direction,RuleCandle,validate_bars};
use rust_decimal::Decimal;
use serde::{Deserialize,Serialize};

#[derive(Debug,Clone,Copy,PartialEq,Eq,Serialize,Deserialize)]
#[serde(rename_all="snake_case")]
pub enum LevelKind { Resistance,Support }
#[derive(Debug,Clone,Copy,PartialEq,Eq,Serialize,Deserialize)]
#[serde(rename_all="snake_case")]
pub enum AttackBasis { Intrabar,Close }
#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct KeyLevel { pub symbol:String,pub timeframe:String,pub kind:LevelKind,pub price:Decimal,
    pub source_index:usize,pub confirmed_index:usize,pub source:String }
impl KeyLevel {
    pub fn validate(&self)->Result<(),String> {
        if self.price<=Decimal::ZERO || self.source_index>self.confirmed_index
            || [&self.symbol,&self.timeframe,&self.source].iter().any(|s|s.trim().is_empty()) {
            return Err("key level needs positive price and dated provenance".into());
        } Ok(())
    }
}
#[derive(Debug,Clone,Copy,Serialize,Deserialize)]
pub struct ShadowPolicy { pub minimum_range_fraction:Decimal }
impl ShadowPolicy { pub fn validate(self)->Result<(),String> {
    if self.minimum_range_fraction<=Decimal::ZERO || self.minimum_range_fraction>Decimal::ONE {
        Err("shadow threshold must be in (0,1]".into())
    } else {Ok(())}
} }
#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct AttackEvidence {
    pub direction:Direction,pub level:KeyLevel,pub bar_index:usize,pub observed_at:String,
    pub previous_close:Decimal,pub extreme_beyond:bool,pub close_beyond:bool,pub touched:bool,
    pub intrabar_crossed:bool,pub close_crossed:bool,pub gap_across:bool,
}
impl AttackEvidence { pub fn qualifies(&self,basis:AttackBasis)->bool {
    match basis {AttackBasis::Intrabar=>self.intrabar_crossed,AttackBasis::Close=>self.close_crossed}
} }
#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct ResistanceEvidence {
    pub attack_direction:Direction,pub opposing_side:String,pub observed_at:String,
    pub higher_open:bool,pub lower_open:bool,pub bullish_body:bool,pub bearish_body:bool,
    pub opposing_open_and_body:bool,pub direct_opposing_open:bool,pub shadow_length:Decimal,
    pub shadow_range_fraction:Option<Decimal>,pub long_shadow:Option<bool>,pub detected:Option<bool>,
    pub reasons:Vec<String>,pub shadow_policy:Option<ShadowPolicy>,
}
pub fn observe_attack(bars:&[RuleCandle],index:usize,level:&KeyLevel,timeframe:&str)->Result<AttackEvidence,String> {
    level.validate()?;
    if index==0 || index>=bars.len() || level.timeframe!=timeframe || level.confirmed_index>=index {
        return Err("attack requires a previous bar and a key already confirmed in this timeframe".into());
    }
    validate_bars(&bars[..=index])?;
    if bars[index].symbol!=level.symbol {return Err("key level symbol mismatch".into());}
    let (previous,current)=(&bars[index-1],&bars[index]);
    let direction=if level.kind==LevelKind::Resistance {Direction::Up}else{Direction::Down};
    let sign=direction.sign();let starting=sign*(previous.close-level.price)<=Decimal::ZERO;
    let extreme_beyond=sign*(direction.extreme(current)-level.price)>Decimal::ZERO;
    let close_beyond=sign*(current.close-level.price)>Decimal::ZERO;
    Ok(AttackEvidence{direction,level:level.clone(),bar_index:index,observed_at:current.timestamp.clone(),
        previous_close:previous.close,extreme_beyond,close_beyond,touched:current.low<=level.price&&level.price<=current.high,
        intrabar_crossed:starting&&extreme_beyond,close_crossed:starting&&close_beyond,
        gap_across:starting&&sign*(current.open-level.price)>Decimal::ZERO})
}
pub fn observe_resistance(previous:&RuleCandle,current:&RuleCandle,direction:Direction,policy:Option<ShadowPolicy>)->Result<ResistanceEvidence,String> {
    if let Some(policy)=policy {policy.validate()?;}
    validate_bars(&[previous.clone(),current.clone()])?;
    let up=direction==Direction::Up;
    let higher=current.open>previous.close;let lower=current.open<previous.close;
    let bullish=current.close>current.open;let bearish=current.close<current.open;
    let open_body=if up {higher&&bearish}else{lower&&bullish};
    let opposing=if up {lower}else{higher};
    let shadow=if up {current.high-current.open.max(current.close)}else{current.open.min(current.close)-current.low};
    let span=current.high-current.low;
    let fraction=if span>Decimal::ZERO {Some(shadow/span)}else{None};
    let long=if shadow==Decimal::ZERO {Some(false)}else{policy.map(|p|shadow>=span*p.minimum_range_fraction)};
    let mut reasons=Vec::new();
    if open_body {reasons.push(if up {"higher_open_bearish_body"}else{"lower_open_bullish_body"}.into());}
    if opposing {reasons.push(if up {"direct_lower_open"}else{"direct_higher_open"}.into());}
    if long==Some(true) {reasons.push(if up {"long_upper_shadow"}else{"long_lower_shadow"}.into());}
    let detected=if !reasons.is_empty() {Some(true)}else{long.map(|_|false)};
    Ok(ResistanceEvidence{attack_direction:direction,opposing_side:if up {"bears"}else{"bulls"}.into(),
        observed_at:current.timestamp.clone(),higher_open:higher,lower_open:lower,bullish_body:bullish,bearish_body:bearish,
        opposing_open_and_body:open_body,direct_opposing_open:opposing,shadow_length:shadow,
        shadow_range_fraction:fraction,long_shadow:long,detected,reasons,shadow_policy:policy})
}
#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct ConfirmationEvidence {pub bar_index:usize,pub observed_at:String,pub close_beyond_key:bool,
    pub close_back_across_key:bool,pub extreme_beyond_attack:bool,pub close_beyond_attack_extreme:bool,
    pub extreme_beyond_response:bool,pub close_beyond_response_extreme:bool,pub close_progress_from_response:bool,
    pub resistance_outcome:String}
#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct AttackSequence {pub phase:String,pub attack_basis:AttackBasis,pub attack:AttackEvidence,
    pub response:Option<ResistanceEvidence>,pub confirmation:Option<ConfirmationEvidence>}
pub fn observe_sequence(bars:&[RuleCandle],index:usize,level:&KeyLevel,timeframe:&str,basis:AttackBasis,policy:Option<ShadowPolicy>)->Result<AttackSequence,String> {
    let attack=observe_attack(bars,index,level,timeframe)?;
    if let Some(policy)=policy {policy.validate()?;}
    let mut result=AttackSequence{phase:"no_attack".into(),attack_basis:basis,attack,response:None,confirmation:None};
    if !result.attack.qualifies(basis) {return Ok(result);}
    result.phase="await_response".into();
    if bars.len()<=index+1 {return Ok(result);}
    let direction=result.attack.direction;
    result.response=Some(observe_resistance(&bars[index],&bars[index+1],direction,policy)?);
    result.phase="await_confirmation".into();
    if bars.len()<=index+2 {return Ok(result);}
    validate_bars(&bars[index+1..=index+2])?;
    let (a,r,c)=(&bars[index],&bars[index+1],&bars[index+2]);let sign=direction.sign();
    result.confirmation=Some(ConfirmationEvidence{bar_index:index+2,observed_at:c.timestamp.clone(),
        close_beyond_key:sign*(c.close-level.price)>Decimal::ZERO,close_back_across_key:sign*(c.close-level.price)<Decimal::ZERO,
        extreme_beyond_attack:sign*(direction.extreme(c)-direction.extreme(a))>Decimal::ZERO,
        close_beyond_attack_extreme:sign*(c.close-direction.extreme(a))>Decimal::ZERO,
        extreme_beyond_response:sign*(direction.extreme(c)-direction.extreme(r))>Decimal::ZERO,
        close_beyond_response_extreme:sign*(c.close-direction.extreme(r))>Decimal::ZERO,
        close_progress_from_response:sign*(c.close-r.close)>Decimal::ZERO,resistance_outcome:"undefined_rule".into()});
    result.phase="confirmation_observed".into();Ok(result)
}
