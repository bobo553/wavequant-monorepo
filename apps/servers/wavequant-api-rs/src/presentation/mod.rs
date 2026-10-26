use crate::presentation::indexed_response::Json;
use std::{collections::BTreeMap, sync::Arc};
use axum::{ Router, body::Body, extract::{DefaultBodyLimit, RawQuery, State}, http::{HeaderMap, HeaderValue, Method, StatusCode, Uri, header}, middleware::{self, Next}, response::{IntoResponse, Response}, routing::{get,post}};
use wavequant_quant_rs::indexed_json::{Value,json};
use tokio::sync::Semaphore;
use crate::{application::{market,research::{ResearchRepository,digest},users}, bootstrap::Config, storage::watchlists::{WatchlistStore,normalize_settings}};
use wavequant_quant_rs::data::tdx::TdxReader;

pub mod error;
pub mod analysis;
mod docs;

use error::ApiError;

#[derive(Clone)]
pub struct AppState {
    pub config:Config,
    pub computation:Arc<crate::application::computation::Computation>,
    pub watchlists:Option<Arc<WatchlistStore>>,
    pub research:Option<Arc<ResearchRepository>>,
    pub sealed:Option<Arc<crate::application::sealed::SealedResearch>>,
    pub users:Option<Arc<tokio_postgres::Client>>,
    pub workers:Arc<Semaphore>,
    pub engine_version:String,
    pub backtests:Option<Arc<crate::application::backtest::BacktestService>>,
    pub watchlist_refresh:Option<Arc<crate::application::watchlist_refresh::WatchlistRefresh>>,
    pub jobs:crate::application::jobs::BacktestJobs,
    pub online:Arc<market::OnlineMarket>,
    pub timeframes:Arc<crate::application::market_timeframes::MarketTimeframes>,
    pub signals:Option<Arc<crate::application::signal_snapshots::SignalSnapshots>>,
    pub ladder:Arc<crate::application::limit_up::LimitUpLadder>,
    pub infrastructure:Arc<crate::application::infrastructure::Infrastructure>,
}

pub async fn initialize(config:Config)->Result<AppState,ApiError> {
    let computation=Arc::new(crate::application::computation::Computation::new(&config)?);
    let root=config.root.clone();
    let watchlists=match root.as_ref() {Some(root)=>Some(Arc::new(WatchlistStore::open(root.join(".watchlists.sqlite"))?)),None=>None};
    let research=root.map(|root|Arc::new(ResearchRepository::new(root)));
    let users=users::connect().await?;
    let engine_version=digest(&std::fs::read(std::env::current_exe()?)?);
    let sealed=research.as_ref().map(|repo|Arc::new(crate::application::sealed::SealedResearch::new(repo.clone(),engine_version.clone())));
    let jobs=crate::application::jobs::BacktestJobs::new(config.root.as_ref().map(|root|root.join(".rust-backtest-history.sqlite")),engine_version.clone())?;
    let infrastructure=Arc::new(crate::application::infrastructure::Infrastructure::from_env()?);
    let market_cache=Arc::new(crate::application::adjusted_market_cache::AdjustedMarketCache::from_env(infrastructure.cache.clone())?);
    let backtests=research.as_ref().map(|repository|Arc::new(crate::application::backtest::BacktestService{market_cache:market_cache.clone(),repository:repository.clone(),tdx_root:config.tdx_root.clone(),online_enabled:config.online_enabled,timeout:config.upstream_timeout,engine_version:engine_version.clone(),computation:computation.clone(),diagnostics_root:config.root.as_ref().map(|root|root.join(".calculation-steps")),record_details:config.record_backtest_diagnostics}));
    if let Some(service)=&backtests{service.start_market_refresh();}
    let online=Arc::new(market::OnlineMarket::new(config.upstream_timeout).with_catalog_cache(config.root.as_deref()));
    let ladder=Arc::new(crate::application::limit_up::LimitUpLadder::new(config.upstream_timeout));

    let timeframes=Arc::new(crate::application::market_timeframes::MarketTimeframes::new(config.tdx_root.clone(),online.clone(),config.online_enabled,infrastructure.clone(),engine_version.clone()));
    let signals=match(&research,&backtests){(Some(repo),Some(backtests))=>Some(Arc::new(crate::application::signal_snapshots::SignalSnapshots::new(repo.clone(),infrastructure.clone(),backtests.clone(),jobs.clone(),engine_version.clone()))),_=>None};
    let watchlist_refresh=match(&watchlists,&backtests){(Some(store),Some(service)) if config.watchlist_refresh_enabled=>{
        let version_service=service.clone();let submit_service=service.clone();let submit_jobs=jobs.clone();let catalog_online=online.clone();let tdx_root=config.tdx_root.clone();let online_enabled=config.online_enabled;
        let refresh=Arc::new(crate::application::watchlist_refresh::WatchlistRefresh::new(store.clone(),jobs.clone(),
            Arc::new(move|run,variant|{let value=version_service.repository.backtest_version(run,variant,&version_service.engine_version)?;value["version"].as_str().map(str::to_owned).ok_or_else(ApiError::internal)}),
            Arc::new(move|source|match source{"tdx"=>{let root=tdx_root.as_ref().ok_or_else(||ApiError::unavailable("通达信目录未配置"))?;TdxReader::new(root).and_then(|reader|reader.catalog()).map_err(|error|ApiError::invalid(error.to_string()))},"akshare" if online_enabled=>catalog_online.catalog(),_=>Err(ApiError::unavailable("行情源未配置"))}),
            Arc::new(move|request|submit_service.submit(request,&submit_jobs))));refresh.start();Some(refresh)
    },_=>None};
    Ok(AppState{config,computation,watchlists,research,sealed,users,jobs,online,timeframes,signals,ladder,infrastructure,engine_version,backtests,watchlist_refresh,workers:Arc::new(Semaphore::new(4))})
}

