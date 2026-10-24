use std::{collections::BTreeMap, path::{Path, PathBuf}};
use rusqlite::{Connection, OpenFlags};
use wavequant_quant_rs::indexed_json::{Value, json};
use sha2::{Digest, Sha256};
use crate::presentation::error::ApiError;

type Result<T> = std::result::Result<T, ApiError>;
#[derive(Clone)]
pub struct ResearchRepository { root: PathBuf }
struct Run { path: PathBuf, state: Value, manifest: Value, report: Value }

pub fn digest(bytes: &[u8]) -> String { format!("{:x}", Sha256::digest(bytes)) }
/// 同一文件版本只哈希一次；文件替换后仍重新校验运行身份。
fn executable_digest()->Result<String>{
    type Cached=(PathBuf,u64,std::time::SystemTime,String);
    static CACHE:std::sync::Mutex<Option<Cached>>=std::sync::Mutex::new(None);
    let path=std::env::current_exe()?;let metadata=std::fs::metadata(&path)?;let modified=metadata.modified()?;
    let mut cache=CACHE.lock().map_err(|_|ApiError::internal())?;
    if let Some((old_path,len,stamp,value))=cache.as_ref(){if old_path==&path&&*len==metadata.len()&&*stamp==modified{return Ok(value.clone());}}
    let value=digest(&std::fs::read(&path)?);*cache=Some((path,metadata.len(),modified,value.clone()));Ok(value)
}
/// 封存文件不变时复用已校验摘要；仍在每次读取时核对路径、版本和清单摘要。
fn sealed_file_digest(path:&Path)->Result<String>{
    type Stamp=(u64,std::time::SystemTime,Option<std::time::SystemTime>);
    static CACHE:std::sync::Mutex<BTreeMap<PathBuf,(Stamp,String)>>=std::sync::Mutex::new(BTreeMap::new());
    let stamp=|metadata:std::fs::Metadata|->Result<Stamp>{Ok((metadata.len(),metadata.modified()?,metadata.created().ok()))};
    let before=stamp(std::fs::metadata(path)?)?;
    let mut cache=CACHE.lock().map_err(|_|ApiError::internal())?;
    if let Some((old,value))=cache.get(path){if old==&before{return Ok(value.clone());}}
    let value=digest(&std::fs::read(path)?);
    if stamp(std::fs::metadata(path)?)?!=before{return Err(ApiError::invalid("sealed input changed while reading"));}
    if cache.len()>=4096&&!cache.contains_key(path){cache.pop_first();}
    cache.insert(path.to_owned(),(before,value.clone()));Ok(value)
}
fn text<'a>(value: &'a Value, key: &str) -> Result<&'a str> {
    value.get(key).and_then(Value::as_str).ok_or_else(|| ApiError::invalid("封存数据缺少必填字段"))
}
fn checked_path(root: &Path, relative: &str) -> Result<PathBuf> {
    let path = root.join(relative).canonicalize()?;
    if !path.starts_with(root) || !path.is_file() { return Err(ApiError::invalid("封存文件路径无效")); }
    Ok(path)
}
fn read_json(path: &Path) -> Result<Value> { Ok(wavequant_quant_rs::indexed_json::from_slice(&std::fs::read(path)?)?) }

