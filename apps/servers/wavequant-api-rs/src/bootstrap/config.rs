use std::{collections::BTreeSet, io, net::SocketAddr, path::PathBuf};

#[derive(Clone,Copy,PartialEq,Eq)]
pub enum AdminAction {IndexRuns,RefreshStructures,WatchStructures,RefreshSignals,WatchSignals,RefreshTimeframes,WatchTimeframes}
impl AdminAction {pub fn watching(self)->bool{matches!(self,Self::WatchStructures|Self::WatchSignals|Self::WatchTimeframes)}}
#[derive(Clone)]
pub struct WorkerOptions {
    pub run:Option<String>,pub variant:String,pub asof:Option<String>,pub interval:u64,
    pub family:String,pub source:String,pub symbols:Vec<String>,pub scenario:String,pub start:String,pub shard_count:u8,pub shard_index:u8,
    pub timeframe_source:String,pub timeframe_symbols:Vec<String>,pub timeframe_asof:Option<String>,pub timeframe_shard_count:u8,pub timeframe_shard_index:u8,
    pub n_target:Option<bool>,pub ordinary:Option<bool>,
}
impl Default for WorkerOptions {fn default()->Self{Self{run:None,variant:"lecture_v1".into(),asof:None,interval:300,family:"all".into(),source:"akshare".into(),symbols:vec![],scenario:"base".into(),start:"2018-01-01".into(),shard_count:1,shard_index:0,timeframe_source:"akshare".into(),timeframe_symbols:vec![],timeframe_asof:None,timeframe_shard_count:1,timeframe_shard_index:0,n_target:None,ordinary:None}}}
#[derive(Clone)]
pub struct Config {
    pub listen: SocketAddr,
    pub action:Option<AdminAction>,pub worker:WorkerOptions,pub api_only:bool,pub record_backtest_diagnostics:bool,
    pub quant_worker:Option<PathBuf>,pub quant_workers:usize,
    pub watchlist_refresh_enabled:bool,
    pub root: Option<PathBuf>,
    pub tdx_root: Option<PathBuf>,
    pub web_root: Option<PathBuf>,
    pub web_url: Option<String>,
    pub allowed_origins: BTreeSet<String>,
    pub online_enabled: bool,
    pub check_infrastructure: bool,
    pub init_database: bool,
    pub upstream_timeout: std::time::Duration,
}

