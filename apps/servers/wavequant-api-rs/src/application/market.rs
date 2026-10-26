use chrono::{Datelike, NaiveDate, Utc, Weekday};
use wavequant_quant_rs::indexed_json::{Value,json};
use std::{collections::{BTreeSet,VecDeque}, path::Path, sync::Mutex, time::{Duration,Instant}};
use wavequant_quant_rs::data::{tdx::{TdxReader,MarketData,DataError},akshare::AkShareProvider};
use crate::presentation::error::ApiError;
use super::research::digest;
type Result<T> = std::result::Result<T,ApiError>;

pub fn parse_day(raw:&str)->Result<NaiveDate> {
    NaiveDate::parse_from_str(raw,"%Y-%m-%d").ok().filter(|d|d.format("%Y-%m-%d").to_string()==raw).ok_or_else(||ApiError::invalid("use YYYY-MM-DD date"))
}
fn bucket(day:NaiveDate,timeframe:&str)->(i32,u32,u32) {
    match timeframe {"1w"=>(day.iso_week().year(),day.iso_week().week(),0),"1mo"=>(day.year(),day.month(),0),"3mo"=>(day.year(),(day.month()-1)/3+1,0),"1y"=>(day.year(),0,0),_=>(day.year(),day.month(),day.day())}
}
fn period_end(day:NaiveDate,timeframe:&str)->bool {
    if timeframe=="1w" {return day.weekday()==Weekday::Fri;}
    if timeframe=="1d" {return true;}
    if (timeframe=="3mo" && ![3,6,9,12].contains(&day.month())) || (timeframe=="1y"&&day.month()!=12) {return false;}
    let Some(mut last)=day.with_day(31).or_else(||day.with_day(30)).or_else(||day.with_day(29)).or_else(||day.with_day(28)) else {return false;};
    while matches!(last.weekday(),Weekday::Sat|Weekday::Sun) {let Some(previous)=last.pred_opt() else {return false;};last=previous;}
    day==last
}
pub fn view(root:&Path,symbol:&str,asof:&str,timeframe:&str)->Result<Value> {
    let reader=TdxReader::new(root.to_path_buf()).map_err(provider_error)?;
    let full=reader.bars(symbol,"9999-12-31").map_err(provider_error)?;
    render("tdx", &full, asof, timeframe)
}

