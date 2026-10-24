//! 请求断开后继续持有真实计算任务；完成历史与大结果使用不同保留窗口。
use std::{collections::BTreeMap, path::PathBuf, sync::{Arc,Mutex,MutexGuard,atomic::{AtomicBool,Ordering}}, time::{Duration,Instant,SystemTime,UNIX_EPOCH}};
use rusqlite::{Connection,params};
use wavequant_quant_rs::indexed_json::{Value,json};
use crate::presentation::error::ApiError;

type Result<T> = std::result::Result<T,ApiError>;
const MAX_ACTIVE:usize=4;
const MAX_COMPLETED:usize=24;
const MAX_HISTORY:usize=256;
const RESULT_TTL:Duration=Duration::from_secs(900);
const HISTORY_TTL:f64=7.0*86400.0;

#[derive(Clone)]
pub struct JobFailure {pub http_status:u16,pub code:Option<String>,pub message:String}
struct Job {
    signature:String,symbol:String,details:Value,started:Instant,completed:Option<Instant>,
    outcome:Option<std::result::Result<Value,JobFailure>>,percent:u8,stage:String,timing:String,
    data_started:Option<Instant>,data_seconds:f64,analysis_seconds:f64,
    pipeline:Option<Value>,preview_revision:u64,preview:Option<Value>,cancel:Arc<AtomicBool>,finished:Arc<tokio::sync::Notify>,
}
struct Registry {jobs:BTreeMap<String,Job>,history:BTreeMap<String,(f64,Value)>,accepting:bool}
#[derive(Clone)]
pub struct BacktestJobs {inner:Arc<Mutex<Registry>>,history_path:Option<PathBuf>,engine_version:String}
#[derive(Clone)]
pub struct JobReporter {jobs:BacktestJobs,id:String,cancel:Arc<AtomicBool>}

pub fn validate_id(id:&str)->Result<()> {
    if !(8..=128).contains(&id.len()) || !id.bytes().all(|byte|byte.is_ascii_alphanumeric()||matches!(byte,b'_'|b'-')) {return Err(ApiError::invalid("invalid backtest job ID"));}Ok(())
}
fn epoch()->f64 {SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_secs_f64()}
fn total_elapsed(job:&Job)->f64 {((job.completed.unwrap_or_else(Instant::now)-job.started).as_secs_f64()*10.0).round()/10.0}
fn elapsed(job:&Job)->f64 {job.analysis_seconds}
fn data_elapsed(job:&Job)->f64 {job.data_seconds+job.data_started.map(|at|(job.completed.unwrap_or_else(Instant::now)-at).as_secs_f64()).unwrap_or(0.0)}
fn progress(job:&Job)->Value {json!({"elapsed_seconds":elapsed(job),"analysis_elapsed_seconds":elapsed(job),"data_request_seconds":data_elapsed(job),"total_elapsed_seconds":total_elapsed(job),"timing_scope":"analysis","progress_percent":job.percent,"progress_stage":job.stage,"pipeline":job.pipeline,"preview_revision":job.preview_revision,"processed_sessions":job.preview.as_ref().map(|p|&p["processed"]),"total_sessions":job.preview.as_ref().map(|p|&p["total"]),"processed_asof":job.preview.as_ref().map(|p|&p["asof"])})}

