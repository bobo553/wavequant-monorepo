//! trend_confirmation._qualify、trend_lifecycle._consume/_price 与普通交替时间门禁。
//! 证书与结构几何分离；历史首次确认保留，随后破坏由单独失效事件记录。
use super::{Direction, KernelBar, KernelEvent, KernelFrame, KernelPoint, PointKind, TrendState, validate_bars};
use rust_decimal::Decimal;
use serde::{Deserialize, Serialize};
use crate::indexed_json::{Value,json};
use std::{collections::BTreeMap,str::FromStr};

#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct DirectionCertificate {
    pub known_index:usize, pub rule:String, pub direction:Direction,
    pub origin:KernelPoint, pub broken_key:KernelPoint, pub flip:KernelPoint,
    pub counter:Option<KernelPoint>, pub confirmed_by:Value,
    pub retracement_origin:Option<KernelPoint>,
}
fn dec(price:f64)->Result<Decimal,String> {
    if !price.is_finite() || price<=0.0 {return Err("positive finite confirmation price required".into());}
    Decimal::from_str(&price.to_string()).map_err(|_|"confirmation price exceeds decimal range".into())
}
fn extreme(bar:&KernelBar,direction:Direction)->f64 {if direction==Direction::Up {bar.high}else{bar.low}}
fn adverse(bar:&KernelBar,direction:Direction)->f64 {if direction==Direction::Up {bar.low}else{bar.high}}
fn point_from_bar(bar:&KernelBar,direction:Direction,template:&KernelPoint)->KernelPoint {
    let mut point=template.clone();point.index=bar.index;point.ordinal=0;point.time=bar.time.clone();
    point.kind=direction.target();point.value=extreme(bar,direction);point.known_index=bar.index;point
}