pub(crate) fn render(source:&str,full:&MarketData,asof:&str,timeframe:&str)->Result<Value> {
    parse_day(asof)?;
    let symbol=&full.symbol;
    let label=match timeframe {"1d"=>"日线","1w"=>"周线","1mo"=>"月线","3mo"=>"季线","1y"=>"年线",_=>return Err(ApiError::invalid("周期仅支持 1d、1w、1mo、3mo 或 1y"))};
    let prefix=full.bars.iter().filter(|row|row.date.as_str()<=asof).collect::<Vec<_>>();
    let last=*prefix.last().ok_or_else(||ApiError::invalid("该回放日期之前没有日线数据"))?;
    let last_day=parse_day(&last.date)?;
    let mut bars:Vec<Value>=Vec::new();
    let mut active=None;
    for row in prefix {
        let key=bucket(parse_day(&row.date)?,timeframe);
        if active==Some(key) {
            let current=bars.last_mut().ok_or_else(ApiError::internal)?;
            current["time"]=json!(row.date);current["high"]=json!(current["high"].as_f64().ok_or_else(ApiError::internal)?.max(row.high));current["low"]=json!(current["low"].as_f64().ok_or_else(ApiError::internal)?.min(row.low));current["close"]=json!(row.close);current["raw_close"]=json!(row.close);current["volume"]=json!(current["volume"].as_f64().ok_or_else(ApiError::internal)?+row.volume as f64);
        } else {bars.push(json!({"time":row.date,"open":row.open,"high":row.high,"low":row.low,"close":row.close,"raw_close":row.close,"volume":row.volume as f64,"factor":1.0}));active=Some(key);}
    }
    let mut sessions=Vec::<NaiveDate>::new();
    for row in &full.bars {let date=parse_day(&row.date)?;if sessions.last().is_some_and(|last|bucket(*last,timeframe)==bucket(date,timeframe)){sessions.pop();}sessions.push(date);}
    let last_bucket=bucket(last_day,timeframe);
    let final_session=sessions.iter().copied().find(|day|bucket(*day,timeframe)==last_bucket);
    let partial=timeframe!="1d" && (final_session.is_some_and(|date|date>last_day) || (full.bars.last().is_some_and(|row|row.date==last.date)&&!period_end(last_day,timeframe)));
    if final_session.is_some_and(|date|date>last_day) {
        let mut known=bars.iter().map(|bar|parse_day(bar["time"].as_str().unwrap_or(""))).collect::<Result<Vec<_>>>()?;
        known.extend(sessions.into_iter().filter(|date|bucket(*date,timeframe)>last_bucket));sessions=known;
    }
    let identity=bars.iter().map(|bar|json!([bar["time"],bar["open"],bar["high"],bar["low"],bar["close"],bar["volume"]])).collect::<Vec<_>>();
    let version=digest(&wavequant_quant_rs::indexed_json::to_vec(&identity)?);
    Ok(json!({"symbol":symbol,"asof":last.date,"requested_asof":asof,"source_policy":"single_upstream_v1","result_scope":source,"data_source":source,"resolved_source":source,"providers":[source],"supplemented_bars":0,"source_fallback":false,"source_warning":null,"data_version":version,"price_basis":"raw_unadjusted","fetched_at":Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Secs,true),"sessions":sessions.iter().map(ToString::to_string).collect::<Vec<_>>(),"bars":bars,"markers":[],"signals":[],"orders":[],"trades":[],"curve":[],"metrics":null,"evidence":if source=="tdx"{"通达信原始不复权行情；未执行回测，除权缺口可能影响形态。"}else{"新浪原始不复权行情（Rust 原生同源读取）；未执行回测，除权缺口可能影响形态。"},"timeframe":timeframe,"timeframe_label":label,"is_partial_last_bar":partial,"timeframe_source":if timeframe=="1d"{"provider_daily"}else{"server_aggregated_from_daily"}}))
}
pub fn page(mut view:Value,year:Option<&str>,version:Option<&str>)->Result<Value> {
    let actual=view["data_version"].as_str().ok_or_else(ApiError::internal)?.to_owned();
    if let Some(version)=version {if version.len()!=64 || !version.bytes().all(|c|c.is_ascii_digit()||(b'a'..=b'f').contains(&c)) {return Err(ApiError::invalid("data_version must be a lowercase SHA256 digest"));}if actual!=version {return Err(ApiError(axum::http::StatusCode::CONFLICT,"MARKET_DATA_VERSION_CHANGED","行情数据版本已变化，请重新加载最新年度".into()));}}
    let bars=view["bars"].as_array().ok_or_else(ApiError::internal)?.clone();
    let years=bars.iter().map(|bar|parse_day(bar["time"].as_str().unwrap_or("")).map(|d|d.year())).collect::<Result<BTreeSet<_>>>()?;
    let available=years.into_iter().rev().collect::<Vec<_>>();
    let selected=match year {None=>*available.first().ok_or_else(ApiError::internal)?,Some(value)=>{if version.is_none()||value.len()!=4||!value.bytes().all(|b|b.is_ascii_digit()){return Err(ApiError::invalid("year pages require a four-digit year and first page data_version"));}let year=value.parse::<i32>().map_err(|_|ApiError::invalid("invalid year"))?;let asof=parse_day(view["requested_asof"].as_str().unwrap_or(""))?;if !(1900..=asof.year()).contains(&year){return Err(ApiError::invalid("year outside requested date"));}year}};
    let next=available.iter().copied().find(|year|*year<selected);
    let selected_prefix=selected.to_string();
    view["bars"]=json!(bars.iter().filter(|bar|bar["time"].as_str().is_some_and(|s|s.starts_with(&selected_prefix))).collect::<Vec<_>>());
    view["sessions"]=json!(view["sessions"].as_array().ok_or_else(ApiError::internal)?.iter().filter(|session|session.as_str().is_some_and(|s|s.starts_with(&selected_prefix))).collect::<Vec<_>>());
    Ok(json!({"schema_version":1,"source":view["resolved_source"],"symbol":view["symbol"],"requested_asof":view["requested_asof"],"resolved_asof":view["asof"],"timeframe":view["timeframe"],"data_version":actual,"year":selected,"next_year":next,"has_more":next.is_some(),"available_years":available,"history_start":bars.first().map(|bar|&bar["time"]),"history_end":bars.last().map(|bar|&bar["time"]),"total_bars":bars.len(),"view":view}))
}