impl BacktestJobs {
    pub fn new(history_path:Option<PathBuf>,engine_version:String)->Result<Self> {
        let service=Self{inner:Arc::new(Mutex::new(Registry{jobs:BTreeMap::new(),history:BTreeMap::new(),accepting:true})),history_path,engine_version};
        if let Some(connection)=service.connection()? {
            let mut statement=connection.prepare("SELECT signature,completed_at,item FROM rust_backtest_history WHERE engine_version=?1 AND completed_at>?2 ORDER BY completed_at DESC LIMIT ?3")?;
            let records=statement.query_map(params![service.engine_version,epoch()-HISTORY_TTL,MAX_HISTORY as i64],|row|Ok((row.get::<_,String>(0)?,row.get::<_,f64>(1)?,row.get::<_,String>(2)?)))?;
            let mut registry=service.lock()?;
            for record in records {let (signature,completed,item)=record?;let value:Value=wavequant_quant_rs::indexed_json::from_str(&item)?;if value.is_object() && value["job"].is_string(){registry.history.insert(signature,(completed,value));}}
        }
        Ok(service)
    }
    fn lock(&self)->Result<MutexGuard<'_,Registry>> {self.inner.lock().map_err(|_|ApiError::internal())}
    fn connection(&self)->Result<Option<Connection>> {
        let Some(path)=&self.history_path else{return Ok(None);};
        if let Some(parent)=path.parent(){std::fs::create_dir_all(parent)?;}
        let connection=Connection::open(path)?;
        connection.busy_timeout(Duration::from_millis(500))?;
        connection.execute_batch("CREATE TABLE IF NOT EXISTS rust_backtest_history(engine_version TEXT NOT NULL,signature TEXT NOT NULL,completed_at REAL NOT NULL,item TEXT NOT NULL,PRIMARY KEY(engine_version,signature))")?;
        Ok(Some(connection))
    }
    fn prune(registry:&mut Registry) {
        let now=Instant::now();registry.jobs.retain(|_,job|job.completed.is_none_or(|completed|now.duration_since(completed)<RESULT_TTL));
        let mut completed=registry.jobs.iter().filter_map(|(id,job)|job.completed.map(|at|(id.clone(),at))).collect::<Vec<_>>();
        completed.sort_by_key(|(_,at)|*at);
        for (id,_) in completed.iter().take(completed.len().saturating_sub(MAX_COMPLETED)){registry.jobs.remove(id);}
        registry.history.retain(|_,(completed,_)|epoch()-*completed<HISTORY_TTL);
        let mut history=registry.history.iter().map(|(key,(completed,_))|(key.clone(),*completed)).collect::<Vec<_>>();history.sort_by(|a,b|a.1.total_cmp(&b.1));
        for (key,_) in history.iter().take(history.len().saturating_sub(MAX_HISTORY)){registry.history.remove(key);}
    }
    pub fn start<F>(&self,id:String,signature:String,symbol:String,details:Value,work:F)->Result<()>
    where F:FnOnce(JobReporter)->std::result::Result<Value,JobFailure>+Send+'static {
        validate_id(&id)?;
        if signature.is_empty()||signature.len()>32768||symbol.is_empty()||!details.is_object(){return Err(ApiError::invalid("invalid backtest job metadata"));}
        let runtime=tokio::runtime::Handle::try_current().map_err(|_|ApiError::unavailable("任务调度器未启动"))?;
        let symbol=symbol.to_lowercase();
        let mut registry=self.lock()?;Self::prune(&mut registry);
        if !registry.accepting{return Err(ApiError::unavailable("服务正在停止，不能提交新任务"));}
        if let Some(job)=registry.jobs.get(&id){return if job.signature==signature {Ok(())}else{Err(ApiError(axum::http::StatusCode::CONFLICT,"BACKTEST_JOB_CONFLICT","backtest job ID belongs to different arguments".into()))};}
        if let Some((running,_))=registry.jobs.iter().find(|(_,job)|job.outcome.is_none()&&job.symbol==symbol){return Err(ApiError(axum::http::StatusCode::CONFLICT,"BACKTEST_SYMBOL_RUNNING",format!("stock already has a running backtest: {running}")));}
        if registry.jobs.values().filter(|job|job.outcome.is_none()).count()>=MAX_ACTIVE {return Err(ApiError(axum::http::StatusCode::SERVICE_UNAVAILABLE,"BACKTEST_CAPACITY","backtest capacity reached; retry shortly".into()));}
        let cancel=Arc::new(AtomicBool::new(false));
        registry.jobs.insert(id.clone(),Job{signature,symbol,details,started:Instant::now(),completed:None,outcome:None,percent:0,stage:"准备回测".into(),timing:String::new(),data_started:None,data_seconds:0.0,analysis_seconds:0.0,pipeline:None,preview_revision:0,preview:None,cancel:cancel.clone(),finished:Arc::new(tokio::sync::Notify::new())});
        drop(registry);
        let owner=self.clone();
        runtime.spawn(async move{
            let reporter=JobReporter{jobs:owner.clone(),id:id.clone(),cancel:cancel.clone()};
            let outcome=match tokio::task::spawn_blocking(move||work(reporter)).await {Ok(outcome)=>outcome,Err(_)=>Err(JobFailure{http_status:500,code:None,message:"回测计算失败，请稍后重试".into()})};
            let outcome=if cancel.load(Ordering::Acquire){Err(JobFailure{http_status:503,code:Some("BACKTEST_CANCELLED".into()),message:"服务停止，回测已取消".into()})}else{outcome};
            if let Err(error)=owner.finish(&id,outcome){tracing::error!(code=error.1,"backtest completion history failed");}
        });
        Ok(())
    }
    pub fn cancel_for_shutdown(&self)->Result<()> {let mut registry=self.lock()?;registry.accepting=false;for job in registry.jobs.values(){if job.outcome.is_none(){job.cancel.store(true,Ordering::Release);}}Ok(())}
    fn finish(&self,id:&str,outcome:std::result::Result<Value,JobFailure>)->Result<()> {
        let mut registry=self.lock()?;
        let job=registry.jobs.get_mut(id).ok_or_else(ApiError::internal)?;
        job.completed=Some(Instant::now());job.preview=None;
        let mut item=job.details.clone();item["job"]=json!(id);item["status"]=json!(if outcome.is_ok(){"completed"}else{"failed"});item["elapsed_seconds"]=json!(elapsed(job));item["pipeline"]=json!(job.pipeline);item["analysis_elapsed_seconds"]=json!(elapsed(job));item["data_request_seconds"]=json!(data_elapsed(job));item["total_elapsed_seconds"]=json!(total_elapsed(job));item["timing_scope"]=json!("analysis");
        if let Ok(result)=&outcome {
            let valid=result["calculation_mode"]!="polyline_only"&&result["calculation_mode"]!="first_level_only"&&result["complete"]!=false&&result["result_scope"]=="stock"&&result["backtest"].is_object()&&result["backtest"]["status"]!="data_unavailable";
            item["result_valid"]=json!(valid);
            if valid {
                if let Some(orders)=result["orders"].as_array(){item["fill_count"]=json!(orders.iter().filter(|row|row["status"]=="filled").count());}
                for key in ["total_return","total_pnl","max_drawdown","holding_max_drawdown","holding_current_max_drawdown"] {if result["metrics"][key].as_f64().is_some_and(f64::is_finite){item[key]=result["metrics"][key].clone();}}
                for key in ["holding_drawdown_version","holding_drawdown_status","holding_current_drawdown_status"] {if result["metrics"][key].is_string(){item[key]=result["metrics"][key].clone();}}
            }
        }
        job.outcome=Some(outcome);job.finished.notify_waiters();
        let signature=job.signature.clone();let completed=epoch();registry.history.insert(signature.clone(),(completed,item.clone()));Self::prune(&mut registry);drop(registry);
        if let Some(mut connection)=self.connection()? {
            let tx=connection.transaction()?;
            tx.execute("INSERT OR REPLACE INTO rust_backtest_history VALUES (?1,?2,?3,?4)",params![self.engine_version,signature,completed,wavequant_quant_rs::indexed_json::to_string(&item)?])?;
            tx.execute("DELETE FROM rust_backtest_history WHERE completed_at<=?1",params![epoch()-HISTORY_TTL])?;
            tx.execute("DELETE FROM rust_backtest_history WHERE engine_version=?1 AND signature IN (SELECT signature FROM rust_backtest_history WHERE engine_version=?1 ORDER BY completed_at DESC LIMIT -1 OFFSET ?2)",params![self.engine_version,MAX_HISTORY as i64])?;tx.commit()?;
        }
        Ok(())
    }
    pub async fn wait(&self,id:&str)->Result<Value> {
        validate_id(id)?;
        let notification={let registry=self.lock()?;registry.jobs.get(id).ok_or_else(||ApiError(axum::http::StatusCode::NOT_FOUND,"BACKTEST_NOT_FOUND","backtest job not found".into()))?.finished.clone()};
        loop {let notified=notification.notified();tokio::pin!(notified);notified.as_mut().enable();let(done,value)=self.outcome(id)?;if done{return Ok(value);}notified.await;}
    }
    pub fn outcome(&self,id:&str)->Result<(bool,Value)> {
        validate_id(id)?;let mut registry=self.lock()?;Self::prune(&mut registry);
        let job=registry.jobs.get(id).ok_or_else(||ApiError(axum::http::StatusCode::NOT_FOUND,"BACKTEST_NOT_FOUND","backtest job not found".into()))?;
        let mut value=progress(job);
        match &job.outcome {
            None=>{value["status"]=json!("running");value["elapsed_seconds"]=json!(elapsed(job));Ok((false,value))},
            Some(Ok(result))=>{value["status"]=json!("completed");value["result"]=result.clone();Ok((true,value))},
            Some(Err(error))=>{value["status"]=json!("failed");value["error"]=json!(error.message);value["http_status"]=json!(error.http_status);if let Some(code)=&error.code{value["code"]=json!(code);}Ok((true,value))},
        }
    }
    pub fn preview(&self,id:&str,after:u64)->Result<Value> {
        validate_id(id)?;let mut registry=self.lock()?;Self::prune(&mut registry);let job=registry.jobs.get(id).ok_or_else(||ApiError(axum::http::StatusCode::NOT_FOUND,"BACKTEST_NOT_FOUND","backtest job not found".into()))?;
        Ok(json!({"job":id,"revision":job.preview_revision,"preview":if job.preview_revision>after&&job.outcome.is_none(){job.preview.as_ref()}else{None}}))
    }
    pub fn snapshot(&self)->Result<Value> {
        let mut registry=self.lock()?;Self::prune(&mut registry);let mut jobs=Vec::new();
        for (id,job) in &registry.jobs {if job.outcome.is_some(){continue;}let mut item=job.details.clone();if let Some(fields)=progress(job).as_object(){for (key,value) in fields{item[key]=value.clone();}}item["job"]=json!(id);item["status"]=json!("running");item["elapsed_seconds"]=json!(elapsed(job));jobs.push(item);}
        let mut history=registry.history.values().collect::<Vec<_>>();history.sort_by(|a,b|b.0.total_cmp(&a.0));
        let recent=history.into_iter().map(|(_,value)|{let mut item=value.clone();item["result_available"]=json!(item["job"].as_str().is_some_and(|id|registry.jobs.contains_key(id)));item}).collect::<Vec<_>>();
        Ok(json!({"active":jobs.len(),"max_active":MAX_ACTIVE,"jobs":jobs,"recent":recent,"watchlist_refresh":null}))
    }
}
impl JobReporter {
    pub fn data_started(&self)->Result<()> {let mut registry=self.jobs.lock()?;if let Some(job)=registry.jobs.get_mut(&self.id){job.data_started=Some(Instant::now());}Ok(())}
    pub fn data_finished(&self)->Result<f64> {let mut registry=self.jobs.lock()?;let job=registry.jobs.get_mut(&self.id).ok_or_else(ApiError::internal)?;if let Some(at)=job.data_started.take(){job.data_seconds+=at.elapsed().as_secs_f64();}job.timing=format!(" · 数据请求耗时 {:.3} 秒",job.data_seconds);Ok(job.data_seconds)}
    pub fn analysis_timing(&self,seconds:f64)->Result<()> {if !seconds.is_finite()||seconds<0.0{return Err(ApiError::invalid("invalid analysis duration"));}let mut registry=self.jobs.lock()?;if let Some(job)=registry.jobs.get_mut(&self.id){job.analysis_seconds=seconds;}Ok(())}