/// 同一证书生产器适用于上升/下降及全部层级。N 目标证书由交易模块独立提供。
pub fn qualify_direction(source:&[KernelPoint],origin_position:usize,same_level_key:Option<&KernelPoint>,
    bars:&[KernelBar],asof:usize,direction:Direction)->Result<Option<DirectionCertificate>,String> {
    validate_bars(bars)?;
    if asof>=bars.len() || bars.iter().enumerate().any(|(i,b)|b.index!=i) {return Err("confirmation requires zero-based contiguous market bars".into());}
    let Some(base)=source.get(origin_position) else {return Ok(None);};
    if base.kind!=direction.target().opposite() || !base.is_closed() || base.known_index>asof {return Ok(None);}
    let sign=direction.sign();
    let failed=(base.index+1..=asof).find(|i|sign*(adverse(&bars[*i],direction)-base.value)<0.0).unwrap_or(asof+1);
    let own=same_level_key.filter(|key|key.kind==direction.target() && key.order()<base.order()
        && sign*(key.value-base.value)>0.0 && key.known_index<=asof);
    let mut proofs:Vec<DirectionCertificate>=Vec::new();
    if let Some(key)=own {
        let start=(base.index+1).max(base.known_index).max(key.known_index+1);
        if let Some(index)=(start..failed.min(asof+1)).find(|i|sign*(extreme(&bars[*i],direction)-key.value)>0.0) {
            let flip=point_from_bar(&bars[index],direction,base);
            proofs.push(DirectionCertificate {known_index:index,rule:"strict_same_level_market_key_break".into(),direction,
                origin:base.clone(),broken_key:key.clone(),flip:flip.clone(),counter:None,
                confirmed_by:json!(flip),retracement_origin:None});
        }
    }
    let frozen=source[..origin_position].iter().rev().find(|p|p.kind==direction.target() && p.is_closed() && p.known_index<=asof);
    let Some(frozen)=frozen.filter(|p|sign*(p.value-base.value)>0.0) else {return Ok(proofs.into_iter().min_by_key(|p|p.known_index));};
    let causal_limit=failed.min(proofs.iter().map(|p|p.known_index).min().unwrap_or(asof+1));
    let mut flips:Vec<(usize,&KernelPoint,&KernelPoint)>=Vec::new();
    let mut strongest:Option<&KernelPoint>=None;
    for (position,point) in source.iter().enumerate().skip(origin_position+1) {
        if !point.is_closed() {continue;}
        if point.known_index>=causal_limit {break;}
        if point.kind!=direction.target() || sign*(point.value-frozen.value)<=0.0
            || frozen.known_index>=point.index || strongest.is_some_and(|p|sign*(point.value-p.value)<=0.0) {continue;}
        let retrace_origin=if strongest.is_some() {
            source[origin_position..position].iter().rev().find(|p|p.kind==base.kind && p.is_closed()).unwrap_or(base)
        }else{base};
        strongest=Some(point);flips.push((position,point,retrace_origin));
    }
    for (cycle,(position,flip,retrace_origin)) in flips.iter().enumerate() {
        let Some(counter)=source.get(position+1).filter(|p|p.is_closed() && p.kind==base.kind && flip.order()<p.order()) else{continue;};
        let signed=if direction==Direction::Up {Decimal::ONE}else{-Decimal::ONE};
        let impulse=signed*(dec(flip.value)?-dec(retrace_origin.value)?);
        let movement=signed*(dec(flip.value)?-dec(counter.value)?);
        if impulse<=Decimal::ZERO || movement<=Decimal::ZERO || movement>=impulse || movement*Decimal::from(3)>=impulse*Decimal::from(2) {continue;}
        let start=base.known_index.max(flip.known_index).max(counter.known_index).max(retrace_origin.known_index)+1;
        let next=flips.get(cycle+1).map_or(asof+1,|(_,p,_)|p.known_index);
        let stop=failed.min(next).min(asof+1);
        for index in counter.index+1..stop {
            if sign*(adverse(&bars[index],direction)-counter.value)<0.0 {break;}
            if index<start {continue;}
            if sign*(bars[index-1].close-flip.value)<=0.0 && sign*(bars[index].close-flip.value)>0.0 {
                proofs.push(DirectionCertificate {known_index:index,rule:"source_key_break_alternation_then_market_turn".into(),direction,
                    origin:base.clone(),broken_key:frozen.clone(),flip:(*flip).clone(),counter:Some(counter.clone()),
                    confirmed_by:json!({"index":index,"time":bars[index].time,"kind":"K","value":bars[index].close,
                        "available_at":index,"market_high":bars[index].high,"market_low":bars[index].low}),
                    retracement_origin:Some((*retrace_origin).clone())});
                return Ok(proofs.into_iter().min_by_key(|p|p.known_index));
            }
        }
    }
    Ok(proofs.into_iter().min_by_key(|p|p.known_index))
}

#[derive(Debug,Clone)]
struct Cycle {direction:Direction,origin:KernelPoint,impulse:KernelPoint,key:KernelPoint,started:usize,
    counter:Option<KernelPoint>,alternated:Option<usize>,confirmed:bool,recovery:bool}
