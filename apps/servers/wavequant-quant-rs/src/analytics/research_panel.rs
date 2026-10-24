//! research_panel.py：读取首个本地日期后按固定哈希抽样，收益不可参与名单冻结。
use super::AnalyticsResult;
use crate::{data::{tdx,gbbq,adjustments,exchange},persistence};
use crate::indexed_json::{Value,json};
use std::{collections::{BTreeMap,BTreeSet},path::{Path,PathBuf},fs::{self,File},io::Read};
pub const DEFAULT_SYMBOLS:[&str;10]=["sh.600036","sh.601318","sh.600519","sh.600900","sh.600104","sz.000651","sz.000333","sz.000858","sh.600276","sz.002415"];
fn err(e:impl std::fmt::Display)->String{e.to_string()}
pub fn freeze_universe(root:&Path,start:&str,size:usize,seed:&str,required:&[String])->AnalyticsResult<Value>{
    tdx::parse_date(start).map_err(err)?;if size==0||seed.is_empty(){return Err("positive sample size and fixed seed required".into());}let root=root.canonicalize().map_err(err)?;
    let mut eligible=BTreeMap::<String,Value>::new();let mut excluded=vec![];
    for(market,prefix)in [("sh","60"),("sz","00")]{let directory=root.join("vipdoc").join(market).join("lday");if !directory.is_dir(){continue;}
        let mut paths=fs::read_dir(directory).map_err(err)?.map(|e|e.map(|e|e.path()).map_err(err)).collect::<AnalyticsResult<Vec<_>>>()?;paths.sort();
        for path in paths{let Some(name)=path.file_name().and_then(|n|n.to_str())else{continue;};
            if name.len()!=12||!name.starts_with(&format!("{market}{prefix}"))||!name.ends_with(".day")||!name[2..8].bytes().all(|b|b.is_ascii_digit()){continue;}
            let symbol=format!("{market}.{}",&name[2..8]);
            let found=(||->AnalyticsResult<Value>{let resolved=path.canonicalize().map_err(err)?;if !resolved.starts_with(&root){return Err("local source escaped root".into());}
                let mut record=[0_u8;32];File::open(&resolved).map_err(err)?.read_exact(&mut record).map_err(|_|"missing first daily record".to_string())?;
                let raw=u32::from_le_bytes([record[0],record[1],record[2],record[3]]);let day=chrono::NaiveDate::from_ymd_opt((raw/10000)as i32,(raw/100)%100,raw%100).ok_or("invalid first local date")?.to_string();
                if day.as_str()>=start{return Err("local_history_not_before_start".into());}Ok(json!({"path":resolved,"first_local_date":day}))})();
            match found{Ok(value)=>{eligible.insert(symbol,value);},Err(reason)=>excluded.push(json!({"symbol":symbol,"reason":reason}))}
        }
    }
    let required:BTreeSet<_>=required.iter().cloned().collect();if required.len()>size||!required.iter().all(|s|eligible.contains_key(s)){return Err("required symbols unavailable or sample too small".into());}
    let mut ordered:Vec<_>=eligible.keys().filter(|s|!required.contains(*s)).cloned().collect();ordered.sort_by_cached_key(|s|persistence::sha256(format!("{seed}:{s}")));
    let mut selected=required.clone();selected.extend(ordered.into_iter().take(size-required.len()));if selected.len()!=size{return Err("insufficient eligible local histories".into());}
    let sources:BTreeMap<_,_>=selected.iter().map(|s|(s.clone(),eligible[s].clone())).collect();
    Ok(json!({"seed":seed,"size":size,"selection_uses_returns":false,"required":required,"selected":selected,"selected_sources":sources,
        "eligible_symbols":eligible.keys().collect::<Vec<_>>(),"exclusions":excluded,"historical_universe_complete":false,
        "warning":"Local file coverage is not a security master; survivor and availability bias remain."}))
}
pub fn build_research_panel(root:&Path,output:&Path,protocol:&Value)->AnalyticsResult<(PathBuf,Value,Value)>{
    if output.exists()&&fs::read_dir(output).map_err(err)?.next().is_some(){return Err("new panel directory required".into());}fs::create_dir_all(output).map_err(err)?;
    let start=protocol["data_start"].as_str().ok_or("data_start missing")?;let end=protocol["data_end"].as_str().ok_or("data_end missing")?;
    tdx::parse_date(end).map_err(err)?;if start>end{return Err("invalid panel dates".into());}
    let u=&protocol["universe"];let size=u["size"].as_u64().and_then(|n|usize::try_from(n).ok()).ok_or("universe size missing")?;
    let seed=u["seed"].as_str().ok_or("fixed sample seed missing")?;let required=if u["include_existing_ten"].as_bool().ok_or("explicit include_existing_ten required")?{DEFAULT_SYMBOLS.iter().map(|s|s.to_string()).collect()}else{vec![]};
    let universe=freeze_universe(root,start,size,seed,&required)?;persistence::write_json(&output.join("universe_frozen.json"),&universe).map_err(err)?;
    let(actions,action_hash)=gbbq::read_actions(&root.join("T0002/hq_cache/gbbq")).map_err(err)?;let mut rows=Vec::new();let mut sources=vec![];let mut quarantine=vec![];
    for symbol in universe["selected"].as_array().ok_or("frozen symbols missing")?{let symbol=symbol.as_str().ok_or("invalid frozen symbol")?;
        let path=PathBuf::from(universe["selected_sources"][symbol]["path"].as_str().ok_or("frozen source path missing")?);let source_hash=persistence::fingerprint(&path).map_err(err)?;
        let converted=(||->AnalyticsResult<Vec<exchange::ImportedBar>>{let raw=tdx::read_day(&path).map_err(err)?;let bars=adjustments::adjust_rows(&raw,&actions,start,end,symbol).map_err(err)?;
            if bars.is_empty(){return Err("no observations in research period".into());}if persistence::fingerprint(&path).map_err(err)?!=source_hash{return Err("source changed while importing".into());}
            Ok(bars.into_iter().map(Into::into).collect())})();
        match converted{Ok(bars)=>{sources.push(json!({"symbol":symbol,"file":path,"sha256":source_hash,"rows":bars.len(),"first":bars.first().map(|b|&b.timestamp),"last":bars.last().map(|b|&b.timestamp)}));rows.extend(bars);},
            Err(reason)=>quarantine.push(json!({"symbol":symbol,"sha256":source_hash,"reason":reason}))}
    }
    persistence::write_json(&output.join("quarantine.json"),&quarantine).map_err(err)?;if rows.is_empty(){return Err("no usable panel data; see quarantine".into());}
    let path=output.join("daily.csv");exchange::write_csv(&path,&rows).map_err(err)?;
    let metadata=json!({"kind":"real_market_exploratory_panel","source":"TDX local .day + gbbq","frequency":"daily","start":rows.iter().map(|b|&b.timestamp).min(),"end":rows.iter().map(|b|&b.timestamp).max(),
        "requested_start":start,"requested_end":end,"sources":sources,"gbbq_sha256":action_hash,"frozen_universe_sha256":persistence::fingerprint(&output.join("universe_frozen.json")).map_err(err)?,
        "quarantine":quarantine,"price_basis":"causal multiplicative adjusted equivalent units","sha256":persistence::fingerprint(&path).map_err(err)?,"rows":rows.len(),"bar_permission_schema":"source_close_sell_permissions_v2",
        "limitations":["Local hash sample is not a PIT universe; survivorship and availability bias remain.","No historical ST status; importer assumes established non-ST main-board permissions.",
            "Missing bars and unsupported actions are quarantined without replacement.","Adjusted units do not model dividend-tax or rights cash ledgers.","No minor timeframe, auction queue or independent holdout certification."]});
    persistence::write_json(&path.with_extension("metadata.json"),&metadata).map_err(err)?;Ok((path,metadata,universe))
}
