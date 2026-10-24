//! source_bear_exit.py、failed_breakout_exit.py 的同日来源路由与失败突破生产器。
use super::{KernelFrame,KernelEvent,KernelPoint,PointKind};
use super::super::trading::{RuleCandle,qualification::confirmed_source_bear};
use rust_decimal::{Decimal,prelude::ToPrimitive};
use crate::indexed_json::{Value,json};
use std::{collections::BTreeMap,str::FromStr};

fn price(value:f64)->Option<Decimal> {Decimal::from_str(&value.to_string()).ok()}
fn append(frame:&mut KernelFrame,point:&KernelPoint,proof:Value) {
    let Some(index)=proof.get("bar_index").and_then(Value::as_u64).and_then(|v|usize::try_from(v).ok()) else{return;};
    let title=proof.get("event").and_then(Value::as_str).unwrap_or("trend_exit_confirmation").to_owned();
    frame.events.push(KernelEvent {title,known_index:index,point:point.clone(),key:None,origin:None,evidence:json!({"lifecycle_event":proof})});
}
fn source_key_breaks(source:&KernelFrame,bars:&[RuleCandle])->Vec<(KernelPoint,Value)> {
    let mut result=Vec::new();
    for now in 1..bars.len() {
        let mut paths:BTreeMap<usize,Vec<&KernelPoint>>=BTreeMap::new();
        for point in &source.points {if point.is_closed() && point.known_index<=now {paths.entry(point.path).or_default().push(point);}}
        for (path,points) in paths {
            let Some(position)=points.iter().rposition(|point|point.kind==PointKind::H) else{continue;};
            if position==0 {continue;}
            let origin=points[position];let key=points[position-1];
            if key.kind!=PointKind::L || key.index>=origin.index || key.known_index>=now || origin.known_index>now {continue;}
            let (Some(high),Some(threshold))=(price(origin.value),price(key.value)) else{continue;};
            if threshold<=Decimal::ZERO || high<=threshold || bars[now].low>=threshold {continue;}
            let failure=(origin.index+1..=now).find(|i|bars[*i].high>high).unwrap_or(now+1);
            let start=(origin.index+1).max(origin.known_index).max(key.known_index+1);
            let first=(start..failure).find(|i|bars[*i].low<threshold);
            if first!=Some(now) {continue;}
            result.push((origin.clone(),json!({"event":"trend_source_market_key_broken","bar_index":now,"available_index":now,
                "trend_level":source.level,"source_path":format!("lecture-{path}"),"direction":"down",
                "confirmation_rule":"strict_same_level_market_key_break","confirmation_index":now,
                "origin_index":origin.index,"origin_known_index":origin.known_index,"origin_price":origin.value,
                "broken_key_index":key.index,"broken_key_known_index":key.known_index,"broken_key_price":key.value,
                "market_low":bars[now].low.to_f64()})));
        }
    }
    result
}