fn lifecycle(cycle:&Cycle,level:u8,now:usize,event:&str,rule:&str)->Value {
    let state=if cycle.confirmed {if cycle.direction==Direction::Up {"转多确认"}else{"转空确认"}}
        else if cycle.alternated.is_some(){if cycle.direction==Direction::Up {"空多交替"}else{"多空交替"}}
        else if cycle.direction==Direction::Up {"空翻多"}else{"多翻空"};
    json!({"event":event,"bar_index":now,"available_index":now,"trend_level":level,
        "source_path":format!("lecture-{}",cycle.origin.path),"state":state,"direction":cycle.direction,
        "cycle_started_index":cycle.started,"origin_index":cycle.origin.index,"origin_price":cycle.origin.value,
        "origin_ordinal":cycle.origin.ordinal,"breakout_level":cycle.impulse.value,
        "flip_index":cycle.impulse.index,"flip_known_index":cycle.impulse.known_index,"flip_ordinal":cycle.impulse.ordinal,
        "counter_index":cycle.counter.as_ref().map(|p|p.index),"counter_ordinal":cycle.counter.as_ref().map(|p|p.ordinal),
        "counter_price":cycle.counter.as_ref().map(|p|p.value),"alternation_index":cycle.alternated,
        "broken_key_index":cycle.key.index,"broken_key_known_index":cycle.key.known_index,"broken_key_price":cycle.key.value,
        "flip_confirmed":true,"special_bull_recovery":cycle.recovery,"confirmation_rule":rule,
        "source_event":if cycle.alternated.is_some() {if cycle.direction==Direction::Up {"空多交替"}else{"多空交替"}} else if cycle.direction==Direction::Up {"翻空为多"}else{"翻多为空"},
        "source_known_index":cycle.impulse.known_index.max(cycle.origin.known_index).max(cycle.key.known_index),
        "rule_version":"rust_causal_ordinary_lifecycle_v1"})
}
fn emit(output:&mut Vec<KernelEvent>,cycle:&Cycle,level:u8,now:usize,event:&str,rule:&str) {
    let mut proof=lifecycle(cycle,level,now,event,rule);
    if event=="trend_lifecycle_invalidated" {proof["reason"]=json!(rule);}
    output.push(KernelEvent {title:event.into(),known_index:now,point:cycle.counter.clone().unwrap_or_else(||cycle.impulse.clone()),
        key:Some(cycle.key.clone()),origin:Some(cycle.origin.clone()),evidence:json!({"lifecycle_event":proof})});
}
fn origin_from_event(event:&KernelEvent)->Option<KernelPoint> {
    event.evidence.get("confirmed_extreme").or_else(||event.evidence.get("anchor"))
        .and_then(|raw|crate::indexed_json::from_value(raw.clone()).ok())
}
fn ordinary_bull_counter(cycle:&Cycle,points:&[KernelPoint],bars:&[KernelBar],now:usize)->Result<Option<(KernelPoint,&'static str)>,String> {
    confirm_ordinary_bull_counter(&cycle.origin,&cycle.impulse,points,bars,now)
}
/// 资格与旧生命周期共用普通确认规则；结构六态的 67% 不替代严格 2/3。
pub(crate) fn confirm_ordinary_bull_counter(origin:&KernelPoint,impulse:&KernelPoint,points:&[KernelPoint],bars:&[KernelBar],now:usize)
    ->Result<Option<(KernelPoint,&'static str)>,String> {
    confirm_ordinary_bull_counter_after(origin,impulse,points,bars,now,None)
}
/// 重开确认先排除旧窗口已消费的低点，沿用 Python 的 after_index 语义。
pub(crate) fn confirm_ordinary_bull_counter_after(origin:&KernelPoint,impulse:&KernelPoint,points:&[KernelPoint],bars:&[KernelBar],now:usize,after:Option<usize>)
    ->Result<Option<(KernelPoint,&'static str)>,String> {
    #[cfg(feature="qualification-timing")]
    let _profile_scope=crate::analysis::kernel::qualification_profile::enter("ordinary_confirmation");

    if origin.index>=impulse.index || impulse.index>=now || after.is_some_and(|after|now<=after) {return Ok(None);}
    if bars[origin.index+1..=now].iter().any(|bar|bar.low<origin.value) {return Ok(None);}
    let counter=points.iter().filter(|p|p.path==origin.path && p.kind==PointKind::L && p.is_closed()
        && p.index>impulse.index && p.known_index<=now && after.is_none_or(|after|p.known_index>after))
        .min_by(|a,b|a.value.total_cmp(&b.value).then(a.order().cmp(&b.order())));
    let Some(counter)=counter else{return Ok(None);};
    if counter.value<=origin.value || counter.value>=impulse.value
        || bars[counter.index+1..=now].iter().any(|bar|bar.low<counter.value) {return Ok(None);}
    let span=dec(impulse.value)?-dec(origin.value)?;
    let depth=dec(impulse.value)?-dec(counter.value)?;
    let rebreak=points.iter().find(|p|p.path==origin.path && p.kind==PointKind::H && p.is_closed()
        && p.index>counter.index && p.value>impulse.value && p.known_index<=now);
    let shallow=depth*Decimal::from(3)<span*Decimal::from(2);
    if !shallow && rebreak.is_none() {return Ok(None);}
    let minimum_close=bars[impulse.index+1..=counter.index].iter().map(|bar|bar.close).min_by(f64::total_cmp).ok_or("missing pullback candles")?;
    let short_shallow=(dec(impulse.value)?-dec(minimum_close)?)*Decimal::from(3)<span*Decimal::from(2)
        && (counter.index-impulse.index)*2<impulse.index-origin.index;
    if short_shallow {
        let crossing=(counter.index+1..=now).find(|i|bars[*i].close>impulse.value);
        let Some(crossing)=crossing else{return Ok(None);};
        if bars[counter.index..=crossing].iter().any(|bar|bar.low<counter.value) {return Ok(None);}
    }
    Ok(Some((counter.clone(),if shallow {"confirmed_higher_pullback_strictly_below_two_thirds"}
        else {"confirmed_higher_pullback_then_confirmed_flip_high_rebreak"})))
}

