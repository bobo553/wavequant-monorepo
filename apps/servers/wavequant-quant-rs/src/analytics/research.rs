//! 实际结果封存：导出采用 create_new，不能覆盖已有实验。
use super::{AnalyticsResult,float,date,metrics,trade_evidence,execution_diagnostics};
use crate::{execution::StrategyBacktest,analysis::trading::RuleCandle};
use crate::indexed_json::{Value,json};
use std::{collections::{BTreeMap,BTreeSet},fs::{self,OpenOptions},io::Write,path::Path};

pub fn build_result(result:&StrategyBacktest,bars:&BTreeMap<String,Vec<RuleCandle>>,signals:&[Value],audit:&[Value],holding_drawdowns:Option<&[Value]>)->AnalyticsResult<Value>{
    let (orders,positions,counts)=trade_evidence::ledger(result,bars,signals,audit)?;
    let mut metrics=metrics::result_metrics(result)?;
    if let Some(episodes)=holding_drawdowns{super::merge(&mut metrics,&super::holding_drawdown::drawdown_metrics(episodes));}else{metrics["holding_drawdown_status"]=json!("not_computed");}
    let curve=metrics::account_curve(result)?;
    let mut trades=crate::indexed_json::to_value(&result.trades).map_err(|e|e.to_string())?;
    for trade in trades.as_array_mut().into_iter().flatten(){for field in ["entry_price","exit_price","initial_stop","quantity","gross_return","net_return","pnl","fees"]{
        if let Some(value)=trade.get(field).and_then(super::number){trade[field]=json!(value);}}}
    Ok(json!({"trades":trades,"metrics":metrics,"equity":curve,"orders":orders,"open_positions":positions,"signals":signals,"audit":audit,
        "holding_drawdowns":holding_drawdowns.unwrap_or(&[]),"counts":counts,"backtest":{"initial_capital":float(result.config.initial_capital)?,
            "execution":result.config,"diagnostics":execution_diagnostics::execution_diagnostics(result),"source":"rust_native_strategy_account",
            "price_basis":"causal_adjusted_equivalent","engineering_verification":"not_run_by_result_builder"}}))
}
pub fn stock_view(result:&StrategyBacktest,bars:&[RuleCandle],signals:&[Value],audit:&[Value],context:&Value,holding_drawdowns:Option<&[Value]>)->AnalyticsResult<Value>{
    let symbol=bars.first().ok_or("stock view requires source bars")?.symbol.as_str();trade_evidence::ensure_single_stock(result,symbol)?;
    crate::analysis::trading::validate_bars(bars)?;
    let asof=context["asof"].as_str().unwrap_or(date(&bars.last().ok_or("stock bars missing")?.timestamp));
    if bars.iter().any(|bar|date(&bar.timestamp)>asof){return Err("stock view contains future source bars".into());}
    if audit.iter().chain(signals).any(|event|event["timestamp"].as_str().is_some_and(|time|date(time)>asof)
        || event["bar_index"].as_u64().is_some_and(|index|index>=bars.len()as u64)){return Err("stock view contains future strategy evidence".into());}
    let grouped=BTreeMap::from([(symbol.to_string(),bars.to_vec())]);let mut payload=build_result(result,&grouped,signals,audit,holding_drawdowns)?;
    let chart:Vec<_>=bars.iter().map(|bar|Ok(json!({"time":date(&bar.timestamp),"open":float(bar.open)?,"high":float(bar.high)?,"low":float(bar.low)?,
        "close":float(bar.close)?,"volume":float(bar.volume)?,"factor":float(bar.adjustment_factor)?,"raw_close":float(bar.close/bar.adjustment_factor)?}))).collect::<AnalyticsResult<_>>()?;
    payload["markers"]=json!(trade_evidence::result_markers(&payload)?);
    payload["curve"]=json!(metrics::chart_curve(payload["equity"].as_array().ok_or("account curve unavailable")?,result.config.initial_capital)?);
    payload["bars"]=json!(chart);payload["symbol"]=json!(symbol);payload["asof"]=json!(asof);payload["result_scope"]=json!("stock");payload["price_basis"]=json!("causal_adjusted_equivalent");
    for key in ["run_id","variant","scenario","strategy_profile"]{if let Some(value)=context.get(key){payload[key]=value.clone();}}
    payload["evidence"]=json!(if result.trades.is_empty(){"个股独立回测：无平仓交易证据"}else{"个股独立回测：已有成交样本，不代表策略有效"});
    payload["comparison"]=json!("相同资金与风控的独立账户，不是共享组合结果的拆分。");Ok(payload)
}
fn write_new(path:&Path,bytes:&[u8])->AnalyticsResult<()>{let mut file=OpenOptions::new().write(true).create_new(true).open(path).map_err(|e|e.to_string())?;
    file.write_all(bytes).and_then(|_|file.sync_all()).map_err(|e|e.to_string())}
