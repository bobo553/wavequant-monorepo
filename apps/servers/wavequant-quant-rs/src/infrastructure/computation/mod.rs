//! 固定子进程、有限字节队列及请求身份核对；所有业务计算调用既有统一入口。
use std::{io::{self,Read,Write,BufReader},path::{Path,PathBuf},process::{Child,Command,Stdio},sync::{Arc,Mutex,mpsc::{self,Receiver,SyncSender},atomic::{AtomicBool,AtomicUsize,Ordering}},thread::{self,JoinHandle},time::{Duration,Instant}};
use serde::{Serialize,Deserialize};
use crate::indexed_json::{Value,json};
use flate2::{read::ZlibDecoder,write::ZlibEncoder,Compression};
use crate::analysis::{analyzer::{self,AnalysisRequest},system::{self,SystemConfig}};
use crate::execution::BacktestSession;
use crate::persistence::{sha256,canonical};
pub mod process_memory;
pub const PAYLOAD_BYTES:usize=8*1024*1024;
pub const QUEUE_BYTES:usize=24*1024*1024;
const EXPANDED_BYTES:usize=64*1024*1024;
const SCHEMA:u32=4;
pub type WorkerResult<T>=Result<T,String>;
#[derive(Debug,Clone,Serialize)]
#[serde(tag="kind",content="request",rename_all="snake_case")]
pub enum WorkerRequest{
    Analyze(AnalysisRequest),
    System{sessions:Vec<BacktestSession>,config:SystemConfig,#[serde(default)]structure_cache_root:Option<PathBuf>},
    SystemWithSteps{sessions:Vec<BacktestSession>,config:SystemConfig,#[serde(default)]structure_cache_root:Option<PathBuf>},
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct SystemInput{sessions:Vec<BacktestSession>,config:SystemConfig,#[serde(default)]structure_cache_root:Option<PathBuf>}
impl<'de> Deserialize<'de> for WorkerRequest{
    fn deserialize<D:serde::Deserializer<'de>>(d:D)->Result<Self,D::Error>{
        let raw=Value::deserialize(d)?;
        let parsed=(||->crate::indexed_json::Result<Self>{
            if !raw.as_object().is_some_and(|fields|fields.len()==2&&fields.contains_key("kind")&&fields.contains_key("request")){return Err(crate::indexed_json::error("invalid worker request fields"));}
            let payload=raw["request"].clone();
            match raw["kind"].as_str(){
                Some("analyze")=>Ok(Self::Analyze(crate::indexed_json::from_value(payload)?)),
                Some(kind @ ("system"|"system_with_steps"))=>{
                    let input:SystemInput=crate::indexed_json::from_value(payload)?;
                    Ok(if kind=="system"{Self::System{sessions:input.sessions,config:input.config,structure_cache_root:input.structure_cache_root}}
                        else{Self::SystemWithSteps{sessions:input.sessions,config:input.config,structure_cache_root:input.structure_cache_root}})
                },
                _=>Err(crate::indexed_json::error("unknown worker request kind")),
            }
        })();parsed.map_err(serde::de::Error::custom)
    }
}
impl WorkerRequest{/// 周期是输入身份；0～3级均共享此周期。账户执行入口目前明确为日线。
    pub fn timeframe(&self)->&str{match self{Self::Analyze(request)=>&request.identity.timeframe,Self::System{..}|Self::SystemWithSteps{..}=>"1d"}}
}
#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct WorkerOutcome{pub value:Value,pub timeframe:String,#[serde(default)]pub structure_cache:Option<Value>,pub request_sha256:String,pub engine_sha256:String,pub pid:u32,pub peak_rss_bytes:Option<u64>,pub elapsed_seconds:f64,pub skipped_progress:usize}
#[derive(Debug,Clone,Serialize)]
#[serde(tag="kind",rename_all="snake_case")]
enum Reply{Progress{request_sha256:String,value:Value},Step{request_sha256:String,value:Value},Completed{outcome:WorkerOutcome},Failed{request_sha256:String,error:String}}
impl<'de> Deserialize<'de> for Reply{
    fn deserialize<D:serde::Deserializer<'de>>(d:D)->Result<Self,D::Error>{
        let raw=Value::deserialize(d)?;
        let parsed=(||->crate::indexed_json::Result<Self>{
            match raw["kind"].as_str(){
                Some("completed")=>Ok(Self::Completed{outcome:crate::indexed_json::from_value(raw["outcome"].clone())?}),
                Some("progress")=>Ok(Self::Progress{request_sha256:crate::indexed_json::from_value(raw["request_sha256"].clone())?,value:raw["value"].clone()}),
                Some("step")=>Ok(Self::Step{request_sha256:crate::indexed_json::from_value(raw["request_sha256"].clone())?,value:raw["value"].clone()}),
                Some("failed")=>Ok(Self::Failed{request_sha256:crate::indexed_json::from_value(raw["request_sha256"].clone())?,error:crate::indexed_json::from_value(raw["error"].clone())?}),
                _=>Err(crate::indexed_json::error("unknown worker reply kind")),
            }
        })();parsed.map_err(serde::de::Error::custom)
    }
}
#[derive(Serialize,Deserialize)]
#[serde(deny_unknown_fields)]
struct Envelope{schema:u32,request_sha256:String,engine_sha256:String,timeframe:String,request:Value}
pub fn engine_identity()->String{include_str!(concat!(env!("OUT_DIR"),"/quant-engine.sha256")).trim().to_owned()}
struct Limited{bytes:Vec<u8>,limit:usize}
impl Write for Limited{fn write(&mut self,bytes:&[u8])->io::Result<usize>{if self.bytes.len().saturating_add(bytes.len())>self.limit{return Err(io::Error::new(io::ErrorKind::InvalidData,"worker payload budget exceeded"));}self.bytes.extend_from_slice(bytes);Ok(bytes.len())}fn flush(&mut self)->io::Result<()>{Ok(())}}
// 管道已有压缩和字节上限；直接序列化避免为一次传输再构建去重节点表。
fn encode(value:&impl Serialize,limit:usize)->WorkerResult<Vec<u8>>{let mut output=Limited{bytes:Vec::new(),limit};crate::indexed_json::to_writer(&mut output,value).map_err(|e|e.to_string())?;Ok(output.bytes)}
fn frame(writer:&mut impl Write,raw:&[u8])->WorkerResult<()>{let mut compressor=ZlibEncoder::new(Limited{bytes:Vec::new(),limit:PAYLOAD_BYTES},Compression::fast());compressor.write_all(raw).map_err(|e|e.to_string())?;let packed=compressor.finish().map_err(|e|e.to_string())?.bytes;if packed.len()>PAYLOAD_BYTES{return Err("worker compressed payload budget exceeded".into());}writer.write_all(&(packed.len()as u32).to_le_bytes()).and_then(|_|writer.write_all(&packed)).and_then(|_|writer.flush()).map_err(|e|e.to_string())}
fn read_frame(reader:&mut impl Read)->WorkerResult<Option<Vec<u8>>>{let mut size=[0u8;4];match reader.read(&mut size[..1]){Ok(0)=>return Ok(None),Ok(_)=>{},Err(e)=>return Err(e.to_string())}reader.read_exact(&mut size[1..]).map_err(|e|e.to_string())?;let size=u32::from_le_bytes(size)as usize;if size==0||size>PAYLOAD_BYTES{return Err("invalid worker frame length".into());}let mut packed=vec![0;size];reader.read_exact(&mut packed).map_err(|e|e.to_string())?;let mut raw=Vec::new();ZlibDecoder::new(packed.as_slice()).take(EXPANDED_BYTES as u64+1).read_to_end(&mut raw).map_err(|e|e.to_string())?;if raw.len()>EXPANDED_BYTES{return Err("worker decompression budget exceeded".into());}Ok(Some(raw))}
/// CLI hidden internal-worker 调用；stdout 专用于帧，业务错误作为失败帧返回。
pub fn worker_main()->WorkerResult<()>{let mut input=BufReader::new(io::stdin().lock());let mut output=io::stdout().lock();let engine=engine_identity();while let Some(raw)=read_frame(&mut input)?{if raw.len()>PAYLOAD_BYTES{return Err("worker request budget exceeded".into());}let envelope:Envelope=crate::indexed_json::wire::from_slice(&raw).map_err(|e|e.to_string())?;let request_hash=sha256(canonical(&envelope.request).map_err(|e|e.to_string())?);let request:WorkerRequest=crate::indexed_json::from_value(envelope.request).map_err(|e|e.to_string())?;let mismatch=if envelope.schema!=SCHEMA{Some("worker protocol schema mismatch")}else if envelope.engine_sha256!=engine{Some("worker engine identity mismatch")}else if envelope.request_sha256!=request_hash{Some("worker request digest mismatch")}else if envelope.timeframe.trim().is_empty()||envelope.timeframe!=request.timeframe(){Some("worker timeframe mismatch")}else{None};
        if let Some(reason)=mismatch{
            eprintln!("quant worker certificate rejected: {reason}; pid={}",std::process::id());
            let failure=encode(&Reply::Failed{request_sha256:envelope.request_sha256,error:reason.into()},PAYLOAD_BYTES)?;
            frame(&mut output,&failure)?;
            continue;
        }let timeframe=envelope.timeframe;let started=Instant::now();let mut skipped=0;let mut structure_cache=None;
        let computed=match request{WorkerRequest::Analyze(request)=>analyzer::analyze(request).and_then(|v|crate::indexed_json::to_value(v).map_err(|e|e.to_string())),WorkerRequest::System{sessions,config,structure_cache_root}=>compute_system(&sessions,config,&mut output,&request_hash,false,structure_cache_root.as_deref()).map(|(v,count,cache)|{skipped=count;structure_cache=cache;v}),WorkerRequest::SystemWithSteps{sessions,config,structure_cache_root}=>compute_system(&sessions,config,&mut output,&request_hash,true,structure_cache_root.as_deref()).map(|(v,count,cache)|{skipped=count;structure_cache=cache;v})};
        let reply=match computed{Ok(value)=>Reply::Completed{outcome:WorkerOutcome{value,timeframe,structure_cache,request_sha256:request_hash.clone(),engine_sha256:engine.clone(),pid:std::process::id(),peak_rss_bytes:process_memory::peak_rss_bytes(std::process::id()),elapsed_seconds:started.elapsed().as_secs_f64(),skipped_progress:skipped}},Err(error)=>Reply::Failed{request_sha256:request_hash.clone(),error}};
        match encode(&reply,EXPANDED_BYTES).and_then(|bytes|frame(&mut output,&bytes)){Ok(())=>{},Err(error)=>{let failure=encode(&Reply::Failed{request_sha256:request_hash,error},PAYLOAD_BYTES)?;frame(&mut output,&failure)?;}}
    }Ok(())}
#[derive(Debug,Clone)]
pub struct WorkerConfig{pub executable:PathBuf,pub workers:usize,pub timeout:Duration}
impl WorkerConfig{pub fn new(executable:impl AsRef<Path>,workers:usize)->WorkerResult<Self>{if !(1..=8).contains(&workers){return Err("workers must be 1..8".into());}let executable=executable.as_ref().canonicalize().map_err(|e|e.to_string())?;if !executable.is_file(){return Err("worker executable must be a regular file".into());}Ok(Self{executable,workers,timeout:Duration::from_secs(1800)})}}
#[derive(Default)]
struct Statistics{submissions:usize,fallbacks:usize,completed:usize,crashed:usize,peak_queue_bytes:usize,peak_worker_rss_bytes:u64,worker_peaks:std::collections::BTreeMap<u32,u64>}
struct Shared{bytes:AtomicUsize,closed:AtomicBool,statistics:Mutex<Statistics>}
struct Charge{shared:Arc<Shared>,size:usize}
impl Drop for Charge{fn drop(&mut self){self.shared.bytes.fetch_sub(self.size,Ordering::AcqRel);}}
struct Job{raw:Arc<Vec<u8>>,reply:SyncSender<WorkerResult<Reply>>,cancelled:Arc<AtomicBool>,_charge:Arc<Charge>}
pub struct WorkerTicket{receiver:Receiver<WorkerResult<Reply>>,cancelled:Arc<AtomicBool>,_charge:Arc<Charge>,hash:String,engine:String,timeframe:String,consumed:bool}
impl WorkerTicket{
    pub fn cancel(&self){self.cancelled.store(true,Ordering::Release);}
    pub fn cancellation_token(&self)->Arc<AtomicBool>{self.cancelled.clone()}
    pub fn result(self,progress:impl FnMut(Value))->WorkerResult<WorkerOutcome>{self.result_with_cancellation(None,progress)}
    pub fn result_with_cancellation(self,external:Option<&AtomicBool>,progress:impl FnMut(Value))->WorkerResult<WorkerOutcome>{self.result_with_observations(external,progress,|_|{})}
    pub fn result_with_observations(mut self,external:Option<&AtomicBool>,mut progress:impl FnMut(Value),mut step:impl FnMut(Value))->WorkerResult<WorkerOutcome>{loop{if external.is_some_and(|flag|flag.load(Ordering::Acquire)){self.cancel();return Err("worker cancelled".into());}let reply=match self.receiver.recv_timeout(Duration::from_millis(100)){Ok(reply)=>reply?,Err(mpsc::RecvTimeoutError::Timeout)=>continue,Err(_)=>return Err("worker result channel closed".into())};match reply{Reply::Progress{request_sha256,value}=>{if request_sha256!=self.hash{return Err("worker progress identity mismatch".into());}progress(value);},Reply::Step{request_sha256,value}=>{if request_sha256!=self.hash{return Err("worker step identity mismatch".into());}step(value);},Reply::Failed{request_sha256,error}=>{self.consumed=true;if request_sha256!=self.hash{return Err("worker error identity mismatch".into());}return Err(error);},Reply::Completed{outcome}=>{self.consumed=true;if outcome.request_sha256!=self.hash||outcome.engine_sha256!=self.engine||outcome.timeframe!=self.timeframe{return Err("worker result identity mismatch".into());}return Ok(outcome);}}}}
}
impl Drop for WorkerTicket{fn drop(&mut self){if !self.consumed{self.cancel();}}}
struct Process{child:Child,input:SyncSender<Arc<Vec<u8>>>,frames:Receiver<WorkerResult<Reply>>,reader:Option<JoinHandle<()>>,writer:Option<JoinHandle<()>>}
impl Process{
    fn spawn(config:&WorkerConfig)->WorkerResult<Self>{let mut command=Command::new(&config.executable);command.arg("internal-worker").stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::inherit());#[cfg(target_os="windows")]{use std::os::windows::process::CommandExt;command.creation_flags(0x08000000);}let mut child=command.spawn().map_err(|e|e.to_string())?;let mut stdin=child.stdin.take().ok_or("worker stdin unavailable")?;let output=child.stdout.take().ok_or("worker stdout unavailable")?;let(tx,frames)=mpsc::sync_channel(1);let(input,requests)=mpsc::sync_channel::<Arc<Vec<u8>>>(1);let write_error=tx.clone();let writer=thread::spawn(move||{while let Ok(raw)=requests.recv(){if let Err(error)=frame(&mut stdin,raw.as_slice()){let _=write_error.send(Err(error));break;}}});let reader=thread::spawn(move||{let mut reader=BufReader::new(output);loop{let reply=match read_frame(&mut reader){Ok(Some(raw))=>crate::indexed_json::wire::from_slice(&raw).map_err(|e|e.to_string()),Ok(None)=>{let _=tx.send(Err("worker process exited without result".into()));break;},Err(e)=>{let _=tx.send(Err(e));break;}};if tx.send(reply).is_err(){break;}}});Ok(Self{child,input,frames,reader:Some(reader),writer:Some(writer)})}
    fn run(&mut self,job:&Job,shared:&Shared,timeout:Duration)->WorkerResult<()>{self.input.try_send(job.raw.clone()).map_err(|_|"worker request writer unavailable")?;let start=Instant::now();loop{if job.cancelled.load(Ordering::Acquire)||shared.closed.load(Ordering::Acquire){return Err("worker cancelled".into());}if start.elapsed()>timeout{return Err("worker deadline exceeded".into());}let response=match self.frames.recv_timeout(Duration::from_millis(100)){Ok(Ok(value))=>value,Ok(Err(error))=>{
            if error=="worker process exited without result"{
                let status=self.child.try_wait().map_err(|e|format!("worker exit status unavailable: {e}"))?.map(|status|status.to_string()).unwrap_or_else(||"stdout closed before process exit".into());
                eprintln!("quant worker {} exited before result: {status}",self.child.id());
                return Err(format!("worker process exited without result ({status})"));
            }
            return Err(error);
        },Err(mpsc::RecvTimeoutError::Timeout)=>continue,Err(_)=>return Err("worker result channel closed before result".into())};let done=!matches!(response,Reply::Progress{..}|Reply::Step{..});if let Reply::Completed{outcome}=&response{if let Ok(mut stats)=shared.statistics.lock(){stats.completed+=1;if let Some(bytes)=outcome.peak_rss_bytes{stats.peak_worker_rss_bytes=stats.peak_worker_rss_bytes.max(bytes);if !stats.worker_peaks.contains_key(&outcome.pid)&&stats.worker_peaks.len()>=32{if let Some(pid)=stats.worker_peaks.keys().next().copied(){stats.worker_peaks.remove(&pid);}}let peak=stats.worker_peaks.entry(outcome.pid).or_default();*peak=(*peak).max(bytes);}}}let mut reply=Ok(response);loop{match job.reply.try_send(reply){Ok(())=>break,Err(mpsc::TrySendError::Full(value))=>{reply=value;if job.cancelled.load(Ordering::Acquire)||shared.closed.load(Ordering::Acquire){return Err("worker cancelled".into());}if start.elapsed()>timeout{return Err("worker consumer deadline exceeded".into());}thread::sleep(Duration::from_millis(20));},Err(mpsc::TrySendError::Disconnected(_))=>return Err("worker consumer dropped".into())}}if done{return Ok(());}}}
}
impl Drop for Process{fn drop(&mut self){let _=self.child.kill();let _=self.child.wait();if let Some(reader)=self.reader.take(){// 接收器先清空使读线程不会卡在有界发送。
    while self.frames.try_recv().is_ok(){}drop(reader);if let Some(writer)=self.writer.take(){drop(writer);}
}}}
pub struct WorkerPool{sender:Option<SyncSender<Job>>,shared:Arc<Shared>,threads:Vec<JoinHandle<()>>,workers:usize,engine:String}
impl WorkerPool{
    pub fn new(config:WorkerConfig)->WorkerResult<Self>{if !(1..=8).contains(&config.workers)||config.timeout.is_zero(){return Err("invalid worker configuration".into());}let(tx,rx)=mpsc::sync_channel::<Job>(config.workers);let receiver=Arc::new(Mutex::new(rx));let shared=Arc::new(Shared{bytes:AtomicUsize::new(0),closed:AtomicBool::new(false),statistics:Mutex::new(Statistics::default())});let mut threads=Vec::new();for _ in 0..config.workers{let receiver=receiver.clone();let shared=shared.clone();let config=config.clone();threads.push(thread::spawn(move||{let mut process:Option<Process>=None;while !shared.closed.load(Ordering::Acquire){let job=match receiver.lock(){Ok(receiver)=>receiver.recv_timeout(Duration::from_millis(100)),Err(_)=>break};let job=match job{Ok(job)=>job,Err(mpsc::RecvTimeoutError::Timeout)=>continue,Err(_)=>break};if job.cancelled.load(Ordering::Acquire){continue;}let result=(||{if process.is_none(){process=Some(Process::spawn(&config)?);}process.as_mut().ok_or("worker unavailable")?.run(&job,&shared,config.timeout)})();if let Err(error)=result{process=None;if let Ok(mut stats)=shared.statistics.lock(){stats.crashed+=1;}let _=job.reply.try_send(Err(error));}}}));}Ok(Self{sender:Some(tx),shared,threads,workers:config.workers,engine:engine_identity()})}
    /// 不等待预算释放；拒收由调用方在原串行时点执行，不改变领域错误的时机。
    fn capacity_rejection(&self,error:String)->String{if let Ok(mut stats)=self.shared.statistics.lock(){stats.fallbacks+=1;}error}
    /// 提交失败只有明确容量不足可串行降级；断开的工作通道仍是运行故障。
    pub fn submit(&self,request:WorkerRequest)->WorkerResult<WorkerTicket>{
        if self.shared.closed.load(Ordering::Acquire){return Err("worker pool closed".into());}
        let timeframe=request.timeframe().to_owned();if timeframe.trim().is_empty(){return Err("worker timeframe must be explicit".into());}
        let capacity=|error:String|if is_capacity_rejection(&error){self.capacity_rejection(error)}else{error};
        let value=crate::indexed_json::to_value(&request).map_err(|e|e.to_string())?;
        let hash=sha256(canonical(&value).map_err(|e|e.to_string())?);
        let raw=encode(&Envelope{schema:SCHEMA,request_sha256:hash.clone(),engine_sha256:self.engine.clone(),timeframe:timeframe.clone(),request:value},PAYLOAD_BYTES).map_err(capacity)?;
        let size=raw.len();let reserved=self.shared.bytes.fetch_update(Ordering::AcqRel,Ordering::Acquire,|current|current.checked_add(size).filter(|next|*next<=QUEUE_BYTES));
        if reserved.is_err(){return Err(self.capacity_rejection("worker queue byte budget exceeded".into()));}
        let charge=Arc::new(Charge{shared:self.shared.clone(),size});let(cancelled,(tx,rx))=(Arc::new(AtomicBool::new(false)),mpsc::sync_channel(1));
        let job=Job{raw:Arc::new(raw),reply:tx,cancelled:cancelled.clone(),_charge:charge.clone()};
        match self.sender.as_ref().ok_or("worker pool closed")?.try_send(job){Ok(())=>{},Err(mpsc::TrySendError::Full(_))=>return Err(self.capacity_rejection("worker queue capacity exceeded".into())),Err(mpsc::TrySendError::Disconnected(_))=>return Err("worker job channel disconnected".into())}
        if let Ok(mut stats)=self.shared.statistics.lock(){stats.submissions+=1;stats.peak_queue_bytes=stats.peak_queue_bytes.max(self.shared.bytes.load(Ordering::Acquire));}
        Ok(WorkerTicket{receiver:rx,cancelled,_charge:charge,hash,engine:self.engine.clone(),timeframe,consumed:false})
    }
    pub fn statistics(&self)->Value{match self.shared.statistics.lock(){Ok(s)=>json!({"workers":self.workers,"submissions":s.submissions,"fallbacks":s.fallbacks,"completed":s.completed,"crashed":s.crashed,"peak_queue_bytes":s.peak_queue_bytes,"queue_bytes":self.shared.bytes.load(Ordering::Acquire),"queue_limit_bytes":QUEUE_BYTES,"payload_limit_bytes":PAYLOAD_BYTES,"worker_peak_rss_bytes":s.worker_peaks,"peak_worker_rss_bytes":s.peak_worker_rss_bytes,"worker_peak_entries_limit":32,"closed":self.shared.closed.load(Ordering::Acquire)}),Err(_)=>json!({"status":"statistics_unavailable"})}}
    pub fn close(&mut self){self.shared.closed.store(true,Ordering::Release);self.sender.take();for thread in self.threads.drain(..){let _=thread.join();}}
}
impl Drop for WorkerPool{fn drop(&mut self){self.close();}}
pub fn is_capacity_rejection(error:&str)->bool{matches!(error,"worker queue byte budget exceeded"|"worker queue capacity exceeded"|"worker payload budget exceeded"|"worker compressed payload budget exceeded")||error.starts_with("worker payload budget exceeded at line ")}
fn compute_system(sessions:&[BacktestSession],config:SystemConfig,output:&mut impl Write,request_hash:&str,record_steps:bool,structure_cache_root:Option<&Path>)->WorkerResult<(Value,usize,Option<Value>)>{
    use std::cell::{RefCell,Cell};
    #[derive(Serialize)]struct BorrowedReply<'a,T:Serialize>{kind:&'static str,request_sha256:&'a str,value:&'a T}
    let output=RefCell::new(output);let skipped=Cell::new(0usize);let failure=RefCell::new(None::<String>);let cancelled=Arc::new(AtomicBool::new(false));
    let cache=structure_cache_root.and_then(|root|crate::persistence::StructurePrefixCache::new(root).ok()).map(Arc::new);
    let shared=cache.as_ref().map(|cache|cache.clone() as Arc<dyn analyzer::StructureCache>);
    let computed=system::run_system_backtest_with_cache(sessions,config,Some(cancelled.clone()),|progress|{
        if failure.borrow().is_some(){return;}let reply=BorrowedReply{kind:"progress",request_sha256:request_hash,value:progress};let sent=encode(&reply,PAYLOAD_BYTES).and_then(|raw|frame(&mut **output.borrow_mut(),&raw));if sent.is_err(){skipped.set(skipped.get()+1);}
    },|step|{
        if !record_steps||failure.borrow().is_some(){return;}let reply=BorrowedReply{kind:"step",request_sha256:request_hash,value:step};if let Err(error)=encode(&reply,PAYLOAD_BYTES).and_then(|raw|frame(&mut **output.borrow_mut(),&raw)){*failure.borrow_mut()=Some(format!("worker step delivery failed: {error}"));cancelled.store(true,Ordering::Release);}
    },shared);
    if let Some(error)=failure.into_inner(){return Err(error);}let result=computed?;Ok((crate::indexed_json::to_value(result).map_err(|e|e.to_string())?,skipped.get(),cache.as_ref().map(|cache|cache.statistics())))
}
#[cfg(test)]
mod transport_tests{
    use super::*;
    #[test]
    fn compressed_transport_preserves_shared_values_without_node_table(){
        let point=json!({"price":"12.3400","sourceIndex":17,"confirmed":false});
        let value=json!({"data":[point.clone(),point],"empty":[],"missing":null});
        let raw=encode(&value,PAYLOAD_BYTES).unwrap();
        let mut bytes=Vec::new();frame(&mut bytes,&raw).unwrap();
        let decoded=read_frame(&mut bytes.as_slice()).unwrap().unwrap();
        let restored:Value=crate::indexed_json::wire::from_slice(&decoded).unwrap();
        assert_eq!(value,restored);
        assert!(!crate::indexed_json::wire::is_document(&restored));
        assert_eq!(restored["data"][1]["price"],"12.3400");
    }
    #[test]
    fn expanded_shared_values_still_obey_request_budget(){
        let shared=json!("x".repeat(1024));
        let value=json!([shared.clone(),shared.clone(),shared]);
        let error=encode(&value,2048).unwrap_err();
        assert!(is_capacity_rejection(&error));
    }
}

#[cfg(test)]
mod digest_roundtrip_tests {
    use super::*;

    #[test]
    fn request_digest_survives_json_and_compressed_pipe() {
        // 复权价必须按原始 f64 精确往返；摘要校验不能忽略最后一位变化。
        let mut state = 0x7a91_3344_5512_9999_u64;
        let mut prices = Vec::new();
        for _ in 0..4096 {
            state = state.wrapping_mul(6364136223846793005).wrapping_add(1);
            let price = f64::from_bits((state & ((1_u64 << 52) - 1)) | (1026_u64 << 52));
            prices.push(price);
        }
        prices.extend([6.165274123456789, 7.199363584352192, 8.887759321806618]);
        let request = crate::indexed_json::to_value(&prices).unwrap();
        let digest = sha256(canonical(&request).unwrap());
        let envelope = Envelope { schema: SCHEMA, request_sha256: digest.clone(),
            engine_sha256: "test-engine".into(), timeframe: "1d".into(), request };
        let raw = encode(&envelope, PAYLOAD_BYTES).unwrap();
        let mut pipe = Vec::new();
        frame(&mut pipe, &raw).unwrap();
        let decoded = read_frame(&mut pipe.as_slice()).unwrap().unwrap();
        let received: Envelope = crate::indexed_json::wire::from_slice(&decoded).unwrap();
        assert_eq!(digest, sha256(canonical(&received.request).unwrap()));
        let restored: Vec<f64> = crate::indexed_json::from_value(received.request.clone()).unwrap();
        assert_eq!(prices.iter().map(|p| p.to_bits()).collect::<Vec<_>>(),
                   restored.iter().map(|p| p.to_bits()).collect::<Vec<_>>());
        let mut changed = received.request;
        changed[0] = crate::indexed_json::to_value(prices[0] + 1.0).unwrap();
        assert_ne!(digest, sha256(canonical(&changed).unwrap()));
    }
}
