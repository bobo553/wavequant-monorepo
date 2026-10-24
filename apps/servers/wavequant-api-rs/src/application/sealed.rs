//! 以不可变日线快照重算独立账户；组合封存成交不能被重新标成个股收益。
use std::{collections::BTreeMap,sync::{Arc,Mutex,atomic::AtomicBool}};
use chrono::{DateTime,NaiveDate,NaiveDateTime,FixedOffset};
use wavequant_quant_rs::indexed_json::{Value,json};
use wavequant_quant_rs::{analysis::{analyzer::AnalyzerConfig,profiles::{ProfileOptions,execution_parameters,execution_configuration},system::{run_system_backtest,SystemConfig}},data::adjustments::ExecutionBar,execution::BacktestSession,analytics::{research::stock_view,trade_evidence::ledger,holding_drawdown::holding_drawdowns}};
use super::research::{ResearchRepository,digest};
use crate::presentation::error::ApiError;
type Result<T>=std::result::Result<T,ApiError>;
pub struct SealedResearch {repository:Arc<ResearchRepository>,engine:String,cache:Mutex<BTreeMap<String,Value>>}
fn text<'a>(row:&'a Value,field:&str)->Result<&'a str>{row[field].as_str().ok_or_else(||ApiError::invalid(format!("snapshot {field} missing")))}
fn number(row:&Value,field:&str)->Result<f64>{text(row,field)?.parse::<f64>().ok().filter(|v|v.is_finite()).ok_or_else(||ApiError::invalid(format!("invalid snapshot {field}")))}
fn boolean(row:&Value,field:&str)->Result<Option<bool>>{match row.get(field).and_then(Value::as_str).map(str::trim).filter(|v|!v.is_empty()).map(str::to_ascii_lowercase).as_deref(){None=>Ok(None),Some("1"|"true"|"yes"|"y")=>Ok(Some(true)),Some("0"|"false"|"no"|"n")=>Ok(Some(false)),_=>Err(ApiError::invalid(format!("invalid snapshot {field}")))}}
pub fn local_day(value:&str)->Result<String>{
    if let Ok(stamp)=DateTime::parse_from_rfc3339(value).or_else(|_|DateTime::parse_from_str(value,"%Y-%m-%dT%H:%M:%S%.f%z")).or_else(|_|DateTime::parse_from_str(value,"%Y-%m-%d %H:%M:%S%.f%z")){return Ok(stamp.with_timezone(&FixedOffset::east_opt(8*3600).ok_or_else(ApiError::internal)?).date_naive().to_string());}
    if let Ok(stamp)=NaiveDateTime::parse_from_str(value,"%Y-%m-%dT%H:%M:%S%.f").or_else(|_|NaiveDateTime::parse_from_str(value,"%Y-%m-%d %H:%M:%S%.f")){return Ok(stamp.date().to_string());}
    NaiveDate::parse_from_str(value,"%Y-%m-%d").map(|day|day.to_string()).map_err(|_|ApiError::invalid("invalid snapshot timestamp"))
}
pub fn parse(rows:Vec<Value>)->Result<BTreeMap<String,Vec<ExecutionBar>>>{
    let mut grouped=BTreeMap::<String,Vec<ExecutionBar>>::new();
    for row in rows {
        let volume=number(&row,"volume")?;
        if volume<0.0{return Err(ApiError::invalid("sealed volume must be nonnegative"));}
        let factor=match row["adjustment_factor"].as_str().filter(|v|!v.is_empty()){Some(value)=>value.parse::<f64>().ok().filter(|v|v.is_finite()&&*v>0.0).ok_or_else(||ApiError::invalid("invalid snapshot factor"))?,None=>1.0};
        let symbol=text(&row,"symbol")?.to_owned();
        let bar=ExecutionBar{timestamp:local_day(text(&row,"timestamp")?)?,symbol:symbol.clone(),open:number(&row,"open")?,high:number(&row,"high")?,low:number(&row,"low")?,close:number(&row,"close")?,volume,adjustment_factor:factor,
            buyable:boolean(&row,"buyable")?.unwrap_or(true),sellable:boolean(&row,"sellable")?.unwrap_or(true),close_sellable:boolean(&row,"close_sellable")?,nonflat_close_sellable:boolean(&row,"nonflat_close_sellable")?,
            close_buyable:None,nonflat_close_buyable:None,raw_is_st:boolean(&row,"raw_is_st")?,raw_trading_active:boolean(&row,"raw_trading_active")?};
        grouped.entry(symbol).or_default().push(bar);
    }
    for bars in grouped.values_mut(){bars.sort_by(|a,b|a.timestamp.cmp(&b.timestamp));if bars.windows(2).any(|pair|pair[0].timestamp>=pair[1].timestamp){return Err(ApiError::invalid("sealed snapshot contains repeated Shanghai trading day"));}}
    Ok(grouped)
}
impl SealedResearch {
    pub fn new(repository:Arc<ResearchRepository>,engine:String)->Self{Self{repository,engine,cache:Mutex::new(BTreeMap::new())}}
    pub fn stock(&self,run:&str,variant:&str,symbol:&str,asof:&str,scenario:&str,options:ProfileOptions,cancellation:Option<Arc<AtomicBool>>)->Result<Value>{
        super::market::parse_day(asof)?;
        self.repository.backtest_version(run,variant,&self.engine)?;
        let(rows,version,_)=self.repository.frozen_snapshot(run)?;
        let profile=self.repository.strategy_config(run,variant,options)?;
        let cache_key=digest(&wavequant_quant_rs::indexed_json::to_vec(&json!(["rust-sealed-stock-v1",run,version,self.engine,variant,symbol,asof,scenario,profile]))?);
        if let Some(value)=self.cache.lock().map_err(|_|ApiError::internal())?.get(&cache_key){return Ok(value.clone());}
        let mut grouped=parse(rows)?;
        let mut bars=grouped.remove(symbol).ok_or_else(||ApiError::invalid("unknown symbol"))?;
        if bars.last().is_none_or(|bar|asof>bar.timestamp.as_str()){return Err(ApiError::invalid("as-of exceeds last available session"));}
        bars.retain(|bar|bar.timestamp.as_str()<=asof);
        if bars.is_empty(){return Err(ApiError::invalid("no bars available as of selected date"));}
        if bars.len()>20000{return Err(ApiError::invalid("single stock snapshot exceeds 20000 daily bars"));}
        let parameters=execution_parameters(&profile,scenario).map_err(ApiError::invalid)?;
        let(execution,holding)=execution_configuration(&profile,scenario).map_err(ApiError::invalid)?;
        let mut analyzer=AnalyzerConfig::default();analyzer.trading=profile.strategy.clone();analyzer.kernel.ordinary_trend_completion_enabled=options.ordinary_trend_completion_enabled;analyzer.n_target_trend_confirmation_enabled=options.n_target_trend_confirmation_enabled;
        let sessions=bars.into_iter().map(|daily|BacktestSession{daily,source:"sealed_snapshot".into(),data_version:version.clone(),minutes:None,minute_source:None,minute_data_version:None}).collect::<Vec<_>>();
        let result=run_system_backtest(&sessions,SystemConfig{analyzer,execution,holding,parameters,trading_start:None,trading_end:None},cancellation,|_|{}).map_err(ApiError::invalid)?;
        let bars=result.bars.get(symbol).ok_or_else(||ApiError::invalid("system did not return requested stock"))?;
        let(orders,_,_)=ledger(&result.account,&result.bars,&result.signals,&result.audit).map_err(ApiError::invalid)?;
        let episodes=holding_drawdowns(&result.bars,&orders,Some(asof),None,None).map_err(ApiError::invalid)?;
        let context=json!({"run_id":run,"variant":variant,"scenario":scenario,"asof":asof,"strategy_profile":{"id":variant,"version":profile.profile_version,"definition":profile.definition}});
        let mut view=stock_view(&result.account,bars,&result.signals,&result.audit,&context,Some(&episodes)).map_err(ApiError::invalid)?;
        view["complete"]=json!(result.unavailable_rules.is_empty());view["unavailable_rules"]=json!(result.unavailable_rules);view["analysis"]=wavequant_quant_rs::indexed_json::to_value(result.analysis)?;
        view["backtest"]["source_run"]=json!(run);view["backtest"]["data_sha256"]=json!(version);view["backtest"]["minute_fallbacks"]=json!(result.minute_fallbacks);
        view["backtest"]["provenance"]=json!("rust_current_engine_on_verified_snapshot_prefix");
        view["backtest"]["status"]=json!(if view["complete"]==true{"completed"}else{"incomplete_rules"});
        self.repository.backtest_version(run,variant,&self.engine)?;
        if view["complete"]==true {let mut cache=self.cache.lock().map_err(|_|ApiError::internal())?;if cache.len()>=32{if let Some(key)=cache.keys().next().cloned(){cache.remove(&key);}}cache.insert(cache_key,view.clone());}
        Ok(view)
    }
    pub fn theory(&self,run:&str,variant:&str,symbol:&str,asof:&str,options:ProfileOptions,cancellation:Option<Arc<AtomicBool>>)->Result<Value>{
        let view=self.stock(run,variant,symbol,asof,"base",options,cancellation)?;
        let response:wavequant_quant_rs::analysis::analyzer::AnalysisResponse=wavequant_quant_rs::indexed_json::from_value(view["analysis"][symbol].clone())?;
        super::theory::project(&response,view["bars"].as_array().ok_or_else(ApiError::internal)?,view["audit"].as_array().ok_or_else(ApiError::internal)?,&self.engine)
    }
    pub fn summary(&self,run:&str,variant:&str,asof:&str,scenario:&str,cancellation:Option<Arc<AtomicBool>>)->Result<Value>{
        super::market::parse_day(asof)?;
        self.repository.strategy_config(run,variant,Default::default())?;
        let(rows,_,report)=self.repository.frozen_snapshot(run)?;
        if report["data"]["end"].as_str().is_none_or(|end|asof>end){return Err(ApiError::invalid("invalid summary cutoff"));}
        let grouped=parse(rows)?;let mut rows=Vec::new();let mut complete=true;
        for(symbol,bars)in grouped {
            if cancellation.as_ref().is_some_and(|cancel|cancel.load(std::sync::atomic::Ordering::Acquire)){return Err(ApiError::unavailable("research request cancelled"));}
            let first=&bars.first().ok_or_else(ApiError::internal)?.timestamp;
            let end=asof.min(bars.last().ok_or_else(ApiError::internal)?.timestamp.as_str());
            if end<first.as_str(){rows.push(json!({"symbol":symbol,"status":"no_history","asof":end}));continue;}
            let view=self.stock(run,variant,&symbol,end,scenario,Default::default(),cancellation.clone())?;
            let valid=view["complete"]==true;complete&=valid;
            rows.push(json!({"symbol":symbol,"status":if valid{"completed"}else{"incomplete_rules"},"asof":end,"metrics":view["metrics"],"diagnostics":view["backtest"]["diagnostics"],"unavailable_rules":view["unavailable_rules"]}));
        }
        Ok(json!({"run_id":run,"variant":variant,"scenario":scenario,"asof":asof,"results":rows,"account_scope":"independent_single_stock","complete":complete}))
    }
}
