//! 可选基础设施按真实配置报告健康状态；缓存故障不代替权威数据。
use std::{path::PathBuf,sync::Arc,time::Duration};
use wavequant_quant_rs::indexed_json::{Value,json};
use crate::{presentation::error::ApiError,storage::{snapshots::SnapshotStore,cache::RedisCache}};
type Result<T>=std::result::Result<T,ApiError>;
#[derive(Clone)]
pub struct Infrastructure {pub database:Option<Arc<SnapshotStore>>,pub cache:Option<Arc<RedisCache>>,database_backend:Option<String>}
impl Infrastructure {
    pub fn from_env()->Result<Self> {
        let database_url=std::env::var("WAVEQUANT_DATABASE_URL").unwrap_or_default();
        let redis_url=std::env::var("WAVEQUANT_REDIS_URL").unwrap_or_default();
        let mut database=None;let mut database_backend=None;
        if !database_url.trim().is_empty(){
            let pool_size=bounded("WAVEQUANT_DB_POOL_SIZE",5,1,20)? as usize;let timeout=Duration::from_secs(bounded("WAVEQUANT_DB_CONNECT_TIMEOUT_SECONDS",3,1,30)?);
            let url=database_url.trim();
            if let Some(path)=url.strip_prefix("sqlite+pysqlite:///").or_else(||url.strip_prefix("sqlite:///")) {
                if path.contains('?')||path.contains('#')||path.is_empty(){return Err(ApiError::invalid("unsupported SQLite URL parameters"));}
                let path=decode_path(path)?;database=Some(Arc::new(SnapshotStore::open(PathBuf::from(path))?));database_backend=Some("sqlite".into());
            }else if url.starts_with("postgresql+psycopg://"){database=Some(Arc::new(SnapshotStore::postgres(url,pool_size,timeout)?));database_backend=Some("postgresql".into());}
            else if url.starts_with("mysql+pymysql://"){database=Some(Arc::new(SnapshotStore::mysql(url,pool_size,timeout)?));database_backend=Some("mysql".into());}
            else{return Err(ApiError::invalid("WAVEQUANT_DATABASE_URL backend is unsupported"));}
        }
        let cache=if redis_url.trim().is_empty(){None}else{
            let prefix=std::env::var("WAVEQUANT_REDIS_PREFIX").unwrap_or_else(|_|"wavequant".into()).trim().to_owned();
            let ttl=bounded("WAVEQUANT_CACHE_TTL_SECONDS",300,1,86400)?;
            let timeout=bounded("WAVEQUANT_REDIS_CONNECT_TIMEOUT_SECONDS",2,1,30)?;
            Some(Arc::new(RedisCache::new(redis_url.trim(),prefix,ttl,Duration::from_secs(timeout))?))
        };
        Ok(Self{database,cache,database_backend})
    }
    pub fn initialize(&self)->Result<()> {self.database.as_ref().ok_or_else(||ApiError::unavailable("可写 SQL 适配器未配置"))?.initialize()}
    pub fn health(&self)->Value {
        let configured=self.database_backend.is_some();let healthy=self.database.as_ref().is_some_and(|database|database.ping().is_ok());
        let redis_configured=self.cache.is_some();let redis_healthy=self.cache.as_ref().is_some_and(|cache|cache.ping().is_ok());
        let mut database=json!({"configured":configured,"healthy":healthy});if let Some(backend)=&self.database_backend{database["backend"]=json!(backend);}
        if configured&&self.database.is_none(){database["reason"]=json!("native_backend_adapter_not_available");}
        let mut redis=json!({"configured":redis_configured,"healthy":redis_healthy});if let Some(cache)=&self.cache{redis["tls"]=json!(cache.tls);}
        json!({"status":if !configured&&!redis_configured{"disabled"}else if (!configured||healthy)&&(!redis_configured||redis_healthy){"ok"}else{"degraded"},"database":database,"redis":redis})
    }
    pub fn cached_json(&self,key:&str,loader:impl FnOnce()->Result<Value>)->Result<Value> {
        if let Some(cache)=&self.cache{match cache.get_json(key){Ok(Some(value))=>return Ok(value),Ok(None)=>{},Err(_)=>tracing::warn!("Redis read failed; using source data")}}
        let value=loader()?;
        if let Some(cache)=&self.cache{if cache.set_json(key,&value,None).is_err(){tracing::warn!("Redis write failed; source response retained");}}
        Ok(value)
    }
}
fn bounded(name:&str,default:u64,min:u64,max:u64)->Result<u64> {let raw=std::env::var(name).unwrap_or_else(|_|default.to_string());raw.parse::<u64>().ok().filter(|value|(min..=max).contains(value)).ok_or_else(||ApiError::invalid(format!("{name} is outside the supported range")))}
fn decode_path(value:&str)->Result<String> {
    let mut result=Vec::new();let mut bytes=value.bytes();while let Some(byte)=bytes.next(){if byte==b'%' {let high=bytes.next().and_then(|byte|(byte as char).to_digit(16));let low=bytes.next().and_then(|byte|(byte as char).to_digit(16));match(high,low){(Some(high),Some(low))=>result.push((high*16+low) as u8),_=>return Err(ApiError::invalid("invalid SQLite URL encoding"))}}else{result.push(byte);}}
    String::from_utf8(result).map_err(|_|ApiError::invalid("SQLite path is not UTF-8"))
}
