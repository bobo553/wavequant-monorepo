//! SQL/Redis 结构及买点读模型；查询只过滤已原子发布的快照。
//! 来源 application/structure_snapshots.py、buy_signal_snapshots.py、screening/*_scanner.py。
use std::{collections::{BTreeMap,BTreeSet},sync::Arc};
use wavequant_quant_rs::indexed_json::{Value,json};
use crate::{presentation::error::ApiError,storage::snapshots::{SignalSnapshot,SignalQuery,SignalFamily,SnapshotStore}};
use super::{research::{ResearchRepository,digest},infrastructure::Infrastructure,backtest::BacktestService,jobs::BacktestJobs,market::parse_day};
use wavequant_quant_rs::analysis::profiles::ProfileOptions;
type Result<T>=std::result::Result<T,ApiError>;
const MARKETS:[&str;5]=["shanghai","shenzhen","chinext","star","beijing"];
const LANDMARKS:[&str;3]=["bear_to_bull","bear_bull_alternation","bullish_turn"];
#[derive(Clone)]
pub struct SignalSnapshots {repository:Arc<ResearchRepository>,infrastructure:Arc<Infrastructure>,backtests:Arc<BacktestService>,jobs:BacktestJobs,engine:String,cancellation:Option<Arc<std::sync::atomic::AtomicBool>>}
#[derive(Clone)]
struct Request {run:String,variant:String,source:String,asof:String,symbol:Option<String>,scenario:Option<String>,start:Option<String>,lookback:u64,
    signal_type:String,trend_level:u64,markets:Vec<String>,n_target:bool,ordinary:bool,params:Value}
