use crate::analysis::trading::{Direction,RuleCandle};
use super::{price_action::{AttackBasis,KeyLevel,LevelKind,observe_attack},regime::{MarketRegime,RegimeObservation},
    strength::{ConfirmedPivot,PivotKind,StrengthScale,WaveStrength,validate_prefix,validate_pivots,observe_wave_strength,compare_ratio}};
use rust_decimal::Decimal;
use serde::{Deserialize,Serialize};
#[derive(Debug,Clone,Copy,Serialize,Deserialize)]
pub enum TurnThreshold {#[serde(rename="exact_2_over_3")] TwoThirds,#[serde(rename="literal_67_percent")] Percent67}
impl TurnThreshold {fn fraction(self)->(i64,i64){match self{Self::TwoThirds=>(2,3),Self::Percent67=>(67,100)}}}
#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct TurnPolicy {pub strength_scale:StrengthScale,pub first_threshold:TurnThreshold,pub second_threshold:TurnThreshold,
    pub key_basis:AttackBasis,pub line_basis:AttackBasis}
#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct RegimeContext {pub symbol:String,pub timeframe:String,pub regime:MarketRegime,pub asof_index:usize,pub source:String}
pub fn context_from_regime(observation:&RegimeObservation,symbol:&str,timeframe:&str)->Result<RegimeContext,String> {
    let frame=observation.frames.last().ok_or("regime has no confirmation frame")?;
    if frame.phase=="wave_boundary_breached"{return Err("regime context invalidated".into());}
    let regime=frame.last_confirmed_regime.ok_or("regime confirmation unavailable")?;
    if symbol.trim().is_empty()||timeframe.trim().is_empty(){return Err("regime identity required".into());}
    Ok(RegimeContext{symbol:symbol.into(),timeframe:timeframe.into(),regime,asof_index:observation.asof_index,
        source:format!("{}_explicit_last_confirmed_snapshot",observation.rule_version)})
}
#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct MinorLine {pub symbol:String,pub timeframe:String,pub direction:Direction,pub first:ConfirmedPivot,
    pub second:ConfirmedPivot,pub selected_at_index:usize,pub source:String}
impl MinorLine {
    pub fn validate(&self)->Result<(),String>{
        let kind=if self.direction==Direction::Up{PivotKind::High}else{PivotKind::Low};
        if [&self.symbol,&self.timeframe,&self.source].iter().any(|s|s.trim().is_empty())
            ||self.first.kind!=kind||self.second.kind!=kind||self.first.index>=self.second.index
            ||self.first.confirmed_index>self.second.confirmed_index||self.second.confirmed_index>self.selected_at_index
            ||self.direction.sign()*(self.second.price-self.first.price)>=Decimal::ZERO{return Err("turn line needs last descending highs or ascending lows with known provenance".into());}
        Ok(())
    }
    // 以分母未约分的直线数值比较，避免有限小数除法改变严格越线条件。
    fn numerator(&self,index:usize)->Result<(Decimal,Decimal),String>{
        self.validate()?;
        let span=Decimal::from((self.second.index-self.first.index)as u64);
        let offset=Decimal::from(index as u64)-Decimal::from(self.first.index as u64);
        Ok((self.first.price*span+(self.second.price-self.first.price)*offset,span))
    }
}
pub fn freeze_minor_line(points:&[ConfirmedPivot],symbol:&str,timeframe:&str,direction:Direction,selected_at_index:usize,source:&str)->Result<Option<MinorLine>,String>{
    let kind=if direction==Direction::Up{PivotKind::High}else{PivotKind::Low};
    let known:Vec<_>=points.iter().filter(|p|p.index<=selected_at_index&&p.confirmed_index<=selected_at_index&&p.kind==kind).collect();
    if known.len()<2{return Ok(None);}
    let line=MinorLine{symbol:symbol.into(),timeframe:timeframe.into(),direction,first:known[known.len()-2].clone(),
        second:known[known.len()-1].clone(),selected_at_index,source:source.into()};line.validate()?;Ok(Some(line))
}
#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct LineBreak {pub bar_index:usize,pub direction:Direction,pub line_price:Decimal,pub previous_line_price:Decimal,
    pub observed_price:Decimal,pub basis:AttackBasis}
