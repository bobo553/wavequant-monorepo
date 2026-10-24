//! 新浪同源行情提供器，对应 AKShare 的日线、复权因子、五分钟端点。
use std::{collections::{BTreeMap,BTreeSet},io::Read,sync::{Arc,Mutex},time::Duration};
use reqwest::blocking::Client;
use serde::{Serialize,Deserialize};
use crate::indexed_json::Value;
use sha2::{Digest,Sha256};
use super::{tdx::{DataError,MarketData,is_stock,parse_date},sina_codec::decode_daily,minute::{MinuteBar,MinuteError,verify_minute_day},adjustments::{ExecutionBar,opening_permissions}};
const MAX_RESPONSE: u64 = 20*1024*1024;
fn invalid(s: &str) -> DataError { DataError::Invalid(s.into()) }
fn upstream() -> DataError { DataError::Unavailable("新浪上游请求失败或超时".into()) }
fn provider_symbol(symbol: &str) -> Result<String,DataError> {
    if !is_stock(symbol) { return Err(invalid("invalid Sina A-share symbol")); }
    Ok(symbol.replace('.',""))
}
fn numeric(value: &Value) -> Result<f64,DataError> {
    let number = value.as_f64().or_else(||value.as_str().and_then(|s|s.parse().ok())).ok_or_else(||invalid("invalid Sina numeric field"))?;
    if !number.is_finite() { return Err(invalid("non-finite Sina field")); }
    Ok(number)
}
fn skip_javascript_comments(mut text:&str)->Result<&str,DataError> {
    loop {
        text=text.trim_start();
        if let Some(comment)=text.strip_prefix("/*") {
            text=comment.split_once("*/").ok_or_else(||invalid("Sina daily comment is incomplete"))?.1;
        } else if let Some(comment)=text.strip_prefix("//") {
            text=comment.find(['\r','\n']).map_or("",|end|&comment[end..]);
        } else {return Ok(text);}
    }
}
/// 上游在赋值后附加注释；只读取指定证券的字符串字面量，不执行 JavaScript。
fn daily_payload(body:&str,code:&str)->Result<String,DataError> {
    let (declaration,value)=body.trim_start_matches('\u{feff}').split_once('=').ok_or_else(||invalid("Sina daily assignment missing"))?;
    let name=declaration.trim().strip_prefix("var").filter(|rest|rest.starts_with(char::is_whitespace)).map(str::trim);
    if name!=Some(format!("KLC_K2_{code}").as_str()){return Err(invalid("Sina daily assignment does not match requested symbol"));}
    let value=value.trim_start();
    let mut stream=crate::indexed_json::Deserializer::from_str(value).into_iter::<String>();
    let encoded=stream.next().transpose().map_err(|_|invalid("Sina daily payload must be a quoted string"))?
        .filter(|text|!text.is_empty()).ok_or_else(||invalid("Sina daily payload is empty"))?;
    let trailing=skip_javascript_comments(&value[stream.byte_offset()..])?;
    let trailing=skip_javascript_comments(trailing.strip_prefix(';').unwrap_or(trailing))?;
    if !trailing.is_empty(){return Err(invalid("unexpected content after Sina daily payload"));}
    Ok(encoded)
}
#[derive(Clone)]
pub struct SinaProvider { pub(crate) client: Client, pub(crate) slot: Arc<Mutex<()>> }
#[derive(Clone,Debug,Serialize,Deserialize)]
pub struct Factor { pub date: String, pub value: f64 }
#[derive(Clone,Debug,Serialize)]
pub struct FactorData { pub factors: Vec<Factor>, pub version: String }
impl SinaProvider {
    pub fn new(timeout: Duration) -> Result<Self,DataError> {
        if timeout < Duration::from_secs(1) || timeout > Duration::from_secs(60) { return Err(invalid("Sina timeout must be 1..60 seconds")); }
        let client = Client::builder().timeout(timeout).connect_timeout(timeout).redirect(reqwest::redirect::Policy::none()).user_agent("WaveQuant Rust market-data adapter").build().map_err(|_|upstream())?;
        Ok(Self{client,slot:Arc::new(Mutex::new(()))})
    }
    pub(crate) fn text(&self,url: &str,query: &[(&str,&str)]) -> Result<String,DataError> {
        let _slot = self.slot.try_lock().map_err(|_|DataError::Unavailable("新浪数据源繁忙，请稍后重试".into()))?;
        let response = self.client.get(url).query(query).send().and_then(reqwest::blocking::Response::error_for_status).map_err(|_|upstream())?;
        if response.content_length().is_some_and(|n|n > MAX_RESPONSE) { return Err(invalid("Sina response exceeds size limit")); }
        let mut bytes = Vec::new();
        response.take(MAX_RESPONSE+1).read_to_end(&mut bytes).map_err(|_|upstream())?;
        if bytes.len() as u64 > MAX_RESPONSE { return Err(invalid("Sina response exceeds size limit")); }
        String::from_utf8(bytes).map_err(|_|invalid("Sina response is not UTF-8"))
    }
    pub fn daily(&self,symbol: &str,asof: &str) -> Result<MarketData,DataError> {
        parse_date(asof)?;
        let code = provider_symbol(symbol)?;
        let body = self.text(&format!("https://finance.sina.com.cn/realstock/company/{code}/hisdata_klc2/klc_kl.js"),&[])?;
        let encoded=daily_payload(&body,&code)?;
        let decoded = decode_daily(&encoded)?;
        let (shares,shares_version)=self.share_amounts(symbol)?;
        let bars=join_share_amounts(&decoded,&shares,asof)?.into_iter().map(|record|record.bar).collect::<Vec<_>>();
        if bars.is_empty() { return Err(DataError::Unavailable("该回放日期之前没有完整新浪日线/股本数据".into())); }
        let version = format!("{:x}",Sha256::digest(format!("sina-klc2-3466-share-join-v2:{symbol}:{asof}:{shares_version}:{body}").as_bytes()));
        Ok(MarketData{symbol:symbol.into(),asof:asof.into(),version,bars})
    }
    pub fn factors(&self,symbol: &str) -> Result<FactorData,DataError> {
        let code=provider_symbol(symbol)?;
        let body=self.text(&format!("https://finance.sina.com.cn/realstock/company/{code}/hfq.js"),&[])?;
        let assignment=body.split_once('=').ok_or_else(||invalid("Sina factor assignment missing"))?.1.lines().next().ok_or_else(||invalid("Sina factor payload missing"))?.trim().trim_end_matches(';');
        let document: Value=json5::from_str(assignment).map_err(|_|invalid("invalid Sina factor JSON"))?;
        let records=document["data"].as_array().filter(|v|!v.is_empty() && v.len()<=100000).ok_or_else(||invalid("Sina factor records missing"))?;
        let mut factors=Vec::new();
        let mut seen=BTreeSet::new();
        for row in records {
            let pair=row.as_array().filter(|v|v.len()==2).ok_or_else(||invalid("invalid Sina factor row"))?;
            let date=pair[0].as_str().ok_or_else(||invalid("Sina factor date missing"))?;
            parse_date(date)?;
            let value=numeric(&pair[1])?;
            if value<=0.0 || !seen.insert(date.to_owned()) { return Err(invalid("Sina factor invalid or duplicate")); }
            factors.push(Factor{date:date.into(),value});
        }
        factors.sort_by(|a,b|a.date.cmp(&b.date));
        Ok(FactorData{factors,version:format!("{:x}",Sha256::digest(body.as_bytes()))})
    }
    pub fn minute_history(&self,symbol: &str) -> Result<(BTreeMap<String,Vec<MinuteBar>>,String),DataError> {
        let code=provider_symbol(symbol)?;
        let query=[("symbol",code.as_str()),("scale","5"),("ma","no"),("datalen","1970")];
        let primary="https://quotes.sina.cn/cn/api/jsonp_v2.php/=/CN_MarketDataService.getKLineData";
        let mut body=self.text(primary,&query)?;
        if !body.contains("=(") {
            body=self.text(&format!("https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_{code}_5_1658852984203=/CN_MarketDataService.getKLineData"),&query)?;
        }
        let json=body.split_once("=(").and_then(|(_,s)|s.rsplit_once(");").map(|(v,_)|v)).ok_or_else(||invalid("invalid Sina minute JSONP envelope"))?;
        let records: Vec<Value>=crate::indexed_json::from_str(json).map_err(|_|invalid("invalid Sina minute JSON"))?;
        if records.len()>20000 { return Err(invalid("Sina minute history exceeds limit")); }
        let mut grouped=BTreeMap::<String,Vec<MinuteBar>>::new();
        for row in records {
            let stamp=row["day"].as_str().ok_or_else(||invalid("Sina minute date missing"))?;
            let datetime=chrono::NaiveDateTime::parse_from_str(stamp,"%Y-%m-%d %H:%M:%S").map_err(|_|invalid("invalid Sina minute date"))?;
            grouped.entry(datetime.date().to_string()).or_default().push(MinuteBar{timestamp:format!("{}+08:00",datetime.format("%Y-%m-%dT%H:%M:%S")),open:numeric(&row["open"])?,high:numeric(&row["high"])?,low:numeric(&row["low"])?,close:numeric(&row["close"])?,volume:numeric(&row["volume"])?});
        }
        for rows in grouped.values_mut() { rows.sort_by(|a,b|a.timestamp.cmp(&b.timestamp)); }
        Ok((grouped,format!("{:x}",Sha256::digest(body.as_bytes()))))
    }
    pub fn minute_day(&self,daily: &ExecutionBar) -> Result<Vec<MinuteBar>,MinuteError> {
        let (mut history,_version)=self.minute_history(&daily.symbol)?;
        let complete: Vec<_>=history.iter().filter(|(_,v)|v.len()==48).map(|(day,_)|day.clone()).collect();
        let coverage=||MinuteError::Coverage{required_date:daily.timestamp.clone(),available_start:complete.first().cloned(),available_end:complete.last().cloned(),provider:"AKShare / 新浪".into()};
        let rows=history.remove(&daily.timestamp).filter(|v|v.len()==48).ok_or_else(coverage)?;
        match verify_minute_day(&rows,daily) {
            Err(MinuteError::VolumeMismatch{minute_volume,daily_volume,..}) if minute_volume < daily_volume => Err(coverage()),
            Err(error) => Err(error), Ok(()) => Ok(rows),
        }
    }
}
/// 因子表归一化到请求起点，严格不使用末日因子反写历史。
pub fn apply_factors(data: &MarketData,factors: &[Factor],start: &str) -> Result<Vec<ExecutionBar>,DataError> {
    parse_date(start)?;
    if data.bars.first().is_none_or(|bar|bar.date.as_str() >= start) { return Err(invalid("choose a start after listing")); }
    if factors.is_empty() || factors.windows(2).any(|p|p[0].date>=p[1].date) { return Err(invalid("missing or unsorted Sina factor history")); }
    for factor in factors { parse_date(&factor.date)?; if !factor.value.is_finite() || factor.value<=0.0 { return Err(invalid("invalid Sina factor")); } }
    let mut cursor=0;
    let mut current: Option<f64>=None;
    let mut base: Option<f64>=None;
    let mut previous: Option<f64>=None;
    let mut result=Vec::new();
    for bar in &data.bars {
        let old=current;
        while cursor<factors.len() && factors[cursor].date<=bar.date { current=Some(factors[cursor].value); cursor+=1; }
        let current=current.ok_or_else(||invalid("Sina historical starting factor missing"))?;
        if bar.date.as_str()<start { previous=Some(bar.close); continue; }
        let factor=current / *base.get_or_insert(current);
        let reference=previous.map_or(bar.open,|price|price*old.unwrap_or(current)/current);
        let active=bar.volume>0;
        let (buyable,sellable)=opening_permissions(&data.symbol,&bar.date,reference,bar.open,false,true)?;
        let (close_buyable,close_sellable)=opening_permissions(&data.symbol,&bar.date,reference,bar.close,false,active)?;
        let nonflat=active && bar.high>bar.low;
        result.push(ExecutionBar{timestamp:bar.date.clone(),symbol:data.symbol.clone(),open:bar.open*factor,high:bar.high*factor,low:bar.low*factor,close:bar.close*factor,volume:bar.volume as f64,adjustment_factor:factor,buyable,sellable,close_buyable:Some(close_buyable),close_sellable:Some(close_sellable),nonflat_close_buyable:Some(nonflat && opening_permissions(&data.symbol,&bar.date,reference,bar.low,false,active)?.0),nonflat_close_sellable:Some(nonflat && opening_permissions(&data.symbol,&bar.date,reference,bar.high,false,active)?.1),raw_is_st:Some(false),raw_trading_active:Some(active)});
        previous=Some(bar.close);
    }
    if result.is_empty() { return Err(invalid("no Sina bars in requested range")); }
    Ok(result)
}

