use super::{OperationsPipeline,FrozenInputs};
use crate::persistence::*;
use chrono::Utc;
use crate::indexed_json::{Value,json};
use std::{collections::BTreeMap,path::Path};

pub fn platform_report(tests:&Value,manifest:&Value,paper:&Value,research:Option<&Value>)->StoreResult<Value>{
    require(tests["status"]=="passed","engineering tests must pass")?;
    let paper_ok=paper["checks"].as_object().is_some_and(|c|!c.is_empty()&&c.values().all(|v|v==true));
    require(paper_ok,"paper acceptance failed")?;
    let capabilities=json!({"durable_paper_oms":paper_ok,"causal_strategy_reuse":paper["checks"]["canonical_signal_to_order"]==true,
        "risk_and_reconciliation":paper["checks"]["independent_reconciliation"]==true,"data_manifest_and_PIT_interface":true,
        "real_PIT_security_data":manifest["checks"]["point_in_time_security_coverage"]==true,"official_calendar_input":manifest["checks"]["exchange_calendar_available"]==true,
        "real_minor_timeframe_data":false,"historical_universe_complete":false,"independent_human_event_annotations":false,"independent_holdout":false,
        "real_broker_adapter":false,"live_order_permission":false,"rolling_diagnostics_executed":research.is_some()});
    let unresolved:Vec<_>=capabilities.as_object().into_iter().flat_map(|m|m.iter()).filter(|(_,v)|**v!=true).map(|(k,_)|k.clone()).collect();
    Ok(json!({"version":"0.3.0-rust","engineering_tests":tests,"data":manifest,"capabilities":capabilities,"paper":paper,
        "production_ready":false,"live_orders_enabled":false,"research_report":research.map(|_|"validation/report.json"),"unresolved":unresolved}))
}
/// 独立审计入口保存实验起止、输入指纹和报告，验收结果必须由原生服务实际生成。
pub fn run_platform_audit<P:OperationsPipeline+?Sized>(inputs:&FrozenInputs,output:&Path,tests:&Value,with_research:bool,pipeline:&mut P)->StoreResult<Value>{
    require(tests["status"]=="passed","engineering tests must pass")?;
    if output.exists(){for path in std::fs::read_dir(output)?{require(path?.file_name()=="test_log.txt","use a new output directory")?;}}
    std::fs::create_dir_all(output)?;let mut store=EventStore::open(&output.join("registry.sqlite"),false)?;
    let mut input_hashes=BTreeMap::new();for path in [Some(inputs.csv.clone()),Some(inputs.csv.with_extension("metadata.json")),Some(inputs.protocol.clone()),inputs.security_master.clone(),inputs.calendar.clone()].into_iter().flatten(){
        input_hashes.insert(path.canonicalize()?.to_string_lossy().into_owned(),fingerprint(&path)?);}
    let(_,environment)=pipeline.environment()?;{let mut registry=ExperimentRegistry{store:&mut store};registry.start("platform-v1",Utc::now(),json!({"protocol":inputs.protocol,"with_research":with_research}),&input_hashes,environment.clone())?;}
    let result=(||->StoreResult<Value>{
        write_json(&output.join("environment.json"),&environment)?;let audit=pipeline.dataset_audit(inputs,Utc::now(),&mut store)?;write_json(&output.join("dataset_manifest.json"),&audit.manifest)?;
        let paper=pipeline.paper_acceptance(&output.join("paper"))?;let research=if with_research{Some(pipeline.research(inputs,&output.join("validation"))?)}else{None};
        let report=platform_report(tests,&audit.manifest,&paper,research.as_ref())?;write_json(&output.join("report.json"),&report)?;
        let mut markdown=String::from("# 量化平台工程验收与数据审计\n\n实盘就绪：否。工程验收与历史诊断不能证明策略有效。\n\n| 能力 | 当前状态 |\n|---|---|\n");
        if let Some(capabilities)=report["capabilities"].as_object(){for(name,available)in capabilities{markdown.push_str(&format!("| {name} | {} |\n",if available==true{"存在／有执行证据"}else{"未就绪／缺失"}));}}
        if research.is_some(){markdown.push_str("\n历史及滚动窗口属于已观察数据的诊断，没有独立锁箱证据，不执行参数自动晋升。\n");}
        std::fs::write(output.join("report.md"),markdown)?;
        let artifacts:Vec<_>=directory_files(output)?.into_iter().filter(|p|!p.file_name().map(|n|n.to_string_lossy().contains(".sqlite")).unwrap_or(false)).collect();
        {let mut registry=ExperimentRegistry{store:&mut store};registry.finish("platform-v1",Utc::now(),&artifacts,"COMPLETED",None)?;
            require(registry.verify_artifacts("platform-v1")?.values().all(|v|*v),"artifact fingerprint mismatch")?;}
        store.backup(&output.join("registry.backup.sqlite"))?;Ok(report)
    })();
    if result.is_err()&&store.get("experiment:end:platform-v1")?.is_none(){ExperimentRegistry{store:&mut store}.finish("platform-v1",Utc::now(),&[],"FAILED",Some("platform_audit_failed"))?;}
    result
}