/// 对同一已计算结构附加正式生命周期；不改变点、价格、来源或动态尾部。
/// 成交量/N/缺口及特殊恢复通道可附加自己的独立证明，不能把待定标签直接放行。
pub fn attach_confirmations(frame:&mut KernelFrame,bars:&[KernelBar])->Result<(),String> {
    validate_bars(bars)?;
    if bars.iter().enumerate().any(|(i,b)|b.index!=i) {return Err("lifecycle requires zero-based contiguous bars".into());}
    let mut facts:Vec<_>=frame.events.iter().filter(|event|matches!(event.title.as_str(),"翻空为多"|"翻多为空"|"多空交替"|"空多交替")).cloned().collect();
    facts.sort_by_key(|event|(event.known_index,event.point.index,event.point.ordinal));
    let mut cycles:BTreeMap<usize,Cycle>=BTreeMap::new();
    let mut output=Vec::new();let mut cursor=0;
    let mut recoveries:BTreeMap<usize,Recovery>=BTreeMap::new();
    for now in 0..bars.len() {
        while cursor<facts.len() && facts[cursor].known_index<=now {
            let fact=&facts[cursor];cursor+=1;
            let up=matches!(fact.title.as_str(),"翻空为多"|"空多交替");
            let direction=if up {Direction::Up}else{Direction::Down};
            if matches!(fact.title.as_str(),"翻空为多"|"翻多为空") {
                let (Some(origin),Some(key))=(origin_from_event(fact),fact.key.clone()) else{continue;};
                if origin.kind!=direction.target().opposite() || fact.point.kind!=direction.target()
                    || !origin.is_closed() || !fact.point.is_closed() || !key.is_closed()
                    || key.level!=frame.level || origin.level!=frame.level || fact.point.level!=frame.level
                    || origin.path!=key.path || origin.path!=fact.point.path
                    || key.order()>=origin.order() || origin.order()>=fact.point.order()
                    || direction.sign()*(fact.point.value-key.value)<=0.0
                    || [origin.known_index,key.known_index,fact.point.known_index].iter().any(|i|*i>now) {continue;}
                if up && (recoveries.get(&origin.path).is_some_and(|state|state.breach.is_some())
                    || cycles.get(&origin.path).is_some_and(|cycle|cycle.recovery && cycle.alternated.is_none())) {continue;}
                if !up {recoveries.remove(&origin.path);}
                let current=cycles.get(&origin.path);
                let fresh=current.is_none_or(|cycle|cycle.direction!=direction);
                if !fresh && current.is_some_and(|cycle|cycle.alternated.is_some()
                    || direction.sign()*(fact.point.value-cycle.impulse.value)<=0.0 || fact.point.order()<=cycle.impulse.order()) {continue;}
                let cycle=if fresh {Cycle {direction,origin:origin.clone(),impulse:fact.point.clone(),key,started:now,counter:None,alternated:None,confirmed:false,recovery:false}}
                    else {let mut cycle=current.cloned().ok_or("missing cycle")?;cycle.impulse=fact.point.clone();cycle};
                emit(&mut output,&cycle,frame.level,now,if fresh {"trend_lifecycle_state"}else{"trend_lifecycle_anchor"},"confirmed_frozen_key_flip");
                if up {recoveries.insert(origin.path,Recovery {seed:cycle.clone(),breach:None,recovered:None,lower_lows:0});}
                cycles.insert(origin.path,cycle);
            } else if !up {
                if let Some(cycle)=cycles.get_mut(&fact.point.path).filter(|cycle|cycle.direction==direction && cycle.alternated.is_none()) {
                    let impulse=fact.evidence.get("flip_extreme").and_then(|v|crate::indexed_json::from_value::<KernelPoint>(v.clone()).ok());
                    let Some(impulse)=impulse else{continue;};
                    if impulse.order()<cycle.impulse.order() || impulse.value>cycle.impulse.value
                        || fact.point.order()<=impulse.order() || fact.known_index<impulse.known_index {continue;}
                    cycle.impulse=impulse;cycle.counter=Some(fact.point.clone());cycle.alternated=Some(now);
                    emit(&mut output,cycle,frame.level,now,"trend_lifecycle_state","confirmed_source_countermove_below_67_percent");
                }
            }
        }
        advance_recoveries(&mut recoveries,&mut cycles,&frame.points,bars,frame.level,now,&mut output);
        let paths:Vec<_>=cycles.keys().copied().collect();
        for path in paths {
            let Some(mut cycle)=cycles.get(&path).cloned() else{continue;};
            if cycle.direction==Direction::Up && cycle.alternated.is_none() {
                if let Some((counter,rule))=ordinary_bull_counter(&cycle,&frame.points,bars,now)? {
                    cycle.counter=Some(counter);cycle.alternated=Some(now);
                    emit(&mut output,&cycle,frame.level,now,"trend_lifecycle_state",rule);
                }
            }
            let defense=cycle.counter.as_ref().unwrap_or(&cycle.origin);
            if cycle.direction.sign()*(adverse(&bars[now],cycle.direction)-defense.value)<0.0 {
                emit(&mut output,&cycle,frame.level,now,"trend_lifecycle_invalidated","defense_broken");
                if cycle.counter.is_some() && cycle.direction.sign()*(adverse(&bars[now],cycle.direction)-cycle.origin.value)>=0.0 {
                    cycle.counter=None;cycle.alternated=None;cycle.confirmed=false;
                    emit(&mut output,&cycle,frame.level,now,"trend_lifecycle_anchor","countermove_extended_with_frozen_origin_defended");
                    if cycle.direction==Direction::Up && cycle.alternated.is_some() {recoveries.remove(&path);} cycles.insert(path,cycle);
                }else{cycles.remove(&path);}
                continue;
            }
            if now>0 && !cycle.confirmed && cycle.alternated.is_some_and(|index|now>index)
                && cycle.direction.sign()*(bars[now-1].close-cycle.impulse.value)<=0.0
                && cycle.direction.sign()*(bars[now].close-cycle.impulse.value)>0.0 {
                cycle.confirmed=true;
                emit(&mut output,&cycle,frame.level,now,"trend_lifecycle_state","strict_close_cross_after_confirmed_alternation");
                if let Some(event)=output.last_mut() {
                    event.evidence["lifecycle_event"]["previous_close"]=json!(bars[now-1].close);
                    event.evidence["lifecycle_event"]["confirmation_close"]=json!(bars[now].close);
                }
            }
            if cycle.direction==Direction::Up && cycle.alternated.is_some() {recoveries.remove(&path);} cycles.insert(path,cycle);
        }
    }
    if let Some(cycle)=cycles.values().max_by_key(|cycle|cycle.started) {
        frame.state=Some(if cycle.confirmed {if cycle.direction==Direction::Up {TrendState::Bull}else{TrendState::Bear}}
            else if cycle.alternated.is_some() {if cycle.direction==Direction::Up {TrendState::BearBullAlternation}else{TrendState::BullBearAlternation}}
            else if cycle.direction==Direction::Up {TrendState::BearToBull}else{TrendState::BullToBear});
    }
    frame.events.retain(|event|event.evidence.get("lifecycle_event").is_none());
    frame.events.extend(output);
    Ok(())
}


