//! hierarchical_entry、multilevel_squeeze、nested_alternation_breakout 的因果入场规则。
//! 上下文只消费统一内核正式生命周期与资格账本；交易消费者不重画趋势。
use super::{Direction,NObservation,NSetup,RuleCandle,qualification::{self,TradingEligibility}};
use super::super::market_state::regime::RegimeFrame;
use rust_decimal::Decimal;
use serde::{Deserialize,Serialize};
use crate::indexed_json::{Value,json};
use std::collections::{BTreeMap,BTreeSet};

#[derive(Debug,Clone,PartialEq,Serialize,Deserialize)]
pub struct EntryContext {
    pub trend_level:u8,pub epoch:usize,pub context_index:usize,pub key_source_index:usize,pub key_price:Decimal,
    pub flip_index:usize,pub flip_high_index:usize,pub flip_high_price:Decimal,pub origin_index:usize,pub origin_price:Decimal,
    pub alternation_index:usize,pub alternation_low_index:usize,pub alternation_low_price:Decimal,
    pub maturity_index:Option<usize>,pub confirmation_attack:Option<usize>,pub source_path:String,
}
impl EntryContext {pub fn episode(&self)->(u8,usize,usize,usize,usize) {
    (self.trend_level,self.epoch,self.context_index,self.flip_index,self.alternation_index)
}}
#[derive(Debug,Clone,Default,Serialize,Deserialize)]
pub struct ContextSnapshot {pub contexts:Vec<EntryContext>,pub events:Vec<Value>,pub unavailable_rules:Vec<String>}
fn text<'a>(value:&'a Value,field:&str)->Option<&'a str>{value.get(field)?.as_str()}
fn epoch(path:&str)->Option<usize>{path.rsplit('-').next()?.parse().ok()}
fn context_from_proof(proof:&Value,source_path:&str,level:u8)->Option<EntryContext> {
    if !qualification::confirmed_alternation_event(proof) || text(proof,"state")!=Some("空多交替") {return None;}
    let context=EntryContext {trend_level:level,epoch:epoch(source_path)?,
        context_index:qualification::index(proof,"origin_index")?,key_source_index:qualification::index(proof,"broken_key_index")?,
        key_price:qualification::price(proof,"broken_key_price")?,
        flip_index:qualification::index(proof,"source_known_index").or_else(||qualification::index(proof,"flip_known_index"))?,
        flip_high_index:qualification::index(proof,"flip_index")?,flip_high_price:qualification::price(proof,"breakout_level")?,
        origin_index:qualification::index(proof,"origin_index")?,origin_price:qualification::price(proof,"origin_price")?,
        alternation_index:qualification::index(proof,"alternation_index")?,alternation_low_index:qualification::index(proof,"counter_index")?,
        alternation_low_price:qualification::price(proof,"counter_price")?,maturity_index:None,
        confirmation_attack:proof.get("confirmation_evidence").and_then(|e|qualification::index(e,"attack_index").or_else(||qualification::index(e,"attack"))),source_path:source_path.into()};
    (context.key_source_index<context.origin_index && context.origin_index<context.flip_high_index
        && context.flip_high_index<context.alternation_low_index && context.alternation_low_index<=context.alternation_index
        && context.flip_high_index<=context.flip_index && context.flip_index<=context.alternation_index
        && Decimal::ZERO<context.origin_price && context.origin_price<=context.alternation_low_price
        && context.alternation_low_price<context.flip_high_price).then_some(context)
}
/// 可按任意过去时点投影同一本资格账本；之后才成熟的证据不会进入 attack-time 上下文。
pub fn contexts_from_eligibilities(eligibilities:&[TradingEligibility],events:&[Value],bars:&[RuleCandle],now:usize)
    ->Result<ContextSnapshot,String> {
    if now>=bars.len(){return Err("hierarchy context requires an available market prefix".into());}
    let mut result=ContextSnapshot::default();
    for eligibility in eligibilities.iter().filter(|state|(1..=3).contains(&state.level)) {
        for window in eligibility.intervals.iter().filter(|window|window.start_index<=now && window.end_index.is_none_or(|end|now<end)) {
            let proof=eligibility.events.iter().chain(events).find(|event|qualification::index(event,"bar_index")==Some(window.start_index)
                && qualification::index(event,"trend_level")==Some(usize::from(window.trend_level))
                && text(event,"source_path")==Some(window.source_path.as_str())
                && qualification::confirmed_alternation_event(event) && text(event,"state")==Some("空多交替"));
            let Some(mut context)=proof.and_then(|proof|context_from_proof(proof,&window.source_path,window.trend_level))else{
                result.unavailable_rules.push(format!("hierarchy_context_missing_frozen_fields:{}:{}",window.trend_level,window.start_index));continue;
            };
            if context.alternation_index>now || context.origin_index>=bars.len(){continue;}
            if bars[context.origin_index..=now].iter().any(|bar|bar.low<context.origin_price) {continue;}
            context.maturity_index=(context.alternation_index+1..=now).find(|index|bars[*index].close>context.flip_high_price);
            if context.alternation_index==now {result.events.push(json!({"event":"hierarchy_alternation_ready","bar_index":now,"context":context}));}
            if context.maturity_index==Some(now){result.events.push(json!({"event":"hierarchy_bull_matured","bar_index":now,"context":context}));}
            result.contexts.push(context);
        }
    }
    result.contexts.sort_by_key(EntryContext::episode);result.contexts.dedup_by(|left,right|left.episode()==right.episode());Ok(result)
}

