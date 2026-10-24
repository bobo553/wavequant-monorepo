//! 数据集注册前的事实清单；不把 CSV 日线声明为全市场或分钟完整性证据。
use std::{collections::BTreeSet,fs::File,io::Read,path::Path};
use chrono::{DateTime,FixedOffset,TimeZone,Utc};
use crate::indexed_json::{Value,json};
use sha2::{Digest,Sha256};
use super::{exchange::{load_bars,parse_timestamp},security_master::{SecurityMaster,utc},tdx::{DataError,parse_date}};
fn invalid(s:&str)->DataError {DataError::Invalid(s.into())}
fn digest(bytes:&[u8])->String {format!("{:x}",Sha256::digest(bytes))}
fn canonical(value:&Value)->Value {
    match (value).kind() {
        Some(crate::indexed_json::Kind::Object(map))=>Value::Object(map.iter().map(|(key,value)|(key.clone(),canonical(value))).collect::<std::collections::BTreeMap<_,_>>().into_iter().collect()),
        Some(crate::indexed_json::Kind::Array(array))=>Value::Array(array.iter().map(canonical).collect()),_=>value.clone(),
    }
}
fn value_digest(value:&Value)->Result<String,DataError> {crate::indexed_json::to_vec(&canonical(value)).map(|bytes|digest(&bytes)).map_err(|_|invalid("manifest serialization failed"))}
pub fn market_close(value:&str)->Result<DateTime<Utc>,DataError> {
    let parsed=parse_timestamp(value)?;
    if parsed.aware {return utc(&parsed.canonical);}
    let day=parse_date(parsed.canonical.get(..10).ok_or_else(||invalid("bar date invalid"))?)?;
    let local=day.and_hms_opt(15,0,0).ok_or_else(||invalid("bar close time invalid"))?;
    FixedOffset::east_opt(8*3600).and_then(|zone|zone.from_local_datetime(&local).single()).map(|stamp|stamp.with_timezone(&Utc)).ok_or_else(||invalid("market close time invalid"))
}
pub fn build_manifest(path:impl AsRef<Path>,known_at:&str,master:&SecurityMaster,calendar:Option<&[String]>)->Result<Value,DataError> {
    let path=path.as_ref().canonicalize()?;
    let imported=load_bars(&path)?;
    let metadata_path=path.with_extension("metadata.json");
    let file=File::open(&metadata_path)?;
    if file.metadata()?.len()>16*1024*1024 {return Err(invalid("dataset metadata exceeds limit"));}
    let mut metadata_bytes=Vec::new();file.take(16*1024*1024+1).read_to_end(&mut metadata_bytes)?;
    if metadata_bytes.len()>16*1024*1024 {return Err(invalid("dataset metadata exceeds limit"));}
    let metadata:Value=crate::indexed_json::from_slice(&metadata_bytes).map_err(|_|invalid("dataset metadata JSON invalid"))?;
    if metadata["sha256"].as_str()!=Some(imported.source_version.as_str()) {return Err(invalid("data/provenance hash mismatch"));}
    let cutoff=utc(known_at)?;
    let sessions:BTreeSet<String>=imported.bars.values().flatten().map(|bar|bar.timestamp[..10].to_owned()).collect();
    let first=sessions.first().ok_or_else(||invalid("dataset sessions missing"))?;
    let last=sessions.last().ok_or_else(||invalid("dataset sessions missing"))?;
    let calendar=calendar.map(|days|days.iter().cloned().collect::<BTreeSet<_>>());
    if let Some(days)=&calendar {
        for day in days {parse_date(day)?;}
        if !sessions.is_subset(days) {return Err(invalid("bars outside supplied exchange calendar"));}
    }
    let mut unknown=0;let mut outside=0;let mut future=0;let mut missing=Vec::new();
    for (symbol,bars) in &imported.bars {
        let available:BTreeSet<_>=bars.iter().map(|bar|bar.timestamp[..10].to_owned()).collect();
        for bar in bars {
            let close=market_close(&bar.timestamp)?;
            if close>cutoff {future+=1;}
            match master.at(symbol,&bar.timestamp[..10],&close.to_rfc3339())? {None=>unknown+=1,Some(fact) if !fact.active=>outside+=1,_=>{}}
        }
        if let Some(days)=&calendar {
            for day in days {
                if day<first || day>last {continue;}
                let close=market_close(day)?;
                if master.at(symbol,day,&close.to_rfc3339())?.is_some_and(|fact|fact.active && !fact.suspended) && !available.contains(day) {missing.push(json!({"symbol":symbol,"session":day}));}
            }
        }
    }
    let checks=json!({"provenance_hash":true,"point_in_time_security_coverage":unknown==0,"exchange_calendar_available":calendar.is_some(),"expected_sessions_complete":missing.is_empty(),"listed_period_only":outside==0,"no_future_rows":future==0,"minor_timeframe_available":false});
    let master_value=crate::indexed_json::to_value(master.snapshot()).map_err(|_|invalid("security master serialization failed"))?;
    let calendar_digest=calendar.as_ref().map(|days|value_digest(&json!(days))).transpose()?;
    let mut manifest=json!({"schema_version":1,"path":path.to_string_lossy(),"sha256":imported.source_version,"metadata_sha256":digest(&metadata_bytes),"rows":imported.bars.values().map(Vec::len).sum::<usize>(),"symbols":imported.bars.keys().collect::<Vec<_>>(),"start":first,"end":last,"security_master_sha256":value_digest(&master_value)?,"calendar_sha256":calendar_digest,"checks":checks,"unknown_security_rows":unknown,"outside_listing_rows":outside,"future_rows":future,"missing_expected_sessions":missing,"data_ready":unknown==0 && calendar.is_some() && missing.is_empty() && outside==0 && future==0,"strict_polyline_ready":false,"universe_completeness":"not_established_from_bar_files","coverage_scope":"provided_symbols_only_not_whole_market","limitations":metadata.get("limitations").cloned().unwrap_or_else(||json!([]))});
    let id=value_digest(&manifest)?;
    manifest["manifest_id"]=json!(id);
    Ok(manifest)
}
