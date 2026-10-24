//! Redis 只保存可重建 JSON；分布式锁使用随机所有权令牌和比较删除。
use std::{thread,time::{Duration,Instant}};
use wavequant_quant_rs::indexed_json::Value;
use crate::presentation::error::ApiError;
type Result<T>=std::result::Result<T,ApiError>;
#[derive(Clone)]
pub struct RedisCache {client:redis::Client,prefix:String,ttl:u64,timeout:Duration,pub tls:bool}
impl RedisCache {
    pub fn new(url:&str,prefix:String,ttl:u64,timeout:Duration)->Result<Self> {
        if !(url.starts_with("redis://")||url.starts_with("rediss://"))||url.contains('#'){return Err(ApiError::invalid("Redis URL must use redis/rediss without TLS bypass fragments"));}
        if prefix.is_empty()||prefix.len()>64||!prefix.bytes().all(|byte|byte.is_ascii_alphanumeric()||matches!(byte,b':'|b'_'|b'-'))||!(1..=86400).contains(&ttl){return Err(ApiError::invalid("invalid Redis prefix or TTL"));}
        let client=redis::Client::open(url).map_err(|_|ApiError::invalid("invalid Redis URL"))?;
        Ok(Self{client,prefix,ttl,timeout,tls:url.starts_with("rediss://")})
    }
    fn connection(&self)->Result<redis::Connection> {
        let connection=self.client.get_connection_with_timeout(self.timeout).map_err(|_|unavailable())?;
        connection.set_read_timeout(Some(self.timeout)).map_err(|_|unavailable())?;
        connection.set_write_timeout(Some(self.timeout)).map_err(|_|unavailable())?;Ok(connection)
    }
    fn key(&self,key:&str)->Result<String>{if key.is_empty()||key.len()>192||!key.bytes().all(|byte|byte.is_ascii_alphanumeric()||matches!(byte,b':'|b'.'|b'_'|b'-')){return Err(ApiError::invalid("Redis key contains unsupported characters"));}Ok(format!("{}:{key}",self.prefix))}
    pub fn ping(&self)->Result<()> {let answer:String=redis::cmd("PING").query(&mut self.connection()?).map_err(|_|unavailable())?;if answer!="PONG"{return Err(unavailable());}Ok(())}
    pub fn get_json(&self,key:&str)->Result<Option<Value>> {
        let raw:Option<String>=redis::cmd("GET").arg(self.key(key)?).query(&mut self.connection()?).map_err(|_|unavailable())?;
        raw.map(|value|wavequant_quant_rs::indexed_json::from_str(&value).map_err(|_|ApiError::unavailable("Redis JSON cache is invalid"))).transpose()
    }
    pub fn set_json(&self,key:&str,value:&Value,ttl:Option<u64>)->Result<()> {
        let ttl=ttl.unwrap_or(self.ttl);if !(1..=86400).contains(&ttl){return Err(ApiError::invalid("Redis TTL must be 1..86400 seconds"));}
        let _:String=redis::cmd("SET").arg(self.key(key)?).arg(wavequant_quant_rs::indexed_json::to_string(value)?).arg("EX").arg(ttl).query(&mut self.connection()?).map_err(|_|unavailable())?;Ok(())
    }
    pub fn delete(&self,key:&str)->Result<()> {let _:u64=redis::cmd("DEL").arg(self.key(key)?).query(&mut self.connection()?).map_err(|_|unavailable())?;Ok(())}
    pub fn fast_cache(&self)->Self{let mut cache=self.clone();cache.timeout=cache.timeout.min(Duration::from_millis(200));cache}
    /// Lua 先检查长度再返回，避免损坏或误写的大值进入应用内存。
    pub fn market_get(&self,key:&str,limit:usize)->Result<Option<Vec<u8>>>{
        let data=self.key(&format!("market-adjusted-v1:{key}"))?;let lru=self.key("market-adjusted-v1:lru")?;let sizes=self.key("market-adjusted-v1:sizes")?;
        redis::Script::new("local n=redis.call('strlen',KEYS[1]); if n==0 then return nil end; if n>tonumber(ARGV[1]) then redis.call('del',KEYS[1]); redis.call('zrem',KEYS[2],KEYS[1]); redis.call('hdel',KEYS[3],KEYS[1]); return nil end; local v=redis.call('get',KEYS[1]); if v then redis.call('zadd',KEYS[2],ARGV[2],KEYS[1]); redis.call('hset',KEYS[3],KEYS[1],n); redis.call('expire',KEYS[2],3600); redis.call('expire',KEYS[3],3600) end; return v")
            .key(data).key(lru).key(sizes).arg(limit).arg(chrono::Utc::now().timestamp_millis()).invoke(&mut self.connection()?).map_err(|_|unavailable())
    }
    /// 独立命名空间同时限制条目数与有效载荷字节，不修改共享 Redis 全局策略。
    pub fn market_set(&self,key:&str,payload:&[u8],ttl:u64,max_entries:usize,max_bytes:usize,max_item:usize)->Result<()>{
        if ttl==0||ttl>300||payload.len()>max_item||payload.len()>max_bytes{return Err(ApiError::invalid("invalid market cache budget"));}
        let data=self.key(&format!("market-adjusted-v1:{key}"))?;let lru=self.key("market-adjusted-v1:lru")?;let sizes=self.key("market-adjusted-v1:sizes")?;let base=format!("{}:market-adjusted-v1:",self.prefix);
        let script="local rows=redis.call('zrange',KEYS[2],0,-1); local total=0; for _,k in ipairs(rows) do if string.sub(k,1,string.len(ARGV[7]))~=ARGV[7] or redis.call('exists',k)==0 then redis.call('zrem',KEYS[2],k); redis.call('hdel',KEYS[3],k) else total=total+tonumber(redis.call('hget',KEYS[3],k) or '0') end end; total=total-tonumber(redis.call('hget',KEYS[3],KEYS[1]) or '0')+string.len(ARGV[1]); redis.call('set',KEYS[1],ARGV[1],'EX',ARGV[2]); redis.call('zadd',KEYS[2],ARGV[3],KEYS[1]); redis.call('hset',KEYS[3],KEYS[1],string.len(ARGV[1])); while total>tonumber(ARGV[5]) or redis.call('zcard',KEYS[2])>tonumber(ARGV[4]) do local old=redis.call('zrange',KEYS[2],0,0)[1]; if not old then break end; total=total-tonumber(redis.call('hget',KEYS[3],old) or '0'); redis.call('del',old); redis.call('zrem',KEYS[2],old); redis.call('hdel',KEYS[3],old) end; redis.call('expire',KEYS[2],3600); redis.call('expire',KEYS[3],3600); return 1";
        let _:i64=redis::Script::new(script).key(data).key(lru).key(sizes).arg(payload).arg(ttl).arg(chrono::Utc::now().timestamp_millis()).arg(max_entries).arg(max_bytes).arg(max_item).arg(base).invoke(&mut self.connection()?).map_err(|_|unavailable())?;Ok(())
    }
    // v2 K线键无TTL。数据、LRU与字节账本在同一个Lua事务内更新。
    pub fn kline_get(&self,id:&str,limit:usize,touch:bool)->Result<Option<Vec<u8>>>{
        let data=self.key(&format!("market-kline-v2:{id}"))?;
        redis::Script::new("local n=redis.call('strlen',KEYS[1]); if n==0 or n>tonumber(ARGV[1]) then return nil end; if ARGV[2]=='1' then redis.call('zadd',KEYS[2],ARGV[3],KEYS[1]) end; return redis.call('get',KEYS[1])")
            .key(data).key(self.key("market-kline-v2:lru")?).arg(limit).arg(if touch{1}else{0}).arg(chrono::Utc::now().timestamp_millis()).invoke(&mut self.connection()?).map_err(|_|unavailable())
    }
    pub fn kline_keys(&self,limit:usize)->Result<Vec<String>>{
        let base=format!("{}:market-kline-v2:",self.prefix);
        let keys:Vec<String>=redis::cmd("ZRANGE").arg(self.key("market-kline-v2:lru")?).arg(0).arg(limit.saturating_sub(1)).query(&mut self.connection()?).map_err(|_|unavailable())?;
        Ok(keys.into_iter().filter_map(|key|key.strip_prefix(&base).filter(|id|id.len()==64&&id.bytes().all(|b|b.is_ascii_hexdigit())).map(str::to_owned)).collect())
    }
    pub fn kline_lock(&self,id:&str,lease:u64)->Result<Option<String>>{
        let token=uuid::Uuid::new_v4().to_string();let result:Option<String>=redis::cmd("SET").arg(self.key(&format!("market-kline-v2:lock:{id}"))?).arg(&token).arg("NX").arg("EX").arg(lease.clamp(10,180)).query(&mut self.connection()?).map_err(|_|unavailable())?;Ok(result.map(|_|token))
    }
    pub fn kline_unlock(&self,id:&str,token:&str)->Result<()>{
        let _:i64=redis::Script::new("if redis.call('get',KEYS[1])==ARGV[1] then return redis.call('del',KEYS[1]) else return 0 end").key(self.key(&format!("market-kline-v2:lock:{id}"))?).arg(token).invoke(&mut self.connection()?).map_err(|_|unavailable())?;Ok(())
    }
    pub fn kline_set(&self,id:&str,payload:&[u8],token:&str,max_entries:usize,max_bytes:usize,max_item:usize,touch:bool)->Result<bool>{
        if payload.len()>max_item||payload.len()>max_bytes{return Err(ApiError::invalid("Kline cache item exceeds budget"));}
        let base=format!("{}:market-kline-v2:",self.prefix);
        let script=r#"if redis.call('get',KEYS[4])~=ARGV[2] then return 0 end
if ARGV[7]=='0' and redis.call('exists',KEYS[1])==0 then return 0 end
local total=0
for _,k in ipairs(redis.call('zrange',KEYS[2],0,-1)) do
 if string.sub(k,1,string.len(ARGV[6]))~=ARGV[6] or redis.call('exists',k)==0 then redis.call('zrem',KEYS[2],k);redis.call('hdel',KEYS[3],k)
 else total=total+tonumber(redis.call('hget',KEYS[3],k) or redis.call('strlen',k)) end
end
local old=tonumber(redis.call('hget',KEYS[3],KEYS[1]) or '0')
local score=redis.call('zscore',KEYS[2],KEYS[1])
local newkey=redis.call('exists',KEYS[1])==0 and 1 or 0
while total-old+string.len(ARGV[1])>tonumber(ARGV[4]) or redis.call('zcard',KEYS[2])+newkey>tonumber(ARGV[3]) do
 local victim=nil
 for _,k in ipairs(redis.call('zrange',KEYS[2],0,1)) do if k~=KEYS[1] then victim=k;break end end
 if not victim then break end
 total=total-tonumber(redis.call('hget',KEYS[3],victim) or '0');redis.call('del',victim);redis.call('zrem',KEYS[2],victim);redis.call('hdel',KEYS[3],victim)
end
redis.call('set',KEYS[1],ARGV[1]);redis.call('hset',KEYS[3],KEYS[1],string.len(ARGV[1]))
if ARGV[7]=='1' or not score then score=ARGV[5] end
redis.call('zadd',KEYS[2],score,KEYS[1]);return 1"#;
        let saved:i64=redis::Script::new(script).key(self.key(&format!("market-kline-v2:{id}"))?).key(self.key("market-kline-v2:lru")?).key(self.key("market-kline-v2:sizes")?).key(self.key(&format!("market-kline-v2:lock:{id}"))?).arg(payload).arg(token).arg(max_entries).arg(max_bytes).arg(chrono::Utc::now().timestamp_millis()).arg(base).arg(if touch{1}else{0}).invoke(&mut self.connection()?).map_err(|_|unavailable())?;Ok(saved==1)
    }
    pub fn with_lock<T>(&self,key:&str,timeout:u64,wait:u64,work:impl FnOnce()->Result<T>)->Result<Option<T>> {
        if !(1..=300).contains(&timeout)||wait>30{return Err(ApiError::invalid("Redis lock timeout is outside supported range"));}
        let key=self.key(&format!("lock:{key}"))?;let token=uuid::Uuid::new_v4().to_string();let started=Instant::now();let mut connection=self.connection()?;
        loop {
            let acquired:Option<String>=redis::cmd("SET").arg(&key).arg(&token).arg("NX").arg("EX").arg(timeout).query(&mut connection).map_err(|_|unavailable())?;
            if acquired.is_some(){break;}if started.elapsed()>=Duration::from_secs(wait){return Ok(None);}thread::sleep(Duration::from_millis(50));
        }
        let result=work();
        let released:i64=redis::Script::new("if redis.call('get',KEYS[1])==ARGV[1] then return redis.call('del',KEYS[1]) else return 0 end").key(&key).arg(&token).invoke(&mut connection).map_err(|_|unavailable())?;
        if released!=1{return Err(ApiError::unavailable("Redis lock ownership expired before release"));}
        result.map(Some)
    }
}
fn unavailable()->ApiError{ApiError::unavailable("Redis cache is unavailable")}
