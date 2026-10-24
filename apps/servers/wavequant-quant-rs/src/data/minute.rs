//! 同源五分钟行情及完整交易时段校验；不使用其他源补齐缺口。
use std::{collections::BTreeMap,fs::File,io::Read,path::{Path,PathBuf}};
use chrono::{NaiveDate,NaiveDateTime,Timelike};
use serde::{Serialize,Deserialize};
use crate::indexed_json::{Value,json};
use sha2::{Digest,Sha256};
use super::{tdx::{DataError,is_stock,parse_date},adjustments::ExecutionBar};

#[derive(Clone,Debug,Serialize,Deserialize)]
pub struct MinuteBar {
    /// 上海当地时间的区间结束时点。
    pub timestamp: String,
    pub open: f64, pub high: f64, pub low: f64, pub close: f64, pub volume: f64,
}
#[derive(Debug)]
pub enum MinuteError {
    Data(DataError),
    Coverage { required_date: String, available_start: Option<String>, available_end: Option<String>, provider: String },
    VolumeMismatch { day: String, minute_volume: f64, daily_volume: f64 },
    Invalid(String),
}
impl std::fmt::Display for MinuteError {
    fn fmt(&self,f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Data(e) => e.fmt(f), Self::Invalid(s) => f.write_str(s),
            Self::VolumeMismatch{day,..} => write!(f,"{day} 五分钟与同源日线成交量不一致"),
            Self::Coverage{required_date,provider,..} => write!(f,"{provider} 缺少 {required_date} 的完整五分钟线；本次未生成成交或收益"),
        }
    }
}
impl std::error::Error for MinuteError {}
impl From<DataError> for MinuteError { fn from(e: DataError) -> Self { Self::Data(e) } }
impl From<std::io::Error> for MinuteError { fn from(e: std::io::Error) -> Self { Self::Data(DataError::Io(e)) } }
fn invalid(s: &str) -> MinuteError { MinuteError::Invalid(s.into()) }
pub fn expected_times() -> Vec<u32> {
    (0..24).map(|i|575+i*5).chain((0..24).map(|i|785+i*5)).collect()
}
pub fn verify_minute_day(rows: &[MinuteBar],daily: &ExecutionBar) -> Result<(),MinuteError> {
    let day = parse_date(&daily.timestamp)?;
    if rows.len() != 48 { return Err(invalid("五分钟行情需完整 48 根才能精确回测")); }
    if !daily.adjustment_factor.is_finite() || daily.adjustment_factor <= 0.0 { return Err(invalid("无效日线复权因子")); }
    for (row,expected) in rows.iter().zip(expected_times()) {
        let stamp = chrono::DateTime::parse_from_rfc3339(&row.timestamp).map_err(|_|invalid("五分钟行情时间格式无效"))?;
        if stamp.offset().local_minus_utc() != 8*3600 || stamp.date_naive() != day || stamp.hour()*60 + stamp.minute() != expected || stamp.second() != 0 || stamp.nanosecond() != 0 {
            return Err(invalid("五分钟行情日期或时间段不完整或顺序异常"));
        }
        if [row.open,row.high,row.low,row.close,row.volume].iter().any(|v|!v.is_finite()) || row.low <= 0.0 || row.volume < 0.0 || row.low > row.open.min(row.close) || row.high < row.open.max(row.close) {
            return Err(invalid("五分钟行情价格或成交量无效"));
        }
    }
    let volume: f64 = rows.iter().map(|b|b.volume).sum();
    let daily_volume = daily.volume;
    if (volume-daily_volume).abs() > 1.0_f64.max(daily_volume*0.00001) {
        return Err(MinuteError::VolumeMismatch{day:daily.timestamp.clone(),minute_volume:volume,daily_volume});
    }
    let high = rows.iter().map(|b|b.high).fold(f64::NEG_INFINITY,f64::max);
    let low = rows.iter().map(|b|b.low).fold(f64::INFINITY,f64::min);
    let factor = daily.adjustment_factor;
    for (actual,expected) in [(rows[0].open,daily.open/factor),(high,daily.high/factor),(low,daily.low/factor),(rows[47].close,daily.close/factor)] {
        if (actual-expected).abs() > 0.015 { return Err(invalid("五分钟与同源日线 OHLC 不一致")); }
    }
    Ok(())
}
fn price(record: &[u8],offset: usize) -> Result<f64,MinuteError> {
    let value = f64::from(f32::from_le_bytes([record[offset],record[offset+1],record[offset+2],record[offset+3]]));
    if !value.is_finite() || value <= 0.0 { return Err(invalid("通达信五分钟价格无效")); }
    // 恢复原生 float32 编码前的分位报价，避免噪声影响涨跌停判断。
    format!("{value:.2}").parse().map_err(|_|invalid("通达信五分钟价格无效"))
}
#[derive(Debug)]
pub struct TdxMinuteSource { root: PathBuf, digests: BTreeMap<String,String> }
impl TdxMinuteSource {
    pub fn new(root: impl AsRef<Path>) -> Result<Self,MinuteError> { Ok(Self{root:root.as_ref().canonicalize()?,digests:BTreeMap::new()}) }
    pub fn get(&mut self,daily: &ExecutionBar) -> Result<Vec<MinuteBar>,MinuteError> {
        let mut batch=self.batch(std::slice::from_ref(daily))?;
        batch.bars.remove(&daily.timestamp).ok_or_else(||MinuteError::Coverage{required_date:daily.timestamp.clone(),
            available_start:batch.available_start,available_end:batch.available_end,provider:"通达信本地".into()})
    }
    /// 同一请求单次读取并冻结 .lc5，缺日显式返回；损坏或与日线矛盾绝不降为缺数据。
    pub fn batch(&mut self,dailies:&[ExecutionBar])->Result<MinuteBatch,MinuteError>{
        let mut result=MinuteBatch{bars:BTreeMap::new(),missing_days:Vec::new(),available_start:None,available_end:None,source_version:None};
        let Some(first)=dailies.first()else{return Ok(result);};
        if !is_stock(&first.symbol)||dailies.iter().any(|d|d.symbol!=first.symbol){return Err(invalid("minute batch requires a single valid symbol"));}
        let mut requested=BTreeMap::new();
        for daily in dailies{parse_date(&daily.timestamp)?;if requested.insert(daily.timestamp.clone(),daily).is_some(){return Err(invalid("duplicate minute batch date"));}}
        let (market,code)=first.symbol.split_once('.').ok_or_else(||invalid("invalid minute symbol"))?;
        let path=self.root.join("vipdoc").join(market).join("fzline").join(format!("{market}{code}.lc5"));
        if !path.is_file(){result.missing_days=requested.keys().cloned().collect();return Ok(result);}
        let path=path.canonicalize()?;if !path.starts_with(&self.root){return Err(invalid("minute path escaped root"));}
        let file=File::open(&path)?;let before=file.metadata()?;let max_bytes=256*1024*1024;
        if before.len()>max_bytes{return Err(invalid("通达信五分钟文件超出读取上限"));}
        let mut bytes=Vec::new();file.take(max_bytes+1).read_to_end(&mut bytes)?;
        let after=std::fs::metadata(&path)?;
        if before.len()!=after.len()||before.modified().ok()!=after.modified().ok(){return Err(MinuteError::Data(DataError::Changed));}
        if bytes.len()as u64>max_bytes||bytes.len()%32!=0{return Err(invalid("通达信五分钟文件记录不完整或过大"));}
        let digest=format!("{:x}",Sha256::digest(&bytes));let mut previous:Option<NaiveDateTime>=None;
        let mut coverage=BTreeMap::<String,usize>::new();let mut decoded=BTreeMap::<String,Vec<MinuteBar>>::new();
        for record in bytes.chunks_exact(32){
            let packed=u16::from_le_bytes([record[0],record[1]])as u32;let clock=u16::from_le_bytes([record[2],record[3]])as u32;
            let date=NaiveDate::from_ymd_opt((2004+packed/2048)as i32,packed%2048/100,packed%2048%100).ok_or_else(||invalid("通达信五分钟日期无效"))?;
            let stamp=date.and_hms_opt(clock/60,clock%60,0).ok_or_else(||invalid("通达信五分钟时间无效"))?;
            if previous.is_some_and(|p|p>=stamp){return Err(invalid("通达信五分钟日期重复或倒序"));}previous=Some(stamp);
            let day=date.to_string();*coverage.entry(day.clone()).or_default()+=1;if !requested.contains_key(&day){continue;}
            let volume=u32::from_le_bytes([record[24],record[25],record[26],record[27]]);
            decoded.entry(day).or_default().push(MinuteBar{timestamp:format!("{}+08:00",stamp.format("%Y-%m-%dT%H:%M:%S")),
                open:price(record,4)?,high:price(record,8)?,low:price(record,12)?,close:price(record,16)?,volume:f64::from(volume)});
        }
        let complete:Vec<_>=coverage.iter().filter(|(_,count)|**count==48).map(|(day,_)|day.clone()).collect();
        result.available_start=complete.first().cloned();result.available_end=complete.last().cloned();result.source_version=Some(digest.clone());
        for(day,daily)in requested{let rows=decoded.remove(&day).unwrap_or_default();
            if rows.len()!=48{result.missing_days.push(day);continue;}
            verify_minute_day(&rows,daily)?;self.digests.insert(day.clone(),digest.clone());result.bars.insert(day,rows);
        }Ok(result)
    }
    pub fn provenance(&self) -> Value { json!({"provider":"tdx","upstream":"tdx_local","frequency":"5m","session_sha256":self.digests}) }
}

#[derive(Clone,Debug,Serialize,Deserialize)]
pub struct MinuteBatch {
    pub bars:BTreeMap<String,Vec<MinuteBar>>,pub missing_days:Vec<String>,
    pub available_start:Option<String>,pub available_end:Option<String>,pub source_version:Option<String>,
}