#[derive(Debug,Clone)]
pub struct HierarchyCandidate<'a> {
    pub setup:&'a NSetup,pub observation:&'a NObservation,pub known_at:usize,pub epoch:usize,
    pub source_path:&'a str,pub qualification_window_start:usize,
}
impl<'a> From<&'a super::candidates::PreparedN> for HierarchyCandidate<'a> {
    fn from(value:&'a super::candidates::PreparedN)->Self {Self {setup:&value.measurement.setup,
        observation:&value.measurement.observation,known_at:value.known_at(),epoch:value.epoch,
        source_path:&value.measurement.source.source_path,qualification_window_start:value.measurement.source.window_start}}
}
impl HierarchyCandidate<'_> {
    pub fn attack(&self)->Option<usize>{self.observation.completion.as_ref().map(|completion|completion.bar_index)}
    pub fn counter_ratio(&self)->Option<Decimal>{self.observation.anchors.as_ref().map(|anchors|anchors.retracement_ratio)}
}
#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct EntrySelection {pub entry:Option<Value>,pub reason:String}
fn payload(context:&EntryContext,kind:&str,priority:u8,ratio:Decimal,filter:bool)->Result<Value,String>{
    let mut result=crate::indexed_json::to_value(context).map_err(|error|error.to_string())?;
    result["buy_point_type"]=json!(kind);result["priority"]=json!(priority);result["counter_filter_applied"]=json!(filter);
    result["counter_ratio"]=json!(ratio);Ok(result)
}
/// hierarchical_entry.select_entry：成熟后不得回退第一买点，第一买点不得借后来证据复活旧 N。
pub fn select_entry(current:&[EntryContext],at_attack:&[EntryContext],candidate:&HierarchyCandidate<'_>,
    asof:usize,shallow_ratio:Decimal,buy_interval_level:Option<u8>)->Result<EntrySelection,String> {
    if buy_interval_level.is_some_and(|level|!(1..=3).contains(&level)) || shallow_ratio<=Decimal::ZERO || shallow_ratio>Decimal::ONE {
        return Err("hierarchy entry requires level 1..3 and a valid shallow threshold".into());
    }
    let (Some(attack),Some(ratio))=(candidate.attack(),candidate.counter_ratio())else{return Ok(EntrySelection{entry:None,reason:"completed_n_unavailable".into()});};
    if candidate.setup.direction!=Direction::Up || candidate.known_at>asof || attack>asof {return Ok(EntrySelection{entry:None,reason:"n_not_yet_available".into()});}
    let live:BTreeMap<_,_>=current.iter().filter(|context|buy_interval_level.is_none_or(|level|context.trend_level==level))
        .map(|context|(context.episode(),context)).collect();
    let mut choices:Vec<(u8,u8,Value)>=Vec::new();let mut reasons=Vec::new();
    for context in at_attack.iter().filter(|context|buy_interval_level.is_none_or(|level|context.trend_level==level)) {
        let Some(current)=live.get(&context.episode())else{continue;};
        if !(context.flip_index<=context.alternation_index && context.alternation_index<attack) {continue;}
        if context.maturity_index.is_none_or(|known|known>=attack) {
            if !matches!(context.trend_level,2|3) && buy_interval_level!=Some(1){reasons.push("first_buy_requires_level_two_or_three");continue;}
            if current.maturity_index.is_some_and(|known|known<asof){reasons.push("early_n_expired_after_maturity");continue;}
            choices.push((1,context.trend_level,payload(context,"transition_squeeze",1,ratio,false)?));
        }else{
            let maturity=context.maturity_index.ok_or("mature context lost its clock")?;
            let setup=candidate.setup;
            if !(setup.origin.index>=context.alternation_low_index && setup.neckline.index>=maturity
                && setup.pullback.index>setup.neckline.index && setup.pullback.index>maturity) {reasons.push("mature_pullback_sequence_not_ready");continue;}
            if ratio<Decimal::ZERO || ratio>=shallow_ratio {reasons.push("mature_pullback_not_shallow");continue;}
            let mut entry=payload(context,"mature_shallow_squeeze",2,ratio,true)?;
            entry["counter_limit"]=json!(shallow_ratio);entry["pullback_index"]=json!(setup.pullback.index);
            entry["impulse_origin_index"]=json!(setup.origin.index);entry["impulse_high_index"]=json!(setup.neckline.index);
            choices.push((2,context.trend_level,entry));
        }
    }
    // Python max 在同优先级保留首个来源；不让后来的同价证据换掉身份。
    let selected=choices.into_iter().reduce(|best,next|if (next.0,next.1)>(best.0,best.1){next}else{best});
    Ok(match selected {Some((_,_,entry))=>EntrySelection{entry:Some(entry),reason:String::new()},
        None=>EntrySelection{entry:None,reason:reasons.first().copied().unwrap_or("hierarchy_transition_not_ready").into()}})
}

