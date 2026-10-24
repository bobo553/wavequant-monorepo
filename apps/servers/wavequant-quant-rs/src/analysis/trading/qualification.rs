//! 仅消费服务端统一趋势函数产生的确认事实，不从当前六态标签推断资格。
use rust_decimal::Decimal;
use serde::{Deserialize, Serialize};
use crate::indexed_json::Value;
use std::{collections::BTreeMap, str::FromStr};

pub(crate) fn index(e: &Value, key: &str) -> Option<usize> { usize::try_from(e.get(key)?.as_u64()?).ok() }
pub(crate) fn price(e: &Value, key: &str) -> Option<Decimal> {
    let v = e.get(key)?;
    match (v).kind() { Some(crate::indexed_json::Kind::Number(n)) => Decimal::from_str(&n.to_string()).ok(), Some(crate::indexed_json::Kind::String(s)) => Decimal::from_str(s).ok(), _ => None }
}
fn text<'a>(e: &'a Value, key: &str) -> Option<&'a str> { e.get(key)?.as_str() }
fn yes(e: &Value, key: &str) -> bool { e.get(key).and_then(Value::as_bool) == Some(true) }
fn clock(e: &Value, now: usize) -> bool { index(e, "bar_index") == Some(now) && index(e, "available_index") == Some(now) }

