//! 二级压力、深回调和组合 A 的因果观察；候选仍须通过统一资格与量价仲裁。
pub mod combined;
pub mod pullback;
use super::{Direction,RuleCandle};
use crate::analysis::market_state::price_action::{observe_resistance,ShadowPolicy};
use rust_decimal::Decimal;
use serde::{Deserialize,Serialize};
use crate::indexed_json::{Value,json};
use std::collections::{BTreeMap,BTreeSet};

pub fn session_date(bar:&RuleCandle)->String {
    if let Ok(time)=chrono::DateTime::parse_from_rfc3339(&bar.timestamp){
        return (time.with_timezone(&chrono::Utc)+chrono::Duration::hours(8)).date_naive().to_string();
    }
    bar.timestamp.get(..10).unwrap_or(&bar.timestamp).to_string()
}
pub fn date_at(bars:&[RuleCandle],index:usize)->String {bars.get(index).map(session_date).unwrap_or_default()}
pub fn number(raw:&Value)->Option<Decimal>{match (raw).kind() {Some(crate::indexed_json::Kind::String(s))=>s.parse().ok(),Some(crate::indexed_json::Kind::Number(n))=>n.to_string().parse().ok(),_=>None}}
fn resistance(previous:&RuleCandle,current:&RuleCandle)->Result<bool,String>{
    Ok(observe_resistance(previous,current,Direction::Up,Some(ShadowPolicy{minimum_range_fraction:Decimal::new(5,1)}))?.detected==Some(true))
}
#[derive(Debug,Clone,PartialEq,Eq,PartialOrd,Ord,Serialize,Deserialize)]
pub struct SecondaryHigh {pub index:usize,pub price:Decimal,pub known_index:usize}
#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct GeometryPoint {pub index:usize,pub ordinal:usize,pub price:Decimal,pub kind:String,pub known:usize,
    pub state:String,pub flip:Option<String>,pub confirmation_rule:Option<String>,pub broken_key:Option<Box<GeometryPoint>>,pub evidence:Value}
