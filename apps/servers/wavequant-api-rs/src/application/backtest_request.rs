//! 旧回测查询契约在入口集中规范化，策略身份及所有可变选项均参与幂等键。
use std::collections::BTreeMap;
use rust_decimal::Decimal;
use serde::{Serialize,Deserialize};
use wavequant_quant_rs::indexed_json::{Value,json};
use wavequant_quant_rs::analysis::profiles::ProfileOptions;
use crate::presentation::error::ApiError;
type Result<T>=std::result::Result<T,ApiError>;
pub const REQUIRED:[&str;6]=["run","variant","symbol","asof","scenario","start"];
pub const OPTIONAL:[&str;13]=["volume_filter","net_reward_risk_filter","shallow_base_breakout_enabled","n_target_trend_confirmation_enabled","ordinary_trend_completion_enabled","buy_interval_level","initial_capital","max_position_weight","backtest_job","strategy_version","trend_offset_enabled","trading_enabled","enable_secondary_reversal"];
#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct BacktestRequest {
    pub source:String,pub run:String,pub variant:String,pub symbol:String,pub asof:String,pub scenario:String,pub start:String,
    #[serde(default="offset_default")] pub trend_offset_enabled:bool,
    #[serde(default="trading_default")] pub trading_enabled:bool,
    #[serde(default)] pub enable_secondary_reversal:Option<[bool;4]>,
    pub volume_filter:bool,pub net_reward_risk_filter:bool,pub shallow_base_breakout_enabled:bool,
    pub n_target_trend_confirmation_enabled:bool,pub ordinary_trend_completion_enabled:bool,pub buy_interval_level:u8,
    pub initial_capital:Decimal,pub max_position_weight:Decimal,pub job_id:String,pub strategy_version:Option<String>,
}

#[cfg(test)]
mod symbol_tests {
    use super::*;

    fn query(symbol:&str)->BTreeMap<String,String>{
        [("run","sample"),("variant","lecture_v3"),("symbol",symbol),("asof","2026-10-26"),
            ("scenario","base"),("start","2018-01-01")].into_iter().map(|(key,value)|(key.into(),value.into())).collect()
    }

    #[test]
    fn online_index_requests_keep_exchange_identity_and_stock_requests_still_work(){
        for symbol in ["sh.000001","sz.399001","bj.899050","sz.000001"]{
            let request=BacktestRequest::parse("akshare",&query(symbol)).unwrap();
            assert_eq!(request.symbol,symbol);assert_eq!(request.params()["symbol"],symbol);
        }
        assert!(BacktestRequest::parse("tdx",&query("sh.000001")).is_err());
        assert!(BacktestRequest::parse("tdx",&query("sz.000001")).is_ok());
    }

    #[test]
    fn index_support_does_not_accept_ambiguous_or_malformed_symbols(){
        for symbol in ["000001","sh000001","sh.00000x","sh.000001/../","sz.600519"]{
            assert!(BacktestRequest::parse("akshare",&query(symbol)).is_err(),"{symbol}");
        }
    }

    #[test]
    fn saved_watchlist_options_disable_accounts_and_preserve_qualification_levels(){
        let mut params=query("sh.601086");
        params.insert("trading_enabled".into(),"false".into());
        params.insert("enable_secondary_reversal".into(),"[false,true,false,true]".into());
        let request=BacktestRequest::parse("akshare",&params).unwrap();
        assert!(!request.executes_trades());
        assert_eq!(request.qualification_levels(),[false,true,false,true]);
        let restored=BacktestRequest::parse("akshare",&request.params()).unwrap();
        assert!(!restored.executes_trades());
        assert_eq!(restored.qualification_levels(),request.qualification_levels());
        let manual=BacktestRequest::parse("akshare",&query("sh.601086")).unwrap();
        assert!(manual.executes_trades());
        assert_ne!(manual.bind_version("v47").unwrap().0,request.bind_version("v47").unwrap().0);
        assert!(!BacktestRequest::parse("akshare",&query("sh.000001")).unwrap().executes_trades());
    }

