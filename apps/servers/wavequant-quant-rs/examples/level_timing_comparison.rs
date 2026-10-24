//! 同一冻结行情、相同配置，对比一级与三级数组路径；不访问网络或结果缓存。
use std::{fs,time::Instant};
use sha2::{Digest,Sha256};
use wavequant_quant_rs::{analysis::analyzer::{AnalysisRequest,TrendAnalyzer},indexed_json};
fn sample(request:AnalysisRequest,max_level:usize)->Result<serde_json::Value,Box<dyn std::error::Error>>{
    let start=Instant::now();let phase=Instant::now();let mut analyzer=TrendAnalyzer::new(request)?;
    let init_ms=phase.elapsed().as_secs_f64()*1000.0;
    let phase=Instant::now();while analyzer.advance_polyline_index()?{}analyzer.finish_polyline_array()?;
    let polyline_ms=phase.elapsed().as_secs_f64()*1000.0;
    let mut levels=[0.0;3];
    for level in 1..=max_level{let phase=Instant::now();analyzer.finish_level_array(level)?;levels[level-1]=phase.elapsed().as_secs_f64()*1000.0;}
    let calculation_ms=start.elapsed().as_secs_f64()*1000.0;
    let phase=Instant::now();let result=analyzer.indexed_response();let output_ms=phase.elapsed().as_secs_f64()*1000.0;
    let counts:Vec<_>=result.levels.iter().map(|level|level.points.len()).collect();
    let first_two_hash=format!("{:x}",Sha256::digest(serde_json::to_vec(&result.levels[..2])?));
    Ok(serde_json::json!({"max_level":max_level,"init_ms":init_ms,"polyline_ms":polyline_ms,"level1_ms":levels[0],"level2_ms":levels[1],"level3_ms":levels[2],"calculation_ms":calculation_ms,"output_ms":output_ms,"points":counts,"first_two_hash":first_two_hash}))
}
fn main()->Result<(),Box<dyn std::error::Error>>{
    let path=std::env::args().nth(1).ok_or("frozen AnalysisRequest path required")?;
    let request:AnalysisRequest=indexed_json::from_slice(&fs::read(&path)?)?;
    let symbol=request.identity.symbol.clone();let bars=request.bars.len();
    for level in [1,3]{sample(request.clone(),level)?;}
    let mut samples=Vec::new();
    for pair in 0..10{for level in if pair%2==0{[1,3]}else{[3,1]}{samples.push(sample(request.clone(),level)?);}}
    let reference=samples[0]["first_two_hash"].clone();
    if samples.iter().any(|sample|sample["first_two_hash"]!=reference){return Err("enabling upper levels changed the lower-level indexed result".into());}
    let mut reports=Vec::new();
    for level in [1,3]{let rows:Vec<_>=samples.iter().filter(|row|row["max_level"]==level).collect();let mut report=serde_json::Map::new();report.insert("max_level".into(),level.into());
        for field in ["init_ms","polyline_ms","level1_ms","level2_ms","level3_ms","calculation_ms","output_ms"]{
            let mut values:Vec<_>=rows.iter().map(|row|row[field].as_f64().expect("timing number")).collect();values.sort_by(f64::total_cmp);
            report.insert(field.into(),serde_json::json!({"median":(values[4]+values[5])/2.0,"min":values[0],"max":values[9]}));
        }
        report.insert("points".into(),rows[0]["points"].clone());reports.push(report);
    }
    println!("{}",serde_json::json!({"symbol":symbol,"bars":bars,"warmups_per_mode":1,"samples_per_mode":10,"first_two_equal":true,"reports":reports,"samples":samples}));Ok(())
}
