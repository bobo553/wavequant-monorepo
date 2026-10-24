//! 无行情 ordinary-local 私有 ABC 的严格来源发布门禁。
use super::{Direction,DirectionCertificate,KernelPoint,PointKind};
use crate::indexed_json::json;

fn identity(left:&KernelPoint,right:&KernelPoint)->bool {
    left.order()==right.order() && left.kind==right.kind && left.value==right.value && left.time==right.time
}
/// trend_confirmation._qualify 的 market=None 分支；只有旧本级 key 的真实来源突破。
pub fn qualify_source_direction(source:&[KernelPoint],position:usize,key:Option<&KernelPoint>,
    asof:usize,direction:Direction)->Option<DirectionCertificate> {
    let origin=source.get(position)?;
    if !origin.is_closed() || origin.known_index>asof || origin.kind!=direction.target().opposite(){return None;}
    let key=key.filter(|key|key.kind==direction.target() && key.order()<origin.order()
        && key.known_index<=asof && direction.sign()*(key.value-origin.value)>0.0)?;
    for point in source.iter().skip(position+1).filter(|point|point.is_closed() && point.known_index<=asof) {
        if point.kind==origin.kind && direction.sign()*(point.value-origin.value)<0.0 {return None;}
        if point.kind==direction.target() && direction.sign()*(point.value-key.value)>0.0 && key.known_index<point.index {
            return Some(DirectionCertificate {known_index:origin.known_index.max(key.known_index).max(point.known_index),
                rule:"strict_same_level_market_key_break".into(),direction,origin:origin.clone(),broken_key:key.clone(),
                flip:point.clone(),counter:None,confirmed_by:json!(point),retracement_origin:None});
        }
    }
    None
}
/// 仅用于 combined_a_entry._reduce_local_points 产生的候选，不授予正式趋势等级。
/// 无行情时 continuation/held_wave_floor 均不成立，L 必须独立取得严格旧压力突破。
pub fn certify_local_publication(candidates:&[KernelPoint],source:&[KernelPoint])->Result<Vec<KernelPoint>,String> {
    let asof=source.iter().map(|point|point.known_index).max().unwrap_or(0);
    let mut result:Vec<KernelPoint>=Vec::new();let mut pressure:Option<&KernelPoint>=None;
    for candidate in candidates {
        if candidate.evidence.get("confirmation_rule").and_then(crate::indexed_json::Value::as_str)!=Some("n_origin_local_structural_key_break") {
            return Err("local publication requires its private structural key-break producer".into());
        }
        let position=source.iter().position(|point|identity(point,candidate) && point.is_closed()
            && point.known_index<=candidate.known_index).ok_or("local candidate must match an actual confirmed source")?;
        let mut point=candidate.clone();
        if point.kind==PointKind::H {
            pressure=Some(candidate);
            if result.last().is_some_and(|last|last.kind==PointKind::H) {continue;}
            if let Some(previous)=result.last() {
                let proof=previous.evidence.get("trend_confirmation");
                if proof.and_then(|proof|proof.get("flip")).and_then(|flip|flip.get("index"))
                    .and_then(crate::indexed_json::Value::as_u64).is_some_and(|index|point.index<index as usize) {continue;}
                point.known_index=point.known_index.max(previous.known_index);
                if let Some(proof)=proof {point.evidence["incoming_trend_confirmation"]=proof.clone();}
                point.evidence["incoming_trend_state"]=json!(if proof.is_some(){"confirmed"}else{"unconfirmed"});
            }
            result.push(point);continue;
        }
        if result.last().is_some_and(|last|last.kind==PointKind::L) {continue;}
        let Some(proof)=qualify_source_direction(source,position,pressure,asof,Direction::Up) else{continue;};
        point.known_index=point.known_index.max(proof.known_index).max(result.last().map_or(0,|last|last.known_index));
        point.evidence["structural_confirmation_rule"]=point.evidence["confirmation_rule"].clone();
        point.evidence["confirmation_rule"]=json!(proof.rule);
        point.evidence["trend_confirmation_route"]=json!("same_level_key_break");
        point.evidence["trend_confirmation"]=json!(proof);
        if !result.is_empty() {point.evidence["incoming_trend_state"]=json!("unconfirmed");}
        result.push(point);
    }
    Ok(result)
}
