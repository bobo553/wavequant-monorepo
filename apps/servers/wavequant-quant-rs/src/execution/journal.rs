use chrono::{DateTime, Utc};
use rusqlite::{Connection, params};
use serde::{Deserialize, Serialize, de::DeserializeOwned};
use sha2::{Digest, Sha256};
use std::{error::Error, fmt, path::Path};

#[derive(Debug)]
pub struct ExecutionError(pub String);
impl fmt::Display for ExecutionError { fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result { f.write_str(&self.0) } }
impl Error for ExecutionError {}
impl From<rusqlite::Error> for ExecutionError { fn from(e: rusqlite::Error) -> Self { Self(e.to_string()) } }
impl From<crate::indexed_json::Error> for ExecutionError { fn from(e: crate::indexed_json::Error) -> Self { Self(e.to_string()) } }
pub type ExecutionResult<T> = Result<T, ExecutionError>;
pub(super) fn require(ok: bool, reason: &str) -> ExecutionResult<()> { if ok { Ok(()) } else { Err(ExecutionError(reason.into())) } }

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub(super) struct Record<T> { pub id: String, pub at: DateTime<Utc>, pub payload: T }

/// SQLite 串行化读取、判定和追加；独立 Rust v1 格式不冒充 Python 账本格式。
pub(super) struct Journal<T> { connection: Connection, marker: std::marker::PhantomData<T> }
impl<T: Serialize + DeserializeOwned + PartialEq> Journal<T> {
    pub fn open_readonly(path:&Path)->ExecutionResult<Self>{
        require(path.is_file(),"existing_execution_journal_required")?;
        let connection=Connection::open_with_flags(path,rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY)?;
        connection.busy_timeout(std::time::Duration::from_secs(10))?;
        let journal=Self{connection,marker:std::marker::PhantomData};journal.records()?;Ok(journal)
    }
    pub fn open(path: &Path) -> ExecutionResult<Self> {
        let connection = Connection::open(path)?;
        connection.busy_timeout(std::time::Duration::from_secs(10))?;
        connection.execute_batch("PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS rust_execution_events_v1 (seq INTEGER PRIMARY KEY, event_id TEXT UNIQUE NOT NULL, record TEXT NOT NULL, previous_hash TEXT NOT NULL, digest TEXT NOT NULL);
            CREATE TRIGGER IF NOT EXISTS rust_execution_no_update BEFORE UPDATE ON rust_execution_events_v1 BEGIN SELECT RAISE(ABORT, 'events are immutable'); END;
            CREATE TRIGGER IF NOT EXISTS rust_execution_no_delete BEFORE DELETE ON rust_execution_events_v1 BEGIN SELECT RAISE(ABORT, 'events are immutable'); END;")?;
        let journal = Self { connection, marker: std::marker::PhantomData };
        journal.records()?;
        Ok(journal)
    }
    pub fn records(&self) -> ExecutionResult<Vec<Record<T>>> {
        let mut statement = self.connection.prepare("SELECT seq,record,previous_hash,digest,event_id FROM rust_execution_events_v1 ORDER BY seq")?;
        let rows = statement.query_map([], |r| Ok((r.get::<_, i64>(0)?, r.get::<_, String>(1)?, r.get::<_, String>(2)?, r.get::<_, String>(3)?, r.get::<_, String>(4)?)))?;
        let mut result = Vec::new();
        let mut previous = "0".repeat(64);
        for row in rows {
            let (seq, encoded, parent, digest, identity) = row?;
            let seq = u64::try_from(seq).map_err(|_| ExecutionError("journal_sequence_negative".into()))?;
            let expected = format!("{:x}", Sha256::digest(crate::indexed_json::to_vec(&(seq, &encoded, &previous))?));
            require(seq == result.len() as u64 + 1 && parent == previous && digest == expected, "journal_hash_chain_integrity_failure")?;
            let record:Record<T>=crate::indexed_json::from_str(&encoded)?;
            require(record.id == identity,"journal_event_identity_mismatch")?;
            result.push(record);
            previous = digest;
        }
        Ok(result)
    }
    pub fn transact<R>(&mut self, operation: impl FnOnce(&[Record<T>]) -> ExecutionResult<(Vec<Record<T>>, R)>) -> ExecutionResult<R> {
        self.connection.execute_batch("BEGIN IMMEDIATE")?;
        let result = (|| {
            let mut records = self.records()?;
            let (new_records, result) = operation(&records)?;
            for record in new_records {
                require(!record.id.is_empty(), "event_identity_required")?;
                if let Some(old) = records.iter().find(|old| old.id == record.id) { require(old == &record, "event_identity_conflict")?; continue; }
                let previous: String = self.connection.query_row("SELECT digest FROM rust_execution_events_v1 ORDER BY seq DESC LIMIT 1", [], |r| r.get(0)).or_else(|e| match e { rusqlite::Error::QueryReturnedNoRows => Ok("0".repeat(64)), other => Err(other) })?;
                let seq = records.len() as u64 + 1;
                let sql_seq = i64::try_from(seq).map_err(|_| ExecutionError("journal_sequence_overflow".into()))?;
                let encoded = crate::indexed_json::to_string(&record)?;
                let digest = format!("{:x}", Sha256::digest(crate::indexed_json::to_vec(&(seq, &encoded, &previous))?));
                self.connection.execute("INSERT INTO rust_execution_events_v1 VALUES (?1,?2,?3,?4,?5)", params![sql_seq, record.id, encoded, previous, digest])?;
                records.push(record);
            }
            Ok(result)
        })();
        match result { Ok(value) => { self.connection.execute_batch("COMMIT")?; Ok(value) }, Err(error) => { self.connection.execute_batch("ROLLBACK")?; Err(error) } }
    }
}

#[derive(Debug,Clone,Serialize)]
pub struct JournalInspection{pub events:usize,pub head:String,pub last_event_at:Option<DateTime<Utc>>,pub schema:String}
/// 读取固定快照，账户和撮合器使用同一链编码；不会创建空库或追加事件。
pub fn inspect_execution_journal(path:&Path)->ExecutionResult<JournalInspection>{
    let journal=Journal::<crate::indexed_json::Value>::open_readonly(path)?;
    journal.connection.execute_batch("BEGIN")?;
    let result=(||{let records=journal.records()?;
        let head=if records.is_empty(){"0".repeat(64)}else{journal.connection.query_row("SELECT digest FROM rust_execution_events_v1 ORDER BY seq DESC LIMIT 1",[],|row|row.get(0))?};
        Ok(JournalInspection{events:records.len(),head,last_event_at:records.iter().map(|r|r.at).max(),schema:"rust_execution_events_v1".into()})})();
    journal.connection.execute_batch("ROLLBACK")?;result
}
