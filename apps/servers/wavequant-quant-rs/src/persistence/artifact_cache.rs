use super::{StoreResult,StoreError,canonical,sha256,require};
use flate2::{Compression,read::ZlibDecoder,write::ZlibEncoder};
use rusqlite::{Connection,OptionalExtension,params};
use crate::indexed_json::{Value,json};
use std::{collections::HashMap,io::{Read,Write},path::{Path,PathBuf},sync::{Arc,Mutex,Weak,atomic::{AtomicBool,Ordering}},time::Duration};

pub struct ArtifactCache{
    pub path:PathBuf,pub max_bytes:u64,pub read_timeout:Duration,pub max_uncompressed_bytes:u64,
    locks:Mutex<HashMap<String,Weak<Mutex<()>>>>,ready:AtomicBool,initializing:Mutex<()>,
}
impl ArtifactCache{
    pub fn new(root:&Path,max_bytes:u64)->StoreResult<Self>{require(max_bytes>0,"positive artifact capacity required")?;
        Ok(Self{path:root.join("rust-artifacts-v1.sqlite"),max_bytes,read_timeout:Duration::from_secs(30),max_uncompressed_bytes:512*1024*1024,locks:Mutex::new(HashMap::new()),ready:AtomicBool::new(false),initializing:Mutex::new(())})}
    pub fn key(namespace:&str,inputs:&Value)->StoreResult<String>{Ok(sha256(canonical(&json!([1,namespace,inputs]))?))}
    pub fn lock(&self,key:&str)->StoreResult<Arc<Mutex<()>>>{let mut locks=self.locks.lock().map_err(|_|StoreError("artifact lock map poisoned".into()))?;
        if let Some(lock)=locks.get(key).and_then(Weak::upgrade){return Ok(lock);}locks.retain(|_,v|v.strong_count()>0);
        let lock=Arc::new(Mutex::new(()));locks.insert(key.into(),Arc::downgrade(&lock));Ok(lock)}
    // 允许不可压缩输入的 zlib 开销；读取前按当前预算筛选，不能先把异常 blob 搬入内存。
    fn max_compressed_bytes(&self)->u64{self.max_uncompressed_bytes.saturating_add(1024*1024).min(self.max_bytes).min(i64::MAX as u64)}
    fn connect(&self,timeout:Duration)->StoreResult<Connection>{
        if let Some(parent)=self.path.parent(){std::fs::create_dir_all(parent)?;}let db=Connection::open(&self.path)?;db.busy_timeout(timeout)?;
        if self.ready.load(Ordering::Acquire){return Ok(db);}
        let _initialization=self.initializing.try_lock().map_err(|_|StoreError("cache initialization already in progress".into()))?;
        if self.ready.load(Ordering::Acquire){return Ok(db);}
        let mode:String=db.query_row("PRAGMA journal_mode",[],|r|r.get(0))?;if mode!="wal"{db.execute_batch("PRAGMA journal_mode=WAL")?;}
        db.execute_batch("BEGIN IMMEDIATE; CREATE TABLE IF NOT EXISTS artifacts(key TEXT PRIMARY KEY,namespace TEXT NOT NULL,payload BLOB NOT NULL,digest TEXT NOT NULL,size INTEGER NOT NULL,created INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS artifact_sizes(key TEXT PRIMARY KEY,size INTEGER NOT NULL) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS artifact_usage(id INTEGER PRIMARY KEY CHECK(id=1),total_bytes INTEGER NOT NULL CHECK(total_bytes>=0));
            CREATE INDEX IF NOT EXISTS artifacts_eviction_order ON artifacts(created,key,size);
            CREATE TABLE IF NOT EXISTS artifact_versions(name TEXT PRIMARY KEY,version TEXT NOT NULL);
            INSERT OR IGNORE INTO artifact_usage SELECT 1,COALESCE(SUM(size),0) FROM artifacts;
            INSERT OR IGNORE INTO artifact_sizes SELECT key,size FROM artifacts;
            CREATE TRIGGER IF NOT EXISTS artifact_usage_insert AFTER INSERT ON artifacts BEGIN
                UPDATE artifact_usage SET total_bytes=total_bytes+NEW.size-COALESCE((SELECT size FROM artifact_sizes WHERE key=NEW.key),0) WHERE id=1;
                INSERT OR REPLACE INTO artifact_sizes(key,size) VALUES(NEW.key,NEW.size); END;
            CREATE TRIGGER IF NOT EXISTS artifact_usage_delete AFTER DELETE ON artifacts BEGIN
                UPDATE artifact_usage SET total_bytes=total_bytes-COALESCE((SELECT size FROM artifact_sizes WHERE key=OLD.key),0) WHERE id=1;
                DELETE FROM artifact_sizes WHERE key=OLD.key; END;
            CREATE TRIGGER IF NOT EXISTS artifact_usage_update AFTER UPDATE OF key,size ON artifacts BEGIN
                UPDATE artifact_usage SET total_bytes=total_bytes-COALESCE((SELECT size FROM artifact_sizes WHERE key=OLD.key),0) WHERE id=1;
                DELETE FROM artifact_sizes WHERE key=OLD.key;
                UPDATE artifact_usage SET total_bytes=total_bytes+NEW.size-COALESCE((SELECT size FROM artifact_sizes WHERE key=NEW.key),0) WHERE id=1;
                INSERT OR REPLACE INTO artifact_sizes(key,size) VALUES(NEW.key,NEW.size);END; COMMIT;")?;self.ready.store(true,Ordering::Release);Ok(db)
    }
    pub fn contains(&self,namespace:&str,inputs:&Value)->bool{(||->StoreResult<bool>{let key=Self::key(namespace,inputs)?;let db=self.connect(self.read_timeout)?;
        Ok(db.query_row("SELECT 1 FROM artifacts WHERE key=?1 AND typeof(payload)='blob' AND length(payload)<=?2 AND typeof(digest)='text' AND length(digest)=64",
            params![key,self.max_compressed_bytes() as i64],|_|Ok(())).optional()?.is_some())})().unwrap_or(false)}
    pub fn get(&self,namespace:&str,inputs:&Value)->Option<Value>{(||->StoreResult<Option<Value>>{
        let key=Self::key(namespace,inputs)?;let db=self.connect(self.read_timeout)?;
        let row:Option<(Vec<u8>,String)>=db.query_row("SELECT payload,digest FROM artifacts WHERE key=?1 AND typeof(payload)='blob' AND length(payload)<=?2 AND typeof(digest)='text' AND length(digest)=64",
            params![&key,self.max_compressed_bytes() as i64],|r|Ok((r.get(0)?,r.get(1)?))).optional()?;
        let Some((packed,digest))=row else{return Ok(None);};require(sha256(&packed)==digest,"artifact checksum mismatch")?;
        let mut unpacked=Vec::new();ZlibDecoder::new(packed.as_slice()).take(self.max_uncompressed_bytes.saturating_add(1)).read_to_end(&mut unpacked)?;
        require(unpacked.len() as u64<=self.max_uncompressed_bytes,"artifact decompression limit exceeded")?;
        let value:Value=crate::indexed_json::wire::from_slice(&unpacked)?;require(value["key"].as_str()==Some(&key)&&matches!(value["schema"].as_u64(),Some(1|2)),"artifact identity mismatch")?;
        Ok(value.get("data").cloned())
    })().ok().flatten()}
    pub fn put(&self,namespace:&str,inputs:&Value,data:&Value)->bool{self.put_result(namespace,inputs,data).unwrap_or(false)}
    fn put_result(&self,namespace:&str,inputs:&Value,data:&Value)->StoreResult<bool>{
        let key=Self::key(namespace,inputs)?;let raw=crate::indexed_json::wire::to_vec(&json!({"schema":2,"key":key,"data":data}))?;
        if raw.len() as u64>self.max_uncompressed_bytes{return Ok(false);}let mut compressor=ZlibEncoder::new(Vec::new(),Compression::fast());compressor.write_all(&raw)?;
        let packed=compressor.finish()?;let size=packed.len() as u64;if size>self.max_compressed_bytes(){return Ok(false);}
        let mut db=self.connect(Duration::from_millis(500))?;let transaction=db.transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
        transaction.execute("INSERT OR REPLACE INTO artifacts VALUES(?1,?2,?3,?4,?5,?6)",params![key,namespace,packed,sha256(&packed),size as i64,chrono::Utc::now().timestamp_micros()])?;
        let mut total:u64=transaction.query_row("SELECT total_bytes FROM artifact_usage WHERE id=1",[],|r|super::sqlite_u64(r,0))?;
        if total>self.max_bytes{let target=(self.max_bytes/10*9).max(size);
            while total>target{let rows={let mut statement=transaction.prepare("SELECT key,size FROM artifacts WHERE key!=?1 ORDER BY created,key LIMIT 256")?;
                statement.query_map([&key],|r|Ok((r.get::<_,String>(0)?,super::sqlite_u64(r,1)?)))?.collect::<Result<Vec<_>,_>>()?};
                if rows.is_empty(){break;}for(old,size)in rows{transaction.execute("DELETE FROM artifacts WHERE key=?1",[old])?;total=total.saturating_sub(size);if total<=target{break;}}
            }
        }
        transaction.commit()?;Ok(true)
    }
    pub fn clear_backtests_for_engine(&self,engine:&Value)->usize{(||->StoreResult<usize>{
        let version=sha256(canonical(engine)?);let mut db=self.connect(Duration::from_millis(500))?;let transaction=db.transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
        let old:Option<String>=transaction.query_row("SELECT version FROM artifact_versions WHERE name='quant-engine'",[],|r|r.get(0)).optional()?;
        if old.as_deref()==Some(&version){return Ok(0);}let removed=transaction.execute("DELETE FROM artifacts WHERE namespace IN ('backtest','signals','screen','akshare-backtest','akshare-signals','adjusted_theory')",[])?;
        transaction.execute("INSERT OR REPLACE INTO artifact_versions VALUES('quant-engine',?1)",[version])?;transaction.commit()?;Ok(removed)
    })().unwrap_or(0)}
}