    #[test]
    fn invalid_qualification_levels_are_rejected(){
        for raw in ["[false,true]","[false,false,true,false,true]","[false,false,1,false]","invalid"]{
            let mut params=query("sh.601086");params.insert("enable_secondary_reversal".into(),raw.into());
            assert!(BacktestRequest::parse("akshare",&params).is_err());
        }
    }
}
fn offset_default()->bool{true}
fn trading_default()->bool{true}
pub fn boolean(query:&BTreeMap<String,String>,name:&str,default:bool)->Result<bool>{match query.get(name).map(String::as_str){None=>Ok(default),Some("true")=>Ok(true),Some("false")=>Ok(false),_=>Err(ApiError::invalid(format!("{name} must be true or false")))}}
fn positive(query:&BTreeMap<String,String>,name:&str,default:Decimal,maximum:Decimal)->Result<Decimal>{
    let value=query.get(name).map(|value|value.parse::<Decimal>().map_err(|_|ApiError::invalid(format!("{name} must be a decimal number")))).transpose()?.unwrap_or(default);
    if value<=Decimal::ZERO||value>maximum{return Err(ApiError::invalid(format!("{name} is outside the supported range")));}Ok(value)
}
impl BacktestRequest {
    pub fn parse(source:&str,query:&BTreeMap<String,String>)->Result<Self> {
        if !matches!(source,"tdx"|"akshare"){return Err(ApiError::invalid("source must be tdx or akshare"));}
        if query.keys().any(|key|!REQUIRED.contains(&key.as_str())&&!OPTIONAL.contains(&key.as_str())){return Err(ApiError::invalid("unknown backtest query arguments"));}
        let field=|name:&str|query.get(name).filter(|value|!value.is_empty()&&value.len()<=128&&value.trim()==value.as_str()).cloned().ok_or_else(||ApiError::invalid(format!("one {name} is required")));
        let run=field("run")?;let variant=field("variant")?;let symbol=field("symbol")?.to_lowercase();let asof=field("asof")?;let scenario=field("scenario")?;let start=field("start")?;
        super::market::parse_day(&asof)?;super::market::parse_day(&start)?;
        if start>asof{return Err(ApiError::invalid("start must not exceed asof"));}
        if !wavequant_quant_rs::data::indices::supported(source,&symbol){return Err(ApiError::invalid("invalid security symbol"));}
        let buy_interval_level=match query.get("buy_interval_level").map(String::as_str).unwrap_or("2"){"1"=>1,"2"=>2,"3"=>3,_=>return Err(ApiError::invalid("buy_interval_level must be 1, 2 or 3"))};
        let enable_secondary_reversal=query.get("enable_secondary_reversal").map(|value|serde_json::from_str::<[bool;4]>(value).map_err(|_|ApiError::invalid("enable_secondary_reversal must contain four booleans"))).transpose()?;
        let job_id=query.get("backtest_job").cloned().unwrap_or_else(||uuid::Uuid::new_v4().to_string());super::jobs::validate_id(&job_id)?;
        let strategy_version=query.get("strategy_version").cloned();if let Some(version)=&strategy_version{crate::storage::snapshots::digest(version)?;}
        Ok(Self{source:source.into(),run,variant,symbol,asof,scenario,start,buy_interval_level,job_id,strategy_version,enable_secondary_reversal,
            trading_enabled:boolean(query,"trading_enabled",true)?,
            trend_offset_enabled:boolean(query,"trend_offset_enabled",true)?,volume_filter:boolean(query,"volume_filter",true)?,net_reward_risk_filter:boolean(query,"net_reward_risk_filter",false)?,
            shallow_base_breakout_enabled:boolean(query,"shallow_base_breakout_enabled",true)?,
            n_target_trend_confirmation_enabled:boolean(query,"n_target_trend_confirmation_enabled",false)?,ordinary_trend_completion_enabled:boolean(query,"ordinary_trend_completion_enabled",false)?,
            initial_capital:positive(query,"initial_capital",Decimal::from(100_000),Decimal::from(1_000_000_000))?,max_position_weight:positive(query,"max_position_weight",Decimal::ONE,Decimal::ONE)?,
        })
    }
    pub fn profile_options(&self)->ProfileOptions {ProfileOptions{n_target_trend_confirmation_enabled:self.n_target_trend_confirmation_enabled,ordinary_trend_completion_enabled:self.ordinary_trend_completion_enabled,buy_interval_level:self.buy_interval_level}}
    pub fn qualification_levels(&self)->[bool;4]{self.enable_secondary_reversal.unwrap_or_else(||std::array::from_fn(|level|level==usize::from(self.buy_interval_level)))}
    pub fn executes_trades(&self)->bool{self.trading_enabled&&!wavequant_quant_rs::data::indices::is_index(&self.symbol)}
    pub fn path(&self)->String{format!("/api/{}-backtest",self.source)}
    pub fn params(&self)->BTreeMap<String,String>{
        let mut params=BTreeMap::from([("run".into(),self.run.clone()),("variant".into(),self.variant.clone()),("symbol".into(),self.symbol.clone()),("asof".into(),self.asof.clone()),("scenario".into(),self.scenario.clone()),("start".into(),self.start.clone())]);
        for(key,value)in [("trend_offset_enabled",self.trend_offset_enabled),("volume_filter",self.volume_filter),("net_reward_risk_filter",self.net_reward_risk_filter),("shallow_base_breakout_enabled",self.shallow_base_breakout_enabled),("n_target_trend_confirmation_enabled",self.n_target_trend_confirmation_enabled),("ordinary_trend_completion_enabled",self.ordinary_trend_completion_enabled)]{params.insert(key.into(),value.to_string());}
        if !self.trading_enabled{params.insert("trading_enabled".into(),"false".into());}
        if let Some(levels)=self.enable_secondary_reversal{params.insert("enable_secondary_reversal".into(),format!("[{},{},{},{}]",levels[0],levels[1],levels[2],levels[3]));}
        params.insert("buy_interval_level".into(),self.buy_interval_level.to_string());params.insert("initial_capital".into(),self.initial_capital.normalize().to_string());params.insert("max_position_weight".into(),self.max_position_weight.normalize().to_string());params
    }
    pub fn bind_version(&self,current:&str)->Result<(String,Value)>{
        if self.strategy_version.as_ref().is_some_and(|expected|expected!=current){return Err(ApiError(axum::http::StatusCode::CONFLICT,"BACKTEST_VERSION_CHANGED","策略代码已变更，请重新读取当前版本后回测".into()));}
        let params=self.params();let path=self.path();
        let signature=wavequant_quant_rs::indexed_json::to_string(&json!([current,path,params]))?;
        Ok((signature,json!({"path":path,"params":params,"symbol":self.symbol,"version":current})))
    }
}