pub fn write_rows(path:&Path,rows:&[Value],columns:&[String])->AnalyticsResult<()>{
    let mut writer=csv::Writer::from_writer(Vec::new());writer.write_record(columns).map_err(|e|e.to_string())?;
    for row in rows{let values:Vec<_>=columns.iter().map(|key|match (&row[key]).kind() {None=>String::new(),Some(crate::indexed_json::Kind::String(s))=>s.clone(),_=>row[key].to_string()}).collect();writer.write_record(values).map_err(|e|e.to_string())?;}
    let bytes=writer.into_inner().map_err(|e|e.to_string())?;write_new(path,&bytes)
}
pub fn write_json(path:&Path,value:&Value)->AnalyticsResult<()>{write_new(path,&crate::indexed_json::to_vec_pretty(value).map_err(|e|e.to_string())?)}
pub fn save_result(directory:&Path,result:&Value)->AnalyticsResult<()>{
    fs::create_dir_all(directory).map_err(|e|e.to_string())?;
    if fs::read_dir(directory).map_err(|e|e.to_string())?.next().is_some(){return Err("nonempty output; preserve previous experiment".into());}
    write_result_files(directory,result)
}
fn write_result_files(directory:&Path,result:&Value)->AnalyticsResult<()>{
    for (field,file) in [("trades","trades.csv"),("equity","equity.csv"),("orders","orders.csv"),("signals","signals.csv")]{
        let rows=result[field].as_array().ok_or_else(||format!("result {field} missing"))?;
        let mut columns:BTreeSet<String>=BTreeSet::new();for row in rows{if let Some(map)=row.as_object(){columns.extend(map.keys().cloned());}}
        if field=="equity"{columns.extend(["timestamp","cash","market_value","equity","exposure","positions"].map(str::to_string));}
        if field=="orders"{columns.extend(["timestamp","symbol","side","status","reason","quantity","price","fee"].map(str::to_string));}
        write_rows(&directory.join(file),rows,&columns.into_iter().collect::<Vec<_>>())?;
    }
    for(field,file)in[("open_positions","open_positions.json"),("holding_drawdowns","holding_drawdowns.json"),("metrics","metrics.json"),("audit","event_audit.json"),("backtest","backtest.json")]{write_json(&directory.join(file),&result[field])?;}
    Ok(())
}
pub fn escape_html(value:&str)->String{value.replace('&',"&amp;").replace('<',"&lt;").replace('>',"&gt;").replace('"',"&quot;").replace('\'',"&#39;")}
pub fn report_files(output:&Path,report:&Value)->AnalyticsResult<()>{
    fs::create_dir_all(output).map_err(|e|e.to_string())?;write_json(&output.join("report.json"),report)?;
    let body=crate::indexed_json::to_string_pretty(report).map_err(|e|e.to_string())?;
    write_new(&output.join("report.md"),format!("# 原生量化研究结果\n\n```json\n{body}\n```\n").as_bytes())?;
    write_new(&output.join("report.html"),format!("<!doctype html><html lang=\"zh-CN\"><meta charset=\"utf-8\"><title>量化研究结果</title><style>body{{font:15px/1.6 system-ui;margin:32px}}pre{{white-space:pre-wrap;overflow-wrap:anywhere}}</style><h1>量化研究结果</h1><pre>{}</pre></html>",escape_html(&body)).as_bytes())
}

/// 仅允许当前运行显式嵌入的冻结输入，仍禁止覆盖任何既有实验产物。
pub fn save_result_with_inputs(directory:&Path,result:&Value,inputs:&[&Path])->AnalyticsResult<()>{
    fs::create_dir_all(directory).map_err(|e|e.to_string())?;let root=directory.canonicalize().map_err(|e|e.to_string())?;
    let allowed=inputs.iter().filter(|p|p.is_file()).map(|p|p.canonicalize().map_err(|e|e.to_string())).collect::<AnalyticsResult<BTreeSet<_>>>()?;
    for entry in fs::read_dir(&root).map_err(|e|e.to_string())?{let entry=entry.map_err(|e|e.to_string())?;let path=entry.path().canonicalize().map_err(|e|e.to_string())?;
        if !entry.file_type().map_err(|e|e.to_string())?.is_file()||path.parent()!=Some(root.as_path())||!allowed.contains(&path){return Err("nonempty output contains non-input artifact".into());}}
    write_result_files(directory,result)
}