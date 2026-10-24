//! 固定真实折线输入的偏移差分计时；冷算、追加、修订分开报告，不把结果缓存算作算法收益。
use std::{fs,time::Instant};
use wavequant_quant_rs::{analysis::kernel::{self,KernelPoint,KernelPointData,KernelFrame,KernelOptions,PointKind,PointState,TrendCursor},indexed_json::{Value,json}};
fn offset(input:&[KernelPoint])->usize{
    let Some(path)=input.last().map(|p|p.path)else{return 0;};
    let current:Vec<_>=input.iter().filter(|p|p.path==path).cloned().collect();
    kernel::six_state(&current,current.first().map_or(0,|p|p.level)).ok().and_then(|f|f.preceding_start).and_then(|p|input.iter().position(|x|x.index==p.index&&x.ordinal==p.ordinal&&x.kind==p.kind)).unwrap_or(0)
}
fn main()->Result<(),Box<dyn std::error::Error>>{
    let path=std::env::args().nth(1).ok_or("input required")?;let v:Value=wavequant_quant_rs::indexed_json::from_slice(&fs::read(path)?)?;
    let rows=v["bars"].as_array().ok_or("bars required")?;
    let input=v["state"]["data"]["polyline"].as_array().ok_or("points required")?.iter().filter(|p|p["confirmed"]==true&&p["referenceOnly"]!=true).map(|p|->Result<KernelPoint,Box<dyn std::error::Error>>{
        let i=p["barIndex"].as_u64().ok_or("bar index")? as usize;let b=rows.get(i).ok_or("bar bounds")?;let kind=if p["kind"]=="H"{PointKind::H}else{PointKind::L};
        Ok(KernelPoint::new(KernelPointData{index:i,ordinal:p["ordinal"].as_u64().ok_or("ordinal")? as usize,time:b["time"].as_str().ok_or("time")?.into(),kind,
            value:b[if kind==PointKind::H{"high"}else{"low"}].as_f64().ok_or("price")?,known_index:p["knownIndex"].as_u64().ok_or("known")? as usize,level:0,path:p["path"].as_u64().unwrap_or(0) as usize,state:PointState::Confirmed,evidence:Value::Null}))
    }).collect::<Result<Vec<_>,_>>()?;
    let mut reports=Vec::new();
    for scenario in ["cold","append","tail_revision","early_revision"]{
        let mut baseline:Option<Vec<KernelFrame>>=None;
        for enabled in [false,true]{let mut samples=Vec::new();let mut counts=None;
            for _ in 0..5{
                let mut options=KernelOptions::default();options.trend_offset_enabled=enabled;let mut cursor=TrendCursor::default();let mut views=Vec::new();
                match scenario{
                    "cold"=>views.push(input.clone()),
                    "append"=>{for end in input.len().saturating_sub(15)..=input.len(){views.push(input[..end].to_vec());}},
                    _=>{let asof=input.iter().map(|p|p.known_index).max().ok_or("empty")?;cursor.advance_from(&input,offset(&input),1,asof,&options)?;
                        for step in 1..=8{let mut changed=input.clone();let i=if scenario=="early_revision"{2}else{input.len()-1};let sign=if changed[i].kind==PointKind::H{1.0}else{-1.0};changed[i].value+=sign*step as f64*0.001;views.push(changed);}}
                }
                let offsets:Vec<_>=views.iter().map(|v|offset(v)).collect();let start=Instant::now();let mut output=Vec::new();
                for (v,o) in views.iter().zip(offsets){let asof=v.iter().map(|p|p.known_index).max().ok_or("empty")?;output.push(cursor.advance_from(v,o,1,asof,&options)?);}
                samples.push(start.elapsed().as_secs_f64()*1000.0);
                if let Some(expected)=&baseline{if expected!=&output{return Err(format!("offset changed output for {scenario}").into());}}else{baseline=Some(output);}
                counts=Some(wavequant_quant_rs::indexed_json::to_value(cursor.statistics())?);
            }
            samples.sort_by(f64::total_cmp);reports.push(json!({"scenario":scenario,"enabled":enabled,"medianMs":samples[2],"samplesMs":samples,"statistics":counts,"outputsEqual":true}));
        }
    }
    // 三个级别使用同一函数，检查同一批来源在不同level下的开关等价。
    for level in 1..=3{let mut source=input.clone();for p in &mut source{p.level=level-1;}let asof=source.iter().map(|p|p.known_index).max().ok_or("empty")?;let mut a=KernelOptions::default();a.trend_offset_enabled=false;
        if kernel::trend(&source,level,asof,&a)?!=kernel::trend(&source,level,asof,&KernelOptions::default())?{return Err("level mismatch".into());}}
    println!("{}",wavequant_quant_rs::indexed_json::to_string(&json!({"sourcePoints":input.len(),"reports":reports,"levelsEqual":true}))?);Ok(())
}
