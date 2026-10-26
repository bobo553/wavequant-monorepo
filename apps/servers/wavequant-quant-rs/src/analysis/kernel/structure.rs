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
fn context(result:&mut RefFrame,point:super::arena::PointView,direction:Option<Direction>,key:Option<PointId>,anchor:Option<PointId>){
    result.contexts.push(super::arena::RefContext{known_index:point.known_index,path:point.path,state:result.state,direction,key,anchor});
}
pub fn six_state(points:&[KernelPoint],level:u8)->Result<KernelFrame,String>{
    let mut pool=PointArena::default();let ids=points.iter().map(|p|pool.import(p)).collect::<Vec<_>>();
    let frame=six_state_indices(&mut pool,&[],&ids,level)?;Ok(pool.materialize(&frame,&[]))
}
/// 有界六态状态：冻结关键位与翻向背景跨点保留，不持有行情前缀。
#[derive(Clone,Default)]
pub(super) struct SixStateMachine {
    highs:Vec<f64>,lows:Vec<f64>,extremes:[Option<(PointId,Option<PointId>)>;2],
    anchored_extremes:[Option<(PointId,Option<PointId>)>;2],direction:Option<Direction>,
    anchor:Option<PointId>,key:Option<PointId>,attack:Option<Attack>,suspicion:bool,
    previous:Option<PointId>,trend_origin:Option<PointId>,state:Option<TrendState>,preceding_start:Option<PointId>,
}
impl SixStateMachine {
    pub(super) fn advance(&mut self,pool:&mut PointArena,bars:&[KernelBar],id:PointId,level:u8)->Result<RefFrame,String>{
        if level>3{return Err("trend level must be 0 through 3".into());}
        let Self{mut highs,mut lows,mut extremes,mut anchored_extremes,mut direction,mut anchor,mut key,mut attack,
            mut suspicion,mut previous,mut trend_origin,state,preceding_start}=std::mem::take(self);
        let mut result=RefFrame::empty(level);result.state=state;result.preceding_start=preceding_start;
    'point: {
        let point=pool.view(id,bars);if !point.is_closed(){break 'point;}
        if point.known_index<point.index||!point.value.is_finite()||point.value<=0.0{return Err("invalid causal trend point".into());}
        if let Some(prior)=previous.map(|p|pool.view(p,bars)){
            if point.order()<=prior.order()||point.known_index<prior.known_index||point.kind==prior.kind
                ||point.kind.sign()*(point.value-prior.value)<=0.0{return Err("ordered alternating confirmed extrema required".into());}
        }
        let prices=if point.kind==PointKind::H{&mut highs}else{&mut lows};
        if prices.len()==2{prices.remove(0);}prices.push(point.value);
        let slot=if point.kind==PointKind::H{0}else{1};
        if extremes[slot].is_none_or(|(old,_)|point.kind.sign()*(point.value-pool.view(old,bars).value)>0.0){extremes[slot]=Some((id,previous));}
        if previous.is_some()&&anchored_extremes[slot].is_none_or(|(old,_)|point.kind.sign()*(point.value-pool.view(old,bars).value)>0.0){
            anchored_extremes[slot]=Some((id,previous));
        }
        if direction.is_none(){
            direction=classify_prices(&highs,&lows)?;
            if let Some(current)=direction{
                trend_origin=extremes[if current==Direction::Up{1}else{0}].map(|(point,_)|point);
                let slot=if current==Direction::Up{0}else{1};if let Some((a,k))=extremes[slot].filter(|(_,key)|key.is_some()).or(anchored_extremes[slot]){anchor=Some(a);key=k;}
                result.state=Some(if current==Direction::Up{TrendState::Bull}else{TrendState::Bear});
            }
            context(&mut result,point,direction,key,anchor);previous=Some(id);break 'point;
        }
        let current=direction.ok_or("missing structure direction")?;
        if key.is_none(){
            // 背景尚未具备完整身份时允许重新初始化；已有有效关键位继续冻结。
            let selected=classify_prices(&highs,&lows)?.unwrap_or(current);
            let slot=if selected==Direction::Up{0}else{1};
            if let Some((a,k))=extremes[slot].filter(|(_,key)|key.is_some()).or(anchored_extremes[slot]){
                direction=Some(selected);anchor=Some(a);key=k;
                result.state=Some(if selected==Direction::Up{TrendState::Bull}else{TrendState::Bear});
            }
            context(&mut result,point,direction,key,anchor);previous=Some(id);break 'point;
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
                    // 翻多测幅冻结整段最低点，不能改成攻击前的局部回撤低点。
                    let origin=if reversal==Direction::Up{active_anchor}else{origin};
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
                if reversal==Direction::Down{active.origin=previous.ok_or("missing attack origin")?;}active.extreme=id;active.checked=false;
            }
        }
        if let Some(failed)=attack.filter(|a|point.kind==pool.view(a.anchor,bars).kind&&sign*(point.value-pool.view(a.anchor,bars).value)<0.0){
            let reverse_origin=failed.extreme;let reverse_key=failed.anchor;
            let origin_point=pool.view(reverse_origin,bars);let key_point=pool.view(reverse_key,bars);
            // Python lecture_trend._annotate：失败翻向反破冻结极值时，同一根点建立新的反向攻击。
            // 只能使用旧攻击极值与攻击日前已知的旧锚点，不能退回多/空头而吞掉空翻多入口。
            if key_point.order()<origin_point.order()&&origin_point.order()<point.order()&&key_point.known_index<point.index{
                direction=Some(reversal);anchor=Some(reverse_origin);key=Some(reverse_key);
                attack=Some(Attack{origin:reverse_origin,extreme:id,anchor:reverse_origin,key:reverse_key,checked:false});
                suspicion=false;result.preceding_start=trend_origin;
                result.state=Some(if current==Direction::Up{TrendState::BearToBull}else{TrendState::BullToBear});
                record(pool,&mut result,if current==Direction::Up{"翻空为多"}else{"翻多为空"},id,reverse_key,Some(reverse_key),
                    vec![("basis",Fact::Static("confirmed_polyline_extreme")),("anchor",Fact::Point(reverse_origin)),
                        ("impulse_origin",Fact::Point(reverse_origin)),("attack",Fact::Point(id)),
                        ("confirmed_extreme",Fact::Point(reverse_origin))],bars);
            }else{
                anchor=Some(id);key=previous;attack=None;suspicion=false;
                result.state=Some(if current==Direction::Up{TrendState::Bull}else{TrendState::Bear});
            }
        }
        context(&mut result,point,direction,key,anchor);previous=Some(id);
    }
        result.direction=direction;result.structure_direction=direction;result.key=key;result.anchor=anchor;
        *self=Self{highs,lows,extremes,anchored_extremes,direction,anchor,key,attack,suspicion,previous,trend_origin,
            state:result.state,preceding_start:result.preceding_start};Ok(result)
    }
}
/// 每个已闭合点保存推进后状态；尾部修订只撤回受影响的后缀。
#[derive(Clone,Default)]
pub(super) struct SixStateCursor {
    pub(super) points:Vec<PointId>,snapshots:Vec<SixStateMachine>,contexts:Vec<super::arena::RefContext>,
    events:Vec<Vec<RefEvent>>,state:SixStateMachine,
}
impl SixStateCursor {
    pub(super) fn sync_from(&mut self,pool:&mut PointArena,bars:&[KernelBar],start:usize,points:impl Iterator<Item=PointId>,level:u8)->Result<(),String>{
        if start>self.points.len(){return Err("six-state checkpoint exceeds retained points".into());}
        self.points.truncate(start);self.snapshots.truncate(start);self.contexts.truncate(start);self.events.truncate(start);
        self.state=self.snapshots.last().cloned().unwrap_or_default();
        for id in points {
            let next=self.state.advance(pool,bars,id,level)?;
            let context=next.contexts.first().copied().ok_or("six-state input must be a closed point")?;
            self.points.push(id);self.snapshots.push(self.state.clone());self.contexts.push(context);self.events.push(next.events);
        }
        Ok(())
    }
    pub(super) fn before(&self,count:usize)->SixStateMachine{count.checked_sub(1).map(|i|self.snapshots[i].clone()).unwrap_or_default()}
    pub(super) fn events_from(&self,start:usize,end:usize)->impl Iterator<Item=&RefEvent>{self.events[start.min(end)..end].iter().flatten()}
    pub(super) fn summary(&self,level:u8)->RefFrame {
        let mut result=RefFrame::empty(level);result.state=self.state.state;result.direction=self.state.direction;
        result.structure_direction=self.state.direction;result.key=self.state.key;result.anchor=self.state.anchor;result.preceding_start=self.state.preceding_start;result
    }
    pub(super) fn frame(&self,level:u8)->RefFrame {
        let mut result=self.summary(level);result.points=self.points.clone();result.contexts=self.contexts.clone();
        result.segment_states=self.contexts.iter().map(|context|context.state).collect();
        result.events=self.events.iter().flatten().cloned().collect();result.state=self.state.state;
        result.direction=self.state.direction;result.structure_direction=self.state.direction;result.key=self.state.key;
        result.anchor=self.state.anchor;result.preceding_start=self.state.preceding_start;result
    }
}
pub(crate) fn six_state_indices(pool:&mut PointArena,bars:&[KernelBar],points:&[PointId],level:u8)->Result<RefFrame,String>{
    if level>3{return Err("trend level must be 0 through 3".into());}
    let mut cursor=SixStateCursor::default();
    let closed:Vec<_>=points.iter().copied().filter(|id|pool.view(*id,bars).is_closed()).collect();
    cursor.sync_from(pool,bars,0,closed.into_iter(),level)?;
    let mut result=cursor.frame(level);let mut closed_states=result.segment_states.into_iter();
    result.segment_states=points.iter().map(|id|if pool.view(*id,bars).is_closed(){closed_states.next().flatten()}else{None}).collect();
    result.points=points.to_vec();Ok(result)
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


#[cfg(test)]
mod failed_flip_tests {
    use super::*;
    use super::super::{KernelPointData,PointState};
    use crate::indexed_json::Value;
    fn points(level:u8,invert:bool)->Vec<KernelPoint>{
        [10.0,5.0,12.0,7.0,14.0,6.0,15.0,10.0].iter().enumerate().map(|(index,value)|{
            let kind=if (index%2==0)^invert{PointKind::H}else{PointKind::L};
            KernelPoint::new(KernelPointData{index,ordinal:0,time:format!("2020-01-{:02}",index+1),known_index:index,
                kind,value:if invert{30.0-*value}else{*value},level,path:0,state:PointState::Confirmed,evidence:Value::Null})
        }).collect()
    }
    #[test]
    fn failed_flip_emits_the_opposite_flip_on_its_own_known_day_at_every_level(){
        for level in 0..=3{for invert in [false,true]{
            let source=points(level,invert);let frame=six_state(&source[..7],level).unwrap();
            let event=frame.events.last().unwrap();
            assert_eq!(event.title,if invert{"翻多为空"}else{"翻空为多"});
            assert_eq!(event.known_index,6);assert_eq!(event.point.index,6);
            assert_eq!(event.key.as_ref().unwrap().index,4);
            assert_eq!(event.evidence["confirmed_extreme"]["index"].as_u64(),Some(5));
            assert_eq!(frame.state,Some(if invert{TrendState::BullToBear}else{TrendState::BearToBull}));
            let after=six_state(&source,level).unwrap();
            assert_eq!(after.state,Some(if invert{TrendState::BullBearAlternation}else{TrendState::BearBullAlternation}));
        }}
    }
    #[test]
    fn failed_flip_cannot_use_a_key_first_known_on_the_attack_day(){
        for level in 0..=3{
            let mut source=points(level,false);source[4].known_index=6;source[5].known_index=6;source[6].known_index=7;
            let frame=six_state(&source[..7],level).unwrap();
            assert!(!frame.events.iter().any(|event|event.title=="翻空为多"));
        }
    }
}

#[cfg(test)]
mod context_tests {
    use super::*;
    use super::super::{KernelPointData,PointState};
    use crate::indexed_json::Value;
    fn points(values:&[f64],level:u8,mirror:bool)->Vec<KernelPoint>{
        values.iter().enumerate().map(|(index,value)|KernelPoint::new(KernelPointData{
            index,ordinal:0,time:format!("2020-01-{:02}",index+1),known_index:index,level,path:0,
            kind:if (index%2==0)!=mirror{PointKind::H}else{PointKind::L},
            value:if mirror{30.0-value}else{*value},state:PointState::Reversal,evidence:Value::Null,
        })).collect()
    }
    #[test]
    fn an_unanchored_first_extreme_cannot_hide_later_flips_at_any_level(){
        for level in 1..=3{for mirror in [false,true]{
            let input=points(&[13.0,4.6,6.4,4.7,9.0,3.8],level,mirror);
            let prefix=six_state(&input[..5],level).unwrap();
            assert_eq!(prefix.anchor.as_ref().map(|p|p.index),Some(4));
            assert_eq!(prefix.key.as_ref().map(|p|p.index),Some(3));
            let frame=six_state(&input,level).unwrap();
            let event=frame.events.iter().find(|e|e.title==if mirror{"翻空为多"}else{"翻多为空"}).unwrap();
            assert_eq!(event.known_index,5);assert_eq!(event.key.as_ref().unwrap().index,3);
            assert_eq!(frame.points,input);
        }}
    }
    #[test]
    fn a_valid_frozen_key_is_not_replaced_with_a_later_local_pair(){
        for level in 1..=3{
            let mut input=points(&[4.0,13.0,5.0,10.0,6.0,11.0,5.5],level,false);
            for p in &mut input{p.kind=p.kind.opposite();}
            let frame=six_state(&input,level).unwrap();
            assert_eq!(frame.anchor.as_ref().map(|p|p.index),Some(1));
            assert_eq!(frame.key.as_ref().map(|p|p.index),Some(0));
            assert!(!frame.events.iter().any(|e|e.title=="翻多为空"));
        }
    }
    #[test]
    fn bull_retracement_and_extension_keep_the_frozen_whole_wave_origin(){
        for level in 1..=3{
            for values in [&[10.0,5.0,9.0,4.0,8.0,6.0,11.0,7.0][..],&[10.0,5.0,9.0,4.0,8.0,6.0,11.0,5.0,13.0,7.0][..]]{
                let frame=six_state(&points(values,level,false),level).unwrap();
                assert_eq!(frame.state,Some(TrendState::BearBullAlternation));
                let event=frame.events.iter().find(|e|e.title=="空多交替").unwrap();
                assert_eq!(event.origin.as_ref().unwrap().index,3);
            }
        }
    }
}
#[cfg(test)]
mod incremental_segment_tests {
    use super::*;
    use super::super::{KernelPointData,PointState};
    fn input(level:u8,mirror:bool)->Vec<KernelPoint>{
        [13.0,4.6,6.4,4.7,9.0,3.8,10.0,5.0,12.0].into_iter().enumerate().map(|(index,value)|{
            let kind=if (index%2==0)^mirror{PointKind::H}else{PointKind::L};
            KernelPoint::new(KernelPointData{index,ordinal:0,time:format!("2020-01-{:02}",index+1),kind,
                value:if mirror{30.0-value}else{value},known_index:index,level,path:0,state:PointState::Confirmed,evidence:crate::indexed_json::Value::Null})
        }).collect()
    }
    #[test]
    fn every_level_keeps_segment_states_and_events_when_appending_or_revising_a_tail(){
        for level in 0..=3{for mirror in [false,true]{
            let source=input(level,mirror);let mut pool=PointArena::default();
            let ids:Vec<_>=source.iter().map(|point|pool.import(point)).collect();let mut cursor=SixStateCursor::default();
            for end in 1..=ids.len(){
                cursor.sync_from(&mut pool,&[],end-1,ids[end-1..end].iter().copied(),level).unwrap();
                let expected=six_state_indices(&mut pool,&[],&ids[..end],level).unwrap();
                assert_eq!(pool.materialize(&cursor.frame(level),&[]),pool.materialize(&expected,&[]));
                assert_eq!(cursor.frame(level).segment_states.len(),end);
            }
            // 更早的确认时钟修订必须撤回该点后的状态，而不是沿用最终快照。
            let mut revised=source[6].clone();revised.known_index=7;let replacement=pool.import(&revised);
            let mut changed=ids.clone();changed[6]=replacement;
            cursor.sync_from(&mut pool,&[],6,changed[6..].iter().copied(),level).unwrap();
            let expected=six_state_indices(&mut pool,&[],&changed,level).unwrap();
            assert_eq!(pool.materialize(&cursor.frame(level),&[]),pool.materialize(&expected,&[]));
        }}
    }
}
