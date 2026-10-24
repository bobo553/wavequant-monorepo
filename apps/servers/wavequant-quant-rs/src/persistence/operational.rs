use super::{EventStore,StoreError,StoreResult,canonical,sha256,require};
use chrono::{DateTime,Utc};
use serde::{Serialize,Deserialize};
use crate::indexed_json::{Value,json};
use std::{collections::{BTreeMap,BTreeSet},path::Path};

pub fn identifier(value:&str)->StoreResult<&str>{
    require(!value.is_empty()&&value.len()<=80&&value.as_bytes()[0].is_ascii_alphanumeric()
        &&value.bytes().all(|c|c.is_ascii_alphanumeric()||c==b'_'||c==b'-'),"identity must be 1-80 ASCII letters, digits, hyphen or underscore")?;
    let upper=value.to_ascii_uppercase();let device=["CON","PRN","AUX","NUL"].contains(&upper.as_str())
        ||(upper.len()==4&&(upper.starts_with("COM")||upper.starts_with("LPT"))&&matches!(upper.as_bytes()[3],b'1'..=b'9'));
    require(!device,"reserved Windows device identity")?;Ok(value)
}
/// 使用操作系统持有的文件锁；进程崩溃释放，锁文件从不删除。
pub struct WorkspaceLock{_file:std::fs::File}
impl WorkspaceLock{pub fn acquire(root:&Path)->StoreResult<Self>{
    std::fs::create_dir_all(root)?;let file=std::fs::OpenOptions::new().create(true).truncate(false).read(true).write(true).open(root.join(".operations.lock"))?;
    file.try_lock().map_err(|_|StoreError("another operations command owns this workspace".into()))?;Ok(Self{_file:file})
}}
fn key(payload:&Value,name:&str)->StoreResult<String>{payload.get(name).and_then(Value::as_str).map(str::to_owned).ok_or_else(||StoreError(format!("missing event field: {name}")))}
fn merge(target:&mut Value,source:&Value)->StoreResult<()>{let object=target.as_object_mut().ok_or_else(||StoreError("invalid event state".into()))?;
    object.extend(source.as_object().ok_or_else(||StoreError("object event payload required".into()))?.clone());Ok(())}
pub fn run_states(store:&EventStore)->StoreResult<BTreeMap<String,Value>>{
    let mut runs=BTreeMap::<String,Value>::new();for e in store.events()?{match e.kind.as_str(){
        "RUN_STARTED"=>{let id=key(&e.payload,"run_id")?;require(!runs.contains_key(&id),"duplicate run start")?;let mut value=e.payload;
            value["status"]=json!("RUNNING");value["started_at"]=json!(e.event_time);value["stages"]=json!([]);runs.insert(id,value);}
        "RUN_STAGE"=>{let id=key(&e.payload,"run_id")?;let state=runs.get_mut(&id).ok_or_else(||StoreError("stage without run".into()))?;
            let mut stage=e.payload;stage["at"]=json!(e.event_time);state["stages"].as_array_mut().ok_or_else(||StoreError("invalid run stages".into()))?.push(stage);}
        "RUN_FINISHED"=>{let id=key(&e.payload,"run_id")?;let state=runs.get_mut(&id).ok_or_else(||StoreError("finish without run".into()))?;merge(state,&e.payload)?;state["finished_at"]=json!(e.event_time);}
        _=>{}
    }}Ok(runs)
}
pub fn alert_states(store:&EventStore)->StoreResult<BTreeMap<String,Value>>{
    let mut alerts=BTreeMap::<String,Value>::new();for e in store.events()?{match e.kind.as_str(){
        "ALERT_OPENED"=>{let id=key(&e.payload,"alert_id")?;let mut value=e.payload;value["opened_at"]=json!(e.event_time);value["active"]=json!(true);value["acknowledged"]=json!(false);alerts.insert(id,value);}
        "ALERT_ACKNOWLEDGED"=>{let id=key(&e.payload,"alert_id")?;let state=alerts.get_mut(&id).ok_or_else(||StoreError("acknowledgement without alert".into()))?;state["acknowledged"]=json!(true);state["acknowledgement"]=e.payload["reason"].clone();}
        "ALERT_RESOLVED"=>{let id=key(&e.payload,"alert_id")?;let state=alerts.get_mut(&id).ok_or_else(||StoreError("resolution without alert".into()))?;state["active"]=json!(false);state["resolved_at"]=json!(e.event_time);}
        _=>{}
    }}Ok(alerts)
}
#[derive(Debug,Clone,Serialize,Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AlertObservation{pub code:String,pub severity:String,pub message:String}
pub fn synchronize_alerts(store:&mut EventStore,scope:&str,observations:&[AlertObservation],when:DateTime<Utc>)->StoreResult<Vec<Value>>{
    let mut codes=BTreeSet::new();for o in observations{require(!o.code.is_empty()&&!o.message.is_empty()&&["warning","critical"].contains(&o.severity.as_str())&&codes.insert(&o.code),"one identified warning or critical observation per code required")?;}
    store.transaction(|store|{
        let states=alert_states(store)?;let mut wanted=BTreeMap::new();for o in observations{let v=crate::indexed_json::to_value(o)?;wanted.insert(sha256(canonical(&v)?),v);}
        let active:BTreeMap<_,_>=states.values().filter(|a|a["scope"].as_str()==Some(scope)&&a["active"]==true)
            .map(|a|Ok((key(a,"condition_hash")?,a))).collect::<StoreResult<_>>()?;
        for(digest,a)in &active{if !wanted.contains_key(digest){let id=key(a,"alert_id")?;store.append(&format!("resolve:{id}"),"ALERT_RESOLVED",when,json!({"alert_id":id}))?;}}
        for(digest,mut observation)in wanted{if !active.contains_key(&digest){let id=sha256(canonical(&json!([scope,digest,store.events()?.len()]))?);
            observation["alert_id"]=json!(id);observation["condition_hash"]=json!(digest);observation["scope"]=json!(scope);
            store.append(&format!("alert:{id}"),"ALERT_OPENED",when,observation)?;}}
        Ok(alert_states(store)?.into_values().filter(|a|a["active"]==true).collect())
    })
}
pub fn acknowledge_alert(store:&mut EventStore,alert_id:&str,reason:&str,when:DateTime<Utc>)->StoreResult<Value>{
    require(!reason.trim().is_empty(),"operator acknowledgement reason required")?;
    store.transaction(|store|{
        let alert=alert_states(store)?.remove(alert_id).ok_or_else(||StoreError("unknown alert".into()))?;
        if alert["acknowledged"]==true{require(alert["acknowledgement"].as_str()==Some(reason),"acknowledgement already recorded with a different reason")?;return Ok(alert);}
        store.append(&format!("ack-alert:{alert_id}"),"ALERT_ACKNOWLEDGED",when,json!({"alert_id":alert_id,"reason":reason}))?;
        alert_states(store)?.remove(alert_id).ok_or_else(||StoreError("acknowledged alert unavailable".into()))
    })
}