pub fn line_break_on_bar(bars:&[RuleCandle],line:&MinorLine,index:usize,basis:AttackBasis)->Result<Option<LineBreak>,String>{
    line.validate()?;validate_prefix(bars,&line.symbol,index)?;
    if index<=line.selected_at_index{return Ok(None);}
    validate_pivots(bars,&[&line.first,&line.second],index)?;
    line_break_known(bars,line,index,basis)
}
fn line_break_known(bars:&[RuleCandle],line:&MinorLine,index:usize,basis:AttackBasis)->Result<Option<LineBreak>,String>{
    if index==0||index>=bars.len(){return Err("line break index unavailable".into());}
    let(before,denominator)=line.numerator(index-1)?;let(now,_)=line.numerator(index)?;
    if before.min(now)<=Decimal::ZERO{return Ok(None);}
    let sign=line.direction.sign();let price=if basis==AttackBasis::Close{bars[index].close}else{line.direction.extreme(&bars[index])};
    if sign*(bars[index-1].close*denominator-before)<=Decimal::ZERO&&sign*(price*denominator-now)>Decimal::ZERO{
        return Ok(Some(LineBreak{bar_index:index,direction:line.direction,line_price:now/denominator,
            previous_line_price:before/denominator,observed_price:price,basis}));
    }Ok(None)
}
#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct TurnSetup {pub symbol:String,pub timeframe:String,pub direction:Direction,pub origin:ConfirmedPivot,pub impulse_end:ConfirmedPivot,
    pub first_counter:ConfirmedPivot,pub second_counter:ConfirmedPivot,pub background:RegimeContext,pub trend_key:KeyLevel}
impl TurnSetup {pub fn validate(&self)->Result<(),String>{
    self.trend_key.validate()?;
    let points=[&self.origin,&self.impulse_end,&self.first_counter,&self.second_counter];let up=self.direction==Direction::Up;
    let kinds=if up{[PivotKind::High,PivotKind::Low,PivotKind::High,PivotKind::Low]}else{[PivotKind::Low,PivotKind::High,PivotKind::Low,PivotKind::High]};
    if points.iter().zip(kinds).any(|(p,k)|p.kind!=k)
        ||points.windows(2).any(|p|p[0].index>=p[1].index||p[0].confirmed_index>p[1].confirmed_index)
        ||self.impulse_end.confirmed_index>=self.first_counter.index{return Err("four chronological alternating pivots must predate counterwave context".into());}
    let bearish=matches!(self.background.regime,MarketRegime::Bear|MarketRegime::StrongBear|MarketRegime::GrindDown);
    if bearish!=up||self.background.source.trim().is_empty()||self.background.symbol!=self.symbol||self.trend_key.symbol!=self.symbol
        ||self.background.timeframe!=self.timeframe||self.trend_key.timeframe!=self.timeframe
        ||self.background.asof_index>self.impulse_end.confirmed_index||self.trend_key.confirmed_index>self.impulse_end.confirmed_index
        ||self.trend_key.kind!=if up{LevelKind::Resistance}else{LevelKind::Support}{return Err("turn background and key must be matching known pre-counterwave context".into());}
    Ok(())
}}
#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct TurnObservation {pub asof_index:usize,pub direction:Direction,pub policy:TurnPolicy,pub stage:String,
    pub first_strength:Option<WaveStrength>,pub second_strength:Option<WaveStrength>,pub suspicion_index:Option<usize>,
    pub suspicion_reason:Option<String>,pub key_break_index:Option<usize>,pub combination_index:Option<usize>,
    pub line_break:Option<LineBreak>,pub invalidated_index:Option<usize>,pub opens_short_position:bool}