/// 只使用当前结构仍持有的最近正式翻空 seed；不拿失效旧周期补判放量。
pub fn attach_failed_breakouts(frame:&mut KernelFrame,bars:&[RuleCandle])->Result<(),String> {
    super::super::trading::validate_bars(bars)?;
    let mut seeds:Vec<_>=frame.events.iter().filter(|event|event.title=="翻多为空").cloned().collect();
    seeds.sort_by_key(|event|(event.known_index,event.point.index,event.point.ordinal));
    for (seed_position,seed) in seeds.iter().enumerate() {
        let Some(origin)=seed.evidence.get("confirmed_extreme").or_else(||seed.evidence.get("anchor"))
            .and_then(|raw|crate::indexed_json::from_value::<KernelPoint>(raw.clone()).ok()) else{continue;};
        let Some(key)=seed.key.as_ref() else{continue;};
        let low=&seed.point;
        if origin.kind!=PointKind::H || low.kind!=PointKind::L || key.kind!=PointKind::L
            || key.index>=origin.index || origin.index>=low.index || low.index>seed.known_index
            || !(low.value<key.value && key.value<origin.value)
            || ![&origin,low,key].iter().all(|point|frame.points.iter().any(|actual|actual==*point)) {continue;}
        let Some(anchor)=bars.get(origin.index) else{continue;};
        let Some(origin_high)=price(origin.value) else{continue;};
        if anchor.high!=origin_high {continue;}
        let stop=seeds.iter().skip(seed_position+1).find(|next|next.point.path==origin.path).map_or(bars.len(),|next|next.known_index.min(bars.len()));
        let mut attack=None;let mut mean=Decimal::ZERO;let mut consecutive=0;let mut longest=0;
        for index in seed.known_index+1..stop {
            let bar=&bars[index];
            consecutive=if bar.close>anchor.close {consecutive+1}else{0};longest=longest.max(consecutive);
            if longest>=3 {break;}
            if attack.is_none() {
                if bar.high<=origin_high {continue;}
                if index<20 {break;}
                mean=bars[index-20..index].iter().map(|bar|bar.volume).sum::<Decimal>()/Decimal::from(20);
                attack=Some(index);
            }
            if bar.close>=anchor.close {continue;}
            let valid=mean>Decimal::ZERO && bar.volume>=Decimal::TWO*mean && bar.open>Decimal::ZERO
                && (bar.open-bar.close)/bar.open>=Decimal::new(3,2) && bars[index-1].close>=anchor.close;
            if valid {
                let Some(attack_index)=attack else{continue;};
                let proof=json!({"event":"trend_bear_failed_breakout","confirmation_rule":"bear_flip_short_breakout_massive_bearish_close_rejection",
                    "bar_index":index,"available_index":index,"trend_level":frame.level,"source_path":format!("lecture-{}",origin.path),
                    "direction":"down","flip_confirmed":true,"prior_flip_origin_index":origin.index,"prior_flip_index":low.index,
                    "prior_flip_known_index":seed.known_index,"origin_index":origin.index,"origin_known_index":origin.known_index,
                    "flip_index":origin.index,"flip_known_index":origin.known_index,"breakout_level":anchor.close.to_f64(),
                    "anchor_high":origin.value,"attack_index":attack_index,"attack_high":bars[attack_index].high.to_f64(),
                    "previous_close":bars[index-1].close.to_f64(),"confirmation_close":bar.close.to_f64(),
                    "observed_open":bar.open.to_f64(),"observed_volume":bar.volume.to_f64(),"volume_baseline_end":attack_index-1,
                    "volume_window":20,"volume_mean":mean.to_f64(),"volume_min_multiple":2.0,"bearish_body_min":0.03,
                    "longest_held_sessions":longest,"stable_close_sessions":3,"stability_basis":"last_rise_high_close"});
                append(frame,&origin,proof);break;
            }
            attack=None;mean=Decimal::ZERO;consecutive=0;longest=0;
        }
    }
    Ok(())
}

/// 直接下级已确认空头，只结束本级资格，绝不回写本级几何或六态。
pub fn attach_source_bearish_exits(frame:&mut KernelFrame,source:&KernelFrame,bars:&[RuleCandle])->Result<(),String> {
    if frame.level==0 || source.level+1!=frame.level {return Err("bear exits require the immediately lower frame".into());}
    super::super::trading::validate_bars(bars)?;
    let level=frame.level;
    let mut confirmations:Vec<(KernelPoint,Value)>=source.events.iter().filter_map(|event|event.evidence.get("lifecycle_event")
        .map(|proof|(event.point.clone(),proof.clone()))).collect();
    confirmations.extend(source_key_breaks(source,bars));
    for (point,proof) in confirmations {
        let Some(now)=proof.get("bar_index").and_then(Value::as_u64).and_then(|value|usize::try_from(value).ok()) else{continue;};
        if now>=bars.len() || !confirmed_source_bear(&proof,now) {continue;}
        let Some(source_path)=proof.get("source_path").and_then(Value::as_str) else{continue;};
        let mut paths=BTreeMap::new();
        for own in frame.points.iter().chain(frame.candidate.iter()).filter(|own|own.known_index<=now) {
            let direct=own.evidence.get("source").and_then(|raw|raw.get("path")).and_then(Value::as_u64)
                .and_then(|path|usize::try_from(path).ok());
            if direct.is_some_and(|path|format!("lecture-{path}")==source_path) {paths.insert(own.path,());}
        }
        for path in paths.keys() {
            append(frame,&point,json!({"event":"trading_source_bear_confirmed","bar_index":now,"available_index":now,
                "trend_level":level,"source_path":format!("lecture-{path}"),"direct_source_path":source_path,
                "reason":"source_bear_trend_confirmed","source_confirmation":proof}));
        }
    }
    Ok(())
}


