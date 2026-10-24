//! HTTP 回测 producer：同源日线/复权/分钟进入统一逐日策略和真实账户，随后构造独立个股结果。
use std::{collections::BTreeMap,path::PathBuf,sync::{Arc,atomic::{AtomicBool,Ordering}},time::Duration};
use rust_decimal::Decimal;
use wavequant_quant_rs::indexed_json::{Value,json};
use wavequant_quant_rs::{analysis::{analyzer::AnalyzerConfig,profiles::{execution_configuration,execution_parameters},system::SystemConfig},
    data::{tdx::{TdxReader,DataError,fingerprint},sina::apply_factors,akshare::AkShareProvider,adjustments::{ExecutionBar,security_spec},minute::{TdxMinuteSource,MinuteBar,MinuteError,verify_minute_day}},analytics::research::stock_view,execution::BacktestSession};
use crate::{presentation::error::ApiError,application::{research::{ResearchRepository,digest},backtest_request::BacktestRequest,jobs::{BacktestJobs,JobReporter,JobFailure}}};
type Result<T>=std::result::Result<T,ApiError>;
#[derive(Clone)]
pub struct BacktestService {pub market_cache:Arc<super::adjusted_market_cache::AdjustedMarketCache>,pub repository:Arc<ResearchRepository>,pub tdx_root:Option<PathBuf>,pub online_enabled:bool,pub timeout:Duration,pub engine_version:String,pub computation:Arc<super::computation::Computation>,pub diagnostics_root:Option<PathBuf>,pub record_details:bool}
#[derive(serde::Serialize,serde::Deserialize)]
pub struct LoadedMarket {pub bars:Vec<ExecutionBar>,pub minutes:BTreeMap<String,Vec<MinuteBar>>,pub version:String,pub provenance:Value,pub files:Vec<(PathBuf,String)>,pub missing:Vec<String>}
#[derive(Clone,serde::Serialize,serde::Deserialize)]
pub struct MarketRequest {pub source:String,pub symbol:String,pub start:String,pub asof:String}
fn check_cancel(cancel:Option<&Arc<AtomicBool>>)->Result<()>{if cancel.is_some_and(|value|value.load(Ordering::Acquire)){Err(ApiError::unavailable("market request cancelled"))}else{Ok(())}}
fn data_error(error:DataError)->ApiError {match error{DataError::Unavailable(_)=>ApiError::unavailable(error.to_string()),DataError::Changed=>ApiError(axum::http::StatusCode::CONFLICT,"MARKET_DATA_VERSION_CHANGED",error.to_string()),_=>ApiError::invalid(error.to_string())}}
fn minute_error(error:MinuteError)->ApiError {match error{MinuteError::Coverage{..}=>ApiError::unavailable(error.to_string()),_=>ApiError::invalid(error.to_string())}}
fn failed(error:ApiError)->JobFailure {JobFailure{http_status:error.0.as_u16(),code:Some(error.1.into()),message:error.2}}
fn effective_start(raw:&[wavequant_quant_rs::data::tdx::DailyBar],requested:&str,asof:&str)->Result<String>{
    if raw.len()<22{return Err(ApiError::invalid("历史日线不足：须先跳过至少 20 个已有交易日"));}
    let start=requested.max(raw[20].date.as_str()).to_owned();if start.as_str()>asof{return Err(ApiError::invalid("所选区间没有可回测日线"));}Ok(start)
}
fn gcd(mut a:usize,mut b:usize)->usize{while b!=0{let remainder=a%b;a=b;b=remainder;}a}
impl BacktestService {
    pub fn start_market_refresh(self:&Arc<Self>){
        let weak=Arc::downgrade(self);self.market_cache.start_refresh(self.timeout,Arc::new(move|request|weak.upgrade().ok_or_else(ApiError::internal)?.load_market_uncached(request,false,false,None)));
    }
    pub fn universe(&self,source:&str)->Result<Value>{match source {
        "tdx"=>{let root=self.tdx_root.as_ref().ok_or_else(||ApiError::unavailable("通达信目录未配置"))?;TdxReader::new(root).and_then(|reader|reader.catalog()).map_err(data_error)},
        "akshare" if self.online_enabled=>AkShareProvider::new(self.timeout).and_then(|provider|provider.catalog(chrono::Utc::now())).map_err(data_error),
        _=>Err(ApiError::unavailable("market provider unavailable")),
    }}
    pub fn submit(&self,request:BacktestRequest,jobs:&BacktestJobs)->Result<String> {
        let version=self.repository.backtest_version(&request.run,&request.variant,&self.engine_version)?;
        let version=version["version"].as_str().ok_or_else(ApiError::internal)?;
        let(signature,details)=request.bind_version(version)?;
        let service=self.clone();let id=request.job_id.clone();
        jobs.start(id.clone(),signature,request.symbol.clone(),details,move|reporter|service.run(request,&reporter).map_err(failed))?;Ok(id)
    }
    pub fn load_market(&self,request:&MarketRequest,need_minutes:bool,fallback:bool,cancellation:Option<&Arc<AtomicBool>>)->Result<LoadedMarket> {
        check_cancel(cancellation)?;
        if request.source=="akshare"{
            if !self.online_enabled{return Err(ApiError::unavailable("在线行情源已禁用"));}
            return self.market_cache.get_or_load(request,&self.engine_version,need_minutes,fallback,cancellation,self.timeout,|selection|self.load_market_uncached(selection,need_minutes,fallback,cancellation));
        }
        self.load_market_uncached(request,need_minutes,fallback,cancellation)
    }
    fn load_market_uncached(&self,request:&MarketRequest,need_minutes:bool,fallback:bool,cancellation:Option<&Arc<AtomicBool>>)->Result<LoadedMarket> {
        super::market::parse_day(&request.start)?;super::market::parse_day(&request.asof)?;
        if !matches!(request.source.as_str(),"tdx"|"akshare")||!wavequant_quant_rs::data::tdx::is_stock(&request.symbol)||request.start>request.asof{return Err(ApiError::invalid("invalid source market selection"));}
        if request.source=="akshare"&&request.asof>(chrono::Utc::now()+chrono::Duration::hours(8)).date_naive().to_string(){return Err(ApiError::invalid("as-of cannot be in the future"));}
        check_cancel(cancellation)?;
        let (bars,base_version,mut provenance,mut files,minutes,missing,minute_version)=if request.source=="tdx" {
            let root=self.tdx_root.as_ref().ok_or_else(||ApiError::unavailable("通达信目录未配置"))?;
            let reader=TdxReader::new(root).map_err(data_error)?;
            let raw=reader.bars(&request.symbol,&request.asof).map_err(data_error)?;
            let start=effective_start(&raw.bars,&request.start,&request.asof)?;
            let adjusted=reader.adjusted_bars(&request.symbol,&start,&request.asof).map_err(data_error)?;
            let files=vec![(reader.day_path(&request.symbol).map_err(data_error)?,adjusted.daily_version.clone()),(reader.root().join("T0002/hq_cache/gbbq"),adjusted.actions_version.clone())];
            let (minutes,missing,minute_version)=if need_minutes {
                let mut source=TdxMinuteSource::new(root).map_err(minute_error)?;let batch=source.batch(&adjusted.bars).map_err(minute_error)?;
                (batch.bars,batch.missing_days,batch.source_version)
            }else{(BTreeMap::new(),vec![],None)};
            let provenance=json!({"provider":"tdx","upstream":"tdx_local","daily_sha256":adjusted.daily_version,"gbbq_sha256":adjusted.actions_version,"price_basis":"causal_adjusted_equivalent","volume_unit":"shares","source_version":adjusted.version,"effective_start":start});
            (adjusted.bars,adjusted.version,provenance,files,minutes,missing,minute_version)
        }else{
            if !self.online_enabled{return Err(ApiError::unavailable("在线行情源已禁用"));}
            if request.symbol.starts_with("bj."){return Err(ApiError::unavailable("固定新浪历史通道不支持北交所回测"));}
            let source_started=std::time::Instant::now();let phase=std::time::Instant::now();
            let provider=AkShareProvider::new(self.timeout).map_err(data_error)?;
            let provider_init_ms=phase.elapsed().as_secs_f64()*1000.0;let phase=std::time::Instant::now();
            let raw=provider.daily(&request.symbol,&request.asof).map_err(data_error)?;
            let daily_ms=phase.elapsed().as_secs_f64()*1000.0;let phase=std::time::Instant::now();
            let factors=provider.factors(&request.symbol).map_err(data_error)?;
            let factors_ms=phase.elapsed().as_secs_f64()*1000.0;let phase=std::time::Instant::now();
            let start=effective_start(&raw.bars,&request.start,&request.asof)?;
            let bars=apply_factors(&raw,&factors.factors,&start).map_err(data_error)?;
            let adjustment_ms=phase.elapsed().as_secs_f64()*1000.0;let phase=std::time::Instant::now();
            let base_version=digest(&wavequant_quant_rs::indexed_json::to_vec(&json!(["akshare-sdk-sina-causal-v1",raw.version,factors.version,start,request.asof]))?);
            let identity_ms=phase.elapsed().as_secs_f64()*1000.0;
            let mut minutes=BTreeMap::new();let mut missing=Vec::new();let mut minute_version=None;
            if need_minutes {
                match provider.minute_history(&request.symbol) {
                    Ok((mut history,version))=>{minute_version=Some(version);for daily in &bars {check_cancel(cancellation)?;match history.remove(&daily.timestamp){Some(rows)if rows.len()==48=>match verify_minute_day(&rows,daily){Ok(())=>{minutes.insert(daily.timestamp.clone(),rows);},Err(MinuteError::VolumeMismatch{minute_volume,daily_volume,..}) if minute_volume<daily_volume=>missing.push(daily.timestamp.clone()),Err(error)=>return Err(minute_error(error))},_=>missing.push(daily.timestamp.clone())}}},
                    Err(DataError::Unavailable(_)) if fallback=>missing=bars.iter().map(|bar|bar.timestamp.clone()).collect(),
                    Err(error)=>return Err(data_error(error)),
                }
            }
            let sdk_version=provider.version().map_err(data_error)?;
            let sdk_calls=wavequant_quant_rs::indexed_json::to_string(&provider.request_timings().map_err(data_error)?)?;
            tracing::info!(symbol=%request.symbol,requested_start=%request.start,asof=%request.asof,provider_init_ms,daily_ms,factors_ms,adjustment_ms,identity_ms,source_total_ms=source_started.elapsed().as_secs_f64()*1000.0,bars=bars.len(),sdk_calls=%sdk_calls,"market_source_timing");
            let provenance=json!({"provider":"akshare","upstream":"sina","transport":"python_sdk_process","daily_endpoint":"stock_zh_a_daily","factor_endpoint":"stock_zh_a_daily:hfq-factor","provider_version":format!("akshare-python/{sdk_version}"),"sdk_version":sdk_version,"daily_sha256":raw.version,"factor_sha256":factors.version,"price_basis":"causal_adjusted_equivalent","volume_unit":"shares","effective_start":start});
            (bars,base_version,provenance,vec![],minutes,missing,minute_version)
        };
        if bars.len()>20000{return Err(ApiError::invalid("单次回测超过 20000 个交易日上限"));}
        if !missing.is_empty()&&!fallback{return Err(ApiError::unavailable(format!("同源五分钟行情不完整，缺少 {} 个交易日；首个缺失日期 {}",missing.len(),missing[0])));}
        if request.source=="tdx" {if let (Some(root),Some(version))=(&self.tdx_root,&minute_version) {let (market,code)=request.symbol.split_once('.').ok_or_else(ApiError::internal)?;files.push((root.join("vipdoc").join(market).join("fzline").join(format!("{market}{code}.lc5")),version.clone()));}}
        let version=digest(&wavequant_quant_rs::indexed_json::to_vec(&json!(["rust-native-market-bundle-v1",base_version,minute_version,missing]))?);
        provenance["minute_sha256"]=json!(minute_version);provenance["minute_source"]=json!(request.source);provenance["source_version"]=json!(version);
        provenance["missing_minute_days"]=json!(missing);provenance["minute_policy"]=json!(if fallback{"same_source_complete_5m_else_explicit_daily_close"}else{"complete_same_source_5m_required"});
        Ok(LoadedMarket{bars,minutes,version,provenance,files,missing})
    }
    fn run(&self,request:BacktestRequest,reporter:&JobReporter)->Result<Value> {
        let started=std::time::Instant::now();
        reporter.progress(5,"校验策略与执行参数")?;
        let mut profile=self.repository.strategy_config(&request.run,&request.variant,request.profile_options())?;
        profile.strategy.volume_filter=request.volume_filter;profile.strategy.shallow_base_breakout_enabled=request.shallow_base_breakout_enabled;
        profile.parameters["volume_filter"]=json!(request.volume_filter);profile.parameters["shallow_base_breakout_enabled"]=json!(request.shallow_base_breakout_enabled);
        let scenario=profile.scenarios.get_mut(&request.scenario).and_then(|v|v.get_mut("execution")).ok_or_else(||ApiError::invalid("unknown execution scenario"))?;
        scenario["initial_capital"]=json!(request.initial_capital);scenario["max_position_weight"]=json!(request.max_position_weight);scenario["net_reward_risk_filter"]=json!(request.net_reward_risk_filter);
        let original=execution_parameters(&profile,&request.scenario).map_err(ApiError::invalid)?;
        let security=security_spec(&request.symbol,&request.asof).map_err(data_error)?;
        let step=security.buy_share_step as usize;
        let lot=original.lot_size.checked_div(gcd(original.lot_size,step)).and_then(|v|v.checked_mul(step)).ok_or_else(||ApiError::invalid("lot size exceeds supported range"))?;
        let scenario=&mut profile.scenarios.get_mut(&request.scenario).ok_or_else(ApiError::internal)?["execution"];
        scenario["lot_size"]=json!(lot);scenario["minimum_entry_shares"]=json!(original.minimum_entry_shares.max(security.minimum_buy_shares as usize));
        let parameters=execution_parameters(&profile,&request.scenario).map_err(ApiError::invalid)?;
        let(execution,holding)=execution_configuration(&profile,&request.scenario).map_err(ApiError::invalid)?;
        let profile_ms=started.elapsed().as_secs_f64()*1000.0;
        reporter.progress(15,"读取同源行情及因果复权")?;
        let market_request=MarketRequest{source:request.source.clone(),symbol:request.symbol.clone(),start:request.start.clone(),asof:request.asof.clone()};
        reporter.data_started()?;
        let loaded_result=self.load_market(&market_request,!wavequant_quant_rs::analysis::analyzer::STRUCTURE_ONLY&&(parameters.staged_exit_intraday||parameters.consolidation_entry_intraday),parameters.missing_minute_daily_fallback,Some(&reporter.cancellation_flag()));
        let data_request_seconds=reporter.data_finished()?;
        let mut loaded=loaded_result?;let trace_cache_started=std::time::Instant::now();
        let artifacts=wavequant_quant_rs::persistence::ArtifactCache::new(&self.repository.root().join(".rust-computation-cache"),4*1024*1024*1024).map_err(|error|ApiError::invalid(error.to_string()))?;
        let cache_identity=json!({"schema":"native-api-stock-result-v5-trend-state","trend_offset_enabled":request.trend_offset_enabled,"timeframe":"1d","engine":self.engine_version,"run":request.run,"profile":profile,"source":request.source,"symbol":request.symbol,"start":request.start,"asof":request.asof,"scenario":request.scenario,"source_version":loaded.version,"missing_minutes":loaded.missing});
        if !self.record_details{if let Some(mut cached)=artifacts.get("backtest",&cache_identity){if super::trend_state::is_response(&cached["view"])&&cached["view"]["complete"]==true {
            let value=&cached["view"];let version=value["dataVersion"].as_str().ok_or_else(ApiError::internal)?;
            if super::trend_state::snapshot(&artifacts,version).is_ok(){
                for(path,version)in &loaded.files{if fingerprint(path).map_err(data_error)?!=*version{return Err(ApiError(axum::http::StatusCode::CONFLICT,"MARKET_DATA_VERSION_CHANGED","行情文件已更新，缓存结果未发布".into()));}}
                self.repository.backtest_version(&request.run,&request.variant,&self.engine_version)?;
                let mut value=value.clone();value["timing"]=json!({"dataRequestMs":data_request_seconds*1000.0,"analysisMs":0.0,"totalMs":started.elapsed().as_secs_f64()*1000.0});
                reporter.progress(99,"读取趋势索引缓存")?;return Ok(value);
            }
        }
        if !super::trend_state::is_response(&cached["view"])&&cached["view"]["complete"]==true&&cached["view"]["symbol"]==request.symbol&&cached["view"]["asof"]==request.asof{
            for(path,version)in &loaded.files{if fingerprint(path).map_err(data_error)?!=*version{return Err(ApiError(axum::http::StatusCode::CONFLICT,"MARKET_DATA_VERSION_CHANGED","行情文件已更新，缓存结果未发布".into()));}}
            self.repository.backtest_version(&request.run,&request.variant,&self.engine_version)?;
            if cached["pipeline"].is_object(){let mut pipeline=cached["pipeline"].clone();pipeline["reused_from_result_cache"]=json!(true);reporter.pipeline(pipeline)?;}
            let mut view=cached.as_object_mut().and_then(|row|row.remove("view")).ok_or_else(ApiError::internal)?;view["backtest"]["source"]=loaded.provenance.clone();view["performance"]=json!({"cache":"disk","elapsed_seconds":0.0,"analysis_elapsed_seconds":0.0,"data_request_seconds":data_request_seconds,"total_elapsed_seconds":started.elapsed().as_secs_f64(),"timing_scope":"analysis"});view["performance"]["data_cache"]=view["backtest"]["source"]["adjusted_cache"].clone();reporter.progress(99,"读取同源同版本账户与趋势缓存")?;return Ok(view);
        }}}
        let artifact_lookup_ms=trace_cache_started.elapsed().as_secs_f64()*1000.0;let trace_input_started=std::time::Instant::now();
        let mut analyzer=AnalyzerConfig::default();analyzer.trading=profile.strategy.clone();analyzer.kernel.trend_offset_enabled=request.trend_offset_enabled;analyzer.kernel.ordinary_trend_completion_enabled=request.ordinary_trend_completion_enabled;analyzer.n_target_trend_confirmation_enabled=request.n_target_trend_confirmation_enabled;
        let minute_inputs=BTreeMap::from([(request.symbol.clone(),loaded.minutes.clone())]);
        let sessions=loaded.bars.iter().cloned().map(|daily|{let minutes=loaded.minutes.remove(&daily.timestamp);let has_minutes=minutes.is_some();BacktestSession{daily,source:request.source.clone(),data_version:loaded.version.clone(),minutes,minute_source:has_minutes.then(||request.source.clone()),minute_data_version:has_minutes.then(||loaded.version.clone())}}).collect::<Vec<_>>();
        let chart_bars=loaded.bars.iter().map(|bar|{
            let decimal=|value:f64|value.to_string().parse::<Decimal>().map_err(|_|ApiError::invalid("price exceeds decimal range"));
            Ok(wavequant_quant_rs::analysis::trading::RuleCandle{symbol:bar.symbol.clone(),timestamp:bar.timestamp.clone(),open:decimal(bar.open)?,high:decimal(bar.high)?,low:decimal(bar.low)?,close:decimal(bar.close)?,volume:decimal(bar.volume)?,adjustment_factor:decimal(bar.adjustment_factor)?,raw_trading_active:bar.raw_trading_active,raw_is_st:bar.raw_is_st})
        }).collect::<Result<Vec<_>>>()?;
        reporter.progress(30,if wavequant_quant_rs::analysis::analyzer::STRUCTURE_ONLY{"逐日计算折线及一、二、三级趋势"}else{"逐日结构、买卖判断与账户同步"})?;
        let recorder=std::cell::RefCell::new(super::calculation::Recorder::new(self.diagnostics_root.as_deref(),json!({"timeframe":"1d","engine":self.engine_version,"run":request.run,"variant":request.variant,"scenario":request.scenario,"source":request.source,"source_version":loaded.version,"profile":profile}),&sessions,self.record_details)?);
        if let Some(pipeline)=recorder.borrow_mut().pipeline_due(true){reporter.pipeline(pipeline)?;}
        let mut progress_error=None;
        let input_preparation_ms=trace_input_started.elapsed().as_secs_f64()*1000.0;
        let worker_started=std::time::Instant::now();
        let computation=self.computation.system(&sessions,SystemConfig{analyzer,execution,holding,parameters,trading_start:None,trading_end:None},reporter.cancellation_flag(),|progress|{
            if progress_error.is_some(){return;}
            let update=(||->Result<()> {
                if let Some(pipeline)=recorder.borrow_mut().pipeline_due(false){reporter.pipeline(pipeline)?;}
                reporter.analysis_timing(progress.analysis_elapsed_seconds)?;
                reporter.progress(30+(u16::from(progress.percent.min(100))*60/100) as u8,&progress.stage)?;
                if let Some(account)=&progress.account {
                    if progress.processed==0||progress.processed>chart_bars.len(){return Err(ApiError::invalid("invalid strategy progress boundary"));}
                    let context=json!({"run_id":request.run,"variant":request.variant,"scenario":request.scenario,"asof":progress.asof});
                    let mut preview=stock_view(account,&chart_bars[..progress.processed],&progress.signals,&progress.audit,&context,None).map_err(ApiError::invalid)?;
                    preview["processed"]=json!(progress.processed);preview["total"]=json!(progress.total);preview["asof"]=json!(progress.asof);
                    preview["analysis"]=wavequant_quant_rs::indexed_json::to_value(&progress.analysis)?;
                    if let Some(analysis)=&progress.analysis{let rows=preview["bars"].as_array().ok_or_else(ApiError::internal)?;preview["theory"]=super::theory::project(analysis,rows,&progress.audit,&self.engine_version)?;}
                    reporter.preview(progress.processed as u64,preview)?;
                }Ok(())
            })();
            if let Err(error)=update{progress_error=Some(error);reporter.cancellation_flag().store(true,std::sync::atomic::Ordering::Release);}
        },|step|recorder.borrow_mut().observe_step(step),true);
        if let Some(error)=progress_error{let pipeline=recorder.borrow_mut().finish(None,Some(&error.2));let _=reporter.pipeline(pipeline);return Err(error);}
        let (result,computation)=match computation{Ok(value)=>value,Err(error)=>{let pipeline=recorder.borrow_mut().finish(None,Some(&error.2));let _=reporter.pipeline(pipeline);return Err(error);}};
        let worker_roundtrip_seconds=worker_started.elapsed().as_secs_f64();
        let preparation_started=std::time::Instant::now();
        if wavequant_quant_rs::analysis::analyzer::STRUCTURE_ONLY {
            reporter.progress(91,"整理趋势索引与独立行情快照")?;
            reporter.analysis_timing(result.analysis_elapsed_seconds)?;reporter.check_cancelled()?;
            for(path,version)in &loaded.files{if fingerprint(path).map_err(data_error)?!=*version{return Err(ApiError(axum::http::StatusCode::CONFLICT,"MARKET_DATA_VERSION_CHANGED","行情文件在计算期间已更新".into()));}}
            self.repository.backtest_version(&request.run,&request.variant,&self.engine_version)?;
            let final_validation_ms=preparation_started.elapsed().as_secs_f64()*1000.0;let trace_view_started=std::time::Instant::now();
            let indexed=result.indexed_analysis.get(&request.symbol).ok_or_else(||ApiError::invalid("缺少股票索引结果"))?;
            let bars=result.bars.get(&request.symbol).ok_or_else(||ApiError::invalid("缺少对应行情"))?;
            let(mut value,snapshot)=super::trend_state::response(indexed,bars,&request.source,&request.asof,data_request_seconds,result.analysis_elapsed_seconds,started.elapsed().as_secs_f64())?;
            let indexed_view_ms=trace_view_started.elapsed().as_secs_f64()*1000.0;let trace_snapshot_started=std::time::Instant::now();
            let identity=json!({"dataVersion":value["dataVersion"]});
            // 内容版本相同且快照校验可读时复用；损坏或被淘汰的快照必须同步补齐。
            let snapshot_reused=value["dataVersion"].as_str().is_some_and(|version|super::trend_state::snapshot(&artifacts,version).is_ok());
            if !snapshot_reused&&!artifacts.put("trend-bars",&identity,&snapshot){return Err(ApiError::unavailable("独立行情快照保存失败，请重试"));}
            let snapshot_write_ms=trace_snapshot_started.elapsed().as_secs_f64()*1000.0;let trace_result_started=std::time::Instant::now();
            let pipeline=recorder.borrow_mut().finish(None,None);reporter.pipeline(pipeline.clone())?;
            reporter.progress(98,"保存趋势索引缓存")?;reporter.check_cancelled()?;
            if value["complete"]==true&&!artifacts.put("backtest",&cache_identity,&json!({"view":value,"pipeline":pipeline})){tracing::warn!("trend result cache write skipped");}
            let result_cache_write_ms=trace_result_started.elapsed().as_secs_f64()*1000.0;
            tracing::info!(symbol=%request.symbol,job=%request.job_id,profile_ms,data_ms=data_request_seconds*1000.0,artifact_lookup_ms,input_preparation_ms,worker_roundtrip_ms=worker_roundtrip_seconds*1000.0,analysis_ms=result.analysis_elapsed_seconds*1000.0,worker_metrics=%computation,final_validation_ms,indexed_view_ms,snapshot_reused,snapshot_write_ms,result_cache_write_ms,total_ms=started.elapsed().as_secs_f64()*1000.0,"backtest_stage_timing");
            value["timing"]["totalMs"]=json!(started.elapsed().as_secs_f64()*1000.0);
            // 100%由任务注册表在接收成功结果后统一发布，运行中进度最高99%。
            reporter.progress(99,"折线及一、二、三级趋势计算完成")?;return Ok(value);
        }
        reporter.progress(91,"分析完成，整理图表结果")?;
        reporter.analysis_timing(result.analysis_elapsed_seconds)?;
        reporter.check_cancelled()?;
        for(path,version)in &loaded.files{if fingerprint(path).map_err(data_error)?!=*version{return Err(ApiError(axum::http::StatusCode::CONFLICT,"MARKET_DATA_VERSION_CHANGED","行情文件在回测期间已更新，未返回混合版本结果".into()));}}
        self.repository.backtest_version(&request.run,&request.variant,&self.engine_version)?;
        let bars=result.bars.get(&request.symbol).ok_or_else(||ApiError::invalid("策略未返回对应股票的真实行情"))?;
        let context=json!({"run_id":request.run,"variant":request.variant,"scenario":request.scenario,"asof":request.asof,"strategy_profile":profile});
        let(orders,_,_)=wavequant_quant_rs::analytics::trade_evidence::ledger(&result.account,&result.bars,&result.signals,&result.audit).map_err(ApiError::invalid)?;
        let episodes=wavequant_quant_rs::analytics::holding_drawdown::holding_drawdowns(&result.bars,&orders,Some(&request.asof),Some(&minute_inputs),None).map_err(ApiError::invalid)?;
        let mut view=stock_view(&result.account,bars,&result.signals,&result.audit,&context,Some(&episodes)).map_err(ApiError::invalid)?;
        // 保持现有前端的股票视图契约；未执行的交易部分仍保留空容器。
        view["backtest"]["strategy"]=wavequant_quant_rs::indexed_json::to_value(&profile.strategy)?;
        view["backtest"]["counts"]=view.get("counts").cloned().unwrap_or_else(||json!({}));
        view["backtest"]["open_positions"]=view.get("open_positions").cloned().unwrap_or_else(||json!([]));
        view["backtest"]["start"]=json!(bars.first().map(|bar|bar.timestamp.as_str()));
        view["backtest"]["end"]=json!(bars.last().map(|bar|bar.timestamp.as_str()));
        view["sessions"]=json!(bars.iter().map(|bar|bar.timestamp.as_str()).collect::<Vec<_>>());
        view["timeframe"]=json!("1d");view["timeframe_label"]=json!("日线");view["is_partial_last_bar"]=json!(false);
        view["data_source"]=json!(request.source);view["resolved_source"]=json!(request.source);
        view["providers"]=json!([request.source]);view["source_fallback"]=json!(false);view["supplemented_bars"]=json!(0);
        view["complete"]=json!(result.unavailable_rules.is_empty());view["unavailable_rules"]=json!(result.unavailable_rules);view["analysis"]=if wavequant_quant_rs::analysis::analyzer::STRUCTURE_ONLY{wavequant_quant_rs::indexed_json::to_value(&result.indexed_analysis)?}else{wavequant_quant_rs::indexed_json::to_value(&result.analysis)?};
        view["backtest"]["status"]=json!(if view["complete"]==true{"completed"}else{"incomplete_rules"});view["backtest"]["source"]=loaded.provenance;
        if wavequant_quant_rs::analysis::analyzer::STRUCTURE_ONLY{view["calculation_mode"]=json!("third_level_only");view["backtest"]["status"]=json!("not_run_structure_only");}
        view["backtest"]["requested_start"]=json!(request.start);view["backtest"]["minute_fallbacks"]=json!(result.minute_fallbacks);view["backtest"]["missing_minute_days"]=json!(loaded.missing);
        view["backtest"]["security_spec"]=wavequant_quant_rs::indexed_json::to_value(security)?;
        let indexed_chart=result.indexed_analysis.get(&request.symbol).map(|indexed|indexed.chart_response(bars)).transpose().map_err(ApiError::invalid)?;
        let analysis=indexed_chart.as_ref().or_else(||result.analysis.get(&request.symbol)).ok_or_else(||ApiError::invalid("策略没有返回股票趋势快照"))?;
        let chart=view["bars"].as_array().ok_or_else(ApiError::internal)?;
        let mut theory=super::theory::project(analysis,chart,&result.audit,&self.engine_version)?;
        theory["price_basis"]=view["price_basis"].clone();theory["run_id"]=view["run_id"].clone();
        if request.source=="akshare"{theory["data_source"]=json!("akshare");theory["upstream"]=json!("sina");}
        view["theory"]=theory;view["parameter_source_run"]=json!(request.run);
        let mut definition=profile.definition.clone();definition["net_reward_risk_filter"]=json!(request.net_reward_risk_filter);definition["shallow_base_breakout_enabled"]=json!(request.shallow_base_breakout_enabled);
        definition["reward_risk_policy"]=json!(if request.net_reward_risk_filter{"execution_price_net_reward_risk_gate_enabled"}else{"execution_price_net_reward_risk_gate_disabled"});
        if let Some(filters)=definition["primary_filters"].as_array_mut(){filters.retain(|name|(request.volume_filter||!matches!(name.as_str(),Some("rvol_1_2"|"volume_gt_previous")))&&(request.net_reward_risk_filter||!matches!(name.as_str(),Some("next_open_net_rr_1_5"|"execution_price_net_rr_1_5"))));}
        view["strategy_profile"]=json!({"id":request.variant,"version":profile.profile_version,"definition":definition});
        view["computation"]=computation;view["performance"]=json!({"cache":"computed","analysis_breakdown":result.analysis_timings,"elapsed_seconds":result.analysis_elapsed_seconds,"analysis_elapsed_seconds":result.analysis_elapsed_seconds,"data_request_seconds":data_request_seconds,"total_elapsed_seconds":started.elapsed().as_secs_f64(),"timing_scope":"analysis","worker_roundtrip_seconds":worker_roundtrip_seconds,"result_preparation_seconds":preparation_started.elapsed().as_secs_f64()});let failure=if view["complete"]==true{None}else{Some("strategy rules incomplete")};let pipeline=recorder.borrow_mut().finish(Some(&view),failure);reporter.pipeline(pipeline.clone())?;
        reporter.progress(98,"图表已整理，保存结果缓存")?;
        let cache_started=std::time::Instant::now();
        reporter.check_cancelled()?;if view["complete"]==true&&!artifacts.put("backtest",&cache_identity,&json!({"view":view,"pipeline":pipeline})){tracing::warn!("backtest result cache write skipped; computed result retained");}
        view["performance"]["data_cache"]=view["backtest"]["source"]["adjusted_cache"].clone();
        view["performance"]["cache_write_seconds"]=json!(cache_started.elapsed().as_secs_f64());
        view["performance"]["total_elapsed_seconds"]=json!(started.elapsed().as_secs_f64());
        reporter.progress(99,"图表结果就绪")?;Ok(view)
    }
}
