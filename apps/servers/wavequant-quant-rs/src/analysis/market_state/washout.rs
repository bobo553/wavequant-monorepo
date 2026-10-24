use crate::analysis::trading::{Direction,RuleCandle,NSetup,NOptions,NStatus,MilestoneBasis,observe_n};
use rust_decimal::Decimal;
use serde::{Deserialize,Serialize};
#[derive(Debug,Clone,Copy,PartialEq,Eq,Serialize,Deserialize)]
pub enum WashoutStage {#[serde(rename="await_open_1p_2t_band")] AwaitTarget,
    #[serde(rename="await_later_neckline_1p_countermove")] AwaitPullback,
    #[serde(rename="await_independent_new_n")] AwaitReattack,#[serde(rename="price_template_completed")] Confirmed,
    #[serde(rename="outside_strict_figure_009_template")] OutsideTemplate,
    #[serde(rename="original_n_origin_breached")] Invalidated,#[serde(rename="positive_price_targets_unavailable")] Unavailable}
#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct WashoutFrame {pub bar_index:usize,pub observed_at:String,pub stage:WashoutStage,pub reason:String,
    pub first_target_band_index:Option<usize>,pub first_pullback_band_index:Option<usize>,pub reattack_index:Option<usize>,pub new_n_defense:Option<Decimal>}
#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct WashoutObservation {pub asof_index:usize,pub n_status:NStatus,pub direction:Direction,pub zone_basis:MilestoneBasis,
    pub initial_attack_index:Option<usize>,pub neckline:Option<Decimal>,pub one_p:Option<Decimal>,pub two_t:Option<Decimal>,
    pub frames:Vec<WashoutFrame>,pub rule_version:String,pub intent:String,pub probability:Option<Decimal>,pub opens_short_position:bool}
pub fn observe_washout(bars:&[RuleCandle],initial:&NSetup,timeframe:&str,zone_basis:MilestoneBasis,
    reattack:Option<&NSetup>,asof:usize)->Result<WashoutObservation,String> {
    if let Some(second)=reattack {
        if second.symbol!=initial.symbol||second.timeframe!=initial.timeframe||second.direction!=initial.direction
            ||second.origin.index<=initial.pullback.index{return Err("independent second N with matching symbol/timeframe/direction required".into());}
    }
    let n=observe_n(bars,initial,&NOptions{timeframe:timeframe.into(),milestone_basis:zone_basis,asof_index:Some(asof),measurement_start:None,measure_targets:true})?;
    let mut result=WashoutObservation{asof_index:asof,n_status:n.status,direction:initial.direction,zone_basis,
        initial_attack_index:None,neckline:None,one_p:None,two_t:None,frames:vec![],
        rule_version:"figure009_strict_open_bands_separate_bars_v1".into(),intent:"unknown_price_pattern_only".into(),probability:None,opens_short_position:false};
    let Some(completion)=&n.completion else{return Ok(result);};
    let anchors=n.anchors.as_ref().ok_or("completed N has no anchors")?;
    let targets=n.targets.as_ref().ok_or("completed N has no targets")?;
    let attack=completion.bar_index;let neckline=anchors.neckline_extreme;let sign=initial.direction.sign();
    let(mut stage,mut reason)=if targets.one_p.is_some()&&targets.two_t.is_some(){(WashoutStage::AwaitTarget,"waiting_target_band".to_string())}
        else{(WashoutStage::Unavailable,"nonpositive_price_projection".to_string())};
    let(mut target_index,mut pullback_index,mut reattack_index,mut new_defense)=(None,None,None,None);
    let mut second_observation=None;
    for i in attack..=asof {
        let bar=&bars[i];
        if !matches!(stage,WashoutStage::OutsideTemplate|WashoutStage::Invalidated|WashoutStage::Confirmed|WashoutStage::Unavailable) {
            let p=targets.one_p.ok_or("washout target unavailable")?;let tt=targets.two_t.ok_or("washout target unavailable")?;
            let formation=i==attack;let forward=if formation{bar.close}else{initial.direction.extreme(bar)};
            let adverse=if formation{bar.close}else{initial.direction.adverse(bar)};
            let value=if formation||zone_basis==MilestoneBasis::Close{bar.close}else{forward};
            let counter=if zone_basis==MilestoneBasis::Close{bar.close}else{adverse};
            if !formation&&sign*(adverse-anchors.origin)<Decimal::ZERO {stage=WashoutStage::Invalidated;reason="original_wave_boundary_breached".into();}
            else if sign*(forward-tt)>=Decimal::ZERO{stage=WashoutStage::OutsideTemplate;reason="two_t_reached_or_overshot".into();}
            else if target_index.is_some_and(|t|i>t)&&sign*(adverse-neckline)<Decimal::ZERO{stage=WashoutStage::OutsideTemplate;reason="neckline_lost_after_target_band".into();}
            else if stage==WashoutStage::AwaitTarget {
                if sign*(value-p)>Decimal::ZERO&&sign*(tt-value)>Decimal::ZERO {
                    if !formation&&sign*(adverse-neckline)<Decimal::ZERO{stage=WashoutStage::OutsideTemplate;reason="target_and_neckline_breach_order_unknown".into();}
                    else{target_index=Some(i);stage=WashoutStage::AwaitPullback;reason="target_band_observed".into();}
                }
            } else if stage==WashoutStage::AwaitPullback {
                if target_index.is_some_and(|t|i>t)&&sign*(counter-neckline)>Decimal::ZERO&&sign*(p-counter)>Decimal::ZERO {
                    pullback_index=Some(i);stage=WashoutStage::AwaitReattack;reason="later_countermove_band_observed".into();
                }
            } else if let Some(second)=reattack.filter(|s|s.pullback.confirmed_index<i) {
                if pullback_index.is_some_and(|p|second.origin.index<p){return Err("second N origin must belong to observed pullback or later".into());}
                if second_observation.is_none(){second_observation=Some(observe_n(bars,second,&NOptions{timeframe:timeframe.into(),
                    milestone_basis:MilestoneBasis::Close,asof_index:Some(asof),measurement_start:None,measure_targets:true})?);}
                if let Some(observation)=&second_observation {
                    if let Some(completed)=observation.completion.as_ref().filter(|c|c.bar_index==i) {
                        reattack_index=Some(i);new_defense=Some(completed.defense);stage=WashoutStage::Confirmed;reason="independent_same_direction_n_completed".into();
                    }else if observation.invalidated_index.is_some_and(|d|d<=i){
                        let status=crate::indexed_json::to_value(observation.status).map_err(|e|e.to_string())?;
                        reason=format!("second_n_{}",status.as_str().ok_or("invalid N status serialization")?);
                    }
                }
            }
        }
        result.frames.push(WashoutFrame{bar_index:i,observed_at:bar.timestamp.clone(),stage,reason:reason.clone(),
            first_target_band_index:target_index,first_pullback_band_index:pullback_index,reattack_index,new_n_defense:new_defense});
    }
    result.initial_attack_index=Some(attack);result.neckline=Some(neckline);result.one_p=targets.one_p;result.two_t=targets.two_t;Ok(result)
}
