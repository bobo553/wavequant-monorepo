use crate::analysis::trading::{Direction,RuleCandle,NSetup,NOptions,NObservation,NStatus,NCompletion,MilestoneBasis,observe_n};
use super::price_action::{KeyLevel,LevelKind,ShadowPolicy,ResistanceEvidence,observe_resistance};
use rust_decimal::Decimal;
use serde::{Deserialize,Serialize};
#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct AttackVolume {pub lookback:usize,pub available_history:usize,pub attack_volume:Decimal,pub previous_volume:Decimal,
    pub increased_from_previous:bool,pub trailing_mean:Option<Decimal>,pub relative_volume:Option<Decimal>,pub increased_from_mean:Option<bool>}
#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct DefenseFrame {pub bar_index:usize,pub observed_at:String,pub intrabar_beyond:bool,pub close_beyond:bool,
    pub first_intrabar_breach_index:Option<usize>,pub first_close_breach_index:Option<usize>,pub recovered_on_close:bool}
#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct ControlBarObservation {pub asof_index:usize,pub n_status:NStatus,pub direction:Direction,pub completion:Option<NCompletion>,
    pub defense_level:Option<KeyLevel>,pub volume:Option<AttackVolume>,pub next_bar_resistance:Option<ResistanceEvidence>,
    pub defense_frames:Vec<DefenseFrame>,pub cost_basis:String,pub participant_intent:String}
pub fn observe_control_bar(bars:&[RuleCandle],setup:&NSetup,timeframe:&str,lookback:usize,shadow:Option<ShadowPolicy>,
    asof:usize,measurement_start:Option<usize>)->Result<ControlBarObservation,String> {
    let n=observe_n(bars,setup,&NOptions{timeframe:timeframe.into(),milestone_basis:MilestoneBasis::Close,
        asof_index:Some(asof),measurement_start,measure_targets:true})?;
    observe_prepared_control(bars,setup,lookback,shadow,measurement_start,&n)
}
pub fn observe_prepared_control(bars:&[RuleCandle],setup:&NSetup,lookback:usize,shadow:Option<ShadowPolicy>,
    measurement_start:Option<usize>,n:&NObservation)->Result<ControlBarObservation,String> {
    if lookback==0||n.asof_index>=bars.len()||measurement_start.is_some_and(|i|i>n.asof_index){return Err("control lookback or prefix unavailable".into());}
    if let Some(shadow)=shadow{shadow.validate()?;}
    let mut result=ControlBarObservation{asof_index:n.asof_index,n_status:n.status,direction:setup.direction,
        completion:n.completion.clone(),defense_level:None,volume:None,next_bar_resistance:None,defense_frames:vec![],
        cost_basis:"not_identifiable_from_ohlcv".into(),participant_intent:"unknown_not_inferred".into()};
    let Some(completion)=&n.completion else{return Ok(result);};let attack=completion.bar_index;
    if attack==0||attack>n.asof_index{return Err("attack requires previous volume".into());}
    let history=&bars[attack.saturating_sub(lookback)..attack];
    if history.iter().chain(std::iter::once(&bars[attack])).any(|b|b.volume<Decimal::ZERO){return Err("comparable nonnegative volume required".into());}
    let mean=if history.len()==lookback{Some(history.iter().map(|b|b.volume).sum::<Decimal>()/Decimal::from(lookback as u64))}else{None};
    result.volume=Some(AttackVolume{lookback,available_history:history.len(),attack_volume:bars[attack].volume,previous_volume:bars[attack-1].volume,
        increased_from_previous:bars[attack].volume>bars[attack-1].volume,trailing_mean:mean,
        relative_volume:mean.filter(|m|*m>Decimal::ZERO).map(|m|bars[attack].volume/m),increased_from_mean:mean.map(|m|bars[attack].volume>m)});
    let up=setup.direction==Direction::Up;let sign=setup.direction.sign();
    let level=KeyLevel{symbol:setup.symbol.clone(),timeframe:setup.timeframe.clone(),kind:if up{LevelKind::Support}else{LevelKind::Resistance},
        price:completion.defense,source_index:attack,confirmed_index:attack,source:"completed_n_virtual_defense".into()};
    if attack<n.asof_index&&measurement_start.is_none_or(|i|attack+1>=i){
        result.next_bar_resistance=Some(observe_resistance(&bars[attack],&bars[attack+1],setup.direction,shadow)?);
    }
    let(mut first_extreme,mut first_close)=(None,None);
    for i in (attack+1).max(measurement_start.unwrap_or(0))..=n.asof_index {
        let bar=&bars[i];let adverse=if measurement_start==Some(i){bar.close}else{setup.direction.adverse(bar)};
        let intrabar=sign*(adverse-level.price)<Decimal::ZERO;let close=sign*(bar.close-level.price)<Decimal::ZERO;
        if intrabar&&first_extreme.is_none(){first_extreme=Some(i);}
        if close&&first_close.is_none(){first_close=Some(i);}
        let recovered=first_close.is_some_and(|first|i>first)&&sign*(bars[i-1].close-level.price)<Decimal::ZERO&&sign*(bar.close-level.price)>=Decimal::ZERO;
        result.defense_frames.push(DefenseFrame{bar_index:i,observed_at:bar.timestamp.clone(),intrabar_beyond:intrabar,close_beyond:close,
            first_intrabar_breach_index:first_extreme,first_close_breach_index:first_close,recovered_on_close:recovered});
    }
    result.defense_level=Some(level);Ok(result)
}
