//! 冻结同输入的本地性能与缓存探针；需用户当次授权，不启动 SDK、HTTP 或账户。
use std::{fs,path::Path,time::Instant};
use sha2::{Digest,Sha256};
use wavequant_quant_rs::{analysis::{analyzer::{AnalysisRequest,TrendAnalyzer},trading::RuleCandle},indexed_json::{self,json},persistence::ArtifactCache};
fn sha(bytes:&[u8])->String{format!("{:x}",Sha256::digest(bytes))}
fn main()->Result<(),Box<dyn std::error::Error>>{
    let args:Vec<_>=std::env::args().collect();let input=fs::read(args.get(1).ok_or("input required")?)?;
    let out=Path::new(args.get(2).ok_or("output directory required")?);fs::create_dir_all(out)?;
    let mut request:AnalysisRequest=indexed_json::from_slice(&input)?;
    // 参数来自同一冻结请求；无 config 时使用该版本既有默认值，不隐式改策略。
    request.state=None;let config_sha=sha(&serde_json::to_vec(&request.config)?);
    let bars:Vec<RuleCandle>=request.bars.iter().map(|b|Ok(RuleCandle{symbol:request.identity.symbol.clone(),timestamp:b.time.clone(),
        open:b.open.to_string().parse()?,high:b.high.to_string().parse()?,low:b.low.to_string().parse()?,close:b.close.to_string().parse()?,
        volume:b.volume,adjustment_factor:b.adjustment_factor,raw_trading_active:b.raw_trading_active,raw_is_st:b.raw_is_st})).collect::<Result<_,Box<dyn std::error::Error>>>()?;
    let symbol=request.identity.symbol.clone();let bar_count=bars.len();let start=Instant::now();let phase=Instant::now();
    let mut analyzer=TrendAnalyzer::new(request)?;let init_ms=phase.elapsed().as_secs_f64()*1000.0;
    let phase=Instant::now();while analyzer.advance_polyline_index()?{}analyzer.finish_polyline_array()?;let polyline_ms=phase.elapsed().as_secs_f64()*1000.0;
    let mut levels=[0.0;3];for level in 1..=3{let phase=Instant::now();analyzer.finish_level_array(level)?;levels[level-1]=phase.elapsed().as_secs_f64()*1000.0;}
    let phase=Instant::now();analyzer.finish_qualification_arrays(None)?;let qualification_ms=phase.elapsed().as_secs_f64()*1000.0;
    let phase=Instant::now();let result=analyzer.indexed_response();let output_ms=phase.elapsed().as_secs_f64()*1000.0;
    let analysis_ms=start.elapsed().as_secs_f64()*1000.0;
    let phase=Instant::now();let(view,snapshot)=wavequant_api_rs::application::trend_state::response(&result,&bars,"frozen-perf",&bars.last().ok_or("empty bars")?.timestamp,0.0,0.0,0.0)
        .map_err(|_|"frontend projection failed")?;let projection_ms=phase.elapsed().as_secs_f64()*1000.0;
    let phase=Instant::now();let raw=indexed_json::to_vec(&result)?;let encode_json_ms=phase.elapsed().as_secs_f64()*1000.0;
    let phase=Instant::now();let wire=indexed_json::wire::to_vec(&result)?;let encode_wire_ms=phase.elapsed().as_secs_f64()*1000.0;
    let value:serde_json::Value=serde_json::to_value(&result)?;
    let memory=value.get("nMemory").cloned().unwrap_or(serde_json::Value::Null);
    let n_records=value.get("nRecords").cloned().unwrap_or(serde_json::Value::Null);
    let first_ns:Vec<usize>=(0..4).map(|level|memory.get(level).and_then(|level|level.get("paths")).and_then(|paths|paths.as_array())
        .map_or(0,|paths|paths.iter().filter_map(|path|path.get("firstNs").and_then(|ns|ns.as_array())).map(Vec::len).sum())).collect();
    fs::write(out.join("indexed.json"),&raw)?;fs::write(out.join("indexed.wire.json"),&wire)?;
    fs::write(out.join("view.json"),indexed_json::to_vec(&view)?)?;fs::write(out.join("view.wire.json"),indexed_json::wire::to_vec(&view)?)?;
    fs::write(out.join("snapshot.wire.json"),indexed_json::wire::to_vec(&snapshot)?)?;fs::write(out.join("memory.json"),serde_json::to_vec(&memory)?)?;fs::write(out.join("n-records.json"),serde_json::to_vec(&n_records)?)?;
    // 独立 SQLite 实测生产编码，不触及运行服务的缓存；pipeline 为空是两组共同固定口径。
    let cache=ArtifactCache::new(&out.join("cache"),64*1024*1024)?;let identity=json!({"fixture":sha(&input),"engine":result.state.version});
    let payload=json!({"view":view,"pipeline":null});let phase=Instant::now();
    if !cache.put("backtest",&identity,&payload){return Err("isolated result cache write failed".into());}let cache_write_ms=phase.elapsed().as_secs_f64()*1000.0;
    let phase=Instant::now();let cached=cache.get("backtest",&identity).ok_or("isolated cache read failed")?;let cache_read_ms=phase.elapsed().as_secs_f64()*1000.0;
    if cached!=payload{return Err("isolated cache roundtrip changed the result".into());}
    let phases:std::collections::BTreeMap<_,_>=analyzer.analysis_timings().iter().map(|(key,value)|(key.clone(),value*1000.0)).collect();
    let sample=serde_json::json!({"symbol":symbol,"bars":bar_count,"input_sha256":sha(&input),"config_sha256":config_sha,"version":result.state.version,
        "engine":wavequant_quant_rs::infrastructure::computation::engine_identity(),"init_ms":init_ms,"polyline_ms":polyline_ms,
        "level1_ms":levels[0],"level2_ms":levels[1],"level3_ms":levels[2],"qualification_ms":qualification_ms,"output_ms":output_ms,"analysis_ms":analysis_ms,
        "projection_ms":projection_ms,"encode_json_ms":encode_json_ms,"encode_wire_ms":encode_wire_ms,"cache_write_ms":cache_write_ms,"cache_read_ms":cache_read_ms,
        "structure_sha256":sha(&serde_json::to_vec(&result.levels)?),"qualification_sha256":sha(&serde_json::to_vec(&result.qualification_intervals)?),
        "qualification_counts":result.qualification_intervals.iter().map(Vec::len).collect::<Vec<_>>(),"points":result.levels.iter().map(|level|level.points.len()).collect::<Vec<_>>(),
        "first_n_counts":first_ns,"phases_ms":phases});
    fs::write(out.join("sample.json"),serde_json::to_vec_pretty(&sample)?)?;Ok(())
}