pub fn router(state:AppState)->Router {
    Router::new()
        .route("/health/live",get(||async{StatusCode::NO_CONTENT}))
        .route("/health",get(database_health))
        .route("/api/docs",get(docs::page))
        .route("/api/docs-json",get(docs::specification))
        .route("/api/catalog",get(catalog))
        .route("/api/research-config",get(research_config))
        .route("/api/limit-up-ladder",get(limit_up_ladder))
        .route("/api/infrastructure/health",get(infrastructure_health))
        .route("/api/health",get(research_health))
        .route("/api/view",get(research_view))
        .route("/api/stock-view",get(sealed_stock))
        .route("/api/stock-summary",get(sealed_summary))
        .route("/api/theory",get(sealed_theory))
        .route("/api/tdx-catalog",get(tdx_catalog))
        .route("/api/tdx-view",get(tdx_view))
        .route("/api/akshare-view",get(akshare_view))
        .route("/api/akshare-catalog",get(akshare_catalog))
        .route("/api/market-bars",get(market_bars))
        .route("/api/trend-bars",get(trend_bars))
        .route("/api/market-timeframe",get(market_timeframe))
        .route("/api/structure-signals",get(structure_signals))
        .route("/api/buy-signals",get(buy_signals))
        .route("/api/tdx-theory",get(tdx_theory))
        .route("/api/akshare-theory",get(akshare_theory))
        .route("/api/tdx-backtest",get(tdx_backtest))
        .route("/api/akshare-backtest",get(akshare_backtest))
        .route("/api/backtest-version",get(backtest_version))
        .route("/api/backtest-job",get(backtest_job))
        .route("/api/backtest-jobs",get(backtest_jobs))
        .route("/api/backtest-preview",get(backtest_preview))
        .route("/api/watchlists",get(watchlists_get).post(watchlists_post))
        .route("/api/v1/users",post(create_user))
        .route("/api/trend-analysis",post(analysis::analyze))
        .fallback(static_file)
        .layer(DefaultBodyLimit::max(4*1024*1024))
        .layer(tower_http::compression::CompressionLayer::new())
        .layer(middleware::from_fn_with_state(state.clone(),security))
        .layer(middleware::from_fn(response_headers))
        .with_state(state)
}

async fn security(State(state):State<AppState>,request:axum::extract::Request,next:Next)->Response {
    let port=state.config.listen.port();
    let valid=[format!("127.0.0.1:{port}"),format!("localhost:{port}"),format!("[::1]:{port}")];
    let host=request.headers().get(header::HOST).and_then(|value|value.to_str().ok());
    if !host.is_some_and(|host|valid.iter().any(|value|value==host)) {return ApiError(StatusCode::FORBIDDEN,"LOOPBACK_HOST_REQUIRED","loopback Host required".into()).into_response();}
    let origin=match request.headers().get(header::ORIGIN) {Some(value)=>match value.to_str(){Ok(value)=>Some(value.to_owned()),Err(_)=>return ApiError::invalid("invalid Origin header").into_response()},None=>None};
    if origin.as_ref().is_some_and(|origin|!valid.iter().any(|host|format!("http://{host}")==*origin)&&!state.config.allowed_origins.contains(origin)) || (!matches!(*request.method(),Method::GET|Method::HEAD)&&request.headers().get("sec-fetch-site").is_some_and(|value|value=="cross-site")) {
        return ApiError(StatusCode::FORBIDDEN,"ORIGIN_DENIED","cross-origin access denied".into()).into_response();
    }
    if request.headers().contains_key(header::TRANSFER_ENCODING) && request.method()==Method::POST {return ApiError::invalid("仅接受有确定长度的 JSON 数据").into_response();}
    let request=if request.method()==Method::POST && request.uri().path()=="/api/watchlists" {
        let content_type=request.headers().get(header::CONTENT_TYPE).and_then(|value|value.to_str().ok()).and_then(|value|value.split(';').next());
        if content_type!=Some("application/json"){return ApiError::invalid("仅接受 JSON 自选数据").into_response();}
        let length=request.headers().get(header::CONTENT_LENGTH).and_then(|value|value.to_str().ok()).and_then(|value|value.parse::<usize>().ok());
        let Some(length)=length.filter(|length|*length>0&&*length<=4*1024*1024) else{return ApiError(StatusCode::PAYLOAD_TOO_LARGE,"BODY_TOO_LARGE","自选数据大小超出限制".into()).into_response();};
        let (parts,body)=request.into_parts();
        let bytes=match tokio::time::timeout(std::time::Duration::from_secs(5),axum::body::to_bytes(body,4*1024*1024)).await {
            Ok(Ok(bytes)) if bytes.len()==length=>bytes,
            _=>return ApiError::invalid("自选数据不完整或读取超时").into_response(),
        };
        axum::extract::Request::from_parts(parts,Body::from(bytes))
    }else{request};
    let mut response=if request.method()==Method::OPTIONS {
        let mut response=StatusCode::NO_CONTENT.into_response();
        response.headers_mut().insert(header::ACCESS_CONTROL_ALLOW_METHODS,HeaderValue::from_static("GET, HEAD, POST, OPTIONS"));
        response.headers_mut().insert(header::ACCESS_CONTROL_ALLOW_HEADERS,HeaderValue::from_static("content-type, if-none-match"));
        response
    } else {match tokio::time::timeout(std::time::Duration::from_secs(90),next.run(request)).await {Ok(response)=>response,Err(_)=>ApiError(StatusCode::REQUEST_TIMEOUT,"REQUEST_TIMEOUT","请求超时".into()).into_response()}};
    let headers=response.headers_mut();
    if let Some(origin)=origin.and_then(|value|HeaderValue::from_str(&value).ok()) {headers.insert(header::ACCESS_CONTROL_ALLOW_ORIGIN,origin);headers.append(header::VARY,HeaderValue::from_static("Origin"));headers.insert(header::ACCESS_CONTROL_ALLOW_CREDENTIALS,HeaderValue::from_static("true"));}
    response
}

