//! 通达信 gbbq 二进制格式，参照项目已安装 pytdx 1.72 的文件协议。
//! 固定轮表是文件格式常量，不是用户凭据；运行时不依赖 Python。
use std::{fs::File,io::Read,path::Path};
use chrono::NaiveDate;
use sha2::{Digest,Sha256};
use serde::Serialize;
use super::{tdx::{DataError,TdxReader,fingerprint},adjustments::{CorporateAction,ExecutionBar,action_symbol,adjust_rows}};
const TABLE: &str = include_str!("gbbq_keys.hex");
const MAX_BYTES: u64 = 128*1024*1024;
fn invalid(message: &str) -> DataError { DataError::Invalid(message.into()) }
fn word(bytes: &[u8],offset: usize) -> u32 { u32::from_le_bytes([bytes[offset],bytes[offset+1],bytes[offset+2],bytes[offset+3]]) }
fn table() -> Result<Vec<u32>,DataError> {
    let bytes = TABLE.split_whitespace().map(|v|u8::from_str_radix(v,16).map_err(|_|invalid("invalid gbbq protocol table"))).collect::<Result<Vec<_>,_>>()?;
    if bytes.len() < 0x1048 || bytes.len()%4 != 0 { return Err(invalid("incomplete gbbq protocol table")); }
    Ok(bytes.chunks_exact(4).map(|b|word(b,0)).collect())
}
fn block(record: &[u8],keys: &[u32]) -> [u8;8] {
    let mut left = word(record,0) ^ keys[17];
    let mut right = word(record,4);
    for round in (1..=16).rev() {
        let a = (left >> 24) as usize;
        let b = ((left >> 16) & 255) as usize;
        let c = ((left >> 8) & 255) as usize;
        let d = (left & 255) as usize;
        let mixed = ((keys[0x112+b].wrapping_add(keys[0x12+a])) ^ keys[0x212+c]).wrapping_add(keys[0x312+d]) ^ keys[round];
        let previous = left;
        left = right ^ mixed;
        right = previous;
    }
    right ^= keys[0];
    let mut result = [0;8];
    result[..4].copy_from_slice(&right.to_le_bytes());
    result[4..].copy_from_slice(&left.to_le_bytes());
    result
}
pub fn decode_actions(bytes: &[u8]) -> Result<Vec<CorporateAction>,DataError> {
    if bytes.len() < 4 { return Err(invalid("gbbq header is truncated")); }
    let count = word(bytes,0) as usize;
    let expected = count.checked_mul(29).and_then(|n|n.checked_add(4)).ok_or_else(||invalid("gbbq record count overflow"))?;
    if bytes.len() != expected { return Err(invalid("gbbq record count or length mismatch")); }
    let keys = table()?;
    let mut actions = Vec::new();
    for encrypted in bytes[4..].chunks_exact(29) {
        let mut clear = [0;29];
        for index in 0..3 { clear[index*8..index*8+8].copy_from_slice(&block(&encrypted[index*8..index*8+8],&keys)); }
        clear[24..].copy_from_slice(&encrypted[24..]);
        let category = u32::from(clear[12]);
        if !matches!(category,1 | 11 | 12) { continue; }
        let code_end = clear[1..8].iter().position(|b|*b == 0).unwrap_or(7);
        let code = std::str::from_utf8(&clear[1..1+code_end]).map_err(|_|invalid("invalid gbbq security code"))?;
        let symbol = action_symbol(u32::from(clear[0]),code)?;
        let day = word(&clear,8);
        let date = NaiveDate::from_ymd_opt((day/10000) as i32,(day/100)%100,day%100).ok_or_else(||invalid("invalid gbbq action date"))?.to_string();
        let number = |offset| f64::from(f32::from_bits(word(&clear,offset)));
        let (cash,rights_price,bonus,rights) = (number(13),number(17),number(21),number(25));
        if [cash,rights_price,bonus,rights].iter().any(|v|!v.is_finite()) { return Err(invalid("invalid gbbq action values")); }
        actions.push(CorporateAction{symbol,date,category,cash,rights_price,bonus,rights});
    }
    Ok(actions)
}
pub fn read_actions(path: &Path) -> Result<(Vec<CorporateAction>,String),DataError> {
    let file = File::open(path)?;
    if file.metadata()?.len() > MAX_BYTES { return Err(invalid("gbbq file exceeds size limit")); }
    let mut bytes = Vec::new();
    file.take(MAX_BYTES+1).read_to_end(&mut bytes)?;
    if bytes.len() as u64 > MAX_BYTES { return Err(invalid("gbbq file exceeds size limit")); }
    let version = format!("{:x}",Sha256::digest(&bytes));
    let actions = decode_actions(&bytes)?;
    if fingerprint(path)? != version { return Err(DataError::Changed); }
    Ok((actions,version))
}
#[derive(Clone,Debug,Serialize)]
pub struct AdjustedMarketData {
    pub symbol: String, pub start: String, pub asof: String,
    pub version: String, pub daily_version: String, pub actions_version: String,
    pub price_basis: String, pub bars: Vec<ExecutionBar>,
}
impl TdxReader {
    pub fn adjusted_bars(&self,symbol: &str,start: &str,asof: &str) -> Result<AdjustedMarketData,DataError> {
        let metadata = self.stock_metadata(symbol)?;
        let name = metadata["name"].as_str().ok_or_else(||invalid("缺少股票名称，无法排除 ST 或退市标的"))?.to_uppercase();
        if name.contains("ST") || name.contains('退') { return Err(invalid("缺少历史时点 ST/退市状态，不支持执行回测")); }
        let source = self.bars(symbol,asof)?;
        if source.bars.first().is_none_or(|bar|bar.date.as_str() >= start) { return Err(invalid("choose a start after listing; IPO rules are not modeled")); }
        let actions_path = self.root().join("T0002/hq_cache/gbbq").canonicalize()?;
        if !actions_path.starts_with(self.root()) { return Err(invalid("corporate-action path escaped TDX root")); }
        let (actions,actions_version) = read_actions(&actions_path)?;
        let bars = adjust_rows(&source.bars,&actions,start,asof,symbol)?;
        if bars.is_empty() { return Err(invalid("no data in requested range")); }
        if fingerprint(&self.day_path(symbol)?)? != source.version || fingerprint(&actions_path)? != actions_version { return Err(DataError::Changed); }
        let version = format!("{:x}",Sha256::digest(format!("tdx-causal-v1:{}:{}:{symbol}:{start}:{asof}",source.version,actions_version).as_bytes()));
        Ok(AdjustedMarketData{symbol:symbol.into(),start:start.into(),asof:asof.into(),version,daily_version:source.version,actions_version,price_basis:"causal multiplicative adjusted equivalent units".into(),bars})
    }
}