pub fn observe_market_turn(bars:&[RuleCandle],setup:&TurnSetup,policy:&TurnPolicy,line:Option<&MinorLine>,asof:usize)->Result<TurnObservation,String>{
    setup.validate()?;validate_prefix(bars,&setup.symbol,asof)?;
    if let Some(line)=line {line.validate()?;if line.symbol!=setup.symbol||line.timeframe!=setup.timeframe||line.direction!=setup.direction{return Err("minor line must share indexed bars and direction".into());}}
    let points=[&setup.origin,&setup.impulse_end,&setup.first_counter,&setup.second_counter];
    validate_pivots(bars,&points,asof)?;
    let first=observe_wave_strength(bars,[points[0],points[1],points[2]],&setup.symbol,policy.strength_scale,asof)?;
    let second=observe_wave_strength(bars,[points[1],points[2],points[3]],&setup.symbol,policy.strength_scale,asof)?;
    if let Some(line)=line{validate_pivots(bars,&[&line.first,&line.second],asof)?;}
    let up=setup.direction==Direction::Up;let sign=setup.direction.sign();
    if asof>=setup.impulse_end.confirmed_index&&sign*(bars[setup.impulse_end.confirmed_index].close-setup.trend_key.price)>Decimal::ZERO{return Err("wave context already beyond major key; provide earlier context".into());}
    let mut key_break=None;
    for i in setup.impulse_end.confirmed_index+1..=asof {
        let price=if policy.key_basis==AttackBasis::Close{bars[i].close}else{setup.direction.extreme(&bars[i])};
        if sign*(bars[i-1].close-setup.trend_key.price)<=Decimal::ZERO&&sign*(price-setup.trend_key.price)>Decimal::ZERO
            &&observe_attack(bars,i,&setup.trend_key,&setup.timeframe)?.qualifies(policy.key_basis){key_break=Some(i);break;}
    }
    let(mut suspicion,mut reason)=(None,None);
    if let Some(first)=&first {let(n,d)=policy.first_threshold.fraction();
        if compare_ratio(first.countermove,first.impulse,n,d)?.is_gt(){suspicion=Some(setup.first_counter.confirmed_index);reason=Some("first_counter_exceeds_threshold".into());}}
    if !up&&key_break.is_some_and(|i|suspicion.is_none_or(|s|i<=s)){suspicion=key_break;reason=Some("last_rise_low_broken".into());}
    let mut stage=if first.is_none(){"await_confirmed_first_counterwave"}else{"first_counter_does_not_trigger_suspicion"};
    let(mut combination,mut event,mut invalidated)=(None,None::<LineBreak>,None);
    if let Some(suspicion)=suspicion {
        stage="turn_suspicion";
        if let Some(second)=&second {let(n,d)=policy.second_threshold.fraction();let comparison=compare_ratio(second.countermove,second.impulse,n,d)?;
            if comparison.is_eq(){stage="second_counter_threshold_equality_unresolved";}
            else if second.countermove>Decimal::ZERO&&comparison.is_lt(){combination=Some(suspicion.max(setup.second_counter.confirmed_index));stage="ratio_combination_known_await_fresh_line_break";}
            else{stage="second_counter_not_below_threshold";}}
    }
    if let Some(combination)=combination {
        for i in combination+1..=asof {
            if sign*(setup.direction.adverse(&bars[i])-setup.second_counter.price)<Decimal::ZERO{invalidated=Some(i);stage="second_counter_extended_new_pivot_required";break;}
            if event.is_none(){if let Some(line)=line.filter(|line|i>line.selected_at_index){event=line_break_known(bars,line,i,policy.line_basis)?;if event.is_some(){stage="market_turn_confirmed";}}}
        }
    }
    if up&&key_break.is_some_and(|i|event.as_ref().is_none_or(|e|i<=e.bar_index)){stage="positive_early_turn_preempted_by_last_fall_high_break";event=None;}
    Ok(TurnObservation{asof_index:asof,direction:setup.direction,policy:policy.clone(),stage:stage.into(),first_strength:first,second_strength:second,
        suspicion_index:suspicion,suspicion_reason:reason,key_break_index:key_break,combination_index:combination,line_break:event,invalidated_index:invalidated,opens_short_position:false})
}