impl GeometryPoint {
    pub fn known(&self,bars:&[RuleCandle],kind:&str,now:usize,allow_seed:bool)->bool {
        self.kind==kind&&self.index<=self.known&&self.known<=now&&self.state!="developing"&&(allow_seed||self.state!="seed")
            &&bars.get(self.index).is_some_and(|b|self.price==if kind=="H"{b.high}else{b.low})
    }
}
#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct GeometryPath {pub id:String,pub source_path:Option<String>,pub source_paths:Vec<String>,pub points:Vec<GeometryPoint>}
pub fn read_geometry(value:&Value,bars:&[RuleCandle],now:usize)->Vec<GeometryPath> {
    let dates:BTreeMap<_,_>=bars.iter().enumerate().take(now+1).map(|(i,b)|(session_date(b),i)).collect();
    fn point(raw:&Value,dates:&BTreeMap<String,usize>,depth:usize)->Option<GeometryPoint>{
        if depth>2||raw.get("display_only").and_then(Value::as_bool)==Some(true){return None;}
        let index=usize::try_from(raw.get("index")?.as_u64()?).ok()?;
        let known=raw.get("available_at").or_else(||raw.get("known_index"))?;
        let known=known.as_u64().and_then(|n|usize::try_from(n).ok()).or_else(||dates.get(known.as_str()?).copied())?;
        let kind=raw.get("kind")?.as_str()?.to_string();if kind!="H"&&kind!="L"{return None;}
        Some(GeometryPoint{index,ordinal:raw.get("ordinal").and_then(Value::as_u64).and_then(|n|usize::try_from(n).ok()).unwrap_or(0),
            price:number(raw.get("value")?)?,kind,known,state:raw.get("state").and_then(Value::as_str).unwrap_or("confirmed").into(),
            flip:raw.get("flip").and_then(Value::as_str).map(str::to_string),confirmation_rule:raw.get("confirmation_rule").and_then(Value::as_str).map(str::to_string),
            broken_key:raw.get("broken_key").and_then(|v|point(v,dates,depth+1)).map(Box::new),evidence:raw.clone()})
    }
    value.get("strokes").and_then(Value::as_array).into_iter().flatten().filter_map(|path|{
        if path.get("display_only").and_then(Value::as_bool)==Some(true){return None;}
        Some(GeometryPath{id:path.get("id")?.as_str()?.into(),source_path:path.get("source_path").and_then(Value::as_str).map(str::to_string),
            source_paths:path.get("source_paths").and_then(Value::as_array).into_iter().flatten().filter_map(Value::as_str).map(str::to_string).collect(),
            points:path.get("points").and_then(Value::as_array).into_iter().flatten().filter_map(|p|point(p,&dates,0)).collect()})
    }).collect()
}
#[derive(Debug,Clone,Default,Serialize,Deserialize)]
pub struct SecondaryResistanceState {
    pub last:Option<usize>,pub observations:BTreeMap<usize,Value>,current_key:Option<(usize,Decimal)>,attack:Option<usize>,
    resisted:bool,record:Decimal,defense:Decimal,resolved_at:Option<usize>,resolved_high:Decimal,tracked_level:Option<SecondaryHigh>,
}
impl SecondaryResistanceState {
    pub fn advance(&mut self,bars:&[RuleCandle],index:usize,previous_highs:&[SecondaryHigh],chart_key:Option<Option<&SecondaryHigh>>,include_resolved:bool)->Result<Option<Value>,String>{
        if index>=bars.len()||self.last.is_some_and(|last|index!=last+1){return Err("secondary resistance requires consecutive visible bars".into());}
        self.last=Some(index);if index==0{return Ok(None);}
        let mut level=previous_highs.iter().filter(|p|p.known_index<index&&p.index<=p.known_index).next_back().cloned();
        if let Some(chart)=chart_key {
            if let Some(chart)=chart.filter(|p|p.known_index<index&&p.index<=p.known_index){if level.as_ref().is_none_or(|p|chart.price>p.price){level=Some(chart.clone());}}
        }
        if self.attack.is_some()&&self.resisted&&self.tracked_level.is_some(){level=self.tracked_level.clone();}
        let Some(level)=level else{self.current_key=None;self.attack=None;self.resisted=false;self.resolved_at=None;return Ok(None);};
        if self.current_key!=Some((level.index,level.price)){self.current_key=Some((level.index,level.price));self.attack=None;self.resisted=false;self.resolved_at=None;self.tracked_level=Some(level.clone());}
        let(bar,previous)=(&bars[index],&bars[index-1]);let upper=bar.high-bar.open.max(bar.close);
        let resisting=resistance(previous,bar)?||(upper>(bar.close-bar.open).abs()&&upper*Decimal::from(3)>=bar.high-bar.low&&upper>Decimal::ZERO);
        if self.attack.is_none()&&previous.high<=level.price&&level.price<bar.high{
            self.attack=Some(index);self.record=bar.high;self.defense=bar.low.min(previous.close);self.resisted=resisting;
        }
        let Some(attack)=self.attack else{return Ok(None);};
        if index==attack+1{self.resisted|=resisting;}
        let mut observation=None;
        if self.resisted {
            if index>=attack+2&&!resisting&&bar.close>bar.open&&bar.close>self.record&&bar.low>=self.defense{
                self.resisted=false;self.resolved_at=Some(index);self.resolved_high=self.record;
            }else{observation=Some(json!({"secondary_high_date":date_at(bars,level.index),"secondary_high":level.price,
                "secondary_attack_date":date_at(bars,attack),"secondary_resistance_high":self.record,
                "secondary_defense":self.defense,"secondary_current_resistance":resisting}));}
        }
        if include_resolved{if let Some(resolved)=self.resolved_at{observation=Some(json!({"secondary_resistance_resolved":true,
            "secondary_high_date":date_at(bars,level.index),"secondary_high":level.price,"secondary_attack_date":date_at(bars,attack),
            "secondary_resolution_date":date_at(bars,resolved),"secondary_resistance_high":self.resolved_high,
            "secondary_defense":self.defense,"secondary_confirmation_close":bars[resolved].close}));}}
        self.record=self.record.max(bar.high);if bar.low<self.defense{self.attack=None;self.resisted=false;self.resolved_at=None;}
        if let Some(value)=&observation{self.observations.insert(index,value.clone());}Ok(observation)
    }
}
#[derive(Debug,Clone,Serialize,Deserialize)]
struct ReclaimEpisode {key:SecondaryHigh,attack:usize,origin:usize,resistance:bool,ceiling:Decimal,ceiling_index:usize,bottom:usize,observed_peak:Decimal}
#[derive(Debug,Clone,Default,Serialize,Deserialize)]
pub struct SecondaryReclaimState {pub last:Option<usize>,pub events:Vec<Value>,pub proofs:BTreeMap<usize,Value>,
    episode:Option<ReclaimEpisode>,attacked:BTreeSet<SecondaryHigh>}
