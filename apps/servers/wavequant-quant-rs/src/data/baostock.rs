//! BaoStock 00.9.30 原生 TCP 客户端；协议来自项目安装包，不执行 Python。
use std::{collections::BTreeMap,io::{Read,Write},net::{TcpStream,ToSocketAddrs},time::Duration};
use flate2::read::ZlibDecoder;
use crate::indexed_json::Value;
use super::{tdx::{DataError,is_stock,parse_date},minute::{MinuteBar,MinuteError,verify_minute_day},adjustments::ExecutionBar};
const HOST: &str = "public-api.baostock.com:10030";
const SPLIT: char = '\u{1}';
const END: &[u8] = b"<![CDATA[]]>\n";
const MAX_BYTES: usize = 16*1024*1024;
const PAGE_SIZE: usize = 2000;
fn invalid(message: &str) -> DataError { DataError::Invalid(message.into()) }
fn unavailable(message: &str) -> DataError { DataError::Unavailable(message.into()) }
pub type ProviderRow = BTreeMap<String,String>;
/// 每个实例拥有一个连接，不共享进程级 socket；调用方限制并发任务数。
pub struct BaoStockClient { stream: TcpStream }
impl BaoStockClient {
    pub fn connect(timeout: Duration) -> Result<Self,DataError> {
        if timeout < Duration::from_secs(1) || timeout > Duration::from_secs(60) { return Err(invalid("BaoStock timeout must be 1..60 seconds")); }
        let addresses = HOST.to_socket_addrs()?;
        let mut connection = None;
        for address in addresses {
            if let Ok(stream) = TcpStream::connect_timeout(&address,timeout) { connection = Some(stream); break; }
        }
        let stream = connection.ok_or_else(||unavailable("BaoStock 服务器连接失败"))?;
        stream.set_read_timeout(Some(timeout))?;
        stream.set_write_timeout(Some(timeout))?;
        let mut client = Self{stream};
        // 原服务公共匿名协议参数，不读取或持久化账户密码。
        let login = ["login","anonymous","123456","0"].join("\u{1}");
        client.request("00",&login,"01")?;
        Ok(client)
    }
    fn request(&mut self,kind: &str,body: &str,expected_kind: &str) -> Result<Vec<String>,DataError> {
        let head_body = format!("00.9.30{SPLIT}{kind}{SPLIT}{:010}{body}",body.len());
        let crc = crc32fast::hash(head_body.as_bytes());
        self.stream.write_all(format!("{head_body}{SPLIT}{crc}\n").as_bytes())?;
        let mut bytes = Vec::new();
        let mut buffer = [0;8192];
        while !bytes.ends_with(END) {
            let count = self.stream.read(&mut buffer)?;
            if count == 0 { return Err(unavailable("BaoStock 连接在完整响应前关闭")); }
            bytes.extend_from_slice(&buffer[..count]);
            if bytes.len() > MAX_BYTES { return Err(invalid("BaoStock response exceeds size limit")); }
        }
        if bytes.len() < 21 + END.len() { return Err(invalid("BaoStock response header is truncated")); }
        let header = std::str::from_utf8(&bytes[..21]).map_err(|_|invalid("BaoStock invalid header"))?;
        let parts: Vec<_> = header.split(SPLIT).collect();
        if parts.len() != 3 || parts[1] != expected_kind { return Err(invalid("BaoStock response type mismatch")); }
        let length = parts[2].parse::<usize>().map_err(|_|invalid("BaoStock invalid body length"))?;
        if length > MAX_BYTES || 21+length > bytes.len()-END.len() { return Err(invalid("BaoStock truncated response body")); }
        let content = if parts[1] == "96" {
            let mut decoded = Vec::new();
            ZlibDecoder::new(&bytes[21..21+length]).take(MAX_BYTES as u64+1).read_to_end(&mut decoded)?;
            if decoded.len() > MAX_BYTES { return Err(invalid("BaoStock decompressed response exceeds limit")); }
            decoded
        } else { bytes[21..21+length].to_vec() };
        let text = String::from_utf8(content).map_err(|_|invalid("BaoStock response is not UTF-8"))?;
        let fields: Vec<String> = text.trim_end_matches(['\n','\r']).split(SPLIT).map(str::to_owned).collect();
        if fields.first().map(String::as_str) != Some("0") { return Err(unavailable("BaoStock 拒绝请求或上游数据不可用")); }
        Ok(fields)
    }
    pub fn history(&mut self,symbol: &str,fields: &[&str],start: &str,end: &str,frequency: &str,adjustment: &str) -> Result<Vec<ProviderRow>,DataError> {
        if !is_stock(symbol) || parse_date(start)? > parse_date(end)? { return Err(invalid("invalid BaoStock symbol/date")); }
        if !matches!(frequency,"d" | "w" | "m" | "5" | "15" | "30" | "60") || !matches!(adjustment,"1" | "2" | "3") { return Err(invalid("invalid BaoStock frequency/adjustment")); }
        if fields.is_empty() || fields.len() > 40 || fields.iter().any(|f|f.is_empty() || !f.bytes().all(|b|b.is_ascii_alphanumeric() || b == b'_')) { return Err(invalid("invalid BaoStock fields")); }
        let wanted = fields.join(",");
        let mut result = Vec::new();
        for page in 1..=100 {
            let body = format!("query_history_k_data_plus{SPLIT}anonymous{SPLIT}{page}{SPLIT}{PAGE_SIZE}{SPLIT}{symbol}{SPLIT}{wanted}{SPLIT}{start}{SPLIT}{end}{SPLIT}{frequency}{SPLIT}{adjustment}");
            let response = self.request("95",&body,"96")?;
            if response.len() < 13 || response[2] != "query_history_k_data_plus" || response[4] != page.to_string() || response[7] != symbol || response[11] != frequency || response[12] != adjustment { return Err(invalid("BaoStock response scope mismatch")); }
            let received: Vec<_> = response[8].split(',').map(str::trim).collect();
            if received != fields { return Err(invalid("BaoStock response fields mismatch")); }
            let rows = if response[6].trim().is_empty() { Vec::new() } else {
                let document: Value = crate::indexed_json::from_str(&response[6]).map_err(|_|invalid("BaoStock invalid records JSON"))?;
                document["record"].as_array().ok_or_else(||invalid("BaoStock records missing"))?.clone()
            };
            let count = rows.len();
            if count > PAGE_SIZE { return Err(invalid("BaoStock page exceeds requested count")); }
            for row in rows {
                let values = row.as_array().ok_or_else(||invalid("BaoStock row is not an array"))?;
                if values.len() != fields.len() { return Err(invalid("BaoStock row width mismatch")); }
                let mut record = BTreeMap::new();
                for (field,value) in fields.iter().zip(values) {
                    let value = match (value).kind() { Some(crate::indexed_json::Kind::String(s)) => s.clone(), Some(crate::indexed_json::Kind::Number(n)) => n.to_string(), None => String::new(), _ => return Err(invalid("BaoStock invalid field value")) };
                    record.insert((*field).to_owned(),value);
                }
                result.push(record);
            }
            if count < PAGE_SIZE { return Ok(result); }
        }
        Err(invalid("BaoStock history exceeds bounded pagination"))
    }
    pub fn minute_day(&mut self,daily: &ExecutionBar) -> Result<Vec<MinuteBar>,MinuteError> {
        if daily.timestamp.as_str() < "2020-01-01" { return Err(MinuteError::Invalid("2020-01-01 之前无可用五分钟历史行情".into())); }
        let rows = self.history(&daily.symbol,&["date","time","code","open","high","low","close","volume","amount","adjustflag"],&daily.timestamp,&daily.timestamp,"5","3")?;
        let mut result = Vec::new();
        for row in rows {
            let get = |key: &str| row.get(key).map(String::as_str).ok_or_else(||invalid("BaoStock minute field missing"));
            let stamp = get("time")?;
            if get("date")? != daily.timestamp || get("code")? != daily.symbol || get("adjustflag")? != "3" || stamp.len() != 17 || stamp.get(14..) != Some("000") { return Err(invalid("BaoStock minute source/date mismatch").into()); }
            let time = chrono::NaiveDateTime::parse_from_str(stamp.get(..14).ok_or_else(||invalid("invalid minute time"))?,"%Y%m%d%H%M%S").map_err(|_|invalid("invalid minute time"))?;
            let number = |key| get(key)?.parse::<f64>().map_err(|_|invalid("invalid minute numeric value"));
            result.push(MinuteBar{timestamp:format!("{}+08:00",time.format("%Y-%m-%dT%H:%M:%S")),open:number("open")?,high:number("high")?,low:number("low")?,close:number("close")?,volume:number("volume")?});
        }
        verify_minute_day(&result,daily)?;
        Ok(result)
    }
    pub fn logout(mut self) -> Result<(),DataError> {
        let now = chrono::Utc::now().format("%Y%m%d%H%M%S").to_string();
        self.request("02",&format!("logout{SPLIT}anonymous{SPLIT}{now}"),"03")?;
        Ok(())
    }
}