#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct ResistanceKey {pub index:usize,pub value:Decimal,pub known_at:usize,pub trend_level:u8,pub position:usize}
#[derive(Debug,Clone,Default,Serialize,Deserialize)]
pub struct MultilevelResistanceIndex {pub keys:Vec<ResistanceKey>}
impl MultilevelResistanceIndex {
    pub fn from_events(events:&[Value])->Self {
        let mut keys=Vec::new();
        for (position,event) in events.iter().enumerate() {
            if text(event,"event")!=Some("hierarchy_resistance_key"){continue;}
            let (Some(level),Some(known),Some(key))=(qualification::index(event,"trend_level"),qualification::index(event,"bar_index"),event.get("key"))else{continue;};
            if !matches!(level,2|3){continue;}
            let (Some(index),Some(value))=(qualification::index(key,"index"),qualification::price(key,"value"))else{continue;};
            if index<=known && value>Decimal::ZERO {keys.push(ResistanceKey{index,value,known_at:known,trend_level:level as u8,position});}
        }
        keys.sort_by_key(|key|key.index);Self{keys}
    }
    pub fn select(&self,origin:usize,attack:usize)->Option<&ResistanceKey>{
        self.keys.iter().filter(|key|origin<key.index && key.index<attack && key.known_at<attack)
            .reduce(|best,next|if (next.value,next.known_at,std::cmp::Reverse(next.position))>
                (best.value,best.known_at,std::cmp::Reverse(best.position)){next}else{best})
    }
}
/// multilevel_squeeze：N 颈线必须就是先已知的二/三级最高阻力，而非任意高点。
pub fn multilevel_squeeze(bars:&[RuleCandle],candidate:&HierarchyCandidate<'_>,frame:&RegimeFrame,
    keys:&MultilevelResistanceIndex)->Option<Value> {
    let attack=candidate.attack()?;let now=frame.bar_index;
    if candidate.setup.direction!=Direction::Up || now>=bars.len() || now<attack+2 || candidate.known_at>now
        || frame.first_defense_breach_index.is_some(){return None;}
    let key=keys.select(candidate.setup.origin.index,attack)?;
    if key.index!=candidate.setup.neckline.index || attack==0
        || !(bars[attack-1].high<=key.value && key.value<bars[attack].close) {return None;}
    let (bar,previous)=(&bars[now],&bars[now-1]);
    let record=bars[attack..now].iter().map(|bar|bar.high).max()?;
    if !(bar.open>=previous.close && bar.close>bar.open && bar.close>record
        && bar.low>=previous.low.min(bars[now-2].close) && bar.volume>previous.volume){return None;}
    Some(json!({"buy_point_type":"multilevel_breakout_squeeze","definition":"multilevel_breakout_squeeze_v1","priority":1,
        "trend_level":key.trend_level,"key_source_index":key.index,"key_price":key.value,"key_known_index":key.known_at,
        "origin_index":candidate.setup.origin.index,"pullback_index":candidate.setup.pullback.index,
        "counter_ratio":candidate.counter_ratio()?,"counter_filter_applied":false,"higher_breakout_index":attack,
        "higher_confirmation_index":now,"higher_resistance_high":record,"higher_defense":candidate.observation.completion.as_ref()?.defense,
        "confirmation_close":bar.close,"confirmation_low":bar.low,"confirmation_volume":bar.volume,"previous_volume":previous.volume}))
}

