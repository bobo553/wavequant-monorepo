//! 单用户自选持久化；数据库结构兼容 Python WatchlistStore。
use std::{collections::{BTreeMap, BTreeSet}, fmt, path::{Path, PathBuf}, time::Duration, sync::{Arc, RwLock}};
use chrono::NaiveDate;
use rusqlite::{Connection, OptionalExtension, TransactionBehavior, params};
use wavequant_quant_rs::indexed_json::{Value, json};
use unicode_casefold::UnicodeCaseFold;
use uuid::Uuid;

pub const MAX_BODY: usize = 4 * 1024 * 1024;
pub type StoreResult<T> = Result<T, StoreError>;
pub type RunState = (u32, f64, bool);
#[derive(Debug)]
pub enum StoreError { Invalid(String), Conflict, Storage(String) }
impl fmt::Display for StoreError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self { Self::Invalid(s) => f.write_str(s), Self::Conflict => f.write_str("自选股已在其他页面更新，请同步后重试"), Self::Storage(_) => f.write_str("服务器自选存储暂不可用") }
    }
}
impl std::error::Error for StoreError {}
impl From<rusqlite::Error> for StoreError { fn from(e: rusqlite::Error) -> Self { Self::Storage(e.to_string()) } }
impl From<wavequant_quant_rs::indexed_json::Error> for StoreError { fn from(e: wavequant_quant_rs::indexed_json::Error) -> Self { Self::Storage(e.to_string()) } }
impl From<std::io::Error> for StoreError { fn from(e: std::io::Error) -> Self { Self::Storage(e.to_string()) } }
fn invalid(s: &str) -> StoreError { StoreError::Invalid(s.into()) }
fn object(v: &Value) -> StoreResult<()> { if v.is_object() { Ok(()) } else { Err(invalid("自选数据格式无效")) } }
fn text(v: &Value, max: usize) -> StoreResult<String> {
    let s = v.as_str().ok_or_else(|| invalid("自选字段格式无效"))?;
    if s.trim().is_empty() || s.chars().count() > max { return Err(invalid("自选字段格式无效")); }
    Ok(s.trim().to_owned())
}
fn folded(s: &str) -> String { s.case_fold().collect() }
fn valid_symbol(s: &str) -> bool {
    s.len() == 9 && matches!(s.get(..3), Some("sh." | "sz." | "bj.")) && s.as_bytes()[3..].iter().all(u8::is_ascii_digit)
}
fn name(v: &Value) -> StoreResult<&str> {
    match v.get("name") { None => Ok(""), Some(value) if value.as_str().is_some_and(|s|s.chars().count()<=80) => Ok(value.as_str().unwrap_or("")), _ => Err(invalid("自选股票名称无效")) }
}
pub fn normalize_snapshot(v: &Value) -> StoreResult<Value> {
    object(v)?;
    let groups = v["groups"].as_array().ok_or_else(|| invalid("自选数据版本无效"))?;
    let members = v["memberships"].as_array().ok_or_else(|| invalid("自选数据版本无效"))?;
    if v["schemaVersion"].as_u64() != Some(1) { return Err(invalid("自选数据版本无效")); }
    if !(1..=20).contains(&groups.len()) { return Err(invalid("自选分类数量超出限制")); }
    let (mut ids, mut names, mut normalized_groups) = (BTreeSet::new(), BTreeSet::new(), Vec::new());
    for (index, group) in groups.iter().enumerate() {
        object(group)?;
        let id = text(&group["id"], 64)?;
        let name = text(&group["name"], 24)?;
        if !ids.insert(id.clone()) || !names.insert(folded(&name)) { return Err(invalid("自选分类重复")); }
        if index == 0 && (id != "default" || name != "我的自选") { return Err(invalid("默认分类不能修改")); }
        normalized_groups.push(json!({"id":id,"name":name,"position":index,"protected":index == 0}));
    }
    let mut keys = BTreeSet::new();
    let mut positions = BTreeMap::<String, usize>::new();
    let mut normalized_members = Vec::new();
    for member in members {
        object(member)?;
        let group = text(&member["groupId"], 64)?;
        let symbol = text(&member["symbol"], 9)?;
        if !ids.contains(&group) || !valid_symbol(&symbol) { return Err(invalid("自选股票代码或分类无效")); }
        let name = name(member)?;
        if !keys.insert((group.clone(), symbol.clone())) { return Err(invalid("自选股票名称无效或重复")); }
        let position = positions.entry(group.clone()).or_default();
        normalized_members.push(json!({"groupId":group,"symbol":symbol,"name":name,"position":*position}));
        *position += 1;
    }
    Ok(json!({"schemaVersion":1,"groups":normalized_groups,"memberships":normalized_members}))
}
pub fn normalize_settings(v: &Value) -> StoreResult<Value> {
    object(v)?;
    let enabled = v["enabled"].as_bool().ok_or_else(|| invalid("自动回测开关无效"))?;
    let context = &v["context"];
    object(context)?;
    let mut result: wavequant_quant_rs::indexed_json::Map<String,Value> = wavequant_quant_rs::indexed_json::Map::new();
    for key in ["run", "variant", "scenario", "source", "start", "volume_filter", "net_reward_risk_filter", "shallow_base_breakout_enabled", "initial_capital", "max_position_weight"] {
        result.insert(key.into(), json!(text(&context[key], 128)?));
    }
    for key in ["n_target_trend_confirmation_enabled", "ordinary_trend_completion_enabled"] {
        let option = match context.get(key) { None => "false", Some(v) => v.as_str().ok_or_else(|| invalid("趋势开关无效"))? };
        if !matches!(option, "true" | "false") { return Err(invalid("趋势开关无效")); }
        result.insert(key.into(), json!(option));
    }
    let level = match context.get("buy_interval_level") { None => "2", Some(v) => v.as_str().ok_or_else(|| invalid("买点筛选级别无效"))? };
    if !matches!(level, "1" | "2" | "3") { return Err(invalid("买点筛选级别无效")); }
    result.insert("buy_interval_level".into(), json!(level));
    // 自动自选任务只计算结构；手动交易开关不进入后台队列。
    result.insert("trading_enabled".into(),json!("false"));
    let levels=context.get("enable_secondary_reversal").and_then(Value::as_str).unwrap_or("[false,false,true,false]");
    let levels: [bool;4]=serde_json::from_str(levels).map_err(|_|invalid("买点开关需要四个布尔值"))?;
    result.insert("enable_secondary_reversal".into(),json!(serde_json::to_string(&levels).map_err(|_|invalid("买点开关无效"))?));
    if !matches!(result["source"].as_str(), Some("akshare" | "tdx")) { return Err(invalid("回测数据源无效")); }
    let start = result["start"].as_str().ok_or_else(|| invalid("回测开始日期无效"))?;
    NaiveDate::parse_from_str(start, "%Y-%m-%d").map_err(|_| invalid("回测开始日期无效"))?;
    for key in ["volume_filter", "net_reward_risk_filter", "shallow_base_breakout_enabled"] {
        if !matches!(result[key].as_str(), Some("true" | "false")) { return Err(invalid("回测开关无效")); }
    }
    for (key, limit) in [("initial_capital", 1_000_000_000.0), ("max_position_weight", 1.0)] {
        let n = result[key].as_str().and_then(|v| v.parse::<f64>().ok()).ok_or_else(|| invalid("回测仓位或本金无效"))?;
        if !n.is_finite() || n <= 0.0 || n > limit { return Err(invalid("回测仓位或本金无效")); }
        result.insert(key.into(), json!(n.to_string()));
    }
    Ok(json!({"enabled":enabled,"context":result}))
}

