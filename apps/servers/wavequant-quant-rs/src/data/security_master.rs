//! 双时态证券主档：事实按生效时间和实际已知时间选择，未知状态不推断。
use std::{collections::BTreeSet,fs::File,io::Read,path::Path,str::FromStr};
use chrono::{DateTime,Utc};
use rust_decimal::Decimal;
use serde::{Deserialize,Serialize};
use super::tdx::{DataError,parse_date};
fn invalid(s:&str)->DataError {DataError::Invalid(s.into())}
pub fn utc(value:&str)->Result<DateTime<Utc>,DataError> {
    DateTime::parse_from_rfc3339(value).map(|d|d.with_timezone(&Utc)).map_err(|_|invalid("timezone-aware event time required"))
}
#[derive(Clone,Debug,Serialize,Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SecurityFact {
    pub symbol:String,pub effective_from:String,pub effective_to:Option<String>,pub known_at:String,
    pub sector:String,pub active:bool,pub suspended:bool,pub st:bool,pub buy_lot:u32,
    pub tick_size:String,pub settlement_days:u8,pub source:String,
}
impl SecurityFact {
    pub fn validate(&self)->Result<(),DataError> {
        let first=parse_date(&self.effective_from)?;
        if let Some(last)=&self.effective_to {if parse_date(last)?<=first {return Err(invalid("effective interval must be half-open and ordered"));}}
        utc(&self.known_at)?;
        if self.symbol.is_empty() || self.sector.is_empty() || self.source.is_empty() {return Err(invalid("symbol, sector and evidence source required"));}
        if self.buy_lot==0 || self.settlement_days>1 {return Err(invalid("positive raw-share lot and explicit T+0/T+1 required"));}
        let tick=Decimal::from_str(&self.tick_size).map_err(|_|invalid("finite decimal tick required"))?;
        if tick<=Decimal::ZERO {return Err(invalid("positive price tick required"));}
        Ok(())
    }
}
#[derive(Clone,Debug,Default)]
pub struct SecurityMaster {facts:Vec<SecurityFact>}
impl SecurityMaster {
    pub fn new(facts:Vec<SecurityFact>)->Result<Self,DataError> {
        let mut keys=BTreeSet::new();
        for fact in &facts {
            fact.validate()?;
            if !keys.insert((fact.symbol.clone(),fact.effective_from.clone(),utc(&fact.known_at)?)) {return Err(invalid("duplicate/conflicting security fact identity"));}
        }
        Ok(Self{facts})
    }
    pub fn load(path:impl AsRef<Path>)->Result<Self,DataError> {
        let file=File::open(path)?;
        if file.metadata()?.len()>64*1024*1024 {return Err(invalid("security master exceeds size limit"));}
        let mut bytes=Vec::new();file.take(64*1024*1024+1).read_to_end(&mut bytes)?;
        if bytes.len()>64*1024*1024 {return Err(invalid("security master exceeds size limit"));}
        let facts=crate::indexed_json::from_slice(&bytes).map_err(|_|invalid("security master JSON invalid"))?;
        Self::new(facts)
    }
    pub fn at(&self,symbol:&str,session:&str,asof:&str)->Result<Option<&SecurityFact>,DataError> {
        parse_date(session)?;
        let cutoff=utc(asof)?;
        let mut selected:Option<(&SecurityFact,DateTime<Utc>)>=None;
        for fact in &self.facts {
            let known=utc(&fact.known_at)?;
            if fact.symbol!=symbol || fact.effective_from.as_str()>session || known>cutoff {continue;}
            if selected.as_ref().is_none_or(|(old,time)|(&fact.effective_from,known)>(&old.effective_from,*time)) {selected=Some((fact,known));}
        }
        // 新事实过期时不能复活较旧的无截止日期事实。
        Ok(selected.map(|(fact,_)|fact).filter(|fact|fact.effective_to.as_deref().is_none_or(|end|session<end)))
    }
    pub fn universe(&self,session:&str,asof:&str,exclude_st:bool)->Result<Vec<String>,DataError> {
        let symbols:BTreeSet<_>=self.facts.iter().map(|fact|fact.symbol.as_str()).collect();
        let mut selected=Vec::new();
        for symbol in symbols {if self.at(symbol,session,asof)?.is_some_and(|fact|fact.active && !fact.suspended && (!exclude_st || !fact.st)) {selected.push(symbol.into());}}
        Ok(selected)
    }
    pub fn snapshot(&self)->&[SecurityFact] {&self.facts}
}
