//! 离线同输入交易开关对照；不访问服务、行情源或生产缓存。
use std::{fs,env,time::Instant};
use serde_json::{Value,json};
use sha2::{Digest,Sha256};
use wavequant_quant_rs::{analysis::{analyzer::AnalyzerConfig,system::{SystemConfig,run_system_backtest}},execution::BacktestSession};

fn main()->Result<(),Box<dyn std::error::Error>> {
    let args:Vec<_>=env::args().collect();
    let input=fs::read(&args[1])?;
    let raw:Value=serde_json::from_slice(&input)?;
    if args[2]=="prepare" {
        let minute_raw:Value=serde_json::from_slice(&fs::read(&args[4])?)?;
        let mut sessions=Vec::new();let mut rejected=Vec::new();
        for value in raw["bars"].as_array().ok_or("bars missing")? {
            let mut value=value.clone();let date=value["timestamp"].as_str().ok_or("timestamp missing")?[..10].to_owned();value["timestamp"]=json!(date);
            let daily=serde_json::from_value(value)?;
            let mut minutes=None;
            if let Some(rows)=minute_raw["sessions"][&date].as_array() {
                let parsed=rows.iter().map(|row|->Result<_,Box<dyn std::error::Error>> {
                    let t=row["time"].as_str().ok_or("minute time missing")?;
                    let number=|key:&str|->Result<f64,Box<dyn std::error::Error>>{Ok(row[key].as_str().ok_or("minute number missing")?.parse()?)};
                    Ok(wavequant_quant_rs::data::minute::MinuteBar{timestamp:format!("{}T{}:{}:00+08:00",date,&t[8..10],&t[10..12]),open:number("open")?,high:number("high")?,low:number("low")?,close:number("close")?,volume:number("volume")?})
                }).collect::<Result<Vec<_>,_>>()?;
                match wavequant_quant_rs::data::minute::verify_minute_day(&parsed,&daily){Ok(())=>minutes=Some(parsed),Err(e)=>rejected.push(json!({"date":date,"reason":e.to_string()}))}
            }
            let present=minutes.is_some();sessions.push(BacktestSession{daily,source:"frozen_baostock".into(),data_version:"601086-20261020".into(),minutes,minute_source:present.then(||"frozen_baostock".into()),minute_data_version:present.then(||"601086-20261020".into())});
        }
        let config=SystemConfig::default();
        fs::write(&args[3],serde_json::to_vec(&json!({"sessions":sessions,"config":config,"rejected_minutes":rejected,"config_scope":"current Rust defaults; not historical UI settings"}))?)?;
        return Ok(());
    }
    let sessions:Vec<BacktestSession>=serde_json::from_value(raw["sessions"].clone())?;
    let mut config:SystemConfig=serde_json::from_value(raw["config"].clone())?;
    config.analyzer.mode=if args[2]=="trading" {AnalyzerConfig::default().for_trading().mode}else{AnalyzerConfig::default().mode};
    let config_hash=format!("{:x}",Sha256::digest(serde_json::to_vec(&config)?));
    let started=Instant::now();
    let mut progress_seconds=0.0;let mut progress_bytes=0usize;let mut checkpoints=Vec::new();
    let result=run_system_backtest(&sessions,config,None,|p|{
        let phase=Instant::now();
        // 计入真实进度JSON编码，排除HTTP、压缩和接收端图表投影。
        progress_bytes+=serde_json::to_vec(p).expect("progress serialization").len();
        progress_seconds+=phase.elapsed().as_secs_f64();
        if p.percent%10==0 {eprintln!("{} {}% {} {:.3}s",args[2],p.percent,p.processed,started.elapsed().as_secs_f64());checkpoints.push(json!({"percent":p.percent,"processed":p.processed,"seconds":started.elapsed().as_secs_f64()}));}
    })?;
    let run_seconds=started.elapsed().as_secs_f64();
    let phase=Instant::now();let bytes=serde_json::to_vec(&result)?;let encode_seconds=phase.elapsed().as_secs_f64();
    fs::write(&args[3],&bytes)?;
    let mut common=std::collections::BTreeMap::new();
    for (symbol,bars) in &result.bars {
        let response=if let Some(indexed)=result.indexed_analysis.get(symbol){indexed.chart_response(bars)?}else{result.analysis[symbol].clone()};
        let geometry:Vec<_>=response.levels.iter().map(|level|level.points.iter().map(|p|json!([p.index,p.kind,p.value,p.ordinal,p.path,p.known_index,p.state])).collect::<Vec<_>>()).collect();
        common.insert(symbol,json!({"geometry":geometry,"qualification_intervals":response.qualification_intervals,"n_records":response.n_records,"n_memory":response.n_memory}));
    }
    fs::write(format!("{}.common.json",args[3]),serde_json::to_vec(&common)?)?;
    println!("{}",json!({"mode":args[2],"input_sha256":format!("{:x}",Sha256::digest(&input)),"config_sha256":config_hash,
        "engine":wavequant_quant_rs::infrastructure::computation::engine_identity(),"days":sessions.len(),"minute_days":sessions.iter().filter(|s|s.minutes.is_some()).count(),
        "run_seconds":run_seconds,"analysis_seconds":result.analysis_elapsed_seconds,"progress_encode_seconds":progress_seconds,"progress_bytes":progress_bytes,
        "result_encode_seconds":encode_seconds,"result_bytes":bytes.len(),"phase_seconds":result.analysis_timings,"orders":result.account.orders.len(),"trades":result.account.trades.len(),
        "signals":result.signals.len(),"unavailable_rules":result.unavailable_rules,"checkpoints":checkpoints,
        "peak_rss_bytes":wavequant_quant_rs::infrastructure::computation::process_memory::peak_rss_bytes(std::process::id())}));
    Ok(())
}
