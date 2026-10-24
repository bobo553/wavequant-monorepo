//! 既有固定协议实验的归并与门槛计算，不生成信号，不运行回测或参数搜索。
use super::{AnalyticsResult,number,research};
use crate::analysis::trading::RuleCandle;
use crate::indexed_json::{Value,json};
use std::{collections::BTreeMap,path::Path};
pub fn validate_protocol(grouped:&BTreeMap<String,Vec<RuleCandle>>,protocol:&Value)->AnalyticsResult<()>{
    if grouped.is_empty()||grouped.values().any(Vec::is_empty){return Err("research requires nonempty security history".into());}
    let last=grouped.values().filter_map(|bars|bars.last().map(|b|super::date(&b.timestamp))).min().ok_or("research coverage missing")?;
    if protocol["data_end"].as_str()!=Some(last)||grouped.values().any(|b|b.last().is_none_or(|bar|super::date(&bar.timestamp)!=last)){return Err("fixed protocol data end must match all securities".into());}
    let mut previous=None;
    for label in ["train","validation","diagnostic"]{let period=protocol["periods"][label].as_array().filter(|p|p.len()==2).ok_or("research period missing")?;
        let begin=period[0].as_str().ok_or("period begin missing")?;let end=period[1].as_str().ok_or("period end missing")?;
        if begin>end||end>last||previous.is_some_and(|old|old>=begin){return Err("ordered nonoverlapping covered periods required".into());}
        if grouped.values().any(|bars|!bars.iter().any(|b|begin<=super::date(&b.timestamp)&&super::date(&b.timestamp)<=end)){return Err("every security requires observations in every period".into());}previous=Some(end);
    }Ok(())
}
pub fn research_gates(protocol:&Value,results:&BTreeMap<String,Value>)->AnalyticsResult<Value>{
    let variants=protocol["variants"].as_object().ok_or("protocol variants missing")?;let gate=&protocol["gate"];
    let required=|key:&str|number(&gate[key]).ok_or_else(||format!("gate threshold missing: {key}"));let mut gates=BTreeMap::new();
    for name in variants.keys(){let rows=results.get(name).ok_or("fixed variant result missing")?;
        let metric=|period:&str,key:&str|number(&rows[period][key]).ok_or_else(||format!("{name} {period} {key} missing"));
        let minimum_sharpe=required("minimum_sharpe")?;
        let checks=json!({"training_trade_count":metric("train","trades")?>=required("minimum_train_trades")?,
            "validation_positive":metric("validation","total_return")?>0.0,"diagnostic_positive":metric("diagnostic","total_return")?>0.0,
            "diagnostic_trade_count":metric("diagnostic","trades")?>=required("minimum_diagnostic_trades")?,
            "diagnostic_sharpe":number(&rows["diagnostic"]["sharpe"]).is_some_and(|v|v>=minimum_sharpe),
            "drawdown_limit":metric("diagnostic","max_drawdown")?>=-required("maximum_drawdown")?,"cost_stress_positive":metric("cost_2x","total_return")?>0.0});
        let passed=checks.as_object().is_some_and(|m|m.values().all(|v|v==true));gates.insert(name,json!({"passed":passed,"checks":checks}));
    }Ok(json!(gates))
}
pub fn assemble_report(mut disclosed_inputs:Value,protocol:&Value,results:&BTreeMap<String,Value>,output:Option<&Path>)->AnalyticsResult<Value>{
    if !disclosed_inputs.is_object(){return Err("system report inputs must be an object".into());}
    // 保留旧研究执行入口的工程证据门禁，不能把本轮未运行转换为通过。
    if disclosed_inputs["engineering_tests"]["status"]!="passed"{return Err("engineering test gate must pass before fixed system research publication".into());}
    disclosed_inputs["protocol"]=protocol.clone();disclosed_inputs["results"]=json!(results);disclosed_inputs["gates"]=research_gates(protocol,results)?;
    disclosed_inputs["production_ready"]=json!(false);disclosed_inputs["selection"]=json!("fixed_primary_no_selection_no_proxy_promotion");
    if let Some(output)=output{research::report_files(output,&disclosed_inputs)?;}Ok(disclosed_inputs)
}
