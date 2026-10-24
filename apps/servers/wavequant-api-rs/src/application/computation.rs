//! 可选有界子进程只改变执行位置；输入、进度和结果均校验来源身份。
use std::{path::PathBuf,sync::{Arc,atomic::{AtomicBool,Ordering}}};
use wavequant_quant_rs::indexed_json::{Value,json};
use serde::Deserialize;
#[derive(Deserialize)]
struct OwnedStep{symbol:String,index:usize,bar:wavequant_quant_rs::analysis::trading::RuleCandle,frames:Vec<wavequant_quant_rs::analysis::kernel::KernelFrame>,trading:wavequant_quant_rs::analysis::trading::day::TradingDayEvaluation}
use wavequant_quant_rs::{analysis::{analyzer::StructureCache,system::{run_system_backtest_with_cache,SystemBacktest,SystemConfig,SystemProgress,SystemStep}},execution::BacktestSession,persistence::StructurePrefixCache,infrastructure::computation::{WorkerConfig,WorkerPool,WorkerRequest,is_capacity_rejection}};
use crate::{bootstrap::Config,presentation::error::ApiError};
type Result<T>=std::result::Result<T,ApiError>;
pub struct Computation {pool:Option<WorkerPool>,structure_cache_root:Option<PathBuf>,structure_cache:Option<Arc<dyn StructureCache>>}
impl Computation {
    pub fn new(config:&Config)->Result<Self>{
        let pool=if let Some(path)=&config.quant_worker {
            if !path.is_absolute(){return Err(ApiError::invalid("--quant-worker requires an absolute executable path"));}
            let options=WorkerConfig::new(path,config.quant_workers).map_err(ApiError::invalid)?;
            Some(WorkerPool::new(options).map_err(ApiError::unavailable)?)
        }else{None};
        let (structure_cache_root,structure_cache)=if let Some(root)=&config.root {
            let absolute=if root.is_absolute(){root.clone()}else{std::env::current_dir().map_err(ApiError::from)?.join(root)};
            let path=absolute.join(".rust-structure-cache");
            match StructurePrefixCache::new(&path){
                Ok(cache)=>{let cache:Arc<dyn StructureCache>=Arc::new(cache);(Some(path),Some(cache))},
                Err(error)=>{tracing::warn!(error=%error,"structure cache unavailable; using canonical calculation");(None,None)},
            }
        }else{(None,None)};
        Ok(Self{pool,structure_cache_root,structure_cache})
    }
    pub fn statistics(&self)->Value{self.pool.as_ref().map_or(json!({"mode":"in_process","workers":0}),WorkerPool::statistics)}
    pub fn system(&self,sessions:&[BacktestSession],config:SystemConfig,cancel:Arc<AtomicBool>,mut progress:impl FnMut(&SystemProgress),mut step:impl FnMut(&SystemStep<'_>),record_steps:bool)->Result<(SystemBacktest,Value)>{
        let mut fallback=None;
        if let Some(pool)=&self.pool{
            let trace_submit_started=std::time::Instant::now();
            let request=if record_steps{WorkerRequest::SystemWithSteps{sessions:sessions.to_vec(),config:config.clone(),structure_cache_root:self.structure_cache_root.clone()}}else{WorkerRequest::System{sessions:sessions.to_vec(),config:config.clone(),structure_cache_root:self.structure_cache_root.clone()}};
            match pool.submit(request) {
                Ok(ticket)=>{
                    let submit_ms=trace_submit_started.elapsed().as_secs_f64()*1000.0;let trace_reply_started=std::time::Instant::now();
                    let progress_error=std::cell::RefCell::new(None::<String>);
                    let outcome=ticket.result_with_observations(Some(cancel.as_ref()),|value|{
                        if progress_error.borrow().is_some(){return;}
                        match wavequant_quant_rs::indexed_json::from_value::<SystemProgress>(value){Ok(value)=>progress(&value),Err(error)=>{*progress_error.borrow_mut()=Some(error.to_string());cancel.store(true,Ordering::Release);}}
                    },|value|{
                        if progress_error.borrow().is_some(){return;}
                        match wavequant_quant_rs::indexed_json::from_value::<OwnedStep>(value){Ok(value)=>step(&SystemStep{symbol:&value.symbol,index:value.index,bar:&value.bar,frames:&value.frames,trading:&value.trading}),Err(error)=>{*progress_error.borrow_mut()=Some(error.to_string());cancel.store(true,Ordering::Release);}}
                    });
                    if let Some(error)=progress_error.into_inner(){return Err(ApiError::invalid(format!("worker observation schema invalid: {error}")));}
                    let outcome=outcome.map_err(ApiError::unavailable)?;
                    let wait_reply_ms=trace_reply_started.elapsed().as_secs_f64()*1000.0;let trace_decode_started=std::time::Instant::now();
                    let result=wavequant_quant_rs::indexed_json::from_value(outcome.value).map_err(|_|ApiError::unavailable("worker result schema invalid"))?;
                    tracing::info!(submit_ms,wait_reply_ms,decode_ms=trace_decode_started.elapsed().as_secs_f64()*1000.0,worker_reported_ms=outcome.elapsed_seconds*1000.0,worker_pid=outcome.pid,peak_rss_bytes=?outcome.peak_rss_bytes,"computation_boundary_timing");
                    return Ok((result,json!({"mode":"bounded_subprocess","structure_cache_enabled":outcome.structure_cache.is_some(),"structure_cache":outcome.structure_cache,"timeframe":outcome.timeframe,"request_sha256":outcome.request_sha256,"engine_sha256":outcome.engine_sha256,"pid":outcome.pid,"peak_rss_bytes":outcome.peak_rss_bytes,"elapsed_seconds":outcome.elapsed_seconds,"skipped_progress":outcome.skipped_progress})));
                },
                Err(error) if is_capacity_rejection(&error)=>fallback=Some(error),
                Err(error)=>return Err(ApiError::unavailable(error)),
            }
        }
        let result=run_system_backtest_with_cache(sessions,config,Some(cancel),&mut progress,&mut step,self.structure_cache.clone()).map_err(ApiError::invalid)?;
        let cache_counts=result.analysis.values().fold((0usize,0usize),|(hits,misses),value|(hits.saturating_add(value.structure_cache.hits),misses.saturating_add(value.structure_cache.misses)));
        Ok((result,json!({"mode":"in_process","structure_cache_enabled":self.structure_cache.is_some(),"structure_cache":{"hits":cache_counts.0,"misses":cache_counts.1,"account_state_reused":false},"timeframe":"1d","fallback":fallback,"record_steps":record_steps})))
    }
}
