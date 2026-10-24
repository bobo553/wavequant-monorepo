//! 用户指定自选股冻结行情差分；直接调用生产System入口，独立标记HTTP/Worker故障。
use std::{fs,path::PathBuf,collections::BTreeMap,io::Write,process::{Command,Stdio}};
use serde::Deserialize;
use rust_decimal::prelude::ToPrimitive;
use wavequant_quant_rs::{indexed_json::{Value,json},analysis::{system::{self,SystemConfig},kernel::PointState},data::adjustments::ExecutionBar,execution::BacktestSession,infrastructure::computation::{WorkerRequest,engine_identity},persistence::{canonical,sha256}};
#[derive(Deserialize)]struct Frozen{market:Market,request:Selection}
#[derive(Deserialize)]struct Market{bars:Vec<ExecutionBar>,version:String}
#[derive(Deserialize)]struct Selection{symbol:String,source:String}
fn difference(a:&Value,b:&Value,path:String)->Option<Value>{
    if a==b{return None;}
    if let(Some(a),Some(b))=(a.as_object(),b.as_object()){
        if a.keys().ne(b.keys()){return Some(json!({"path":path,"reason":"object keys differ"}));}
        for(k,v)in a{if let Some(d)=difference(v,&b[k],format!("{path}.{k}")){return Some(d);}}
    }else if let(Some(a),Some(b))=(a.as_array(),b.as_array()){
        if a.len()!=b.len(){return Some(json!({"path":path,"offLength":a.len(),"onLength":b.len()}));}
        for(i,(a,b))in a.iter().zip(b).enumerate(){if let Some(d)=difference(a,b,format!("{path}[{i}]")){return Some(d);}}
    }
    Some(json!({"path":path,"off":a,"on":b}))
}
fn main()->Result<(),Box<dyn std::error::Error>>{
    let manifest=PathBuf::from(std::env::args().nth(1).ok_or("manifest required")?);let root=manifest.parent().ok_or("root required")?;
    let m:Value=wavequant_quant_rs::indexed_json::from_slice(&fs::read(&manifest)?)?;let asof=m["asof"].as_str().ok_or("asof required")?;
    let context=&m["context"];let mut reports=Vec::new();let mut probed=false;
    for entry in m["entries"].as_array().ok_or("entries required")?{
        let file=entry["file"].as_str().ok_or("file required")?;let frozen:Frozen=wavequant_quant_rs::indexed_json::from_slice(&fs::read(file)?)?;let symbol=&frozen.request.symbol;
        let sessions:Vec<_>=frozen.market.bars.into_iter().filter(|b|b.timestamp.as_str()<=asof).map(|daily|BacktestSession{daily,source:frozen.request.source.clone(),data_version:frozen.market.version.clone(),minutes:None,minute_source:None,minute_data_version:None}).collect();
        let mut config=SystemConfig::default();config.trading_start=Some(context["start"].as_str().ok_or("start")?.into());config.trading_end=Some(asof.into());
        config.analyzer.kernel.ordinary_trend_completion_enabled=context["ordinary_trend_completion_enabled"]=="true";
        config.analyzer.n_target_trend_confirmation_enabled=context["n_target_trend_confirmation_enabled"]=="true";
        config.analyzer.trading.volume_filter=context["volume_filter"]=="true";config.analyzer.trading.shallow_base_breakout_enabled=context["shallow_base_breakout_enabled"]=="true";
        let mut outputs=BTreeMap::new();let mut failure=None;
        for enabled in [false,true]{config.analyzer.kernel.trend_offset_enabled=enabled;
            match system::run_system_backtest_with_cache(&sessions,config.clone(),None,|_|{},|_|{},None){Ok(output)=>{outputs.insert(enabled,output);},Err(error)=>{failure=Some(error);break;}}
        }
        if let Some(error)=failure{reports.push(json!({"symbol":symbol,"status":"core_error","error":error}));continue;}
        let off=&outputs[&false];let on=&outputs[&true];let mut a=wavequant_quant_rs::indexed_json::to_value(&off.indexed_analysis[symbol])?;let mut b=wavequant_quant_rs::indexed_json::to_value(&on.indexed_analysis[symbol])?;
        // 配置中的执行开关本身预期不同，除此之外比较整个索引结果和状态。
        a["state"]["config"]["kernel"].as_object_mut().ok_or("kernel config")?.remove("trend_offset_enabled");b["state"]["config"]["kernel"].as_object_mut().ok_or("kernel config")?.remove("trend_offset_enabled");
        let diff=difference(&a,&b,"$".into());
        fs::write(root.join(format!("{symbol}-native-off.json")),wavequant_quant_rs::indexed_json::to_vec(&a)?)?;fs::write(root.join(format!("{symbol}-native-on.json")),wavequant_quant_rs::indexed_json::to_vec(&b)?)?;
        let indexed=&off.indexed_analysis[symbol];let level=&indexed.levels[0];let mut ids=level.points.clone();if let Some(id)=level.candidate{if !ids.iter().any(|i|i.0==id.0){ids.push(id);}}
        let polyline=ids.iter().map(|id|{let i=id.0;json!({"barIndex":level.bar_indices[i].0,"kind":level.kinds[i],"ordinal":level.ordinals[i],"knownIndex":level.known_indices[i].0,"path":level.paths[i],"confirmed":level.states[i]!=PointState::Developing})}).collect::<Vec<_>>();
        let bars=off.bars[symbol].iter().map(|b|json!({"time":b.timestamp,"open":b.open.to_f64(),"high":b.high.to_f64(),"low":b.low.to_f64(),"close":b.close.to_f64(),"volume":b.volume.to_f64()})).collect::<Vec<_>>();
        fs::write(root.join(format!("{symbol}-native-input.json")),wavequant_quant_rs::indexed_json::to_vec(&json!({"state":{"data":{"polyline":polyline}},"bars":bars}))?)?;
        let request=wavequant_quant_rs::indexed_json::to_value(WorkerRequest::System{sessions,config:config.clone(),structure_cache_root:None})?;
        let before=sha256(canonical(&request)?);let raw=wavequant_quant_rs::indexed_json::to_vec(&json!({"schema":4,"request_sha256":before,"engine_sha256":engine_identity(),"timeframe":"1d","request":request}))?;
        let parsed:Value=wavequant_quant_rs::indexed_json::from_slice(&raw)?;let after=sha256(canonical(&parsed["request"])?);let roundtrip=before==after;
        let mut worker_reproduction=Value::Null;
        if !roundtrip&&!probed{
            probed=true;let mut compressor=flate2::write::ZlibEncoder::new(Vec::new(),flate2::Compression::fast());compressor.write_all(&raw)?;let packed=compressor.finish()?;
            let mut command=Command::new("target/debug/wavequant-quant-rs.exe");command.arg("internal-worker").stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped());
            #[cfg(target_os="windows")]{use std::os::windows::process::CommandExt;command.creation_flags(0x08000000);}
            let mut child=command.spawn()?;let mut input=child.stdin.take().ok_or("stdin")?;input.write_all(&(packed.len() as u32).to_le_bytes())?;input.write_all(&packed)?;drop(input);let result=child.wait_with_output()?;
            worker_reproduction=json!({"exitCode":result.status.code(),"stderr":String::from_utf8_lossy(&result.stderr).to_string(),"stdoutBytes":result.stdout.len()});
        }
        let report=json!({"symbol":symbol,"name":entry["name"],"status":if diff.is_none(){"equal"}else{"difference"},"bars":off.bars[symbol].len(),"points":indexed.levels.iter().map(|l|l.points.len()+usize::from(l.candidate.is_some())).collect::<Vec<_>>(),"difference":diff,"normalizedStateHash":sha256(canonical(&a)?),"workerRequestRoundtripEqual":roundtrip,"workerRequestRoundtripDifference":difference(&request,&parsed["request"],"$.request".into()),"workerReproduction":worker_reproduction});
        println!("{}",wavequant_quant_rs::indexed_json::to_string(&report)?);reports.push(report);
    }
    fs::write(root.join("native-summary-20.json"),wavequant_quant_rs::indexed_json::to_vec_pretty(&json!({"asof":asof,"reports":reports}))?)?;Ok(())
}
