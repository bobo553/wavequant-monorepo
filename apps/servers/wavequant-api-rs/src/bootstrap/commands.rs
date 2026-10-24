//! 保留原 API 管理命令边界：发布只由显式 CLI/Worker 驱动，HTTP 只读快照。
use std::{collections::{BTreeMap,BTreeSet},sync::{Arc,atomic::{AtomicBool,Ordering}},io::Write};
use wavequant_quant_rs::indexed_json::{Value,json};
use sha2::{Digest,Sha256};
use crate::{presentation::error::ApiError,storage::snapshots::ResearchRun,application::{research::ResearchRepository,infrastructure::Infrastructure,backtest::BacktestService,jobs::BacktestJobs,signal_snapshots::SignalSnapshots,market::{OnlineMarket,parse_day},market_timeframes::MarketTimeframes}};
use super::{Config,AdminAction,WorkerOptions,shutdown_signal};
use wavequant_quant_rs::analysis::profiles::ProfileOptions;
type Result<T>=std::result::Result<T,ApiError>;
struct Context {config:Config,repository:Arc<ResearchRepository>,infrastructure:Arc<Infrastructure>,backtests:Arc<BacktestService>,signals:SignalSnapshots,timeframes:MarketTimeframes}
fn check(cancel:&AtomicBool)->Result<()>{if cancel.load(Ordering::Acquire){Err(ApiError::unavailable("worker cancelled"))}else{Ok(())}}
fn text<'a>(value:&'a Value,field:&str)->Result<&'a str>{value[field].as_str().ok_or_else(||ApiError::invalid(format!("worker {field} missing")))}
fn scopes(catalog:&Value)->Result<Vec<String>>{let rows=catalog["stocks"].as_array().ok_or_else(||ApiError::unavailable("行情目录未返回股票列表"))?;let mut result=Vec::new();let mut seen=BTreeSet::new();for row in rows{if let Some(symbol)=row["symbol"].as_str(){if seen.insert(symbol.to_owned()){result.push(symbol.to_owned());}}}if result.is_empty(){return Err(ApiError::unavailable("行情目录没有可预计算股票"));}Ok(result)}
fn shard(symbol:&str,count:u8)->u8{let digest=Sha256::digest(symbol.as_bytes());let mut bytes=[0;8];bytes.copy_from_slice(&digest[..8]);(u64::from_be_bytes(bytes)%u64::from(count))as u8}
fn priority(symbol:&str)->u8{if symbol.starts_with("sh.68"){3}else if symbol.starts_with("sh."){0}else if symbol.starts_with("sz.30"){2}else if symbol.starts_with("sz."){1}else if symbol.starts_with("bj."){4}else{5}}
fn unique(values:&[String])->Vec<String>{let mut seen=BTreeSet::new();values.iter().filter(|v|seen.insert((*v).clone())).cloned().collect()}
impl Context {
    fn new(config:Config)->Result<Self>{
        let root=config.root.clone().ok_or_else(||ApiError::invalid("管理命令需要 --root 或 WAVEQUANT_RESULTS_ROOT"))?;
        let repository=Arc::new(ResearchRepository::new(root));let infrastructure=Arc::new(Infrastructure::from_env()?);
        if infrastructure.database.is_none(){return Err(ApiError::unavailable("管理命令需要配置 WAVEQUANT_DATABASE_URL"));}
        let engine=crate::application::research::digest(&std::fs::read(std::env::current_exe()?)?);
        let jobs=BacktestJobs::new(None,engine.clone())?;
        let computation=Arc::new(crate::application::computation::Computation::new(&config)?);
        let backtests=Arc::new(BacktestService{market_cache:Arc::new(crate::application::adjusted_market_cache::AdjustedMarketCache::from_env(infrastructure.cache.clone())?),repository:repository.clone(),tdx_root:config.tdx_root.clone(),online_enabled:config.online_enabled,timeout:config.upstream_timeout,engine_version:engine.clone(),computation,diagnostics_root:config.root.as_ref().map(|root|root.join(".calculation-steps")),record_details:config.record_backtest_diagnostics});
        let signals=SignalSnapshots::new(repository.clone(),infrastructure.clone(),backtests.clone(),jobs,engine.clone());
        let timeframes=MarketTimeframes::new(config.tdx_root.clone(),Arc::new(OnlineMarket::new(config.upstream_timeout)),config.online_enabled,infrastructure.clone(),engine);
        Ok(Self{config,repository,infrastructure,backtests,signals,timeframes})
    }
    fn run_id(&self)->Result<String>{if let Some(run)=&self.config.worker.run{return Ok(run.clone());}let catalog=self.repository.catalog()?;catalog["runs"].as_array().and_then(|rows|rows.first()).and_then(|row|row["id"].as_str()).map(str::to_owned).ok_or_else(||ApiError::invalid("结构预计算需要至少一个有效封存运行，或显式传入 --structure-run"))}
    fn params(&self,source:&str,options:ProfileOptions)->Result<BTreeMap<String,String>>{let worker=&self.config.worker;let mut params=BTreeMap::from([("run".into(),self.run_id()?),("variant".into(),worker.variant.clone()),("source".into(),source.into()),("n_target_trend_confirmation_enabled".into(),options.n_target_trend_confirmation_enabled.to_string()),("ordinary_trend_completion_enabled".into(),options.ordinary_trend_completion_enabled.to_string())]);if let Some(asof)=&worker.asof{parse_day(asof)?;params.insert("asof".into(),asof.clone());}Ok(params)}
    fn once(&self,options:ProfileOptions,cancel:&Arc<AtomicBool>)->Result<Value>{check(cancel)?;match self.config.action.ok_or_else(ApiError::internal)?{
        AdminAction::IndexRuns=>{let catalog=self.repository.catalog()?;let mut count=0;for row in catalog["runs"].as_array().ok_or_else(ApiError::internal)?{check(cancel)?;let id=text(row,"id")?.to_owned();let mut payload=row.clone();payload.as_object_mut().ok_or_else(ApiError::internal)?.remove("id");self.infrastructure.database.as_ref().ok_or_else(ApiError::internal)?.save(ResearchRun{run_id:id,status:"COMPLETED".into(),strategy:None,payload,created_at:None,updated_at:None})?;count+=1;}Ok(json!({"indexed":count}))},
        AdminAction::RefreshStructures|AdminAction::WatchStructures=>self.signals.refresh_with_cancel("structure",&self.params("tdx",options)?,cancel.clone()),
        AdminAction::RefreshSignals|AdminAction::WatchSignals=>self.refresh_signals(options,cancel),
        AdminAction::RefreshTimeframes|AdminAction::WatchTimeframes=>self.refresh_timeframes(options,cancel),
    }}
    fn refresh_signals(&self,options:ProfileOptions,cancel:&Arc<AtomicBool>)->Result<Value>{
        let worker=&self.config.worker;let source=&worker.source;let mut params=self.params(source,options)?;let mut market_total=None;
        if source=="snapshot"&&worker.family!="buy"{return Err(ApiError::invalid("结构快照 Worker 仅支持 tdx 或 akshare"));}
        let selected=if source=="akshare" {
            let catalog=self.backtests.universe(source)?;if !params.contains_key("asof"){params.insert("asof".into(),text(&catalog,"latest")?.into());}
            if !worker.symbols.is_empty(){unique(&worker.symbols)}else if worker.family=="structure"{let all=scopes(&catalog)?;market_total=Some(all.len());all}else{return Err(ApiError::invalid("AkShare 买点预计算必须至少传一个 --signal-symbol"));}
        }else{if !worker.symbols.is_empty(){return Err(ApiError::invalid("--signal-symbol 只用于 AkShare"));}vec![String::new()]};
        let mut selected=selected.into_iter().filter(|symbol|source!="akshare"||!worker.symbols.is_empty()||shard(symbol,worker.shard_count)==worker.shard_index).collect::<Vec<_>>();
        if source=="akshare"&&worker.family=="structure"&&worker.symbols.is_empty(){
            let algorithm=self.signals.structure_algorithm_version(options.n_target_trend_confirmation_enabled,options.ordinary_trend_completion_enabled)?;
            let published=self.infrastructure.database.as_ref().ok_or_else(ApiError::internal)?.list_structure_snapshots(params["run"].as_str(),None,source,params["asof"].as_str(),&algorithm,20000)?;
            let published=published.into_iter().filter(|snapshot|snapshot.payload["complete"]!=false&&snapshot.failed==0).filter_map(|snapshot|snapshot.scope_symbol).collect::<BTreeSet<_>>();selected.retain(|symbol|!published.contains(symbol));selected.sort_by_key(|symbol|priority(symbol));
        }
        let mut results=Vec::new();
        for symbol in selected{check(cancel)?;let mut current=params.clone();if !symbol.is_empty(){current.insert("symbol".into(),symbol.clone());}
            for family in ["structure","buy"] {if worker.family!="all"&&worker.family!=family{continue;}check(cancel)?;let mut request=current.clone();
                if family=="buy"{request.insert("scenario".into(),worker.scenario.clone());request.insert("start".into(),worker.start.clone());}else if let Some(total)=market_total{request.insert("market_total".into(),total.to_string());}
                let result=if family=="buy"{self.signals.refresh_with_cancel("buy",&request,cancel.clone())}else{self.signals.refresh_with_cancel("structure",&request,cancel.clone())};
                match result{Ok(value)=>results.push(value),Err(error)=>{check(cancel)?;if source!="akshare"{return Err(error);}results.push(json!({"status":"failed","family":family,"source":source,"symbol":symbol,"error":error.2}));}}
            }
        }Ok(json!(results))
    }
    fn refresh_timeframes(&self,options:ProfileOptions,cancel:&Arc<AtomicBool>)->Result<Value>{
        let worker=&self.config.worker;let source=&worker.timeframe_source;let catalog=self.backtests.universe(source)?;
        let asof=worker.timeframe_asof.as_deref().map(Ok).unwrap_or_else(||text(&catalog,"latest"))?;parse_day(asof)?;
        let selected=if worker.timeframe_symbols.is_empty(){scopes(&catalog)?}else{unique(&worker.timeframe_symbols)};
        let selected=selected.into_iter().filter(|symbol|shard(symbol,worker.timeframe_shard_count)==worker.timeframe_shard_index).collect::<Vec<_>>();
        let mut published=0;let mut unchanged=0;let mut failed=Vec::new();let mut incomplete=0;
        let timeframes=["1d","1w","1mo","3mo","1y"].map(str::to_owned);
        for symbol in &selected{check(cancel)?;match self.timeframes.precompute(source,symbol,asof,&timeframes,options,Some(cancel)){
            Ok(value)=>{published+=value["published"].as_array().map_or(0,Vec::len);unchanged+=value["unchanged"].as_array().map_or(0,Vec::len);incomplete+=value["incomplete"].as_array().map_or(0,Vec::len);},
            Err(error)=>{check(cancel)?;failed.push(json!({"symbol":symbol,"error":error.2}));}
        }}
        Ok(json!({"source":source,"requested_asof":asof,"symbols":selected.len(),"published_periods":published,"unchanged_periods":unchanged,"incomplete_periods":incomplete,"failed_count":failed.len(),"failed":failed.into_iter().take(20).collect::<Vec<_>>(),"shard":{"count":worker.timeframe_shard_count,"index":worker.timeframe_shard_index}}))
    }
}
fn modes(options:&WorkerOptions)->Vec<ProfileOptions>{let n=options.n_target.map_or(vec![false,true],|v|vec![v]);let ordinary=options.ordinary.map_or(vec![false,true],|v|vec![v]);n.into_iter().flat_map(|n|ordinary.iter().map(move|ordinary|ProfileOptions{n_target_trend_confirmation_enabled:n,ordinary_trend_completion_enabled:*ordinary,..Default::default()})).collect()}
fn io_error(error:ApiError)->std::io::Error{std::io::Error::other(error.2)}
pub async fn run(config:Config)->std::result::Result<(),Box<dyn std::error::Error+Send+Sync>>{
    let action=config.action.ok_or_else(||std::io::Error::other("administration action required"))?;let interval=config.worker.interval;
    let options=if action==AdminAction::IndexRuns{vec![ProfileOptions::default()]}else{modes(&config.worker)};
    let context=Arc::new(tokio::task::spawn_blocking(move||Context::new(config)).await?.map_err(io_error)?);
    let cancel=Arc::new(AtomicBool::new(false));
    loop{
        for option in &options{let context=context.clone();let cancellation=cancel.clone();let option=*option;
            let mut work=tokio::task::spawn_blocking(move||context.once(option,&cancellation));
            let outcome=tokio::select! {outcome=&mut work=>outcome?,()=shutdown_signal()=>{cancel.store(true,Ordering::Release);let _=tokio::time::timeout(std::time::Duration::from_secs(35),work).await;return Ok(());}};
            match outcome{Ok(value)=>{println!("{}",wavequant_quant_rs::indexed_json::to_string(&value)?);std::io::stdout().flush()?;},Err(error)if action.watching()=>tracing::error!(code=error.1,message=%error.2,"snapshot worker failed; prior publication retained"),Err(error)=>return Err(io_error(error).into())}
        }
        if !action.watching(){return Ok(());}
        tokio::select!{()=tokio::time::sleep(std::time::Duration::from_secs(interval))=>{},()=shutdown_signal()=>{cancel.store(true,Ordering::Release);return Ok(());}}
    }
}