async fn response_headers(request:axum::extract::Request,next:Next)->Response {
    let mut response=next.run(request).await;
    let headers=response.headers_mut();
    if !headers.contains_key(header::CACHE_CONTROL) {headers.insert(header::CACHE_CONTROL,HeaderValue::from_static("no-store"));}
    headers.insert("x-content-type-options",HeaderValue::from_static("nosniff"));
    headers.insert("x-frame-options",HeaderValue::from_static("DENY"));
    headers.insert("x-dns-prefetch-control",HeaderValue::from_static("off"));
    headers.insert("x-download-options",HeaderValue::from_static("noopen"));
    headers.insert("x-permitted-cross-domain-policies",HeaderValue::from_static("none"));
    headers.insert("cross-origin-opener-policy",HeaderValue::from_static("same-origin"));
    headers.insert("cross-origin-resource-policy",HeaderValue::from_static("same-origin"));
    headers.insert("referrer-policy",HeaderValue::from_static("no-referrer"));
    headers.insert("content-security-policy",HeaderValue::from_static("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; font-src 'self'; frame-ancestors 'none'; base-uri 'none'"));
    response
}

pub async fn blocking<T,F>(state:&AppState,work:F)->Result<T,ApiError>
where T:Send+'static,F:FnOnce()->Result<T,ApiError>+Send+'static {
    let permit=state.workers.clone().try_acquire_owned().map_err(|_|ApiError(StatusCode::SERVICE_UNAVAILABLE,"CAPACITY_REACHED","服务计算容量已满，请稍后重试".into()))?;
    tokio::task::spawn_blocking(move||{let _permit=permit;work()}).await.map_err(|_|ApiError::internal())?
}
fn query(raw:Option<String>,required:&[&str],optional:&[&str])->Result<BTreeMap<String,String>,ApiError> {
    let raw=raw.unwrap_or_default();
    if raw.len()>8192 {return Err(ApiError::invalid("查询参数过长"));}
    if !raw.is_empty() && raw.split('&').any(|part|!part.contains('=')){return Err(ApiError::invalid("invalid query arguments"));}
    decode(&raw)?;
    let pairs:Vec<(String,String)>=serde_urlencoded::from_str(&raw).map_err(|_|ApiError::invalid("invalid query encoding"))?;
    let mut values=BTreeMap::new();
    for (key,value) in pairs {
        if value.is_empty() || (!required.contains(&key.as_str())&&!optional.contains(&key.as_str())) || values.insert(key,value).is_some() {return Err(ApiError::invalid("unknown, duplicate or empty query arguments"));}
    }
    if required.iter().any(|name|!values.contains_key(*name)){return Err(ApiError::invalid("required query arguments missing"));}
    Ok(values)
}
fn field<'a>(query:&'a BTreeMap<String,String>,name:&str)->Result<&'a str,ApiError> {query.get(name).map(String::as_str).ok_or_else(||ApiError::invalid("required argument missing"))}
fn repository(state:&AppState)->Result<Arc<ResearchRepository>,ApiError> {state.research.clone().ok_or_else(||ApiError::unavailable("封存研究目录未配置，请使用 --root"))}
async fn research_config(State(state):State<AppState>,RawQuery(raw):RawQuery)->Result<Json<Value>,ApiError>{query(raw,&[],&[])?;let repo=repository(&state)?;Ok(Json(blocking(&state,move||repo.configuration()).await?))}
async fn catalog(State(state):State<AppState>,RawQuery(raw):RawQuery)->Result<Json<Value>,ApiError>{query(raw,&[],&[])?;let repo=repository(&state)?;Ok(Json(blocking(&state,move||repo.catalog()).await?))}
async fn backtest_version(State(state):State<AppState>,RawQuery(raw):RawQuery)->Result<Json<Value>,ApiError>{let q=query(raw,&["run","variant"],&[])?;let repo=repository(&state)?;let engine=state.engine_version.clone();Ok(Json(blocking(&state,move||repo.backtest_version(field(&q,"run")?,field(&q,"variant")?,&engine)).await?))}
async fn research_health(State(state):State<AppState>,RawQuery(raw):RawQuery)->Result<Json<Value>,ApiError>{let q=query(raw,&["run"],&[])?;let repo=repository(&state)?;Ok(Json(blocking(&state,move||repo.health(field(&q,"run")?)).await?))}
async fn research_view(State(state):State<AppState>,RawQuery(raw):RawQuery)->Result<Json<Value>,ApiError>{let q=query(raw,&["run","variant","symbol","asof","scenario"],&[])?;let repo=repository(&state)?;Ok(Json(blocking(&state,move||repo.view(field(&q,"run")?,field(&q,"variant")?,field(&q,"symbol")?,field(&q,"asof")?,field(&q,"scenario")?)).await?))}