    pub fn cancellation_flag(&self)->Arc<AtomicBool>{self.cancel.clone()}
    pub fn cancelled(&self)->bool{self.cancel.load(Ordering::Acquire)}
    pub fn check_cancelled(&self)->Result<()> {if self.cancelled(){Err(ApiError::unavailable("回测任务已取消"))}else{Ok(())}}
    pub fn progress(&self,percent:u8,stage:&str)->Result<()> {
        self.check_cancelled()?;
        if percent>99||stage.is_empty(){return Err(ApiError::invalid("invalid backtest progress"));}
        let mut registry=self.jobs.lock()?;
        if let Some(job)=registry.jobs.get_mut(&self.id){if job.outcome.is_none()&&percent>=job.percent{job.percent=percent;if let Some((description,timing))=stage.split_once(" · 累计耗时："){job.timing=format!(" · 累计耗时：{timing}");job.stage=format!("{description}{}",job.timing);}else{job.stage=format!("{stage}{}",job.timing);}}}Ok(())
    }
    pub fn pipeline(&self,value:Value)->Result<()> {self.check_cancelled()?;if !value.is_object(){return Err(ApiError::invalid("invalid pipeline"));}let mut registry=self.jobs.lock()?;if let Some(job)=registry.jobs.get_mut(&self.id){if job.outcome.is_none(){job.pipeline=Some(value);}}Ok(())}
    pub fn preview(&self,revision:u64,preview:Value)->Result<()> {
        self.check_cancelled()?;
        let processed=preview["processed"].as_u64().ok_or_else(||ApiError::invalid("preview processed required"))?;
        let total=preview["total"].as_u64().ok_or_else(||ApiError::invalid("preview total required"))?;
        let asof=preview["asof"].as_str().ok_or_else(||ApiError::invalid("preview asof required"))?;
        super::market::parse_day(asof)?;
        if processed==0||processed>total||processed!=revision {return Err(ApiError::invalid("invalid preview revision"));}
        for (key,time_key) in [("orders","timestamp"),("signals","timestamp"),("audit","timestamp"),("trades","exit_time")] {
            let records=preview[key].as_array().ok_or_else(||ApiError::invalid("preview requires account records"))?;
            if records.iter().any(|record|record[time_key].as_str().and_then(|time|time.get(..10)).is_none_or(|date|date>asof)){return Err(ApiError::invalid("preview cannot contain future decisions"));}
        }
        let curve=preview["curve"].as_array().ok_or_else(||ApiError::invalid("preview requires an equity curve"))?;
        if curve.len() as u64!=processed||curve.last().and_then(|row|row["timestamp"].as_str()).and_then(|time|time.get(..10))!=Some(asof){return Err(ApiError::invalid("preview account must finish the same market day"));}
        let mut registry=self.jobs.lock()?;
        if let Some(job)=registry.jobs.get_mut(&self.id){if job.outcome.is_none()&&revision>job.preview_revision{job.preview_revision=revision;let flow=if wavequant_quant_rs::analysis::analyzer::STRUCTURE_ONLY{"折线 → 一级趋势计算"}else{"同日联动：折线 → 一级 → 二级 → 三级 → B/S及账户"};job.stage=format!("{flow} · 已处理{processed}/{total}个交易日 · 截至{asof}{}",job.timing);job.preview=Some(preview);}}Ok(())
    }
}
