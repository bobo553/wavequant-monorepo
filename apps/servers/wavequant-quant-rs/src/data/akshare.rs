//! 默认在线数据源：通过有界子进程调用真实 AKShare Python SDK，领域规则仍在 Rust。
use std::{collections::{BTreeMap,BTreeSet,VecDeque},io::{BufRead,BufReader,Read,Write},path::{Path,PathBuf},process::{Child,ChildStdin,ChildStdout,Command,Stdio},sync::{Arc,Mutex,TryLockError,atomic::{AtomicUsize,Ordering},mpsc},time::{Duration,Instant}};
use chrono::{DateTime,NaiveDateTime,Utc};
use crate::indexed_json::{Value,json};
use sha2::{Digest,Sha256};
use super::{catalog::{latest_completed_session,weekday_fallback_session},sina::{Factor,FactorData},tdx::{DailyBar,MarketData,DataError,is_stock,parse_date},minute::MinuteBar};

const WORKER:&str=include_str!(concat!(env!("CARGO_MANIFEST_DIR"),"/scripts/rust_sdk_worker.py"));
const MAX_BYTES:usize=20*1024*1024;
static ACTIVE:AtomicUsize=AtomicUsize::new(0);
// 所有provider实例复用同一个数据进程；每条请求独占协议管道，不混合响应。
static SDK:Mutex<Option<SdkProcess>>=Mutex::new(None);
// 仅缓存日线和复权因子。绝对TTL不随命中续期，跨中国交易日强制重新读取。
const SOURCE_CACHE_BYTES:usize=64*1024*1024;
const SOURCE_ITEM_BYTES:usize=8*1024*1024;
static SOURCE_CACHE:Mutex<VecDeque<SourceReply>>=Mutex::new(VecDeque::new());
#[derive(Clone)]
struct SourceReply{key:String,data:Value,source_version:String,sdk_version:String,created:Instant,bytes:usize}
fn cached_reply(key:&str)->Option<SourceReply>{
    let mut cache=SOURCE_CACHE.lock().ok()?;
    cache.retain(|entry|entry.created.elapsed()<Duration::from_secs(60));
    let index=cache.iter().position(|entry|entry.key==key)?;
    let entry=cache.remove(index)?;cache.push_back(entry.clone());Some(entry)
}
fn cache_reply(entry:SourceReply){
    if entry.bytes>SOURCE_ITEM_BYTES{return;}
    if let Ok(mut cache)=SOURCE_CACHE.lock(){
        cache.retain(|old|old.key!=entry.key&&old.created.elapsed()<Duration::from_secs(60));
        let mut size=cache.iter().map(|old|old.bytes).sum::<usize>();
        while !cache.is_empty()&&(cache.len()>=128||size+entry.bytes>SOURCE_CACHE_BYTES){if let Some(old)=cache.pop_front(){size=size.saturating_sub(old.bytes);}}
        cache.push_back(entry);
    }
}
const SDK_LOOP:&str=r#"import io
import sys
scope = {"__name__": "wavequant_sdk_resident"}
exec(sys.argv[1], scope)
original_input = sys.stdin
while True:
    packet = original_input.buffer.readline(4098)
    if not packet:
        break
    if len(packet) > 4097 or not packet.endswith(b"\n"):
        break
    sys.stdin = io.TextIOWrapper(io.BytesIO(packet[:-1]), encoding="utf-8")
    try:
        scope["main"]()
    finally:
        sys.stdin = original_input
    sys.stdout.buffer.write(b"\n")
    sys.stdout.buffer.flush()