async fn tdx_catalog(State(state):State<AppState>,RawQuery(raw):RawQuery,headers:HeaderMap)->Result<Response,ApiError>{
    query(raw,&[],&[])?;let root=state.config.tdx_root.clone().ok_or_else(||ApiError::unavailable("通达信目录未配置"))?;
    let value=blocking(&state,move||TdxReader::new(root).and_then(|reader|reader.catalog()).map_err(|error|ApiError::invalid(error.to_string()))).await?;
    versioned(value,&headers)
}
async fn tdx_view(State(state):State<AppState>,RawQuery(raw):RawQuery)->Result<Json<Value>,ApiError>{
    let q=query(raw,&["symbol","asof"],&["timeframe"])?;let root=state.config.tdx_root.clone().ok_or_else(||ApiError::unavailable("通达信目录未配置"))?;
    Ok(Json(blocking(&state,move||market::view(&root,field(&q,"symbol")?,field(&q,"asof")?,q.get("timeframe").map_or("1d",String::as_str))).await?))
}
async fn market_bars(State(state):State<AppState>,RawQuery(raw):RawQuery,headers:HeaderMap)->Result<Response,ApiError>{
    let q=query(raw,&["source","symbol","asof","timeframe"],&["year","data_version"])?;
    let source=field(&q,"source")?;
    if !matches!(source,"tdx"|"akshare"){return Err(ApiError::invalid("source must be tdx or akshare"));}
    if source=="akshare"&&!state.config.online_enabled{return Err(ApiError::unavailable("在线行情源已禁用"));}
    let root=state.config.tdx_root.clone();let online=state.online.clone();
    let value=blocking(&state,move||{
        let view=if field(&q,"source")?=="tdx" {
            market::view(&root.ok_or_else(||ApiError::unavailable("通达信目录未配置"))?,field(&q,"symbol")?,field(&q,"asof")?,field(&q,"timeframe")?)?
        }else{online.view(field(&q,"symbol")?,field(&q,"asof")?,field(&q,"timeframe")?)?};
        market::page(view,q.get("year").map(String::as_str),q.get("data_version").map(String::as_str))
    }).await?;
    versioned(value,&headers)
}
fn versioned(value:Value,headers:&HeaderMap)->Result<Response,ApiError>{
    let mut identity=value.clone();if let Some(view)=identity.get_mut("view").and_then(Value::as_object_mut){view.remove("fetched_at");}
    let tag=format!("\"{}\"",digest(&wavequant_quant_rs::indexed_json::to_vec(&identity)?));
    let mut response=if headers.get(header::IF_NONE_MATCH).is_some_and(|header|header.as_bytes()==tag.as_bytes()){StatusCode::NOT_MODIFIED.into_response()}else{Json(value).into_response()};
    response.headers_mut().insert(header::ETAG,HeaderValue::from_str(&tag).map_err(|_|ApiError::internal())?);
    response.headers_mut().insert(header::CACHE_CONTROL,HeaderValue::from_static("private, no-cache"));Ok(response)
}