#[derive(Clone, Debug)]
pub struct WatchlistStore { path: PathBuf, cached: Arc<RwLock<Option<Value>>> }
impl WatchlistStore {
    pub fn open(path: impl AsRef<Path>) -> StoreResult<Self> {
        let path = path.as_ref().to_path_buf();
        if let Some(parent) = path.parent().filter(|p| !p.as_os_str().is_empty()) { std::fs::create_dir_all(parent)?; }
        let store = Self { path, cached: Arc::new(RwLock::new(None)) };
        let mut connection = store.connect()?;
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        transaction.execute_batch("CREATE TABLE IF NOT EXISTS watchlist_document (id INTEGER PRIMARY KEY CHECK(id=1),revision INTEGER NOT NULL,payload TEXT NOT NULL); CREATE TABLE IF NOT EXISTS watchlist_imports (token TEXT PRIMARY KEY); CREATE TABLE IF NOT EXISTS watchlist_runs (key TEXT PRIMARY KEY,attempts INTEGER NOT NULL,retry_at REAL NOT NULL,completed INTEGER NOT NULL,payload TEXT NOT NULL);")?;
        let payload = json!({"snapshot":{"schemaVersion":1,"groups":[{"id":"default","name":"我的自选","position":0,"protected":true}],"memberships":[]},"settings":null});
        transaction.execute("INSERT OR IGNORE INTO watchlist_document VALUES (1,0,?)", [payload.to_string()])?;
        transaction.commit()?;
        store.load()?;
        Ok(store)
    }
    fn connect(&self) -> StoreResult<Connection> {
        let c = Connection::open(&self.path)?;
        c.busy_timeout(Duration::from_secs(5))?;
        Ok(c)
    }
    fn read_document(c: &Connection) -> StoreResult<(u64, Value)> {
        let (revision, payload): (i64, String) = c.query_row("SELECT revision,payload FROM watchlist_document WHERE id=1", [], |r| Ok((r.get(0)?,r.get(1)?)))?;
        let revision = u64::try_from(revision).map_err(|_| StoreError::Storage("negative watchlist revision".into()))?;
        let document = wavequant_quant_rs::indexed_json::from_str::<Value>(&payload)?;
        object(&document)?;
        Ok((revision, document))
    }
    fn response(revision: u64, mut document: Value) -> StoreResult<Value> {
        document.as_object_mut().ok_or_else(|| invalid("自选数据格式无效"))?.insert("revision".into(), json!(revision));
        Ok(document)
    }
    fn write(c: &Connection, revision: u64, document: &Value) -> StoreResult<()> {
        let revision = i64::try_from(revision).map_err(|_| invalid("自选版本超出限制"))?;
        c.execute("UPDATE watchlist_document SET revision=?,payload=? WHERE id=1", params![revision,document.to_string()])?;
        Ok(())
    }
    /// 单用户读模型；只在启动读取和已提交写入后发布，不持锁访问SQLite。
    pub fn cached_document(&self) -> StoreResult<Value> {
        self.cached.read().map_err(|_| StoreError::Storage("watchlist cache poisoned".into()))?
            .clone().ok_or_else(|| StoreError::Storage("watchlist cache not initialized".into()))
    }
    fn publish(&self, revision: u64, document: Value) -> StoreResult<Value> {
        let result = Self::response(revision, document)?;
        let mut cached = self.cached.write().map_err(|_| StoreError::Storage("watchlist cache poisoned".into()))?;
        // 较旧并发读取不能覆盖较新已提交版本。
        if cached.as_ref().and_then(|value| value["revision"].as_u64()).is_none_or(|saved| revision >= saved) {
            *cached = Some(result.clone());
        }
        Ok(result)
    }
    pub fn load(&self) -> StoreResult<Value> {
        let (revision, mut document) = Self::read_document(&self.connect()?)?;
        if !document["settings"].is_null() { document["settings"] = normalize_settings(&document["settings"])?; }
        self.publish(revision, document)
    }
    pub fn save(&self, revision: &Value, value: &Value, field: &str) -> StoreResult<Value> {
        let normalized = match field { "snapshot" => normalize_snapshot(value)?, "settings" => normalize_settings(value)?, _ => return Err(invalid("自选字段无效")) };
        let expected = revision.as_u64().ok_or_else(|| invalid("自选版本无效"))?;
        let mut c = self.connect()?;
        let tx = c.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let (mut revision, mut document) = Self::read_document(&tx)?;
        if revision != expected { return Err(StoreError::Conflict); }
        if document[field] != normalized {
            document[field] = normalized;
            revision = revision.checked_add(1).ok_or_else(|| invalid("自选版本超出限制"))?;
            Self::write(&tx, revision, &document)?;
        }
        tx.commit()?;
        self.publish(revision, document)
    }
    pub fn update_members(&self, group_id: &Value, stocks: &Value, remove: bool) -> StoreResult<Value> {
        let group = text(group_id,64)?;
        let candidates = stocks.as_array().filter(|s| !s.is_empty()).ok_or_else(|| invalid("请选择自选股票"))?;
        let mut normalized = Vec::new();
        for stock in candidates {
            object(stock)?;
            let symbol = text(&stock["symbol"],9)?;
            if !valid_symbol(&symbol) { return Err(invalid("自选股票代码无效")); }
            normalized.push((symbol,name(stock)?.to_owned()));
        }
        let mut c = self.connect()?;
        let tx = c.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let (mut revision, mut document) = Self::read_document(&tx)?;
        let saved = normalize_snapshot(&document["snapshot"])?;
        let groups = saved["groups"].as_array().ok_or_else(|| invalid("自选分类无效"))?;
        if !groups.iter().any(|v| v["id"] == group) { return Err(invalid("自选分类不存在")); }
        let mut members = saved["memberships"].as_array().ok_or_else(|| invalid("自选成员无效"))?.clone();
        if remove {
            let symbols: BTreeSet<_> = normalized.iter().map(|(s,_)|s.as_str()).collect();
            members.retain(|m| m["groupId"] != group || !m["symbol"].as_str().is_some_and(|s| symbols.contains(s)));
        } else {
            let mut existing: BTreeSet<String> = members.iter().filter(|m|m["groupId"] == group).filter_map(|m|m["symbol"].as_str().map(str::to_owned)).collect();
            for (symbol,name) in normalized { if existing.insert(symbol.clone()) { members.push(json!({"groupId":group,"symbol":symbol,"name":name})); } }
        }
        let updated = normalize_snapshot(&json!({"schemaVersion":1,"groups":groups,"memberships":members}))?;
        if updated != saved { document["snapshot"] = updated; revision = revision.checked_add(1).ok_or_else(|| invalid("自选版本超出限制"))?; Self::write(&tx,revision,&document)?; }
        tx.commit()?;
        self.publish(revision,document)
    }
    pub fn import_legacy(&self, token: &Value, value: &Value) -> StoreResult<Value> {
        let token = text(token,64)?;
        if token.len() < 8 || !token.bytes().all(|c|c.is_ascii_alphanumeric() || c == b'_' || c == b'-') { return Err(invalid("迁移标识无效")); }
        let legacy = normalize_snapshot(value)?;
        let mut c = self.connect()?;
        let tx = c.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let (revision,mut document) = Self::read_document(&tx)?;
        if tx.query_row("SELECT 1 FROM watchlist_imports WHERE token=?", [&token], |_|Ok(())).optional()?.is_some() { return self.publish(revision,document); }
        let current = normalize_snapshot(&document["snapshot"])?;
        let mut groups = current["groups"].as_array().ok_or_else(|| invalid("自选分类无效"))?.clone();
        let mut members = current["memberships"].as_array().ok_or_else(|| invalid("自选成员无效"))?.clone();
        let mut mapping = BTreeMap::from([("default".to_owned(),"default".to_owned())]);
        for group in legacy["groups"].as_array().ok_or_else(|| invalid("自选分类无效"))?.iter().skip(1) {
            let name = text(&group["name"],24)?;
            let old_id = text(&group["id"],64)?;
            let same = groups.iter().find(|v|v["name"].as_str().is_some_and(|v|folded(v) == folded(&name)));
            let id = if let Some(same) = same { text(&same["id"],64)? } else {
                let id = if groups.iter().any(|v|v["id"] == old_id) { Uuid::new_v4().to_string() } else { old_id.clone() };
                groups.push(json!({"id":id,"name":name,"position":groups.len(),"protected":false}));
                id
            };
            mapping.insert(old_id,id);
        }
        let mut keys: BTreeSet<(String,String)> = members.iter().filter_map(|v|Some((v["groupId"].as_str()?.into(),v["symbol"].as_str()?.into()))).collect();
        for member in legacy["memberships"].as_array().ok_or_else(|| invalid("自选成员无效"))? {
            let group = mapping.get(&text(&member["groupId"],64)?).ok_or_else(|| invalid("自选分类不存在"))?;
            let symbol = text(&member["symbol"],9)?;
            if keys.insert((group.clone(),symbol.clone())) { members.push(json!({"groupId":group,"symbol":symbol,"name":name(member)?})); }
        }
        document["snapshot"] = normalize_snapshot(&json!({"schemaVersion":1,"groups":groups,"memberships":members}))?;
        let revision = revision.checked_add(1).ok_or_else(|| invalid("自选版本超出限制"))?;
        Self::write(&tx,revision,&document)?;
        tx.execute("INSERT INTO watchlist_imports VALUES (?)",[&token])?;
        tx.commit()?;
        self.publish(revision,document)
    }
    pub fn run_state(&self,key: &str) -> StoreResult<RunState> {
        Ok(self.connect()?.query_row("SELECT attempts,retry_at,completed FROM watchlist_runs WHERE key=?",[key],|r|Ok((r.get(0)?,r.get(1)?,r.get(2)?))).optional()?.unwrap_or((0,0.0,false)))
    }
    pub fn run_states(&self) -> StoreResult<BTreeMap<String,RunState>> {
        let c = self.connect()?;
        let mut s = c.prepare("SELECT key,attempts,retry_at,completed FROM watchlist_runs")?;
        Ok(s.query_map([],|r|Ok((r.get(0)?,(r.get(1)?,r.get(2)?,r.get(3)?))))?.collect::<Result<_,_>>()?)
    }
    pub fn finish(&self,key: &str,attempts: u32,retry_at: f64,completed: bool,summary: Option<&Value>) -> StoreResult<()> {
        if !retry_at.is_finite() || retry_at < 0.0 { return Err(invalid("重试时间无效")); }
        let summary = summary.cloned().unwrap_or_else(||json!({}));
        object(&summary)?;
        self.connect()?.execute("INSERT OR REPLACE INTO watchlist_runs VALUES (?,?,?,?,?)",params![key,attempts,retry_at,completed,summary.to_string()])?;
        Ok(())
    }
    pub fn summaries(&self) -> StoreResult<Vec<Value>> {
        let c = self.connect()?;
        let mut s = c.prepare("SELECT payload FROM watchlist_runs WHERE completed=1 ORDER BY rowid")?;
        let rows = s.query_map([],|r|r.get::<_,String>(0))?.collect::<Result<Vec<_>,_>>()?;
        rows.iter().map(|v| { let value = wavequant_quant_rs::indexed_json::from_str::<Value>(v)?; object(&value)?; Ok(value) }).collect()
    }
    pub fn prune_runs(&self,desired: &BTreeSet<String>) -> StoreResult<()> {
        let mut c = self.connect()?;
        let tx = c.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let keys = { let mut s = tx.prepare("SELECT key FROM watchlist_runs")?; s.query_map([],|r|r.get::<_,String>(0))?.collect::<Result<Vec<_>,_>>()? };
        for key in keys { if !desired.contains(&key) { tx.execute("DELETE FROM watchlist_runs WHERE key=?",[key])?; } }
        tx.commit()?;
        Ok(())
    }
    pub fn retry(&self) -> StoreResult<()> { self.connect()?.execute("DELETE FROM watchlist_runs WHERE completed=0",[])?; Ok(()) }
}