/// nested_alternation_breakout：两层已知低点均持续守住，当日放量实体突破共同阻力。
pub fn nested_alternation_breakout(bars:&[RuleCandle],before:&[EntryContext],current:&[EntryContext],
    now:usize,origin:usize,pullback:usize)->Result<Option<Value>,String> {
    if now==0 || now>=bars.len(){return Ok(None);}
    let (bar,previous)=(&bars[now],&bars[now-1]);let body=bar.close-bar.open;let upper=bar.high-bar.open.max(bar.close);
    if body<=Decimal::ZERO || previous.volume<=Decimal::ZERO || bar.volume<=previous.volume
        || upper>Decimal::ZERO && upper>=body && upper*Decimal::from(3)>=bar.high-bar.low {return Ok(None);}
    let live:BTreeSet<_>=current.iter().map(EntryContext::episode).collect();
    let mut primaries:Vec<_>=before.iter().filter(|context|context.trend_level==1 && live.contains(&context.episode())
        && context.origin_index==origin && context.alternation_low_index==pullback).collect();
    let mut secondaries:Vec<_>=before.iter().filter(|context|context.trend_level==2 && live.contains(&context.episode())).collect();
    primaries.sort_by_key(|context|std::cmp::Reverse(context.alternation_index));
    secondaries.sort_by_key(|context|std::cmp::Reverse(context.alternation_index));
    for primary in primaries {
        if !(primary.origin_index<primary.flip_high_index && primary.flip_high_index<primary.alternation_low_index
            && primary.alternation_low_index<=primary.alternation_index && primary.alternation_index<now){continue;}
        for secondary in &secondaries {
            if !(secondary.origin_index<secondary.flip_high_index && secondary.flip_high_index<secondary.alternation_low_index
                && secondary.alternation_low_index<=primary.origin_index && primary.origin_index<primary.alternation_low_index
                && secondary.flip_index<=secondary.alternation_index && secondary.alternation_index<primary.alternation_index
                && primary.flip_index<=primary.alternation_index && secondary.alternation_low_price<=primary.origin_price
                && primary.origin_price<primary.alternation_low_price && primary.alternation_low_price<primary.flip_high_price){continue;}
            if bars[secondary.alternation_low_index..=now].iter().any(|bar|bar.low<secondary.alternation_low_price)
                || bars[primary.alternation_low_index..=now].iter().any(|bar|bar.low<primary.alternation_low_price){continue;}
            let high=bars[primary.alternation_low_index..now].iter().map(|bar|bar.high).max().unwrap_or(primary.flip_high_price).max(primary.flip_high_price);
            if bar.close<=high {continue;}
            let ratio=(primary.flip_high_price-primary.alternation_low_price)/(primary.flip_high_price-primary.origin_price);
            let mut proof=payload(primary,"nested_alternation_breakout",3,ratio,false)?;
            proof["definition"]=json!("secondary_primary_alternation_breakout_v1");
            for (prefix,context) in [("secondary",*secondary),("primary",primary)] {
                proof[format!("{prefix}_low_index")]=json!(context.alternation_low_index);
                proof[format!("{prefix}_low_price")]=json!(context.alternation_low_price);
                proof[format!("{prefix}_known_index")]=json!(context.alternation_index);
                proof[format!("{prefix}_low_date")]=json!(bars[context.alternation_low_index].timestamp.get(..10).unwrap_or(&bars[context.alternation_low_index].timestamp));
                proof[format!("{prefix}_known_date")]=json!(bars[context.alternation_index].timestamp.get(..10).unwrap_or(&bars[context.alternation_index].timestamp));
            }
            proof["breakout_high"]=json!(high);proof["confirmation_close"]=json!(bar.close);proof["confirmation_low"]=json!(bar.low);
            proof["confirmation_volume"]=json!(bar.volume);proof["previous_volume"]=json!(previous.volume);return Ok(Some(proof));
        }
    }
    Ok(None)
}

