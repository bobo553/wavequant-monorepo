//! 获准后用冻结输入核对资格；无行情源、账户、HTTP或生产缓存访问。
use std::{fs,path::Path,time::Instant};
use wavequant_quant_rs::{analysis::analyzer::{AnalysisRequest,TrendAnalyzer},indexed_json};

fn main()->Result<(),Box<dyn std::error::Error>>{
    let args:Vec<_>=std::env::args().collect();
    let input=fs::read(args.get(1).ok_or("input path required")?)?;
    let out=Path::new(args.get(2).ok_or("output directory required")?);fs::create_dir_all(out)?;
    let mut request:AnalysisRequest=indexed_json::from_slice(&input)?;request.state=None;
    if let Some(end)=args.get(3){request.bars.truncate(end.parse::<usize>()?+1);}
    let started=Instant::now();let mut analyzer=TrendAnalyzer::new(request)?;
    while analyzer.advance_polyline_index()?{}analyzer.finish_polyline_array()?;
    for level in 1..=3{analyzer.finish_level_array(level)?;}
    analyzer.finish_qualification_arrays(None)?;
    let analysis_ms=started.elapsed().as_secs_f64()*1000.0;
    let result=analyzer.indexed_response();
    fs::write(out.join("indexed.json"),indexed_json::to_vec(&result)?)?;
    fs::write(out.join("summary.json"),serde_json::to_vec_pretty(&serde_json::json!({"version":result.state.version,
        "analysis_ms":analysis_ms,"phases":analyzer.analysis_timings(),"intervals":result.qualification_intervals}))?)?;
    #[cfg(feature="qualification-timing")]
    fs::write(out.join("qualification-inputs.json"),indexed_json::to_vec(&analyzer.qualification_inputs())?)?;
    Ok(())
}
