//! trend_lifecycle.advance 的来源修订和结束路径失效。
//! 仅比较相邻可见前缀的真实证据，不把价格止损或资格关闭反推成结构失效。
use super::{KernelEvent, KernelFrame, KernelPoint};
use crate::indexed_json::{Value,json};
use std::collections::{BTreeMap,BTreeSet};

fn index(value:&Value,key:&str)->Option<usize> {
    value.get(key)?.as_u64().and_then(|number|usize::try_from(number).ok())
}
fn scalar_same(left:&KernelPoint,right:&KernelPoint)->bool {
    left.level==right.level && left.path==right.path && left.order()==right.order()
        && left.kind==right.kind && left.value==right.value && left.time==right.time
        && left.known_index==right.known_index && right.is_closed()
}
fn deepest_path(point:&KernelPoint)->usize {
    let mut path=point.path;let mut value=&point.evidence;
    // 正常结构至多四级；有界读取避免外部任意 JSON 的无限嵌套。
    for _ in 0..8 {
        let Some(source)=value.get("source") else{break;};
        if let Some(found)=index(source,"path") {path=found;}
        let Some(next)=source.get("evidence") else{break;};value=next;
    }
    path
}
fn active_paths(frame:&KernelFrame,polyline:Option<&KernelFrame>)->BTreeSet<usize> {
    if let Some(polyline)=polyline {
        let Some(active)=polyline.candidate.as_ref().map(|point|point.path) else{return BTreeSet::new();};
        frame.points.iter().chain(frame.candidate.iter()).filter(|point|deepest_path(point)==active)
            .map(|point|point.path).collect()
    }else{
        frame.candidate.as_ref().map(|point|point.path).into_iter().collect()
    }
}
fn reference<'a>(frame:&'a KernelFrame,proof:&Value,prefix:&str)->Option<&'a KernelPoint> {
    let position=index(proof,&format!("{prefix}_index"))?;
    let ordinal=index(proof,&format!("{prefix}_ordinal")).unwrap_or(0);
    let price=proof.get(&format!("{prefix}_price")).and_then(Value::as_f64)
        .or_else(||(prefix=="flip").then(||proof.get("breakout_level").and_then(Value::as_f64)).flatten());
    frame.points.iter().find(|point|point.is_closed() && point.index==position && point.ordinal==ordinal
        && price.is_none_or(|value|point.value==value))
}
fn retained(point:&KernelPoint,frame:&KernelFrame)->bool {frame.points.iter().any(|current|scalar_same(point,current))}

/// 调用顺序：当日结构及正式证书完成后、资格账本消费前。
/// polyline 参数用于原始路径截止与下级确认来源；历史绘图点不会保活已结束路径。
pub fn reconcile_lifecycle_sources(previous:&KernelFrame,current:&mut KernelFrame,
    previous_polyline:Option<&KernelFrame>,current_polyline:Option<&KernelFrame>,asof:usize)->Result<(),String> {
    if previous.level!=current.level {return Err("lifecycle source revisions must compare the same level".into());}
    let present=active_paths(current,current_polyline);
    let mut latest:BTreeMap<String,(&KernelEvent,bool)>=BTreeMap::new();
    for event in &previous.events {
        let Some(proof)=event.evidence.get("lifecycle_event")else{continue;};
        let Some(path)=proof.get("source_path").and_then(Value::as_str)else{continue;};
        if event.known_index>=asof {continue;}
        let name=proof.get("event").and_then(Value::as_str).unwrap_or("");
        if matches!(name,"trend_lifecycle_state"|"trend_lifecycle_anchor"|"trend_lifecycle_invalidated") {
            latest.insert(path.into(),(event,name!="trend_lifecycle_invalidated"));
        }
    }
    let mut invalidations=Vec::new();
    let mut retained_endings=Vec::new();
    for (path,(prior,active)) in latest {
        let proof=&prior.evidence["lifecycle_event"];
        let own_path=prior.origin.as_ref().map_or(prior.point.path,|point|point.path);
        if proof.get("reason").and_then(Value::as_str)==Some("source_path_ended") && !present.contains(&own_path) {
            if !current.events.iter().any(|event|event.evidence==prior.evidence) {retained_endings.push(prior.clone());}
            continue;
        }
        let mut reason=None;
        if !present.contains(&own_path) {reason=Some("source_path_ended");}
        else if active {
            let origin=reference(previous,proof,"origin").or(prior.origin.as_ref());
            let key=prior.key.as_ref();
            let impulse=reference(previous,proof,"flip");
            let missing_origin=origin.is_some_and(|point|!retained(point,current));
            let missing_key=key.is_some_and(|point|!retained(point,current));
            let mut missing_impulse=impulse.is_some_and(|point|!retained(point,current));
            if missing_impulse && !missing_origin && !missing_key
                && proof.get("special_bull_recovery").and_then(Value::as_bool)==Some(true) {
                if let (Some(origin),Some(key),Some(old_high))=(origin,key,impulse) {
                    let continued=current.points.windows(3).any(|triple|scalar_same(key,&triple[0])
                        && scalar_same(origin,&triple[1]) && triple[2].kind==old_high.kind
                        && triple[2].order()>=old_high.order() && triple[2].value>=old_high.value
                        && triple[2].known_index>=old_high.known_index);
                    if continued {missing_impulse=false;}
                }
            }
            let source_level=index(proof,"counter_source_level").unwrap_or(usize::from(previous.level));
            let counter_missing=if source_level==0 && previous.level>0 {
                previous_polyline.zip(current_polyline).and_then(|(before,after)|
                    reference(before,proof,"counter").map(|point|!retained(point,after))).unwrap_or(false)
            }else{reference(previous,proof,"counter").is_some_and(|point|!retained(point,current))};
            if missing_origin || missing_key || missing_impulse || counter_missing {reason=Some("source_evidence_revised");}
        }
        let Some(reason)=reason else{continue;};
        if current.events.iter().any(|event|event.known_index==asof
            && event.evidence.get("lifecycle_event").is_some_and(|record|record.get("reason").and_then(Value::as_str)==Some(reason)
                && record.get("source_path").and_then(Value::as_str)==Some(path.as_str()))) {continue;}
        let mut value=proof.clone();value["event"]=json!("trend_lifecycle_invalidated");
        value["bar_index"]=json!(asof);value["available_index"]=json!(asof);value["reason"]=json!(reason);
        value["source_revision_rule"]=json!("same_scalar_pivot_and_first_known_clock");
        let mut event=prior.clone();event.title="trend_lifecycle_invalidated".into();event.known_index=asof;
        event.evidence=json!({"lifecycle_event":value});invalidations.push(event);
    }
    if !invalidations.is_empty() || !retained_endings.is_empty() {
        let (mut historical,current_day):(Vec<_>,Vec<_>)=std::mem::take(&mut current.events).into_iter()
            .partition(|event|event.known_index<asof);
        historical.extend(retained_endings);historical.sort_by_key(|event|event.known_index);
        historical.extend(invalidations);historical.extend(current_day);current.events=historical;
    }
    Ok(())
}