#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct WaveEntryOptions {
    pub deep_ratio:Option<Decimal>,pub shallow_ratio:Decimal,pub first_basis:String,
    pub second_inclusive:bool,pub allow_confirming_n:bool,pub allow_same_bar_pullback:bool,pub buy_interval_level:Option<u8>,
}
impl WaveEntryOptions {
    pub fn from_config(config:&super::config::TradingDayConfig)->Self {Self {deep_ratio:config.first_pullback_threshold,
        shallow_ratio:config.mature_shallow_ratio,first_basis:config.first_pullback_basis.clone(),second_inclusive:config.mature_shallow_inclusive,
        allow_confirming_n:false,allow_same_bar_pullback:false,buy_interval_level:(config.buy_interval_level>0).then_some(config.buy_interval_level)}}
}
fn fraction_threshold(value:Decimal)->Option<(Decimal,Decimal)> {
    if value==Decimal::ONE/Decimal::from(3){Some((Decimal::ONE,Decimal::from(3)))}
    else if value==Decimal::new(5,1){Some((Decimal::ONE,Decimal::TWO))}
    else if value==Decimal::TWO/Decimal::from(3){Some((Decimal::TWO,Decimal::from(3)))}else{None}
}
fn exact_ratio(numerator:Decimal,denominator:Decimal)->Result<String,String> {
    let scale=numerator.scale().max(denominator.scale());
    let aligned=|value:Decimal|10_i128.checked_pow(scale-value.scale()).and_then(|factor|value.mantissa().checked_mul(factor));
    let mut a=aligned(numerator).ok_or("exact ratio numerator exceeds integer range")?;
    let mut b=aligned(denominator).ok_or("exact ratio denominator exceeds integer range")?;
    if b<=0{return Err("exact ratio requires a positive denominator".into());}
    let (mut left,mut right)=(a.unsigned_abs(),b.unsigned_abs());
    while right!=0 {let rest=left%right;left=right;right=rest;}
    let divisor=i128::try_from(left).map_err(|_|"exact ratio gcd exceeds integer range")?;
    a/=divisor;b/=divisor;
    Ok(if b==1{a.to_string()}else{format!("{a}/{b}")})
}
pub fn wave_entry_contexts(current:&[EntryContext],at_attack:&[EntryContext],attack:usize,asof:usize,allow_confirming_n:bool)->Vec<EntryContext> {
    let mut candidates=at_attack.to_vec();
    if allow_confirming_n {for context in current {
        if context.confirmation_attack==Some(attack) && attack<context.alternation_index && context.alternation_index<=asof
            && !candidates.contains(context){candidates.push(context.clone());}
    }} candidates
}
/// whole_wave_entry.select_wave_entry：全翻向波幅，不以局部 N 回调比替代第一/第二买点门槛。
pub fn select_wave_entry(current:&[EntryContext],at_attack:&[EntryContext],bars:&[RuleCandle],candidate:&HierarchyCandidate<'_>,
    asof:usize,options:&WaveEntryOptions)->Result<EntrySelection,String> {
    let Some(attack)=candidate.attack()else{return Ok(EntrySelection{entry:None,reason:"wave_invalid_n_sequence".into()});};
    let low=candidate.setup.pullback.index;
    if !(low<=attack && attack<=asof && asof<bars.len() && (low<attack || options.allow_same_bar_pullback)) {
        return Ok(EntrySelection{entry:None,reason:"wave_invalid_n_sequence".into()});
    }
    if candidate.setup.direction!=Direction::Up || candidate.known_at>asof {return Ok(EntrySelection{entry:None,reason:"n_not_yet_available".into()});}
    if options.buy_interval_level.is_some_and(|level|!(1..=3).contains(&level))
        || !["alternation_low","minimum_close"].contains(&options.first_basis.as_str()) {
        return Err("invalid whole-wave entry level or counter basis".into());
    }
    let shallow=fraction_threshold(options.shallow_ratio).ok_or("whole-wave threshold must be exactly one-third, one-half or two-thirds")?;
    let deep=options.deep_ratio.map(|value|fraction_threshold(value).ok_or("invalid whole-wave deep threshold")).transpose()?;
    let current:Vec<_>=current.iter().filter(|context|options.buy_interval_level.is_none_or(|level|level==context.trend_level)).cloned().collect();
    let at_attack:Vec<_>=at_attack.iter().filter(|context|options.buy_interval_level.is_none_or(|level|level==context.trend_level)).cloned().collect();
    let live:BTreeMap<_,_>=current.iter().map(|context|(context.episode(),context)).collect();
    let contexts=wave_entry_contexts(&current,&at_attack,attack,asof,options.allow_confirming_n);
    if contexts.is_empty(){return Ok(EntrySelection{entry:None,reason:"wave_no_alternation_at_attack".into()});}
    let mut choices:Vec<(u8,u8,Value)>=Vec::new();let mut reasons=Vec::new();
    for context in contexts {
        if !live.contains_key(&context.episode()){reasons.push("wave_context_no_longer_live");continue;}
        let joint=options.allow_confirming_n && context.confirmation_attack==Some(attack) && attack<context.alternation_index && context.alternation_index<=asof;
        if !(context.flip_index<=context.alternation_index && (context.alternation_index<attack || joint)) {reasons.push("wave_alternation_not_before_n");continue;}
        let (l0,h0)=(context.origin_price,context.flip_high_price);
        if h0<=l0 {reasons.push("wave_invalid_flip_amplitude");continue;}
        if context.origin_index>asof || bars[context.origin_index..=asof].iter().any(|bar|bar.low<l0){reasons.push("wave_flip_origin_broken");continue;}
        let mut common=crate::indexed_json::to_value(&context).map_err(|error|error.to_string())?;
        common["definition"]=json!("whole_flip_wave_v3");common["counter_filter_applied"]=json!(true);
        common["flip_origin_price"]=json!(l0);common["local_n_ratio"]=json!(candidate.counter_ratio());
        common["eligibility_frozen_at"]=json!(if joint{context.alternation_index}else{attack});common["joint_alternation_confirmation"]=json!(joint);
        if context.maturity_index.is_none_or(|mature|mature>=attack) {
            if !matches!(context.trend_level,2|3) && options.buy_interval_level!=Some(1){reasons.push("first_buy_requires_level_two_or_three");continue;}
            let a=context.alternation_low_price;
            if !(l0<=a && a<h0){reasons.push("wave_alternation_origin_broken");continue;}
            let mut close_index=None;let mut counter=a;
            if options.first_basis=="minimum_close" {
                if !(context.flip_high_index<=context.alternation_low_index && context.alternation_low_index<attack){reasons.push("wave_alternation_not_before_n");continue;}
                close_index=(context.flip_high_index..=context.alternation_low_index).min_by_key(|index|bars[*index].close);
                let Some(index)=close_index else{continue;};counter=bars[index].close;
            }
            let numerator=h0-counter;let denominator=h0-l0;
            if deep.is_some_and(|(num,den)|numerator*den<=denominator*num){reasons.push("wave_first_pullback_not_deep");continue;}
            common["buy_point_type"]=json!("transition_squeeze");common["priority"]=json!(1);
            common["counter_filter_applied"]=json!(deep.is_some());common["counter_ratio"]=json!(numerator/denominator);
            common["counter_exact_ratio"]=json!(exact_ratio(numerator,denominator)?);common["counter_limit"]=json!(options.deep_ratio);
            common["counter_operator"]=json!(deep.map(|_|">"));
            common["counter_basis"]=json!(if options.first_basis=="minimum_close"{"minimum_close_from_flip_high_to_alternation"}else{"alternation_low_over_whole_flip"});
            common["ratio_high_price"]=json!(h0);common["ratio_low_price"]=json!(l0);common["counter_price"]=json!(counter);
            if let Some(index)=close_index {common["minimum_close_index"]=json!(index);}
            choices.push((1,context.trend_level,common));continue;
        }
        let mature=context.maturity_index.ok_or("whole-wave maturity missing")?;
        let peak_index=(mature..attack).reduce(|best,index|if bars[index].high>bars[best].high{index}else{best});
        let Some(peak_index)=peak_index else{reasons.push("wave_second_peak_pullback_sequence");continue;};
        let peak=bars[peak_index].high;
        if !(peak>h0 && context.alternation_index<mature && mature<=peak_index && peak_index<low && low<attack) {reasons.push("wave_second_peak_pullback_sequence");continue;}
        let close_index=(peak_index..=asof).min_by_key(|index|bars[*index].close).ok_or("whole-wave pullback close unavailable")?;
        let close=bars[close_index].close;let numerator=(peak-close).max(Decimal::ZERO);let denominator=peak-l0;
        let exceeds=if options.second_inclusive {numerator*shallow.1>denominator*shallow.0}else{numerator*shallow.1>=denominator*shallow.0};
        if exceeds{reasons.push("wave_second_close_pullback_too_deep");continue;}
        common["buy_point_type"]=json!("mature_shallow_squeeze");common["priority"]=json!(2);
        common["counter_ratio"]=json!(numerator/denominator);common["counter_exact_ratio"]=json!(exact_ratio(numerator,denominator)?);
        common["counter_limit"]=json!(options.shallow_ratio);common["counter_operator"]=json!(if options.second_inclusive{"<="}else{"<"});
        common["counter_basis"]=json!("minimum_close_over_peak_minus_flip_origin");common["ratio_high_price"]=json!(peak);
        common["ratio_low_price"]=json!(l0);common["counter_price"]=json!(close);common["peak_index"]=json!(peak_index);
        common["peak_price"]=json!(peak);common["minimum_close_index"]=json!(close_index);common["pullback_index"]=json!(low);
        common["impulse_high_index"]=json!(peak_index);common["impulse_origin_index"]=json!(context.origin_index);
        choices.push((2,context.trend_level,common));
    }
    Ok(match choices.into_iter().reduce(|best,next|if (next.0,next.1)>(best.0,best.1){next}else{best}){
        Some((_,_,entry))=>EntrySelection{entry:Some(entry),reason:String::new()},None=>EntrySelection{entry:None,
            reason:reasons.first().copied().unwrap_or("wave_no_eligible_context").into()}})
}

