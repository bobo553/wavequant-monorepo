//! 浏览模式的显式上游切换；固定新浪模式与交易执行不得调用跨源后备。
use std::{collections::BTreeMap,sync::Mutex,time::{Duration,Instant}};
use chrono::{DateTime,Datelike,Utc};
use serde::{Serialize,Deserialize};
use crate::indexed_json::Value;
use sha2::{Digest,Sha256};
use super::{sina::SinaProvider,akshare::AkShareProvider,tdx::{DailyBar,MarketData,DataError,is_stock,parse_date}};
fn invalid(s:&str)->DataError {DataError::Invalid(s.into())}
fn numeric(value:&Value)->Result<f64,DataError> {
    let n=value.as_f64().or_else(||value.as_str().and_then(|s|s.parse().ok())).ok_or_else(||invalid("invalid online price/volume"))?;
    if !n.is_finite() {return Err(invalid("non-finite online price/volume"));}Ok(n)
}
fn shares(value:f64)->Result<u64,DataError> {
    if value<0.0 || !value.is_finite() || value.fract()!=0.0 || value>u64::MAX as f64 {return Err(invalid("invalid raw-share volume"));}Ok(value as u64)
}
fn validate(bars:&[DailyBar])->Result<(),DataError> {
    if bars.is_empty() || bars.len()>20000 {return Err(invalid("online bars empty or exceed limit"));}
    for bar in bars {
        parse_date(&bar.date)?;
        if [bar.open,bar.high,bar.low,bar.close,bar.amount].iter().any(|v|!v.is_finite()) || bar.low<=0.0 || bar.low>bar.open.min(bar.close) || bar.high<bar.open.max(bar.close) {return Err(invalid("invalid online OHLC"));}
    }
    if bars.windows(2).any(|p|p[0].date>=p[1].date) {return Err(invalid("online dates duplicate or non-increasing"));}Ok(())
}
fn assignment(text:&str)->Result<Value,DataError> {
    let start=text.find("={").ok_or_else(||invalid("Tencent JSON assignment missing"))?+1;
    json5::from_str(text[start..].trim().trim_end_matches(';')).map_err(|_|invalid("Tencent JSON invalid"))
}
impl SinaProvider {
    pub fn eastmoney_daily(&self,symbol:&str,asof:&str)->Result<MarketData,DataError> {
        if !is_stock(symbol) {return Err(invalid("invalid Eastmoney A-share symbol"));}parse_date(asof)?;
        let code=&symbol[3..];let market=if code.starts_with('6'){1}else{0};
        let secid=format!("{market}.{code}");let end=asof.replace('-',"");
        // ut是现有公开客户端参数，不是用户认证凭据。
        let body=self.text("https://push2his.eastmoney.com/api/qt/stock/kline/get",&[("fields1","f1,f2,f3,f4,f5,f6"),("fields2","f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f116"),("ut","7eea3edcaed734bea9cbfc24409ed989"),("klt","101"),("fqt","0"),("secid",secid.as_str()),("beg","19900101"),("end",end.as_str())])?;
        let document:Value=crate::indexed_json::from_str(&body).map_err(|_|invalid("Eastmoney JSON invalid"))?;
        let records=document["data"]["klines"].as_array().ok_or_else(||DataError::Unavailable("东方财富未返回日线".into()))?;
        let mut bars=Vec::new();
        for record in records {
            let fields:Vec<_>=record.as_str().ok_or_else(||invalid("invalid Eastmoney row"))?.split(',').collect();
            if fields.len()<11 {return Err(invalid("Eastmoney row missing fields"));}
            let number=|index:usize|fields[index].parse::<f64>().map_err(|_|invalid("invalid Eastmoney numeric field"));
            if fields[0]>asof {continue;}
            bars.push(DailyBar{date:fields[0].into(),open:number(1)?,close:number(2)?,high:number(3)?,low:number(4)?,volume:shares(number(5)?*100.0)?,amount:number(6)?});
        }
        bars.sort_by(|a,b|a.date.cmp(&b.date));validate(&bars)?;
        Ok(MarketData{symbol:symbol.into(),asof:asof.into(),version:format!("{:x}",Sha256::digest(format!("eastmoney-raw-v1:{symbol}:{asof}:{body}").as_bytes())),bars})
    }
    pub fn tencent_daily(&self,symbol:&str,start:&str,asof:&str)->Result<MarketData,DataError> {
        if !is_stock(symbol) {return Err(invalid("invalid Tencent A-share symbol"));}
        let mut first=parse_date(start)?;let last=parse_date(asof)?;
        if first>last {return Err(invalid("Tencent start must not exceed end"));}
        let code=symbol.replace('.',"");
        let lookup=self.text("https://web.ifzq.gtimg.cn/other/klineweb/klineWeb/weekTrends",&[("code",code.as_str()),("type","qfq"),("_var","trend_qfq"),("r","0.3506048543943414")])?;
        let document=assignment(&lookup)?;
        let first_date=if let Some(date)=document["data"].as_array().and_then(|r|r.first()).and_then(|r|r[0].as_str()) {date.to_owned()} else {
            let parameter=format!("{code},day,,,320,qfq");
            let body=self.text("https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get",&[("_var","kline_dayqfq"),("param",parameter.as_str()),("r","0.751892490072597")])?;
            let data=assignment(&body)?;
            data["data"][&code]["day"].as_array().and_then(|r|r.first()).and_then(|r|r[0].as_str()).ok_or_else(||invalid("Tencent initial history date missing"))?.to_owned()
        };
        first=first.max(parse_date(&first_date)?);
        if last.year()-first.year()>100 {return Err(invalid("Tencent requested history exceeds bound"));}
        let mut versions=Vec::new();let mut unique=BTreeMap::<String,DailyBar>::new();
        for year in first.year()..=last.year() {
            let variable=format!("kline_day{year}");let parameter=format!("{code},day,{year}-01-01,{}-12-31,640,",year+1);
            let body=self.text("https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get",&[("_var",variable.as_str()),("param",parameter.as_str()),("r","0.8205512681390605")])?;
            versions.push(format!("{:x}",Sha256::digest(body.as_bytes())));
            let document=assignment(&body)?;
            // 请求raw却只返回复权数据不能冒充raw，明确拒绝。
            let rows=document["data"][&code]["day"].as_array().ok_or_else(||invalid("Tencent raw day records missing"))?;
            for row in rows {
                let fields=row.as_array().filter(|r|r.len()>=9).ok_or_else(||invalid("Tencent row missing fields"))?;
                let date=fields[0].as_str().ok_or_else(||invalid("Tencent date missing"))?;
                let day=parse_date(date)?;if day<first || day>last {continue;}
                let volume_scale=if code.starts_with("sh688") || code.starts_with("sz399") || code.starts_with("sh000") || code.starts_with("sz000"){1.0}else{100.0};
                let bar=DailyBar{date:date.into(),open:numeric(&fields[1])?,close:numeric(&fields[2])?,high:numeric(&fields[3])?,low:numeric(&fields[4])?,volume:shares(numeric(&fields[5])?*volume_scale)?,amount:numeric(&fields[8])?*10000.0};
                if let Some(old)=unique.get(date) {if old.open!=bar.open || old.high!=bar.high || old.low!=bar.low || old.close!=bar.close || old.volume!=bar.volume || old.amount!=bar.amount {return Err(invalid("conflicting overlapping Tencent history"));}}
                unique.insert(date.into(),bar);
            }
        }
        let bars:Vec<_>=unique.into_values().collect();validate(&bars)?;
        Ok(MarketData{symbol:symbol.into(),asof:asof.into(),version:format!("{:x}",Sha256::digest(format!("tencent-raw-v1:{symbol}:{start}:{asof}:{}",versions.join(":")).as_bytes())),bars})
    }
}
#[derive(Clone,Debug,Serialize,Deserialize)]
pub struct BrowserMarketData {pub upstream:String,pub endpoint:String,pub data:MarketData}
#[derive(Default)]
struct RetryState {primary:Option<Instant>,sina:Option<Instant>}
pub struct BrowserHistoryProvider {primary:AkShareProvider,sina:AkShareProvider,tencent:AkShareProvider,pinned:bool,retry:Mutex<RetryState>}
impl BrowserHistoryProvider {
    pub fn new(pinned:bool)->Result<Self,DataError> {Ok(Self{primary:AkShareProvider::new(Duration::from_secs(3))?,sina:AkShareProvider::new(Duration::from_secs(10))?,tencent:AkShareProvider::new(Duration::from_secs(20))?,pinned,retry:Mutex::new(RetryState::default())})}
    pub fn history(&self,symbol:&str,asof:&str,now:DateTime<Utc>)->Result<BrowserMarketData,DataError> {
        let date=parse_date(asof)?;if date>now.date_naive(){return Err(invalid("online replay cannot be later than today"));}
        if self.pinned {
            if symbol.starts_with("bj."){return Err(DataError::Unavailable("固定新浪历史模式暂不支持北交所".into()));}
            return Ok(BrowserMarketData{upstream:"sina".into(),endpoint:"stock_zh_a_daily".into(),data:self.sina.daily(symbol,asof)?});
        }
        let now_tick=Instant::now();
        let use_primary=self.retry.lock().map_err(|_|invalid("provider state unavailable"))?.primary.is_none_or(|t|now_tick>=t);
        if use_primary {
            match self.primary.eastmoney_daily(symbol,asof) {
                Ok(data)=>return Ok(BrowserMarketData{upstream:"eastmoney".into(),endpoint:"stock_zh_a_hist".into(),data}),
                Err(DataError::Unavailable(_))=>self.retry.lock().map_err(|_|invalid("provider state unavailable"))?.primary=Some(now_tick+Duration::from_secs(600)),
                Err(error)=>return Err(error),
            }
        }
        let use_sina=self.retry.lock().map_err(|_|invalid("provider state unavailable"))?.sina.is_none_or(|t|now_tick>=t);
        if !symbol.starts_with("bj.") && use_sina {
            match self.sina.daily(symbol,asof) {
                Ok(data)=>return Ok(BrowserMarketData{upstream:"sina".into(),endpoint:"stock_zh_a_daily".into(),data}),
                Err(DataError::Unavailable(_))=>self.retry.lock().map_err(|_|invalid("provider state unavailable"))?.sina=Some(now_tick+Duration::from_secs(600)),
                Err(error)=>return Err(error),
            }
        }
        let start=(now.date_naive()-chrono::Duration::days(180)).to_string();
        Ok(BrowserMarketData{upstream:"tencent".into(),endpoint:"stock_zh_a_hist_tx".into(),data:self.tencent.tencent_daily(symbol,&start,asof)?})
    }
}
