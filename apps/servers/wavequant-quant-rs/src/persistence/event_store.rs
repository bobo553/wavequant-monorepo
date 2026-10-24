use super::{StoreResult,StoreError,canonical,sha256,require};
use chrono::{DateTime,Utc,SecondsFormat};
use rusqlite::{Connection,OpenFlags,OptionalExtension,params};
use serde::{Serialize,Deserialize};
use crate::indexed_json::{Value,json};
use std::{path::{Path,PathBuf},time::Duration};

#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct StoredEvent{pub seq:u64,pub event_id:String,pub kind:String,pub event_time:String,pub payload:Value,pub previous_hash:String,pub digest:String}
pub struct EventStore{pub path:PathBuf,db:Connection,readonly:bool}
impl EventStore{
    /// 独立 Rust 编码版本，不向 Python 数字编码账本追加另一种哈希格式。
    pub fn open(path:&Path,readonly:bool)->StoreResult<Self>{
        if readonly{require(path.is_file(),"existing journal required for read-only access")?;}
        else if let Some(parent)=path.parent(){std::fs::create_dir_all(parent)?;}
        let db=Connection::open_with_flags(path,if readonly{OpenFlags::SQLITE_OPEN_READ_ONLY}else{OpenFlags::SQLITE_OPEN_READ_WRITE|OpenFlags::SQLITE_OPEN_CREATE})?;
        db.busy_timeout(Duration::from_secs(10))?;
        if !readonly{db.execute_batch("PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS rust_events_v1(seq INTEGER PRIMARY KEY,event_id TEXT UNIQUE NOT NULL,kind TEXT NOT NULL,event_time TEXT NOT NULL,payload TEXT NOT NULL,previous_hash TEXT NOT NULL,digest TEXT NOT NULL);
            CREATE TRIGGER IF NOT EXISTS rust_events_v1_no_update BEFORE UPDATE ON rust_events_v1 BEGIN SELECT RAISE(ABORT,'events are immutable');END;
            CREATE TRIGGER IF NOT EXISTS rust_events_v1_no_delete BEFORE DELETE ON rust_events_v1 BEGIN SELECT RAISE(ABORT,'events are immutable');END;")?;}
        let store=Self{path:path.to_owned(),db,readonly};store.verify()?;Ok(store)
    }
    fn decode(row:&rusqlite::Row<'_>)->rusqlite::Result<StoredEvent>{
        let encoded:String=row.get(4)?;let payload=crate::indexed_json::from_str(&encoded).map_err(|e|rusqlite::Error::FromSqlConversionFailure(4,rusqlite::types::Type::Text,Box::new(e)))?;
        Ok(StoredEvent{seq:super::sqlite_u64(row,0)?,event_id:row.get(1)?,kind:row.get(2)?,event_time:row.get(3)?,payload,previous_hash:row.get(5)?,digest:row.get(6)?})
    }
    pub fn events(&self)->StoreResult<Vec<StoredEvent>>{let mut statement=self.db.prepare("SELECT * FROM rust_events_v1 ORDER BY seq")?;
        Ok(statement.query_map([],Self::decode)?.collect::<Result<Vec<_>,_>>()?)}
    pub fn get(&self,id:&str)->StoreResult<Option<StoredEvent>>{Ok(self.db.query_row("SELECT * FROM rust_events_v1 WHERE event_id=?1",[id],Self::decode).optional()?)}
    pub fn transaction<R>(&mut self,operation:impl FnOnce(&EventStore)->StoreResult<R>)->StoreResult<R>{
        require(!self.readonly,"read-only journal cannot write")?;self.db.execute_batch("BEGIN IMMEDIATE")?;
        let result=operation(self);match result{Ok(v)=>{self.db.execute_batch("COMMIT")?;Ok(v)},Err(e)=>{let _=self.db.execute_batch("ROLLBACK");Err(e)}}
    }
    pub fn snapshot<R>(&mut self,operation:impl FnOnce(&EventStore)->StoreResult<R>)->StoreResult<R>{
        self.db.execute_batch("BEGIN")?;let result=operation(self);match result{Ok(v)=>{self.db.execute_batch("COMMIT")?;Ok(v)},Err(e)=>{let _=self.db.execute_batch("ROLLBACK");Err(e)}}
    }
    pub fn append(&self,id:&str,kind:&str,when:DateTime<Utc>,payload:Value)->StoreResult<StoredEvent>{
        require(!self.readonly&&!self.db.is_autocommit(),"append requires writable transaction")?;
        require(!id.is_empty()&&!kind.is_empty(),"event identity and kind required")?;
        let time=when.to_rfc3339_opts(SecondsFormat::AutoSi,false);let encoded=canonical(&payload)?;
        if let Some(existing)=self.get(id)?{require(existing.kind==kind&&existing.event_time==time&&canonical(&existing.payload)?==encoded,"idempotency conflict")?;return Ok(existing);}
        let last:Option<(u64,String)>=self.db.query_row("SELECT seq,digest FROM rust_events_v1 ORDER BY seq DESC LIMIT 1",[],|r|Ok((super::sqlite_u64(r,0)?,r.get(1)?))).optional()?;
        let(seq,previous)=match last{Some((s,h))=>(s.checked_add(1).ok_or_else(||StoreError("event sequence overflow".into()))?,h),None=>(1,"0".repeat(64))};
        let sql_seq=i64::try_from(seq).map_err(|_|StoreError("event sequence exceeds SQLite INTEGER".into()))?;
        let digest=sha256(canonical(&json!([seq,id,kind,time,payload,previous]))?);
        self.db.execute("INSERT INTO rust_events_v1 VALUES(?1,?2,?3,?4,?5,?6,?7)",params![sql_seq,id,kind,time,encoded,previous,digest])?;
        self.get(id)?.ok_or_else(||StoreError("appended event unavailable".into()))
    }
    pub fn verify(&self)->StoreResult<String>{let mut previous="0".repeat(64);
        for(index,e)in self.events()?.into_iter().enumerate(){let seq=index as u64+1;let expected=sha256(canonical(&json!([seq,e.event_id,e.kind,e.event_time,e.payload,previous]))?);
            require(e.seq==seq&&e.previous_hash==previous&&e.digest==expected,"journal hash chain integrity failure")?;previous=expected;}Ok(previous)
    }
    pub fn backup(&self,destination:&Path)->StoreResult<()>{snapshot_sqlite(&self.path,destination)}
}
pub fn snapshot_sqlite(source:&Path,destination:&Path)->StoreResult<()>{
    require(source.is_file()&&!destination.exists(),"existing source and new backup destination required")?;
    if let Some(parent)=destination.parent(){std::fs::create_dir_all(parent)?;}
    let _reserved=std::fs::OpenOptions::new().create_new(true).write(true).open(destination)?;drop(_reserved);
    let input=Connection::open_with_flags(source,OpenFlags::SQLITE_OPEN_READ_ONLY)?;let mut output=Connection::open(destination)?;
    {let backup=rusqlite::backup::Backup::new(&input,&mut output)?;backup.run_to_completion(128,Duration::from_millis(1),None)?;}
    let integrity:String=output.query_row("PRAGMA integrity_check",[],|r|r.get(0))?;require(integrity=="ok","SQLite backup integrity failure")?;Ok(())
}
