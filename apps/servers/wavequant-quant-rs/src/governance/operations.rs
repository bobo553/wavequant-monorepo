use crate::persistence::*;
use chrono::{DateTime,Utc,NaiveDate,Timelike};
use serde::{Serialize,Deserialize};
use crate::indexed_json::{Value,json};
use std::{collections::{BTreeMap,BTreeSet},path::{Path,PathBuf}};

fn yes()->bool{true}fn offline()->String{"offline".into()}fn start_date()->String{"2018-01-01".into()}
#[derive(Debug,Clone,Serialize,Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OperationsConfig{
    pub root:PathBuf,pub csv:PathBuf,pub protocol:PathBuf,#[serde(default)]pub security_master:Option<PathBuf>,#[serde(default)]pub calendar:Option<PathBuf>,
    #[serde(default)]pub refresh_tdx:bool,#[serde(default)]pub tdx_root:Option<PathBuf>,#[serde(default)]pub symbols:Vec<String>,
    #[serde(default="start_date")]pub start:String,#[serde(default)]pub end:Option<String>,#[serde(default="yes")]pub run_research:bool,#[serde(default="offline")]pub mode:String,
}
impl OperationsConfig{
    pub fn load(path:&Path)->StoreResult<Self>{let path=path.canonicalize()?;let mut config:Self=crate::indexed_json::from_slice(&std::fs::read(&path)?)?;
        let parent=path.parent().ok_or_else(||StoreError("configuration parent unavailable".into()))?;
        for path in [&mut config.root,&mut config.csv,&mut config.protocol]{*path=absolute_path(&parent.join(&*path))?;}
        for path in [&mut config.security_master,&mut config.calendar,&mut config.tdx_root].into_iter().flatten(){*path=absolute_path(&parent.join(&*path))?;}
        config.validate()?;Ok(config)
    }
    pub fn validate(&self)->StoreResult<()>{
        require(self.mode=="offline","only offline operations are supported; no live adapter")?;
        let start=NaiveDate::parse_from_str(&self.start,"%Y-%m-%d").map_err(|e|StoreError(e.to_string()))?;
        if let Some(end)=&self.end{require(NaiveDate::parse_from_str(end,"%Y-%m-%d").map_err(|e|StoreError(e.to_string()))?>=start,"end precedes start")?;}
        require(!self.refresh_tdx||self.tdx_root.is_some()&&!self.symbols.is_empty()&&self.end.is_some(),"TDX refresh requires root, frozen symbols and explicit end")?;
        let mut unique=BTreeSet::new();for symbol in &self.symbols{require(!symbol.is_empty()&&unique.insert(symbol),"unique explicit symbols required")?;
            if self.refresh_tdx{let valid=symbol.len()==9&&symbol.is_ascii()&&(symbol.starts_with("sh.60")||symbol.starts_with("sz.00"))&&symbol[3..].bytes().all(|b|b.is_ascii_digit());
                require(valid,"TDX refresh supports established SH/SZ main-board identifiers only")?;}}
        let root=absolute_path(&self.root)?;for input in [Some(&self.csv),Some(&self.protocol),self.security_master.as_ref(),self.calendar.as_ref(),self.tdx_root.as_ref()].into_iter().flatten(){
            require(!absolute_path(input)?.starts_with(&root),"input files must live outside operations output root")?;}
        Ok(())
    }
}
#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct Freshness{pub status:String,pub expected_session:Option<String>,pub latest_by_symbol:BTreeMap<String,String>,pub reason:String,pub lagging_symbols:Vec<String>}
pub fn check_freshness(latest:&BTreeMap<String,String>,calendar:Option<&[String]>,when:DateTime<Utc>)->StoreResult<Freshness>{
    let mut result=Freshness{status:"UNKNOWN".into(),expected_session:None,latest_by_symbol:latest.clone(),reason:"official_exchange_calendar_not_supplied".into(),lagging_symbols:vec![]};
    let Some(calendar)=calendar else{return Ok(result);};for date in calendar{NaiveDate::parse_from_str(date,"%Y-%m-%d").map_err(|e|StoreError(e.to_string()))?;}
    require(calendar.windows(2).all(|p|p[0]<p[1]),"calendar must contain unique ordered ISO sessions")?;
    let local=when+chrono::Duration::hours(8);let today=local.format("%Y-%m-%d").to_string();
    if calendar.last().is_none_or(|last|last<&today){result.reason="calendar_does_not_cover_current_date".into();return Ok(result);}
    let Some(expected)=calendar.iter().rev().find(|d|*d<&today||**d==today&&local.hour()>=15)else{result.reason="no_completed_session_in_calendar".into();return Ok(result);};
    result.expected_session=Some(expected.clone());result.lagging_symbols=latest.iter().filter(|(_,d)|*d<expected).map(|(s,_)|s.clone()).collect();
    result.status=if result.lagging_symbols.is_empty(){"CURRENT"}else{"STALE"}.into();result.reason="supplied_calendar_comparison; suspensions require separate security evidence".into();Ok(result)
}
#[derive(Debug,Clone)]
pub struct FrozenInputs{pub csv:PathBuf,pub protocol:PathBuf,pub security_master:Option<PathBuf>,pub calendar:Option<PathBuf>}
pub struct DatasetAudit{pub manifest:Value,pub latest_by_symbol:BTreeMap<String,String>}
/// 原生服务实现真实阶段；这里只调度，不调用 Python、不接收任意外部 shell 命令。
pub trait OperationsPipeline{
    fn environment(&mut self)->StoreResult<(PathBuf,Value)>;
    fn engineering_tests(&mut self,output:&Path)->StoreResult<Value>;
    fn import_tdx(&mut self,raw_root:&Path,csv:&Path,config:&OperationsConfig)->StoreResult<()>;
    fn dataset_audit(&mut self,inputs:&FrozenInputs,when:DateTime<Utc>,store:&mut EventStore)->StoreResult<DatasetAudit>;
    fn research(&mut self,inputs:&FrozenInputs,output:&Path)->StoreResult<Value>;
    fn paper_acceptance(&mut self,output:&Path)->StoreResult<Value>;
    fn account_report(&mut self,journal:&Path)->StoreResult<Value>;
    fn journal_head(&mut self,journal:&Path)->StoreResult<String>;
}
pub fn verify_run(path:&Path,expected:Option<&str>)->StoreResult<bool>{
    let root=path.canonicalize()?;let manifest_path=root.join("artifacts.json");if let Some(expected)=expected{require(fingerprint(&manifest_path)?==expected,"run artifact manifest changed")?;}
    let manifest:BTreeMap<String,String>=crate::indexed_json::from_slice(&std::fs::read(manifest_path)?)?;require(!manifest.is_empty(),"empty run artifact manifest")?;
    for(name,digest)in manifest{let candidate=safe_member(&root,&name)?;require(candidate.is_file()&&fingerprint(&candidate)?==digest,"run artifact integrity failure")?;}Ok(true)
}
fn record_stage(store:&mut EventStore,run_id:&str,name:&str)->StoreResult<()>{store.transaction(|store|{store.append(&format!("stage:{run_id}:{name}"),"RUN_STAGE",Utc::now(),json!({"run_id":run_id,"stage":name,"status":"COMPLETED"}))?;Ok(())})}
fn checks_passed(value:&Value)->bool{value.get("checks").and_then(Value::as_object).is_some_and(|c|!c.is_empty()&&c.values().all(|v|v==true))}
pub fn readiness(manifest:&Value,freshness:&Freshness,research:Option<&Value>,paper:&Value)->Vec<Value>{
    vec![json!({"code":"local_engineering","status":if checks_passed(paper){"PASS"}else{"FAIL"},"detail":"Synthetic signal/order/restart/reconciliation/backup acceptance; not market returns."}),
        json!({"code":"historical_security_data","status":if manifest["checks"]["point_in_time_security_coverage"]==true{"PASS"}else{"BLOCKED"},"detail":format!("Unknown historical security rows: {}",manifest["unknown_security_rows"])}),
        json!({"code":"exchange_calendar","status":if manifest["checks"]["exchange_calendar_available"]==true{"PASS"}else{"BLOCKED"},"detail":"Supplied calendar is checked, its external authority is not automatically authenticated."}),
        json!({"code":"daily_freshness","status":freshness.status,"detail":freshness.reason}),
        json!({"code":"strict_minor_paths","status":"BLOCKED","detail":"Daily OHLC cannot establish inside/outside intrabar ordering."}),
        json!({"code":"historical_universe","status":"BLOCKED","detail":"Local files do not establish historical delisted-universe completeness."}),
        json!({"code":"independent_annotations","status":"BLOCKED","detail":"No independent labelled gold set supplied."}),
        json!({"code":"strategy_edge","status":"NOT_ESTABLISHED","detail":"Fixed historical diagnostics are not independent forward evidence."}),
        json!({"code":"historical_diagnostics","status":if research.is_some(){"EXECUTED"}else{"NOT_RUN"},"detail":"Strict and daily proxy remain separate; no automatic parameter promotion."}),
        json!({"code":"broker_and_live_permission","status":"DISABLED","detail":"No broker adapter, credentials or real order route."}),
        json!({"code":"external_notifications","status":"NOT_CONFIGURED","detail":"Local durable inbox only; no email/webhook delivery."}),
        json!({"code":"external_backup_security","status":"NOT_ESTABLISHED","detail":"Local hashes and restore checks are not offsite backups or access control."})]
}
fn report_markdown(report:&Value)->String{
    let mut output=format!("# 量化系统运行验收\n\n运行：{}；离线流水线已执行；实盘订单关闭；策略有效性未建立。\n\n| 验收项 | 状态 | 说明 |\n|---|---|---|\n",report["run_id"].as_str().unwrap_or(""));
    if let Some(rows)=report["readiness"].as_array(){for row in rows{output.push_str(&format!("| {} | {} | {} |\n",row["code"].as_str().unwrap_or(""),row["status"].as_str().unwrap_or(""),row["detail"].as_str().unwrap_or("").replace('|',"\\|")));}}
    output
}
pub fn run_operations<P:OperationsPipeline+?Sized>(config:&OperationsConfig,run_id:&str,pipeline:&mut P)->StoreResult<Value>{
    config.validate()?;identifier(run_id)?;let root=absolute_path(&config.root)?;let _lock=WorkspaceLock::acquire(&root)?;
    let mut store=EventStore::open(&root.join("operations.sqlite"),false)?;let cfg=crate::indexed_json::to_value(config)?;let identity=sha256(canonical(&cfg)?);
    let runs=run_states(&store)?;let output=root.join("runs").join(run_id);
    if let Some(previous)=runs.get(run_id){require(previous["config_hash"].as_str()==Some(&identity),"run ID/configuration conflict")?;
        if previous["status"]=="COMPLETED"{verify_run(&output,previous["artifacts_sha256"].as_str())?;return Ok(crate::indexed_json::from_slice(&std::fs::read(output.join("report.json"))?)?);}
        return Err(StoreError("run ID already used; inspect failure/interruption and choose a new ID".into()));}
    for(id,old)in runs{if old["status"]=="RUNNING"{store.transaction(|store|{store.append(&format!("run:end:{id}"),"RUN_FINISHED",Utc::now(),json!({"run_id":id,"status":"INTERRUPTED","error":"worker ended before terminal record"}))?;Ok(())})?;}}
    require(!output.exists(),"unregistered existing run directory; never overwrite it")?;std::fs::create_dir_all(&output)?;
    store.transaction(|store|{store.append(&format!("run:{run_id}"),"RUN_STARTED",Utc::now(),json!({"run_id":run_id,"config_hash":identity,"config":cfg}))?;Ok(())})?;
    let result=(||->StoreResult<Value>{
        write_json(&output.join("config.json"),&cfg)?;let(project_root,environment)=pipeline.environment()?;write_json(&output.join("environment.json"),&environment)?;
        let hashes=environment["hashes"].as_object().ok_or_else(||StoreError("environment source hashes required".into()))?;
        for(relative,digest)in hashes{let path=safe_member(&project_root,relative)?;let target=safe_member(&output.join("snapshot/source"),relative)?;
            require(digest.as_str()==Some(freeze_file(&path,&target)?.as_str()),"source changed while freezing environment")?;}
        let mut inputs=BTreeMap::new();let protocol=output.join("snapshot/protocol.json");inputs.insert(config.protocol.to_string_lossy().into_owned(),freeze_file(&config.protocol,&protocol)?);
        let mut frozen=FrozenInputs{csv:output.join("snapshot/daily.csv"),protocol,security_master:None,calendar:None};
        for(name,path)in [("security_master",&config.security_master),("calendar",&config.calendar)]{if let Some(path)=path{
            let target=output.join("snapshot").join(format!("{name}.json"));inputs.insert(path.to_string_lossy().into_owned(),freeze_file(path,&target)?);
            if name=="security_master"{frozen.security_master=Some(target);}else{frozen.calendar=Some(target);}
        }}
        let tests=pipeline.engineering_tests(&output)?;require(tests["status"]=="passed","engineering gate failed")?;record_stage(&mut store,run_id,"tests")?;
        if config.refresh_tdx{let raw_root=output.join("snapshot/tdx_raw");let source_root=config.tdx_root.as_ref().ok_or_else(||StoreError("TDX root missing".into()))?;
            let mut relatives=vec!["T0002/hq_cache/gbbq".to_string()];for symbol in &config.symbols{let(market,code)=symbol.split_once('.').ok_or_else(||StoreError("invalid TDX symbol".into()))?;relatives.push(format!("vipdoc/{market}/lday/{market}{code}.day"));}
            for relative in relatives{let source=safe_member(source_root,&relative)?;let target=safe_member(&raw_root,&relative)?;inputs.insert(source.to_string_lossy().into_owned(),freeze_file(&source,&target)?);}
            pipeline.import_tdx(&raw_root,&frozen.csv,config)?;record_stage(&mut store,run_id,"import_tdx")?;
        }else{inputs.insert(config.csv.to_string_lossy().into_owned(),freeze_file(&config.csv,&frozen.csv)?);let metadata=config.csv.with_extension("metadata.json");
            inputs.insert(metadata.to_string_lossy().into_owned(),freeze_file(&metadata,&frozen.csv.with_extension("metadata.json"))?);}
        write_json(&output.join("inputs.json"),&inputs)?;let when=Utc::now();let audit=pipeline.dataset_audit(&frozen,when,&mut store)?;record_stage(&mut store,run_id,"data_audit")?;
        let manifest=audit.manifest;write_json(&output.join("data_manifest.json"),&manifest)?;
        require(manifest["checks"]["no_future_rows"]==true,"data contains not-yet-available bars")?;require(manifest["checks"]["listed_period_only"]==true,"data outside supplied listing periods")?;
        let calendar:Option<Vec<String>>=frozen.calendar.as_ref().map(|p|Ok::<_,StoreError>(crate::indexed_json::from_slice(&std::fs::read(p)?)?)).transpose()?;
        let freshness=check_freshness(&audit.latest_by_symbol,calendar.as_deref(),when)?;
        let research=if config.run_research{let result=pipeline.research(&frozen,&output.join("research"))?;record_stage(&mut store,run_id,"historical_diagnostics")?;Some(result)}else{None};
        let paper=pipeline.paper_acceptance(&output.join("paper"))?;require(checks_passed(&paper),"paper acceptance failed")?;record_stage(&mut store,run_id,"paper_acceptance")?;
        let accounting=pipeline.account_report(&output.join("paper/account.sqlite"))?;write_json(&output.join("paper/accounting.json"),&accounting)?;require(accounting["pnl_conserved"]==true,"paper P&L conservation failed")?;
        create_bundle(&output.join("snapshot"),&output.join("backup_inputs"))?;record_stage(&mut store,run_id,"backup")?;
        restore_bundle(&output.join("backup_inputs"),&output.join("restored_inputs"))?;record_stage(&mut store,run_id,"restore_inputs")?;
        create_bundle(&output.join("paper"),&output.join("backup_accounts"))?;record_stage(&mut store,run_id,"backup_accounts")?;
        restore_bundle(&output.join("backup_accounts"),&output.join("restored_accounts"))?;record_stage(&mut store,run_id,"restore_accounts")?;
        for name in ["account","venue"]{require(pipeline.journal_head(&output.join("paper").join(format!("{name}.sqlite")))?==pipeline.journal_head(&output.join("restored_accounts").join(format!("{name}.sqlite")))?,"restored journal differs")?;}
        let readiness=readiness(&manifest,&freshness,research.as_ref(),&paper);let mut observations=vec![];
        for row in &readiness{if !["PASS","CURRENT","EXECUTED"].contains(&row["status"].as_str().unwrap_or("")){observations.push(AlertObservation{code:row["code"].as_str().unwrap_or("").into(),severity:"warning".into(),message:row["detail"].as_str().unwrap_or("").into()});}}
        if let Some(missing)=manifest["missing_expected_sessions"].as_array(){if !missing.is_empty(){observations.push(AlertObservation{code:"missing_sessions".into(),severity:"critical".into(),message:format!("Expected active sessions missing: {}",missing.len())});}}
        let alerts=synchronize_alerts(&mut store,"readiness",&observations,Utc::now())?;synchronize_alerts(&mut store,"worker",&[],Utc::now())?;
        let summary=research.as_ref().and_then(|r|r["variants"].as_object()).map(|variants|variants.iter().map(|(name,row)|{
            let scenarios: BTreeMap<String,Value>=row["scenarios"].as_object().map(|m|m.iter().map(|(label,v)|(label.clone(),v["metrics"].clone())).collect()).unwrap_or_default();(name.clone(),scenarios)
        }).collect::<BTreeMap<_,_>>());
        let report=json!({"schema":1,"run_id":run_id,"mode":"offline","completed_at":Utc::now(),"engineering_tests":tests,"data":manifest,
            "freshness":freshness,"readiness":readiness,"research_summary":summary,"paper_checks":paper["checks"],"active_alerts":alerts,
            "recovery_drill_passed":true,"production_ready":false,"live_orders_enabled":false,"strategy_validated":false});
        write_json(&output.join("report.json"),&report)?;std::fs::write(output.join("report.md"),report_markdown(&report))?;
        require(pipeline.environment()?.1==environment,"source/test/environment changed during execution")?;
        for(path,digest)in &inputs{require(fingerprint(Path::new(path))?==*digest,"input changed during execution")?;}
        let mut artifacts=BTreeMap::new();for path in directory_files(&output)?{let name=path.file_name().map(|n|n.to_string_lossy()).unwrap_or_default();if name.ends_with("-wal")||name.ends_with("-shm"){continue;}
            artifacts.insert(path.strip_prefix(&output).map_err(|e|StoreError(e.to_string()))?.to_string_lossy().replace('\\',"/"),fingerprint(&path)?);}
        write_json(&output.join("artifacts.json"),&artifacts)?;verify_run(&output,None)?;
        store.transaction(|store|{store.append(&format!("run:end:{run_id}"),"RUN_FINISHED",Utc::now(),json!({"run_id":run_id,"status":"COMPLETED","artifacts_sha256":fingerprint(&output.join("artifacts.json"))?}))?;Ok(())})?;Ok(report)
    })();
    if result.is_err(){let stages=run_states(&store)?.get(run_id).and_then(|r|r.get("stages")).cloned().unwrap_or(json!([]));
        let failure=json!({"run_id":run_id,"status":"FAILED","error":"pipeline_error","completed_stages":stages});write_json(&output.join("failure.json"),&failure)?;
        store.transaction(|store|{store.append(&format!("run:end:{run_id}"),"RUN_FINISHED",Utc::now(),failure)?;Ok(())})?;
        synchronize_alerts(&mut store,"worker",&[AlertObservation{code:"pipeline_failed".into(),severity:"critical".into(),message:format!("Run {run_id} failed; inspect failure.json and command error.")}],Utc::now())?;
    }
    result
}
pub fn operations_status(root:&Path)->StoreResult<Value>{let root=root.canonicalize()?;let mut store=EventStore::open(&root.join("operations.sqlite"),true)?;
    store.snapshot(|store|{let runs=run_states(store)?;let integrity:BTreeMap<_,_>=runs.iter().filter(|(_,r)|r["status"]=="COMPLETED").map(|(id,r)|
        (id.clone(),verify_run(&root.join("runs").join(id),r["artifacts_sha256"].as_str()).unwrap_or(false))).collect();
        Ok(json!({"runs":runs,"artifact_integrity":integrity,"journal_head":store.verify()?,"alerts":alert_states(store)?.into_values().collect::<Vec<_>>(),"live_orders_enabled":false,
            "note":"RUNNING means no terminal record yet; status alone does not prove worker liveness."}))})
}