/// 已正式翻空后的首个真实反弹高/低结构继续破位；与六态防守失效独立。
pub fn attach_bearish_structure_breaks(frame:&mut KernelFrame,bars:&[RuleCandle])->Result<(),String> {
    super::super::trading::validate_bars(bars)?;
    let facts:Vec<_>=frame.events.iter().filter(|event|event.title=="翻多为空").cloned().collect();
    let mut seen=std::collections::BTreeSet::new();
    for now in 1..bars.len() {
        let mut latest:BTreeMap<usize,&KernelEvent>=BTreeMap::new();
        for fact in facts.iter().filter(|fact|fact.known_index<=now) {
            if latest.get(&fact.point.path).is_none_or(|old|old.known_index<=fact.known_index) {latest.insert(fact.point.path,fact);}
        }
        for (path,flip) in latest {
            let Some(anchor)=flip.evidence.get("confirmed_extreme").or_else(||flip.evidence.get("anchor"))
                .and_then(|raw|crate::indexed_json::from_value::<KernelPoint>(raw.clone()).ok()) else{continue;};
            if flip.key.is_none() || anchor.index>=flip.point.index || flip.known_index>=now {continue;}
            let mut following=frame.points.iter().filter(|point|point.path==path && point.is_closed()
                && point.known_index<=now && point.order()>flip.point.order());
            let (Some(rebound),Some(key))=(following.next().cloned(),following.next().cloned()) else{continue;};
            if rebound.kind!=PointKind::H || key.kind!=PointKind::L || flip.known_index>=rebound.index
                || rebound.index>=key.index || key.known_index>=now {continue;}
            if rebound.value>anchor.value && !super::waves::rejects_wick(&anchor,&flip.point,&rebound) {continue;}
            let direction=rebound.evidence.get("proof").and_then(|proof|proof.get("direction_after").or_else(||proof.get("direction")))
                .or_else(||rebound.evidence.get("direction_after")).and_then(Value::as_str);
            if direction!=Some("down") {continue;}
            let (Some(high),Some(threshold))=(price(rebound.value),price(key.value)) else{continue;};
            if bars[now].low>=threshold || threshold>bars[now-1].low
                || bars[rebound.index+1..=now].iter().any(|bar|bar.high>high) {continue;}
            if !seen.insert((path,flip.point.index,rebound.index,key.index)) {continue;}
            let proof=json!({"event":"trend_bear_structure_low_broken","bar_index":now,"available_index":now,
                "trend_level":frame.level,"source_path":format!("lecture-{path}"),"direction":"down","flip_confirmed":true,
                "confirmation_rule":"confirmed_bear_flip_then_bear_structure_low_break","prior_flip_origin_index":anchor.index,
                "prior_flip_index":flip.point.index,"prior_flip_known_index":flip.known_index,
                "origin_index":rebound.index,"origin_known_index":rebound.known_index,"flip_index":key.index,
                "flip_known_index":key.known_index,"breakout_level":key.value,"previous_low":bars[now-1].low.to_f64(),
                "market_low":bars[now].low.to_f64()});
            append(frame,&rebound,proof);
        }
    }
    Ok(())
}
