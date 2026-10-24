//! 首N轮次拥有权、独立新N确认与反N后的因果再入场规则。
use super::{RuleCandle,RuleDecision,qualification::{index,price,confirmed_alternation_event},entries::strong_bullish_candle};
use rust_decimal::Decimal;
use serde::{Serialize,Deserialize};
use crate::indexed_json::{Value,json};
use std::collections::BTreeSet;

#[derive(Debug,Clone,PartialEq,Eq,Serialize,Deserialize)]
pub struct SqueezeN{pub origin:usize,pub neckline:usize,pub pullback:usize,pub attack:usize,pub known_at:usize,pub defense:Decimal}
impl SqueezeN{pub fn identity(&self)->(usize,usize,usize,usize){(self.origin,self.neckline,self.pullback,self.attack)}}
pub fn joint_squeeze_confirmations(events:&[Value],level:u8)->BTreeSet<(String,usize,(usize,usize,usize,usize))>{
    let mut result=BTreeSet::new();for e in events{
        if index(e,"trend_level")!=Some(usize::from(level))||e.get("state").and_then(Value::as_str)!=Some("空多交替")
            ||!matches!(e.get("confirmation_rule").and_then(Value::as_str),Some("positive_n_and_squeeze_after_qualified_b"|"double_volume_positive_n_squeeze_whole_flip_pullback"))
            ||!confirmed_alternation_event(e){continue;}
        let(Some(path),Some(now),Some(proof))=(e.get("source_path").and_then(Value::as_str),index(e,"bar_index"),e.get("confirmation_evidence"))else{continue;};
        let(Some(a),Some(b),Some(c),Some(attack))=(index(proof,"n_origin"),index(proof,"n_neckline"),index(proof,"n_pullback"),index(proof,"attack"))else{continue;};
        if a<=b&&b<=c&&c<=attack&&attack<=now&&a<c&&proof.get("source_path").and_then(Value::as_str)==Some(path)
            &&index(proof,"trend_level")==Some(usize::from(level))&&index(proof,"bar_index")==Some(now)&&index(proof,"candidate_index")==Some(now){result.insert((path.into(),now,(a,b,c,attack)));}
    }result
}
#[derive(Debug,Default,Clone,Serialize,Deserialize)]
pub struct SqueezeNRound{pub owner:Option<SqueezeN>,pub selected_at:Option<usize>,pub emitted_at:Option<usize>,pub broken_at:Option<usize>}
impl SqueezeNRound{
    pub fn evidence(&self)->Value{json!({"squeeze_owner_origin":self.owner.as_ref().map(|n|n.origin),"squeeze_owner_neckline":self.owner.as_ref().map(|n|n.neckline),
        "squeeze_owner_pullback":self.owner.as_ref().map(|n|n.pullback),"squeeze_owner_attack":self.owner.as_ref().map(|n|n.attack),
        "squeeze_owner_known_at":self.owner.as_ref().map(|n|n.known_at),"squeeze_owner_defense":self.owner.as_ref().map(|n|n.defense),
        "squeeze_owner_selected_at":self.selected_at,"squeeze_owner_emitted_at":self.emitted_at,"squeeze_round_broken_at":self.broken_at.map_or(-1,|i|i as i64)})}
    pub fn before(events:&[Value],now:usize)->Result<Self,String>{
        let Some(e)=events.iter().enumerate().filter(|(_,e)|e.get("event").and_then(Value::as_str)==Some("squeeze_round_changed")&&index(e,"bar_index").is_some_and(|i|i<now))
            .max_by_key(|(order,e)|(index(e,"bar_index"),*order)).map(|(_,e)|e)else{return Ok(Self::default());};
        let owner=if let Some(attack)=index(e,"squeeze_owner_attack"){Some(SqueezeN{attack,origin:index(e,"squeeze_owner_origin").ok_or("owner origin missing")?,
            neckline:index(e,"squeeze_owner_neckline").ok_or("owner neckline missing")?,pullback:index(e,"squeeze_owner_pullback").ok_or("owner pullback missing")?,
            known_at:index(e,"squeeze_owner_known_at").ok_or("owner known time missing")?,defense:price(e,"squeeze_owner_defense").ok_or("owner defense missing")?})}else{None};
        Ok(Self{selected_at:if owner.is_some(){Some(index(e,"squeeze_owner_selected_at").ok_or("owner selection time missing")?)}else{None},
            owner,emitted_at:index(e,"squeeze_owner_emitted_at"),broken_at:index(e,"squeeze_round_broken_at")})
    }
    pub fn close_unemitted(&mut self)->Option<Value>{if self.emitted_at.is_some(){return None;}let prior=self.owner.take()?;self.selected_at=None;
        let mut e=self.evidence();e["reason"]=json!("unemitted_first_n_qualification_closed");e["retired_owner_attack"]=json!(prior.attack);Some(e)}
    pub fn observe_low(&mut self,index:usize,low:Decimal)->Option<Value>{let owner=self.owner.as_ref()?;
        if self.selected_at.is_none_or(|i|index<=i)||low>=owner.defense{return None;}let prior=owner.clone();let emitted=self.emitted_at;
        *self=Self{broken_at:Some(index),..Default::default()};let mut e=self.evidence();e["reason"]=json!("first_n_squeeze_defense_broken");
        e["broken_owner_attack"]=json!(prior.attack);e["broken_owner_defense"]=json!(prior.defense);e["broken_owner_emitted_at"]=json!(emitted);e["observed_low"]=json!(low);Some(e)}
    pub fn select(&mut self,index:usize,candidates:&[SqueezeN])->Option<Value>{if self.owner.is_some(){return None;}
        let owner=candidates.iter().filter(|n|self.broken_at.is_none_or(|i|n.attack>i&&n.pullback>=i)&&n.attack<=index&&n.known_at<=index)
            .min_by_key(|n|(n.attack.max(n.known_at),n.attack,n.identity()))?;
        self.owner=Some(owner.clone());self.selected_at=Some(index);let mut e=self.evidence();e["reason"]=json!("first_n_selected");Some(e)}
    pub fn rejection(&self,candidate:&SqueezeN)->Option<&'static str>{let Some(owner)=&self.owner else{return Some("squeeze_waiting_for_new_n_after_defense_break");};
        if self.emitted_at.is_some(){Some("first_n_squeeze_already_emitted")}else if candidate.identity()!=owner.identity(){Some("squeeze_requires_first_n")}else{None}}
    pub fn consume(&mut self,candidate:&SqueezeN,index:usize)->Result<Value,String>{if self.rejection(candidate).is_some(){return Err("only unconsumed first N can emit squeeze entry".into());}
        self.emitted_at=Some(index);let mut e=self.evidence();e["reason"]=json!("first_n_squeeze_emitted");Ok(e)}
}
pub fn n_record_reconfirmation(bars:&[RuleCandle],attack:usize,defense:Decimal,now:usize,new_origin:usize,new_neckline:usize,new_pullback:usize,new_known:usize)->Option<Value>{
    if !(new_origin<=attack&&attack<new_neckline&&new_neckline<new_pullback&&new_pullback<now&&now<bars.len()&&new_known<=now)
        ||bars[attack+1..=now].iter().any(|b|b.low<defense){return None;}
    let record=bars[attack..now].iter().map(|b|b.high).max()?;let bar=&bars[now];if bar.close<=record||bar.close<=bar.open{return None;}
    Some(json!({"squeeze_confirmation":"fresh_n_defeats_old_n_resistance","reconfirmed_by_n":now,"reconfirmed_n_date":bar.timestamp,
        "confirmation_record_high":record,"retained_n_defense":defense}))
}
pub fn consolidation_gap(bars:&[RuleCandle],attack:usize,now:usize,defense:Decimal)->Option<Value>{
    if now<3||attack>=now-2||now>=bars.len()||bars[attack+1..=now].iter().any(|b|b.low<defense){return None;}
    let(original,bar,previous)=(&bars[attack],&bars[now],&bars[now-1]);
    if !(bar.low>previous.high&&bar.open>previous.high&&bar.close>original.high&&bar.close>bar.open&&bar.volume>previous.volume
        &&bars[now-2].close<=original.high&&previous.close<=original.high){return None;}
    Some(json!({"consolidation_n_date":original.timestamp,"consolidation_high":original.high,"consolidation_defense":defense,
        "gap_previous_high":previous.high,"gap_low":bar.low,"gap_volume":bar.volume,"gap_previous_volume":previous.volume}))
}
#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct InverseEpisode{pub known_at:usize,pub attack:usize,pub b_index:usize,pub b_high:Decimal,pub kill_high:Option<Decimal>}
fn latest(inverse:&[InverseEpisode],now:usize)->Option<&InverseEpisode>{inverse.iter().filter(|e|e.known_at<=now).max_by_key(|e|(e.known_at,e.attack,e.b_high))}
pub fn inverse_reentry_rejection(bars:&[RuleCandle],now:usize,attack:usize,inverse:&[InverseEpisode],gap:bool)->Option<RuleDecision>{
    let last=latest(inverse,now)?;let bar=bars.get(now)?;
    if now>last.known_at&&bar.close>last.b_high&&(attack>last.known_at||gap){return None;}
    Some(RuleDecision::new("inverse_n_requires_new_attack_above_b_high",now,Decimal::ZERO).with("inverse_n_date",&bars.get(last.attack)?.timestamp)
        .with("inverse_known_date",&bars.get(last.known_at)?.timestamp).with("inverse_b_date",&bars.get(last.b_index)?.timestamp)
        .with("inverse_b_high",last.b_high).with("confirmation_close",bar.close))
}
pub fn fresh_strong_squeeze_recovery(bars:&[RuleCandle],now:usize,attack:usize,origin:usize,inverse:&[InverseEpisode],strong:bool)->Option<Value>{
    let last=latest(inverse,now)?;let kill=last.kill_high?;
    if !strong||!(last.known_at<origin&&origin<attack&&attack<now&&now<bars.len())||bars[now].close<=kill{return None;}
    Some(json!({"inverse_reentry_path":"fresh_n_uninterrupted_strong_squeeze","recovery_inverse_date":bars[last.attack].timestamp,
        "recovery_kill_high":kill,"recovery_previous_b_high":last.b_high}))
}
pub fn deep_pullback_recovery(bars:&[RuleCandle],now:usize,attack:usize,inverse:&[InverseEpisode],alternation:&Value,record_break:bool,attack_bar_break:bool)->Option<Value>{
    let last=latest(inverse,now)?;if !(record_break||attack_bar_break)||alternation.get("definition").and_then(Value::as_str)!=Some("whole_flip_wave_v3")
        ||!matches!(index(alternation,"trend_level"),Some(2|3)){return None;}
    let(origin,peak,low,confirmed)=(index(alternation,"origin_index")?,index(alternation,"flip_high_index")?,index(alternation,"alternation_low_index")?,index(alternation,"alternation_index")?);
    if !(origin<peak&&peak<last.attack&&last.attack<=low&&low<attack&&attack<=now&&now<bars.len()&&last.known_at<attack&&last.known_at<=confirmed&&confirmed<=now){return None;}
    if attack_bar_break&&!record_break&&!(now>=attack+2&&bars[now].high>bars[attack].high&&bars[now].close>bars[attack].close&&bars[now].close>bars[now-1].close&&strong_bullish_candle(&bars[now])){return None;}
    let(start,high,bottom)=(price(alternation,"origin_price")?,price(alternation,"flip_high_price")?,bars[low].low);
    if !(start<bottom&&bottom<high)||(high-bottom)*Decimal::from(3)<(high-start)*Decimal::TWO{return None;}
    let close=bars[peak+1..=low].iter().map(|b|b.close).min()?;
    if (close-high)*Decimal::from(3)+(high-start)*Decimal::TWO>Decimal::ZERO&&(low-peak)*2<peak-origin{return None;}
    if bars[low..=now].iter().any(|b|b.low<bottom){return None;}let kill=last.kill_high?;if bars[now].close<=kill{return None;}
    Some(json!({"inverse_reentry_path":if record_break{"deep_alternation_kill_high_record_squeeze"}else{"deep_alternation_kill_high_attack_bar_squeeze"},
        "recovery_inverse_date":bars[last.attack].timestamp,"recovery_kill_high":kill,"recovery_previous_b_high":last.b_high,"recovery_whole_retracement":(high-bottom)/(high-start)}))
}