#[derive(Debug,Clone,Default,Serialize,Deserialize)]
pub struct ResistanceLedger {previous:BTreeMap<u8,Option<(usize,Decimal)>>,pub events:Vec<Value>}
impl ResistanceLedger {
    /// chart_entry_history：真实本级 H、已确认下降压力及源确认 A 高进入同一阻力选择。
    pub fn advance(&mut self,frame:&super::super::kernel::KernelFrame,now:usize)->Result<Option<Value>,String>{
        use super::super::kernel::PointKind;
        if !matches!(frame.level,2|3){return Err("multilevel resistance consumes level 2 or 3".into());}
        let mut candidates:Vec<(usize,Decimal,&str)>=Vec::new();
        for point in &frame.points {
            if point.kind==PointKind::H && point.is_closed() && point.known_index<=now {
                let value=point.value.to_string().parse::<Decimal>().map_err(|_|"invalid hierarchy resistance price")?;
                candidates.push((point.index,value,"formal"));
            }
        }
        for point in frame.points.iter().chain(frame.candidate.iter()) {
            let proof=point.evidence.get("upgrade_evidence").or_else(||point.evidence.get("proof"));
            let Some(proof)=proof else{continue;};
            let known=qualification::index(proof,"available_index").or_else(||qualification::index(proof,"available_at"));
            if known.is_none_or(|known|known>now){continue;}
            let source=if text(proof,"direction")==Some("up"){proof.get("confirmed_by")}
                else if text(proof,"direction")==Some("down"){proof.get("origin")}else{None};
            let Some(source)=source.filter(|source|text(source,"kind")==Some("H"))else{continue;};
            let (Some(index),Some(value),Some(known))=(qualification::index(source,"index"),qualification::price(source,"value"),
                qualification::index(source,"known_index").or_else(||qualification::index(source,"available_at")))else{continue;};
            if index<=known && known<=now && !matches!(text(source,"state"),Some("seed"|"developing")) {
                candidates.push((index,value,"confirmed_source"));
            }
        }
        let selected=candidates.into_iter().reduce(|best,next|if (next.0,next.1)>(best.0,best.1){next}else{best});
        let identity=selected.map(|(index,value,_)|(index,value));
        if self.previous.get(&frame.level).copied().flatten()==identity{return Ok(None);}
        self.previous.insert(frame.level,identity);
        let event=json!({"event":"hierarchy_resistance_key","bar_index":now,"trend_level":frame.level,
            "key":selected.map(|(index,value,source)|json!({"index":index,"value":value,"source":source}))});
        self.events.push(event.clone());Ok(Some(event))
    }
}

#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct BullPermission {pub context_index:usize,pub key_source_index:usize,pub key_price:Decimal,
    pub flip_index:usize,pub alternation_index:usize,pub bullish_index:usize}
impl BullPermission {pub fn permits(&self,attack_index:usize)->bool {
    self.flip_index<self.alternation_index && self.alternation_index<=self.bullish_index && self.bullish_index<attack_index
}}
#[derive(Debug,Clone,Serialize,Deserialize)]
struct FrozenBearContext {epoch:usize,window_start:usize,index:usize,points:Vec<super::super::kernel::KernelPoint>,
    key:super::super::kernel::KernelPoint,bottom:super::super::kernel::KernelPoint}
