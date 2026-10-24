//! 新浪分钟历史的同源档案；保存观察到的完整时钟网格，消费时仍核对日线事实。
use std::{collections::BTreeMap,path::{Path,PathBuf},sync::Mutex,time::Duration};
use chrono::{DateTime,Utc};
use rusqlite::{Connection,OptionalExtension,params};
use crate::indexed_json::{Value,json};
use sha2::{Digest,Sha256};
use super::{akshare::AkShareProvider,minute::{MinuteBar,MinuteError,verify_minute_day},adjustments::ExecutionBar,tdx::DataError};
fn invalid(s:&str)->MinuteError {MinuteError::Invalid(s.into())}
fn sql(_:rusqlite::Error)->MinuteError {MinuteError::Data(DataError::Unavailable("同源分钟档案暂不可用".into()))}
#[derive(Clone)]
struct ObservedRows {rows:BTreeMap<String,Vec<MinuteBar>>,version:String}
pub struct SinaMinuteArchive {
    path:PathBuf,provider:AkShareProvider,symbol:String,
    observed:Mutex<Option<ObservedRows>>,digests:Mutex<BTreeMap<String,Value>>,
}
impl SinaMinuteArchive {
    pub fn open(path:impl AsRef<Path>,provider:AkShareProvider,symbol:&str)->Result<Self,MinuteError> {
        if !super::tdx::is_stock(symbol) {return Err(invalid("invalid minute archive symbol"));}
        let path=path.as_ref().to_path_buf();
        if let Some(parent)=path.parent().filter(|p|!p.as_os_str().is_empty()){std::fs::create_dir_all(parent)?;}
        let archive=Self{path,provider,symbol:symbol.into(),observed:Mutex::new(None),digests:Mutex::new(BTreeMap::new())};
        // SDK 来源与旧直接抓取档案隔离，不为旧行重新标注来源。
        archive.connect()?.execute_batch("CREATE TABLE IF NOT EXISTS akshare_sdk_minute_history_v1(symbol TEXT NOT NULL,session TEXT NOT NULL,source_version TEXT NOT NULL,known_at INTEGER NOT NULL,payload_digest TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(symbol,session,source_version)); CREATE INDEX IF NOT EXISTS akshare_sdk_minute_history_lookup_v1 ON akshare_sdk_minute_history_v1(symbol,session,known_at);").map_err(sql)?;
        Ok(archive)
    }
    fn connect(&self)->Result<Connection,MinuteError> {
        let c=Connection::open(&self.path).map_err(sql)?;c.busy_timeout(Duration::from_millis(250)).map_err(sql)?;Ok(c)
    }
    fn store_observation(&self,rows:&BTreeMap<String,Vec<MinuteBar>>,version:&str,now:i64)->Result<(),MinuteError> {
        let mut c=self.connect()?;let tx=c.transaction().map_err(sql)?;
        for (day,bars) in rows {
            if bars.len()!=48 {continue;}
            let payload=crate::indexed_json::to_string(bars).map_err(|_|invalid("minute archive serialization failed"))?;
            let digest=format!("{:x}",Sha256::digest(payload.as_bytes()));
            tx.execute("INSERT OR IGNORE INTO akshare_sdk_minute_history_v1 VALUES(?,?,?,?,?,?)",params![self.symbol,day,version,now,digest,payload]).map_err(sql)?;
        }
        tx.commit().map_err(sql)?;Ok(())
    }
    fn cached(&self,day:&str,now:i64)->Result<Option<(Vec<MinuteBar>,String,i64)>,MinuteError> {
        let row:Option<(String,String,String,i64)>=self.connect()?.query_row("SELECT payload,payload_digest,source_version,known_at FROM akshare_sdk_minute_history_v1 WHERE symbol=? AND session=? AND known_at<=? ORDER BY known_at DESC,source_version LIMIT 1",params![self.symbol,day,now],|r|Ok((r.get(0)?,r.get(1)?,r.get(2)?,r.get(3)?))).optional().map_err(sql)?;
        let Some((payload,digest,version,known_at))=row else {return Ok(None)};
        if payload.len()>1024*1024 || format!("{:x}",Sha256::digest(payload.as_bytes()))!=digest {return Err(invalid("minute archive integrity mismatch"));}
        let bars=crate::indexed_json::from_str::<Vec<MinuteBar>>(&payload).map_err(|_|invalid("minute archive invalid JSON"))?;
        Ok(Some((bars,version,known_at)))
    }
    pub fn get(&self,daily:&ExecutionBar,now:DateTime<Utc>)->Result<Vec<MinuteBar>,MinuteError> {
        if daily.symbol!=self.symbol {return Err(invalid("minute and daily symbol mismatch"));}
        let mut observed=self.observed.lock().map_err(|_|invalid("minute state unavailable"))?;
        let mut upstream_error=None;
        if observed.is_none() {
            match self.provider.minute_history(&self.symbol) {
                Ok((rows,version))=>{self.store_observation(&rows,&version,now.timestamp())?;*observed=Some(ObservedRows{rows,version});}
                Err(error)=>upstream_error=Some(error),
            }
        }
        let live=observed.as_ref().and_then(|o|o.rows.get(&daily.timestamp).filter(|r|r.len()==48).map(|r|(r.clone(),o.version.clone(),now.timestamp(),false)));
        let selection=match live {Some(value)=>Some(value),None=>self.cached(&daily.timestamp,now.timestamp())?.map(|(rows,version,time)|(rows,version,time,true))};
        let Some((rows,version,known_at,cache_hit))=selection else {
            if let Some(error)=upstream_error {return Err(error.into());}
            let complete:Vec<_>=observed.as_ref().map(|o|o.rows.iter().filter(|(_,r)|r.len()==48).map(|(d,_)|d.clone()).collect()).unwrap_or_default();
            return Err(MinuteError::Coverage{required_date:daily.timestamp.clone(),available_start:complete.first().cloned(),available_end:complete.last().cloned(),provider:"AKShare / 新浪".into()});
        };
        match verify_minute_day(&rows,daily) {
            Err(MinuteError::VolumeMismatch{minute_volume,daily_volume,..}) if minute_volume<daily_volume=>return Err(MinuteError::Coverage{required_date:daily.timestamp.clone(),available_start:None,available_end:None,provider:"AKShare / 新浪（分钟成交量不完整）".into()}),
            Err(error)=>return Err(error),Ok(())=>{}
        }
        self.digests.lock().map_err(|_|invalid("minute provenance state unavailable"))?.insert(daily.timestamp.clone(),json!({"source_sha256":version,"observed_at_unix":known_at,"cache_hit":cache_hit}));
        Ok(rows)
    }
    pub fn provenance(&self)->Result<Value,MinuteError> {
        let digests=self.digests.lock().map_err(|_|invalid("minute provenance state unavailable"))?;
        Ok(json!({"provider":"akshare","transport":"python_sdk_process","upstream":"sina","endpoint":"stock_zh_a_minute","frequency":"5m","adjustment":"raw_then_same_daily_factor","volume_unit":"shares","sessions":&*digests}))
    }
}
