//! Redis保存可重建的复权日线快照，不设物理TTL；行情新鲜度、收盘补齐与容量分别管理。
use std::{collections::VecDeque,sync::{Arc,Mutex,TryLockError,atomic::{AtomicBool,AtomicU64,Ordering}},time::{Duration,Instant}};
use chrono::{DateTime,Utc,NaiveDate,TimeZone,Timelike};
use serde::{Serialize,Deserialize};
use wavequant_quant_rs::{indexed_json::{Value,json},data::akshare::AkShareProvider};
use super::{backtest::{LoadedMarket,MarketRequest},research::digest};
use crate::{presentation::error::ApiError,storage::cache::RedisCache};
type Result<T>=std::result::Result<T,ApiError>;
type Loader=Arc<dyn Fn(&MarketRequest)->Result<LoadedMarket>+Send+Sync>;
const POLICY:&str="akshare-sina-causal-adjusted-kline-v2";
const MEMORY_BYTES:usize=64*1024*1024;
#[derive(Clone)]
struct Policy{max_entries:usize,max_bytes:usize,max_item:usize,intraday_seconds:i64,batch:usize}
impl Default for Policy{fn default()->Self{Self{max_entries:2048,max_bytes:512*1024*1024,max_item:16*1024*1024,intraday_seconds:60,batch:16}}}
#[derive(Serialize,Deserialize)]
struct Envelope{schema:u32,key:String,created_at:i64,refresh_after:i64,checked_session:Option<String>,request:MarketRequest,market:LoadedMarket}
struct Entry{key:String,payload:Arc<Vec<u8>>}
#[derive(Default)]
struct Memory{entries:VecDeque<Entry>,bytes:usize,evicted:u64}
impl Memory{
    fn get(&mut self,key:&str)->Option<Arc<Vec<u8>>>{let index=self.entries.iter().position(|entry|entry.key==key)?;let entry=self.entries.remove(index)?;let value=entry.payload.clone();self.entries.push_back(entry);Some(value)}
    fn put(&mut self,key:String,payload:Arc<Vec<u8>>){
        self.remove(&key);if payload.len()>MEMORY_BYTES{return;}
        while self.entries.len()>=256||self.bytes+payload.len()>MEMORY_BYTES{if let Some(entry)=self.entries.pop_front(){self.bytes-=entry.payload.len();self.evicted+=1;}else{break;}}
        self.bytes+=payload.len();self.entries.push_back(Entry{key,payload});
    }
    fn remove(&mut self,key:&str){if let Some(index)=self.entries.iter().position(|entry|entry.key==key){if let Some(entry)=self.entries.remove(index){self.bytes-=entry.payload.len();}}}
}
#[derive(Default)]
struct Calendar{days:Vec<String>,loaded_day:String}
pub struct AdjustedMarketCache{
    memory:Mutex<Memory>,gates:[Mutex<()>;64],redis:Option<Arc<RedisCache>>,redis_retry:Mutex<Option<Instant>>,policy:Policy,calendar:Mutex<Calendar>,
    memory_hits:AtomicU64,redis_hits:AtomicU64,misses:AtomicU64,errors:AtomicU64,refreshes:AtomicU64,cursor:AtomicU64,
}
fn local_day(now:DateTime<Utc>)->String{(now+chrono::Duration::hours(8)).date_naive().to_string()}
fn local_timestamp(day:&str,hour:u32,minute:u32)->Option<i64>{let day=NaiveDate::parse_from_str(day,"%Y-%m-%d").ok()?;Some(Utc.from_utc_datetime(&day.and_hms_opt(hour,minute,0)?).timestamp()-8*3600)}
impl AdjustedMarketCache{
    pub fn new(redis:Option<Arc<RedisCache>>)->Self{Self{memory:Mutex::new(Memory::default()),gates:std::array::from_fn(|_|Mutex::new(())),redis:redis.map(|cache|Arc::new(cache.fast_cache())),redis_retry:Mutex::new(None),policy:Policy::default(),calendar:Mutex::new(Calendar::default()),memory_hits:AtomicU64::new(0),redis_hits:AtomicU64::new(0),misses:AtomicU64::new(0),errors:AtomicU64::new(0),refreshes:AtomicU64::new(0),cursor:AtomicU64::new(0)}}
    pub fn from_env(redis:Option<Arc<RedisCache>>)->Result<Self>{
        fn read(name:&str,default:usize,min:usize,max:usize)->Result<usize>{std::env::var(name).unwrap_or_else(|_|default.to_string()).parse::<usize>().ok().filter(|v|(min..=max).contains(v)).ok_or_else(||ApiError::invalid(format!("{name} outside supported range")))}
        let mut cache=Self::new(redis);cache.policy.max_entries=read("WAVEQUANT_KLINE_MAX_ENTRIES",2048,1,10000)?;cache.policy.max_bytes=read("WAVEQUANT_KLINE_MAX_MIB",512,32,4096)?*1024*1024;cache.policy.max_item=read("WAVEQUANT_KLINE_MAX_ITEM_MIB",16,1,32)?*1024*1024;cache.policy.intraday_seconds=read("WAVEQUANT_KLINE_INTRADAY_SECONDS",60,30,900)? as i64;cache.policy.batch=read("WAVEQUANT_KLINE_REFRESH_BATCH",16,1,64)?;Ok(cache)
    }
    fn key(request:&MarketRequest)->Result<String>{let index=wavequant_quant_rs::data::indices::is_index(&request.symbol);Ok(digest(&wavequant_quant_rs::indexed_json::to_vec(&json!([if index{wavequant_quant_rs::data::indices::cache_policy(&request.symbol)}else{POLICY},request.source,request.symbol,request.start,"1d",if index{"unadjusted_index"}else{"causal_adjusted_equivalent"},std::env::var("WAVEQUANT_AKSHARE_PYTHON").ok()]))?))}
    fn redis_ready(&self)->bool{self.redis.is_some()&&self.redis_retry.lock().map(|next|next.is_none_or(|next|Instant::now()>=next)).unwrap_or(false)}
    fn redis_failed(&self){self.errors.fetch_add(1,Ordering::Relaxed);if let Ok(mut next)=self.redis_retry.lock(){*next=Some(Instant::now()+Duration::from_secs(30));}tracing::warn!("Kline Redis unavailable; bounded memory/source fallback for 30 seconds");}
    fn decode(&self,payload:&[u8],key:&str)->Option<Envelope>{
        if payload.len()>self.policy.max_item{return None;}
        let entry:Envelope=wavequant_quant_rs::indexed_json::from_slice(payload).ok()?;
        if entry.schema!=2||entry.key!=key||Self::key(&entry.request).ok()?.as_str()!=key||entry.request.source!="akshare"||entry.created_at>Utc::now().timestamp()+5||entry.refresh_after<=entry.created_at||entry.market.bars.is_empty()||entry.market.bars.len()>20000||entry.market.version.len()!=64{return None;}
        if super::market::parse_day(&entry.request.asof).is_err()||super::market::parse_day(&entry.request.start).is_err()||!wavequant_quant_rs::data::indices::supported(&entry.request.source,&entry.request.symbol){return None;}
        if entry.market.bars.iter().any(|bar|bar.symbol!=entry.request.symbol||bar.timestamp<entry.request.start||bar.timestamp>entry.request.asof||[bar.open,bar.high,bar.low,bar.close,bar.volume,bar.adjustment_factor].iter().any(|n|!n.is_finite())||bar.low<=0.0||bar.low>bar.open.min(bar.close)||bar.high<bar.open.max(bar.close)||bar.volume<0.0||bar.adjustment_factor<=0.0)||entry.market.bars.windows(2).any(|pair|pair[0].timestamp>=pair[1].timestamp){return None;}
        Some(entry)
    }
    fn get(&self,key:&str,touch:bool)->Option<(Envelope,&'static str)>{
        // Redis是共享事实缓存；短时L1只在Redis不可用时回退，避免L1隐藏跨实例刷新/淘汰。
        if self.redis_ready(){if let Some(redis)=&self.redis{match redis.kline_get(key,self.policy.max_item,touch){
            Ok(Some(payload))=>{if let Some(entry)=self.decode(&payload,key){if let Ok(mut memory)=self.memory.lock(){memory.put(key.into(),Arc::new(payload));}self.redis_hits.fetch_add(1,Ordering::Relaxed);return Some((entry,"redis"));}self.errors.fetch_add(1,Ordering::Relaxed);return None;},
            Ok(None)=>return None,Err(_)=>self.redis_failed(),
        }}}
        let payload=self.memory.lock().ok()?.get(key)?;let entry=self.decode(&payload,key)?;self.memory_hits.fetch_add(1,Ordering::Relaxed);Some((entry,"memory_fallback"))
    }
    fn freshness(&self,now:DateTime<Utc>,tail:&str)->(i64,Option<String>){
        let local=now+chrono::Duration::hours(8);let today=local.date_naive().to_string();let minute=local.hour()*60+local.minute();
        let Ok(calendar)=self.calendar.lock()else{return (now.timestamp()+300,None)};
        if calendar.days.is_empty()||calendar.loaded_day!=today{return (now.timestamp()+300,None);}
        let is_session=calendar.days.binary_search(&today).is_ok();
        let target=calendar.days.iter().rev().find(|day|day.as_str()<today.as_str()||(day.as_str()==today.as_str()&&minute>=16*60+10)).cloned();
        if is_session&&(9*60+15..16*60+10).contains(&minute){return (now.timestamp()+self.policy.intraday_seconds,target.filter(|day|tail>=day.as_str()));}
        // 上游尚未补齐收盘或停牌无法证明尾部完整时保留旧数据，并继续重试，不能伪造交易日K线。
        if target.as_ref().is_some_and(|day|tail<day.as_str()){return (now.timestamp()+300,None);}
        let next_open=calendar.days.iter().filter_map(|day|local_timestamp(day,9,15)).find(|timestamp|*timestamp>now.timestamp()).unwrap_or(now.timestamp()+3600);
        (next_open,target)
    }
    fn fresh(&self,entry:&Envelope,request:&MarketRequest)->bool{
        if request.asof>entry.request.asof||Utc::now().timestamp()>=entry.refresh_after{return false;}
        let now=Utc::now();let (deadline,session)=self.freshness(now,entry.market.bars.last().map(|bar|bar.timestamp.as_str()).unwrap_or(""));
        // 即使磁盘/Redis中的旧逻辑截止时间较远，新的收盘交易日也必须触发刷新。
        if session>entry.checked_session&&now.timestamp()>=entry.created_at+300{return false;}
        if deadline==now.timestamp()+self.policy.intraday_seconds&&entry.created_at+self.policy.intraday_seconds<=now.timestamp(){return false;}
        deadline>now.timestamp()
    }
    fn project(entry:Envelope,request:&MarketRequest,backend:&str,hit:bool)->Result<LoadedMarket>{
        let Envelope{created_at,refresh_after,checked_session,mut market,..}=entry;
        market.bars.retain(|bar|bar.timestamp<=request.asof);market.minutes.clear();market.missing.clear();
        if market.bars.is_empty(){return Err(ApiError::invalid("所选区间没有可用日线"));}
        // 后续交易日或因子新增不能改变历史前缀身份；规则升级通过POLICY显式失效。
        let prefix=digest(&serde_json::to_vec(&market.bars).map_err(|_|ApiError::internal())?);
        let policy=if wavequant_quant_rs::data::indices::is_index(&request.symbol){wavequant_quant_rs::data::indices::cache_policy(&request.symbol)}else{POLICY};
        market.version=digest(&wavequant_quant_rs::indexed_json::to_vec(&json!([policy,request.source,request.symbol,request.start,request.asof,prefix]))?);
        market.provenance["source_version"]=json!(market.version);
        let refresh_policy=if wavequant_quant_rs::data::indices::is_index(&request.symbol){"calendar_close_reconcile_index_daily_atomic_replace"}else{"calendar_close_reconcile_daily_and_factors_atomic_replace"};
        market.provenance["adjusted_cache"]=json!({"hit":hit,"backend":backend,"created_at":created_at,"refresh_after":refresh_after,"checked_session":checked_session,"expires_at":null,"refresh_policy":refresh_policy,"schema":2});Ok(market)
    }
    fn rebuild(&self,key:&str,request:&MarketRequest,touch:bool,timeout:Duration,load:&dyn Fn(&MarketRequest)->Result<LoadedMarket>)->Result<Option<Envelope>>{
        let lease=(timeout.as_secs()*2+15).clamp(30,180);
        let mut owner=None;if self.redis_ready(){if let Some(redis)=&self.redis{match redis.kline_lock(key,lease){Ok(Some(token))=>owner=Some(token),Ok(None)=>return Ok(None),Err(_)=>self.redis_failed()}}}
        let previous=self.get(key,false).map(|(entry,_)|entry);
        let result=(||{
            let canonical=MarketRequest{asof:local_day(Utc::now()),..request.clone()};self.misses.fetch_add(1,Ordering::Relaxed);
            // SDK实际提供完整历史。完整对账既补尾部也接收历史修订/因子修订，不能把旧复权序列简单拼接。
            let market=load(&canonical)?;
            // 不完整的成功响应不能使已知历史消失；因子改变时也不能拿旧复权价格填补新版本。
            if let Some(old)=&previous{if old.market.bars.iter().any(|bar|market.bars.binary_search_by(|current|current.timestamp.cmp(&bar.timestamp)).is_err()){return Err(ApiError::unavailable("上游历史覆盖缩短，保留上次K线缓存并稍后重试"));}}
            let now=Utc::now();let (refresh_after,checked_session)=self.freshness(now,market.bars.last().map(|bar|bar.timestamp.as_str()).unwrap_or(""));
            let entry=Envelope{schema:2,key:key.into(),created_at:now.timestamp(),refresh_after,checked_session,request:canonical,market};
            let payload=wavequant_quant_rs::indexed_json::to_vec(&entry)?;
            if payload.len()<=self.policy.max_item{
                if let (Some(redis),Some(token))=(&self.redis,owner.as_deref()){
                    match redis.kline_set(key,&payload,token,self.policy.max_entries,self.policy.max_bytes,self.policy.max_item,touch){Ok(true)=>tracing::info!(symbol=%request.symbol,background=!touch,payload_bytes=payload.len(),"kline_cache_redis_saved"),Ok(false)=>tracing::warn!("Kline lease expired; newer shared snapshot retained"),Err(_)=>self.redis_failed()}
                }
                if let Ok(mut memory)=self.memory.lock(){memory.put(key.into(),Arc::new(payload));}
            }else{self.errors.fetch_add(1,Ordering::Relaxed);tracing::warn!(symbol=%request.symbol,"Kline exceeds single-item cache budget");}
            Ok(Some(entry))
        })();
        if let (Some(redis),Some(token))=(&self.redis,owner.as_deref()){if redis.kline_unlock(key,token).is_err(){self.redis_failed();}}
        result
    }
    pub fn get_or_load(&self,request:&MarketRequest,_engine:&str,minutes:bool,_fallback:bool,cancel:Option<&Arc<AtomicBool>>,timeout:Duration,load:impl Fn(&MarketRequest)->Result<LoadedMarket>)->Result<LoadedMarket>{
        if minutes||request.source!="akshare"{return load(request);}
        super::market::parse_day(&request.start)?;super::market::parse_day(&request.asof)?;
        if request.start>request.asof||request.asof>local_day(Utc::now())||!wavequant_quant_rs::data::indices::supported(&request.source,&request.symbol){return Err(ApiError::invalid("invalid cached Kline selection"));}
        let started=Instant::now();let key=Self::key(request)?;
        if let Some((entry,backend))=self.get(&key,true){if self.fresh(&entry,request){tracing::info!(symbol=%request.symbol,backend,lookup_ms=started.elapsed().as_secs_f64()*1000.0,"kline_cache_hit");return Self::project(entry,request,backend,true);}let reason=if request.asof>entry.request.asof{"coverage_extended"}else if Utc::now().timestamp()>=entry.refresh_after{"logical_refresh_due"}else{"calendar_phase_changed"};tracing::info!(symbol=%request.symbol,backend,reason,created_at=entry.created_at,refresh_after=entry.refresh_after,"kline_cache_refresh_required");}else{tracing::info!(symbol=%request.symbol,reason="absent_or_invalid","kline_cache_miss");}
        let stripe=key.bytes().fold(0usize,|n,b|n.wrapping_mul(31).wrapping_add(usize::from(b)))%self.gates.len();
        let _gate=loop{
            if cancel.is_some_and(|flag|flag.load(Ordering::Acquire)){return Err(ApiError::unavailable("行情缓存等待已取消"));}
            match self.gates[stripe].try_lock(){Ok(guard)=>break guard,Err(TryLockError::Poisoned(_))=>return Err(ApiError::internal()),Err(TryLockError::WouldBlock)=>{if started.elapsed()>=timeout{return Err(ApiError::unavailable("等待行情缓存超时"));}std::thread::sleep(Duration::from_millis(10));}}
        };
        loop{
            if cancel.is_some_and(|flag|flag.load(Ordering::Acquire)){return Err(ApiError::unavailable("行情缓存等待已取消"));}
            if let Some((entry,backend))=self.get(&key,true){if self.fresh(&entry,request){return Self::project(entry,request,backend,true);}}
            if started.elapsed()>=timeout{return Err(ApiError::unavailable("等待共享行情刷新超时"));}
            if let Some(entry)=self.rebuild(&key,request,true,timeout,&load)?{tracing::info!(symbol=%request.symbol,total_ms=started.elapsed().as_secs_f64()*1000.0,"kline_cache_source_timing");return Self::project(entry,request,"source",false);}
            std::thread::sleep(Duration::from_millis(100));
        }
    }
    fn update_calendar(&self,timeout:Duration)->Result<()>{
        let today=local_day(Utc::now());if self.calendar.lock().map_err(|_|ApiError::internal())?.loaded_day==today{return Ok(());}
        let mut days=None;
        if self.redis_ready(){if let Some(redis)=&self.redis{if let Ok(Some(value))=redis.get_json("market-kline-v2:calendar"){if value["loaded_day"]==today{days=wavequant_quant_rs::indexed_json::from_value::<Vec<String>>(value["days"].clone()).ok();}}}}
        let days=match days{Some(days)=>days,None=>{
            let days=AkShareProvider::new(timeout).and_then(|provider|provider.calendar()).map_err(|_|ApiError::unavailable("交易日历暂不可用，稍后重试收盘更新"))?;
            if let Some(redis)=&self.redis{if redis.set_json("market-kline-v2:calendar",&json!({"loaded_day":today,"days":days}),Some(86400)).is_err(){self.redis_failed();}}days
        }};
        if days.is_empty()||days.len()>100000||days.iter().any(|day|super::market::parse_day(day).is_err())||days.windows(2).any(|pair|pair[0]>=pair[1]){return Err(ApiError::unavailable("交易日历无效"));}
        *self.calendar.lock().map_err(|_|ApiError::internal())?=Calendar{days,loaded_day:today};Ok(())
    }
    pub fn start_refresh(self:&Arc<Self>,timeout:Duration,loader:Loader){
        let weak=Arc::downgrade(self);tokio::spawn(async move{
            let mut timer=tokio::time::interval(Duration::from_secs(60));timer.tick().await;
            loop{timer.tick().await;let Some(cache)=weak.upgrade()else{break;};let loader=loader.clone();
                let result=tokio::task::spawn_blocking(move||->Result<()>{
                    if !cache.redis_ready(){return Ok(());}let redis=cache.redis.as_ref().ok_or_else(ApiError::internal)?;
                    let keys=redis.kline_keys(cache.policy.max_entries)?;if keys.is_empty(){return Ok(());}
                    cache.update_calendar(timeout)?;
                    let local=Utc::now()+chrono::Duration::hours(8);let minute=local.hour()*60+local.minute();
                    // 活跃时段由真实请求刷新，后台只在收盘结算后/下次开盘前补齐，避免抢占交互SDK。
                    if (9*60+15..16*60+10).contains(&minute)&&cache.calendar.lock().map_err(|_|ApiError::internal())?.days.binary_search(&local.date_naive().to_string()).is_ok(){return Ok(());}
                    let offset=cache.cursor.fetch_add(cache.policy.batch as u64,Ordering::Relaxed) as usize;
                    for step in 0..cache.policy.batch.min(keys.len()){
                        let key=&keys[(offset+step)%keys.len()];let Some((entry,_))=cache.get(key,false)else{continue;};
                        if cache.fresh(&entry,&entry.request){continue;}
                        if cache.rebuild(key,&entry.request,false,timeout,loader.as_ref())?.is_some(){cache.refreshes.fetch_add(1,Ordering::Relaxed);}
                    }Ok(())
                }).await;
                if !matches!(result,Ok(Ok(()))){tracing::warn!("Kline close reconciliation incomplete; retained last snapshot and will retry");}
            }
        });
    }
    pub fn statistics(&self)->Value{
        let memory=self.memory.lock().unwrap_or_else(|poison|poison.into_inner());
        json!({"mode":if self.redis.is_some(){"redis_persistent_kline_lru"}else{"bounded_memory_fallback"},"physical_ttl_seconds":null,"redis_max_entries":self.policy.max_entries,"redis_max_payload_bytes":self.policy.max_bytes,"max_item_bytes":self.policy.max_item,"intraday_refresh_seconds":self.policy.intraday_seconds,"close_reconcile_time":"16:10 Asia/Shanghai","refresh_batch":self.policy.batch,"memory_entries":memory.entries.len(),"memory_payload_bytes":memory.bytes,"memory_max_payload_bytes":MEMORY_BYTES,"evicted":memory.evicted,"memory_hits":self.memory_hits.load(Ordering::Relaxed),"redis_hits":self.redis_hits.load(Ordering::Relaxed),"source_loads":self.misses.load(Ordering::Relaxed),"cache_errors":self.errors.load(Ordering::Relaxed),"background_reconciliations":self.refreshes.load(Ordering::Relaxed)})
    }
}