// 自选数据库I/O不与SDK和量化计算争用通用计算门禁。
async fn watchlist_io<T,F>(work:F)->Result<T,ApiError>
where T:Send+'static,F:FnOnce()->Result<T,ApiError>+Send+'static {
    static LIMIT: std::sync::OnceLock<Arc<Semaphore>> = std::sync::OnceLock::new();
    let permit=LIMIT.get_or_init(||Arc::new(Semaphore::new(4))).clone().try_acquire_owned()
        .map_err(|_|ApiError::unavailable("自选数据通道繁忙，请稍后重试"))?;
    tokio::task::spawn_blocking(move||{let _permit=permit;work()}).await.map_err(|_|ApiError::internal())?
}
async fn watchlists_get(State(state):State<AppState>,RawQuery(raw):RawQuery)->Result<Response,ApiError>{
    let params=query(raw,&[],&["cache"])?;
    let store=state.watchlists.clone().ok_or_else(||ApiError::unavailable("服务器自选存储不可用"))?;
    match params.get("cache").map(String::as_str) {
        // 首屏缓存只读，完整数据仍经fresh路径同步；缓存不参与写入版本决策。
        Some("only")=>Ok(axum::Json(store.cached_document()?).into_response()),
        Some("refresh")=>Ok(axum::Json(watchlist_io(move||Ok(store.load()?)).await?).into_response()),
        None=>Ok(Json(watchlist_io(move||Ok(store.load()?)).await?).into_response()),
        _=>Err(ApiError::invalid("自选缓存模式无效")),
    }
}
async fn watchlists_post(State(state):State<AppState>,RawQuery(raw):RawQuery,Json(body):Json<Value>)->Result<Json<Value>,ApiError>{
    query(raw,&[],&[])?;
    let store=state.watchlists.clone().ok_or_else(||ApiError::unavailable("服务器自选存储不可用"))?;
    let repo=state.research.clone();let engine=state.engine_version.clone();let refresh=state.watchlist_refresh.clone();
    Ok(Json(watchlist_io(move||{
        let result=match body["action"].as_str(){
            Some("save")=>store.save(&body["revision"],&body["snapshot"],"snapshot"),
            Some("add")=>store.update_members(&body["groupId"],&body["stocks"],false),
            Some("remove")=>store.update_members(&body["groupId"],&body["stocks"],true),
            Some("import")=>store.import_legacy(&body["token"],&body["snapshot"]),
            Some("settings")=>{
                let settings=normalize_settings(&body["settings"])?;
                let repo=repo.ok_or_else(||ApiError::unavailable("封存研究目录未配置"))?;
                let run=settings["context"]["run"].as_str().ok_or_else(||ApiError::invalid("run required"))?;
                let variant=settings["context"]["variant"].as_str().ok_or_else(||ApiError::invalid("variant required"))?;
                repo.backtest_version(run,variant,&engine)?;
                if refresh.is_none(){return Err(ApiError::unavailable("自选回测调度器未配置"));}
                store.save(&body["revision"],&settings,"settings")
            }
            Some("retry")=>{store.retry()?;store.load()},
            _=>return Err(ApiError::invalid("自选操作无效")),
        };let result=result?;if let Some(refresh)=refresh{refresh.notify();}Ok(result)
    }).await?))
}
async fn create_user(State(state):State<AppState>,Json(input):Json<users::CreateUser>)->Result<(StatusCode,Json<Value>),ApiError>{let client=state.users.as_ref().ok_or_else(||ApiError::unavailable("用户数据库未配置"))?;Ok((StatusCode::CREATED,Json(users::create(client,input).await?)))}
async fn database_health(State(state):State<AppState>)->Response {
    let Some(client)=state.users.as_ref() else {return (StatusCode::SERVICE_UNAVAILABLE,Json(json!({"status":"error","info":{},"error":{"database":{"status":"down"}},"details":{"database":{"status":"down"}}}))).into_response();};
    let healthy=matches!(tokio::time::timeout(std::time::Duration::from_secs(3),client.simple_query("SELECT 1")).await,Ok(Ok(_)));
    let status=if healthy{"up"}else{"down"};let detail=json!({"database":{"status":status}});
    (if healthy{StatusCode::OK}else{StatusCode::SERVICE_UNAVAILABLE},Json(json!({"status":if healthy{"ok"}else{"error"},"info":if healthy{detail.clone()}else{json!({})},"error":if healthy{json!({})}else{detail.clone()},"details":detail}))).into_response()
}

fn decode(raw:&str)->Result<String,ApiError>{
    let mut bytes=Vec::new();let mut iter=raw.as_bytes().iter();
    while let Some(byte)=iter.next(){if *byte==b'%' {let a=iter.next().copied().and_then(|b|(b as char).to_digit(16));let b=iter.next().copied().and_then(|b|(b as char).to_digit(16));match(a,b){(Some(a),Some(b))=>bytes.push((a*16+b) as u8),_=>return Err(ApiError::invalid("invalid URL encoding"))}}else{bytes.push(*byte);}}
    String::from_utf8(bytes).map_err(|_|ApiError::invalid("invalid UTF-8 URL"))
}
async fn static_file(State(state):State<AppState>,method:Method,uri:Uri)->Result<Response,ApiError>{
    if !matches!(method,Method::GET|Method::HEAD){return Ok((StatusCode::METHOD_NOT_ALLOWED,Json(json!({"error":"method not allowed"}))).into_response());}
    if uri.path()=="/favicon.ico"{return Ok(StatusCode::NO_CONTENT.into_response());}
    if uri.path()=="/" {if let Some(url)=state.config.web_url.as_ref(){let mut response=StatusCode::TEMPORARY_REDIRECT.into_response();response.headers_mut().insert(header::LOCATION,HeaderValue::from_str(url).map_err(|_|ApiError::internal())?);return Ok(response);}}
    let root=state.config.web_root.clone().ok_or_else(||ApiError(StatusCode::NOT_FOUND,"NOT_FOUND","not found".into()))?;
    let path=decode(uri.path())?;
    if path.starts_with("/api/")||path.contains('\\')||path.contains('\0')||path.split('/').any(|segment|segment=="..") {return Err(ApiError(StatusCode::NOT_FOUND,"NOT_FOUND","not found".into()));}
    blocking(&state,move||{
        let root=root.canonicalize()?;
        let relative=match path.as_str(){"/"=>"index.html","/research"=>"research.html","/market"=>"market.html",_=>path.trim_start_matches('/')};
        let file=root.join(relative).canonicalize().map_err(|_|ApiError(StatusCode::NOT_FOUND,"NOT_FOUND","not found".into()))?;
        if !file.starts_with(root)||!file.is_file(){return Err(ApiError(StatusCode::NOT_FOUND,"NOT_FOUND","not found".into()));}
        let mime=match file.extension().and_then(|value|value.to_str()){Some("html")=>"text/html; charset=utf-8",Some("css")=>"text/css; charset=utf-8",Some("js")=>"text/javascript; charset=utf-8",Some("json")=>"application/json",Some("svg")=>"image/svg+xml",Some("png")=>"image/png",Some("jpg"|"jpeg")=>"image/jpeg",Some("webp")=>"image/webp",Some("ico")=>"image/x-icon",Some("woff2")=>"font/woff2",_=>"application/octet-stream"};
        let bytes=std::fs::read(file)?;let length=bytes.len();
        let mut response=Response::new(if method==Method::HEAD{Body::empty()}else{Body::from(bytes)});
        response.headers_mut().insert(header::CONTENT_TYPE,HeaderValue::from_static(mime));
        response.headers_mut().insert(header::CONTENT_LENGTH,HeaderValue::from_str(&length.to_string()).map_err(|_|ApiError::internal())?);Ok(response)
    }).await
}


