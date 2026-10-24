//! 与旧 stock_info_a_code_name 相同的交易所目录及新浪交易日历。
use std::{fmt,io::{Cursor,Read}};
use calamine::{Reader,Xlsx};
use chrono::{DateTime,Datelike,Duration,FixedOffset,Timelike,Utc,Weekday};
use serde::{Deserialize,Deserializer,de::{MapAccess,Visitor}};
use crate::indexed_json::{Value,json};
use sha2::{Digest,Sha256};
use super::{sina::SinaProvider,sina_codec::decode_calendar,tdx::{DataError,is_stock,parse_date}};
const MAX_BYTES:u64=20*1024*1024;
fn invalid(s:&str)->DataError { DataError::Invalid(s.into()) }
fn unavailable()->DataError { DataError::Unavailable("在线交易所目录暂不可用".into()) }
#[derive(Debug)]
struct OrderedRecord(Vec<(String,Value)>);
impl<'de> Deserialize<'de> for OrderedRecord {
    fn deserialize<D:Deserializer<'de>>(deserializer:D)->Result<Self,D::Error> {
        struct Fields;
        impl<'de> Visitor<'de> for Fields {
            type Value=OrderedRecord;
            fn expecting(&self,f:&mut fmt::Formatter)->fmt::Result { f.write_str("an exchange record object") }
            fn visit_map<M:MapAccess<'de>>(self,mut map:M)->Result<Self::Value,M::Error> {
                let mut fields=Vec::new();
                while let Some(field)=map.next_entry::<String,Value>()? { fields.push(field); }
                Ok(OrderedRecord(fields))
            }
        }
        deserializer.deserialize_map(Fields)
    }
}
#[derive(Deserialize)]
struct BeijingPage { #[serde(rename="totalPages")] total_pages:usize, content:Vec<OrderedRecord> }
fn code(value:&str)->String {
    let text=value.trim().split('.').next().unwrap_or_default();
    if text.len()>6 || !text.bytes().all(|b|b.is_ascii_digit()) { return String::new(); }
    format!("{text:0>6}")
}
fn symbol(code:&str)->Option<String> {
    ["sh","sz","bj"].iter().map(|m|format!("{m}.{code}")).find(|s|is_stock(s))
}
fn field_text(value:&Value)->Option<String> {
    match (value).kind() { Some(crate::indexed_json::Kind::String(s))=>Some(s.clone()),Some(crate::indexed_json::Kind::Number(n))=>Some(n.to_string()),_=>None }
}
impl SinaProvider {
    fn exchange_bytes(&self,url:&str,params:&[(&str,&str)],post:bool,referer:Option<&str>)->Result<Vec<u8>,DataError> {
        let _slot=self.slot.try_lock().map_err(|_|unavailable())?;
        let mut request=if post { self.client.post(url).form(params) } else { self.client.get(url).query(params) };
        // 北交所对旧浏览器标识返回同地址 307；保留本客户端真实标识，避免把重定向页当目录。
        if !url.starts_with("https://www.bse.cn/"){request=request.header(reqwest::header::USER_AGENT,"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/110.0.0.0 Safari/537.36");}
        if let Some(referer)=referer { request=request.header(reqwest::header::REFERER,referer); }
        let response=request.send().and_then(reqwest::blocking::Response::error_for_status).map_err(|_|unavailable())?;
        if !response.status().is_success(){return Err(unavailable());}
        if response.content_length().is_some_and(|n|n>MAX_BYTES) { return Err(invalid("exchange response exceeds size limit")); }
        let mut bytes=Vec::new();
        response.take(MAX_BYTES+1).read_to_end(&mut bytes).map_err(|_|unavailable())?;
        if bytes.len() as u64>MAX_BYTES { return Err(invalid("exchange response exceeds size limit")); }
        Ok(bytes)
    }
    fn shanghai_names(&self,board:&str)->Result<(Vec<(String,String)>,String),DataError> {
        let params=[("STOCK_TYPE",board),("REG_PROVINCE",""),("CSRC_CODE",""),("STOCK_CODE",""),("sqlId","COMMON_SSE_CP_GPJCTPZ_GPLB_GP_L"),("COMPANY_STATUS","2,4,5,7,8"),("type","inParams"),("isPagination","true"),("pageHelp.cacheSize","1"),("pageHelp.beginPage","1"),("pageHelp.pageSize","10000"),("pageHelp.pageNo","1"),("pageHelp.endPage","1")];
        let bytes=self.exchange_bytes("https://query.sse.com.cn/sseQuery/commonQuery.do",&params,false,Some("https://www.sse.com.cn/assortment/stock/list/share/"))?;
        let document:Value=crate::indexed_json::from_slice(&bytes).map_err(|_|invalid("invalid SSE catalog JSON"))?;
        let records=document["result"].as_array().filter(|r|r.len()<=10000).ok_or_else(||invalid("SSE catalog records missing"))?;
        let mut result=Vec::new();
        for record in records {
            if let (Some(code),Some(name))=(field_text(&record["A_STOCK_CODE"]),record["SEC_NAME_CN"].as_str()) { result.push((code,name.into())); }
        }
        Ok((result,format!("{:x}",Sha256::digest(&bytes))))
    }
    fn shenzhen_names(&self)->Result<(Vec<(String,String)>,String),DataError> {
        let bytes=self.exchange_bytes("https://www.szse.cn/api/report/ShowReport",&[("SHOWTYPE","xlsx"),("CATALOGID","1110"),("TABKEY","tab1"),("random","0.6935816432433362")],false,None)?;
        let version=format!("{:x}",Sha256::digest(&bytes));
        let mut workbook=Xlsx::new(Cursor::new(bytes)).map_err(|_|invalid("invalid SZSE xlsx workbook"))?;
        let range=workbook.worksheet_range_at(0).ok_or_else(||invalid("SZSE worksheet missing"))?.map_err(|_|invalid("SZSE worksheet unreadable"))?;
        if range.height()>20001 || range.width()>100 { return Err(invalid("SZSE worksheet exceeds limits")); }
        let mut rows=range.rows();
        let header=rows.next().ok_or_else(||invalid("SZSE header missing"))?;
        let code_col=header.iter().position(|v|v.to_string()=="A股代码").ok_or_else(||invalid("SZSE A-share code column missing"))?;
        let name_col=header.iter().position(|v|v.to_string()=="A股简称").ok_or_else(||invalid("SZSE A-share name column missing"))?;
        let mut result=Vec::new();
        for row in rows {
            if let (Some(code),Some(name))=(row.get(code_col),row.get(name_col)) { result.push((code.to_string(),name.to_string())); }
        }
        Ok((result,version))
    }
    fn beijing_page(&self,page:usize)->Result<(BeijingPage,String),DataError> {
        let page_text=page.to_string();
        let bytes=self.exchange_bytes("https://www.bse.cn/nqxxController/nqxxCnzq.do",&[("page",page_text.as_str()),("typejb","T"),("xxfcbj[]","2"),("xxzqdm",""),("sortfield","xxzqdm"),("sorttype","asc")],true,None)?;
        let start=bytes.iter().position(|b|*b==b'[').ok_or_else(||invalid("BSE catalog envelope missing"))?;
        let end=bytes.iter().rposition(|b|*b==b']').ok_or_else(||invalid("BSE catalog envelope incomplete"))?;
        if start>=end { return Err(invalid("invalid BSE envelope")); }
        let mut pages:Vec<BeijingPage>=crate::indexed_json::from_slice(&bytes[start..=end]).map_err(|_|invalid("invalid BSE catalog JSON"))?;
        if pages.len()!=1 { return Err(invalid("invalid BSE page shape")); }
        Ok((pages.remove(0),format!("{:x}",Sha256::digest(&bytes))))
    }
    fn beijing_names(&self)->Result<(Vec<(String,String)>,Vec<String>),DataError> {
        let (first,version)=self.beijing_page(0)?;
        let total=first.total_pages;
        if total==0 || total>1000 { return Err(invalid("BSE page count outside bounds")); }
        let mut result=Vec::new();
        let mut versions=vec![version];
        let mut rows=first.content;
        for page in 1..total {
            let (next,version)=self.beijing_page(page)?;
            if next.total_pages!=total { return Err(DataError::Changed); }
            versions.push(version);
            rows.extend(next.content);
            if rows.len()>20000 { return Err(invalid("BSE catalog exceeds row limit")); }
        }
        // 原安装包按返回字段顺序选取证券代码与简称；保留顺序，禁止猜测字段别名。
        for row in rows {
            let code=row.0.get(38).and_then(|(_,v)|field_text(v)).ok_or_else(||invalid("BSE code layout changed"))?;
            let name=row.0.get(40).and_then(|(_,v)|field_text(v)).ok_or_else(||invalid("BSE name layout changed"))?;
            result.push((code,name));
        }
        Ok((result,versions))
    }
    pub fn calendar(&self)->Result<Vec<String>,DataError> {
        let body=self.text("https://finance.sina.com.cn/realstock/company/klc_td_sh.txt",&[])?;
        let value=body.split_once('=').ok_or_else(||invalid("Sina calendar assignment missing"))?.1.trim().trim_end_matches(';').trim();
        let encoded:String=crate::indexed_json::from_str(value).map_err(|_|invalid("Sina calendar payload invalid"))?;
        decode_calendar(&encoded)
    }
    pub fn catalog(&self,now:DateTime<Utc>)->Result<Value,DataError> {
        let (mut names,sz_version)=self.shenzhen_names()?;
        let (sh_names,sh_version)=self.shanghai_names("1")?;
        let (star_names,star_version)=self.shanghai_names("8")?;
        let (bj_names,bj_versions)=self.beijing_names()?;
        names.extend(sh_names); names.extend(star_names); names.extend(bj_names);
        if names.len()>20000 { return Err(invalid("online catalog exceeds row limit")); }
        let mut warnings=Vec::new();
        let latest=match self.calendar().and_then(|sessions|latest_completed_session(&sessions,now)) {
            Ok(day)=>day,
            Err(_)=>{ warnings.push("AkShare 交易日历暂不可用，最新日期按中国市场工作日保守估算。".to_owned()); weekday_fallback_session(now)? }
        };
        let mut seen=std::collections::BTreeSet::new();
        let mut stocks=Vec::new();
        for (raw_code,name) in names {
            let Some(symbol)=symbol(&code(&raw_code)) else {continue};
            let name=name.trim();
            if name.is_empty() || name.chars().count()>80 || !seen.insert(symbol.clone()) {continue;}
            stocks.push(json!({"symbol":symbol,"name":name,"source":"akshare","has_data":true,"status":"available_on_demand","first":null,"last":latest,"bar_count":null}));
        }
        if stocks.is_empty() { return Err(invalid("online exchanges returned no valid A-share catalog")); }
        let source_versions=json!({"szse":sz_version,"sse_main":sh_version,"sse_star":star_version,"bse_pages":bj_versions});
        let version=format!("{:x}",Sha256::digest(format!("catalog-native-v1:{latest}:{source_versions}").as_bytes()));
        Ok(json!({"with_daily":stocks.len(),"stocks":stocks,"source":"SSE + SZSE + BSE stock_info_a_code_name equivalent + Sina calendar","provider_version":"rust-native-sina-klc2-v1","data_version":version,"source_versions":source_versions,"available":true,"latest":latest,"scope":"online_SH_SZ_BJ_A_shares","warnings":warnings,"notice":"在线沪深北 A 股目录；最新日期按中国市场交易日历和日线就绪时点确定。"}))
    }
}
pub fn latest_completed_session(sessions:&[String],now:DateTime<Utc>)->Result<String,DataError> {
    let zone=FixedOffset::east_opt(8*3600).ok_or_else(||invalid("invalid market timezone"))?;
    let local=now.with_timezone(&zone);
    let cutoff=if local.hour()>=17 {local.date_naive()} else {local.date_naive()-Duration::days(1)};
    let mut latest=None;
    for session in sessions {
        let day=parse_date(session)?;
        if day<=cutoff && latest.is_none_or(|previous|day>previous) { latest=Some(day); }
    }
    latest.map(|d|d.to_string()).ok_or_else(||invalid("Sina calendar has no completed session"))
}
pub fn weekday_fallback_session(now:DateTime<Utc>)->Result<String,DataError> {
    let zone=FixedOffset::east_opt(8*3600).ok_or_else(||invalid("invalid market timezone"))?;
    let local=now.with_timezone(&zone);
    let mut day=if local.hour()>=17 {local.date_naive()} else {local.date_naive()-Duration::days(1)};
    while matches!(day.weekday(),Weekday::Sat | Weekday::Sun) { day-=Duration::days(1); }
    Ok(day.to_string())
}