fn provider_error(error:DataError)->ApiError {
    match error {
        DataError::Invalid(message)=>ApiError::invalid(message),
        DataError::Changed=>ApiError(axum::http::StatusCode::CONFLICT,"MARKET_DATA_VERSION_CHANGED","行情正在更新，请重新加载".into()),
        DataError::Unavailable(message)=>ApiError::unavailable(message),
        DataError::Io(_)=>ApiError::unavailable("行情源暂不可用"),
    }
}

/// 只缓存已校验同源数据；不在 Sina、TDX 与 BaoStock 之间补齐或回退。
pub struct OnlineMarket {
    timeout:Duration,
    histories:Mutex<VecDeque<(String,Instant,MarketData)>>,
    catalog:Mutex<Option<(Instant,Value)>>,
    sdk:Mutex<Option<AkShareProvider>>,
    catalog_store:Option<wavequant_quant_rs::persistence::ArtifactCache>,
}
impl OnlineMarket {
    pub fn new(timeout:Duration)->Self {Self{timeout,histories:Mutex::new(VecDeque::new()),catalog:Mutex::new(None),sdk:Mutex::new(None),catalog_store:None}}
    pub fn with_catalog_cache(mut self,root:Option<&Path>)->Self{
        self.catalog_store=root.and_then(|root|wavequant_quant_rs::persistence::ArtifactCache::new(&root.join(".rust-market-catalog-cache"),16*1024*1024).ok()).map(|mut cache|{cache.read_timeout=Duration::from_millis(250);cache.max_uncompressed_bytes=8*1024*1024;cache});self
    }
    fn provider(&self)->Result<AkShareProvider>{let mut sdk=self.sdk.lock().map_err(|_|ApiError::internal())?;if sdk.is_none(){*sdk=Some(AkShareProvider::new(self.timeout).map_err(provider_error)?);}sdk.clone().ok_or_else(ApiError::internal)}
    pub fn decorate(&self,view:&mut Value,upstream_version:&str)->Result<()>{
        let version=self.provider()?.version().map_err(provider_error)?;
        view["provider_version"]=json!(format!("akshare-python/{version}"));view["sdk_version"]=json!(version);
        let index=view["symbol"].as_str().is_some_and(wavequant_quant_rs::data::indices::is_index);
        let symbol=view["symbol"].as_str().unwrap_or("").to_owned();
        view["upstream"]=json!(if index{wavequant_quant_rs::data::indices::upstream(&symbol)}else{"sina"});view["history_endpoint"]=json!(if index{wavequant_quant_rs::data::indices::endpoint(&symbol)}else{"stock_zh_a_daily"});view["transport"]=json!("python_sdk_process");
        view["upstream_version"]=json!(upstream_version);
        view["evidence"]=json!("AKShare Python SDK / 新浪原始不复权行情；未执行回测，除权缺口可能影响形态。");
        if index{view["evidence"]=json!(format!("AKShare Python SDK / {}指数日线；指数点位不作个股复权，仅供行情及趋势分析。",if wavequant_quant_rs::data::indices::upstream(&symbol)=="sina"{"新浪"}else{"东方财富"}));}
        view["data_version"]=json!(digest(&wavequant_quant_rs::indexed_json::to_vec(&json!(["akshare-sdk-market-v1",view["data_version"],upstream_version]))?));Ok(())
    }
    pub fn view(&self,symbol:&str,asof:&str,timeframe:&str)->Result<Value> {
        let today=(Utc::now()+chrono::Duration::hours(8)).date_naive();
        if parse_day(asof)?>today{return Err(ApiError::invalid("AkShare 回放日期不能晚于今天"));}
        if symbol.starts_with("bj.")&&!wavequant_quant_rs::data::indices::is_index(symbol){return Err(ApiError::unavailable("当前统一行情源为新浪，暂不支持北交所历史行情"));}
        let full=self.history(symbol,&today.to_string())?;
        let mut view=render("akshare",&full,asof,timeframe)?;
        self.decorate(&mut view,&full.version)?;
        Ok(view)
    }
    pub(crate) fn daily(&self,symbol:&str,asof:&str)->Result<MarketData>{
        let today=(Utc::now()+chrono::Duration::hours(8)).date_naive();if parse_day(asof)?>today{return Err(ApiError::invalid("AkShare 回放日期不能晚于今天"));}if symbol.starts_with("bj.")&&!wavequant_quant_rs::data::indices::is_index(symbol){return Err(ApiError::unavailable("当前统一行情源为新浪，暂不支持北交所历史行情"));}self.history(symbol,&today.to_string())
    }
    fn history(&self,symbol:&str,today:&str)->Result<MarketData> {
        {
            let mut cache=self.histories.lock().map_err(|_|ApiError::internal())?;
            if let Some(position)=cache.iter().position(|(key,loaded,_)|key==symbol&&loaded.elapsed()<Duration::from_secs(300)) {
                let entry=cache.remove(position).ok_or_else(ApiError::internal)?;
                let data=entry.2.clone();cache.push_back(entry);return Ok(data);
            }
        }
        let provider=self.provider()?;
        let data=if wavequant_quant_rs::data::indices::is_index(symbol){provider.index_daily(symbol,today)}else{provider.daily(symbol,today)}.map_err(provider_error)?;
        if data.bars.len()>20000{return Err(ApiError::unavailable("新浪返回日线记录数量异常"));}
        if data.bars.windows(2).any(|rows|rows[0].date>=rows[1].date)||data.bars.iter().any(|bar|[bar.open,bar.high,bar.low,bar.close].iter().any(|value|!value.is_finite()||*value<=0.0)||bar.low>bar.open.min(bar.close)||bar.high<bar.open.max(bar.close)) {return Err(ApiError::unavailable("新浪返回日线价格或日期异常"));}
        let mut cache=self.histories.lock().map_err(|_|ApiError::internal())?;
        cache.retain(|(key,_,_)|key!=symbol);cache.push_back((symbol.into(),Instant::now(),data.clone()));
        while cache.len()>32{cache.pop_front();}Ok(data)
    }
    pub fn catalog(&self)->Result<Value> {
        // 目录独立合并请求；不让多个页面同时重复下载整个股票目录并阻塞行情管道。
        let mut memory=self.catalog.lock().map_err(|_|ApiError::internal())?;
        if let Some((loaded,value))=memory.as_ref(){if loaded.elapsed()<Duration::from_secs(3600){return Ok(value.clone());}}
        let now=Utc::now();let today=(now+chrono::Duration::hours(8)).date_naive().to_string();
        let identity=json!({"schema":1,"source":"akshare-sdk-catalog","china_day":today,"python":std::env::var("WAVEQUANT_AKSHARE_PYTHON").ok()});
        if let Some(entry)=self.catalog_store.as_ref().and_then(|cache|cache.get("market-catalog",&identity)){
            let created=entry["created_at"].as_i64();let expires=entry["expires_at"].as_i64();
            if let (Some(created),Some(expires))=(created,expires){
                let value=&entry["catalog"];
                if created<=now.timestamp()&&expires>now.timestamp()&&expires-created==3600&&value["stocks"].as_array().is_some_and(|rows|!rows.is_empty()&&rows.len()<=20000)&&value["sdk_version"].as_str().is_some_and(|v|!v.is_empty()){
                    // 内存恢复原始年龄，重启和命中均不延长目录有效期。
                    let loaded=Instant::now().checked_sub(Duration::from_secs((now.timestamp()-created) as u64)).unwrap_or_else(Instant::now);
                    *memory=Some((loaded,value.clone()));
                    tracing::info!(message="market_catalog_cache",cache_hit=true,backend="disk");return Ok(value.clone());
                }
            }
        }
        let started=Instant::now();let value=self.provider()?.catalog(now).map_err(provider_error)?;
        let stored=self.catalog_store.as_ref().is_some_and(|cache|cache.put("market-catalog",&identity,&json!({"created_at":now.timestamp(),"expires_at":now.timestamp()+3600,"catalog":value})));
        *memory=Some((started,value.clone()));
        tracing::info!(message="market_catalog_cache",cache_hit=false,persisted=stored,load_ms=started.elapsed().as_secs_f64()*1000.0);
        Ok(value)
    }
}
