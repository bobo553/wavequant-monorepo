//! 与 Python 表结构兼容的原子快照仓库。计算完成后才发布，读请求不产生空快照。
use std::{path::Path,sync::{Arc,Mutex,MutexGuard},time::Duration};
use rusqlite::{Connection,Row,params_from_iter,types::{Value as SqlValue,ValueRef}};
use serde::{Serialize,Deserialize,de::DeserializeOwned};
use wavequant_quant_rs::indexed_json::{Value,json};
use crate::{presentation::error::ApiError,application::market::parse_day};
type Result<T>=std::result::Result<T,ApiError>;

#[derive(Clone,Debug,Serialize,Deserialize)]
pub struct ResearchRun {pub run_id:String,pub status:String,pub strategy:Option<String>,pub payload:Value,pub created_at:Option<String>,pub updated_at:Option<String>}
#[derive(Clone,Debug,Serialize,Deserialize)]
pub struct SignalSnapshot {
    pub snapshot_id:String,pub run_id:String,pub variant:String,pub source:String,pub scope_symbol:Option<String>,
    pub asof:String,pub algorithm_version:String,pub data_version:String,pub payload:Value,
    pub total:u64,pub skipped:u64,pub failed:u64,
    #[serde(default)]pub scenario:Option<String>,#[serde(default)]pub start:Option<String>,
    pub created_at:Option<String>,pub updated_at:Option<String>,
}
#[derive(Clone,Debug,Serialize,Deserialize)]
pub struct TimeframeSnapshot {
    pub snapshot_id:String,pub source:String,pub symbol:String,pub timeframe:String,
    pub requested_asof:String,pub resolved_asof:String,pub algorithm_version:String,pub data_version:String,
    pub payload:Value,pub created_at:Option<String>,pub updated_at:Option<String>,
}
#[derive(Clone,Copy)]
pub enum SignalFamily {Structure,Buy}
impl SignalFamily {fn table(self)->&'static str {match self{Self::Structure=>"wavequant_structure_signal_snapshots",Self::Buy=>"wavequant_buy_signal_snapshots"}}}
pub struct SignalQuery<'a> {pub run_id:&'a str,pub variant:&'a str,pub source:&'a str,pub asof:&'a str,pub algorithm_version:&'a str,pub scope_symbol:Option<&'a str>,pub data_version:Option<&'a str>,pub scenario:Option<&'a str>,pub start:Option<&'a str>}
pub struct TimeframeQuery<'a>{pub source:&'a str,pub symbol:&'a str,pub timeframe:&'a str,pub requested_asof:&'a str,pub algorithm_version:&'a str,pub data_version:Option<&'a str>}
#[derive(Clone)]
pub struct SnapshotStore {connection:Option<Arc<Mutex<Connection>>>,remote:Option<Arc<super::remote_sql::RemoteStore>>}