"#;
fn invalid(message:&str)->DataError{DataError::Invalid(message.into())}
fn unavailable(message:&str)->DataError{DataError::Unavailable(message.into())}
fn digest(bytes:impl AsRef<[u8]>)->String{format!("{:x}",Sha256::digest(bytes.as_ref()))}
struct Permit;
impl Permit{fn acquire()->Result<Self,DataError>{ACTIVE.fetch_update(Ordering::AcqRel,Ordering::Acquire,|n|(n<4).then_some(n+1)).map_err(|_|unavailable("AKShare 数据进程容量已满，请稍后重试"))?;Ok(Self)}}
impl Drop for Permit{fn drop(&mut self){ACTIVE.fetch_sub(1,Ordering::AcqRel);}}
struct Process(Child);
impl Drop for Process{fn drop(&mut self){let _=self.0.kill();let _=self.0.wait();}}
struct SdkProcess{python:PathBuf,child:Process,input:ChildStdin,output:Option<BufReader<ChildStdout>>}
impl SdkProcess{
    fn start(python:&Path)->Result<Self,DataError>{
        let mut command=Command::new(python);
        command.args(["-I","-X","utf8","-u","-c",SDK_LOOP,WORKER]).stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null());
        #[cfg(windows)]{use std::os::windows::process::CommandExt;command.creation_flags(0x08000000);}
        let mut child=Process(command.spawn().map_err(|_|unavailable("无法启动 AKShare 常驻数据进程"))?);
        let input=child.0.stdin.take().ok_or_else(||unavailable("AKShare 请求管道不可用"))?;
        let output=child.0.stdout.take().ok_or_else(||unavailable("AKShare 响应管道不可用"))?;
        Ok(Self{python:python.to_path_buf(),child,input,output:Some(BufReader::new(output))})
    }
}
fn read_reply(output:&mut impl BufRead,limit:usize)->std::io::Result<Vec<u8>>{
    let mut bytes=Vec::new();
    output.take((limit+1) as u64).read_until(b'\n',&mut bytes)?;
    if bytes.len()>limit||bytes.last()!=Some(&b'\n'){
        return Err(std::io::Error::new(std::io::ErrorKind::InvalidData,"SDK response exceeds limit or lacks a frame terminator"));
    }
    Ok(bytes)
}

fn interpreter()->Result<PathBuf,DataError>{
    if let Some(path)=std::env::var_os("WAVEQUANT_AKSHARE_PYTHON"){
        let path=PathBuf::from(path);if !path.is_absolute()||!path.is_file(){return Err(unavailable("WAVEQUANT_AKSHARE_PYTHON 必须指向已安装 AKShare 的 Python 可执行文件绝对路径"));}return Ok(path);
    }
    let relative=if cfg!(windows){"packages/wavequant-core/.venv/Scripts/python.exe"}else{"packages/wavequant-core/.venv/bin/python"};
    let current=std::env::current_dir()?;
    for root in current.ancestors().chain(Path::new(env!("CARGO_MANIFEST_DIR")).ancestors()) {let path=root.join(relative);if path.is_file(){return Ok(path);}}
    Err(unavailable("未找到 AKShare 隔离环境，请设置 WAVEQUANT_AKSHARE_PYTHON"))
}
fn number(row:&Value,key:&str)->Result<f64,DataError>{row[key].as_f64().or_else(||row[key].as_str().and_then(|s|s.parse().ok())).filter(|n|n.is_finite()).ok_or_else(||invalid("AKShare 返回非有限或缺失的数值字段"))}
fn day(value:&Value)->Result<String,DataError>{let text=value.as_str().ok_or_else(||invalid("AKShare 日期格式无效"))?;let day=text.split(['T',' ']).next().unwrap_or("");parse_date(day)?;Ok(day.into())}
fn rows(value:&Value,limit:usize)->Result<&Vec<Value>,DataError>{value.as_array().filter(|rows|rows.len()<=limit).ok_or_else(||invalid("AKShare 返回记录格式或数量异常"))}
fn validate_symbol(symbol:&str)->Result<(),DataError>{if !is_stock(symbol)||symbol.starts_with("bj."){return Err(invalid("AKShare 新浪历史接口仅支持沪深 A 股"));}Ok(())}

