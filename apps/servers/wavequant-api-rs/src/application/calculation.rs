//! 逐日正式观察的诊断适配；直接保存既有帧与证据，不重新计算或修饰结构。
use std::{collections::{BTreeMap,BTreeSet},path::Path,sync::Arc,time::{Duration,Instant}};
use serde::Deserialize;
use wavequant_quant_rs::indexed_json::{Value,json};
use wavequant_quant_rs::{analysis::{system::SystemStep,kernel::KernelFrame,trading::{RuleCandle,day::TradingDayEvaluation}},execution::BacktestSession,
    persistence::{ArtifactCache,CalculationStepCache,CalculationPlan,canonical,sha256}};
use crate::presentation::error::ApiError;
type Result<T>=std::result::Result<T,ApiError>;
#[derive(Deserialize)]
struct OwnedStep{symbol:String,index:usize,bar:RuleCandle,frames:Vec<KernelFrame>,trading:TradingDayEvaluation}
pub struct Recorder{cache:CalculationStepCache,timeframe:String,details:bool,versions:BTreeMap<u8,String>,events:BTreeSet<String>,observed_steps:BTreeSet<u8>,last_index:Option<usize>,symbol:String,error:Option<String>,last_publish:Instant,finished:bool}
impl Recorder{
    pub fn new(root:Option<&Path>,identity:Value,sessions:&[BacktestSession],details:bool)->Result<Self>{if identity.get("timeframe").and_then(Value::as_str)!=Some("1d"){return Err(ApiError::invalid("system calculation recording requires explicit daily timeframe 1d"));}let timeframe="1d".to_owned();let symbol=sessions.first().map(|s|s.daily.symbol.clone()).ok_or_else(||ApiError::invalid("calculation recording requires source sessions"))?;if sessions.iter().any(|s|s.daily.symbol!=symbol){return Err(ApiError::invalid("stock calculation recording requires one security"));}
        let artifact=if details{root.map(|root|ArtifactCache::new(root,4*1024*1024*1024).map(Arc::new)).transpose().map_err(|e|ApiError::invalid(e.to_string()))?}else{None};let mut cache=CalculationStepCache::new(artifact,identity,details).map_err(|e|ApiError::invalid(e.to_string()))?;cache.bind(sessions).map_err(|e|ApiError::invalid(e.to_string()))?;Ok(Self{cache,timeframe,details,versions:BTreeMap::new(),events:BTreeSet::new(),observed_steps:BTreeSet::new(),last_index:None,symbol,error:None,last_publish:Instant::now(),finished:false})
    }
    pub fn set_order(&mut self,order:&[u8])->Result<()>{self.cache.set_plan(CalculationPlan::resolve(Some(order)).map_err(|e|ApiError::invalid(e.to_string()))?).map_err(|e|ApiError::invalid(e.to_string()))}
    fn append(&mut self,step:u8,index:usize,row:&Value){match self.cache.append_at(step,index,row){Ok(())=>{self.observed_steps.insert(step);},Err(error)=>{if self.error.is_none(){self.error=Some(error.to_string());}}}}
    fn event(&mut self,event:&Value,index:usize,level:Option<u8>){let mut row=event.clone();if !row.is_object(){return;}row["symbol"]=json!(self.symbol);row["timeframe"]=json!(self.timeframe);if let Some(level)=level{row["trend_level"]=json!(level);}let identity=match canonical(&row){Ok(v)=>sha256(v),Err(error)=>{self.error=Some(error.to_string());return;}};if !self.events.insert(identity){return;}if self.events.len()>131072{self.error=Some("diagnostic event identity budget exceeded".into());return;}row["observed_index"]=json!(index);
        let name=row["event"].as_str().unwrap_or("");if name.starts_with("n_")||name.starts_with("wave_")||name.starts_with("one_p_"){self.append(2,index,&row);}
        let level=level.or_else(||row["trend_level"].as_u64().and_then(|v|u8::try_from(v).ok()));if let Some(level)=level.filter(|v|(1..=3).contains(v)){let state=row["state"].as_str();let state=match state{Some("多头")=>Some("转多确认"),Some("空头")=>Some("转空确认"),v=>v};if let Some(offset)=state.and_then(|s|["空翻多","空多交替","转多确认","多翻空","多空交替","转空确认"].iter().position(|v|*v==s)){self.append(4+(level-1)*7+offset as u8,index,&row);}else if name=="trend_lifecycle_invalidated"{for offset in 0..6{self.append(4+(level-1)*7+offset,index,&row);}}}
    }
    pub fn observe_step(&mut self,step:&SystemStep<'_>){if self.error.is_some(){return;}if step.symbol!=self.symbol||self.last_index.is_some_and(|old|step.index<=old){self.error=Some("calculation observer sequence does not match source".into());return;}self.last_index=Some(step.index);
        for frame in step.frames{let number=match frame.level{0=>1,1=>3,2=>10,3=>17,_=>continue};if self.details{match wavequant_quant_rs::indexed_json::to_value(frame).and_then(|value|wavequant_quant_rs::indexed_json::to_vec(&value).map(|bytes|(value,bytes))){Ok((value,bytes))=>{let digest=sha256(&bytes);if self.versions.get(&number)!=Some(&digest){self.versions.insert(number,digest);self.append(number,step.index,&json!({"symbol":step.symbol,"bar_index":step.index,"timestamp":step.bar.timestamp,"trend_level":frame.level,"strokes":[value]}));}},Err(error)=>{self.error=Some(error.to_string());return;}}}else{self.append(number,step.index,&json!({"symbol":step.symbol,"bar_index":step.index,"trend_level":frame.level,"point_count":frame.points.len(),"event_count":frame.events.len(),"state":frame.state,"blocked_at":frame.blocked_at}));}
            for event in &frame.events{if event.known_index>step.index{continue;}if let Some(proof)=event.evidence.get("lifecycle_event"){self.event(proof,step.index,Some(frame.level));}}
        }
        for event in &step.trading.events{self.event(event,step.index,None);}
    }
    pub fn observe_worker_step(&mut self,value:Value)->Result<()>{let step:OwnedStep=wavequant_quant_rs::indexed_json::from_value(value)?;self.observe_step(&SystemStep{symbol:&step.symbol,index:step.index,bar:&step.bar,frames:&step.frames,trading:&step.trading});Ok(())}
    pub fn snapshot(&self)->Value{let mut value=self.cache.snapshot();value["observed_through_index"]=json!(self.last_index);value["symbol"]=json!(self.symbol);value["timeframe"]=json!(self.timeframe);value["recording_error"]=json!(self.error);value["row_count_basis"]=json!(if self.details{"changed_frame_versions_and_unique_events"}else{"daily_observations_and_unique_events"});value}
    pub fn pipeline_due(&mut self,force:bool)->Option<Value>{if force||self.last_publish.elapsed()>=Duration::from_secs(1){self.last_publish=Instant::now();Some(self.snapshot())}else{None}}
    pub fn finish(&mut self,result:Option<&Value>,error:Option<&str>)->Value{self.finished=true;if error.is_none()&&self.error.is_none(){for step in self.observed_steps.clone(){if let Err(error)=self.cache.complete(step){self.error=Some(error.to_string());break;}}}
        let failure=error.map(str::to_owned).or_else(||self.error.clone());if let Err(error)=self.cache.finish(result,failure.as_deref()){self.error=Some(error.to_string());}self.snapshot()
    }
}
impl Drop for Recorder{fn drop(&mut self){if !self.finished{let _=self.cache.finish(None,Some("calculation aborted before final publication"));}}}