fn field<'a>(p:&'a BTreeMap<String,String>,key:&str)->Result<&'a str>{p.get(key).map(String::as_str).filter(|v|!v.is_empty()).ok_or_else(||ApiError::invalid(format!("missing {key}")))}
fn boolean(p:&BTreeMap<String,String>,key:&str)->Result<bool>{match p.get(key).map(String::as_str){None|Some("false"|"0")=>Ok(false),Some("true"|"1")=>Ok(true),_=>Err(ApiError::invalid(format!("{key} must be a boolean")))}}
fn number(v:&Value,key:&str)->Option<u64>{v.get(key).and_then(|v|v.as_u64().or_else(||v.as_str()?.parse().ok()))}
fn text<'a>(v:&'a Value,key:&str)->&'a str{v.get(key).and_then(Value::as_str).unwrap_or("")}
fn version(value:&Value)->Result<String>{Ok(digest(&wavequant_quant_rs::indexed_json::to_vec(value)?))}
fn request(p:&BTreeMap<String,String>,buy:bool,refresh:bool)->Result<Request>{
    let required=if buy{vec!["run","variant","source","asof","scenario","start"]}else{vec!["run","variant","source","asof"]};
    let optional=["symbol","lookback","signal_type","trend_level","markets","market_total","n_target_trend_confirmation_enabled","ordinary_trend_completion_enabled"];
    if p.keys().any(|k|!required.contains(&k.as_str())&&!optional.contains(&k.as_str())){return Err(ApiError::invalid("invalid signal snapshot query keys"));}
    if buy&&p.keys().any(|k|["signal_type","trend_level","markets","market_total"].contains(&k.as_str())){return Err(ApiError::invalid("buy snapshot does not accept structure filters"));}
    for key in &required{field(p,key)?;}
    let source=field(p,"source")?;let symbol=p.get("symbol").cloned();
    if !(if buy{["tdx","snapshot","akshare"].contains(&source)}else{["tdx","akshare"].contains(&source)})
        ||((buy||refresh)&&((source=="akshare")!=symbol.is_some()))||(symbol.is_some()&&source!="akshare"){return Err(ApiError::invalid("invalid source or single-stock AkShare scope"));}
    let asof=field(p,"asof")?;parse_day(asof)?;
    if let Some(start)=p.get("start"){parse_day(start)?;if start.as_str()>asof{return Err(ApiError::invalid("start exceeds asof"));}}
    let lookback=p.get("lookback").map_or(Ok(20),|s|s.parse::<u64>().map_err(|_|ApiError::invalid("lookback must be 1, 5 or 20")))?;
    if ![1,5,20].contains(&lookback){return Err(ApiError::invalid("lookback must be 1, 5 or 20"));}
    let level=p.get("trend_level").map_or(Ok(0),|s|s.parse::<u64>().map_err(|_|ApiError::invalid("trend level must be 0..3")))?;
    if level>3{return Err(ApiError::invalid("trend level must be 0..3"));}
    let signal_type=p.get("signal_type").map_or("any",String::as_str);
    if signal_type!="any"&&!LANDMARKS.contains(&signal_type){return Err(ApiError::invalid("invalid structure signal type"));}
    let markets=if let Some(raw)=p.get("markets"){
        let values=raw.split(',').collect::<Vec<_>>();let unique=values.iter().copied().collect::<BTreeSet<_>>();
        if values.is_empty()||unique.len()!=values.len()||values.iter().any(|v|!MARKETS.contains(v)){return Err(ApiError::invalid("invalid structure markets"));}
        MARKETS.iter().filter(|v|unique.contains(**v)).map(|s|(*s).into()).collect::<Vec<String>>()
    }else{MARKETS[..3].iter().map(|s|(*s).into()).collect()};
    let n_target=boolean(p,"n_target_trend_confirmation_enabled")?;let ordinary=boolean(p,"ordinary_trend_completion_enabled")?;
    let mut params=wavequant_quant_rs::indexed_json::to_value(p)?;params["lookback"]=json!(lookback);params["n_target_trend_confirmation_enabled"]=json!(n_target);params["ordinary_trend_completion_enabled"]=json!(ordinary);
    if !buy{params["trend_level"]=json!(level);params["signal_type"]=json!(signal_type);params["markets"]=json!(markets.join(","));if let Some(object)=params.as_object_mut(){object.remove("symbol");}}
    Ok(Request{run:field(p,"run")?.into(),variant:field(p,"variant")?.into(),source:source.into(),asof:asof.into(),symbol,
        scenario:p.get("scenario").cloned(),start:p.get("start").cloned(),lookback,signal_type:signal_type.into(),trend_level:level,markets,n_target,ordinary,params})
}
impl Request {
    fn query<'a>(&'a self,algorithm:&'a str,data:Option<&'a str>)->SignalQuery<'a>{SignalQuery{run_id:&self.run,variant:&self.variant,source:&self.source,asof:&self.asof,
        algorithm_version:algorithm,scope_symbol:self.symbol.as_deref(),data_version:data,scenario:self.scenario.as_deref(),start:self.start.as_deref()}}
}
impl SignalSnapshots {
    pub fn new(repository:Arc<ResearchRepository>,infrastructure:Arc<Infrastructure>,backtests:Arc<BacktestService>,jobs:BacktestJobs,engine:String)->Self{Self{repository,infrastructure,backtests,jobs,engine,cancellation:None}}
    fn database(&self)->Result<&SnapshotStore>{self.infrastructure.database.as_deref().ok_or_else(||ApiError::unavailable("信号读模型未配置；请配置数据库并运行预计算 Worker"))}
    fn algorithm(&self,r:&Request,buy:bool)->Result<String>{
        if !buy{return version(&json!({"engine":self.engine,"consumer":"rust-native-structure-landmarks-v1","n_target_trend_confirmation_enabled":r.n_target,"ordinary_trend_completion_enabled":r.ordinary}));}
        let config=self.repository.strategy_config(&r.run,&r.variant,ProfileOptions{n_target_trend_confirmation_enabled:r.n_target,ordinary_trend_completion_enabled:r.ordinary,..Default::default()})?;
        let execution=config.scenarios.get(r.scenario.as_deref().ok_or_else(||ApiError::invalid("scenario required"))?).and_then(|v|v.get("execution")).ok_or_else(||ApiError::invalid("unknown execution scenario"))?;
        version(&json!(["rust-native-buy-signal-v1",self.engine,config.strategy,execution]))
    }
    pub fn query_buy(&self,params:&BTreeMap<String,String>)->Result<Value>{
        let r=request(params,true,false)?;let algorithm=self.algorithm(&r,true)?;
        let snapshot=self.database()?.find_signal(SignalFamily::Buy,r.query(&algorithm,None))?
            .ok_or_else(||ApiError::unavailable("该行情日、股票范围或当前算法版本尚无完成买点快照"))?;
        let key=format!("signal:buy:v1:{}:{}",snapshot.snapshot_id,r.lookback);
        self.infrastructure.cached_json(&key,||{
            let rows=snapshot.payload.get("results").and_then(Value::as_array).ok_or_else(||ApiError::invalid("买点快照结果格式无效"))?;
            let mut matches=rows.iter().filter(|row|number(row,"session_age").is_some_and(|age|age<=r.lookback)).cloned().collect::<Vec<_>>();
            matches.sort_by(|a,b|(text(b,"signal_date"),number(b,"priority").unwrap_or(0),text(b,"symbol")).cmp(&(text(a,"signal_date"),number(a,"priority").unwrap_or(0),text(a,"symbol"))));
            let mut result=response(&snapshot,&r.params,"ready",matches);result["notice"]=json!("结果由后台按行情与算法版本预计算；次开盘仍须经过执行风控。");
            result["funnel"]=snapshot.payload.get("funnel").cloned().unwrap_or_else(||json!({}));result["performance"]=snapshot.payload.get("performance").cloned().unwrap_or_else(||json!({}));Ok(result)
        })
    }
    pub fn query_structure(&self,params:&BTreeMap<String,String>)->Result<Value>{
        let r=request(params,false,false)?;let algorithm=self.algorithm(&r,false)?;let database=self.database()?;
        let (snapshot,status,current_published,expected,rejected_current)=if r.source=="akshare"{
            let generations=database.list_structure_snapshot_generations(&r.run,&r.source,&r.asof,500,r.n_target,r.ordinary)?;
            let current=generations.iter().find(|g|text(g,"asof")==r.asof&&text(g,"algorithm_version")==algorithm);
            let legacy=generations.iter().filter(|g|g.get("market_total").is_none_or(Value::is_null)).filter_map(|g|number(g,"published_stocks")).max().unwrap_or(0);
            let historical=generations.iter().filter_map(|g|number(g,"market_total").or_else(||number(g,"published_stocks"))).max().unwrap_or(0);
            let published=current.and_then(|g|number(g,"published_stocks")).unwrap_or(0);
            let expected=current.and_then(|g|number(g,"market_total")).filter(|v|*v>0).unwrap_or(historical.max(published));
            let complete=current.is_some()&&published>=expected;let healthy=complete&&current.is_some_and(healthy_generation);
            let mut fallbacks=generations.iter().filter(|g|number(g,"published_stocks").unwrap_or(0)>=number(g,"market_total").filter(|v|*v>0).unwrap_or(legacy)&&healthy_generation(g)).collect::<Vec<_>>();
            fallbacks.sort_by(|a,b|(text(b,"algorithm_version")==algorithm,text(b,"asof"),text(b,"updated_at")).cmp(&(text(a,"algorithm_version")==algorithm,text(a,"asof"),text(a,"updated_at"))));
            let selected=if healthy{current}else{fallbacks.first().copied()};
            let shards=if let Some(g)=selected{database.list_structure_snapshots(&r.run,None,&r.source,text(g,"asof"),text(g,"algorithm_version"),20000)?}
                else if current.is_some()&&!complete{database.list_structure_snapshots(&r.run,None,&r.source,&r.asof,&algorithm,20000)?}else{vec![]};
            if shards.is_empty(){return pending_structure(&r,&algorithm);}
            (aggregate(&r,&shards)?,if healthy{"ready"}else{"rebuilding"},published,expected,complete&&!healthy)
        }else{
            let snapshot=database.find_signal(SignalFamily::Structure,r.query(&algorithm,None))?.ok_or_else(||ApiError::unavailable("该行情日或当前算法版本尚无完成结构快照"))?;
            let total=snapshot.total;(snapshot,"ready",total,total,false)
        };
        let key=format!("signal:structure:v5:{}:{}:{}:{}:{}:{}:{}:{}",r.n_target,snapshot.snapshot_id,current_published,expected,r.signal_type,r.trend_level,r.lookback,r.markets.join("-"));
        self.infrastructure.cached_json(&key,||{
            let rows=snapshot.payload.get("results").and_then(Value::as_array).ok_or_else(||ApiError::invalid("结构快照结果格式无效"))?;
            let mut matches=rows.iter().filter(|row|market(text(row,"symbol")).is_some_and(|m|r.markets.iter().any(|s|s==m))
                &&!text(row,"name").contains('*')&&!text(row,"name").contains('＊')&&(r.signal_type=="any"||text(row,"signal_type")==r.signal_type)
                &&(r.trend_level==0||number(row,"trend_level")==Some(r.trend_level))&&number(row,"session_age").is_some_and(|age|age<=r.lookback)).cloned().collect::<Vec<_>>();
            matches.sort_by(|a,b|(text(b,"available_at"),number(b,"trend_level"),text(b,"event_date"),text(b,"symbol"),text(b,"id"))
                .cmp(&(text(a,"available_at"),number(a,"trend_level"),text(a,"event_date"),text(a,"symbol"),text(a,"id"))));
            let matched=matches.iter().map(|r|text(r,"symbol")).collect::<BTreeSet<_>>().len();
            let mut result=response(&snapshot,&r.params,status,matches);result["snapshot"]["requested_asof"]=json!(r.asof);
            result["snapshot"]["is_fallback"]=json!(snapshot.asof!=r.asof||snapshot.algorithm_version!=algorithm);result["matched_stocks"]=json!(matched);
            result["coverage"]=json!({"scope":if r.source=="akshare"{"akshare_market"}else{"tdx_market"},"published_stocks":snapshot.total,"building_stocks":current_published,"expected_stocks":expected});
            result["filters"]=json!({"markets":r.markets,"excluded_name_markers":["*","＊"]});
            result["notice"]=json!(if rejected_current{"当前目标快照的行情日期一致性校验失败，继续提供上一份完整健康读模型。"}else if status=="rebuilding"{"新结构快照正在后台重建，当前提供上一份完整读模型。"}else{"结果由后台按行情与算法版本预计算；结构出现不构成买卖建议。"});Ok(result)
        })
    }
}
fn response(snapshot:&SignalSnapshot,params:&Value,status:&str,rows:Vec<Value>)->Value{json!({"status":status,"params":params,
    "snapshot":{"id":snapshot.snapshot_id,"asof":snapshot.asof,"computed_at":snapshot.updated_at.as_ref().or(snapshot.created_at.as_ref()),"algorithm_version":snapshot.algorithm_version,"data_version":snapshot.data_version},
    "total":snapshot.total,"processed":snapshot.total,"skipped":snapshot.skipped,"failed":snapshot.failed,"stale":snapshot.payload.get("stale").unwrap_or(&Value::Null),
    "skip_reasons":snapshot.payload.get("skip_reasons").cloned().unwrap_or_else(||json!({})),"errors":snapshot.payload.get("errors").cloned().unwrap_or_else(||json!([])),"results":rows})}
