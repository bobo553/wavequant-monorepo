//! 历史讲义基础关系的描述性标注；不是盘态、N 或交易资格判断。
use super::{AnalyticsResult,quality,research};
use crate::analysis::trading::RuleCandle;
use rust_decimal::Decimal;
use crate::indexed_json::{Value,json};
use sha2::{Sha256,Digest};
use std::{collections::{BTreeMap,BTreeSet},path::Path,fs};
pub fn bar_relations(previous:&RuleCandle,current:&RuleCandle)->AnalyticsResult<Value>{
    crate::analysis::trading::validate_bars(&[previous.clone(),current.clone()])?;
    let lower_high=current.high<previous.high;let higher_low=current.low>previous.low;let higher_high=current.high>previous.high;let lower_low=current.low<previous.low;
    Ok(json!({"virtual_low":current.low.min(previous.close),"virtual_high":current.high.max(previous.close),"shrinking_head":lower_high,"lifting_foot":higher_low,
        "extending_head":higher_high,"falling_tail":lower_low,"sunrise":higher_high&&higher_low&&current.close>previous.high,
        "sunset":lower_high&&lower_low&&current.close<previous.low,"inside":lower_high&&higher_low,"outside":higher_high&&lower_low,
        "equal_high":current.high==previous.high,"equal_low":current.low==previous.low,"requires_lower_timeframe":lower_high&&higher_low||higher_high&&lower_low}))
}
fn legacy_resistance(previous:&RuleCandle,current:&RuleCandle,up:bool)->bool{
    let body=if up{current.close<current.open}else{current.close>current.open};let gap=if up{current.open<previous.close}else{current.open>previous.close};
    let shadow=if up{current.high-current.open.max(current.close)}else{current.open.min(current.close)-current.low};
    body||gap||current.high>current.low&&shadow*Decimal::TWO>=current.high-current.low
}
pub fn audit_foundations(input:&Path,output:&Path,test_status:Option<&Value>)->AnalyticsResult<Value>{
    let(digest,grouped)=quality::load_rules(input)?;let metadata=input.with_extension("metadata.json");
    if metadata.exists(){let value:Value=crate::indexed_json::from_slice(&fs::read(&metadata).map_err(|e|e.to_string())?).map_err(|e|e.to_string())?;
        if value["sha256"].as_str()!=Some(&digest){return Err("dataset hash mismatch".into());}}
    for bars in grouped.values(){let dates:BTreeSet<_>=bars.iter().map(|b|super::date(&b.timestamp)).collect();if dates.len()!=bars.len(){return Err("foundation audit requires daily bars".into());}}
    fs::create_dir_all(output).map_err(|e|e.to_string())?;
    for entry in fs::read_dir(output).map_err(|e|e.to_string())?{if entry.map_err(|e|e.to_string())?.file_name()!="test_log.txt"{return Err("nonempty foundation output".into());}}
    let mut totals=BTreeMap::<String,usize>::new();let mut coverage=BTreeMap::new();
    for(number,(symbol,bars))in grouped.iter().enumerate(){let mut rows=vec![];let mut counts=BTreeMap::<String,usize>::new();
        for pair in bars.windows(2){let mut row=bar_relations(&pair[0],&pair[1])?;
            for(key,value)in row.as_object().ok_or("relation object missing")?{if value.as_bool()==Some(true){*counts.entry(key.clone()).or_default()+=1;}}
            row["timestamp"]=json!(pair[1].timestamp);row["symbol"]=json!(symbol);row["bearish_resistance_shape"]=json!(legacy_resistance(&pair[0],&pair[1],true));
            row["bullish_resistance_shape"]=json!(legacy_resistance(&pair[0],&pair[1],false));rows.push(row);}
        let filename=format!("symbol_{:03}.csv",number+1);let columns=rows.first().and_then(Value::as_object).map(|r|r.keys().cloned().collect::<Vec<_>>()).unwrap_or_else(||vec!["timestamp".into(),"symbol".into()]);
        research::write_rows(&output.join(&filename),&rows,&columns)?;
        coverage.insert(symbol,json!({"rows":bars.len(),"annotated_rows":rows.len(),"first":bars.first().map(|b|&b.timestamp),"last":bars.last().map(|b|&b.timestamp),"counts":counts,"file":filename}));
        for(key,count)in counts{*totals.entry(key).or_default()+=count;}
    }
    let report=json!({"kind":"descriptive_foundation_audit","production_ready":false,"rows":grouped.values().map(Vec::len).sum::<usize>(),
        "annotated_rows":grouped.values().map(|b|b.len().saturating_sub(1)).sum::<usize>(),"data_sha256":digest,
        "source_csv":input.canonicalize().map_err(|e|e.to_string())?.to_string_lossy(),"symbols":coverage,"counts":totals,
        "source_sha256":{"foundation_audit.rs":format!("{:x}",Sha256::digest(include_bytes!("foundation_audit.rs")))},
        "engineering_tests":test_status.cloned().unwrap_or_else(||json!({"status":"not_run_by_this_command"})),
        "limitations":["No N formation inferred from adjacent candles alone.","Resistance shape counts alone do not establish a six-state regime.",
            "Inside/outside candles flag lower-timeframe ambiguity, not an invented intraday path.","This report contains no returns, orders or evidence of main-operator intent."]});
    research::report_files(output,&report)?;Ok(report)
}