impl SecondaryReclaimState {
    pub fn advance(&mut self,bars:&[RuleCandle],now:usize,previous_highs:&[SecondaryHigh],entry_allowed:bool)->Result<Option<Value>,String>{
        if now>=bars.len()||self.last.is_some_and(|i|now!=i+1){return Err("secondary reclaim requires consecutive prefix".into());}
        self.last=Some(now);if now==0{return Ok(None);}
        let(bar,previous)=(&bars[now],&bars[now-1]);
        let key=previous_highs.iter().filter(|p|p.index<=p.known_index&&p.known_index<now).max_by_key(|p|(p.index,p.known_index));
        if self.episode.as_ref().is_some_and(|e|bar.low<bars[e.origin].low){
            if let Some(episode)=self.episode.take(){self.events.push(json!({"bar_index":now,"event":"secondary_reclaim_invalidated","secondary_attack_index":episode.attack,"reason":"secondary_origin_low_broken"}));}
        }
        if self.episode.is_none(){if let Some(key)=key.filter(|key|!self.attacked.contains(*key)&&previous.high<=key.price&&key.price<bar.high){
            self.attacked.insert(key.clone());
            if key.index+1>=now{return Ok(None);}
            let origin=(key.index+1..now).min_by_key(|&i|(bars[i].low,i)).ok_or("secondary origin unavailable")?;
            if bars[origin].low>=key.price||bar.low<bars[origin].low{return Ok(None);}
            self.episode=Some(ReclaimEpisode{key:key.clone(),attack:now,origin,resistance:resistance(previous,bar)?,ceiling:bar.high,ceiling_index:now,bottom:now,observed_peak:bar.high});
            self.events.push(json!({"bar_index":now,"event":"secondary_reclaim_breakout_observed","secondary_high_date":date_at(bars,key.index),"secondary_high":key.price,"secondary_high_known_index":key.known_index,"secondary_attack_index":now}));
        }}
        let Some(episode)=self.episode.as_mut()else{return Ok(None);};
        if bar.low<bars[episode.bottom].low{episode.bottom=now;}episode.observed_peak=episode.observed_peak.max(bar.high);
        if now==episode.attack+1{
            episode.resistance|=resistance(previous,bar)?;
            if bar.high>episode.ceiling{episode.ceiling=bar.high;episode.ceiling_index=now;}
            if !episode.resistance{self.episode=None;return Ok(None);}
            self.events.push(json!({"bar_index":now,"event":"secondary_reclaim_resistance_observed","secondary_attack_index":episode.attack,"secondary_resistance_high":episode.ceiling,"secondary_resistance_known_index":now}));
        }
        if now<episode.attack+2||!episode.resistance||!entry_allowed{return Ok(None);}
        let body=bar.close-bar.open;let span=bar.high-bar.low;
        let body_reclaim=body*Decimal::from(50)>bar.open&&bar.close>episode.ceiling;
        let gap=bar.open>previous.high&&bar.low>previous.high&&bar.open>episode.ceiling&&bar.close>episode.ceiling
            &&body*Decimal::from(100)>=bar.open*Decimal::from(3)&&body*Decimal::from(5)>=span*Decimal::from(3);
        if previous.volume<=Decimal::ZERO||bar.volume<=previous.volume||!(body_reclaim||gap){return Ok(None);}
        let proof=json!({"definition":"secondary_breakout_resistance_reclaim_v1","buy_point_type":"secondary_resistance_reclaim","trend_level":2,
            "secondary_high_date":date_at(bars,episode.key.index),"secondary_high":episode.key.price,"secondary_high_known_date":date_at(bars,episode.key.known_index),
            "secondary_attack_index":episode.attack,"secondary_attack_date":date_at(bars,episode.attack),
            "secondary_resistance_date":date_at(bars,episode.ceiling_index),"secondary_resistance_high":episode.ceiling,"secondary_resistance_known_date":date_at(bars,episode.attack+1),
            "secondary_origin_date":date_at(bars,episode.origin),"secondary_origin_low":bars[episode.origin].low,
            "secondary_pullback_low_date":date_at(bars,episode.bottom),"secondary_pullback_low":bars[episode.bottom].low,
            "secondary_reclaim_type":if gap{"gap"}else{"body"},"secondary_reclaim_body_fraction":body/bar.open,
            "secondary_reclaim_body_range_fraction":if span>Decimal::ZERO{body/span}else{Decimal::ZERO},
            "secondary_reclaim_unfilled_gap":bar.open>previous.high&&bar.low>previous.high,"secondary_reclaim_close":bar.close,
            "secondary_observed_peak":episode.observed_peak,"previous_volume":previous.volume,"breakout_volume":bar.volume,
            "breakout_volume_multiple":bar.volume/previous.volume,"stop":bars[episode.bottom].low,
            "counter_ratio":(episode.ceiling-bars[episode.bottom].low)/(episode.ceiling-bars[episode.origin].low)});
        self.proofs.insert(now,proof.clone());let mut event=proof.clone();event["bar_index"]=json!(now);event["event"]=json!("secondary_reclaim_candidate");self.events.push(event);Ok(Some(proof))
    }
}