fn market(symbol:&str)->Option<&'static str>{let s=symbol.to_ascii_lowercase();let(exchange,code)=s.split_once('.')?;
    match exchange{"bj"=>Some("beijing"),"sh"=>Some(if code.starts_with("688")||code.starts_with("689"){"star"}else{"shanghai"}),
        "sz"=>Some(if code.starts_with("300")||code.starts_with("301"){"chinext"}else{"shenzhen"}),_=>None}}
fn healthy_generation(g:&Value)->bool{number(g,"published_stocks").zip(number(g,"stale_stocks")).is_some_and(|(published,stale)|published>0&&stale.saturating_mul(2)<published)}
fn aggregate(r:&Request,shards:&[SignalSnapshot])->Result<SignalSnapshot>{
    let mut ordered=shards.iter().collect::<Vec<_>>();ordered.sort_by(|a,b|a.scope_symbol.cmp(&b.scope_symbol));
    let mut rows=vec![];let mut errors=vec![];let mut skips:BTreeMap<String,u64>=BTreeMap::new();let mut stale=0;
    for shard in &ordered{
        rows.extend(shard.payload.get("results").and_then(Value::as_array).cloned().ok_or_else(||ApiError::invalid("结构分片缺少结果数组"))?);
        if let Some(values)=shard.payload.get("errors").and_then(Value::as_array){errors.extend(values.clone());}
        if let Some(values)=shard.payload.get("skip_reasons").and_then(Value::as_object){for(k,v)in values{if let Some(n)=v.as_u64(){*skips.entry(k.clone()).or_default()+=n;}}}
        stale+=number(&shard.payload,"stale").unwrap_or(0);
    }errors.truncate(30);let first=ordered.first().ok_or_else(||ApiError::invalid("empty structure shard generation"))?;
    Ok(SignalSnapshot{snapshot_id:version(&json!(ordered.iter().map(|s|json!([s.scope_symbol,s.snapshot_id])).collect::<Vec<_>>()))?,run_id:r.run.clone(),variant:r.variant.clone(),source:r.source.clone(),scope_symbol:None,
        asof:first.asof.clone(),algorithm_version:first.algorithm_version.clone(),data_version:version(&json!(ordered.iter().map(|s|json!([s.scope_symbol,s.data_version])).collect::<Vec<_>>()))?,
        payload:json!({"results":rows,"errors":errors,"skip_reasons":skips,"stale":stale}),total:ordered.len() as u64,skipped:ordered.iter().map(|s|s.skipped).sum(),failed:ordered.iter().map(|s|s.failed).sum(),scenario:None,start:None,created_at:None,
        updated_at:ordered.iter().filter_map(|s|s.updated_at.as_ref().or(s.created_at.as_ref())).max().cloned()})
}
fn pending_structure(r:&Request,algorithm:&str)->Result<Value>{Ok(json!({"status":"rebuilding","params":r.params,
    "snapshot":{"id":version(&json!({"run":r.run,"source":r.source,"asof":r.asof,"algorithm":algorithm}))?,"asof":r.asof,"requested_asof":r.asof,"is_fallback":false,"computed_at":null,"algorithm_version":algorithm,"data_version":"pending"},
    "total":0,"processed":0,"skipped":0,"failed":0,"stale":0,"skip_reasons":{},"errors":[],"results":[],"matched_stocks":0,
    "coverage":{"scope":"akshare_market","published_stocks":0,"building_stocks":0,"expected_stocks":null},"filters":{"markets":r.markets,"excluded_name_markers":["*","＊"]},
    "notice":"首份结构快照尚无已发布结果，等待后台生成。"}))}
