//! 指定日期的东方财富涨停池；空池是明确的上游结果，失败不替换日期。
use std::{collections::{BTreeSet,VecDeque},sync::Mutex,time::{Duration,Instant}};
use chrono::{Datelike,FixedOffset,NaiveTime,Utc};
use wavequant_quant_rs::indexed_json::{Value,json};
use crate::presentation::error::ApiError;

type Result<T>=std::result::Result<T,ApiError>;
pub struct LimitUpLadder {cache:Mutex<VecDeque<(String,Instant,Value)>>,timeout:Duration}
impl LimitUpLadder {
    pub fn new(timeout:Duration)->Self {Self{cache:Mutex::new(VecDeque::new()),timeout}}
    pub fn snapshot(&self,date:Option<&str>,refresh:bool)->Result<Value> {
        let zone=FixedOffset::east_opt(8*3600).ok_or_else(ApiError::internal)?;
        let now=Utc::now().with_timezone(&zone);
        let chosen=match date{Some(value)=>super::market::parse_day(value)?,None=>now.date_naive()};
        if chosen>now.date_naive()||chosen.year()<1990{return Err(ApiError::invalid("日期必须在 1990-01-01 至今天之间"));}
        let key=chosen.to_string();
        if !refresh {
            let mut cache=self.cache.lock().map_err(|_|ApiError::internal())?;
            if let Some(position)=cache.iter().position(|(date,expires,_)|*date==key&&Instant::now()<*expires){let item=cache.remove(position).ok_or_else(ApiError::internal)?;let result=item.2.clone();cache.push_back(item);return Ok(result);}
        }
        let compact=chosen.format("%Y%m%d").to_string();
        let data=wavequant_quant_rs::data::akshare::AkShareProvider::new(self.timeout).and_then(|provider|provider.limit_up(&compact)).map_err(|error|ApiError::unavailable(error.to_string()))?;
        let rows=data["rows"].as_array().ok_or_else(upstream)?;let stocks=normalize(rows)?;let empty=stocks.is_empty();
        let payload=json!({"date":key,"source":"akshare/eastmoney","provider_version":format!("akshare-python/{}",data["sdk_version"].as_str().ok_or_else(upstream)?),"sdk_version":data["sdk_version"],"transport":"python_sdk_process","data_version":data["data_version"],"endpoint":"stock_zt_pool_em","fetched_at":now.to_rfc3339(),"status":if empty{"empty"}else{"ok"},"stocks":stocks,"notice":if empty{"该日期暂无涨停池数据，可能为休市日、尚未发布或超出上游历史范围。"}else{"东方财富涨停股池口径，不含 ST、科创板及未开板新股；当日数据为抓取时快照。"}});
        let ttl=if chosen==now.date_naive()||empty{60}else{3600};
        let mut cache=self.cache.lock().map_err(|_|ApiError::internal())?;cache.retain(|(date,_,_)|date!=&key);cache.push_back((key,Instant::now()+Duration::from_secs(ttl),payload.clone()));while cache.len()>128{cache.pop_front();}Ok(payload)
    }
}
fn upstream()->ApiError {ApiError::unavailable("东方财富涨停池暂不可用或返回格式异常")}
fn number(value:&Value)->Option<f64> {value.as_f64().or_else(||value.as_str().and_then(|value|value.parse().ok())).filter(|number|number.is_finite())}
fn seal(value:&Value)->Option<String> {
    let raw=if let Some(text)=value.as_str(){text.to_owned()}else{value.to_string()};
    let text=raw.strip_suffix(".0").unwrap_or(&raw);let padded=format!("{text:0>6}");
    if padded.len()!=6||!padded.bytes().all(|byte|byte.is_ascii_digit()){return None;}
    NaiveTime::parse_from_str(&padded,"%H%M%S").ok().map(|time|time.format("%H:%M:%S").to_string())
}
fn normalize(rows:&[Value])->Result<Vec<Value>> {
    let mut stocks=Vec::new();let mut seen=BTreeSet::new();
    for row in rows {
        let raw=row.get("代码").ok_or_else(upstream)?;let raw=raw.as_str().map(str::to_owned).unwrap_or_else(||raw.to_string());let code=format!("{:0>6}",raw.strip_suffix(".0").unwrap_or(&raw));
        let name=row["名称"].as_str().map(str::trim).filter(|name|!name.is_empty()&&!matches!(*name,"nan"|"None")).ok_or_else(upstream)?;
        let streak=number(&row["连板数"]).filter(|value|value.fract()==0.0&&(1.0..=100.0).contains(value)).ok_or_else(upstream)?;
        if code.len()!=6||!code.bytes().all(|byte|byte.is_ascii_digit())||!seen.insert(code.clone()){return Err(upstream());}
        let openings=number(&row["炸板次数"]).filter(|value|*value>=0.0&&value.fract()==0.0&&*value<=u32::MAX as f64).map(|value|value as u32);
        stocks.push(json!({"code":code,"name":name,"streak":streak as u32,"price":number(&row["最新价"]),"change_pct":number(&row["涨跌幅"]),"amount":number(&row["成交额"]),"turnover_pct":number(&row["换手率"]),"seal_amount":number(&row["封板资金"]),"first_seal":seal(&row["首次封板时间"]),"last_seal":seal(&row["最后封板时间"]),"open_count":openings,"industry":row["所属行业"].as_str().filter(|industry|!industry.is_empty()).unwrap_or("未分类")}));
    }
    stocks.sort_by(|a,b|b["streak"].as_u64().cmp(&a["streak"].as_u64()).then_with(||a["first_seal"].as_str().unwrap_or("99").cmp(b["first_seal"].as_str().unwrap_or("99"))).then_with(||a["code"].as_str().cmp(&b["code"].as_str())));Ok(stocks)
}
