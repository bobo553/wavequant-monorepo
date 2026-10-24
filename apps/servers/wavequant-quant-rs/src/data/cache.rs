//! 有界、带来源版本和请求时点的可重建行情缓存，不替代源数据事实。
use std::{collections::BTreeMap,path::Path,time::Duration};
use rusqlite::{Connection,OptionalExtension,TransactionBehavior,params};
use serde::{Serialize,Deserialize};
use crate::indexed_json::Value;
use sha2::{Digest,Sha256};
use super::tdx::DataError;
const MAX_PAYLOAD:usize=20*1024*1024;
fn invalid(s:&str)->DataError {DataError::Invalid(s.into())}
fn storage(_:rusqlite::Error)->DataError {DataError::Unavailable("行情缓存暂不可用".into())}
#[derive(Clone,Debug,Serialize,Deserialize)]
pub struct CacheIdentity {
    pub provider:String,pub endpoint:String,pub symbol:String,pub asof:String,
    pub codec_version:String,pub parameters:Value,
}
#[derive(Clone,Debug)]
pub struct CachedData {pub value:Value,pub source_version:String,pub created_at:i64,pub expires_at:i64}
#[derive(Clone,Debug)]
pub struct DataCache {path:std::path::PathBuf,max_entries:usize}
fn canonical(value:&Value)->Value {
    match (value).kind() {
        Some(crate::indexed_json::Kind::Object(map))=>Value::Object(map.iter().map(|(key,value)|(key.clone(),canonical(value))).collect::<BTreeMap<_,_>>().into_iter().collect()),
        Some(crate::indexed_json::Kind::Array(values))=>Value::Array(values.iter().map(canonical).collect()),
        _=>value.clone(),
    }
}
impl CacheIdentity {
    pub fn key(&self)->Result<String,DataError> {
        if [&self.provider,&self.endpoint,&self.codec_version].iter().any(|v|v.is_empty() || v.len()>512) || self.symbol.len()>128 || self.asof.len()>128 {return Err(invalid("invalid cache identity"));}
        let value=crate::indexed_json::to_value(self).map_err(|_|invalid("cache identity cannot be serialized"))?;
        let encoded=crate::indexed_json::to_vec(&canonical(&value)).map_err(|_|invalid("cache identity cannot be serialized"))?;
        Ok(format!("{:x}",Sha256::digest(encoded)))
    }
}
impl DataCache {
    pub fn open(path:impl AsRef<Path>,max_entries:usize)->Result<Self,DataError> {
        if !(1..=100000).contains(&max_entries) {return Err(invalid("cache capacity outside bounds"));}
        let path=path.as_ref().to_path_buf();
        if let Some(parent)=path.parent().filter(|p|!p.as_os_str().is_empty()) {std::fs::create_dir_all(parent)?;}
        let cache=Self{path,max_entries};
        cache.connect()?.execute_batch("CREATE TABLE IF NOT EXISTS rust_market_cache_v1(identity TEXT NOT NULL,source_version TEXT NOT NULL,payload TEXT NOT NULL,payload_digest TEXT NOT NULL,created_at INTEGER NOT NULL,expires_at INTEGER NOT NULL,PRIMARY KEY(identity,source_version)); CREATE INDEX IF NOT EXISTS rust_market_cache_expiry_v1 ON rust_market_cache_v1(expires_at);").map_err(storage)?;
        Ok(cache)
    }
    fn connect(&self)->Result<Connection,DataError> {
        let c=Connection::open(&self.path).map_err(storage)?;
        c.busy_timeout(Duration::from_millis(250)).map_err(storage)?;
        Ok(c)
    }
    pub fn get(&self,identity:&CacheIdentity,now:i64)->Result<Option<CachedData>,DataError> {
        let key=identity.key()?;
        let row:Option<(String,String,String,i64,i64)>=self.connect()?.query_row("SELECT payload,payload_digest,source_version,created_at,expires_at FROM rust_market_cache_v1 WHERE identity=? AND created_at<=? AND expires_at>? ORDER BY created_at DESC,source_version LIMIT 1",params![key,now,now],|r|Ok((r.get(0)?,r.get(1)?,r.get(2)?,r.get(3)?,r.get(4)?))).optional().map_err(storage)?;
        let Some((payload,digest,source_version,created_at,expires_at))=row else {return Ok(None)};
        if payload.len()>MAX_PAYLOAD || format!("{:x}",Sha256::digest(payload.as_bytes()))!=digest {return Err(invalid("market cache integrity mismatch"));}
        let value=crate::indexed_json::from_str(&payload).map_err(|_|invalid("market cache JSON invalid"))?;
        Ok(Some(CachedData{value,source_version,created_at,expires_at}))
    }
    pub fn put(&self,identity:&CacheIdentity,source_version:&str,value:&Value,now:i64,ttl_seconds:u32)->Result<(),DataError> {
        if !(1..=86400).contains(&ttl_seconds) || source_version.len()!=64 || !source_version.bytes().all(|b|b.is_ascii_hexdigit()) {return Err(invalid("invalid cache version or TTL"));}
        let key=identity.key()?;
        let payload=crate::indexed_json::to_string(&canonical(value)).map_err(|_|invalid("cache serialization failed"))?;
        if payload.len()>MAX_PAYLOAD {return Err(invalid("cache payload exceeds size limit"));}
        let digest=format!("{:x}",Sha256::digest(payload.as_bytes()));
        let expires=now.checked_add(i64::from(ttl_seconds)).ok_or_else(||invalid("cache expiry overflow"))?;
        let mut c=self.connect()?;
        let tx=c.transaction_with_behavior(TransactionBehavior::Immediate).map_err(storage)?;
        let existing:Option<String>=tx.query_row("SELECT payload_digest FROM rust_market_cache_v1 WHERE identity=? AND source_version=?",params![key,source_version],|r|r.get(0)).optional().map_err(storage)?;
        if existing.is_some_and(|old|old!=digest) {return Err(invalid("immutable source/version cache payload changed"));}
        tx.execute("INSERT INTO rust_market_cache_v1 VALUES(?,?,?,?,?,?) ON CONFLICT(identity,source_version) DO UPDATE SET created_at=excluded.created_at,expires_at=excluded.expires_at",params![key,source_version,payload,digest,now,expires]).map_err(storage)?;
        tx.execute("DELETE FROM rust_market_cache_v1 WHERE expires_at<=?",[now]).map_err(storage)?;
        tx.execute("DELETE FROM rust_market_cache_v1 WHERE rowid IN (SELECT rowid FROM rust_market_cache_v1 ORDER BY created_at DESC,rowid DESC LIMIT -1 OFFSET ?)",[self.max_entries as i64]).map_err(storage)?;
        tx.commit().map_err(storage)?;
        Ok(())
    }
}