/// 按内容版本读取计算使用的同一份复权行情，不重新请求上游或运行趋势。
async fn trend_bars(State(state):State<AppState>,RawQuery(raw):RawQuery,headers:HeaderMap)->Result<Response,ApiError>{
    let q=query(raw,&["data_version"],&[])?;let version=field(&q,"data_version")?.to_owned();
    let root=state.research.as_ref().ok_or_else(||ApiError::unavailable("研究目录未配置"))?.root().join(".rust-computation-cache");
    let value=blocking(&state,move||{let cache=wavequant_quant_rs::persistence::ArtifactCache::new(&root,4*1024*1024*1024).map_err(|e|ApiError::invalid(e.to_string()))?;crate::application::trend_state::snapshot(&cache,&version)}).await?;
    let tag=format!("\"{}\"",value["dataVersion"].as_str().ok_or_else(ApiError::internal)?);
    let mut response=if headers.get(header::IF_NONE_MATCH).is_some_and(|h|h.as_bytes()==tag.as_bytes()){StatusCode::NOT_MODIFIED.into_response()}else{axum::Json(value).into_response()};
    response.headers_mut().insert(header::ETAG,HeaderValue::from_str(&tag).map_err(|_|ApiError::internal())?);
    response.headers_mut().insert(header::CACHE_CONTROL,HeaderValue::from_static("private, max-age=300, immutable"));Ok(response)
}
async fn submit_backtest(state:AppState,raw:Option<String>,source:&str)->Result<Response,ApiError> {
    use crate::application::backtest_request::{BacktestRequest,REQUIRED,OPTIONAL};
    let params=query(raw,&REQUIRED,&OPTIONAL)?;let request=BacktestRequest::parse(source,&params)?;
    let service=state.backtests.clone().ok_or_else(||ApiError::unavailable("封存研究执行场景未配置"))?;
    let symbol=request.symbol.clone();let normalized=wavequant_quant_rs::indexed_json::to_value(request.params())?;let path=request.path();
    let jobs=state.jobs.clone();let id=match blocking(&state,move||service.submit(request,&jobs)).await {
        Ok(id)=>id,
        Err(error) if matches!(error.1,"BACKTEST_SYMBOL_RUNNING"|"BACKTEST_CAPACITY")=>{
            let mut payload=state.jobs.snapshot()?;payload["error"]=json!(error.2);payload["code"]=json!(error.1);
            if error.1=="BACKTEST_SYMBOL_RUNNING"{if let Some(job)=payload["jobs"].as_array().and_then(|jobs|jobs.iter().find(|job|job["symbol"]==symbol)).cloned(){payload["running_job"]=job["job"].clone();payload["same_request"]=json!(job["params"]==normalized&&job["path"]==path);}}
            return Ok((error.0,Json(payload)).into_response());
        },Err(error)=>return Err(error),
    };
    let result=state.jobs.wait(&id).await?;Ok(backtest_response(true,result))
}
async fn tdx_backtest(State(state):State<AppState>,RawQuery(raw):RawQuery)->Result<Response,ApiError>{submit_backtest(state,raw,"tdx").await}
async fn akshare_backtest(State(state):State<AppState>,RawQuery(raw):RawQuery)->Result<Response,ApiError>{submit_backtest(state,raw,"akshare").await}
async fn backtest_job(State(state):State<AppState>,RawQuery(raw):RawQuery)->Result<Response,ApiError> {
    let q=query(raw,&["job"],&[])?;
    let (done,payload)=state.jobs.outcome(field(&q,"job")?)?;
    Ok((if done {StatusCode::OK} else {StatusCode::ACCEPTED},Json(payload)).into_response())
}
fn terminal_backtest_error(payload:&Value)->Value{
    let code=if payload["code"]=="BACKTEST_VERSION_CHANGED"{"BACKTEST_VERSION_CHANGED"}else{"BACKTEST_JOB_FAILED"};
    let mut error=json!({"error":payload["error"],"code":code});
    if payload["code"].is_string(){error["cause_code"]=payload["code"].clone();}error
}
fn backtest_response(done:bool,mut payload:Value)->Response {
    if !done{return (StatusCode::ACCEPTED,Json(payload)).into_response();}
    if payload["status"]=="failed" {let status=payload["http_status"].as_u64().and_then(|v|u16::try_from(v).ok()).and_then(|v|StatusCode::from_u16(v).ok()).filter(|v|v.is_client_error()||v.is_server_error()).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR);return(status,Json(terminal_backtest_error(&payload))).into_response();}
    let Some(result)=payload.as_object_mut().and_then(|value|value.remove("result")) else{return ApiError::internal().into_response();};
    (StatusCode::OK,Json(result)).into_response()
}
async fn backtest_jobs(State(state):State<AppState>,RawQuery(raw):RawQuery)->Result<Json<Value>,ApiError> {
    query(raw,&[],&[])?;
    let jobs=state.jobs.clone();let watchlists=state.watchlists.clone();let refresh=state.watchlist_refresh.clone();let computation=state.computation.clone();
    Ok(Json(blocking(&state,move||{
        let mut snapshot=jobs.snapshot()?;snapshot["computation"]=computation.statistics();if let Some(refresh)=refresh{snapshot["watchlist_refresh"]=refresh.state()?;}
        if let Some(store)=watchlists {
            let mut recent=BTreeMap::<String,Value>::new();
            let mut summaries=store.summaries()?;
            summaries.extend(snapshot["recent"].as_array().ok_or_else(ApiError::internal)?.iter().cloned());
            for item in summaries {
                let key=wavequant_quant_rs::indexed_json::to_string(&json!([item["path"],item["params"],item["version"]]))?;
                if recent.get(&key).is_some_and(|previous|previous["persisted_current"]==true)&&item["result_available"]!=true{continue;}
                recent.insert(key,item);
            }
            snapshot["recent"]=json!(recent.into_values().collect::<Vec<_>>());
        }
        Ok(snapshot)
    }).await?))
}
async fn backtest_preview(State(state):State<AppState>,RawQuery(raw):RawQuery)->Result<Json<Value>,ApiError> {
    let q=query(raw,&["job","after"],&[])?;
    let after=field(&q,"after")?.parse::<u64>().map_err(|_|ApiError::invalid("preview revision must be nonnegative"))?;
    Ok(Json(state.jobs.preview(field(&q,"job")?,after)?))
}


