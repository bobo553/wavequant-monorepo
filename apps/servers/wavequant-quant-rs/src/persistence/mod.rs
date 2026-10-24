//! 本地不可变运行账本、可丢弃产物缓存和目录快照。
mod event_store;
mod artifact_cache;
mod experiment;
mod operational;
mod backup;
pub mod calculation_chunk;
pub mod calculation_step_cache;
mod structure_prefix_cache;
pub use structure_prefix_cache::StructurePrefixCache;
pub use calculation_step_cache::{CalculationStepCache,CalculationPlan,CalculationReuseCertificate};
pub use event_store::*;
pub use artifact_cache::*;
pub use experiment::*;
pub use operational::*;
pub use backup::*;

use crate::indexed_json::Value;
use sha2::{Digest,Sha256};
use std::{fmt,io::Read,path::{Path,PathBuf}};
#[derive(Debug)]
pub struct StoreError(pub String);
impl fmt::Display for StoreError{fn fmt(&self,f:&mut fmt::Formatter<'_>)->fmt::Result{f.write_str(&self.0)}}
impl std::error::Error for StoreError{}
impl From<std::io::Error> for StoreError{fn from(e:std::io::Error)->Self{Self(e.to_string())}}
impl From<rusqlite::Error> for StoreError{fn from(e:rusqlite::Error)->Self{Self(e.to_string())}}
impl From<crate::indexed_json::Error> for StoreError{fn from(e:crate::indexed_json::Error)->Self{Self(e.to_string())}}
pub type StoreResult<T>=Result<T,StoreError>;
// SQLite INTEGER 是有符号 64 位，领域计数仍保留无符号语义并拒绝损坏的负值。
pub(crate) fn sqlite_u64(row:&rusqlite::Row<'_>,index:usize)->rusqlite::Result<u64>{
    u64::try_from(row.get::<_,i64>(index)?).map_err(|e|rusqlite::Error::FromSqlConversionFailure(index,rusqlite::types::Type::Integer,Box::new(e)))
}
pub(crate) fn require(ok:bool,reason:&str)->StoreResult<()>{if ok{Ok(())}else{Err(StoreError(reason.into()))}}
pub fn canonical(value:&Value)->StoreResult<String>{
    fn ordered(v:&Value)->Value{match (v).kind() {Some(crate::indexed_json::Kind::Object(m))=>Value::Object(m.iter().map(|(k,v)|(k.clone(),ordered(v))).collect::<std::collections::BTreeMap<_,_>>().into_iter().collect()),
        Some(crate::indexed_json::Kind::Array(a))=>Value::Array(a.iter().map(ordered).collect()),_=>v.clone()}}
    Ok(crate::indexed_json::to_string(&ordered(value))?)
}
pub fn sha256(bytes:impl AsRef<[u8]>)->String{format!("{:x}",Sha256::digest(bytes.as_ref()))}
pub fn fingerprint(path:&Path)->StoreResult<String>{
    let mut file=std::fs::File::open(path)?;let mut hash=Sha256::new();let mut buffer=[0u8;65536];
    loop{let n=file.read(&mut buffer)?;if n==0{break;}hash.update(&buffer[..n]);}Ok(format!("{:x}",hash.finalize()))
}
pub fn write_json(path:&Path,value:&impl serde::Serialize)->StoreResult<()>{
    if let Some(parent)=path.parent(){std::fs::create_dir_all(parent)?;}let mut bytes=crate::indexed_json::to_vec_pretty(value)?;bytes.push(b'\n');std::fs::write(path,bytes)?;Ok(())
}
/// 仅规范化已存在祖先；新输出目录也具有可检查的绝对边界。
pub fn absolute_path(path:&Path)->StoreResult<PathBuf>{
    let absolute=if path.is_absolute(){path.to_owned()}else{std::env::current_dir()?.join(path)};
    let mut existing=absolute.as_path();let mut missing=Vec::new();
    while !existing.exists(){let name=existing.file_name().ok_or_else(||StoreError("invalid output path".into()))?;
        require(name!=".."&&name!=".","relative traversal in output path")?;missing.push(name.to_os_string());existing=existing.parent().ok_or_else(||StoreError("output ancestor unavailable".into()))?;}
    let mut result=existing.canonicalize()?;for part in missing.into_iter().rev(){result.push(part);}Ok(result)
}