#[cfg(test)]
mod cache_tests {
    use super::*;

    #[test]
    fn committed_updates_publish_shared_memory_and_older_reads_cannot_roll_it_back() -> StoreResult<()> {
        let path=std::env::temp_dir().join(format!("wavequant-watchlist-cache-{}.sqlite",Uuid::new_v4()));
        let store=WatchlistStore::open(&path)?;
        let cloned=store.clone();
        let old=store.cached_document()?;
        let updated=store.update_members(&json!("default"),&json!([{"symbol":"sz.002724","name":"海洋王"}]),false)?;
        assert_eq!(cloned.cached_document()?,updated);
        store.publish(0,old)?;
        assert_eq!(cloned.cached_document()?,updated);
        let removed=store.update_members(&json!("default"),&json!([{"symbol":"sz.002724"}]),true)?;
        assert_eq!(cloned.cached_document()?,removed);
        std::fs::remove_file(path)?;
        Ok(())
    }

    #[test]
    fn memory_read_does_not_open_sqlite_and_fresh_read_rebuilds_it() -> StoreResult<()> {
        let path=std::env::temp_dir().join(format!("wavequant-watchlist-cache-{}.sqlite",Uuid::new_v4()));
        let store=WatchlistStore::open(&path)?;
        let initial=store.cached_document()?;
        let saved=path.with_extension("saved");
        std::fs::rename(&path,&saved)?;
        assert_eq!(store.cached_document()?,initial);
        std::fs::rename(&saved,&path)?;
        let mut changed=initial.clone();
        changed.as_object_mut().unwrap().remove("revision");
        WatchlistStore::write(&store.connect()?,1,&changed)?;
        assert_eq!(store.cached_document()?["revision"],json!(0));
        assert_eq!(store.load()?["revision"],json!(1));
        assert_eq!(store.cached_document()?["revision"],json!(1));
        std::fs::remove_file(path)?;
        Ok(())
    }
}