impl ResearchRepository {
    pub fn new(root: PathBuf) -> Self { Self { root } }
    pub fn root(&self)->&Path{&self.root}
    fn states(&self) -> Result<(BTreeMap<String,Value>, BTreeMap<String,Value>)> {
        let db = Connection::open_with_flags(self.root.join("operations.sqlite"), OpenFlags::SQLITE_OPEN_READ_ONLY)?;
        let mut statement = db.prepare("SELECT seq,event_id,kind,event_time,payload,previous_hash,digest FROM events ORDER BY seq")?;
        let records = statement.query_map([], |row| Ok((row.get::<_,i64>(0)?,row.get::<_,String>(1)?,row.get::<_,String>(2)?,row.get::<_,String>(3)?,row.get::<_,String>(4)?,row.get::<_,String>(5)?,row.get::<_,String>(6)?)))?;
        let mut runs = BTreeMap::<String,Value>::new();
        let mut alerts = BTreeMap::<String,Value>::new();
        let mut previous = "0".repeat(64);
        for (index, record) in records.enumerate() {
            let (sequence,id,kind,time,raw,prior,expected) = record?;
            // payload 在 Python 日志中已规范化；保留原始数字编码，避免跨语言浮点重序列化改变哈希。
            let canonical = format!("[{},{},{},{},{},{}]", sequence, json!(id), json!(kind), json!(time), raw, json!(previous));
            if sequence != i64::try_from(index + 1).map_err(|_| ApiError::invalid("日志过长"))? || prior != previous || digest(canonical.as_bytes()) != expected {
                return Err(ApiError::invalid("journal hash chain integrity failure"));
            }
            previous = expected;
            let payload: Value = wavequant_quant_rs::indexed_json::from_str(&raw)?;
            match kind.as_str() {
                "RUN_STARTED" => { let mut state = payload.clone(); state["status"] = json!("RUNNING"); state["started_at"] = json!(time); state["stages"] = json!([]); runs.insert(text(&payload,"run_id")?.into(),state); }
                "RUN_STAGE" => {
                    let state = runs.get_mut(text(&payload,"run_id")?).ok_or_else(|| ApiError::invalid("日志运行顺序无效"))?;
                    let mut stage = payload.clone(); stage["at"] = json!(time);
                    state["stages"].as_array_mut().ok_or_else(|| ApiError::invalid("运行阶段无效"))?.push(stage);
                }
                "RUN_FINISHED" => {
                    let state = runs.get_mut(text(&payload,"run_id")?).ok_or_else(|| ApiError::invalid("日志运行顺序无效"))?;
                    let fields = payload.as_object().ok_or_else(|| ApiError::invalid("日志字段无效"))?;
                    for (key,value) in fields { state[key] = value.clone(); }
                    state["finished_at"] = json!(time);
                }
                "ALERT_OPENED" => { let mut state=payload.clone(); state["opened_at"]=json!(time); state["active"]=json!(true); state["acknowledged"]=json!(false); alerts.insert(text(&payload,"alert_id")?.into(),state); }
                "ALERT_ACKNOWLEDGED" | "ALERT_RESOLVED" => {
                    let state=alerts.get_mut(text(&payload,"alert_id")?).ok_or_else(|| ApiError::invalid("告警日志顺序无效"))?;
                    if kind=="ALERT_ACKNOWLEDGED" { state["acknowledged"]=json!(true); state["acknowledgement"]=payload["reason"].clone(); }
                    else {state["active"]=json!(false); state["resolved_at"]=json!(time);}
                }
                _ => {}
            }
        }
        Ok((runs,alerts))
    }
    fn run(&self,id:&str,state:Value)->Result<Run> {
        let runs=self.root.join("runs").canonicalize()?;
        let path=runs.join(id).canonicalize()?;
        if !path.starts_with(&runs) || state["status"]!="COMPLETED" { return Err(ApiError::invalid("unknown registered run")); }
        let raw=std::fs::read(checked_path(&path,"artifacts.json")?)?;
        if digest(&raw)!=text(&state,"artifacts_sha256")? { return Err(ApiError::invalid("artifact manifest changed")); }
        let manifest:Value=wavequant_quant_rs::indexed_json::from_slice(&raw)?;
        let entries=manifest.as_object().filter(|m|!m.is_empty()).ok_or_else(||ApiError::invalid("empty run artifact manifest"))?;
        for (relative, expected) in entries {
            let input=checked_path(&path,relative)?;
            if Some(sealed_file_digest(&input)?.as_str())!=expected.as_str() { return Err(ApiError::invalid("sealed input changed")); }
        }
        let report=read_json(&checked_path(&path,"report.json")?)?;
        Ok(Run{path,state,manifest,report})
    }
    fn selected(&self,id:&str)->Result<Run> {
        let (mut states,_)=self.states()?;
        self.run(id,states.remove(id).ok_or_else(||ApiError::invalid("unknown registered run"))?)
    }
    /// 初始化只校验运行日志、清单和报告；行情及其他封存文件在实际消费时校验。
    pub fn configuration(&self)->Result<Value> {
        let (states,_)=self.states()?;
        let runs_root=self.root.join("runs").canonicalize()?;
        let mut rows=Vec::new();
        for (id,state) in states {
            if state["status"]!="COMPLETED" || !self.root.join("runs").join(&id).join("research/report.json").is_file(){continue;}
            let path=runs_root.join(&id).canonicalize()?;
            if !path.starts_with(&runs_root){return Err(ApiError::invalid("封存文件路径无效"));}
            let raw=std::fs::read(checked_path(&path,"artifacts.json")?)?;
            if digest(&raw)!=text(&state,"artifacts_sha256")?{return Err(ApiError::invalid("artifact manifest changed"));}
            let manifest:Value=wavequant_quant_rs::indexed_json::from_slice(&raw)?;
            let report_path=checked_path(&path,"report.json")?;
            let raw_report=std::fs::read(report_path)?;
            if Some(digest(&raw_report).as_str())!=manifest["report.json"].as_str(){return Err(ApiError::invalid("sealed input changed"));}
            let report:Value=wavequant_quant_rs::indexed_json::from_slice(&raw_report)?;
            rows.push((state["finished_at"].as_str().unwrap_or("").to_owned(),json!({"id":id,"start":report["data"]["start"],"end":report["data"]["end"]})));
        }
        rows.sort_by(|a,b|b.0.cmp(&a.0).then_with(||a.1["id"].as_str().cmp(&b.1["id"].as_str())));
        if rows.is_empty(){return Err(ApiError::invalid("no sealed run with research data; run system-run first"));}
        Ok(json!({"runs":rows.into_iter().map(|(_,row)|row).collect::<Vec<_>>(),"variants":variants()}))
    }
    pub fn catalog(&self)->Result<Value> {
        let (states,_)=self.states()?;
        let mut result=Vec::new();
        for (id,state) in states {
            if state["status"]!="COMPLETED" || !self.root.join("runs").join(&id).join("research/report.json").is_file() {continue;}
            let run=self.run(&id,state)?;
            let mut symbols=BTreeMap::<String,Vec<String>>::new();
            for bar in run.daily()? {symbols.entry(text(&bar,"symbol")?.into()).or_default().push(day(text(&bar,"timestamp")?)?);}
            let symbols:Vec<Value>=symbols.into_iter().map(|(symbol,mut sessions)|{sessions.sort();json!({"symbol":symbol,"sessions":sessions})}).collect();
            result.push(json!({"id":id,"completed_at":run.state["finished_at"],"symbols":symbols,"start":run.report["data"]["start"],"end":run.report["data"]["end"],"rows":run.report["data"]["rows"]}));
        }
        if result.is_empty() {return Err(ApiError::invalid("no sealed run with research data; run system-run first"));}
        result.sort_by(|a,b| b["completed_at"].as_str().cmp(&a["completed_at"].as_str()));
        Ok(json!({"runs":result,"variants":variants(),"scenarios":scenarios(),"sdk":"TradingView Lightweight Charts 5.2.1","mode":"read_only_offline_research","price_basis":"causal_adjusted_equivalent","notice":"历史诊断，不是实时行情；严格版与日线代理不得混合。"}))
    }
    pub fn health(&self,id:&str)->Result<Value> {
        let run=self.selected(id)?;
        let (_,alerts)=self.states()?;
        let report=run.json("report.json")?;
        Ok(json!({"run_id":id,"status":run.state["status"],"readiness":report["readiness"],"stages":run.state["stages"],"alerts":alerts.into_values().collect::<Vec<_>>(),"data":report["data"],"recovery_drill_passed":report["recovery_drill_passed"],"production_ready":false,"live_orders_enabled":false,"integrity":"sealed_artifacts_verified_on_start_and_first_read"}))
    }
    pub fn strategy_config(&self,id:&str,variant:&str,options:wavequant_quant_rs::analysis::profiles::ProfileOptions)->Result<wavequant_quant_rs::analysis::profiles::StrategyProfile> {
        let run=self.selected(id)?;
        let report=run.json("research/report.json")?;
        wavequant_quant_rs::analysis::profiles::strategy_profile(&report["variants"],variant,options).map_err(ApiError::invalid)
    }
    pub fn backtest_version(&self,id:&str,variant:&str,engine:&str)->Result<Value> {
        for value in [id,variant]{if value.is_empty()||value.len()>128||value.trim()!=value{return Err(ApiError::invalid("one run and variant required for backtest version"));}}
        if executable_digest()?!=engine{return Err(ApiError::invalid("Rust 可执行文件已变更，请重启服务以刷新策略身份"));}
        let profile=self.strategy_config(id,variant,Default::default())?;
        let payload=wavequant_quant_rs::indexed_json::to_vec(&json!(["rust-native",id,variant,engine,profile]))?;
        Ok(json!({"version":digest(&payload),"engine_version":engine,"profile_version":profile.profile_version}))
    }
    pub fn frozen_snapshot(&self,id:&str)->Result<(Vec<Value>,String,Value)> {
        let run=self.selected(id)?;
        let rows=run.daily()?;
        let version=digest(&run.bytes("snapshot/daily.csv")?);
        Ok((rows,version,run.report))
    }
    pub fn view(&self,id:&str,variant:&str,symbol:&str,asof:&str,scenario:&str)->Result<Value> {
        if !matches!(variant,"strict_full"|"proxy_full") || scenarios().get(scenario).is_none() {return Err(ApiError::invalid("unknown sealed strategy or scenario"));}
        super::market::parse_day(asof)?;
        let run=self.selected(id)?;
        let mut selected=run.daily()?.into_iter().filter(|b|b["symbol"]==symbol).map(|row|Ok((day(text(&row,"timestamp")?)?,row))).collect::<Result<Vec<_>>>()?;
        selected.sort_by(|a,b|a.0.cmp(&b.0));let all=selected.into_iter().map(|(_,row)|row).collect::<Vec<_>>();
        let last=all.last().ok_or_else(||ApiError::invalid("unknown symbol"))?;
        if asof>day(text(last,"timestamp")?)?.as_str() {return Err(ApiError::invalid("as-of exceeds last available session"));}
        let mut bars=Vec::new();
        for row in &all { if day(text(row,"timestamp")?)?.as_str()>asof{continue;}
            let factor=row["adjustment_factor"].as_str().filter(|v|!v.is_empty()).unwrap_or("1").parse::<f64>().map_err(|_|ApiError::invalid("invalid factor"))?;
            if !factor.is_finite() || factor<=0.0 {return Err(ApiError::invalid("invalid factor"));}
            bars.push(json!({"time":day(text(row,"timestamp")?)?,"open":number(row,"open")?,"high":number(row,"high")?,"low":number(row,"low")?,"close":number(row,"close")?,"volume":number(row,"volume")?,"factor":factor,"raw_close":number(row,"close")?/factor}));
        }
        if bars.is_empty() {return Err(ApiError::invalid("no bars available as of selected date"));}
        let prefix=format!("research/{variant}/{scenario}");
        let equity=through(run.csv(&format!("{prefix}/equity.csv"))?,"timestamp",asof)?;
        let trades=through(run.csv(&format!("{prefix}/trades.csv"))?,"exit_time",asof)?;
        let orders=through(run.csv(&format!("{prefix}/orders.csv"))?,"timestamp",asof)?;
        let signals=through(run.csv(&format!("research/{variant}/signals.csv"))?,"timestamp",asof)?;
        let capital=run.json("research/report.json")?["variants"][variant]["scenarios"][scenario]["execution"]["initial_capital"].as_f64().filter(|n|n.is_finite()&&*n>0.0).ok_or_else(||ApiError::invalid("invalid initial capital"))?;
        let mut peak=capital; let mut drawdown=0.0_f64; let mut exposure=0.0; let mut curve=Vec::new();
        for row in &equity {let value=number(row,"equity")?;peak=peak.max(value);let dd=value/peak-1.0;drawdown=drawdown.min(dd);exposure+=number(row,"exposure")?;curve.push(json!({"time":day(text(row,"timestamp")?)?,"value":value/capital,"drawdown":dd*100.0,"cash":number(row,"cash")?,"exposure":number(row,"exposure")?*100.0}));}
        let mut wins=0usize; for trade in &trades {if number(trade,"pnl")?>0.0 {wins+=1;}}
        let last_equity=equity.last().map(|r|number(r,"equity")).transpose()?.unwrap_or(capital);
        let metrics=json!({"total_return":last_equity/capital-1.0,"max_drawdown":drawdown,"trades":trades.len(),"average_exposure":if equity.is_empty(){0.0}else{exposure/equity.len() as f64},"equity":last_equity,"win_rate":if trades.is_empty(){None}else{Some(wins as f64/trades.len() as f64)}});
        let selected_signals=signals.into_iter().filter(|r|r["symbol"]==symbol).map(|mut r|{r["time"]=json!(day(text(&r,"timestamp")?)?);Ok(r)}).collect::<Result<Vec<_>>>()?;
        let mut markers=Vec::new();
        for (index,row) in orders.iter().filter(|r|r["symbol"]==symbol).enumerate() {
            markers.push(json!({"id":format!("order-{index}"),"time":day(text(row,"timestamp")?)?,"kind":if row["status"]=="filled"{"fill"}else{"order"},"side":row["side"],"status":row["status"],"price":optional_number(row,"price")?,"reason":row["reason"],"quantity":optional_number(row,"quantity")?,"fee":optional_number(row,"fee")?,"stop":optional_number(row,"stop_price")?,"target":optional_number(row,"target_price")?,"signal_time":row["signal_timestamp"].as_str().filter(|s|!s.is_empty()).map(day).transpose()?,"source":"sealed_orders"}));
        }
        for (index,row) in selected_signals.iter().enumerate() {if matches!(row["side"].as_str(),Some("LONG"|"EXIT")) {markers.push(json!({"id":format!("signal-{index}"),"time":row["time"],"kind":"signal","side":row["side"],"price":optional_number(row,"reference_price")?,"reason":row["reason"],"regime":row["regime"],"stop":if row["side"]=="LONG"{optional_number(row,"invalidation_price")?}else{None},"target":if row["side"]=="LONG"{optional_number(row,"target_price")?}else{None},"rvol":optional_number(row,"rvol")?,"source":"sealed_signals"}));}}
        markers.sort_by(|a,b|a["time"].as_str().cmp(&b["time"].as_str()));
        Ok(json!({"run_id":id,"variant":variant,"scenario":scenario,"symbol":symbol,"asof":asof,"price_basis":"causal_adjusted_equivalent","bars":bars,"markers":markers,"signals":selected_signals,"orders":orders,"trades":trades,"metrics":metrics,"curve":curve,"evidence":if trades.is_empty(){"无平仓交易证据"}else{"交易样本有限，尚未建立策略优势"},"comparison":"同比 / 环比 N/A：独立历史实验；切换成本场景进行同区间比较。"}))
    }
}
impl Run {
    fn daily(&self)->Result<Vec<Value>> {
        let rows=self.csv("snapshot/daily.csv")?;
        let mut sessions=std::collections::BTreeSet::new();
        for row in &rows {
            let symbol=text(row,"symbol")?;
            let timestamp=text(row,"timestamp")?;
            let session=day(timestamp)?;
            if symbol.is_empty() || !sessions.insert((symbol.to_owned(),session)) {return Err(ApiError::invalid("invalid or duplicate daily bar identity"));}
            let (open,high,low,close,volume)=(number(row,"open")?,number(row,"high")?,number(row,"low")?,number(row,"close")?,number(row,"volume")?);
            let factor=optional_number(row,"adjustment_factor")?.unwrap_or(1.0);
            if low<=0.0 || low>open.min(close) || high<open.max(close) || volume<0.0 || factor<=0.0 {return Err(ApiError::invalid("invalid daily price bounds or factor"));}
        }
        Ok(rows)
    }
    fn bytes(&self,relative:&str)->Result<Vec<u8>> {
        let manifest=std::fs::read(self.path.join("artifacts.json"))?;
        if digest(&manifest)!=text(&self.state,"artifacts_sha256")? {return Err(ApiError::invalid("artifact manifest changed"));}
        let raw=std::fs::read(checked_path(&self.path,relative)?)?;
        if self.manifest[relative].as_str()!=Some(digest(&raw).as_str()) {return Err(ApiError::invalid("sealed input changed"));}
        Ok(raw)
    }
    fn json(&self,relative:&str)->Result<Value> {Ok(wavequant_quant_rs::indexed_json::from_slice(&self.bytes(relative)?)?)}
    fn csv(&self,relative:&str)->Result<Vec<Value>> {
        let bytes=self.bytes(relative)?;
        let mut reader=csv::Reader::from_reader(bytes.strip_prefix(&[0xef,0xbb,0xbf]).unwrap_or(&bytes));
        let headers=reader.headers()?.clone();let mut rows=Vec::new();
        for row in reader.records() {let row=row?;rows.push(Value::Object(headers.iter().zip(row.iter()).map(|(k,v)|(k.into(),json!(v))).collect()));}
        Ok(rows)
    }
}
fn day(value:&str)->Result<String> {super::sealed::local_day(value)}
fn through(rows:Vec<Value>,field:&str,asof:&str)->Result<Vec<Value>> {let mut visible=Vec::new();for row in rows{if day(text(&row,field)?)?.as_str()<=asof{visible.push(row);}}Ok(visible)}
fn number(row:&Value,key:&str)->Result<f64> {optional_number(row,key)?.ok_or_else(||ApiError::invalid("missing numeric field"))}
fn optional_number(row:&Value,key:&str)->Result<Option<f64>> {
    match row[key].as_str().filter(|v|!v.is_empty()) {None=>Ok(None),Some(raw)=>raw.parse::<f64>().ok().filter(|n|n.is_finite()).map(Some).ok_or_else(||ApiError::invalid("invalid numeric field"))}
}
fn scenarios()->Value {json!({"base":"原费用","cost_2x":"2 倍成本","cost_3x":"3 倍成本","capacity_half":"半容量"})}
fn variants()->Value {json!({"strict_full":"旧严格折线版","proxy_full":"日线代理版","lecture_v1":"讲义因果版 V1","lecture_v2":"分级双买点 V2","lecture_v3":"整段双买点 V3 · 二/三级交替 / 第二类收盘 ≤1/3","lecture_v3_c50":"整段双买点 V3 · 二/三级交替 / 第二类收盘 <1/2","lecture_v3_d67_c33":"整段双买点 V3 · 深 >2/3 / 收盘 ≤1/3","lecture_v3_d50_c50":"整段双买点 V3 · 深 >1/2 / 收盘 ≤1/2","lecture_v3_d67_c50":"整段双买点 V3 · 深 >2/3 / 收盘 ≤1/2","lecture_v3_close_d50_c50":"整段双买点 V3 · 第一类收盘 >1/2 / 第二类收盘 <1/2"})}
