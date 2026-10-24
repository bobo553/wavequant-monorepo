//! trend_structure.py 严格同类比较、十进制回撤；polyline_lifecycle.py 冻结关键位观察。
use super::{Direction, KernelFrame, KernelPoint, PointKind, TrendState};
use rust_decimal::{Decimal, prelude::ToPrimitive};
use serde::{Deserialize, Serialize};
use std::str::FromStr;

pub fn classify_prices(highs: &[f64], lows: &[f64]) -> Result<Option<Direction>, String> {
    if highs.iter().chain(lows).any(|p| !p.is_finite() || *p <= 0.0) { return Err("finite positive trend prices required".into()); }
    if highs.len() < 2 || lows.len() < 2 { return Ok(None); }
    if highs.windows(2).chain(lows.windows(2)).all(|pair| pair[1] > pair[0]) { return Ok(Some(Direction::Up)); }
    if highs.windows(2).chain(lows.windows(2)).all(|pair| pair[1] < pair[0]) { return Ok(Some(Direction::Down)); }
    Ok(None)
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Retracement {
    pub impulse: f64, pub countermove: f64, pub ratio: f64,
    pub below_67_percent: bool, pub below_33_percent: bool, pub partial: bool,
}
fn decimal(value: f64) -> Result<Decimal, String> {
    if !value.is_finite() || value <= 0.0 { return Err("finite positive price required".into()); }
    Decimal::from_str(&value.to_string()).map_err(|_| "price exceeds decimal range".into())
}
pub fn retracement(origin: f64, extreme: f64, counter: f64, direction: Direction) -> Result<Retracement, String> {
    let sign = if direction == Direction::Up { Decimal::ONE } else { -Decimal::ONE };
    let a = decimal(origin)?; let b = decimal(extreme)?; let c = decimal(counter)?;
    let impulse = sign.checked_mul(b.checked_sub(a).ok_or("price difference overflow")?).ok_or("price overflow")?;
    let movement = sign.checked_mul(b.checked_sub(c).ok_or("price difference overflow")?).ok_or("price overflow")?;
    if impulse <= Decimal::ZERO || movement < Decimal::ZERO { return Err("positive impulse and nonnegative counter move required".into()); }
    let ratio = movement.checked_div(impulse).and_then(|v| v.to_f64()).ok_or("retracement ratio overflow")?;
    let threshold67 = impulse.checked_mul(Decimal::new(67, 2)).ok_or("retracement threshold overflow")?;
    let threshold33 = impulse.checked_mul(Decimal::new(33, 2)).ok_or("retracement threshold overflow")?;
    Ok(Retracement { impulse: impulse.to_f64().ok_or("impulse overflow")?, countermove: movement.to_f64().ok_or("countermove overflow")?,
        ratio, below_67_percent: movement < threshold67, below_33_percent: movement < threshold33,
        partial: movement > Decimal::ZERO && movement < impulse })
}
use super::{KernelBar,arena::{PointArena,PointId,RefFrame,RefEvent,EventProof,Fact}};
#[derive(Clone,Copy)]
struct Attack { origin:PointId,extreme:PointId,anchor:PointId,key:PointId,checked:bool }
fn record(pool:&mut PointArena,result:&mut RefFrame,title:&'static str,point:PointId,key:PointId,origin:Option<PointId>,facts:super::arena::Proof,bars:&[KernelBar]){
    result.events.push(RefEvent{title,known_index:pool.view(point,bars).known_index,point,key:Some(key),origin,proof:EventProof::Inline(facts)});
}
pub fn six_state(points:&[KernelPoint],level:u8)->Result<KernelFrame,String>{
    let mut pool=PointArena::default();let ids=points.iter().map(|p|pool.import(p)).collect::<Vec<_>>();
    let frame=six_state_indices(&mut pool,&[],&ids,level)?;Ok(pool.materialize(&frame,&[]))
}
pub(crate) fn six_state_indices(pool:&mut PointArena,bars:&[KernelBar],points:&[PointId],level:u8)->Result<RefFrame,String>{
    if level>3{return Err("trend level must be 0 through 3".into());}
    let mut result=RefFrame::empty(level);let(mut highs,mut lows)=(Vec::with_capacity(2),Vec::with_capacity(2));
    let mut extremes:[Option<(PointId,Option<PointId>)>;2]=[None,None];
    let(mut direction,mut anchor,mut key,mut attack)=(None,None,None,None::<Attack>);
    let mut suspicion=false;let mut previous=None;let mut trend_origin=None;
    for id in points.iter().copied(){
        let point=pool.view(id,bars);if !point.is_closed(){continue;}
        if point.known_index<point.index||!point.value.is_finite()||point.value<=0.0{return Err("invalid causal trend point".into());}
        if let Some(prior)=previous.map(|p|pool.view(p,bars)){
            if point.order()<=prior.order()||point.known_index<prior.known_index||point.kind==prior.kind
                ||point.kind.sign()*(point.value-prior.value)<=0.0{return Err("ordered alternating confirmed extrema required".into());}
        }
        let prices=if point.kind==PointKind::H{&mut highs}else{&mut lows};
        if prices.len()==2{prices.remove(0);}prices.push(point.value);
        let slot=if point.kind==PointKind::H{0}else{1};
        if extremes[slot].is_none_or(|(old,_)|point.kind.sign()*(point.value-pool.view(old,bars).value)>0.0){extremes[slot]=Some((id,previous));}
        if direction.is_none(){
            direction=classify_prices(&highs,&lows)?;
            if let Some(current)=direction{
                trend_origin=extremes[if current==Direction::Up{1}else{0}].map(|(point,_)|point);
                let slot=if current==Direction::Up{0}else{1};if let Some((a,k))=extremes[slot]{anchor=Some(a);key=k;}
                result.state=Some(if current==Direction::Up{TrendState::Bull}else{TrendState::Bear});
            }
            previous=Some(id);continue;
        }
        let current=direction.ok_or("missing structure direction")?;
        if key.is_none(){
            let slot=if current==Direction::Up{0}else{1};if let Some((a,k))=extremes[slot]{anchor=Some(a);key=k;}
            previous=Some(id);continue;
        }
        let mut active_anchor=anchor.ok_or("missing structure anchor")?;let mut active_key=key.ok_or("missing structure key")?;
        let reversal=current.opposite();let attack_kind=reversal.target();let sign=reversal.sign();
        if attack.is_none(){
            let a=pool.view(active_anchor,bars);let k=pool.view(active_key,bars);
            if point.kind==a.kind&&previous.is_some()&&sign*(point.value-a.value)<0.0{
                active_anchor=id;active_key=previous.ok_or("missing preceding point")?;
                anchor=Some(active_anchor);key=Some(active_key);suspicion=false;
                result.state=Some(if current==Direction::Up{TrendState::Bull}else{TrendState::Bear});
            }else if point.kind==a.kind&&previous.is_some_and(|p|sign*(point.value-a.value)>=0.0&&sign*(pool.view(p,bars).value-k.value)<0.0)&&!suspicion{
                record(pool,&mut result,if reversal==Direction::Up{"底部疑虑"}else{"头部疑虑"},id,active_key,Some(active_anchor),
                    vec![("basis",Fact::Static("confirmed_polyline_extreme")),("anchor",Fact::Point(active_anchor))],bars);suspicion=true;
            }
            if point.kind==attack_kind&&sign*(point.value-pool.view(active_key,bars).value)>0.0{
                if let Some(origin)=previous{
                    attack=Some(Attack{origin,extreme:id,anchor:active_anchor,key:active_key,checked:false});
                    result.state=Some(if reversal==Direction::Up{TrendState::BearToBull}else{TrendState::BullToBear});result.preceding_start=trend_origin;
                    record(pool,&mut result,if reversal==Direction::Up{"翻空为多"}else{"翻多为空"},id,active_key,Some(active_key),
                        vec![("basis",Fact::Static("confirmed_polyline_extreme")),("anchor",Fact::Point(active_anchor)),("impulse_origin",Fact::Point(origin)),
                            ("attack",Fact::Point(id)),("confirmed_extreme",Fact::Point(active_anchor))],bars);
                }
            }
        }else if point.kind!=attack_kind&&attack.is_some_and(|a|!a.checked){
            let active=attack.as_mut().ok_or("missing attack")?;
            let evidence=retracement(pool.view(active.origin,bars).value,pool.view(active.extreme,bars).value,point.value,reversal)?;active.checked=true;
            if evidence.partial&&evidence.below_67_percent{
                record(pool,&mut result,if reversal==Direction::Up{"空多交替"}else{"多空交替"},id,active.key,Some(active.origin),
                    vec![("basis",Fact::Static("confirmed_polyline_extreme")),("ratio",Fact::Float(evidence.ratio)),("weak_countermove",Fact::Bool(evidence.below_33_percent)),
                        ("origin",Fact::Point(active.origin)),("broken_key",Fact::Point(active.key)),("flip_extreme",Fact::Point(active.extreme)),("abc_confirmed",Fact::Bool(false))],bars);
                direction=Some(reversal);trend_origin=Some(active.origin);result.state=Some(if reversal==Direction::Up{TrendState::BearBullAlternation}else{TrendState::BullBearAlternation});
                anchor=Some(active.extreme);key=Some(active.origin);attack=None;suspicion=false;
            }
        }else if point.kind==attack_kind{
            if let Some(active)=attack.as_mut().filter(|a|sign*(point.value-pool.view(a.extreme,bars).value)>0.0){
                active.origin=previous.ok_or("missing attack origin")?;active.extreme=id;active.checked=false;
            }
        }
        if attack.is_some_and(|a|point.kind==pool.view(a.anchor,bars).kind&&sign*(point.value-pool.view(a.anchor,bars).value)<0.0){
            anchor=Some(id);key=previous;attack=None;suspicion=false;
            result.state=Some(if current==Direction::Up{TrendState::Bull}else{TrendState::Bear});
        }
        previous=Some(id);
    }
    result.direction=direction;result.structure_direction=direction;result.key=key;result.anchor=anchor;result.points=points.to_vec();Ok(result)
}

/// polyline_lifecycle.polyline_last_fall_pair：几何接缝只保留同类更强极值。
/// 冻结 key 来自共同结构状态，anchor 是它紧邻的原始反向点。
pub fn polyline_last_fall_pair(points:&[KernelPoint])->Result<Option<(KernelPoint,KernelPoint)>,String> {
    let mut compact:Vec<KernelPoint>=Vec::new();
    for source in points.iter().filter(|p|p.is_closed()) {
        let mut point=source.clone();
        if let Some(previous)=compact.last().filter(|previous|previous.kind==point.kind) {
            if point.kind.sign()*(point.value-previous.value)<=0.0 {continue;}
            compact.pop();
        }
        if let Some(previous)=compact.last() {point.known_index=point.known_index.max(previous.known_index);}
        compact.push(point);
    }
    let state=six_state(&compact,0)?;
    if state.direction!=Some(Direction::Down) {return Ok(None);}
    let Some(key)=state.key else{return Ok(None);};
    let Some(position)=compact.iter().position(|point|point.order()==key.order() && point.kind==key.kind && point.value==key.value) else{return Ok(None);};
    Ok(compact.get(position+1).filter(|point|point.kind==PointKind::L).map(|low|(key,low.clone())))
}
