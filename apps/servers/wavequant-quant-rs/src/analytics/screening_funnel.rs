use super::{AnalyticsResult,date};
use crate::indexed_json::{Value,json};
use std::collections::BTreeMap;
pub fn funnel(view:&Value,lookback:usize)->AnalyticsResult<Value>{
    if lookback==0{return Err("positive screening lookback required".into());}
    let bars=view["bars"].as_array().ok_or("view bars missing")?;
    let cutoff=if bars.is_empty(){"9999"}else{bars[bars.len()-lookback.min(bars.len())]["time"].as_str().ok_or("bar date missing")?};
    let mut counts=BTreeMap::<String,usize>::new();let mut rejections=BTreeMap::<String,usize>::new();let mut squeeze=false;
    for event in view["audit"].as_array().into_iter().flatten(){let stamp=event["timestamp"].as_str().ok_or("audit timestamp missing")?;
        if date(stamp)<cutoff{continue;}let kind=event["event"].as_str().ok_or("audit event missing")?;*counts.entry(kind.into()).or_default()+=1;
        if matches!(kind,"entry_rejected"|"entry_preflight_rejected"){*rejections.entry(event["reason"].as_str().unwrap_or("unknown").into()).or_default()+=1;}
        squeeze|=kind=="regime_confirmation"&&matches!(event["regime"].as_str(),Some("轧空"|"强轧空"));}
    let has_long=view["signals"].as_array().into_iter().flatten().any(|s|s["side"]=="LONG"&&s["time"].as_str().or_else(||s["timestamp"].as_str().map(date)).is_some_and(|d|d>=cutoff));
    Ok(json!({"structure_interrupted":usize::from(counts.get("strict_structure_interrupted").copied().unwrap_or(0)>0),
        "has_n":usize::from(counts.get("n_completed").copied().unwrap_or(0)>0),"has_squeeze":usize::from(squeeze),
        "no_long_signal":usize::from(!has_long),"events":counts,"rejections":rejections}))
}