impl Config {
    pub fn help() -> &'static str {
        r#"WaveQuant Rust API
用法: wavequant-api-rs [选项]

服务:
  --listen ADDRESS                   回环监听地址，默认 127.0.0.1:8010
  --port PORT                        覆盖监听端口
  --root PATH                        研究目录，默认 WAVEQUANT_RESULTS_ROOT
  --tdx-root PATH                    TDX 根目录
  --web-root PATH                    静态站点目录
  --api-only                         关闭静态站点
  --web-url URL                      根路径跳转的回环 HTTP 地址
  --allow-origin ORIGIN              允许的回环 HTTP Origin，可重复
  --disable-akshare                  关闭 AKShare Python SDK 在线行情
  --akshare-timeout SECONDS           上游超时，默认 30，范围 1..60
  --quant-worker PATH                原生 quant CLI 的绝对可执行文件路径
  --quant-workers COUNT              子进程数，默认 2，范围 1..8
  --record-backtest-diagnostics       保存实际逐日计算步骤
  --no-watchlist-refresh              本进程不自动提交自选回测，保留已有设置

管理动作（互斥）:
  --check-infrastructure             读取已配置基础设施健康状态
  --init-database                    初始化研究数据库 schema
  --index-runs                       索引封存研究
  --refresh-structures               刷新结构快照
  --watch-structures                 周期刷新结构快照
  --refresh-signals                  刷新信号快照
  --watch-signals                    周期刷新信号快照
  --refresh-timeframes               预计算周期快照
  --precompute-timeframes            --refresh-timeframes 的别名
  --watch-timeframes                 周期刷新周期快照

刷新参数:
  --structure-run ID                 默认最近完成的封存研究
  --structure-variant ID             默认 lecture_v1
  --structure-asof YYYY-MM-DD        默认来源最新交易日
  --structure-refresh-interval N     默认 300 秒，范围 60..86400
  --signal-family all|buy|structure   默认 all
  --signal-source tdx|snapshot|akshare 默认 akshare
  --signal-symbol SYMBOL             股票范围，可重复
  --signal-scenario ID               默认 base
  --signal-start YYYY-MM-DD          默认 2018-01-01
  --signal-shard-count N             默认 1，范围 1..16
  --signal-shard-index N             默认 0，必须小于分片总数
  --timeframe-source akshare|tdx      默认 akshare
  --timeframe-symbol SYMBOL          股票范围，可重复
  --timeframe-asof YYYY-MM-DD         默认来源最新交易日
  --timeframe-shard-count N          默认 1，范围 1..16
  --timeframe-shard-index N          默认 0，必须小于分片总数
  --n-target-trend-confirmation-enabled [true|false]
  --ordinary-trend-completion-enabled [true|false]
                                     单独标志为 true，省略则生成两种配置
  -h, --help                         显示帮助，不启动服务或访问数据

level 0/1/2/3 是同一 K 线周期内的折线及一/二/三级趋势。
timeframe 1d/1w/1mo/3mo/1y 分别是日/周/月/季/年；每种周期均计算四级。
更多接口与来源边界见 apps/servers/wavequant-api-rs/README.md。
"#
    }

    pub fn from_args(args: impl IntoIterator<Item = String>) -> io::Result<Self> {
        let mut config = Self {
            listen: "127.0.0.1:8010".parse().map_err(|_| invalid("invalid default listen address"))?,
            root: std::env::var_os("WAVEQUANT_RESULTS_ROOT").map(PathBuf::from),
            check_infrastructure: false, init_database: false, online_enabled: true, upstream_timeout: std::time::Duration::from_secs(30),
            action:None,worker:WorkerOptions::default(),api_only:false,record_backtest_diagnostics:false,
            quant_worker:None,quant_workers:2,watchlist_refresh_enabled:true,tdx_root: None, web_root: None, web_url: None, allowed_origins: BTreeSet::new(),
        };
        let mut args = args.into_iter().peekable();
        while let Some(flag) = args.next() {
            let action=match flag.as_str(){"--index-runs"=>Some(AdminAction::IndexRuns),"--refresh-structures"=>Some(AdminAction::RefreshStructures),"--watch-structures"=>Some(AdminAction::WatchStructures),"--refresh-signals"=>Some(AdminAction::RefreshSignals),"--watch-signals"=>Some(AdminAction::WatchSignals),"--refresh-timeframes"|"--precompute-timeframes"=>Some(AdminAction::RefreshTimeframes),"--watch-timeframes"=>Some(AdminAction::WatchTimeframes),_=>None};
            if let Some(action)=action{if config.action.replace(action).is_some(){return Err(invalid("select only one administration action"));}continue;}
            if flag=="--api-only"{config.api_only=true;continue;}
            if flag=="--record-backtest-diagnostics"{config.record_backtest_diagnostics=true;continue;}
            if flag=="--no-watchlist-refresh"{config.watchlist_refresh_enabled=false;continue;}
            if matches!(flag.as_str(),"--n-target-trend-confirmation-enabled"|"--ordinary-trend-completion-enabled"){
                let value=if args.peek().is_some_and(|next|!next.starts_with("--")){match args.next().as_deref(){Some("true")=>true,Some("false")=>false,_=>return Err(invalid("trend option must be true or false"))}}else{true};
                let target=if flag=="--n-target-trend-confirmation-enabled"{&mut config.worker.n_target}else{&mut config.worker.ordinary};if target.replace(value).is_some(){return Err(invalid("duplicate trend option"));}continue;
            }
            if flag == "--check-infrastructure" { config.check_infrastructure = true; continue; }
            if flag == "--init-database" { config.init_database = true; continue; }
            if flag == "--disable-akshare" { config.online_enabled = false; continue; }
            let value = args.next().ok_or_else(|| invalid("option requires a value"))?;
            match flag.as_str() {
                "--akshare-timeout" => {
                    let seconds = value.parse::<f64>().map_err(|_| invalid("invalid upstream timeout"))?;
                    if !seconds.is_finite() || !(1.0..=60.0).contains(&seconds) { return Err(invalid("upstream timeout must be 1..60 seconds")); }
                    config.upstream_timeout = std::time::Duration::from_secs_f64(seconds);
                }
                "--structure-run"=>config.worker.run=Some(value),
                "--structure-variant"=>config.worker.variant=value,
                "--structure-asof"=>config.worker.asof=Some(value),
                "--structure-refresh-interval"=>config.worker.interval=value.parse().map_err(|_|invalid("invalid refresh interval"))?,
                "--signal-family"=>config.worker.family=value,
                "--signal-source"=>config.worker.source=value,
                "--signal-symbol"=>config.worker.symbols.push(value),
                "--signal-scenario"=>config.worker.scenario=value,
                "--signal-start"=>config.worker.start=value,
                "--signal-shard-count"=>config.worker.shard_count=value.parse().map_err(|_|invalid("invalid signal shard count"))?,
                "--signal-shard-index"=>config.worker.shard_index=value.parse().map_err(|_|invalid("invalid signal shard index"))?,
                "--timeframe-source"=>config.worker.timeframe_source=value,
                "--timeframe-symbol"=>config.worker.timeframe_symbols.push(value),
                "--timeframe-asof"=>config.worker.timeframe_asof=Some(value),
                "--timeframe-shard-count"=>config.worker.timeframe_shard_count=value.parse().map_err(|_|invalid("invalid timeframe shard count"))?,
                "--timeframe-shard-index"=>config.worker.timeframe_shard_index=value.parse().map_err(|_|invalid("invalid timeframe shard index"))?,
                "--quant-worker"=>config.quant_worker=Some(PathBuf::from(value)),
                "--quant-workers"=>config.quant_workers=value.parse().map_err(|_|invalid("invalid quant workers"))?,
                "--listen" => config.listen = value.parse().map_err(|_| invalid("invalid listen address"))?,
                "--port" => config.listen.set_port(value.parse().map_err(|_| invalid("invalid port"))?),
                "--root" => config.root = Some(PathBuf::from(value)),
                "--tdx-root" => config.tdx_root = Some(PathBuf::from(value)),
                "--web-root" => config.web_root = Some(PathBuf::from(value)),
                "--web-url" => config.web_url = Some(format!("{}/", loopback_origin(&value)?)),
                "--allow-origin" => { config.allowed_origins.insert(loopback_origin(&value)?); }
                _ => return Err(invalid("unknown option")),
            }
        }
        if u8::from(config.check_infrastructure)+u8::from(config.init_database)+u8::from(config.action.is_some())>1 { return Err(invalid("select only one infrastructure action")); }
        if !config.listen.ip().is_loopback() || config.listen.port() == 0 {
            return Err(invalid("require a loopback address and a nonzero port"));
        }
        if !(1..=8).contains(&config.quant_workers)||config.quant_worker.as_ref().is_some_and(|path|!path.is_absolute()){return Err(invalid("quant workers require 1..8 and an absolute executable path"));}
        let worker=&config.worker;
        if !(60..=86400).contains(&worker.interval)||!(1..=16).contains(&worker.shard_count)||worker.shard_index>=worker.shard_count||!(1..=16).contains(&worker.timeframe_shard_count)||worker.timeframe_shard_index>=worker.timeframe_shard_count{return Err(invalid("invalid worker interval or shard bounds"));}
        if !matches!(worker.family.as_str(),"all"|"buy"|"structure")||!matches!(worker.source.as_str(),"tdx"|"snapshot"|"akshare")||!matches!(worker.timeframe_source.as_str(),"tdx"|"akshare"){return Err(invalid("invalid worker family or source"));}
        if (worker.shard_count,worker.shard_index)!=(1,0)&&(worker.source!="akshare"||!worker.symbols.is_empty()||worker.family!="structure"){return Err(invalid("signal sharding requires complete AkShare structure catalog"));}
        if config.api_only{config.web_root=None;}
        Ok(config)
    }
}

fn loopback_origin(value: &str) -> io::Result<String> {
    let origin = value.strip_suffix('/').unwrap_or(value);
    let authority = origin.strip_prefix("http://").ok_or_else(|| invalid("Web origin must use HTTP"))?;
    let (host, port) = authority.rsplit_once(':').ok_or_else(|| invalid("Web origin requires an explicit port"))?;
    if !matches!(host, "127.0.0.1" | "localhost" | "[::1]") || port.parse::<u16>().ok().filter(|p| *p > 0).is_none() {
        return Err(invalid("Web origin must be a loopback HTTP origin with an explicit port"));
    }
    Ok(origin.to_owned())
}

fn invalid(message: &'static str) -> io::Error { io::Error::new(io::ErrorKind::InvalidInput, message) }
