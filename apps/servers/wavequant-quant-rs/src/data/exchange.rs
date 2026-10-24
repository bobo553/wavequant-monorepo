//! CSV/JSON 行情交换边界，保留显式权限、未知状态及原始份额精度。
use std::{collections::BTreeMap,fs::File,io::{Read,Write},path::Path};
use chrono::{DateTime,NaiveDate,NaiveDateTime};
use serde::{Serialize,Deserialize};
use crate::indexed_json::Value;
use sha2::{Digest,Sha256};
use super::tdx::DataError;
const MAX_BYTES:u64=256*1024*1024;
const MAX_ROWS:usize=2_000_000;
fn invalid(s:&str)->DataError {DataError::Invalid(s.into())}
fn yes()->bool {true}
fn one()->f64 {1.0}
#[derive(Clone,Debug,Serialize,Deserialize)]
pub struct ImportedBar {
    pub timestamp:String,pub symbol:String,pub open:f64,pub high:f64,pub low:f64,pub close:f64,pub volume:f64,
    #[serde(default="yes")] pub buyable:bool,
    #[serde(default="yes")] pub sellable:bool,
    #[serde(default="one")] pub adjustment_factor:f64,
    #[serde(default)] pub close_buyable:Option<bool>,
    #[serde(default)] pub nonflat_close_buyable:Option<bool>,
    #[serde(default)] pub close_sellable:Option<bool>,
    #[serde(default)] pub nonflat_close_sellable:Option<bool>,
    #[serde(default)] pub raw_is_st:Option<bool>,
    #[serde(default)] pub raw_trading_active:Option<bool>,
}
#[derive(Clone,Debug,Serialize,Deserialize)]
pub struct ImportedData {pub source_version:String,pub bars:BTreeMap<String,Vec<ImportedBar>>}
#[derive(Clone,Debug)]
pub struct ParsedTimestamp {pub canonical:String,pub micros:i64,pub aware:bool}
pub fn parse_timestamp(value:&str)->Result<ParsedTimestamp,DataError> {
    let value=value.trim();
    if let Ok(stamp)=DateTime::parse_from_rfc3339(&value.replace(' ',"T")) {return Ok(ParsedTimestamp{canonical:stamp.to_rfc3339(),micros:stamp.timestamp_micros(),aware:true});}
    let stamp=NaiveDateTime::parse_from_str(value,"%Y-%m-%dT%H:%M:%S%.f")
        .or_else(|_|NaiveDateTime::parse_from_str(value,"%Y-%m-%d %H:%M:%S%.f"))
        .or_else(|_|NaiveDateTime::parse_from_str(value,"%Y-%m-%dT%H:%M"))
        .or_else(|_|NaiveDateTime::parse_from_str(value,"%Y-%m-%d %H:%M"))
        .ok().or_else(||NaiveDate::parse_from_str(value,"%Y-%m-%d").ok().and_then(|date|date.and_hms_opt(0,0,0)))
        .ok_or_else(||invalid("invalid bar timestamp"))?;
    Ok(ParsedTimestamp{canonical:stamp.format("%Y-%m-%dT%H:%M:%S%.f").to_string(),micros:stamp.and_utc().timestamp_micros(),aware:false})
}
pub fn parse_bool(value:Option<&str>,default:bool)->Result<bool,DataError> {
    match value.map(str::trim).filter(|v|!v.is_empty()).map(str::to_lowercase).as_deref() {
        None=>Ok(default),Some("1" | "true" | "yes" | "y")=>Ok(true),Some("0" | "false" | "no" | "n")=>Ok(false),_=>Err(invalid("invalid boolean value")),
    }
}
fn optional_bool(value:Option<&str>)->Result<Option<bool>,DataError> {value.map(str::trim).filter(|v|!v.is_empty()).map(|v|parse_bool(Some(v),false)).transpose()}
impl ImportedBar {
    pub fn validate(&self)->Result<(),DataError> {
        parse_timestamp(&self.timestamp)?;
        if self.symbol.is_empty() || self.symbol.len()>128 || [self.open,self.high,self.low,self.close,self.volume,self.adjustment_factor].iter().any(|v|!v.is_finite()) {return Err(invalid("bar symbol empty or numeric field non-finite"));}
        if self.low<=0.0 || self.low>self.high || self.low>self.open.min(self.close) || self.high<self.open.max(self.close) || self.volume<0.0 || self.adjustment_factor<=0.0 {return Err(invalid("invalid bar OHLC/volume/factor"));}
        Ok(())
    }
}
fn bytes(path:&Path)->Result<Vec<u8>,DataError> {
    let file=File::open(path)?;
    if file.metadata()?.len()>MAX_BYTES {return Err(invalid("bar file exceeds size limit"));}
    let mut value=Vec::new();file.take(MAX_BYTES+1).read_to_end(&mut value)?;
    if value.len() as u64>MAX_BYTES {return Err(invalid("bar file exceeds size limit"));}
    Ok(value)
}
fn normalize(rows:Vec<ImportedBar>,source:&[u8])->Result<ImportedData,DataError> {
    if rows.is_empty() || rows.len()>MAX_ROWS {return Err(invalid("bar input empty or exceeds row limit"));}
    let mut awareness=None;
    let mut grouped=BTreeMap::<String,Vec<(i64,ImportedBar)>>::new();
    for mut row in rows {
        row.symbol=row.symbol.trim().into();row.validate()?;
        let stamp=parse_timestamp(&row.timestamp)?;
        if awareness.is_some_and(|old|old!=stamp.aware) {return Err(invalid("do not mix timezone-aware and naive timestamps"));}
        awareness=Some(stamp.aware);row.timestamp=stamp.canonical;
        grouped.entry(row.symbol.clone()).or_default().push((stamp.micros,row));
    }
    let mut bars=BTreeMap::new();
    for (symbol,mut values) in grouped {
        values.sort_by_key(|(stamp,_)|*stamp);
        if values.windows(2).any(|pair|pair[0].0>=pair[1].0) {return Err(invalid("duplicate/non-increasing timestamp for symbol"));}
        bars.insert(symbol,values.into_iter().map(|(_,bar)|bar).collect());
    }
    Ok(ImportedData{source_version:format!("{:x}",Sha256::digest(source)),bars})
}
pub fn load_csv(path:impl AsRef<Path>)->Result<ImportedData,DataError> {
    let data=bytes(path.as_ref())?;
    let content=data.strip_prefix(&[0xef,0xbb,0xbf]).unwrap_or(&data);
    let mut reader=csv::ReaderBuilder::new().from_reader(content);
    let headers=reader.headers().map_err(|_|invalid("CSV header invalid"))?.clone();
    let positions:BTreeMap<_,_>=headers.iter().enumerate().map(|(index,key)|(key,index)).collect();
    for required in ["timestamp","symbol","open","high","low","close","volume"] {if !positions.contains_key(required) {return Err(invalid("CSV required columns missing"));}}
    let mut rows=Vec::new();
    for record in reader.records() {
        let record=record.map_err(|_|invalid("CSV row invalid"))?;
        let get=|key:&str|positions.get(key).and_then(|index|record.get(*index));
        let number=|key:&str|->Result<f64,DataError>{get(key).ok_or_else(||invalid("CSV numeric field missing"))?.parse().map_err(|_|invalid("CSV numeric field invalid"))};
        rows.push(ImportedBar{timestamp:get("timestamp").ok_or_else(||invalid("CSV timestamp missing"))?.into(),symbol:get("symbol").ok_or_else(||invalid("CSV symbol missing"))?.into(),open:number("open")?,high:number("high")?,low:number("low")?,close:number("close")?,volume:number("volume")?,buyable:parse_bool(get("buyable"),true)?,sellable:parse_bool(get("sellable"),true)?,adjustment_factor:match get("adjustment_factor").filter(|v|!v.trim().is_empty()){Some(v)=>v.parse().map_err(|_|invalid("invalid adjustment_factor"))?,None=>1.0},close_buyable:optional_bool(get("close_buyable"))?,nonflat_close_buyable:optional_bool(get("nonflat_close_buyable"))?,close_sellable:optional_bool(get("close_sellable"))?,nonflat_close_sellable:optional_bool(get("nonflat_close_sellable"))?,raw_is_st:optional_bool(get("raw_is_st"))?,raw_trading_active:optional_bool(get("raw_trading_active"))?});
        if rows.len()>MAX_ROWS {return Err(invalid("CSV rows exceed limit"));}
    }
    normalize(rows,&data)
}
pub fn load_json(path:impl AsRef<Path>)->Result<ImportedData,DataError> {
    let data=bytes(path.as_ref())?;
    let content=data.strip_prefix(&[0xef,0xbb,0xbf]).unwrap_or(&data);
    let value:Value=crate::indexed_json::from_slice(content).map_err(|_|invalid("bar JSON invalid"))?;
    let rows=if value.is_array() {value} else {value.get("bars").cloned().ok_or_else(||invalid("JSON requires bar array"))?};
    let rows=crate::indexed_json::from_value::<Vec<ImportedBar>>(rows).map_err(|_|invalid("JSON bar fields invalid"))?;
    normalize(rows,&data)
}
pub fn load_bars(path:impl AsRef<Path>)->Result<ImportedData,DataError> {
    if path.as_ref().extension().is_some_and(|e|e.eq_ignore_ascii_case("json")) {load_json(path)} else {load_csv(path)}
}
pub fn write_csv(path:impl AsRef<Path>,bars:&[ImportedBar])->Result<(),DataError> {
    for bar in bars {bar.validate()?;}
    if let Some(parent)=path.as_ref().parent().filter(|p|!p.as_os_str().is_empty()) {std::fs::create_dir_all(parent)?;}
    let mut writer=csv::Writer::from_path(path).map_err(|_|invalid("CSV output unavailable"))?;
    for bar in bars {writer.serialize(bar).map_err(|_|invalid("CSV serialization failed"))?;}
    writer.flush()?;Ok(())
}
pub fn write_json(path:impl AsRef<Path>,value:&Value)->Result<(),DataError> {
    if let Some(parent)=path.as_ref().parent().filter(|p|!p.as_os_str().is_empty()) {std::fs::create_dir_all(parent)?;}
    let bytes=crate::indexed_json::to_vec_pretty(value).map_err(|_|invalid("JSON serialization failed"))?;
    if bytes.len() as u64>MAX_BYTES {return Err(invalid("JSON output exceeds size limit"));}
    let mut file=File::create(path)?;file.write_all(&bytes)?;file.sync_all()?;Ok(())
}