#[derive(Clone,Debug,Serialize,Deserialize)]
pub struct ShareAmount { pub date:String, pub shares:f64 }
#[derive(Clone,Debug,Serialize)]
pub struct SinaDailyRecord {
    #[serde(flatten)] pub bar:super::tdx::DailyBar,
    pub outstanding_share:f64, pub turnover:f64,
}
/// 外连接后逐列前向填充；只以当时已知股本参与缺失剔除和换手率。
pub fn join_share_amounts(bars:&[super::tdx::DailyBar],shares:&[ShareAmount],asof:&str)->Result<Vec<SinaDailyRecord>,DataError> {
    parse_date(asof)?;
    let mut dates=BTreeMap::<String,(Option<super::tdx::DailyBar>,Option<f64>)>::new();
    for bar in bars {
        parse_date(&bar.date)?;
        if dates.entry(bar.date.clone()).or_default().0.replace(bar.clone()).is_some() {return Err(invalid("duplicate Sina history date"));}
    }
    for row in shares {
        parse_date(&row.date)?;
        if !row.shares.is_finite() || row.shares<=0.0 {return Err(invalid("invalid Sina outstanding shares"));}
        if dates.entry(row.date.clone()).or_default().1.replace(row.shares).is_some() {return Err(invalid("duplicate Sina share amount date"));}
    }
    let mut latest_bar:Option<super::tdx::DailyBar>=None;
    let mut latest_shares=None;
    let mut raw_seen=BTreeSet::new();
    let mut normalized_seen=BTreeSet::new();
    let mut result=Vec::new();
    for (date,(bar,shares)) in dates {
        if date.as_str()>asof {break;}
        if let Some(bar)=bar {latest_bar=Some(bar);}
        if let Some(shares)=shares {latest_shares=Some(shares);}
        let Some(mut bar)=latest_bar.clone() else {continue};
        let raw_key=[bar.open.to_bits(),bar.high.to_bits(),bar.low.to_bits(),bar.close.to_bits(),bar.volume,bar.amount.to_bits()];
        // Python 先依据原始OHLCV/amount去重，再round和dropna。
        if !raw_seen.insert(raw_key) {continue;}
        let Some(outstanding_share)=latest_shares else {continue};
        bar.date=date;
        bar.open=(bar.open*100.0).round_ties_even()/100.0;
        bar.high=(bar.high*100.0).round_ties_even()/100.0;
        bar.low=(bar.low*100.0).round_ties_even()/100.0;
        bar.close=(bar.close*100.0).round_ties_even()/100.0;
        let turnover=bar.volume as f64/outstanding_share;
        let key=[bar.open.to_bits(),bar.high.to_bits(),bar.low.to_bits(),bar.close.to_bits(),bar.volume,bar.amount.to_bits(),outstanding_share.to_bits(),turnover.to_bits()];
        if normalized_seen.insert(key) {result.push(SinaDailyRecord{bar,outstanding_share,turnover});}
    }
    Ok(result)
}
impl SinaProvider {
    pub fn share_amounts(&self,symbol:&str)->Result<(Vec<ShareAmount>,String),DataError> {
        let code=provider_symbol(symbol)?;
        let body=self.text(&format!("https://stock.finance.sina.com.cn/stock/api/jsonp.php/var%20KKE_ShareAmount_{code}=/StockService.getAmountBySymbol?_=20&symbol={code}"),&[])?;
        let start=body.find('[').ok_or_else(||invalid("Sina share amount array missing"))?;
        let end=body.rfind(']').ok_or_else(||invalid("Sina share amount array incomplete"))?;
        if start>=end {return Err(invalid("invalid Sina share amount envelope"));}
        let rows:Vec<Value>=json5::from_str(&body[start..=end]).map_err(|_|invalid("invalid Sina share amount JSON"))?;
        if rows.len()>100000 {return Err(invalid("Sina share amount records exceed limit"));}
        let mut shares=Vec::new();
        for row in rows {
            let pair=row.as_array().filter(|r|r.len()==2).ok_or_else(||invalid("invalid Sina share amount row"))?;
            let date=pair[0].as_str().ok_or_else(||invalid("Sina share amount date missing"))?;
            parse_date(date)?;
            shares.push(ShareAmount{date:date.into(),shares:numeric(&pair[1])?*10000.0});
        }
        Ok((shares,format!("{:x}",Sha256::digest(body.as_bytes()))))
    }
}