#[derive(Debug,Clone,Default,Serialize,Deserialize)]
struct PermissionTransition {attack:Option<usize>,alternation:Option<usize>,ratio:Option<Decimal>,drawdown:Option<Decimal>,impulse:Option<Decimal>,stage:String}
#[derive(Debug,Clone,Default,Serialize,Deserialize)]
pub struct BullPermissionState {context:Option<FrozenBearContext>,transition:Option<PermissionTransition>,ready:Option<BullPermission>,
    previous_points:Vec<super::super::kernel::KernelPoint>,previous_support:Option<Decimal>,epoch:Option<usize>,last_index:Option<usize>}
#[derive(Debug,Clone,Default,Serialize,Deserialize)]
pub struct BullPermissionDay {pub permission:Option<BullPermission>,pub events:Vec<Value>}
fn point_price(point:&super::super::kernel::KernelPoint)->Result<Decimal,String>{
    point.value.to_string().parse().map_err(|_|"hierarchy price exceeds decimal range".into())
}
fn scalar_equal(left:&super::super::kernel::KernelPoint,right:&super::super::kernel::KernelPoint)->bool {
    left.order()==right.order() && left.kind==right.kind && left.value==right.value && left.known_index==right.known_index
}
fn legacy_transition(bars:&[RuleCandle],points:&[super::super::kernel::KernelPoint],context:&FrozenBearContext,now:usize)
    ->Result<PermissionTransition,String> {
    use super::super::kernel::PointKind;
    let mut result=PermissionTransition {stage:"watching_frozen_key".into(),..Default::default()};
    let bottom=point_price(&context.bottom)?;let key=point_price(&context.key)?;let mut invalid=false;
    for index in context.index+1..=now {
        if bars[index].low<bottom {invalid=true;break;}
        if result.attack.is_none() && bars[index-1].close<=key && bars[index].close>key {result.attack=Some(index);}
    }
    let Some(attack)=result.attack else{if invalid{result.stage="original_extreme_breached".into();}return Ok(result);};
    result.stage="key_broken_await_confirmed_countermove".into();
    for (position,point) in points.iter().enumerate() {
        if point.kind!=PointKind::H || point.index<attack || point.known_index<attack || position==0 {continue;}
        let origin=&points[position-1];
        if !(origin.index<attack && attack<=point.index){break;}
        if let Some(counter)=points.get(position+1) {
            if counter.index<=point.index {break;}
            let origin=point_price(origin)?;let high=point_price(point)?;let low=point_price(counter)?;
            let amplitude=high-origin;let drawdown=high-low;
            if amplitude<=Decimal::ZERO || drawdown<Decimal::ZERO{return Err("frozen transition requires positive impulse and nonnegative counter".into());}
            result.ratio=Some(drawdown/amplitude);result.drawdown=Some(drawdown);result.impulse=Some(amplitude);
            if drawdown>Decimal::ZERO && drawdown<amplitude && drawdown*Decimal::from(100)<amplitude*Decimal::from(67) {
                result.stage="countermove_below_67_percent".into();result.alternation=Some(counter.known_index);
            }else{result.stage="countermove_not_below_67_percent".into();}
        }
        break;
    }
    if invalid {result.stage="original_extreme_breached".into();}Ok(result)
}
impl BullPermissionState {
    fn clear(&mut self){self.context=None;self.transition=None;self.ready=None;self.previous_points.clear();}
    /// bull_eligibility.bull_permission_history 的逐日前缀状态，使用共享 classify_prices 判断已给定点。
    /// 这是旧 entry policy 的许可；不改动 KernelFrame，也不授予交易资格区间。
    pub fn advance(&mut self,bars:&[RuleCandle],frame:&super::super::kernel::KernelFrame,now:usize,window:usize,
        maximum_retracement:Option<Decimal>)->Result<BullPermissionDay,String> {
        use super::super::kernel::{PointKind,Direction as KernelDirection,classify_prices};
        if frame.level!=0 || now>=bars.len() || window==0 || self.last_index.is_some_and(|last|last.checked_add(1)!=Some(now)) {
            return Err("bull permission requires consecutive available polyline days".into());
        }
        self.last_index=Some(now);let mut result=BullPermissionDay::default();
        let blocked=frame.blocked_at==Some(now);
        let current_epoch=frame.candidate.as_ref().map(|point|point.path).or(frame.blocked_at)
            .or_else(||frame.points.last().map(|point|point.path)).unwrap_or(0);
        if self.epoch!=Some(current_epoch) || blocked {
            if self.context.is_some(){result.events.push(json!({"event":"bull_permission_revoked","bar_index":now,"reason":"structure_segment_interrupted"}));}
            self.clear();self.previous_support=None;
        }
        self.epoch=Some(current_epoch);if blocked{return Ok(result);}
        let start=current_epoch.max(now.saturating_sub(window));
        let points:Vec<_>=frame.points.iter().filter(|point|point.path==current_epoch && point.is_closed() && point.index>=start && point.known_index<=now).cloned().collect();
        let highs:Vec<_>=points.iter().filter(|point|point.kind==PointKind::H).map(|point|point.value).collect();
        let lows:Vec<_>=points.iter().filter(|point|point.kind==PointKind::L).map(|point|point.value).collect();
        let direction=classify_prices(&highs[highs.len().saturating_sub(2)..],&lows[lows.len().saturating_sub(2)..])?;
        let bottom=points.iter().filter(|point|point.kind==PointKind::L).reduce(|best,next|if next.value<best.value{next}else{best});
        let peak=points.iter().filter(|point|point.kind==PointKind::H).reduce(|best,next|if next.value>best.value{next}else{best});
        let fall_key=bottom.and_then(|bottom|points.iter().rev().find(|point|point.kind==PointKind::H && point.order()<bottom.order()));
        let rise_key=peak.and_then(|peak|points.iter().rev().find(|point|point.kind==PointKind::L && point.order()<peak.order()));
        let support=rise_key.map(point_price).transpose()?;
        let mut reason=None;
        if let Some(context)=&self.context {
            if bars[now].low<point_price(&context.bottom)? {reason=Some("frozen_bottom_breached");}
            else if self.transition.as_ref().is_some_and(|transition|transition.alternation.is_some()) {
                if now>0 && self.previous_support.is_some_and(|support|bars[now-1].close>=support && bars[now].close<support){reason=Some("last_rise_low_close_broken");}
                else if direction==Some(KernelDirection::Down){reason=Some("bearish_structure_returned");}
            }else if !context.points.iter().all(|frozen|frame.points.iter().any(|point|scalar_equal(frozen,point))){reason=Some("frozen_context_replaced_by_proxy");}
            else if self.transition.as_ref().is_none_or(|transition|transition.attack.is_none()) && now-context.index>window {reason=Some("unbroken_context_expired");}
        }
        if let Some(reason)=reason {result.events.push(json!({"event":"bull_permission_revoked","bar_index":now,"reason":reason}));self.clear();}
        if self.context.is_none() && direction==Some(KernelDirection::Down) {
            if let (Some(bottom),Some(key))=(bottom,fall_key) {
                if bars[now].low>=point_price(bottom)? && bars[now].close<=point_price(key)? {
                    self.context=Some(FrozenBearContext{epoch:current_epoch,window_start:start,index:now,points:points.clone(),key:key.clone(),bottom:bottom.clone()});
                    result.events.push(json!({"event":"bearish_context_frozen","bar_index":now,"key":point_price(key)?,"key_source":key.index,"bottom":point_price(bottom)?}));
                }
            }
        }
        if let Some(context)=self.context.clone().filter(|context|now>context.index) {
            let completed=self.transition.as_ref().is_some_and(|transition|transition.alternation.is_some());
            let key=point_price(&context.key)?;
            let crossed=now>0 && bars[now-1].close<=key && bars[now].close>key;
            let changed=points.len()!=self.previous_points.len() || points.iter().zip(&self.previous_points).any(|(left,right)|!scalar_equal(left,right));
            if !completed && (changed || crossed) {
                let visible:Vec<_>=frame.points.iter().filter(|point|point.path==current_epoch && point.is_closed()
                    && point.index>=context.window_start && point.known_index<=now).cloned().collect();
                let next=legacy_transition(bars,&visible,&context,now)?;
                if maximum_retracement.zip(next.drawdown.zip(next.impulse)).is_some_and(|(limit,(drawdown,impulse))|
                    fraction_threshold(limit).map_or(drawdown>=impulse*limit,|(num,den)|drawdown*den>=impulse*num)) {
                    result.events.push(json!({"event":"bull_permission_revoked","bar_index":now,"reason":"countermove_not_below_exact_two_thirds"}));
                    self.clear();self.previous_points=points;self.previous_support=support;return Ok(result);
                }
                if next.attack.is_some() && self.transition.as_ref().is_none_or(|prior|prior.attack.is_none()) {
                    result.events.push(json!({"event":"bear_to_bull_flip","bar_index":now,"flip_index":next.attack,"context_index":context.index,"key":key}));
                }
                if next.alternation.is_some(){result.events.push(json!({"event":"bear_bull_alternation","bar_index":now,"flip_index":next.attack,"alternation_index":next.alternation,"ratio":next.ratio}));}
                else if matches!(next.stage.as_str(),"countermove_not_below_67_percent"|"original_extreme_breached") {
                    result.events.push(json!({"event":"bull_permission_revoked","bar_index":now,"reason":next.stage}));self.clear();
                }
                if self.context.is_some(){self.transition=Some(next);}
            }
            if let Some(transition)=self.transition.as_ref().filter(|transition|transition.alternation.is_some() && direction==Some(KernelDirection::Up)) {
                if self.ready.is_none() {
                    if let (Some(flip),Some(alternation))=(transition.attack,transition.alternation){
                        let ready=BullPermission{context_index:context.index,key_source_index:context.key.index,key_price:key,
                            flip_index:flip,alternation_index:alternation,bullish_index:now};
                        let mut event=crate::indexed_json::to_value(&ready).map_err(|error|error.to_string())?;
                        event["event"]=json!("bullish_entry_permission_confirmed");event["bar_index"]=json!(now);result.events.push(event);self.ready=Some(ready);
                    }
                }
                result.permission=self.ready.clone();
            }
        }
        self.previous_points=points;self.previous_support=support;Ok(result)
    }
}