#[derive(Clone)]
pub struct AkShareProvider{python:PathBuf,timeout:Duration,version:Arc<Mutex<Option<String>>>,timings:Arc<Mutex<Vec<SdkCallTiming>>>}
/// 只计量Rust边界；roundtrip包含SDK内部网络和Python处理，不能当作纯网络耗时。
#[derive(Clone,Debug,serde::Serialize)]
pub struct SdkCallTiming{pub operation:String,pub cache_hit:bool,pub queue_ms:f64,pub process_spawn_ms:f64,pub process_started:bool,pub roundtrip_ms:f64,pub decode_ms:f64,pub fingerprint_ms:f64,pub total_ms:f64,pub response_bytes:usize}
impl AkShareProvider{
    pub fn new(timeout:Duration)->Result<Self,DataError>{if timeout<Duration::from_secs(1)||timeout>Duration::from_secs(60){return Err(invalid("AKShare timeout must be 1..60 seconds"));}Ok(Self{python:interpreter()?,timeout,version:Arc::new(Mutex::new(None)),timings:Arc::new(Mutex::new(Vec::new()))})}
    pub fn request_timings(&self)->Result<Vec<SdkCallTiming>,DataError>{self.timings.lock().map(|v|v.clone()).map_err(|_|unavailable("AKShare计时状态不可用"))}
    pub fn version(&self)->Result<String,DataError>{self.version.lock().map_err(|_|unavailable("AKShare 版本状态不可用"))?.clone().ok_or_else(||unavailable("AKShare SDK 尚未返回版本"))}
    fn reuse_reply(&self,operation:&str,entry:SourceReply,started:Instant,queue_ms:f64)->Result<(Value,String),DataError>{
        self.accept_version(&entry.sdk_version)?;
        if let Ok(mut timings)=self.timings.lock(){if timings.len()>=8{timings.remove(0);}timings.push(SdkCallTiming{operation:operation.into(),cache_hit:true,queue_ms,process_spawn_ms:0.0,process_started:false,roundtrip_ms:0.0,decode_ms:0.0,fingerprint_ms:0.0,total_ms:started.elapsed().as_secs_f64()*1000.0,response_bytes:0});}
        Ok((entry.data,entry.source_version))
    }
    fn accept_version(&self,version:&str)->Result<(),DataError>{
        let mut current=self.version.lock().map_err(|_|unavailable("AKShare 版本状态不可用"))?;
        if current.as_ref().is_some_and(|old|old!=version){return Err(unavailable("AKShare SDK 在请求期间发生变更，请重启服务"));}
        *current=Some(version.into());Ok(())
    }
    fn call(&self,operation:&str,arguments:Value)->Result<(Value,String),DataError>{
        let started=Instant::now();
        let mut request=arguments;request["protocol"]=json!(1);request["operation"]=json!(operation);
        let identity=digest(crate::indexed_json::to_vec(&request).map_err(|_|invalid("SDK request encoding failed"))?);request["request_id"]=json!(identity);
        let cache_key=matches!(operation,"daily"|"factors").then(||digest(format!("{}:{}:{}",self.python.display(),(Utc::now()+chrono::Duration::hours(8)).date_naive(),identity)));
        if let Some(entry)=cache_key.as_deref().and_then(cached_reply){return self.reuse_reply(operation,entry,started,0.0);}
        let _permit=Permit::acquire()?;
        let request=crate::indexed_json::to_vec(&request).map_err(|_|invalid("SDK request encoding failed"))?;
        if request.len()>4096{return Err(invalid("SDK request exceeds limit"));}
        let queue_started=Instant::now();
        let mut slot=loop{
            match SDK.try_lock(){
                Ok(slot)=>break slot,
                Err(TryLockError::Poisoned(_))=>return Err(unavailable("AKShare 常驻进程状态不可用")),
                Err(TryLockError::WouldBlock)=>{
                    if started.elapsed()>=self.timeout{return Err(unavailable("等待 AKShare 常驻进程超时"));}
                    std::thread::sleep(Duration::from_millis(10));
                }
            }
        };
        let queue_ms=queue_started.elapsed().as_secs_f64()*1000.0;
        // 等待管道的相同请求复用刚完成的响应，不再发起第二次SDK调用。
        if let Some(entry)=cache_key.as_deref().and_then(cached_reply){return self.reuse_reply(operation,entry,started,queue_ms);}
        if let Some(process)=slot.as_mut(){
            if process.python!=self.python||process.child.0.try_wait().map_err(|_|unavailable("AKShare 进程状态不可用"))?.is_some(){*slot=None;}
        }
        let process_started=slot.is_none();let spawn_started=Instant::now();
        let mut process=match slot.take(){Some(process)=>process,None=>SdkProcess::start(&self.python)?};
        let process_spawn_ms=spawn_started.elapsed().as_secs_f64()*1000.0;let roundtrip_started=Instant::now();
        process.input.write_all(&request).and_then(|_|process.input.write_all(b"\n")).and_then(|_|process.input.flush())
            .map_err(|_|unavailable("AKShare 请求发送失败，数据进程已回收"))?;
        let mut output=process.output.take().ok_or_else(||unavailable("AKShare 响应管道不可用"))?;
        let(sender,receiver)=mpsc::sync_channel(1);
        let reader=std::thread::spawn(move||{
            let result=read_reply(&mut output,MAX_BYTES);
            let _=sender.send((result,output));
        });
        let received=receiver.recv_timeout(self.timeout.saturating_sub(started.elapsed()));
        if received.is_err(){let _=process.child.0.kill();let _=process.child.0.wait();let _=reader.join();return Err(unavailable("AKShare SDK 调用超时或进程中断，数据进程已回收"));}
        let _=reader.join();
        let(result,output)=received.map_err(|_|unavailable("AKShare 数据进程中断"))?;
        process.output=Some(output);
        let bytes=result.map_err(|_|unavailable("AKShare 响应读取失败"))?;
        if bytes.len()>MAX_BYTES||bytes.last()!=Some(&b'\n'){return Err(unavailable("AKShare 响应超限或协议中断，数据进程已回收"));}
        let roundtrip_ms=roundtrip_started.elapsed().as_secs_f64()*1000.0;let decode_started=Instant::now();
        let response:Value=crate::indexed_json::from_slice(&bytes).map_err(|_|unavailable("AKShare 数据进程返回无效 JSON"))?;
        if response["protocol"]!=1||response["request_id"]!=identity{return Err(unavailable("AKShare 响应身份不匹配"));}
        if response["ok"]!=true{*slot=Some(process);return Err(unavailable(match response["error"].as_str(){Some("sdk_missing")=>"指定 Python 环境未安装完整 AKShare SDK 依赖",Some("response_too_large")=>"AKShare 响应超过大小上限",_=>"AKShare SDK 上游请求失败，请稍后重试"}));}
        let version=response["sdk_version"].as_str().filter(|v|!v.is_empty()&&v.len()<=64).ok_or_else(||unavailable("AKShare SDK 版本缺失"))?.to_owned();
        self.accept_version(&version)?;
        let data=response.get("data").ok_or_else(||unavailable("AKShare 数据字段缺失"))?.clone();
        let decode_ms=decode_started.elapsed().as_secs_f64()*1000.0;let fingerprint_started=Instant::now();
        // 协议帧已经在内存中，直接散列原始字节，避免再次遍历并编码整份行情JSON。
        let source_version=digest(format!("akshare-python-sdk-resident-v3:{}:{}:{}:{}:{}:{}",digest(WORKER),digest(SDK_LOOP),version,operation,identity,digest(&bytes)));
        let fingerprint_ms=fingerprint_started.elapsed().as_secs_f64()*1000.0;
        if let Some(key)=cache_key{cache_reply(SourceReply{key,data:data.clone(),source_version:source_version.clone(),sdk_version:version,created:started,bytes:bytes.len().saturating_mul(4)});}
        *slot=Some(process);
        // 每个provider最多保留最近8次调用，在线目录等长生命周期实例不能无限积累。
        if let Ok(mut timings)=self.timings.lock(){if timings.len()>=8{timings.remove(0);}timings.push(SdkCallTiming{operation:operation.into(),cache_hit:false,queue_ms,process_spawn_ms,process_started,roundtrip_ms,decode_ms,fingerprint_ms,total_ms:started.elapsed().as_secs_f64()*1000.0,response_bytes:bytes.len()});}
        Ok((data,source_version))
    }
    pub fn daily(&self,symbol:&str,asof:&str)->Result<MarketData,DataError>{
        validate_symbol(symbol)?;let cutoff=parse_date(asof)?;
        let today=(Utc::now()+chrono::Duration::hours(8)).date_naive();
        if cutoff>today{return Err(invalid("AKShare 回放日期不能晚于今天"));}
        // 新浪SDK内部先读取完整历史；预览和分析共用当天快照，再隔离各自的历史前缀。
        let(data,_)=self.call("daily",json!({"symbol":symbol,"asof":today.to_string()}))?;
        let mut market=Self::market_data(symbol,&today.to_string(),data,String::new())?;
        market.bars.retain(|bar|bar.date.as_str()<=asof);
        if market.bars.is_empty(){return Err(unavailable("该回放日期之前没有 AKShare 日线"));}
        market.asof=asof.into();
        // 历史身份仅包含可见前缀，未来K线变化不能改变过去的行情身份。
        let prefix=serde_json::to_vec(&market.bars).map_err(|_|invalid("SDK daily prefix encoding failed"))?;
        market.version=digest(format!("akshare-daily-prefix-v1:{}:{}:{}:{}:{}:{}",digest(WORKER),digest(SDK_LOOP),self.version()?,symbol,asof,digest(prefix)));
        Ok(market)
    }
    pub fn eastmoney_daily(&self,symbol:&str,asof:&str)->Result<MarketData,DataError>{
        if !is_stock(symbol){return Err(invalid("invalid AKShare symbol"));}parse_date(asof)?;
        let(data,version)=self.call("eastmoney_daily",json!({"symbol":symbol,"asof":asof}))?;Self::market_data(symbol,asof,data,version)
    }
    pub fn tencent_daily(&self,symbol:&str,start:&str,asof:&str)->Result<MarketData,DataError>{
        if !is_stock(symbol){return Err(invalid("invalid AKShare symbol"));}parse_date(start)?;parse_date(asof)?;if start>asof{return Err(invalid("start exceeds as-of date"));}
        let(data,version)=self.call("tencent_daily",json!({"symbol":symbol,"asof":asof,"start":start}))?;Self::market_data(symbol,asof,data,version)
    }
    fn market_data(symbol:&str,asof:&str,data:Value,version:String)->Result<MarketData,DataError>{
        let mut bars=Vec::new();let mut seen=BTreeSet::new();
        for row in rows(&data,20000)?{let date=day(&row["date"])?;if date.as_str()>asof||!seen.insert(date.clone()){return Err(invalid("AKShare 日线包含未来或重复日期"));}
            let(open,high,low,close,volume)=(number(row,"open")?,number(row,"high")?,number(row,"low")?,number(row,"close")?,number(row,"volume")?);
            if low<=0.0||low>open.min(close)||high<open.max(close)||volume<0.0||volume.fract()!=0.0||volume>=u64::MAX as f64{return Err(invalid("AKShare 日线价格或成交量无效"));}
            let amount=number(row,"amount")?;if amount<0.0{return Err(invalid("AKShare 日线成交额无效"));}
            bars.push(DailyBar{date,open,high,low,close,volume:volume as u64,amount});
        }
        bars.sort_by(|a,b|a.date.cmp(&b.date));if bars.is_empty(){return Err(unavailable("该回放日期之前没有 AKShare 日线"));}Ok(MarketData{symbol:symbol.into(),asof:asof.into(),version,bars})
    }
    pub fn factors(&self,symbol:&str)->Result<FactorData,DataError>{
        validate_symbol(symbol)?;let(data,version)=self.call("factors",json!({"symbol":symbol}))?;let mut factors=Vec::new();let mut seen=BTreeSet::new();
        for row in rows(&data,100000)?{let date=day(&row["date"])?;let value=number(row,"hfq_factor")?;if value<=0.0||!seen.insert(date.clone()){return Err(invalid("AKShare 复权因子无效或重复"));}factors.push(Factor{date,value});}
        factors.sort_by(|a,b|a.date.cmp(&b.date));if factors.is_empty(){return Err(unavailable("AKShare 复权因子缺失"));}Ok(FactorData{factors,version})
    }
    pub fn minute_history(&self,symbol:&str)->Result<(BTreeMap<String,Vec<MinuteBar>>,String),DataError>{
        validate_symbol(symbol)?;let(data,version)=self.call("minutes",json!({"symbol":symbol}))?;let mut grouped=BTreeMap::<String,Vec<MinuteBar>>::new();let mut seen=BTreeSet::new();
        for row in rows(&data,20000)?{let stamp=row["day"].as_str().ok_or_else(||invalid("AKShare 分钟时间缺失"))?;
            let parsed=NaiveDateTime::parse_from_str(stamp,"%Y-%m-%d %H:%M:%S").or_else(|_|NaiveDateTime::parse_from_str(stamp,"%Y-%m-%dT%H:%M:%S%.f")).map_err(|_|invalid("AKShare 分钟时间无效"))?;
            if !seen.insert(parsed){return Err(invalid("AKShare 分钟时间重复"));}
            let(open,high,low,close,volume)=(number(row,"open")?,number(row,"high")?,number(row,"low")?,number(row,"close")?,number(row,"volume")?);
            if low<=0.0||low>open.min(close)||high<open.max(close)||volume<0.0{return Err(invalid("AKShare 分钟价格或成交量无效"));}
            grouped.entry(parsed.date().to_string()).or_default().push(MinuteBar{timestamp:format!("{}+08:00",parsed.format("%Y-%m-%dT%H:%M:%S")),open,high,low,close,volume});
        }
        for bars in grouped.values_mut(){bars.sort_by(|a,b|a.timestamp.cmp(&b.timestamp));}Ok((grouped,version))
    }
    pub fn calendar(&self)->Result<Vec<String>,DataError>{let(data,_)=self.call("calendar",json!({}))?;rows(&data,100000)?.iter().map(|row|day(&row["trade_date"])).collect()}
    pub fn catalog(&self,now:DateTime<Utc>)->Result<Value,DataError>{
        let(data,version)=self.call("catalog",json!({}))?;let mut warnings=Vec::new();
        let calendar=rows(&data["calendar"],100000).and_then(|records|records.iter().map(|row|day(&row["trade_date"])).collect::<Result<Vec<_>,_>>());
        let latest=match calendar.and_then(|sessions|latest_completed_session(&sessions,now)){Ok(day)=>day,Err(_)=>{warnings.push("AKShare 交易日历暂不可用，最新日期按中国市场工作日保守估算。");weekday_fallback_session(now)?}};
        let mut stocks=BTreeMap::new();for row in rows(&data["stocks"],20000)?{let code=row["code"].as_str().ok_or_else(||invalid("AKShare 股票代码缺失"))?;
            let symbol=["sh","sz","bj"].iter().map(|market|format!("{market}.{code}")).find(|symbol|is_stock(symbol));let Some(symbol)=symbol else{continue};
            let name=row["name"].as_str().map(str::trim).filter(|name|!name.is_empty()&&name.chars().count()<=80).ok_or_else(||invalid("AKShare 股票名称无效"))?;
            if stocks.insert(symbol.clone(),json!({"symbol":symbol,"name":name,"source":"akshare","catalog_source":"akshare","data_source":"akshare","has_data":true,"status":"available_on_demand","first":null,"last":latest,"bar_count":null})).is_some(){return Err(invalid("AKShare 股票目录代码重复"));}
        }
        if stocks.is_empty(){return Err(unavailable("AKShare 股票目录为空"));}
        let sdk=self.version()?;Ok(json!({"with_daily":stocks.len(),"stocks":stocks.into_values().collect::<Vec<_>>(),"source":"akshare","default_source":"akshare","providers":["akshare"],"provider_version":format!("akshare-python/{sdk}"),"sdk_version":sdk,"transport":"python_sdk_process","upstream":"SSE + SZSE + BSE","data_version":digest(format!("{version}:{latest}")),"available":true,"latest":latest,"scope":"online_SH_SZ_BJ_A_shares","warnings":warnings,"notice":"通过 AKShare Python SDK 读取沪深北 A 股目录。"}))
    }
    pub fn limit_up(&self,date:&str)->Result<Value,DataError>{let(data,version)=self.call("limit_up",json!({"date":date}))?;rows(&data,10000)?;Ok(json!({"rows":data,"sdk_version":self.version()?,"data_version":version}))}
}

#[cfg(test)]
mod resident_protocol_tests{
    use super::*;
    #[test]
    fn consecutive_replies_keep_the_next_frame_in_the_same_reader(){
        let mut reader=BufReader::new(std::io::Cursor::new(b"{\"request_id\":\"first\"}\n{\"request_id\":\"second\"}\n"));
        let first:Value=crate::indexed_json::from_slice(&read_reply(&mut reader,64).unwrap()).unwrap();
        let second:Value=crate::indexed_json::from_slice(&read_reply(&mut reader,64).unwrap()).unwrap();
        assert_eq!(first["request_id"],"first");assert_eq!(second["request_id"],"second");
        assert!(read_reply(&mut reader,64).is_err());
    }
    #[test]
    fn oversized_and_truncated_responses_cannot_be_reused(){
        assert!(read_reply(&mut std::io::Cursor::new(b"0123456789\n"),8).is_err());
        assert!(read_reply(&mut std::io::Cursor::new(b"{}"),8).is_err());
    }
}
