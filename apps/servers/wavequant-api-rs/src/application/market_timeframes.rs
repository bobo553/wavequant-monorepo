//! 日/周/月/季/年行情与结构共用同一规范前缀，完成的同版本快照才发布到 SQL。
use std::{collections::VecDeque,path::PathBuf,sync::{Arc,Mutex,atomic::{AtomicBool,Ordering}}};
use rust_decimal::Decimal;
use wavequant_quant_rs::indexed_json::{Value,json};
use wavequant_quant_rs::analysis::{analyzer::{AnalysisBar,AnalysisRequest,AnalyzerConfig,DataIdentity,TrendAnalyzer},profiles::ProfileOptions};
use crate::{presentation::error::ApiError,storage::snapshots::{TimeframeQuery,TimeframeSnapshot}};
use super::{market::{self,OnlineMarket},infrastructure::Infrastructure,research::digest,theory};
type Result<T>=std::result::Result<T,ApiError>;
pub struct MarketTimeframes {pub tdx_root:Option<PathBuf>,pub online:Arc<OnlineMarket>,pub online_enabled:bool,pub infrastructure:Arc<Infrastructure>,pub engine:String,cache:Mutex<VecDeque<(String,Value)>>}
impl MarketTimeframes {
    pub fn new(tdx_root:Option<PathBuf>,online:Arc<OnlineMarket>,online_enabled:bool,infrastructure:Arc<Infrastructure>,engine:String)->Self{Self{tdx_root,online,online_enabled,infrastructure,engine,cache:Mutex::new(VecDeque::new())}}
    fn algorithm(&self,options:ProfileOptions)->Result<String>{Ok(digest(&wavequant_quant_rs::indexed_json::to_vec(&json!(["rust-native-calendar-theory-v1",self.engine,options]))?))}
    pub fn view(&self,source:&str,symbol:&str,asof:&str,timeframe:&str)->Result<Value>{
        market::parse_day(asof)?;if !matches!(timeframe,"1d"|"1w"|"1mo"|"3mo"|"1y"){return Err(ApiError::invalid("unsupported market timeframe"));}
        match source {"tdx"=>market::view(self.tdx_root.as_ref().ok_or_else(||ApiError::unavailable("通达信目录未配置"))?,symbol,asof,timeframe),"akshare" if self.online_enabled=>self.online.view(symbol,asof,timeframe),_=>Err(ApiError::unavailable("market provider unavailable"))}
    }
    fn render(&self,view:&Value,source:&str,symbol:&str,timeframe:&str,options:ProfileOptions,cancellation:Option<&Arc<AtomicBool>>)->Result<Value>{
        let rows=view["bars"].as_array().filter(|rows|!rows.is_empty()).ok_or_else(||ApiError::invalid("market view requires bars"))?;
        if rows.len()>20000{return Err(ApiError::invalid("market theory exceeds 20000 bars"));}
        let key=digest(&wavequant_quant_rs::indexed_json::to_vec(&json!([source,symbol,timeframe,view["data_version"],self.engine,options]))?);
        {let mut cache=self.cache.lock().map_err(|_|ApiError::internal())?;if let Some(i)=cache.iter().position(|(id,_)|id==&key){let item=cache.remove(i).ok_or_else(ApiError::internal)?;let value=item.1.clone();cache.push_back(item);return Ok(value);}}
        let bars=rows.iter().enumerate().map(|(index,row)|{
            let number=|field:&str|row[field].as_f64().filter(|n|n.is_finite()).ok_or_else(||ApiError::invalid(format!("market {field} must be finite")));
            let decimal=|field:&str|->Result<Decimal>{number(field)?.to_string().parse().map_err(|_|ApiError::invalid("market number exceeds decimal range"))};
            let time=row["time"].as_str().ok_or_else(||ApiError::invalid("market bar time missing"))?.to_owned();market::parse_day(&time)?;
            Ok(AnalysisBar{index,time,open:number("open")?,high:number("high")?,low:number("low")?,close:number("close")?,volume:decimal("volume")?,adjustment_factor:decimal("factor")?,raw_is_st:None,raw_trading_active:None})
        }).collect::<Result<Vec<_>>>()?;
        let mut config=AnalyzerConfig::default();config.n_target_trend_confirmation_enabled=options.n_target_trend_confirmation_enabled;config.kernel.ordinary_trend_completion_enabled=options.ordinary_trend_completion_enabled;
        let mut analyzer=TrendAnalyzer::new(AnalysisRequest{identity:DataIdentity{symbol:symbol.into(),source_version:view["data_version"].as_str().ok_or_else(ApiError::internal)?.into(),adjustment:view["price_basis"].as_str().unwrap_or("raw_unadjusted").into(),timeframe:timeframe.into()},bars,config,state:None}).map_err(ApiError::invalid)?;
        loop{if cancellation.is_some_and(|flag|flag.load(Ordering::Acquire)){return Err(ApiError::unavailable("market analysis cancelled"));}if !analyzer.process_next().map_err(ApiError::invalid)?{break;}}
        let mut result=theory::project(&analyzer.response(),rows,&analyzer.trading_state().events,&self.engine)?;
        for field in ["requested_asof","data_source","resolved_source","providers","provider_version","sdk_version","transport","upstream","supplemented_bars","source_fallback","source_warning","data_version","timeframe","timeframe_label","is_partial_last_bar","timeframe_source","price_basis"]{if let Some(value)=view.get(field){result[field]=value.clone();}}
        result["computed_from"]=json!("rust_calendar_timeframe_from_canonical_daily");
        let mut cache=self.cache.lock().map_err(|_|ApiError::internal())?;if cache.len()>=32{cache.pop_front();}cache.push_back((key,result.clone()));Ok(result)
    }
    pub fn theory(&self,source:&str,symbol:&str,asof:&str,timeframe:&str,options:ProfileOptions,cancellation:Option<&Arc<AtomicBool>>)->Result<Value>{let view=self.view(source,symbol,asof,timeframe)?;self.render(&view,source,symbol,timeframe,options,cancellation)}
    pub fn bundle(&self,source:&str,symbol:&str,asof:&str,timeframe:&str,options:ProfileOptions,compute_if_missing:bool,cancellation:Option<&Arc<AtomicBool>>)->Result<Value>{
        let view=self.view(source,symbol,asof,timeframe)?;self.bundle_from_view(view,source,symbol,asof,timeframe,options,compute_if_missing,cancellation)
    }
    fn bundle_from_view(&self,view:Value,source:&str,symbol:&str,asof:&str,timeframe:&str,options:ProfileOptions,compute_if_missing:bool,cancellation:Option<&Arc<AtomicBool>>)->Result<Value>{
        let algorithm=self.algorithm(options)?;let data=view["data_version"].as_str().ok_or_else(ApiError::internal)?;
        if let Some(database)=&self.infrastructure.database{if let Some(stored)=database.find_market_timeframe_snapshot(TimeframeQuery{source,symbol,timeframe,requested_asof:asof,algorithm_version:&algorithm,data_version:Some(data)})?{
            if stored.payload["theory"]["complete"]==true{let mut payload=stored.payload;payload["cache_state"]=json!("precomputed");return Ok(payload);}
        }}
        if !compute_if_missing{return Err(ApiError::unavailable("该股票周期快照尚未预计算"));}
        let theory=self.render(&view,source,symbol,timeframe,options,cancellation)?;
        let id=digest(format!("{source}:{symbol}:{timeframe}:{asof}:{data}:{algorithm}").as_bytes());
        let resolved=view["asof"].as_str().ok_or_else(ApiError::internal)?.to_owned();
        let mut payload=json!({"schema_version":1,"snapshot_id":id,"source":source,"symbol":symbol,"timeframe":timeframe,"requested_asof":asof,"resolved_asof":resolved,"algorithm_version":algorithm,"n_target_trend_confirmation_enabled":options.n_target_trend_confirmation_enabled,"ordinary_trend_completion_enabled":options.ordinary_trend_completion_enabled,"data_version":data,"view":view,"theory":theory});
        let complete=payload["theory"]["complete"]==true;
        let mut published=false;
        if cancellation.is_some_and(|flag|flag.load(Ordering::Acquire)){return Err(ApiError::unavailable("timeframe publication cancelled"));}
        if complete{if let Some(database)=&self.infrastructure.database{database.save_market_timeframe_snapshot(TimeframeSnapshot{snapshot_id:id.clone(),source:source.into(),symbol:symbol.into(),timeframe:timeframe.into(),requested_asof:asof.into(),resolved_asof:resolved,algorithm_version:algorithm,data_version:data.into(),payload:payload.clone(),created_at:None,updated_at:None})?;published=true;
            if let Some(cache)=&self.infrastructure.cache{if cache.set_json(&format!("market:timeframe:rust:v1:{id}"),&payload,None).is_err(){tracing::warn!("timeframe Redis write failed; SQL remains authoritative");}}
        }}
        payload["cache_state"]=json!(if published{"precomputed"}else if complete{"computed_on_demand"}else{"computed_on_demand_incomplete"});Ok(payload)
    }
    pub fn precompute(&self,source:&str,symbol:&str,asof:&str,timeframes:&[String],options:ProfileOptions,cancellation:Option<&Arc<AtomicBool>>)->Result<Value>{
        if self.infrastructure.database.is_none(){return Err(ApiError::unavailable("周期预计算需要配置持久化数据库"));}
        let daily=match source{"tdx"=>{let root=self.tdx_root.as_ref().ok_or_else(||ApiError::unavailable("通达信目录未配置"))?;wavequant_quant_rs::data::tdx::TdxReader::new(root).and_then(|r|r.bars(symbol,"9999-12-31")).map_err(|error|ApiError::invalid(error.to_string()))?},"akshare" if self.online_enabled=>self.online.daily(symbol,asof)?,_=>return Err(ApiError::unavailable("market provider unavailable"))};
        let mut published=Vec::new();let mut unchanged=Vec::new();let mut incomplete=Vec::new();let mut seen=std::collections::BTreeSet::new();
        for timeframe in timeframes{if !seen.insert(timeframe){continue;}if cancellation.is_some_and(|flag|flag.load(Ordering::Acquire)){return Err(ApiError::unavailable("timeframe precompute cancelled"));}let algorithm=self.algorithm(options)?;let mut view=market::render(source,&daily,asof,timeframe)?;if source=="akshare"{self.online.decorate(&mut view,&daily.version)?;}let data=view["data_version"].as_str().ok_or_else(ApiError::internal)?;
            let old=self.infrastructure.database.as_ref().ok_or_else(ApiError::internal)?.find_market_timeframe_snapshot(TimeframeQuery{source,symbol,timeframe,requested_asof:asof,algorithm_version:&algorithm,data_version:Some(data)})?;
            if old.is_some_and(|value|value.payload["theory"]["complete"]==true){unchanged.push(timeframe.clone());continue;}
            let value=self.bundle_from_view(view,source,symbol,asof,timeframe,options,true,cancellation)?;if value["theory"]["complete"]==true{published.push(timeframe.clone());}else{incomplete.push(json!({"timeframe":timeframe,"unavailable_rules":value["theory"]["unavailable_rules"]}));}
        }
        Ok(json!({"source":source,"symbol":symbol,"requested_asof":asof,"published":published,"unchanged":unchanged,"incomplete":incomplete,"complete":incomplete.is_empty()}))
    }
}