#[derive(Debug,Clone,Default,Serialize,Deserialize)]
pub struct SecondaryDayState {pub resistance:SecondaryResistanceState,pub reclaim:SecondaryReclaimState,
    pub pullback:pullback::SecondaryPullbackState,pub combined:combined::CombinedAState,
    previous_highs:Vec<SecondaryHigh>,previous_key:Option<SecondaryHigh>}
impl SecondaryDayState{
    pub fn pressure_at(&self,index:usize)->Option<Value>{self.resistance.observations.get(&index).cloned()}
    /// 分钟只投影昨日冻结压力到当前已观察K线，不提交当日状态或重新计算趋势。
    pub fn preview_pressure(&self,bars:&[RuleCandle],index:usize)->Result<Option<Value>,String>{
        if self.resistance.last==Some(index){return Ok(self.pressure_at(index));}
        let mut frozen=self.resistance.clone();frozen.advance(bars,index,&self.previous_highs,Some(self.previous_key.as_ref()),true)
    }
}
#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct SecondaryDayEvaluation {pub pressure:Option<Value>,pub reclaim:Option<Value>,pub pullback:Option<Value>,pub combined:Option<Value>,pub events:Vec<Value>}
pub fn evaluate_secondary_day(bars:&[RuleCandle],frames:&[crate::analysis::kernel::KernelFrame],index:usize,audit:&[Value],entry_allowed:bool,
    state:&mut SecondaryDayState,config:&super::config::TradingDayConfig)->Result<SecondaryDayEvaluation,String>{
    if index>=bars.len(){return Err("secondary market prefix unavailable".into());}
    let frame=|level:u8|frames.iter().find(|f|f.level==level).ok_or("missing shared hierarchy level");
    let geometry=|level:u8|->Result<Vec<GeometryPath>,String>{Ok(read_geometry(&crate::analysis::kernel::geometry(frame(level)?),bars,index))};
    let drawing=geometry(0)?;let first=geometry(1)?;let second=geometry(2)?;let third=geometry(3)?;
    let pressure=state.resistance.advance(bars,index,&state.previous_highs,Some(state.previous_key.as_ref()),true)?;
    let start_reclaim=state.reclaim.events.len();let start_pullback=state.pullback.events.len();let start_combined=state.combined.events.len();
    let reclaim=if config.secondary_reclaim_entry_enabled{state.reclaim.advance(bars,index,&state.previous_highs,entry_allowed)?}else{None};
    let pullback=if config.secondary_pullback_entry_enabled{
        let candidates=pullback::secondary_pullback_candidates(bars,&drawing,&first,&second,index);
        state.pullback.advance(bars,index,&candidates,entry_allowed)?
    }else{None};
    let combined=if config.combined_a_entry_enabled{
        let contexts=combined::combined_a_contexts_from_geometry(bars,&drawing,&first,&second,&third,index,audit)?;
        state.combined.advance(bars,index,&contexts,entry_allowed)?
    }else{None};
    state.previous_highs=second.iter().flat_map(|p|p.points.iter()).filter(|p|p.known(bars,"H",index,true))
        .map(|p|SecondaryHigh{index:p.index,price:p.price,known_index:p.known}).collect();
    state.previous_highs.sort_by_key(|p|(p.index,p.known_index));
    state.previous_key=frame(2)?.key.as_ref().filter(|p|p.kind==crate::analysis::kernel::PointKind::H&&p.known_index<=index)
        .and_then(|p|p.value.to_string().parse().ok().map(|price|SecondaryHigh{index:p.index,price,known_index:p.known_index}));
    let events=state.reclaim.events[start_reclaim..].iter().chain(&state.pullback.events[start_pullback..]).chain(&state.combined.events[start_combined..]).cloned().collect();
    Ok(SecondaryDayEvaluation{pressure,reclaim,pullback,combined,events})
}