#[cfg(test)]
mod tests{
    use super::*;
    #[test]
    fn closed_snapshot_survives_weekend_until_the_next_real_session(){
        let cache=AdjustedMarketCache::new(None);*cache.calendar.lock().unwrap()=Calendar{days:vec!["2026-10-23".into(),"2026-10-26".into()],loaded_day:"2026-10-24".into()};
        let now=Utc.with_ymd_and_hms(2026,10,24,2,0,0).single().unwrap();
        let (refresh,session)=cache.freshness(now,"2026-10-23");
        assert_eq!(session.as_deref(),Some("2026-10-23"));assert_eq!(refresh,local_timestamp("2026-10-26",9,15).unwrap());
    }
    #[test]
    fn absent_close_bar_is_pending_and_retried_without_claiming_coverage(){
        let cache=AdjustedMarketCache::new(None);*cache.calendar.lock().unwrap()=Calendar{days:vec!["2026-10-23".into(),"2026-10-26".into(),"2026-10-27".into()],loaded_day:"2026-10-26".into()};
        let now=Utc.with_ymd_and_hms(2026,10,26,9,0,0).single().unwrap();
        let (refresh,session)=cache.freshness(now,"2026-10-23");assert_eq!(refresh,now.timestamp()+300);assert_eq!(session,None);
    }
    #[test]
    fn future_tail_changes_do_not_change_a_historical_view_version(){
        use wavequant_quant_rs::data::adjustments::ExecutionBar;
        fn entry(last:f64)->Envelope{
            let bars=[("2026-10-22",10.0),("2026-10-23",last)].into_iter().map(|(day,price)|ExecutionBar{timestamp:day.into(),symbol:"sz.000978".into(),open:price,high:price,low:price,close:price,volume:100.0,adjustment_factor:1.0,buyable:true,sellable:true,close_buyable:None,close_sellable:None,nonflat_close_buyable:None,nonflat_close_sellable:None,raw_is_st:None,raw_trading_active:None}).collect();
            Envelope{schema:2,key:String::new(),created_at:1,refresh_after:2,checked_session:None,request:MarketRequest{source:"akshare".into(),symbol:"sz.000978".into(),start:"2018-01-01".into(),asof:"2026-10-23".into()},market:LoadedMarket{bars,minutes:Default::default(),version:"a".repeat(64),provenance:json!({}),files:vec![],missing:vec![]}}
        }
        let request=MarketRequest{source:"akshare".into(),symbol:"sz.000978".into(),start:"2018-01-01".into(),asof:"2026-10-22".into()};
        let first=AdjustedMarketCache::project(entry(20.0),&request,"redis",true).unwrap();let revised=AdjustedMarketCache::project(entry(30.0),&request,"redis",true).unwrap();
        assert_eq!(first.bars.len(),1);assert_eq!(first.version,revised.version);
    }
    #[test]
    fn capacity_evicts_the_least_recently_accessed_snapshot(){
        let mut memory=Memory::default();for i in 0..256{memory.put(i.to_string(),Arc::new(vec![1]));}
        assert!(memory.get("0").is_some());memory.put("new".into(),Arc::new(vec![2]));assert!(memory.get("1").is_none());assert!(memory.get("0").is_some());assert_eq!(memory.bytes,256);
    }
}
