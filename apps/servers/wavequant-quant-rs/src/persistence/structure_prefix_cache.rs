//! 四层正式结构前缀缓存；调用方继续推进当日资格和账户，不缓存交易状态。
use std::{io::{self,Write},path::Path,sync::atomic::{AtomicUsize,Ordering},time::Duration};
use serde::{Serialize,Deserialize};
use crate::indexed_json::{Value,json};
use crate::analysis::{analyzer::{StructureCache,StructureCacheIdentity,VERSION},kernel::{KernelFrame,KernelPoint}};
use crate::infrastructure::computation::engine_identity;
use super::{ArtifactCache,StoreResult,StoreError,sha256,require,absolute_path};

const SCHEMA:u32=2;
const NAMESPACE:&str="structure-prefix-v2-indexed";
const FRAME_BYTES:usize=32*1024*1024;
const KEY_BYTES:usize=1024*1024;
const ARTIFACT_BYTES:u64=64*1024*1024;
const DISK_BYTES:u64=4*1024*1024*1024;

struct BoundedJson{bytes:Vec<u8>,limit:usize}
impl Write for BoundedJson{
    fn write(&mut self,bytes:&[u8])->io::Result<usize>{
        if self.bytes.len().saturating_add(bytes.len())>self.limit{return Err(io::Error::new(io::ErrorKind::InvalidData,"structure cache JSON budget exceeded"));}
        self.bytes.extend_from_slice(bytes);Ok(bytes.len())
    }
    fn flush(&mut self)->io::Result<()>{Ok(())}
}
fn encode(value:&impl Serialize,limit:usize)->StoreResult<Vec<u8>>{
    let mut output=BoundedJson{bytes:Vec::new(),limit};crate::indexed_json::to_writer(&mut output,&crate::indexed_json::wire::pack(&crate::indexed_json::to_value(value)?)?)?;Ok(output.bytes)
}
fn digest(value:&str)->bool{value.len()==64&&value.bytes().all(|b|b.is_ascii_hexdigit())}
fn valid_key(key:&StructureCacheIdentity)->bool{
    key.analyzer_version==VERSION&&key.through_index<250_000&&digest(&key.prefix_digest)
        &&[&key.identity.symbol,&key.identity.source_version,&key.identity.adjustment,&key.identity.timeframe].iter().all(|v|!v.trim().is_empty())
        &&key.identity.timeframe.len()<=64&&!key.identity.timeframe.chars().any(char::is_control)
}
fn valid_point(point:&KernelPoint,through:usize)->bool{
    point.index<=point.known_index&&point.known_index<=through&&point.level<=3&&point.value.is_finite()&&point.value>0.0&&!point.time.is_empty()
}
fn valid_frames(frames:&[KernelFrame;4],through:usize)->bool{
    frames.iter().enumerate().all(|(level,frame)|{
        frame.level==level as u8&&frame.blocked_at.is_none_or(|index|index<=through)
            &&frame.points.iter().chain(&frame.publication).all(|point|valid_point(point,through))
            &&[&frame.candidate,&frame.key,&frame.anchor,&frame.preceding_start].iter().all(|point|point.as_ref().is_none_or(|point|valid_point(point,through)))
            &&frame.events.iter().all(|event|event.known_index<=through&&valid_point(&event.point,through)
                &&event.key.as_ref().is_none_or(|point|valid_point(point,through))&&event.origin.as_ref().is_none_or(|point|valid_point(point,through)))
    })
}
#[derive(Serialize)]
struct BoundKey<'a>{schema:u32,engine_sha256:&'a str,identity:&'a StructureCacheIdentity}
#[derive(Serialize,Deserialize)]
#[serde(deny_unknown_fields)]
struct StoredPrefix{
    schema:u32,engine_sha256:String,identity:StructureCacheIdentity,
    frames_sha256:String,frames_json:String,
}
/// 只把精确已知前缀的四个 KernelFrame 交还统一分析器；任一失败都是缓存未命中。
pub struct StructurePrefixCache{
    cache:ArtifactCache,engine:String,
    hits:AtomicUsize,misses:AtomicUsize,writes:AtomicUsize,skipped_writes:AtomicUsize,
}
impl StructurePrefixCache{
    pub fn new(root:&Path)->StoreResult<Self>{
        let engine=engine_identity();require(digest(&engine),"structure cache requires compiled engine identity")?;
        let root=absolute_path(root)?;let mut cache=ArtifactCache::new(&root,DISK_BYTES)?;
        cache.max_uncompressed_bytes=ARTIFACT_BYTES;cache.read_timeout=Duration::from_millis(500);
        Ok(Self{cache,engine,hits:AtomicUsize::new(0),misses:AtomicUsize::new(0),writes:AtomicUsize::new(0),skipped_writes:AtomicUsize::new(0)})
    }
    fn key(&self,key:&StructureCacheIdentity)->StoreResult<Value>{
        require(valid_key(key),"structure cache identity must bind a valid exact prefix")?;
        let encoded=encode(&BoundKey{schema:SCHEMA,engine_sha256:&self.engine,identity:key},KEY_BYTES)?;
        Ok(crate::indexed_json::from_slice(&encoded)?)
    }
    fn read(&self,key:&StructureCacheIdentity)->Option<[KernelFrame;4]>{
        let inputs=self.key(key).ok()?;let value=self.cache.get(NAMESPACE,&inputs)?;
        let stored:StoredPrefix=crate::indexed_json::from_value(value).ok()?;
        if stored.schema!=SCHEMA||stored.engine_sha256!=self.engine||stored.identity!=*key
            ||stored.frames_json.len()>FRAME_BYTES||!digest(&stored.frames_sha256)
            ||sha256(stored.frames_json.as_bytes())!=stored.frames_sha256{return None;}
        let frames:[KernelFrame;4]=crate::indexed_json::wire::from_slice(stored.frames_json.as_bytes()).ok()?;
        valid_frames(&frames,key.through_index).then_some(frames)
    }
    fn write(&self,key:&StructureCacheIdentity,frames:&[KernelFrame;4])->StoreResult<bool>{
        let inputs=self.key(key)?;require(valid_frames(frames,key.through_index),"structure cache frames exceed their declared prefix")?;
        let raw=encode(frames,FRAME_BYTES)?;let frames_sha256=sha256(&raw);
        let frames_json=String::from_utf8(raw).map_err(|error|StoreError(error.to_string()))?;
        let payload=crate::indexed_json::to_value(StoredPrefix{schema:SCHEMA,engine_sha256:self.engine.clone(),identity:key.clone(),frames_sha256,frames_json})?;
        Ok(self.cache.put(NAMESPACE,&inputs,&payload))
    }
    pub fn statistics(&self)->Value{
        json!({"kind":"exact_structure_prefix","engine_sha256":self.engine,
            "hits":self.hits.load(Ordering::Relaxed),"misses":self.misses.load(Ordering::Relaxed),
            "writes":self.writes.load(Ordering::Relaxed),"skipped_writes":self.skipped_writes.load(Ordering::Relaxed),
            "max_frame_bytes":FRAME_BYTES,"max_artifact_bytes":ARTIFACT_BYTES,"max_disk_payload_bytes":DISK_BYTES,
            "account_state_reused":false})
    }
}
impl StructureCache for StructurePrefixCache{
    fn get(&self,key:&StructureCacheIdentity)->Option<[KernelFrame;4]>{
        let frames=self.read(key);if frames.is_some(){self.hits.fetch_add(1,Ordering::Relaxed);}else{self.misses.fetch_add(1,Ordering::Relaxed);}frames
    }
    fn put(&self,key:&StructureCacheIdentity,frames:&[KernelFrame;4]){
        if self.write(key,frames).unwrap_or(false){self.writes.fetch_add(1,Ordering::Relaxed);}else{self.skipped_writes.fetch_add(1,Ordering::Relaxed);}
    }
}