pub fn secondary_wave_recovery(bars:&[RuleCandle],now:usize,wave:&Value,pressure:&Value)->Option<Value>{
    if now>=bars.len()||!matches!(wave.get("wave_entry_path")?.as_str()?,"two_t_held_defense_gap_attack"|"two_t_strong_a_resistance_rebreak"|"one_p_held_defense_rebound")
        ||pressure.get("secondary_current_resistance").and_then(Value::as_bool)==Some(true)||pressure.get("secondary_resistance_resolved").and_then(Value::as_bool)==Some(true){return None;}
    let attack=pressure.get("secondary_attack_date")?.as_str()?;let n=wave.get("wave_entry_n_date")?.as_str()?;
    let a=wave.get("wave_a_high_date")?.as_str()?;let b=wave.get("wave_b_low_date")?.as_str()?;
    if !(n<=attack&&attack<=a&&a<b&&b<=session_date(&bars[now]).as_str()){return None;}
    let(high,record,defense)=(number(pressure.get("secondary_high")?)?,number(pressure.get("secondary_resistance_high")?)?,number(pressure.get("secondary_defense")?)?);
    let a_price=number(wave.get("wave_a_high")?)?;
    if record>a_price||high>a_price||number(wave.get("wave_b_low")?)?<defense||bars[now].close<=high{return None;}
    Some(json!({"wave_secondary_recovery":"completed_a_defended_b_gap_attack","wave_recovered_secondary_attack_date":attack,
        "wave_recovered_secondary_high":high,"wave_recovered_secondary_record":record,"wave_recovered_secondary_defense":defense}))
}
pub fn inverse_wave_recovery(bars:&[RuleCandle],now:usize,wave:&Value,inverse:&[Value])->Option<Value>{
    use super::qualification::index;
    if now>=bars.len()||!matches!(wave.get("wave_entry_path")?.as_str()?,"two_t_held_defense_gap_attack"|"two_t_strong_a_resistance_rebreak"|"one_p_held_defense_rebound"){return None;}
    let last=inverse.iter().filter(|e|index(e,"known_at").is_some_and(|i|i<=now)).filter_map(|e|Some(((index(e,"known_at")?,index(e,"attack")?,number(e.get("b_high")?)?),e))).max_by_key(|(key,_)|*key)?.1;
    let(a,b,attack,low,known)=(index(wave,"wave_a_high_index")?,index(last,"b_index")?,index(last,"attack")?,index(wave,"wave_b_low_index")?,index(last,"known_at")?);
    let high=number(last.get("b_high")?)?;
    if !(a<b&&b<attack&&attack<=low&&low<=now&&attack<=known&&known<now&&high<=number(wave.get("wave_a_high")?)?){return None;}
    if wave.get("wave_a_class").and_then(Value::as_str)==Some("ordinary")&&bars[now].close<=high{return None;}
    Some(json!({"inverse_reentry_path":"completed_a_defended_b_gap_attack","recovery_inverse_date":date_at(bars,attack),
        "recovery_inverse_known_date":date_at(bars,known),"recovery_inverse_b_date":date_at(bars,b),"recovery_previous_b_high":high}))
}
