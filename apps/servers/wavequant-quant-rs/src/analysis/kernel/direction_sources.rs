//! 原整波发布元数据的只读投影；下游不得由高点标签另造趋势。
use super::{KernelFrame, KernelPoint, PointKind};
use crate::indexed_json::Value;
use std::collections::BTreeMap;

fn integer(value:&Value,key:&str)->Option<usize> {
    value.get(key)?.as_u64().and_then(|number|usize::try_from(number).ok())
}
fn same(point:&KernelPoint,reference:&Value)->bool {
    integer(reference,"index")==Some(point.index)
        && integer(reference,"ordinal").unwrap_or(0)==point.ordinal
        && reference.get("kind").and_then(Value::as_str)==Some(if point.kind==PointKind::H {"H"}else{"L"})
        && reference.get("value").and_then(Value::as_f64)==Some(point.value)
}

/// integrated_strategy._completed_first_trend_down_starts 的真实一级下降起点。
/// 普通 H 发布读取方向；升级 L 只接受 endpoint 证书指向的同级已知 H。
pub fn confirmed_decline_starts(frame:&KernelFrame,asof:usize)->Vec<KernelPoint> {
    let mut starts:BTreeMap<(usize,usize,usize),KernelPoint>=BTreeMap::new();
    for point in &frame.points {
        if !point.is_closed() || point.known_index>asof {continue;}
        let direction=point.evidence.get("wave_direction_after").or_else(||point.evidence.get("direction_after"));
        if point.kind==PointKind::H && direction.and_then(Value::as_str)==Some("down") {
            starts.insert((point.path,point.index,point.ordinal),point.clone());
        }
        if point.kind!=PointKind::L {continue;}
        let proof=point.evidence.get("upgrade_evidence").or_else(||point.evidence.get("proof"));
        let Some(proof)=proof else{continue;};
        if proof.get("role").and_then(Value::as_str)!=Some("endpoint")
            || proof.get("direction").and_then(Value::as_str)!=Some("down") {continue;}
        let known=integer(proof,"available_index").or_else(||integer(proof,"available_at"));
        let Some(known)=known.filter(|index|*index<=asof) else{continue;};
        let Some(origin)=proof.get("origin") else{continue;};
        let high=frame.points.iter().find(|candidate|candidate.path==point.path && candidate.kind==PointKind::H
            && candidate.is_closed() && candidate.order()<point.order() && candidate.known_index<=known && same(candidate,origin));
        if let Some(high)=high {starts.entry((high.path,high.index,high.ordinal)).or_insert_with(||high.clone());}
    }
    for event in &frame.events {
        if event.known_index>asof || event.point.kind!=PointKind::H {continue;}
        let direction=event.evidence.get("direction_after").or_else(||event.evidence.get("direction"));
        if direction.and_then(Value::as_str)!=Some("down") {continue;}
        if let Some(point)=frame.points.iter().find(|point|point.is_closed() && point.path==event.point.path
            && point.order()==event.point.order() && point.kind==PointKind::H && point.value==event.point.value
            && point.known_index<=event.known_index) {
            starts.entry((point.path,point.index,point.ordinal)).or_insert_with(||point.clone());
        }
    }
    starts.into_values().collect()
}