pub fn identifier(value:&str)->Result<()> {if !(1..=128).contains(&value.len())||!value.bytes().all(|byte|byte.is_ascii_alphanumeric()||matches!(byte,b'.'|b'_'|b':'|b'-')){return Err(ApiError::invalid("identifier contains unsupported characters"));}Ok(())}
pub fn digest(value:&str)->Result<()> {if value.len()!=64||!value.bytes().all(|byte|byte.is_ascii_digit()||(b'a'..=b'f').contains(&byte)){return Err(ApiError::invalid("version must be a lowercase SHA256 digest"));}Ok(())}
fn string(value:&str)->SqlValue {SqlValue::Text(value.into())}
fn optional(value:Option<&str>)->SqlValue {value.map_or(SqlValue::Null,string)}
fn decode_row(row:&Row<'_>)->rusqlite::Result<Value> {
    let mut object=wavequant_quant_rs::indexed_json::Map::new();
    for (index,name) in row.as_ref().column_names().iter().enumerate() {
        let value=match row.get_ref(index)? {
            ValueRef::Null=>Value::Null,ValueRef::Integer(value)=>json!(value),ValueRef::Real(value)=>json!(value),
            ValueRef::Text(bytes)=>{
                let text=std::str::from_utf8(bytes).map_err(|error|rusqlite::Error::FromSqlConversionFailure(index,rusqlite::types::Type::Text,Box::new(error)))?;
                if *name=="payload"{wavequant_quant_rs::indexed_json::from_str(text).map_err(|error|rusqlite::Error::FromSqlConversionFailure(index,rusqlite::types::Type::Text,Box::new(error)))?}else{json!(text)}
            }
            ValueRef::Blob(_)=>return Err(rusqlite::Error::InvalidColumnType(index,(*name).into(),rusqlite::types::Type::Blob)),
        };object.insert((*name).into(),value);
    }Ok(Value::Object(object))
}
impl SnapshotStore {
    pub fn open(path:impl AsRef<Path>)->Result<Self> {
        let connection=Connection::open(path)?;connection.busy_timeout(Duration::from_secs(3))?;
        Ok(Self{connection:Some(Arc::new(Mutex::new(connection))),remote:None})
    }
        pub fn postgres(url:&str,pool_size:usize,timeout:Duration)->Result<Self> {Ok(Self{connection:None,remote:Some(Arc::new(super::remote_sql::RemoteStore::postgres(url,pool_size,timeout)?))})}
    pub fn mysql(url:&str,pool_size:usize,timeout:Duration)->Result<Self> {Ok(Self{connection:None,remote:Some(Arc::new(super::remote_sql::RemoteStore::mysql(url,pool_size,timeout)?))})}
    fn lock(&self)->Result<MutexGuard<'_,Connection>> {self.connection.as_ref().ok_or_else(ApiError::internal)?.lock().map_err(|_|ApiError::internal())}
    pub fn ping(&self)->Result<()> {if let Some(remote)=&self.remote{return remote.ping();}self.lock()?.query_row("SELECT 1",[],|row|row.get::<_,i64>(0))?;Ok(())}
    pub fn initialize(&self)->Result<()> {if let Some(remote)=&self.remote{return remote.initialize();}
        let mut connection=self.lock()?;let tx=connection.transaction()?;
        tx.execute_batch("CREATE TABLE IF NOT EXISTS wavequant_research_runs(run_id TEXT PRIMARY KEY,status TEXT NOT NULL,strategy TEXT,payload JSON NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS ix_wavequant_research_runs_status_updated ON wavequant_research_runs(status,updated_at);
        CREATE TABLE IF NOT EXISTS wavequant_structure_signal_snapshots(snapshot_id TEXT PRIMARY KEY,run_id TEXT NOT NULL,variant TEXT NOT NULL,source TEXT NOT NULL,scope_symbol TEXT,asof TEXT NOT NULL,algorithm_version TEXT NOT NULL,data_version TEXT NOT NULL,payload JSON NOT NULL,total INTEGER NOT NULL,skipped INTEGER NOT NULL,failed INTEGER NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS ix_wavequant_structure_signal_lookup ON wavequant_structure_signal_snapshots(run_id,variant,source,scope_symbol,asof,algorithm_version,updated_at);
        CREATE INDEX IF NOT EXISTS ix_wavequant_structure_generation_lookup ON wavequant_structure_signal_snapshots(run_id,source,asof,algorithm_version,scope_symbol,updated_at);
        CREATE TABLE IF NOT EXISTS wavequant_buy_signal_snapshots(snapshot_id TEXT PRIMARY KEY,run_id TEXT NOT NULL,variant TEXT NOT NULL,scenario TEXT NOT NULL,source TEXT NOT NULL,scope_symbol TEXT,asof TEXT NOT NULL,start TEXT NOT NULL,algorithm_version TEXT NOT NULL,data_version TEXT NOT NULL,payload JSON NOT NULL,total INTEGER NOT NULL,skipped INTEGER NOT NULL,failed INTEGER NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS ix_wavequant_buy_signal_lookup ON wavequant_buy_signal_snapshots(run_id,variant,scenario,source,scope_symbol,asof,start,algorithm_version,updated_at);
        CREATE TABLE IF NOT EXISTS wavequant_market_timeframe_snapshots(snapshot_id TEXT PRIMARY KEY,source TEXT NOT NULL,symbol TEXT NOT NULL,timeframe TEXT NOT NULL,requested_asof TEXT NOT NULL,resolved_asof TEXT NOT NULL,algorithm_version TEXT NOT NULL,data_version TEXT NOT NULL,payload JSON NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS ix_wavequant_market_timeframe_lookup ON wavequant_market_timeframe_snapshots(source,symbol,timeframe,requested_asof,algorithm_version,updated_at);")?;tx.commit()?;Ok(())
    }
    fn query<T:DeserializeOwned>(&self,sql:&str,parameters:Vec<SqlValue>)->Result<Vec<T>> {
        if let Some(remote)=&self.remote{return remote.query(sql,parameters)?.into_iter().map(|value|wavequant_quant_rs::indexed_json::from_value(value).map_err(ApiError::from)).collect();}
        let connection=self.lock()?;let mut statement=connection.prepare(sql)?;
        let rows=statement.query_map(params_from_iter(parameters),decode_row)?;
        let mut values=Vec::new();for row in rows{values.push(wavequant_quant_rs::indexed_json::from_value(row?)?);}Ok(values)
    }
    fn publish<T:DeserializeOwned>(&self,table:&str,key:&str,mut value:Value,updates:&[&str])->Result<T> {
        let object=value.as_object_mut().ok_or_else(||ApiError::invalid("snapshot must be an object"))?;
        let now=chrono::Utc::now().format("%Y-%m-%d %H:%M:%S%.6f").to_string();
        for name in ["created_at","updated_at"] {if object.get(name).is_none_or(Value::is_null){object.insert(name.into(),json!(now));}}
        let columns=object.keys().map(String::as_str).collect::<Vec<_>>();
        let mut params=Vec::new();
        for (name,value) in object.iter(){params.push(if name=="payload" {string(&wavequant_quant_rs::indexed_json::to_string(value)?)}else{match (value).kind() {None=>SqlValue::Null,Some(wavequant_quant_rs::indexed_json::Kind::String(value))=>string(value),Some(wavequant_quant_rs::indexed_json::Kind::Number(value))=>SqlValue::Integer(value.as_i64().ok_or_else(||ApiError::invalid("snapshot count exceeds SQL integer range"))?),_=>return Err(ApiError::invalid("invalid snapshot field"))}});}
        let sql=format!("INSERT INTO {table} ({}) VALUES ({}) ON CONFLICT({key}) DO UPDATE SET {}",columns.join(","),vec!["?";columns.len()].join(","),updates.iter().map(|field|format!("{field}=excluded.{field}")).collect::<Vec<_>>().join(","));
        let identity=object.get(key).and_then(Value::as_str).ok_or_else(||ApiError::invalid("snapshot identity required"))?.to_owned();
        if let Some(remote)=&self.remote{return Ok(wavequant_quant_rs::indexed_json::from_value(remote.publish(table,key,&columns,params,updates,&identity)?)?);}
        let mut connection=self.lock()?;let tx=connection.transaction()?;tx.execute(&sql,params_from_iter(params))?;
        let row=tx.query_row(&format!("SELECT * FROM {table} WHERE {key}=?"),[identity],decode_row)?;
        let stored=wavequant_quant_rs::indexed_json::from_value(row)?;tx.commit()?;Ok(stored)
    }
    pub fn save(&self,run:ResearchRun)->Result<ResearchRun> {
        identifier(&run.run_id)?;if run.status.is_empty()||run.status.len()>32||!run.status.as_bytes()[0].is_ascii_uppercase()||!run.status.bytes().all(|byte|byte.is_ascii_uppercase()||byte.is_ascii_digit()||matches!(byte,b'_'|b'-')){return Err(ApiError::invalid("status must be an uppercase stable identifier"));}
        if let Some(strategy)=&run.strategy{identifier(strategy)?;}if !run.payload.is_object(){return Err(ApiError::invalid("run payload must be an object"));}
        self.publish("wavequant_research_runs","run_id",wavequant_quant_rs::indexed_json::to_value(run)?,&["status","strategy","payload","updated_at"])
    }
    pub fn get(&self,run_id:&str)->Result<Option<ResearchRun>> {identifier(run_id)?;Ok(self.query("SELECT * FROM wavequant_research_runs WHERE run_id=?",vec![string(run_id)])?.pop())}
    pub fn list_recent(&self,limit:u16)->Result<Vec<ResearchRun>> {if !(1..=200).contains(&limit){return Err(ApiError::invalid("limit must be 1..200"));}self.query("SELECT * FROM wavequant_research_runs ORDER BY updated_at DESC LIMIT ?",vec![SqlValue::Integer(limit.into())])}
    pub fn save_structure_snapshot(&self,snapshot:SignalSnapshot)->Result<SignalSnapshot> {self.save_signal(SignalFamily::Structure,snapshot)}
    pub fn save_buy_signal_snapshot(&self,snapshot:SignalSnapshot)->Result<SignalSnapshot> {self.save_signal(SignalFamily::Buy,snapshot)}
    fn save_signal(&self,family:SignalFamily,snapshot:SignalSnapshot)->Result<SignalSnapshot> {
        for value in [&snapshot.snapshot_id,&snapshot.run_id,&snapshot.variant,&snapshot.source]{identifier(value)?;}
        if let Some(symbol)=&snapshot.scope_symbol{identifier(symbol)?;}
        parse_day(&snapshot.asof)?;digest(&snapshot.algorithm_version)?;digest(&snapshot.data_version)?;
        if !snapshot.payload.is_object(){return Err(ApiError::invalid("snapshot payload must be an object"));}
        if matches!(family,SignalFamily::Buy){identifier(snapshot.scenario.as_deref().ok_or_else(||ApiError::invalid("scenario required"))?)?;parse_day(snapshot.start.as_deref().ok_or_else(||ApiError::invalid("start required"))?)?;}
        let mut value=wavequant_quant_rs::indexed_json::to_value(snapshot)?;
        if matches!(family,SignalFamily::Structure){let object=value.as_object_mut().ok_or_else(ApiError::internal)?;object.remove("scenario");object.remove("start");}
        self.publish(family.table(),"snapshot_id",value,&["payload","total","skipped","failed","updated_at"])
    }
    pub fn find_signal(&self,family:SignalFamily,query:SignalQuery<'_>)->Result<Option<SignalSnapshot>> {
        for value in [query.run_id,query.variant,query.source,query.algorithm_version]{identifier(value)?;}parse_day(query.asof)?;
        if let Some(value)=query.scope_symbol{identifier(value)?;}if let Some(value)=query.data_version{digest(value)?;}
        let mut sql=format!("SELECT * FROM {} WHERE run_id=? AND variant=? AND source=? AND scope_symbol IS ? AND asof=? AND algorithm_version=?",family.table());
        let mut params=vec![string(query.run_id),string(query.variant),string(query.source),optional(query.scope_symbol),string(query.asof),string(query.algorithm_version)];
        if let Some(version)=query.data_version{sql.push_str(" AND data_version=?");params.push(string(version));}
        if matches!(family,SignalFamily::Buy){let scenario=query.scenario.ok_or_else(||ApiError::invalid("scenario required"))?;let start=query.start.ok_or_else(||ApiError::invalid("start required"))?;identifier(scenario)?;parse_day(start)?;sql.push_str(" AND scenario=? AND start=?");params.extend([string(scenario),string(start)]);}
        sql.push_str(" ORDER BY updated_at DESC LIMIT 1");Ok(self.query(&sql,params)?.pop())
    }
    pub fn list_structure_snapshots(&self,run:&str,variant:Option<&str>,source:&str,asof:&str,algorithm:&str,limit:u32)->Result<Vec<SignalSnapshot>> {
        for value in [run,source,algorithm]{identifier(value)?;}if let Some(value)=variant{identifier(value)?;}parse_day(asof)?;if !(1..=20000).contains(&limit){return Err(ApiError::invalid("limit must be 1..20000"));}
        self.query("SELECT * FROM (SELECT *,row_number() OVER (PARTITION BY scope_symbol ORDER BY updated_at DESC) AS scope_rank FROM wavequant_structure_signal_snapshots WHERE run_id=? AND source=? AND scope_symbol IS NOT NULL AND asof=? AND algorithm_version=? AND (? IS NULL OR variant=?)) AS ranked WHERE scope_rank=1 ORDER BY scope_symbol LIMIT ?",vec![string(run),string(source),string(asof),string(algorithm),optional(variant),optional(variant),SqlValue::Integer(limit.into())])
    }
    pub fn list_structure_snapshot_generations(&self,run:&str,source:&str,asof:&str,limit:u16,n_target:bool,ordinary:bool)->Result<Vec<Value>> {
        identifier(run)?;identifier(source)?;parse_day(asof)?;if !(1..=500).contains(&limit){return Err(ApiError::invalid("limit must be 1..500"));}
        self.query("SELECT asof,algorithm_version,count(*) AS published_stocks,max(updated_at) AS updated_at,max(json_extract(payload,'$.market_total')) AS market_total,coalesce(sum(json_extract(payload,'$.stale')),0) AS stale_stocks FROM (SELECT *,row_number() OVER (PARTITION BY asof,algorithm_version,scope_symbol ORDER BY updated_at DESC) AS scope_rank FROM wavequant_structure_signal_snapshots WHERE run_id=? AND source=? AND scope_symbol IS NOT NULL AND asof<=?) AS ranked WHERE scope_rank=1 AND coalesce(json_extract(payload,'$.n_target_trend_confirmation_enabled'),0)=? AND coalesce(json_extract(payload,'$.ordinary_trend_completion_enabled'),0)=? GROUP BY asof,algorithm_version ORDER BY asof DESC,max(updated_at) DESC LIMIT ?",vec![string(run),string(source),string(asof),SqlValue::Integer(i64::from(n_target)),SqlValue::Integer(i64::from(ordinary)),SqlValue::Integer(limit.into())])
    }
    pub fn save_market_timeframe_snapshot(&self,snapshot:TimeframeSnapshot)->Result<TimeframeSnapshot> {
        for value in [&snapshot.snapshot_id,&snapshot.source,&snapshot.symbol,&snapshot.timeframe]{identifier(value)?;}
        parse_day(&snapshot.requested_asof)?;parse_day(&snapshot.resolved_asof)?;digest(&snapshot.algorithm_version)?;digest(&snapshot.data_version)?;
        if !snapshot.payload.is_object(){return Err(ApiError::invalid("timeframe payload must be an object"));}
        self.publish("wavequant_market_timeframe_snapshots","snapshot_id",wavequant_quant_rs::indexed_json::to_value(snapshot)?,&["payload","resolved_asof","updated_at"])
    }
    pub fn find_market_timeframe_snapshot(&self,query:TimeframeQuery<'_>)->Result<Option<TimeframeSnapshot>> {
        for value in [query.source,query.symbol,query.timeframe]{identifier(value)?;}parse_day(query.requested_asof)?;digest(query.algorithm_version)?;if let Some(version)=query.data_version{digest(version)?;}
        Ok(self.query("SELECT * FROM wavequant_market_timeframe_snapshots WHERE source=? AND symbol=? AND timeframe=? AND requested_asof=? AND algorithm_version=? AND (? IS NULL OR data_version=?) ORDER BY updated_at DESC LIMIT 1",vec![string(query.source),string(query.symbol),string(query.timeframe),string(query.requested_asof),string(query.algorithm_version),optional(query.data_version),optional(query.data_version)])?.pop())
    }
}