// Producer 与查询共用版本身份；完整计算之前不会发布可被查询命中的行。
use std::{path::PathBuf,time::{Instant,SystemTime,UNIX_EPOCH}};
use rust_decimal::Decimal;
use wavequant_quant_rs::{analysis::{analyzer::{AnalysisBar,AnalysisRequest,AnalyzerConfig,DataIdentity,TrendAnalyzer},kernel,
    profiles::{execution_parameters,execution_configuration,StrategyProfile},system::{run_system_backtest,run_system_signals_with_cancel,SystemConfig},trading::RuleCandle},
    data::{adjustments::{ExecutionBar,security_spec},tdx::{TdxReader,fingerprint}},execution::BacktestSession,
    analytics::{research::stock_view,screening_funnel::funnel}};
use super::{backtest::{MarketRequest,LoadedMarket},jobs::{JobReporter,JobFailure},market::OnlineMarket};
struct SourceState {stocks:Vec<Value>,version:String,skips:BTreeMap<String,u64>,files:Vec<(PathBuf,String)>,
    sealed:BTreeMap<String,Vec<ExecutionBar>>,raw:Option<Value>,not_listed:bool}
fn increment(counts:&mut BTreeMap<String,u64>,name:&str){*counts.entry(name.into()).or_default()+=1;}
fn check_flag(flag:Option<&std::sync::atomic::AtomicBool>)->Result<()>{if flag.is_some_and(|flag|flag.load(std::sync::atomic::Ordering::Acquire)){return Err(ApiError::unavailable("信号刷新已取消，未发布快照"));}Ok(())}
fn progress(reporter:Option<&JobReporter>,percent:u8,stage:&str)->Result<()>{if let Some(reporter)=reporter{reporter.progress(percent,stage)?;}Ok(())}
fn decimal(value:f64)->Result<Decimal>{if !value.is_finite(){return Err(ApiError::invalid("nonfinite market value"));}value.to_string().parse().map_err(|_|ApiError::invalid("market value exceeds decimal range"))}
fn price(v:&Value)->Option<f64>{v.as_f64().or_else(||v.as_str()?.parse().ok()).filter(|v|v.is_finite())}
fn source_error(error:impl std::fmt::Display)->ApiError{ApiError::unavailable(error.to_string())}
fn changed()->ApiError{ApiError(axum::http::StatusCode::CONFLICT,"MARKET_DATA_VERSION_CHANGED","计算期间行情或算法版本发生变化，未发布混合版本快照".into())}
fn stat_identity(path:&std::path::Path)->Result<String>{let m=std::fs::metadata(path)?;let modified=m.modified()?.duration_since(UNIX_EPOCH).map_err(|_|ApiError::invalid("invalid source modification time"))?;Ok(format!("{}:{}",m.len(),modified.as_nanos()))}
fn options(r:&Request)->ProfileOptions{ProfileOptions{n_target_trend_confirmation_enabled:r.n_target,ordinary_trend_completion_enabled:r.ordinary,..Default::default()}}
fn analysis_config(r:&Request)->AnalyzerConfig{let mut config=AnalyzerConfig::default();config.kernel.ordinary_trend_completion_enabled=r.ordinary;config.n_target_trend_confirmation_enabled=r.n_target;config}
fn candles(bars:&[ExecutionBar])->Result<Vec<RuleCandle>>{bars.iter().map(|b|Ok(RuleCandle{symbol:b.symbol.clone(),timestamp:b.timestamp.clone(),open:decimal(b.open)?,high:decimal(b.high)?,low:decimal(b.low)?,close:decimal(b.close)?,volume:decimal(b.volume)?,adjustment_factor:decimal(b.adjustment_factor)?,raw_trading_active:b.raw_trading_active,raw_is_st:b.raw_is_st})).collect()}
fn raw_bars(view:&Value)->Result<Vec<ExecutionBar>>{
    let symbol=text(view,"symbol");if symbol.is_empty(){return Err(ApiError::invalid("raw source symbol missing"));}
    view["bars"].as_array().ok_or_else(||ApiError::invalid("raw source bars missing"))?.iter().map(|b|{
        let numeric=|key:&str|price(&b[key]).ok_or_else(||ApiError::invalid(format!("raw market {key} missing")));
        let timestamp=text(b,"time").to_owned();parse_day(&timestamp)?;
        // 原始结构/在线信号模式不执行账户，这些兼容字段不构成成交权限。
        Ok(ExecutionBar{symbol:symbol.into(),timestamp,open:numeric("open")?,high:numeric("high")?,low:numeric("low")?,close:numeric("close")?,volume:numeric("volume")?,adjustment_factor:1.0,
            buyable:false,sellable:false,close_buyable:None,nonflat_close_buyable:None,close_sellable:None,nonflat_close_sellable:None,raw_trading_active:None,raw_is_st:None})
    }).collect()
}
impl SignalSnapshots {
    fn refresh_request(&self,params:&BTreeMap<String,String>,buy:bool)->Result<Request>{
        let mut params=params.clone();params.entry("source".into()).or_insert_with(||"akshare".into());
        if !params.contains_key("asof"){
            let source=field(&params,"source")?;
            let latest=if source=="snapshot"{let(_,_,report)=self.repository.frozen_snapshot(field(&params,"run")?)?;text(&report["data"],"end").to_owned()}
            else{let catalog=self.backtests.universe(source)?;
                if source=="tdx"{let mut counts=BTreeMap::<String,usize>::new();for row in catalog["stocks"].as_array().into_iter().flatten(){let last=text(row,"last");if !last.is_empty(){*counts.entry(last.into()).or_default()+=1;}}
                    counts.into_iter().max_by(|a,b|a.1.cmp(&b.1).then_with(||a.0.cmp(&b.0))).map(|(day,_)|day).ok_or_else(||ApiError::unavailable("目录没有可用交易日"))?
                }else{text(&catalog,"latest").to_owned()}};
            parse_day(&latest)?;params.insert("asof".into(),latest);
        }
        if let Some(total)=params.get("market_total") {if buy||field(&params,"source")?!="akshare"||total.parse::<u64>().ok().is_none_or(|n|n==0){return Err(ApiError::invalid("market_total only accepts a positive AkShare structure count"));}}
        request(&params,buy,true)
    }
    /// HTTP 提交只登记工作；计算和 SQL 发布在同一可取消任务中完成。
    pub fn submit_refresh(&self,kind:&str,params:BTreeMap<String,String>)->Result<String>{
        let buy=match kind{"buy"=>true,"structure"=>false,_=>return Err(ApiError::invalid("refresh kind must be buy or structure"))};
        self.database()?;let r=self.refresh_request(&params,buy)?;let algorithm=self.algorithm(&r,buy)?;
        let signature=version(&json!(["signal-refresh-v1",kind,r.params,algorithm]))?;
        let epoch=SystemTime::now().duration_since(UNIX_EPOCH).map_err(|_|ApiError::internal())?.as_nanos();
        let id=format!("signal-{}",&version(&json!([signature,epoch.to_string()]))?[..32]);
        let scope=format!("signal:{kind}:{}:{}",r.source,r.symbol.as_deref().unwrap_or("market"));
        let service=self.clone();let details=json!({"task":"signal_snapshot_refresh","kind":kind,"params":r.params,"algorithm_version":algorithm});
        self.jobs.start(id.clone(),signature,scope,details,move|reporter|service.refresh(r,buy,Some(&reporter)).map_err(|e|JobFailure{http_status:e.0.as_u16(),code:Some(e.1.into()),message:e.2}))?;Ok(id)
    }
    pub fn refresh_structure(&self,params:&BTreeMap<String,String>)->Result<Value>{self.refresh(self.refresh_request(params,false)?,false,None)}
    pub fn refresh_buy(&self,params:&BTreeMap<String,String>)->Result<Value>{self.refresh(self.refresh_request(params,true)?,true,None)}
    fn raw_view(&self,r:&Request,symbol:&str,asof:&str)->Result<Value>{
        match r.source.as_str(){"tdx"=>super::market::view(self.backtests.tdx_root.as_deref().ok_or_else(||ApiError::unavailable("通达信目录未配置"))?,symbol,asof,"1d"),
            "akshare" if self.backtests.online_enabled=>OnlineMarket::new(self.backtests.timeout).view(symbol,asof,"1d"),_=>Err(ApiError::unavailable("raw market source unavailable"))}
    }
    fn source_state(&self,r:&Request,buy:bool,reporter:Option<&JobReporter>)->Result<SourceState>{
        self.check_cancelled(reporter)?;
        if r.source=="snapshot"{
            let(rows,version,report)=self.repository.frozen_snapshot(&r.run)?;
            if text(&report["data"],"end")<r.asof.as_str(){return Err(ApiError::invalid("asof exceeds sealed snapshot end"));}
            let sealed=super::sealed::parse(rows)?;
            let stocks=sealed.keys().map(|symbol|json!({"symbol":symbol,"name":null})).collect();
            return Ok(SourceState{stocks,version,skips:BTreeMap::new(),files:vec![],sealed,raw:None,not_listed:false});
        }
        let catalog=self.backtests.universe(&r.source)?;
        let stocks=catalog["stocks"].as_array().ok_or_else(||ApiError::unavailable("market catalog stocks missing"))?;
        if r.source=="akshare"{
            let symbol=r.symbol.as_deref().ok_or_else(||ApiError::invalid("AkShare symbol required"))?;
            let stock=stocks.iter().find(|s|text(s,"symbol")==symbol).cloned().ok_or_else(||ApiError::invalid("unknown online stock"))?;
            let raw=match self.raw_view(r,symbol,&r.asof){Ok(value)=>value,Err(error)=>{
                let latest=text(&catalog,"latest");if buy||latest<=r.asof.as_str(){return Err(error);}
                let future=self.raw_view(r,symbol,latest)?;let first=future["bars"].as_array().and_then(|v|v.first()).map(|v|text(v,"time")).unwrap_or("");
                if first<=r.asof.as_str(){return Err(error);}
                return Ok(SourceState{stocks:vec![stock],version:version(&json!(["not_listed_asof",symbol,r.asof,first]))?,skips:BTreeMap::from([("not_listed_asof".into(),1)]),files:vec![],sealed:BTreeMap::new(),raw:None,not_listed:true});
            }};
            return Ok(SourceState{stocks:vec![stock],version:text(&raw,"data_version").to_owned(),skips:BTreeMap::new(),files:vec![],sealed:BTreeMap::new(),raw:Some(raw),not_listed:false});
        }
        let root=self.backtests.tdx_root.as_deref().ok_or_else(||ApiError::unavailable("通达信目录未配置"))?;
        let reader=TdxReader::new(root).map_err(source_error)?;
        let mut selected=vec![];let mut skips=BTreeMap::new();let mut files=vec![];
        for stock in stocks{
            self.check_cancelled(reporter)?;let symbol=text(stock,"symbol");
            let skip=if buy&&security_spec(symbol,&r.asof).is_err(){Some("unsupported_board")}
                else if stock["has_data"]!=true{Some("no_daily")}
                else if buy&&(text(stock,"name").to_uppercase().contains("ST")||text(stock,"name").contains('退')){Some("st_or_delisted")}
                else if text(stock,"last")<r.asof.as_str(){Some("stale_daily")}else{None};
            if let Some(reason)=skip{increment(&mut skips,reason);continue;}
            let path=reader.day_path(symbol).map_err(source_error)?;files.push((path.clone(),stat_identity(&path)?));selected.push(stock.clone());
        }
        let actions=if buy{Some(fingerprint(&root.join("T0002/hq_cache/gbbq")).map_err(source_error)?)}else{None};
        let minute_versions=if buy {selected.iter().map(|stock| {let symbol=text(stock,"symbol");let(market,code)=symbol.split_once('.').ok_or_else(||ApiError::invalid("invalid stock symbol"))?;let path=root.join("vipdoc").join(market).join("fzline").join(format!("{market}{code}.lc5"));Ok(json!([symbol,if path.exists(){Some(stat_identity(&path)?)}else{None}]))}).collect::<Result<Vec<_>>>()?}else{vec![]};
        let data=version(&json!(["tdx-signal-source-v1",r.asof,selected,skips,files,actions,minute_versions]))?;
        Ok(SourceState{stocks:selected,version:data,skips,files,sealed:BTreeMap::new(),raw:None,not_listed:false})
    }
    fn refresh(&self,r:Request,buy:bool,reporter:Option<&JobReporter>)->Result<Value>{
        self.database()?;let started=Instant::now();progress(reporter,2,"固定行情与算法版本")?;
        let profile=self.repository.strategy_config(&r.run,&r.variant,options(&r))?;
        let engine=self.repository.backtest_version(&r.run,&r.variant,&self.engine)?;
        let algorithm=self.algorithm(&r,buy)?;let mut source=self.source_state(&r,buy,reporter)?;
        let family=if buy{SignalFamily::Buy}else{SignalFamily::Structure};
        if let Some(existing)=self.database()?.find_signal(family,r.query(&algorithm,Some(&source.version)))?{return Ok(refresh_result(&existing,"current"));}
        let mut rows=vec![];let mut errors=vec![];let mut failed=0u64;let mut stale=0u64;let mut gates=json!({});
        let total=source.stocks.len()as u64+source.skips.values().sum::<u64>()-u64::from(source.not_listed);
        let stocks=source.stocks.clone();
        for (index,stock) in stocks.iter().enumerate(){
            self.check_cancelled(reporter)?;if source.not_listed{break;}
            let symbol=text(stock,"symbol");let phase=if buy{"计算买点与执行证据"}else{"计算共享趋势地标"};
            progress(reporter,5+((index*85)/stocks.len().max(1))as u8,&format!("{phase} {symbol}"))?;
            let result=(||->Result<()> {
                let stem=symbol.replace('.',"");
                if let Some((path,before))=source.files.iter().find(|(path,_)|path.file_stem().is_some_and(|s|s.to_string_lossy()==stem)) {if stat_identity(path)?!=*before{return Err(changed());}}
                if buy{
                    let view=self.buy_view(&r,symbol,&profile,&mut source,reporter)?;
                    let(match_,skip)=buy_match(&view,&r.asof,20)?;let stock_gates=funnel(&view,20).map_err(ApiError::invalid)?;
                    if let Some(row)=match_{rows.push(row);}if skip{stale+=1;}merge_counts(&mut gates,&stock_gates);
                }else{
                    let view=if let Some(raw)=&source.raw{raw.clone()}else{self.raw_view(&r,symbol,&r.asof)?};
                    let(mut matched,is_stale)=structure_rows(&r,stock,&view,reporter,self.cancellation.as_deref())?;rows.append(&mut matched);if is_stale{stale+=1;}
                }Ok(())
            })();
            if let Err(error)=result{self.check_cancelled(reporter)?;if matches!(error.1,"MARKET_DATA_VERSION_CHANGED"|"SIGNAL_RULES_INCOMPLETE"){return Err(error);}failed+=1;if errors.len()<30{errors.push(json!({"symbol":symbol,"error":error.2}));}}
        }
        progress(reporter,94,"确认版本一致并原子发布")?;self.check_cancelled(reporter)?;
        let after=self.source_state(&r,buy,reporter)?;
        if after.version!=source.version||self.algorithm(&r,buy)?!=algorithm||self.repository.backtest_version(&r.run,&r.variant,&self.engine)?!=engine{return Err(changed());}
        rows.sort_by(|a,b|(text(b,if buy{"signal_date"}else{"available_at"}),text(b,"symbol"),text(b,"id")).cmp(&(text(a,if buy{"signal_date"}else{"available_at"}),text(a,"symbol"),text(a,"id"))));
        let skipped=source.skips.values().sum();
        let mut payload=json!({"results":rows,"errors":errors,"skip_reasons":source.skips,"stale":stale,
            "n_target_trend_confirmation_enabled":r.n_target,"ordinary_trend_completion_enabled":r.ordinary,
            "funnel":gates,"performance":{"elapsed_seconds":started.elapsed().as_secs_f64(),"engine":"rust_native"}});
        if let Some(total)=number(&r.params,"market_total"){payload["market_total"]=json!(total);}
        let snapshot=SignalSnapshot{snapshot_id:version(&json!(["rust-signals-v1",buy,r.run,r.variant,r.source,r.symbol,r.asof,r.scenario,r.start,algorithm,source.version]))?,run_id:r.run,variant:r.variant,source:r.source,scope_symbol:r.symbol,asof:r.asof,
            algorithm_version:algorithm,data_version:source.version,payload,total,skipped,failed,scenario:if buy{r.scenario}else{None},start:if buy{r.start}else{None},created_at:None,updated_at:None};
        self.check_cancelled(reporter)?;let published=if buy{self.database()?.save_buy_signal_snapshot(snapshot)?}else{self.database()?.save_structure_snapshot(snapshot)?};
        progress(reporter,99,"快照已发布")?;Ok(refresh_result(&published,"published"))
    }
    fn buy_view(&self,r:&Request,symbol:&str,profile:&StrategyProfile,source:&mut SourceState,reporter:Option<&JobReporter>)->Result<Value>{
        let mut config=analysis_config(r);config.trading=profile.strategy.clone();
        if r.source=="akshare"{
            let mut view=source.raw.clone().ok_or_else(||ApiError::invalid("online raw source missing"))?;
            let bars=candles(&raw_bars(&view)?)?;
            let flag=reporter.map(JobReporter::cancellation_flag).or_else(||self.cancellation.clone());
            let result=run_system_signals_with_cancel(&bars,DataIdentity{symbol:symbol.into(),source_version:source.version.clone(),adjustment:"raw_unadjusted".into(),timeframe:"1d".into()},config,flag.as_deref()).map_err(ApiError::invalid)?;
            if !result.analysis.complete{return Err(ApiError(axum::http::StatusCode::SERVICE_UNAVAILABLE,"SIGNAL_RULES_INCOMPLETE",format!("买点规则尚未完整可用: {}",result.analysis.unavailable_rules.join(","))));}
            view["signals"]=json!(result.signals);view["audit"]=json!(result.audit);view["run_id"]=json!(r.run);view["variant"]=json!(r.variant);
            view["backtest"]=json!({"source":{"provider":"akshare","upstream":"sina","mode":"raw_signal_only","data_version":source.version},"status":"signals_only"});return Ok(view);
        }
        let scenario=r.scenario.as_deref().ok_or_else(||ApiError::invalid("scenario required"))?;
        let mut profile=profile.clone();
        let original=execution_parameters(&profile,scenario).map_err(ApiError::invalid)?;
        let security=security_spec(symbol,&r.asof).map_err(source_error)?;
        let mut a=original.lot_size;let mut b=security.buy_share_step as usize;while b!=0{let rem=a%b;a=b;b=rem;}
        let lot=original.lot_size.checked_div(a).and_then(|v|v.checked_mul(security.buy_share_step as usize)).ok_or_else(||ApiError::invalid("lot size overflow"))?;
        let execution=profile.scenarios.get_mut(scenario).and_then(|v|v.get_mut("execution")).ok_or_else(||ApiError::invalid("unknown execution scenario"))?;
        execution["lot_size"]=json!(lot);execution["minimum_entry_shares"]=json!(original.minimum_entry_shares.max(security.minimum_buy_shares as usize));
        let parameters=execution_parameters(&profile,scenario).map_err(ApiError::invalid)?;
        let(execution,holding)=execution_configuration(&profile,scenario).map_err(ApiError::invalid)?;
        let mut loaded=if r.source=="snapshot"{
            let mut bars=source.sealed.remove(symbol).ok_or_else(||ApiError::invalid("sealed stock missing"))?;bars.retain(|bar|bar.timestamp<=r.asof);
            LoadedMarket{bars,minutes:BTreeMap::new(),version:source.version.clone(),provenance:json!({"provider":"sealed_snapshot","run_id":r.run,"data_sha256":source.version}),files:vec![],missing:vec![]}
        }else{self.backtests.load_market(&MarketRequest{source:r.source.clone(),symbol:symbol.into(),start:r.start.clone().ok_or_else(||ApiError::invalid("start required"))?,asof:r.asof.clone()},parameters.staged_exit_intraday||parameters.consolidation_entry_intraday,parameters.missing_minute_daily_fallback,reporter.map(JobReporter::cancellation_flag).or_else(||self.cancellation.clone()).as_ref())?};
        let sessions=loaded.bars.iter().cloned().map(|daily|{let minutes=loaded.minutes.remove(&daily.timestamp);let present=minutes.is_some();BacktestSession{daily,source:r.source.clone(),data_version:loaded.version.clone(),minutes,minute_source:present.then(||r.source.clone()),minute_data_version:present.then(||loaded.version.clone())}}).collect::<Vec<_>>();
        let result=run_system_backtest(&sessions,SystemConfig{analyzer:config,execution,holding,parameters,trading_start:r.start.clone(),trading_end:Some(r.asof.clone())},reporter.map(JobReporter::cancellation_flag).or_else(||self.cancellation.clone()),|_|{}).map_err(ApiError::invalid)?;
        self.check_cancelled(reporter)?;
        if !result.unavailable_rules.is_empty(){return Err(ApiError(axum::http::StatusCode::SERVICE_UNAVAILABLE,"SIGNAL_RULES_INCOMPLETE",format!("买点规则尚未完整可用: {}",result.unavailable_rules.join(","))));}
        for(path,expected)in &loaded.files{if fingerprint(path).map_err(source_error)?!=*expected{return Err(changed());}}
        let bars=result.bars.get(symbol).ok_or_else(||ApiError::invalid("native system did not return stock bars"))?;
        let mut view=stock_view(&result.account,bars,&result.signals,&result.audit,&json!({"run_id":r.run,"variant":r.variant,"scenario":r.scenario,"asof":r.asof}),None).map_err(ApiError::invalid)?;
        view["backtest"]["source"]=loaded.provenance;view["backtest"]["minute_fallbacks"]=json!(result.minute_fallbacks);Ok(view)
    }
}
fn structure_rows(r:&Request,stock:&Value,view:&Value,reporter:Option<&JobReporter>,cancel:Option<&std::sync::atomic::AtomicBool>)->Result<(Vec<Value>,bool)>{
    let source=raw_bars(view)?;
    if source.last().is_none_or(|b|b.timestamp!=r.asof){return Ok((vec![],true));}
    let bars=candles(&source)?;
    let input=source.iter().enumerate().map(|(index,b)|Ok(AnalysisBar{index,time:b.timestamp.clone(),open:b.open,high:b.high,low:b.low,close:b.close,volume:decimal(b.volume)?,adjustment_factor:Decimal::ONE,raw_trading_active:None,raw_is_st:None})).collect::<Result<Vec<_>>>()?;
    let mut analyzer=TrendAnalyzer::new(AnalysisRequest{identity:DataIdentity{symbol:text(stock,"symbol").into(),source_version:text(view,"data_version").into(),adjustment:"raw_unadjusted".into(),timeframe:"1d".into()},bars:input,config:analysis_config(r),state:None}).map_err(ApiError::invalid)?;
    loop{check_flag(cancel)?;if let Some(reporter)=reporter{reporter.check_cancelled()?;}if !analyzer.process_next().map_err(ApiError::invalid)?{break;}}
    let asof=bars.len().checked_sub(1).ok_or_else(||ApiError::invalid("empty raw market"))?;
    let fields=[("bear_to_bull","bear_to_bull_highs"),("bear_bull_alternation","bear_bull_alternation_lows"),("bullish_turn","bullish_turn_signals")];
    let mut rows=vec![];
    for frame in analyzer.levels().iter().skip(1){
        let group=kernel::project_landmarks(frame,&bars,&analyzer.trading_state().events,asof).map_err(ApiError::invalid)?;
        for(signal_type,field)in fields{
            for landmark in group[field].as_array().into_iter().flatten(){
                if ["id","time","kind","value","available_at"].iter().any(|key|landmark.get(*key).is_none_or(Value::is_null)){continue;}
                let Some(position)=bars.iter().position(|b|b.timestamp==text(landmark,"available_at"))else{continue;};
                let age=bars.len()-position;if age>20{continue;}
                rows.push(json!({"id":landmark["id"],"symbol":stock["symbol"],"name":stock["name"],"signal_type":signal_type,
                    "trend_level":frame.level,"event_date":landmark["time"],"available_at":landmark["available_at"],"session_age":age,
                    "label":landmark["label"],"kind":landmark["kind"],"value":landmark["value"],"price_basis":"raw_unadjusted","evidence":landmark}));
            }
        }
    }
    rows.sort_by(|a,b|(text(b,"available_at"),text(b,"event_date"),number(b,"trend_level"),text(b,"id")).cmp(&(text(a,"available_at"),text(a,"event_date"),number(a,"trend_level"),text(a,"id"))));
    Ok((rows,false))
}
/// Python buy_scanner.buy_match：持仓中的 LONG 只是移动防守，不是新入场。
fn buy_match(view:&Value,asof:&str,lookback:usize)->Result<(Option<Value>,bool)>{
    let bars=view["bars"].as_array().ok_or_else(||ApiError::invalid("signal view bars missing"))?;
    if bars.last().is_none_or(|bar|text(bar,"time")!=asof){return Ok((None,true));}
    let window=bars.iter().rev().take(lookback).map(|b|text(b,"time")).collect::<BTreeSet<_>>();
    let signals=view["signals"].as_array().ok_or_else(||ApiError::invalid("signal view signals missing"))?;
    let orders=view["orders"].as_array().ok_or_else(||ApiError::invalid("signal view orders missing"))?;
    for signal in signals.iter().rev(){
        let when=text(signal,"timestamp");let day=when.get(..10).unwrap_or("");
        if text(signal,"side")!="LONG"||!window.contains(day){continue;}
        let mut held=false;
        for order in orders{if text(order,"timestamp")>when{break;}if text(order,"status")=="filled"{held=text(order,"side")=="BUY";}}
        if held{continue;}
        let related=orders.iter().filter(|o|text(o,"side")=="BUY"&&text(o,"signal_timestamp")==when).collect::<Vec<_>>();
        let fill=related.iter().copied().find(|o|text(o,"status")=="filled");
        let reject=related.iter().copied().rev().find(|o|text(o,"status")=="cancelled");
        let invalid=signals.iter().any(|s|text(s,"side")=="EXIT"&&text(s,"timestamp")>when);
        let status=if fill.is_some(){"filled"}else if reject.is_some(){"rejected"}else if invalid{"invalidated"}else if day==asof{"awaiting_next_open"}else{"historical_unfilled"};
        if lookback==1&&status!="awaiting_next_open"{continue;}
        let position=bars.iter().position(|b|text(b,"time")==day).ok_or_else(||ApiError::invalid("signal lacks its market candle"))?;
        let reference=price(&signal["reference_price"]).ok_or_else(||ApiError::invalid("signal reference price missing"))?;
        let stop=price(&signal["invalidation_price"]).ok_or_else(||ApiError::invalid("signal stop price missing"))?;
        let factor=price(&bars[position]["factor"]).filter(|f|*f>0.0).ok_or_else(||ApiError::invalid("signal adjustment factor missing"))?;
        let target=price(&signal["target_price"]);let risk=reference-stop;
        let evidence=view["audit"].as_array().into_iter().flatten().filter(|e|text(e,"timestamp")==when&&matches!(text(e,"event"),"long_signal"|"long_transition_evidence")).cloned().collect::<Vec<_>>();
        let proof=evidence.iter().find(|e|text(e,"event")=="long_transition_evidence").cloned().unwrap_or_else(||json!({}));
        return Ok((Some(json!({"symbol":view["symbol"],"signal_date":day,"status":status,"regime":signal["regime"],
            "reference_price":reference,"raw_reference_price":reference/factor,"stop":stop,"target":target,"rvol":price(&signal["rvol"]),"retracement":price(&signal["retracement"]),
            "gross_reward_risk":target.filter(|_|risk>0.0).map(|target|(target-reference)/risk),"reason":signal["reason"],
            "execution_reason":fill.or(reject).map(|o|&o["reason"]),"fill_date":fill.map(|o|text(o,"timestamp").get(..10).unwrap_or("")),
            "evidence":evidence,"buy_point_type":proof["buy_point_type"],"priority":number(&proof,"priority").unwrap_or(0),"trend_level":proof["trend_level"],
            "price_basis":view["price_basis"],"run_id":view["run_id"],"source":view["backtest"]["source"],"data_source":view["data_source"],
            "resolved_source":view["resolved_source"],"providers":view.get("providers").cloned().unwrap_or_else(||json!([])),"supplemented_bars":number(view,"supplemented_bars").unwrap_or(0),"asof":asof,"session_age":bars.len()-position})),false));
    }Ok((None,false))
}
fn merge_counts(target:&mut Value,addition:&Value){
    if let Some(map)=addition.as_object(){if !target.is_object(){*target=json!({});}for(key,value)in map{if value.is_object(){merge_counts(&mut target[key],value);}else if let Some(value)=value.as_u64(){target[key]=json!(target[key].as_u64().unwrap_or(0)+value);}}}
}
fn refresh_result(snapshot:&SignalSnapshot,status:&str)->Value{json!({"status":status,"snapshot_id":snapshot.snapshot_id,"source":snapshot.source,"symbol":snapshot.scope_symbol,
    "asof":snapshot.asof,"algorithm_version":snapshot.algorithm_version,"data_version":snapshot.data_version,"computed_at":snapshot.updated_at.as_ref().or(snapshot.created_at.as_ref()),
    "total":snapshot.total,"matches":snapshot.payload["results"].as_array().map_or(0,Vec::len),"skipped":snapshot.skipped,"failed":snapshot.failed})}
impl SignalSnapshots {
    fn check_cancelled(&self,reporter:Option<&JobReporter>)->Result<()>{check_flag(self.cancellation.as_deref())?;if let Some(reporter)=reporter{reporter.check_cancelled()?;}Ok(())}
    /// CLI/Worker 的退出旗标贯穿读取、每个前缀及 SQL 发布前的最后边界。
    pub fn refresh_with_cancel(&self,kind:&str,params:&BTreeMap<String,String>,cancel:Arc<std::sync::atomic::AtomicBool>)->Result<Value>{
        let mut service=self.clone();service.cancellation=Some(cancel);service.check_cancelled(None)?;
        match kind{"structure"=>service.refresh_structure(params),"buy"=>service.refresh_buy(params),_=>Err(ApiError::invalid("refresh kind must be buy or structure"))}
    }
    pub fn structure_algorithm_version(&self,n_target:bool,ordinary:bool)->Result<String>{version(&json!({"engine":self.engine,"consumer":"rust-native-structure-landmarks-v1","n_target_trend_confirmation_enabled":n_target,"ordinary_trend_completion_enabled":ordinary}))}
}