#[cfg(test)]
mod daily_payload_tests {
    use super::daily_payload;

    #[test]
    fn accepts_daily_assignment_with_provider_trailing_comment() {
        let body="var KLC_K2_sz002724=\"K2/LNbTEiwh3IrqVN+\";\n\n/* provider footer = \"text\" */";
        assert_eq!(daily_payload(body,"sz002724").unwrap(),"K2/LNbTEiwh3IrqVN+");
        assert_eq!(daily_payload("var KLC_K2_sz002724 = \"K2/ABC\"", "sz002724").unwrap(),"K2/ABC");
        assert_eq!(daily_payload("var KLC_K2_sz002724=\"K2/ABC\" /* comment */ ; // footer", "sz002724").unwrap(),"K2/ABC");
    }

    #[test]
    fn rejects_wrong_symbol_executable_tail_and_truncated_payload() {
        for body in [
            "var KLC_K2_sz002725=\"K2/ABC\";",
            "var KLC_K2_sz002724=\"K2/ABC\"; other();",
            "var KLC_K2_sz002724=\"K2/ABC\"; /* truncated",
            "var KLC_K2_sz002724=\"K2/ABC",
            "var KLC_K2_sz002724={};",
            "var KLC_K2_sz002724=\"\";",
        ] {assert!(daily_payload(body,"sz002724").is_err(),"accepted {body}");}
    }
}