async fn akshare_view(State(state):State<AppState>,RawQuery(raw):RawQuery)->Result<Json<Value>,ApiError> {
    let q=query(raw,&["symbol","asof"],&["timeframe"])?;
    if !state.config.online_enabled{return Err(ApiError::unavailable("在线行情源已禁用"));}
    let online=state.online.clone();
    Ok(Json(blocking(&state,move||online.view(field(&q,"symbol")?,field(&q,"asof")?,q.get("timeframe").map_or("1d",String::as_str))).await?))
}
async fn akshare_catalog(State(state):State<AppState>,RawQuery(raw):RawQuery,headers:HeaderMap)->Result<Response,ApiError> {
    query(raw,&[],&[])?;
    if !state.config.online_enabled{return versioned(json!({"available":false,"stocks":[],"with_daily":0,"notice":"在线行情源已禁用"}),&headers);}
    let online=state.online.clone();
    versioned(blocking(&state,move||online.catalog()).await?,&headers)
}


async fn limit_up_ladder(State(state):State<AppState>,RawQuery(raw):RawQuery)->Result<Json<Value>,ApiError> {
    let q=query(raw,&[],&["date","refresh"])?;
    let refresh=match q.get("refresh").map(String::as_str){None|Some("false")=>false,Some("true")=>true,_=>return Err(ApiError::invalid("refresh 必须为 true 或 false"))};
    let service=state.ladder.clone();
    Ok(Json(blocking(&state,move||service.snapshot(q.get("date").map(String::as_str),refresh)).await?))
}

async fn infrastructure_health(State(state):State<AppState>,RawQuery(raw):RawQuery)->Result<Json<Value>,ApiError> {
    query(raw,&[],&[])?;let infrastructure=state.infrastructure.clone();let cache=state.backtests.as_ref().map(|b|b.market_cache.clone());
    Ok(Json(blocking(&state,move||{let mut value=infrastructure.health();value["adjusted_market_cache"]=cache.map(|c|c.statistics()).unwrap_or(Value::Null);Ok(value)}).await?))
}