#[derive(Clone)]
struct Recovery {seed:Cycle,breach:Option<usize>,recovered:Option<usize>,lower_lows:usize}
/// trend_lifecycle.advance_bull_recovery：最多三次连续更低低价，严格收回只是恢复候选。
fn advance_recoveries(recoveries:&mut BTreeMap<usize,Recovery>,cycles:&mut BTreeMap<usize,Cycle>,
    points:&[KernelPoint],bars:&[KernelBar],level:u8,now:usize,events:&mut Vec<KernelEvent>) {
    let paths:Vec<_>=recoveries.keys().copied().collect();
    for path in paths {
        let Some(mut recovery)=recoveries.get(&path).cloned() else{continue;};
        if now<=recovery.seed.started {continue;}
        let low=bars[now].low;let original=recovery.seed.origin.value;
        if recovery.breach.is_none() {
            if low>=original {continue;}
            recovery.breach=Some(now);
            if cycles.get(&path).is_some_and(|cycle|cycle.direction==Direction::Up) {cycles.remove(&path);}
            emit(events,&recovery.seed,level,now,"trend_lifecycle_invalidated","bull_recovery_pending");
            if let Some(event)=events.last_mut() {event.evidence["lifecycle_event"]["reason"]=json!("bull_recovery_pending");}
        }
        recovery.lower_lows=if now>0 && low<bars[now-1].low {recovery.lower_lows+1}else{0};
        if recovery.lower_lows>3 {
            recoveries.remove(&path);
            emit(events,&recovery.seed,level,now,"trend_lifecycle_recovery_expired","four_consecutive_lower_lows");
            continue;
        }
        if bars[now].close<original {recovery.recovered=None;}
        else if bars[now].close>original && recovery.recovered.is_none() {
            recovery.recovered=Some(now);
            emit(events,&recovery.seed,level,now,"trend_lifecycle_recovered","intraday_break_then_close_reclaim_with_at_most_three_lower_lows");
            if let Some(event)=events.last_mut() {
                event.evidence["lifecycle_event"]["original_low_index"]=json!(recovery.seed.origin.index);
                event.evidence["lifecycle_event"]["breach_index"]=json!(recovery.breach);
                event.evidence["lifecycle_event"]["recovered_index"]=json!(recovery.recovered);
            }
        }
        if let (Some(breach),Some(recovered))=(recovery.breach,recovery.recovered) {
            let visible:Vec<_>=points.iter().filter(|point|point.path==path && point.is_closed() && point.known_index<=now).collect();
            let found=visible.windows(3).find(|triple| {
                let key=triple[0];let origin=triple[1];let impulse=triple[2];
                key.kind==PointKind::H && origin.kind==PointKind::L && impulse.kind==PointKind::H
                    && origin.index>=breach && impulse.index>=recovered && key.known_index<impulse.index
                    && key.index<origin.index && origin.index<impulse.index
                    && impulse.value>key.value && impulse.value>origin.value && origin.value<key.value
                    && !bars[origin.index+1..=now].iter().any(|bar|bar.low<origin.value)
            });
            if let Some(triple)=found {
                let cycle=Cycle {direction:Direction::Up,origin:triple[1].clone(),impulse:triple[2].clone(),key:triple[0].clone(),
                    started:now,counter:None,alternated:None,confirmed:false,recovery:true};
                emit(events,&cycle,level,now,"trend_lifecycle_state","bounded_lower_low_recovery_then_same_level_break");
                if let Some(event)=events.last_mut() {
                    event.evidence["lifecycle_event"]["source_event"]=json!("短暂跌破后同级再次空翻多");
                    event.evidence["lifecycle_event"]["original_low_index"]=json!(recovery.seed.origin.index);
                    event.evidence["lifecycle_event"]["breach_index"]=json!(breach);
                    event.evidence["lifecycle_event"]["recovered_index"]=json!(recovered);
                }
                if cycle.direction==Direction::Up && cycle.alternated.is_some() {recoveries.remove(&path);} cycles.insert(path,cycle);recoveries.remove(&path);continue;
            }
        }
        recoveries.insert(path,recovery);
    }
}
