use super::{RuleCandle,GeometryPath,GeometryPoint,date_at};
use rust_decimal::Decimal;
use serde::{Serialize,Deserialize};
use crate::indexed_json::{Value,json};
use std::collections::{BTreeMap,BTreeSet};
pub const DIRECT_PROMOTION_RULE:&str="level1_confirmed_high_breaks_known_level2_last_fall_high";
#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct SecondaryPullbackCandidate {pub origin:GeometryPoint,pub high:GeometryPoint,pub key:GeometryPoint,
    pub resistance:GeometryPoint,pub low:GeometryPoint,pub retracement_ratio:Decimal,pub source_path:String,pub source_base_path:String}
impl SecondaryPullbackCandidate {
    pub fn known_index(&self)->usize{[self.origin.known,self.high.known,self.key.known,self.resistance.known,self.low.known].into_iter().max().unwrap_or(0)}
    fn episode(&self)->(String,usize,Decimal,usize,Decimal){(self.source_path.clone(),self.origin.index,self.origin.price,self.high.index,self.high.price)}
    fn observation(&self)->(String,usize,Decimal,usize,Decimal,usize,Decimal){(self.source_path.clone(),self.origin.index,self.origin.price,self.high.index,self.high.price,self.low.index,self.low.price)}
}
pub fn secondary_pullback_candidates(bars:&[RuleCandle],drawing:&[GeometryPath],first:&[GeometryPath],second:&[GeometryPath],now:usize)->Vec<SecondaryPullbackCandidate>{
    if now>=bars.len(){return vec![];}let mut result=vec![];
    for path in second {
        let Some(source)=first.iter().find(|s|Some(&s.id)==path.source_path.as_ref())else{continue;};
        let base_paths:Vec<_>=if source.source_paths.is_empty(){source.source_path.iter().collect()}else{source.source_paths.iter().collect()};
        for pair in path.points.windows(2){let(origin,high)=(&pair[0],&pair[1]);
            if high.confirmation_rule.as_deref()!=Some(DIRECT_PROMOTION_RULE){continue;}
            let Some(key)=high.broken_key.as_deref()else{continue;};
            if !origin.known(bars,"L",now,false)||!high.known(bars,"H",now,false)||!key.known(bars,"H",now,false)
                ||!(key.index<origin.index&&origin.index<high.index&&high.index<now)||key.known>=high.index||high.price<=key.price||high.price<=origin.price{continue;}
            if [origin,high].iter().any(|endpoint|!source.points.iter().any(|p|p.known(bars,&p.kind,now,false)&&p.index==endpoint.index&&p.kind==endpoint.kind&&p.price==endpoint.price)){continue;}
            if bars[origin.index+1..=now].iter().any(|b|b.low<origin.price)||bars[high.index+1..=now].iter().any(|b|b.high>high.price){continue;}
            let Some(bottom)=(high.index+1..=now).min_by_key(|&i|(bars[i].low,i))else{continue;};
            let amplitude=high.price-origin.price;let retraced=high.price-bars[bottom].low;
            if retraced*Decimal::from(3)<amplitude*Decimal::TWO||retraced>=amplitude{continue;}
            for base in drawing.iter().filter(|b|base_paths.contains(&&b.id)){
                for pair in base.points.windows(2){let(resistance,low)=(&pair[0],&pair[1]);
                    if !resistance.known(bars,"H",now,false)||!low.known(bars,"L",now,false)||!(high.index<resistance.index&&resistance.index<low.index&&low.index==bottom)
                        ||resistance.known>=low.index||resistance.price<=low.price||resistance.price>=high.price{continue;}
                    result.push(SecondaryPullbackCandidate{origin:origin.clone(),high:high.clone(),key:key.clone(),resistance:resistance.clone(),low:low.clone(),
                        retracement_ratio:retraced/amplitude,source_path:path.id.clone(),source_base_path:base.id.clone()});
                }
            }
        }
    }result
}
fn evidence(c:&SecondaryPullbackCandidate,bars:&[RuleCandle])->Value{
    json!({"definition":"secondary_direct_upgrade_deep_pullback_reclaim_v1","buy_point_type":"secondary_deep_pullback_reclaim","trend_level":2,
        "observed_low":true,"formal_alternation":false,"requires_positive_n":false,"origin_index":c.origin.index,"flip_high_index":c.high.index,
        "secondary_origin_index":c.origin.index,"secondary_origin_date":date_at(bars,c.origin.index),"secondary_origin_low":c.origin.price,"secondary_origin_known_date":date_at(bars,c.origin.known),
        "secondary_high_index":c.high.index,"secondary_high_date":date_at(bars,c.high.index),"secondary_high":c.high.price,"secondary_high_known_date":date_at(bars,c.high.known),
        "secondary_high_confirmation_rule":DIRECT_PROMOTION_RULE,"secondary_key_index":c.key.index,"secondary_key_date":date_at(bars,c.key.index),
        "secondary_key_high":c.key.price,"secondary_key_known_date":date_at(bars,c.key.known),"secondary_resistance_index":c.resistance.index,
        "secondary_resistance_date":date_at(bars,c.resistance.index),"secondary_resistance_high":c.resistance.price,"secondary_resistance_known_date":date_at(bars,c.resistance.known),
        "secondary_pullback_low_index":c.low.index,"secondary_pullback_low_date":date_at(bars,c.low.index),"secondary_pullback_low":c.low.price,
        "secondary_pullback_low_known_date":date_at(bars,c.low.known),"secondary_observation_known_date":date_at(bars,c.known_index()),
        "source_path":c.source_path,"source_base_path":c.source_base_path,"counter_ratio":c.retracement_ratio,"stop":c.low.price,"target":c.high.price})
}
#[derive(Debug,Clone,Default,Serialize,Deserialize)]
pub struct SecondaryPullbackState {last:Option<usize>,previous:Vec<SecondaryPullbackCandidate>,pub events:Vec<Value>,pub proofs:BTreeMap<usize,Value>,
    retired:BTreeSet<(String,usize,Decimal,usize,Decimal)>,observed:BTreeSet<(String,usize,Decimal,usize,Decimal,usize,Decimal)>,
    invalidated:BTreeSet<(String,usize,Decimal,usize,Decimal,usize,Decimal)>}