pub fn confirmed_alternation_event(event: &Value) -> bool {
    let (Some(now), Some(state), Some(flip), Some(known), Some(counter)) = (index(event, "bar_index"),
        text(event, "state"), index(event, "flip_index"), index(event, "flip_known_index"), index(event, "counter_index")) else { return false; };
    text(event, "event") == Some("trend_lifecycle_state") && matches!(state, "空多交替" | "多空交替")
        && (state != "空多交替" || yes(event, "flip_confirmed")) && flip <= known && known <= now
        && flip <= counter && counter <= now && index(event, "alternation_index") == Some(now)
}
pub fn confirmed_bear_key_break(e: &Value, now: usize) -> bool {
    let (Some(key), Some(known), Some(origin), Some(attack), Some(attack_known), Some(source_known), Some(threshold), Some(value)) = (
        index(e,"broken_key_index"),index(e,"broken_key_known_index"),index(e,"origin_index"),index(e,"flip_index"),
        index(e,"flip_known_index"),index(e,"source_known_index"),price(e,"broken_key_price"),price(e,"breakout_level")) else { return false; };
    matches!(text(e,"event"),Some("trend_lifecycle_state" | "trend_lifecycle_anchor")) && text(e,"state") == Some("多翻空")
        && text(e,"direction") == Some("down") && text(e,"source_event") == Some("翻多为空") && yes(e,"flip_confirmed") && clock(e,now)
        && key < origin && origin < attack && attack <= attack_known && attack_known <= source_known && source_known <= now
        && key <= known && known < attack && Decimal::ZERO < value && value < threshold
}
pub fn confirmed_source_market_break(e: &Value, now: usize) -> bool {
    let (Some(key),Some(known),Some(origin),Some(origin_known),Some(confirm),Some(threshold),Some(high),Some(low)) = (
        index(e,"broken_key_index"),index(e,"broken_key_known_index"),index(e,"origin_index"),index(e,"origin_known_index"),
        index(e,"confirmation_index"),price(e,"broken_key_price"),price(e,"origin_price"),price(e,"market_low")) else { return false; };
    text(e,"event") == Some("trend_source_market_key_broken") && text(e,"direction") == Some("down")
        && text(e,"confirmation_rule") == Some("strict_same_level_market_key_break") && clock(e,now)
        && key < origin && origin < confirm && confirm == now && key <= known && known < now
        && origin <= origin_known && origin_known <= now && Decimal::ZERO < low && low < threshold && threshold < high
}
pub fn confirmed_source_bear(e: &Value, now: usize) -> bool {
    if confirmed_bear_key_break(e,now) || confirmed_source_market_break(e,now) || confirmed_failed_breakout(e,now) { return true; }
    if !clock(e,now) || text(e,"direction") != Some("down") || !yes(e,"flip_confirmed") { return false; }
    if text(e,"event") == Some("trend_bear_structure_low_broken") {
        let (Some(po),Some(pf),Some(pk),Some(origin),Some(ok),Some(key),Some(known),Some(threshold),Some(previous),Some(low)) = (
            index(e,"prior_flip_origin_index"),index(e,"prior_flip_index"),index(e,"prior_flip_known_index"),index(e,"origin_index"),
            index(e,"origin_known_index"),index(e,"flip_index"),index(e,"flip_known_index"),price(e,"breakout_level"),price(e,"previous_low"),price(e,"market_low")) else { return false; };
        return text(e,"confirmation_rule") == Some("confirmed_bear_flip_then_bear_structure_low_break")
            && po < pf && pf <= pk && pk < origin && origin < key && key <= known && known < now
            && origin <= ok && ok <= now && Decimal::ZERO < low && low < threshold && threshold <= previous;
    }
    let (Some(origin),Some(flip),Some(known),Some(counter),Some(alternation),Some(threshold),Some(previous),Some(close)) = (
        index(e,"origin_index"),index(e,"flip_index"),index(e,"flip_known_index"),index(e,"counter_index"),index(e,"alternation_index"),
        price(e,"breakout_level"),price(e,"previous_close"),price(e,"confirmation_close")) else { return false; };
    text(e,"event") == Some("trend_lifecycle_state") && text(e,"state") == Some("转空确认")
        && text(e,"confirmation_rule") == Some("strict_close_cross_after_confirmed_alternation")
        && origin < flip && flip <= known && known <= alternation && alternation < now && flip < counter && counter <= alternation
        && Decimal::ZERO < close && close < threshold && threshold <= previous
}
pub fn confirmed_failed_breakout(e: &Value, now: usize) -> bool {
    let (Some(origin),Some(known),Some(flip),Some(flip_known),Some(attack),Some(end),Some(longest)) = (
        index(e,"origin_index"),index(e,"origin_known_index"),index(e,"prior_flip_index"),index(e,"prior_flip_known_index"),
        index(e,"attack_index"),index(e,"volume_baseline_end"),index(e,"longest_held_sessions")) else {return false;};
    if text(e,"event")!=Some("trend_bear_failed_breakout") || text(e,"confirmation_rule")!=Some("bear_flip_short_breakout_massive_bearish_close_rejection")
        || !clock(e,now) || text(e,"direction")!=Some("down") || !yes(e,"flip_confirmed")
        || !(origin<=known && known<=flip_known && flip_known<attack && attack<=now && origin<flip && flip<=flip_known)
        || attack<20 || end!=attack-1 || longest>=3 || index(e,"prior_flip_origin_index")!=Some(origin)
        || index(e,"flip_index")!=Some(origin) || index(e,"flip_known_index")!=Some(known) || index(e,"volume_window")!=Some(20)
        || price(e,"volume_min_multiple")!=Some(Decimal::TWO) || price(e,"bearish_body_min")!=Some(super::ratio(3,100))
        || index(e,"stable_close_sessions")!=Some(3) || text(e,"stability_basis")!=Some("last_rise_high_close") {return false;}
    let (Some(threshold),Some(high),Some(attack_high),Some(previous),Some(close),Some(open),Some(volume),Some(mean)) = (
        price(e,"breakout_level"),price(e,"anchor_high"),price(e,"attack_high"),price(e,"previous_close"),price(e,"confirmation_close"),
        price(e,"observed_open"),price(e,"observed_volume"),price(e,"volume_mean")) else {return false;};
    Decimal::ZERO<close && close<threshold && threshold<=previous && threshold<=high && high<attack_high
        && open>Decimal::ZERO && mean>Decimal::ZERO && volume>=Decimal::TWO*mean && open-close>=open*super::ratio(3,100)
}
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ConfirmationGap { pub index: usize, pub lower: Decimal, pub upper: Decimal, pub kind: String }
fn confirmation_gap(event: &Value, now: usize) -> Option<ConfirmationGap> {
    let proof = event.get("confirmation_evidence")?;
    let (day, lower, upper, kind) = match text(event,"confirmation_rule")? {
        "body_gap_volume_bullish_breakout_after_defended_low" => (index(proof,"candidate_index")?,price(proof,"gap_previous_body_high")?,price(proof,"gap_open")?,"body"),
        "positive_n_consolidation_gap_after_qualified_b" => (index(proof,"gap_confirmation_index")?,price(proof,"gap_previous_high")?,price(proof,"gap_low")?,"full"), _ => return None,
    };
    (day == now && Decimal::ZERO < lower && lower < upper).then_some(ConfirmationGap { index:day,lower,upper,kind:kind.into() })
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TradingInterval {
    pub trend_level:u8,pub source_path:String,pub start_index:usize,pub start_flip_index:usize,
    pub end_index:Option<usize>,pub end_flip_index:Option<usize>,pub end_reason:Option<String>,
    pub start_low_index:usize,pub start_low_ordinal:usize,pub start_low_price:Option<Decimal>,
    pub end_key_index:Option<usize>,pub end_key_price:Option<Decimal>,pub confirmation_gap:Option<ConfirmationGap>,pub gap_replaced_index:Option<usize>,
}
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
struct RecoveryPoint { index:usize,known:usize,price:Decimal,kind:String,ordinal:usize }
fn pivot(value: &Value, now:usize) -> Option<RecoveryPoint> {
    let i=index(value,"index")?;
    let known=index(value,"available_at").or_else(|| index(value,"known_index"))?;
    let p=price(value,"value")?;
    let kind=text(value,"kind")?;
    (i<=known && known<=now && p>Decimal::ZERO && matches!(kind,"H"|"L")).then_some(RecoveryPoint {
        index:i,known,price:p,kind:kind.into(),ordinal:index(value,"ordinal").unwrap_or(0),
    })
}
#[derive(Debug, Clone, Serialize, Deserialize)]
struct RecoveryBarrier { high:RecoveryPoint,low:RecoveryPoint,breach:usize }
#[derive(Debug, Default, Clone, Serialize, Deserialize)]
pub struct TradingEligibility {
    pub initialized:bool,
    pub level:u8,pub intervals:Vec<TradingInterval>,opened:BTreeMap<String,usize>,last_index:Option<usize>,
    blocked:BTreeMap<String,RecoveryBarrier>,released:BTreeMap<String,usize>,
    pub polyline_gate:super::entries::PolylineBuyGate,
    pub events:Vec<Value>,
}
impl TradingEligibility {
    pub fn new(level:u8)->Result<Self,String> {
        if level>3 { return Err("eligibility level must be 0 through 3".into()); }
        Ok(Self{level,initialized:true,..Default::default()})
    }
    pub fn active(&self)->bool { !self.opened.is_empty() }
    pub fn active_intervals(&self)->Vec<&TradingInterval> { self.opened.values().map(|&i|&self.intervals[i]).collect() }
    pub fn contains(&self, now:usize)->bool { self.intervals.iter().any(|v|v.start_index<=now && v.end_index.is_none_or(|end|now<end)) }
    /// 资格恢复仅使用本级已发布节点；突破旧高先解除门槛，新的翻多仍须重新确认交替。
    pub fn advance_same_level_recovery(&mut self,frame:&super::super::kernel::KernelFrame,bars:&[super::RuleCandle],now:usize)->Result<Vec<Value>,String>{
        use super::super::kernel::{KernelPoint,PointKind,PointState};
        if now>=bars.len(){return Err("same-level recovery requires a visible market bar".into());}
        if !self.initialized{*self=Self::new(frame.level)?;}
        if self.level!=frame.level{return Err("same-level recovery frame belongs to another level".into());}
        let reference=|p:&RecoveryPoint|crate::indexed_json::json!({"index":p.index,"ordinal":p.ordinal,"kind":p.kind,"value":p.price,"available_at":p.known,"state":"confirmed"});
        let convert=|point:&KernelPoint|->Option<RecoveryPoint>{
            let price=Decimal::from_str(&point.value.to_string()).ok()?;
            (point.index<=point.known_index&&point.known_index<=now&&price>Decimal::ZERO).then_some(RecoveryPoint{
                index:point.index,ordinal:point.ordinal,known:point.known_index,price,kind:if point.kind==PointKind::H{"H"}else{"L"}.into()})
        };
        let ended:std::collections::BTreeSet<String>=frame.events.iter().filter(|e|e.known_index<=now)
            .filter_map(|e|e.evidence.get("lifecycle_event")).filter(|e|text(e,"event")==Some("trend_lifecycle_invalidated")&&text(e,"reason")==Some("source_path_ended"))
            .filter_map(|e|text(e,"source_path").map(str::to_owned)).collect();
        let mut groups=BTreeMap::<usize,Vec<&KernelPoint>>::new();
        for p in &frame.points{if p.known_index<=now&&p.state!=PointState::Seed&&p.evidence.get("wave_identity_state").and_then(Value::as_str)!=Some("direction_only"){
            groups.entry(p.path).or_default().push(p);
        }}
        let mut events=vec![];
        for(path_id,points)in groups{
            let path=format!("lecture-{path_id}");if ended.contains(&path){continue;}
            if points.windows(2).any(|pair|pair[0].order()>=pair[1].order()){return Err("same-level recovery requires ordered shared nodes".into());}
            let keys=points.iter().rposition(|p|p.kind==PointKind::H).filter(|&position|position>0).and_then(|position|{
                let mut low=convert(points[position-1])?;let high=convert(points[position])?;
                if low.kind!="L"||low.index>=high.index||low.price>=high.price{return None;}
                let mut origins=Vec::<RecoveryPoint>::new();
                for event in frame.events.iter().filter(|e|e.title=="翻空为多"&&e.point.order()==points[position].order()&&e.point.path==path_id&&e.known_index<=now){
                    let (Some(origin),Some(broken))=(&event.origin,&event.key)else{continue;};
                    let owned=|p:&KernelPoint|points.iter().any(|own|own.order()==p.order()&&own.kind==p.kind&&own.value==p.value&&own.known_index==p.known_index);
                    if !owned(origin)||!owned(broken)||origin.kind!=PointKind::L||broken.kind!=PointKind::H
                        ||!(broken.index<origin.index&&origin.index<high.index&&broken.known_index<high.index){continue;}
                    let (Some(origin),Some(broken))=(convert(origin),convert(broken))else{continue;};
                    if origin.price<broken.price&&broken.price<high.price&&!origins.contains(&origin){origins.push(origin);}
                }
                if origins.len()==1{low=origins.remove(0);}
                let decline=points.get(position+1).filter(|_|points[position].is_closed()).and_then(|p|convert(p))
                    .filter(|p|p.kind=="L"&&p.index>high.index&&p.price<low.price);
                Some((low,high,decline))
            });
            let base=crate::indexed_json::json!({"bar_index":now,"available_index":now,"trend_level":frame.level,"source_path":path});
            let mut emit=None;
            if let Some(pending)=self.blocked.get(&path){
                if now>pending.breach.max(pending.high.known)&&bars[now].high>pending.high.price{
                    let mut event=base;event["event"]=crate::indexed_json::json!("trading_same_level_recovery_cleared");event["recovery_high"]=reference(&pending.high);
                    event["broken_low"]=reference(&pending.low);event["market_high"]=crate::indexed_json::json!(bars[now].high);emit=Some(event);
                }else if let Some((low,high,Some(decline)))=keys{
                    if high.index>pending.high.index.max(pending.breach){let mut event=base;event["event"]=crate::indexed_json::json!("trading_same_level_recovery_required");
                        event["recovery_high"]=reference(&high);event["broken_low"]=reference(&low);event["decline_low"]=reference(&decline);emit=Some(event);}
                }
            }else if let Some((low,high,_))=keys{
                if low.known<now&&bars[now].low<low.price&&high.index<now&&!bars[high.index+1..=now].iter().any(|bar|bar.high>high.price){
                    let mut event=base;event["event"]=crate::indexed_json::json!("trading_same_level_recovery_required");event["recovery_high"]=reference(&high);
                    event["broken_low"]=reference(&low);event["market_low"]=crate::indexed_json::json!(bars[now].low);emit=Some(event);
                }
            }
            if let Some(event)=emit{self.observe(&event)?;events.push(event);}
        }
        Ok(events)
    }
    fn close(&mut self,path:&str,now:usize,reason:&str,flip:Option<usize>,key:Option<usize>,price:Option<Decimal>)->Option<bool> {
        let position=self.opened.remove(path)?;
        let v=&mut self.intervals[position]; v.end_index=Some(now);v.end_reason=Some(reason.into());v.end_flip_index=flip;
        v.end_key_index=key;v.end_key_price=price;Some(false)
    }
    pub fn observe(&mut self,event:&Value)->Result<Option<bool>,String> {
        let (Some(now),Some(path))=(index(event,"bar_index"),text(event,"source_path")) else { return Ok(None); };
        if index(event,"trend_level")!=Some(usize::from(self.level)) { return Ok(None); }
        if self.last_index.is_some_and(|last|now<last) { return Err("eligibility requires chronological events".into()); }
        self.last_index=Some(now);
        self.events.push(event.clone());
        let kind=text(event,"event").unwrap_or("");let reason=text(event,"reason").unwrap_or("");
        if kind=="trend_lifecycle_invalidated" && reason=="source_path_ended" { self.blocked.remove(path);self.released.remove(path); }
        if matches!(kind,"trading_same_level_recovery_required"|"trading_same_level_recovery_cleared") {
            if index(event,"available_index")!=Some(now) { return Ok(None); }
            let (Some(high),Some(low))=(event.get("recovery_high").and_then(|v|pivot(v,now)),event.get("broken_low").and_then(|v|pivot(v,now))) else { return Ok(None); };
            if high.kind!="H" || low.kind!="L" || low.index>=high.index || low.known>=now || low.price>=high.price { return Ok(None); }
            if kind=="trading_same_level_recovery_cleared" {
                if self.blocked.get(path).is_some_and(|p|p.high==high && p.low==low && now>p.breach.max(high.known))
                    && price(event,"market_high").is_some_and(|p|p>high.price) { self.blocked.remove(path);self.released.insert(path.into(),now); }
                return Ok(None);
            }
            let breach=if let Some(pending)=self.blocked.get(path) {
                let Some(decline)=event.get("decline_low").and_then(|v|pivot(v,now)) else{return Ok(None);};
                if decline.kind!="L" || decline.price>=low.price || pending.high.index>=high.index || high.index>=decline.index || high.index<=pending.breach {return Ok(None);}
                pending.breach
            } else {if !price(event,"market_low").is_some_and(|p|p>Decimal::ZERO && p<low.price){return Ok(None);}now};
            self.released.remove(path);self.blocked.insert(path.into(),RecoveryBarrier{high,low:low.clone(),breach});
            return Ok(self.close(path,now,"same_level_last_rise_low_broken",None,Some(low.index),Some(low.price)));
        }
        if confirmed_bear_key_break(event,now) {return Ok(self.close(path,now,"same_level_last_rise_low_broken",None,index(event,"broken_key_index"),price(event,"broken_key_price")));}
        if kind=="trend_bear_structure_low_broken" || kind=="trend_lifecycle_state" && text(event,"state")==Some("转空确认") {
            return Ok(if confirmed_source_bear(event,now) {self.close(path,now,"same_level_bear_trend_confirmed",None,index(event,"flip_index"),price(event,"breakout_level"))}else{None});
        }
        if kind=="trading_source_bear_confirmed" {
            if let Some(proof)=event.get("source_confirmation") {
                if self.level>0 && index(proof,"trend_level")==Some(usize::from(self.level-1)) && text(event,"direct_source_path").is_some()
                    && text(proof,"source_path")==text(event,"direct_source_path") && index(event,"available_index")==Some(now) && confirmed_source_bear(proof,now) {
                    let key=confirmed_bear_key_break(proof,now)||confirmed_source_market_break(proof,now);
                    return Ok(self.close(path,now,"source_bear_trend_confirmed",None,index(proof,if key{"broken_key_index"}else{"flip_index"}),price(proof,if key{"broken_key_price"}else{"breakout_level"})));
                }
            } return Ok(None);
        }
        if kind=="trading_reversal_low_broken" {
            if let (Some(i),Some(known),Some(p),Some(low))=(index(event,"reversal_low_index"),index(event,"reversal_low_known_index"),price(event,"reversal_low_price"),price(event,"market_low")) {
                if i<=known && known<now && p>Decimal::ZERO && low<p {return Ok(self.close(path,now,"reversal_low_broken",None,Some(i),Some(p)));}
            }return Ok(None);
        }
        if kind=="trend_lifecycle_confirmation_revoked" && reason=="confirmation_gap_filled" {
            let valid=self.opened.get(path).is_some_and(|&position|{
                let window=&self.intervals[position];
                window.gap_replaced_index.is_none() && now>window.start_index && index(event,"qualification_index")==Some(window.start_index)
                    && window.confirmation_gap.as_ref().is_some_and(|gap| index(event,"confirmation_gap_index")==Some(gap.index)
                        && price(event,"confirmation_gap_lower")==Some(gap.lower) && price(event,"confirmation_gap_upper")==Some(gap.upper)
                        && text(event,"confirmation_gap_kind")==Some(gap.kind.as_str()) && price(event,"market_low").is_some_and(|p|p>Decimal::ZERO && p<=gap.lower))
            });return Ok(if valid {self.close(path,now,"confirmation_gap_filled",None,None,None)}else{None});
        }
        if kind=="trend_lifecycle_invalidated" && matches!(reason,"source_evidence_revised"|"source_path_ended"|"bull_recovery_pending"|"special_bull_origin_broken") {
            return Ok(self.close(path,now,reason,None,None,None));
        }
        if !confirmed_alternation_event(event) {return Ok(None);}
        let flip=index(event,"flip_index").ok_or("confirmed flip missing")?;
        if text(event,"state")==Some("多空交替") {return Ok(self.close(path,now,"bear_alternation_completed",Some(flip),None,None));}
        if self.blocked.contains_key(path) || self.released.get(path).is_some_and(|&released|flip<released) {return Ok(None);}
        let gap=confirmation_gap(event,now);
        if let Some(&position)=self.opened.get(path) {
            let window=&mut self.intervals[position];
            if window.confirmation_gap.as_ref().is_some_and(|g|index(event,"replaces_gap_confirmation_index")==Some(g.index)) && gap.is_none() {window.gap_replaced_index=Some(now);}
            return Ok(None);
        }
        self.opened.insert(path.into(),self.intervals.len());
        self.intervals.push(TradingInterval{trend_level:self.level,source_path:path.into(),start_index:now,start_flip_index:flip,
            end_index:None,end_flip_index:None,end_reason:None,start_low_index:index(event,"counter_index").ok_or("counter missing")?,
            start_low_ordinal:index(event,"counter_ordinal").unwrap_or(0),start_low_price:price(event,"counter_price").filter(|p|*p>Decimal::ZERO),
            end_key_index:None,end_key_price:None,confirmation_gap:gap,gap_replaced_index:None});Ok(Some(true))
    }
}
pub fn interval_days(intervals:&[TradingInterval],size:usize,include_end:bool)->Vec<bool> {
    let mut changes=vec![0_i64;size+1];
    for v in intervals {let end=v.end_index.map_or(size,|i|i.saturating_add(usize::from(include_end)).min(size));
        if v.start_index<end && end<=size {changes[v.start_index]+=1;changes[end]-=1;}}
    let mut active=0;changes[..size].iter().map(|delta|{active+=delta;active>0}).collect()
}
