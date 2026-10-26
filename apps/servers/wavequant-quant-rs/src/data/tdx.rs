//! 通达信本地行情只读适配器，版本来自实际读取的文件字节。
use std::{collections::{BTreeMap, BTreeSet}, fmt, fs::{self,File}, io::{Read,Seek,SeekFrom}, path::{Path,PathBuf}};
use chrono::NaiveDate;
use encoding_rs::GBK;
use serde::{Deserialize,Serialize};
use crate::indexed_json::{Value,json};
use sha2::{Digest,Sha256};

const RECORD_BYTES: usize = 32;
const MAX_DATA_BYTES: u64 = 128 * 1024 * 1024;
#[derive(Debug)]
pub enum DataError { Invalid(String), Unavailable(String), Changed, Io(std::io::Error) }
impl fmt::Display for DataError {
    fn fmt(&self,f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self { Self::Invalid(s) | Self::Unavailable(s) => f.write_str(s), Self::Changed => f.write_str("通达信日线正在更新，请稍后重试"), Self::Io(_) => f.write_str("本地行情文件不可读") }
    }
}
impl std::error::Error for DataError {}
impl From<std::io::Error> for DataError { fn from(e: std::io::Error) -> Self { Self::Io(e) } }
fn invalid(s: &str) -> DataError { DataError::Invalid(s.into()) }
pub fn parse_date(s: &str) -> Result<NaiveDate,DataError> {
    let d = NaiveDate::parse_from_str(s,"%Y-%m-%d").map_err(|_|invalid("use YYYY-MM-DD date"))?;
    if d.format("%Y-%m-%d").to_string() != s { return Err(invalid("use YYYY-MM-DD date")); }
    Ok(d)
}
pub fn is_stock(symbol: &str) -> bool {
    if symbol.len() != 9 || !symbol.as_bytes()[3..].iter().all(u8::is_ascii_digit) { return false; }
    match symbol.get(..3) { Some("sh.") => matches!(symbol.get(3..5),Some("60" | "68")), Some("sz.") => matches!(symbol.get(3..5),Some("00" | "30")), Some("bj.") => matches!(symbol.get(3..5),Some("43" | "82" | "83" | "87" | "88" | "92")), _ => false }
}
#[derive(Clone,Debug,Serialize,Deserialize)]
pub struct DailyBar {
    pub date: String,
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub volume: u64,
    /// 未提供成交额的指数接口保留None；不能用零或价格乘成交量冒充真实成交额。
    pub amount: Option<f64>,
}
#[derive(Clone,Debug,Serialize,Deserialize)]
pub struct MarketData { pub symbol: String, pub asof: String, pub version: String, pub bars: Vec<DailyBar> }
fn integer(bytes: &[u8],offset: usize) -> u32 { u32::from_le_bytes([bytes[offset],bytes[offset+1],bytes[offset+2],bytes[offset+3]]) }
fn trading_date(value: u32) -> Result<String,DataError> {
    let year = i32::try_from(value / 10000).map_err(|_|invalid("invalid TDX date"))?;
    NaiveDate::from_ymd_opt(year,(value / 100) % 100,value % 100).map(|d|d.format("%Y-%m-%d").to_string()).ok_or_else(||invalid("invalid TDX date"))
}
fn bounded_bytes(path: &Path) -> Result<Vec<u8>,DataError> {
    let file = File::open(path)?;
    if file.metadata()?.len() > MAX_DATA_BYTES { return Err(invalid("行情文件超出读取上限")); }
    let mut bytes = Vec::new();
    file.take(MAX_DATA_BYTES + 1).read_to_end(&mut bytes)?;
    if bytes.len() as u64 > MAX_DATA_BYTES { return Err(invalid("行情文件超出读取上限")); }
    Ok(bytes)
}
pub fn fingerprint(path: &Path) -> Result<String,DataError> {
    Ok(format!("{:x}",Sha256::digest(bounded_bytes(path)?)))
}
pub fn decode_day(bytes: &[u8]) -> Result<Vec<DailyBar>,DataError> {
    if bytes.is_empty() || bytes.len() % RECORD_BYTES != 0 { return Err(invalid("empty or truncated 32-byte day records")); }
    let mut result = Vec::<DailyBar>::with_capacity(bytes.len()/RECORD_BYTES);
    for record in bytes.chunks_exact(RECORD_BYTES) {
        let date = trading_date(integer(record,0))?;
        if result.last().is_some_and(|b|b.date >= date) { return Err(invalid("duplicate/non-increasing TDX date")); }
        let (open,high,low,close) = (integer(record,4),integer(record,8),integer(record,12),integer(record,16));
        let amount = f64::from(f32::from_le_bytes([record[20],record[21],record[22],record[23]]));
        if open == 0 || close == 0 || low == 0 || low > open.min(close) || high < open.max(close) || !amount.is_finite() { return Err(invalid("invalid TDX OHLC or amount")); }
        result.push(DailyBar{date,open:f64::from(open)/100.0,high:f64::from(high)/100.0,low:f64::from(low)/100.0,close:f64::from(close)/100.0,volume:u64::from(integer(record,24)),amount:Some(amount)});
    }
    Ok(result)
}
pub fn read_day(path: &Path) -> Result<Vec<DailyBar>,DataError> { decode_day(&bounded_bytes(path)?) }
pub fn read_names(path: &Path) -> Result<BTreeMap<String,String>,DataError> {
    let bytes = bounded_bytes(path)?;
    let records = bytes.get(50..).unwrap_or_default();
    if records.is_empty() { return Ok(BTreeMap::new()); }
    for (size,offset,width) in [(360,31,40),(314,23,8)] {
        if records.len() % size != 0 { continue; }
        let mut names = BTreeMap::new();
        for record in records.chunks_exact(size) {
            let code = &record[..6];
            if !code.iter().all(u8::is_ascii_digit) { continue; }
            let text = &record[offset..offset+width];
            let end = text.iter().position(|b|*b == 0).unwrap_or(text.len());
            let (decoded,_,bad) = GBK.decode(&text[..end]);
            if bad { continue; }
            let name: String = decoded.chars().filter(|c|!c.is_whitespace()).collect();
            if !name.is_empty() { names.insert(String::from_utf8_lossy(code).into_owned(),name); }
        }
        if !names.is_empty() { return Ok(names); }
    }
    Err(invalid("unsupported TNF layout"))
}
#[derive(Clone,Debug)]
pub struct TdxReader { root: PathBuf }
impl TdxReader {
    pub fn new(root: impl AsRef<Path>) -> Result<Self,DataError> {
        let root = root.as_ref().canonicalize()?;
        if !root.is_dir() { return Err(invalid("TDX root must be a directory")); }
        Ok(Self{root})
    }
    pub fn root(&self) -> &Path { &self.root }
    fn confined(&self,path: &Path) -> Result<PathBuf,DataError> {
        let path = path.canonicalize()?;
        if !path.starts_with(&self.root) { return Err(invalid("TDX path escaped root")); }
        Ok(path)
    }
    pub fn day_path(&self,symbol: &str) -> Result<PathBuf,DataError> {
        if !is_stock(symbol) { return Err(invalid("not an A-share symbol")); }
        let (market,code) = symbol.split_once('.').ok_or_else(||invalid("invalid TDX symbol"))?;
        let path = self.root.join("vipdoc").join(market).join("lday").join(format!("{market}{code}.day"));
        if !path.is_file() { return Err(DataError::Unavailable("通达信尚未下载该股票日线".into())); }
        self.confined(&path)
    }
    pub fn bars(&self,symbol: &str,asof: &str) -> Result<MarketData,DataError> {
        parse_date(asof)?;
        let path = self.day_path(symbol)?;
        let bytes = bounded_bytes(&path)?;
        let version = format!("{:x}",Sha256::digest(&bytes));
        let bars: Vec<_> = decode_day(&bytes)?.into_iter().filter(|b|b.date.as_str() <= asof).collect();
        if bars.is_empty() { return Err(DataError::Unavailable("该回放日期之前没有日线数据".into())); }
        if fingerprint(&path)? != version { return Err(DataError::Changed); }
        Ok(MarketData{symbol:symbol.into(),asof:asof.into(),version,bars})
    }
    pub fn stock_metadata(&self,symbol: &str) -> Result<Value,DataError> {
        if !is_stock(symbol) { return Err(invalid("not an A-share symbol")); }
        let (market,code) = symbol.split_once('.').ok_or_else(||invalid("invalid TDX symbol"))?;
        let path = self.root.join("T0002/hq_cache").join(format!("{market}s.tnf"));
        let names = if path.is_file() { read_names(&self.confined(&path)?)? } else { BTreeMap::new() };
        Ok(json!({"symbol":symbol,"name":names.get(code)}))
    }
    pub fn catalog(&self) -> Result<Value,DataError> {
        let mut stocks = Vec::new();
        let mut warnings = Vec::new();
        let mut latest: Option<String> = None;
        let mut with_daily = 0;
        for market in ["sh","sz","bj"] {
            let path = self.root.join("T0002/hq_cache").join(format!("{market}s.tnf"));
            let names = if path.is_file() { match self.confined(&path).and_then(|p|read_names(&p)) {
                Ok(names) => names, Err(e) => { warnings.push(format!("{market} 名称缓存不可读：{e}")); BTreeMap::new() }
            } } else { BTreeMap::new() };
            let mut paths = BTreeMap::new();
            let directory = self.root.join("vipdoc").join(market).join("lday");
            if directory.is_dir() {
                let directory = self.confined(&directory)?;
                for entry in fs::read_dir(directory)? {
                    let entry = entry?;
                    let file = entry.file_name().to_string_lossy().into_owned();
                    if !file.starts_with(market) || !file.ends_with(".day") { continue; }
                    if let Some(code) = file.get(2..file.len()-4) { if is_stock(&format!("{market}.{code}")) { paths.insert(code.to_owned(),entry.path()); } }
                }
            }
            let codes: BTreeSet<_> = paths.keys().chain(names.keys()).filter(|code|is_stock(&format!("{market}.{code}"))).cloned().collect();
            for code in codes {
                let mut row = json!({"symbol":format!("{market}.{code}"),"name":names.get(&code),"source":"tdx","bar_count":0,"first":null,"last":null,"has_data":false,"status":"missing_daily"});
                if let Some(path) = paths.get(&code) {
                    match self.confined(path).and_then(|p|daily_header(&p)) {
                        Ok((first,last,count)) => {
                            row["first"] = json!(first); row["last"] = json!(last); row["bar_count"] = json!(count); row["has_data"] = json!(true); row["status"] = json!("available");
                            if latest.as_ref().is_none_or(|current|last > *current) { latest = Some(last); }
                            with_daily += 1;
                        },
                        Err(e) => { row["status"] = json!("invalid_daily"); row["error"] = json!(e.to_string()); }
                    }
                }
                stocks.push(row);
            }
        }
        Ok(json!({"available":!stocks.is_empty(),"stocks":stocks,"source":"Tongdaxin local .tnf + .day","latest":latest,"warnings":warnings,"with_daily":with_daily,"scope":"local_SH_SZ_BJ_A_shares","notice":"沪深北 A 股本机目录，非实时行情；未下载日线的股票不可打开。名称为当前通达信缓存，不代表历史名称。"}))
    }
    pub fn view(&self,symbol: &str,asof: &str) -> Result<Value,DataError> {
        let data = self.bars(symbol,asof)?;
        let all = self.bars(symbol,"9999-12-31")?;
        if data.version != all.version { return Err(DataError::Changed); }
        let bars: Vec<_> = data.bars.iter().map(|b|json!({"time":b.date,"open":b.open,"high":b.high,"low":b.low,"close":b.close,"raw_close":b.close,"volume":b.volume,"factor":1})).collect();
        Ok(json!({"symbol":symbol,"asof":asof,"result_scope":"tdx","price_basis":"raw_unadjusted","data_version":data.version,"sessions":all.bars.iter().map(|b|&b.date).collect::<Vec<_>>(),"bars":bars,"markers":[],"signals":[],"orders":[],"trades":[],"curve":[],"metrics":null,"evidence":"通达信原始不复权日线，仅行情与讲义绘图；未执行回测，除权缺口可能影响形态。"}))
    }
}
fn daily_header(path: &Path) -> Result<(String,String,u64),DataError> {
    let mut file = File::open(path)?;
    let length = file.metadata()?.len();
    if length == 0 || length % RECORD_BYTES as u64 != 0 { return Err(invalid("日线记录为空或不完整")); }
    let mut record = [0;RECORD_BYTES];
    file.read_exact(&mut record)?;
    let first = trading_date(integer(&record,0))?;
    file.seek(SeekFrom::End(-(RECORD_BYTES as i64)))?;
    file.read_exact(&mut record)?;
    let last = trading_date(integer(&record,0))?;
    if first > last { return Err(invalid("日期倒序")); }
    Ok((first,last,length / RECORD_BYTES as u64))
}
