//! 自选刷新与前台任务共享容量/同股互斥；只有实际完成且完整的单股结果才持久化成功。
use std::{collections::{BTreeMap,BTreeSet},sync::{Arc,Mutex,atomic::{AtomicBool,Ordering}},time::{Duration,Instant,SystemTime,UNIX_EPOCH}};
use wavequant_quant_rs::indexed_json::{Value,json};
use tokio::sync::Notify;
use crate::{presentation::error::ApiError,storage::watchlists::WatchlistStore};
use super::{jobs::BacktestJobs,backtest_request::BacktestRequest,research::digest};
type Result<T>=std::result::Result<T,ApiError>;
type Version=Arc<dyn Fn(&str,&str)->Result<String>+Send+Sync>;
type Catalog=Arc<dyn Fn(&str)->Result<Value>+Send+Sync>;
type Submit=Arc<dyn Fn(BacktestRequest)->Result<String>+Send+Sync>;
struct Active {key:String,id:String,attempts:u32,details:Value}
struct RefreshState {active:Option<Active>,status:Value,catalog_at:Option<Instant>,catalog_source:String,stocks:BTreeMap<String,Value>,desired:Option<BTreeSet<String>>}
pub struct WatchlistRefresh {store:Arc<WatchlistStore>,jobs:BacktestJobs,version:Version,catalog:Catalog,submit:Submit,inner:Mutex<RefreshState>,stopped:AtomicBool,wake:Notify,started:AtomicBool}
fn text<'a>(value:&'a Value,key:&str)->Result<&'a str>{value[key].as_str().ok_or_else(||ApiError::invalid(format!("watchlist {key} missing")))}
fn epoch()->f64{SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_secs_f64()}
impl WatchlistRefresh {
    pub fn new(store:Arc<WatchlistStore>,jobs:BacktestJobs,version:Version,catalog:Catalog,submit:Submit)->Self {
        Self{store,jobs,version,catalog,submit,inner:Mutex::new(RefreshState{active:None,status:json!({"status":"waiting","error":""}),catalog_at:None,catalog_source:String::new(),stocks:BTreeMap::new(),desired:None}),stopped:AtomicBool::new(false),wake:Notify::new(),started:AtomicBool::new(false)}
    }
    pub fn start(self:&Arc<Self>) {if self.started.swap(true,Ordering::AcqRel){return;}let service=self.clone();tokio::spawn(async move{
        while !service.stopped.load(Ordering::Acquire){
            let worker=service.clone();let outcome=tokio::task::spawn_blocking(move||worker.tick()).await;
            if !matches!(outcome,Ok(Ok(()))){tracing::error!("saved watchlist refresh failed");if let Ok(mut state)=service.inner.lock(){state.status=json!({"status":"error","error":"自动刷新暂不可用，稍后重试"});}}
            tokio::select!{_=service.wake.notified()=>{},_=tokio::time::sleep(Duration::from_secs(5))=>{}}
        }
    });}
    pub fn notify(&self){self.wake.notify_one();}
    pub fn close(&self){self.stopped.store(true,Ordering::Release);self.wake.notify_one();}
    pub fn state(&self)->Result<Value>{Ok(self.inner.lock().map_err(|_|ApiError::internal())?.status.clone())}
    fn tick(&self)->Result<()> {
        if self.stopped.load(Ordering::Acquire){return Ok(());}
        let mut state=self.inner.lock().map_err(|_|ApiError::internal())?;let now=epoch();
        if let Some(active)=&state.active {
            let(done,outcome)=self.jobs.outcome(&active.id)?;if !done{return Ok(());}
            let result=&outcome["result"];let expected=&active.details["params"];
            let completed=outcome["status"]=="completed"&&result["complete"]!=false&&result["backtest"].is_object()
                &&result["backtest"]["status"]!="data_unavailable"&&result["result_scope"]=="stock"
                &&result["symbol"]==expected["symbol"]&&result["asof"]==expected["asof"];
            let mut summary=active.details.clone();summary["status"]=json!(if completed{"completed"}else{"failed"});summary["result_valid"]=json!(completed);summary["persisted_current"]=json!(true);summary["result_available"]=json!(false);
            for key in ["total_return","total_pnl","max_drawdown","holding_max_drawdown","holding_current_max_drawdown"]{if result["metrics"][key].as_f64().is_some_and(f64::is_finite){summary[key]=result["metrics"][key].clone();}}
            for key in ["holding_drawdown_version","holding_drawdown_status","holding_current_drawdown_status"]{if result["metrics"][key].is_string(){summary[key]=result["metrics"][key].clone();}}
            if let Some(orders)=result["orders"].as_array(){summary["fill_count"]=json!(orders.iter().filter(|order|order["status"]=="filled").count());}
            let delay=[5,15,45][(active.attempts as usize).min(2)];
            self.store.finish(&active.key,active.attempts+1,now+f64::from(delay),completed,Some(&summary))?;state.active=None;
        }
        let document=self.store.load()?;let configured=&document["settings"];
        if configured.is_null(){state.status=json!({"status":"waiting","error":"","total":0,"completed":0,"failed":0});return Ok(());}
        if configured["enabled"]==false{state.status["status"]=json!("paused");return Ok(());}
        let context=&configured["context"];let run=text(context,"run")?;let variant=text(context,"variant")?;let source=text(context,"source")?;
        let version=(self.version)(run,variant)?;
        if state.catalog_source!=source||state.catalog_at.is_none_or(|at|at.elapsed()>=Duration::from_secs(30))||state.stocks.is_empty(){
            let catalog=(self.catalog)(source)?;let stocks=catalog["stocks"].as_array().ok_or_else(||ApiError::unavailable("market catalog unavailable"))?;
            state.stocks=stocks.iter().filter_map(|stock|stock["symbol"].as_str().map(|symbol|(symbol.to_owned(),stock.clone()))).collect();state.catalog_source=source.into();state.catalog_at=Some(Instant::now());
        }
        let mut candidates=Vec::new();let mut seen=BTreeSet::new();
        let members=document["snapshot"]["memberships"].as_array().ok_or_else(||ApiError::invalid("watchlist memberships missing"))?;
        for member in members {
            let symbol=text(member,"symbol")?;if !seen.insert(symbol.to_owned()){continue;}
            let Some(stock)=state.stocks.get(symbol).filter(|stock|stock["has_data"]!=false)else{continue};
            let Some(asof)=stock["last"].as_str().filter(|asof|*asof>=context["start"].as_str().unwrap_or(""))else{continue};
            let mut params=BTreeMap::new();for(key,value)in context.as_object().ok_or_else(||ApiError::invalid("watchlist context missing"))? {if key!="source"{params.insert(key.clone(),value.as_str().ok_or_else(||ApiError::invalid("watchlist context value must be a string"))?.to_owned());}}
            params.insert("symbol".into(),symbol.into());params.insert("asof".into(),asof.into());
            let mut request=BacktestRequest::parse(source,&params)?;request.strategy_version=Some(version.clone());
            let key=digest(&wavequant_quant_rs::indexed_json::to_vec(&json!(["rust-native-watchlist",version,source,request.params()]))?);
            candidates.push((key,request));
        }
        let desired=candidates.iter().map(|(key,_)|key.clone()).collect::<BTreeSet<_>>();if state.desired.as_ref()!=Some(&desired){self.store.prune_runs(&desired)?;state.desired=Some(desired);}
        let mut known=self.store.run_states()?;let snapshot=self.jobs.snapshot()?;
        let mut current_records=BTreeMap::new();
        if let Some(recent)=snapshot["recent"].as_array(){for item in recent{if item["version"]==version&&item["path"]==format!("/api/{source}-backtest")&&item["status"]=="completed"&&item["result_valid"]==true{current_records.insert(wavequant_quant_rs::indexed_json::to_string(&item["params"])?,item.clone());}}}
        for(key,request)in &candidates {
            if known.get(key).is_some_and(|state|state.2){continue;}
            if let Some(item)=current_records.get(&wavequant_quant_rs::indexed_json::to_string(&request.params())?).filter(|item|item["symbol"]==request.symbol){
                let mut summary=item.clone();summary["persisted_current"]=json!(true);summary["result_available"]=json!(false);self.store.finish(key,1,0.0,true,Some(&summary))?;known.insert(key.clone(),(1,0.0,true));
            }
        }
        let completed=candidates.iter().filter(|(key,_)|known.get(key).is_some_and(|state|state.2)).count();
        let failed=candidates.iter().filter(|(key,_)|known.get(key).is_some_and(|state|!state.2&&state.0>=3)).count();
        state.status=json!({"status":"idle","error":"","version":version,"source":source,"total":candidates.len(),"completed":completed,"failed":failed});
        if snapshot["active"].as_u64().unwrap_or(0)>0{state.status["status"]=json!("waiting");return Ok(());}
        for(key,request)in candidates {
            if self.stopped.load(Ordering::Acquire){return Ok(());}
            let(attempts,retry_at,done)=known.get(&key).copied().unwrap_or((0,0.0,false));if done||attempts>=3||now<retry_at{continue;}
            let(_,details)=request.bind_version(&version)?;let symbol=request.symbol.clone();
            let id=match(self.submit)(request){Ok(id)=>id,Err(error)if matches!(error.1,"BACKTEST_CAPACITY"|"BACKTEST_SYMBOL_RUNNING")=>return Ok(()),Err(error)=>return Err(error)};
            state.active=Some(Active{key,id,attempts,details});state.status["status"]=json!("running");state.status["symbol"]=json!(symbol);return Ok(());
        }Ok(())
    }
}
