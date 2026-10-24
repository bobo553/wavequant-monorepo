//! 数据完整性和调用者明确指定的本地独立快照；不发出网络请求。
use super::{AnalyticsResult,date,number};
use crate::{analysis::trading::RuleCandle,data::{exchange,tdx::fingerprint}};
use rust_decimal::Decimal;
use crate::indexed_json::{Value,json};
use std::{collections::{BTreeMap,BTreeSet},path::Path,fs};
pub fn load_rules(path:&Path)->AnalyticsResult<(String,BTreeMap<String,Vec<RuleCandle>>)>{
    let imported=exchange::load_bars(path).map_err(|e|e.to_string())?;let mut grouped=BTreeMap::new();
    let value=|v:f64|v.to_string().parse::<Decimal>().map_err(|_|"invalid imported decimal".to_string());
    for(symbol,bars)in imported.bars{let rows=bars.into_iter().map(|b|Ok(RuleCandle{symbol:b.symbol,timestamp:b.timestamp,
        open:value(b.open)?,high:value(b.high)?,low:value(b.low)?,close:value(b.close)?,volume:value(b.volume)?,
        adjustment_factor:value(b.adjustment_factor)?,raw_trading_active:b.raw_trading_active,raw_is_st:b.raw_is_st})).collect::<AnalyticsResult<Vec<_>>>()?;grouped.insert(symbol,rows);}
    Ok((imported.source_version,grouped))
}
pub fn audit_bars(grouped:&BTreeMap<String,Vec<RuleCandle>>)->AnalyticsResult<Value>{
    for bars in grouped.values(){crate::analysis::trading::validate_bars(bars)?;}
    let calendar:BTreeSet<_>=grouped.values().flatten().map(|b|b.timestamp.as_str()).collect();
    let missing:BTreeMap<_,_>=grouped.iter().map(|(symbol,bars)|{let days:BTreeSet<_>=bars.iter().map(|b|b.timestamp.as_str()).collect();(symbol.clone(),calendar.difference(&days).count())}).collect();
    let latest:BTreeMap<_,_>=grouped.iter().filter_map(|(symbol,bars)|bars.last().map(|b|(symbol.clone(),date(&b.timestamp).to_string()))).collect();
    Ok(json!({"symbols":grouped.len(),"rows":grouped.values().map(Vec::len).sum::<usize>(),"union_sessions":calendar.len(),
        "zero_volume_rows":grouped.values().flatten().filter(|b|b.volume==Decimal::ZERO).count(),"missing_sessions":missing,"latest_by_symbol":latest,
        "note":"Missing sessions may be suspensions, listing gaps or missing downloads; not automatically filled."}))
}
pub fn audit_data(path:&Path)->AnalyticsResult<Value>{
    let(_,grouped)=load_rules(path)?;let mut summary=audit_bars(&grouped)?;let mut checks=vec![];
    let root=path.parent().ok_or("dataset parent unavailable")?.canonicalize().map_err(|e|e.to_string())?;
    for(symbol,bars)in grouped{
        if !symbol.bytes().all(|b|b.is_ascii_alphanumeric()||b==b'.'||b==b'_'||b==b'-'){continue;}
        let snapshot=root.join("cache").join(format!("{symbol}_2018-01-01_2025-12-31.json"));if !snapshot.is_file(){continue;}
        let snapshot=snapshot.canonicalize().map_err(|e|e.to_string())?;if !snapshot.starts_with(&root){return Err("raw snapshot escapes dataset directory".into());}
        if fs::metadata(&snapshot).map_err(|e|e.to_string())?.len()>64*1024*1024{return Err("raw snapshot exceeds bound".into());}
        let cached:Value=crate::indexed_json::from_slice(&fs::read(&snapshot).map_err(|e|e.to_string())?).map_err(|e|e.to_string())?;
        let raw:BTreeMap<_,_>=cached["raw"].as_array().ok_or("raw snapshot rows missing")?.iter().filter(|r|r["tradestatus"]=="1")
            .filter_map(|r|r["date"].as_str().map(|day|(day,r))).collect();
        let mut matched=0;let mut mismatches=vec![];
        for bar in bars{let Some(row)=raw.get(date(&bar.timestamp))else{continue;};matched+=1;
            for(field,value)in[("open",bar.open),("high",bar.high),("low",bar.low),("close",bar.close)]{
                let actual=super::float(value/bar.adjustment_factor)?;let expected=number(&row[field]).ok_or("raw snapshot OHLC invalid")?;
                if (actual-expected).abs()>0.011{mismatches.push(json!({"date":row["date"],"field":field,"tdx":actual,"cached":expected}));}}}
        checks.push(json!({"symbol":symbol,"snapshot":snapshot.to_string_lossy(),"sha256":fingerprint(&snapshot).map_err(|e|e.to_string())?,
            "compared_bars":matched,"mismatch_fields":mismatches.len(),"examples":mismatches.into_iter().take(10).collect::<Vec<_>>()}));
    }
    summary["independent_cached_raw_price_check"]=json!({"status":if checks.is_empty(){"unavailable"}else{"checked"},"source":"existing BaoStock raw snapshots",
        "tolerance_yuan":0.011,"compared_bars":checks.iter().map(|c|c["compared_bars"].as_u64().unwrap_or(0)).sum::<u64>(),
        "mismatch_fields":checks.iter().map(|c|c["mismatch_fields"].as_u64().unwrap_or(0)).sum::<u64>(),"checks":checks,
        "caveat":"Checks only overlapping cached OHLC, not corporate-action completeness or 2026 data."});Ok(summary)
}