impl SecondaryPullbackState {
    pub fn advance(&mut self,bars:&[RuleCandle],now:usize,candidates:&[SecondaryPullbackCandidate],entry_allowed:bool)->Result<Option<Value>,String>{
        if now>=bars.len()||self.last.is_some_and(|i|now!=i+1){return Err("pullback stream requires consecutive prefix".into());}
        let mut result=None;
        if now>0 {
            let mut known:Vec<_>=self.previous.iter().filter(|c|c.known_index()<now&&!self.retired.contains(&c.episode())&&!self.invalidated.contains(&c.observation())).cloned().collect();
            known.sort_by_key(|c|std::cmp::Reverse((c.high.index,c.low.index)));
            for candidate in known {let bar=&bars[now];
                let reason=if bar.low<candidate.origin.price{Some("secondary_origin_low_broken")}else if bar.high>candidate.high.price{Some("secondary_a_new_high")}
                    else if bar.low<candidate.low.price{Some("observed_b_low_broken")}else{None};
                if let Some(reason)=reason{let mut event=evidence(&candidate,bars);event["bar_index"]=json!(now);event["event"]=json!("secondary_pullback_invalidated");event["reason"]=json!(reason);self.events.push(event);
                    if reason!="observed_b_low_broken"{self.retired.insert(candidate.episode());}else{self.invalidated.insert(candidate.observation());}continue;}
                if bar.high>=candidate.high.price||!entry_allowed{continue;}
                let previous=&bars[now-1];if previous.volume<=Decimal::ZERO||bar.volume<=previous.volume||bar.close<=candidate.low.price{continue;}
                let body=bar.close-bar.open;let span=bar.high-bar.low;let gap=bar.open>previous.high&&bar.low>previous.high;
                let body_reclaim=body*Decimal::from(50)>bar.open&&body*Decimal::TWO>=span;
                let gap_body=gap&&body*Decimal::from(100)>=bar.open*Decimal::from(3)&&body*Decimal::from(5)>=span*Decimal::from(3);
                if !body_reclaim&&!gap_body{continue;}
                let ceiling_index=(candidate.resistance.index..now).max_by_key(|&i|(bars[i].high,std::cmp::Reverse(i))).ok_or("pullback ceiling unavailable")?;
                let ceiling=candidate.resistance.price.max(bars[ceiling_index].high);let gap_reclaim=gap_body&&bar.open>ceiling;
                if bar.close<=ceiling||!(body_reclaim||gap_reclaim){continue;}
                let mut proof=evidence(&candidate,bars);
                let extra=json!({"bar_index":now,"reclaim_type":if gap_reclaim{"gap"}else{"body"},"confirmation_close":bar.close,
                    "reclaim_body_fraction":body/bar.open,"reclaim_body_range_fraction":if span>Decimal::ZERO{body/span}else{Decimal::ZERO},
                    "unfilled_gap":gap,"previous_volume":previous.volume,"breakout_volume":bar.volume,"breakout_volume_multiple":bar.volume/previous.volume,
                    "reclaim_ceiling":ceiling,"reclaim_ceiling_date":date_at(bars,ceiling_index),"gross_reward_risk":(candidate.high.price-bar.close)/(bar.close-candidate.low.price)});
                if let(Some(target),Some(extra))=(proof.as_object_mut(),extra.as_object()){target.extend(extra.clone());}
                self.proofs.insert(now,proof.clone());let mut event=proof.clone();event["event"]=json!("secondary_pullback_candidate");self.events.push(event);result=Some(proof);break;
            }
        }
        for candidate in candidates {
            if candidate.known_index()>now||self.retired.contains(&candidate.episode())||self.observed.contains(&candidate.observation())||self.invalidated.contains(&candidate.observation()){continue;}
            self.observed.insert(candidate.observation());let mut event=evidence(candidate,bars);event["bar_index"]=json!(now);event["event"]=json!("secondary_pullback_observed");self.events.push(event);
        }
        self.previous=candidates.to_vec();self.last=Some(now);Ok(result)
    }
}
