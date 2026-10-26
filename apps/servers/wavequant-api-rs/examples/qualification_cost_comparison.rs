//! 用户授权的当前版本资格成本对照；不运行SDK、HTTP、缓存或账户。
use std::{collections::BTreeMap,fs,time::Instant};
use sha2::{Digest,Sha256};
use wavequant_quant_rs::{analysis::analyzer::{AnalysisRequest,TrendAnalyzer},indexed_json};
#[derive(Clone,Copy,PartialEq,Eq,serde::Serialize)]
#[serde(rename_all="snake_case")]
enum Mode{Structure,Memory,Qualified}
fn digest(value:&impl serde::Serialize)->Result<String,serde_json::Error>{Ok(format!("{:x}",Sha256::digest(serde_json::to_vec(value)?)))}
fn sample(mut request:AnalysisRequest,mode:Mode)->Result<serde_json::Value,Box<dyn std::error::Error>>{
    request.state=None;
    if mode!=Mode::Qualified{request.config.enable_secondary_reversal=[false;4];}
    let flags=request.config.enable_secondary_reversal;let config_hash=digest(&request.config)?;
    let start=Instant::now();let phase=Instant::now();let mut analyzer=TrendAnalyzer::new(request)?;
    let init_ms=phase.elapsed().as_secs_f64()*1000.0;
    let phase=Instant::now();while analyzer.advance_polyline_index()?{}analyzer.finish_polyline_array()?;
    let polyline_ms=phase.elapsed().as_secs_f64()*1000.0;
    let mut levels=[0.0;3];
    for level in 1..=3{let phase=Instant::now();analyzer.finish_level_array(level)?;levels[level-1]=phase.elapsed().as_secs_f64()*1000.0;}
    // 仅用于测量无N/资格阶段的基线，不修改生产调度。
    let qualification_ms=if mode==Mode::Structure{0.0}else{let phase=Instant::now();analyzer.finish_qualification_arrays(None)?;phase.elapsed().as_secs_f64()*1000.0};
    let phase=Instant::now();let result=analyzer.indexed_response();let output_ms=phase.elapsed().as_secs_f64()*1000.0;
    let analysis_ms=start.elapsed().as_secs_f64()*1000.0;
    let phases_ms:BTreeMap<_,_>=analyzer.analysis_timings().iter().map(|(key,value)|(key.clone(),value*1000.0)).collect();
    let memory_ms:f64=phases_ms.get("n_memory_total").copied().unwrap_or_else(||phases_ms.iter().filter(|(key,_)|key.ends_with(".n_memory")).map(|(_,value)|*value).sum());
    Ok(serde_json::json!({"mode":mode,"enabled":flags,"config_sha256":config_hash,"init_ms":init_ms,"polyline_ms":polyline_ms,
        "level1_ms":levels[0],"level2_ms":levels[1],"level3_ms":levels[2],"qualification_ms":qualification_ms,
        "n_memory_ms":memory_ms,"qualification_and_mapping_ms":qualification_ms-memory_ms,
        "output_ms":output_ms,"analysis_ms":analysis_ms,"phases_ms":phases_ms,
        "structure_sha256":digest(&result.levels)?,"n_memory_sha256":digest(&(&result.n_memory,&result.n_records))?,"qualification_sha256":digest(&result.qualification_intervals)?,
        "points":result.levels.iter().map(|l|l.points.len()).collect::<Vec<_>>(),"intervals":result.qualification_intervals,
        "first_n_counts":result.n_memory.iter().map(|l|l.paths.iter().map(|p|p.first_ns.len()).sum::<usize>()).collect::<Vec<_>>()}))
}
fn main()->Result<(),Box<dyn std::error::Error>>{
    let args:Vec<_>=std::env::args().collect();let raw=fs::read(args.get(1).ok_or("input required")?)?;
    let mut request:AnalysisRequest=indexed_json::from_slice(&raw)?;request.state=None;
    if request.config.enable_secondary_reversal==[false;4]{return Err("input must enable qualification for the qualified control".into());}
    if let Some(mode)=args.get(3){
        let mode=match mode.as_str(){"structure"=>Mode::Structure,"memory"=>Mode::Memory,"qualified"=>Mode::Qualified,_=>return Err("invalid comparison mode".into())};
        let mut row=sample(request.clone(),mode)?;
        row["input_sha256"]=serde_json::json!(format!("{:x}",Sha256::digest(&raw)));
        row["symbol"]=serde_json::json!(request.identity.symbol);row["bars"]=serde_json::json!(request.bars.len());
        row["version"]=serde_json::json!(wavequant_quant_rs::analysis::analyzer::VERSION);
        row["engine"]=serde_json::json!(wavequant_quant_rs::infrastructure::computation::engine_identity());
        fs::write(args.get(2).ok_or("output required")?,serde_json::to_vec_pretty(&row)?)?;return Ok(());
    }
    let modes=[Mode::Structure,Mode::Memory,Mode::Qualified];
    for mode in modes{let row=sample(request.clone(),mode)?;eprintln!("warmup {}: {}ms",row["mode"],row["analysis_ms"]);}
    let mut samples=Vec::new();
    for round in 0..5{for offset in 0..3{let mode=modes[(round+offset)%3];let row=sample(request.clone(),mode)?;
        eprintln!("sample {} {}: {}ms",round+1,row["mode"],row["analysis_ms"]);samples.push(row);}}
    let reference=&samples[0]["structure_sha256"];
    if samples.iter().any(|row|&row["structure_sha256"]!=reference){return Err("mode changed four-level structure or segment states".into());}
    let memory=samples.iter().find(|row|row["mode"]=="memory").ok_or("missing memory control")?;
    if samples.iter().filter(|row|row["mode"]!="structure").any(|row|row["n_memory_sha256"]!=memory["n_memory_sha256"]){return Err("qualification changed N memory".into());}
    let mut reports=Vec::new();
    for mode in modes{
        let key=serde_json::to_value(mode)?;let rows:Vec<_>=samples.iter().filter(|row|row["mode"]==key).collect();let first=rows[0];
        if rows.iter().any(|row|row["qualification_sha256"]!=first["qualification_sha256"]||row["config_sha256"]!=first["config_sha256"]){return Err("mode results are nondeterministic".into());}
        if mode!=Mode::Qualified&&rows.iter().any(|row|row["intervals"].as_array().is_none_or(|levels|levels.iter().any(|l|l.as_array().is_none_or(|intervals|!intervals.is_empty())))){return Err("disabled qualification returned intervals".into());}
        let mut report=serde_json::Map::new();report.insert("mode".into(),key);
        for field in ["init_ms","polyline_ms","level1_ms","level2_ms","level3_ms","qualification_ms","n_memory_ms","qualification_and_mapping_ms","output_ms","analysis_ms"]{
            let mut values=rows.iter().map(|row|row[field].as_f64().ok_or("invalid timing")).collect::<Result<Vec<_>,_>>()?;values.sort_by(f64::total_cmp);
            report.insert(field.into(),serde_json::json!({"median":values[2],"min":values[0],"max":values[4]}));
        }
        for field in ["enabled","config_sha256","points","first_n_counts","intervals","structure_sha256","n_memory_sha256","qualification_sha256"]{report.insert(field.into(),first[field].clone());}
        reports.push(report);
    }
    let output=serde_json::json!({"version":wavequant_quant_rs::analysis::analyzer::VERSION,"engine":wavequant_quant_rs::infrastructure::computation::engine_identity(),
        "input_sha256":format!("{:x}",Sha256::digest(&raw)),"symbol":request.identity.symbol,"bars":request.bars.len(),"base_config":request.config,
        "samples_per_mode":5,"warmups_per_mode":1,"structure_equal":true,"memory_equal_between_memory_and_qualified":true,"reports":reports,"samples":samples,
        "scope":"same current binary, rotating modes in one process; includes initialization, four-level structure and segment states, optional N/qualification, indexed output; excludes JSON parsing, projection, encoding, caches, SDK, Worker, HTTP and account"});
    fs::write(args.get(2).ok_or("output required")?,serde_json::to_vec_pretty(&output)?)?;
    println!("{}",serde_json::to_string(&output["reports"])?);Ok(())
}