impl From<ImportedBar> for super::adjustments::ExecutionBar {
    fn from(bar:ImportedBar)->Self {
        Self{timestamp:bar.timestamp,symbol:bar.symbol,open:bar.open,high:bar.high,low:bar.low,close:bar.close,volume:bar.volume,adjustment_factor:bar.adjustment_factor,buyable:bar.buyable,sellable:bar.sellable,close_buyable:bar.close_buyable,nonflat_close_buyable:bar.nonflat_close_buyable,close_sellable:bar.close_sellable,nonflat_close_sellable:bar.nonflat_close_sellable,raw_is_st:bar.raw_is_st,raw_trading_active:bar.raw_trading_active}
    }
}
impl From<super::adjustments::ExecutionBar> for ImportedBar {
    fn from(bar:super::adjustments::ExecutionBar)->Self {
        Self{timestamp:bar.timestamp,symbol:bar.symbol,open:bar.open,high:bar.high,low:bar.low,close:bar.close,volume:bar.volume,adjustment_factor:bar.adjustment_factor,buyable:bar.buyable,sellable:bar.sellable,close_buyable:bar.close_buyable,nonflat_close_buyable:bar.nonflat_close_buyable,close_sellable:bar.close_sellable,nonflat_close_sellable:bar.nonflat_close_sellable,raw_is_st:bar.raw_is_st,raw_trading_active:bar.raw_trading_active}
    }
}