struct CancelRequest(Arc<std::sync::atomic::AtomicBool>);
impl Drop for CancelRequest {fn drop(&mut self){self.0.store(true,std::sync::atomic::Ordering::Release);}}
fn trend_options(q:&BTreeMap<String,String>)->Result<wavequant_quant_rs::analysis::profiles::ProfileOptions,ApiError>{
    Ok(wavequant_quant_rs::analysis::profiles::ProfileOptions{
        n_target_trend_confirmation_enabled:crate::application::backtest_request::boolean(q,"n_target_trend_confirmation_enabled",false)?,
        ordinary_trend_completion_enabled:crate::application::backtest_request::boolean(q,"ordinary_trend_completion_enabled",false)?,
        buy_interval_level:2,
    })
}
async fn sealed_stock(State(state):State<AppState>,RawQuery(raw):RawQuery)->Result<Json<Value>,ApiError>{
    let q=query(raw,&["run","variant","symbol","asof","scenario"],&["n_target_trend_confirmation_enabled","ordinary_trend_completion_enabled"])?;
    let options=trend_options(&q)?;let service=state.sealed.clone().ok_or_else(||ApiError::unavailable("封存研究目录未配置"))?;
    let cancellation=Arc::new(std::sync::atomic::AtomicBool::new(false));let _cancel=CancelRequest(cancellation.clone());
    Ok(Json(blocking(&state,move||service.stock(field(&q,"run")?,field(&q,"variant")?,field(&q,"symbol")?,field(&q,"asof")?,field(&q,"scenario")?,options,Some(cancellation))).await?))
}
async fn sealed_summary(State(state):State<AppState>,RawQuery(raw):RawQuery)->Result<Json<Value>,ApiError>{
    let q=query(raw,&["run","variant","asof","scenario"],&[])?;
    let service=state.sealed.clone().ok_or_else(||ApiError::unavailable("封存研究目录未配置"))?;
    let cancellation=Arc::new(std::sync::atomic::AtomicBool::new(false));let _cancel=CancelRequest(cancellation.clone());
    Ok(Json(blocking(&state,move||service.summary(field(&q,"run")?,field(&q,"variant")?,field(&q,"asof")?,field(&q,"scenario")?,Some(cancellation))).await?))
}
async fn sealed_theory(State(state):State<AppState>,RawQuery(raw):RawQuery)->Result<Json<Value>,ApiError>{
    let q=query(raw,&["run","variant","symbol","asof"],&["n_target_trend_confirmation_enabled","ordinary_trend_completion_enabled"])?;
    let options=trend_options(&q)?;let service=state.sealed.clone().ok_or_else(||ApiError::unavailable("封存研究目录未配置"))?;
    let cancellation=Arc::new(std::sync::atomic::AtomicBool::new(false));let _cancel=CancelRequest(cancellation.clone());
    Ok(Json(blocking(&state,move||service.theory(field(&q,"run")?,field(&q,"variant")?,field(&q,"symbol")?,field(&q,"asof")?,options,Some(cancellation))).await?))
}
async fn market_timeframe(State(state):State<AppState>,RawQuery(raw):RawQuery,headers:HeaderMap)->Result<Response,ApiError>{
    let q=query(raw,&["source","symbol","asof","timeframe"],&["n_target_trend_confirmation_enabled","ordinary_trend_completion_enabled"])?;
    let options=trend_options(&q)?;let service=state.timeframes.clone();
    let cancellation=Arc::new(std::sync::atomic::AtomicBool::new(false));let _cancel=CancelRequest(cancellation.clone());
    let value=blocking(&state,move||service.bundle(field(&q,"source")?,field(&q,"symbol")?,field(&q,"asof")?,field(&q,"timeframe")?,options,true,Some(&cancellation))).await?;
    let tag=format!("\"{}\"",value["snapshot_id"].as_str().ok_or_else(ApiError::internal)?);
    let mut response=if headers.get(header::IF_NONE_MATCH).is_some_and(|header|header.as_bytes()==tag.as_bytes()){StatusCode::NOT_MODIFIED.into_response()}else{Json(value).into_response()};
    response.headers_mut().insert(header::ETAG,HeaderValue::from_str(&tag).map_err(|_|ApiError::internal())?);response.headers_mut().insert(header::CACHE_CONTROL,HeaderValue::from_static("private, no-cache"));Ok(response)
}
async fn provider_theory(state:AppState,raw:Option<String>,source:&'static str)->Result<Json<Value>,ApiError>{
    let q=query(raw,&["symbol","asof"],&["timeframe","n_target_trend_confirmation_enabled","ordinary_trend_completion_enabled"])?;
    let options=trend_options(&q)?;let service=state.timeframes.clone();let cancellation=Arc::new(std::sync::atomic::AtomicBool::new(false));let _cancel=CancelRequest(cancellation.clone());
    Ok(Json(blocking(&state,move||service.theory(source,field(&q,"symbol")?,field(&q,"asof")?,q.get("timeframe").map_or("1d",String::as_str),options,Some(&cancellation))).await?))
}
async fn tdx_theory(State(state):State<AppState>,RawQuery(raw):RawQuery)->Result<Json<Value>,ApiError>{provider_theory(state,raw,"tdx").await}
async fn akshare_theory(State(state):State<AppState>,RawQuery(raw):RawQuery)->Result<Json<Value>,ApiError>{provider_theory(state,raw,"akshare").await}
async fn structure_signals(State(state):State<AppState>,RawQuery(raw):RawQuery)->Result<Json<Value>,ApiError>{
    let q=query(raw,&["run","variant","source","asof","lookback","signal_type","trend_level"],&["symbol","markets","n_target_trend_confirmation_enabled","ordinary_trend_completion_enabled"])?;
    let service=state.signals.clone().ok_or_else(||ApiError::unavailable("结构读模型未配置"))?;
    Ok(Json(blocking(&state,move||service.query_structure(&q)).await?))
}
async fn buy_signals(State(state):State<AppState>,RawQuery(raw):RawQuery)->Result<Json<Value>,ApiError>{
    let q=query(raw,&["run","variant","scenario","source","asof","start","lookback"],&["symbol","n_target_trend_confirmation_enabled","ordinary_trend_completion_enabled"])?;
    let service=state.signals.clone().ok_or_else(||ApiError::unavailable("买点读模型未配置"))?;
    Ok(Json(blocking(&state,move||service.query_buy(&q)).await?))
}

pub mod indexed_response;

#[cfg(test)]
mod terminal_failure_tests{
    use super::*;
    #[test]
    fn failed_jobs_are_terminal_even_when_the_upstream_status_is_503(){
        let error=terminal_backtest_error(&json!({"status":"failed","http_status":503,"code":"SERVICE_UNAVAILABLE","error":"index source unavailable"}));
        assert_eq!(error["code"],"BACKTEST_JOB_FAILED");assert_eq!(error["cause_code"],"SERVICE_UNAVAILABLE");
        assert_eq!(error["error"],"index source unavailable");
        assert_eq!(terminal_backtest_error(&json!({"code":"BACKTEST_VERSION_CHANGED"}))["code"],"BACKTEST_VERSION_CHANGED");
    }
}
