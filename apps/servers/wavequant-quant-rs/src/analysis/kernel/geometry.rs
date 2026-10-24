//! 统一内核已计算事实的兼容几何投影，不执行趋势识别或修线。
use super::{KernelFrame,KernelPoint,PointKind};
use crate::indexed_json::{Value,json};
use std::collections::{BTreeMap,BTreeSet};
fn reference(point:&KernelPoint)->Value {
    json!({"index":point.index,"ordinal":point.ordinal,"time":point.time,"kind":point.kind,"value":point.value,
        "available_at":point.known_index,"state":point.state,"trend_level":point.level,"source_path":format!("level{}-lecture-{}",point.level,point.path)})
}
fn clock_references(value:&Value)->Value {
    match (value).kind() {
        Some(crate::indexed_json::Kind::Array(rows))=>Value::Array(rows.iter().map(clock_references).collect()),
        Some(crate::indexed_json::Kind::Object(fields))=>{
            let mut result:crate::indexed_json::Map<String,Value>=fields.iter().map(|(key,value)|(key.clone(),clock_references(value))).collect();
            if !result.contains_key("available_at") {
                if let Some(known)=fields.get("known_index").or_else(||fields.get("available_index")) {result.insert("available_at".into(),known.clone());}
            }
            Value::Object(result)
        }
        _=>value.clone(),
    }
}
fn projected(point:&KernelPoint)->Value {
    let mut result=reference(point);let evidence=&point.evidence;
    for field in ["confirmation_rule","wave_direction_after","wave_direction_before","wave_identity_state","wave_completion_state",
        "upgrade_evidence","n_target_confirmation","trend_confirmation","incoming_trend_confirmation","incoming_trend_state",
        "active_trend_confirmation","structural_confirmation_rule","trend_confirmation_route","flip","broken_key"] {
        if let Some(value)=evidence.get(field) {result[field]=clock_references(value);}
    }
    if let Some(proof)=evidence.get("proof") {
        if result.get("broken_key").is_none() {
            if let Some(value)=proof.get("broken_key").or_else(||proof.get("reversal_key")) {result["broken_key"]=clock_references(value);}
        }
        if let Some(value)=proof.get("confirmed_by") {result["confirmed_by"]=clock_references(value);}
        if result.get("wave_direction_before").is_none() {
            if let Some(direction)=proof.get("direction_before"){result["wave_direction_before"]=direction.clone();}
        }
        let strict=evidence.get("confirmation_rule").and_then(Value::as_str)==Some("strict_known_trend_key_break");
        if strict {
            let direction=proof.get("direction").and_then(Value::as_str);
            result["upgrade_evidence"]=clock_references(proof);
            if let Some(direction)=direction {result["flip"]=json!(if direction=="up"{"翻空为多"}else{"翻多为空"});}
            if point.level>0 {
                let high=direction==Some("up");
                result["confirmation_rule"]=json!(format!("level{}_confirmed_{}_breaks_known_level{}_{}_origin",point.level-1,
                    if high{"high"}else{"low"},point.level,if high{"last_fall_high"}else{"last_rise_low"}));
            }
        }else if proof.get("direction_before").is_some() && proof.get("direction_after").is_some() {
            if let Some(direction)=proof.get("direction_after").and_then(Value::as_str) {result["flip"]=json!(if direction=="up"{"翻空为多"}else{"翻多为空"});}
        }
    }
    if let Some(source)=evidence.get("source") {
        result["source"]=clock_references(source);
        if let Some(path)=source.get("path").and_then(Value::as_u64){let source_level=source.get("level").and_then(Value::as_u64).unwrap_or(u64::from(point.level.saturating_sub(1)));result["source_path"]=json!(format!("level{source_level}-lecture-{path}"));}
    }
    result["label"]=json!(if point.kind==PointKind::H{"H"}else{"L"});result
}
/// `points` 保留完成状态；candidate 仍为 developing。所有时钟均是可知坐标。
pub fn geometry(frame:&KernelFrame)->Value {
    let mut groups:BTreeMap<usize,Vec<&KernelPoint>>=BTreeMap::new();
    for point in frame.points.iter().chain(frame.candidate.iter()) {groups.entry(point.path).or_default().push(point);}
    let strokes:Vec<_>=groups.into_iter().map(|(path,points)| {
        let source_paths:BTreeSet<String>=points.iter().filter_map(|point| {
            let level=frame.level.checked_sub(1)?;
            let path=point.evidence.get("source").and_then(|source|source.get("path")).and_then(Value::as_u64)
                .unwrap_or(point.path as u64);
            Some(format!("level{level}-lecture-{path}"))
        }).collect();
        let rows:Vec<_>=points.into_iter().map(projected).collect();
        json!({"id":format!("level{}-lecture-{path}",frame.level),"source_path":source_paths.iter().next_back(),"source_paths":source_paths,
            "trend_level":frame.level,"points":rows})
    }).collect();
    json!({"trend_level":frame.level,"source_level":frame.level.checked_sub(1),"strokes":strokes,
        "events":frame.events,"state":frame.state,"direction":frame.direction,"blocked_at":frame.blocked_at,"issues":frame.issues})
}

/// 仅序列化已由统一内核产生的许可链；不运行资格识别或更改结构几何。
pub fn permission_geometry(frame:&KernelFrame)->Value {
    let mut groups:BTreeMap<usize,Vec<&KernelPoint>>=BTreeMap::new();
    for point in &frame.publication{groups.entry(point.path).or_default().push(point);}
    let strokes:Vec<_>=groups.into_iter().map(|(path,points)|{
        let source_paths:BTreeSet<_>=points.iter().filter_map(|point|{
            let level=frame.level.checked_sub(1)?;let source=point.evidence.get("source").and_then(|p|p.get("path")).and_then(Value::as_u64).unwrap_or(point.path as u64);
            Some(format!("level{level}-lecture-{source}"))
        }).collect();
        json!({"id":format!("level{}-lecture-{path}",frame.level),"source_path":source_paths.iter().next_back(),"source_paths":source_paths,
            "trend_level":frame.level,"points":points.into_iter().map(projected).collect::<Vec<_>>()})
    }).collect();
    json!({"trend_level":frame.level,"source_level":frame.level.checked_sub(1),"strokes":strokes,"basis":"native_direction_publication"})
}
