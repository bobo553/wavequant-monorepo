//! 用户授权的资格开关同输入计时；四级结构相同，不访问网络或账户。
use std::{fs,time::Instant,collections::BTreeMap};
use sha2::{Digest,Sha256};
use wavequant_quant_rs::{analysis::analyzer::{AnalysisRequest,TrendAnalyzer},indexed_json};
fn sample(mut request:AnalysisRequest,enabled:bool)->Result<serde_json::Value,Box<dyn std::error::Error>>{
    request.config.enable_secondary_reversal=[false,false,enabled,false];
    let start=Instant::now();let phase=Instant::now();let mut analyzer=TrendAnalyzer::new(request)?;
    let init_ms=phase.elapsed().as_secs_f64()*1000.0;
    let phase=Instant::now();while analyzer.advance_polyline_index()?{}analyzer.finish_polyline_array()?;
    let polyline_ms=phase.elapsed().as_secs_f64()*1000.0;
    let mut levels=[0.0;3];
    for level in 1..=3{let phase=Instant::now();analyzer.finish_level_array(level)?;levels[level-1]=phase.elapsed().as_secs_f64()*1000.0;}
    #[cfg(feature="qualification-timing")]
    wavequant_quant_rs::analysis::kernel::qualification_profile::reset();
    let phase=Instant::now();analyzer.finish_qualification_arrays(None)?;
    let qualification_ms=phase.elapsed().as_secs_f64()*1000.0;
    let phase=Instant::now();let result=analyzer.indexed_response();let output_ms=phase.elapsed().as_secs_f64()*1000.0;
    let analysis_ms=start.elapsed().as_secs_f64()*1000.0;
    #[cfg(feature="qualification-timing")]
    let profile=wavequant_quant_rs::analysis::kernel::qualification_profile::take();
    #[cfg(not(feature="qualification-timing"))]
    let profile=serde_json::Value::Null;
    let structure_hash=format!("{:x}",Sha256::digest(serde_json::to_vec(&result.levels)?));
    let qualification_hash=format!("{:x}",Sha256::digest(serde_json::to_vec(&result.qualification_intervals)?));
    let phases_ms:BTreeMap<_,_>=analyzer.analysis_timings().iter().map(|(k,v)|(k.clone(),v*1000.0)).collect();
    Ok(serde_json::json!({"enabled":enabled,"init_ms":init_ms,"polyline_ms":polyline_ms,"level1_ms":levels[0],"level2_ms":levels[1],"level3_ms":levels[2],"qualification_ms":qualification_ms,"output_ms":output_ms,"analysis_ms":analysis_ms,"structure_hash":structure_hash,"qualification_hash":qualification_hash,"intervals":result.qualification_intervals[2].len(),"qualification_intervals":result.qualification_intervals[2],"points":result.levels.iter().map(|l|l.points.len()).collect::<Vec<_>>(),"phases_ms":phases_ms,"qualification_profile":profile}))
}
fn main()->Result<(),Box<dyn std::error::Error>>{
    let args:Vec<_>=std::env::args().collect();let raw=fs::read(args.get(1).ok_or("input required")?)?;
    let mut request:AnalysisRequest=indexed_json::from_slice(&raw)?;
    // 与当前页面的讲义 V3 对齐，开关之外两组配置完全相同。
    request.config.trading.strict_n_attack_quality=false;
    let symbol=request.identity.symbol.clone();let bars=request.bars.len();let config=request.config.clone();
    for mode in [false,true]{let warmup=sample(request.clone(),mode)?;eprintln!("warmup enabled={mode}: {} ms",warmup["analysis_ms"]);}
    let mut samples=Vec::new();
    for pair in 0..5{for mode in if pair%2==0{[false,true]}else{[true,false]}{let row=sample(request.clone(),mode)?;eprintln!("sample pair={pair} enabled={mode}: {} ms",row["analysis_ms"]);samples.push(row);}}
    let reference=&samples[0]["structure_hash"];
    if samples.iter().any(|s|&s["structure_hash"]!=reference){return Err("qualification switch changed indexed structure".into());}
    let active_hash=samples.iter().find(|s|s["enabled"]==true).ok_or("missing active result")?["qualification_hash"].clone();
    if samples.iter().filter(|s|s["enabled"]==true).any(|s|s["qualification_hash"]!=active_hash){return Err("qualification result was not deterministic".into());}
    let mut reports=Vec::new();
    for mode in [false,true]{let rows:Vec<_>=samples.iter().filter(|s|s["enabled"]==mode).collect();let mut report=serde_json::Map::new();report.insert("enabled".into(),mode.into());
        for field in ["init_ms","polyline_ms","level1_ms","level2_ms","level3_ms","qualification_ms","output_ms","analysis_ms"]{
            let mut values:Vec<_>=rows.iter().map(|r|r[field].as_f64().unwrap()).collect();values.sort_by(f64::total_cmp);
            report.insert(field.into(),serde_json::json!({"median":values[2],"min":values[0],"max":values[4]}));}
        report.insert("intervals".into(),rows[0]["intervals"].clone());reports.push(report);
    }
    let output=serde_json::json!({"symbol":symbol,"bars":bars,"input_sha256":format!("{:x}",Sha256::digest(&raw)),"engine":wavequant_quant_rs::infrastructure::computation::engine_identity(),"config":config,"samples_per_mode":5,"warmups_per_mode":1,"structure_equal":true,"qualification_deterministic":true,"reports":reports,"samples":samples,"scope":"in-process current array path; excludes JSON input, SDK/Redis, Worker pipe and HTTP; includes initialization, four-level structure, optional qualification, indexed result"});
    fs::write(args.get(2).ok_or("output required")?,serde_json::to_vec_pretty(&output)?)?;
    println!("{}",serde_json::to_string(&output["reports"])?);Ok(())
}
