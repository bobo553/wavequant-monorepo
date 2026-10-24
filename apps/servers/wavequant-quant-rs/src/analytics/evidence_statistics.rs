//! 原 Python 联合日期区块重采样；所有候选使用相同抽样日期，保留横截面相关。
use super::{AnalyticsResult,number,date};
use crate::indexed_json::{Value,json};
use std::collections::BTreeMap;

use crate::infrastructure::seeded_random::Random;
pub fn equity_returns(curve:&[Value],capital:f64)->AnalyticsResult<(Vec<String>,Vec<f64>)>{
    if !capital.is_finite()||capital<=0.0{return Err("positive capital required".into());}
    let mut dates=vec![];let mut values=vec![];let mut previous=capital;
    for row in curve{let day=date(row["timestamp"].as_str().ok_or("equity date missing")?).to_string();
        let current=number(&row["equity"]).filter(|v|*v>0.0).ok_or("positive finite equity required")?;
        if dates.last().is_some_and(|last|day<=*last){return Err("unique ordered daily curve required".into());}
        dates.push(day);values.push(current/previous-1.0);previous=current;}Ok((dates,values))
}
fn quantile(values:&[f64],p:f64)->f64{let x=(values.len()-1)as f64*p;let lo=x.floor()as usize;let hi=(lo+1).min(values.len()-1);values[lo]+(values[hi]-values[lo])*(x-lo as f64)}
pub fn joint_block_test(series:&BTreeMap<String,Vec<f64>>,block:usize,replications:usize,seed:u64,alpha:f64)->AnalyticsResult<Value>{
    if series.is_empty()||block==0||replications<100||!alpha.is_finite()||!(0.0<alpha&&alpha<1.0){return Err("nonempty family, positive blocks, >=100 replications and alpha in (0,1) required".into());}
    let n=series.values().next().map_or(0,Vec::len);
    if series.values().any(|v|v.len()!=n||v.iter().any(|v|!v.is_finite())){return Err("finite aligned equal-length return paths required".into());}
    if n<3.max(block.saturating_mul(2)){return Ok(json!({"status":"unavailable_short_history","candidates":{},"family_size":series.len()}));}
    let names:Vec<_>=series.keys().collect();let means:Vec<_>=names.iter().map(|k|series[*k].iter().sum::<f64>()/n as f64).collect();
    let valid:Vec<_>=names.iter().enumerate().map(|(j,k)|series[*k].iter().map(|v|(v-means[j]).powi(2)).sum::<f64>()/n as f64>1e-30).collect();
    let prefixes:Vec<Vec<f64>>=names.iter().map(|k|{let mut p=vec![0.0];for v in series[*k].iter().chain(series[*k].iter()){p.push(p.last().copied().unwrap_or(0.0)+v);}p}).collect();
    let mut rng=Random::new(seed);let mut samples=vec![vec![];names.len()];let mut maxima=vec![];
    for _ in 0..replications{let mut totals=vec![0.0;names.len()];let mut remaining=n;
        while remaining>0{let start=rng.range(n);let length=block.min(remaining);remaining-=length;
            for j in 0..names.len(){totals[j]+=prefixes[j][start+length]-prefixes[j][start];}}
        let mut maximum=0.0_f64;for j in 0..names.len(){let value=totals[j]/n as f64;samples[j].push(value);if valid[j]{maximum=maximum.max(value-means[j]);}}maxima.push(maximum);}
    let mut result=BTreeMap::new();let tail=alpha/(2*names.len())as f64;
    for(j,name)in names.iter().enumerate(){samples[j].sort_by(f64::total_cmp);result.insert((*name).clone(),json!({
        "status":if valid[j]{"exploratory"}else{"unavailable_constant_returns"},"mean_daily_net_return":means[j],
        "active_return_sessions":series[*name].iter().filter(|v|v.abs()>1e-15).count(),
        "family_adjusted_p":if valid[j]{Some((1+maxima.iter().filter(|v|**v>=means[j]).count())as f64/(replications+1)as f64)}else{None},
        "bonferroni_mean_ci":if valid[j]{Some([quantile(&samples[j],tail),quantile(&samples[j],1.0-tail)])}else{None}}));}
    Ok(json!({"status":"exploratory","method":"joint_centered_circular_block_max_mean","family_size":names.len(),"sessions":n,
        "block_sessions":block,"replications":replications,"seed":seed,"alpha":alpha,"candidates":result,"complete_historical_trial_correction":false,
        "warning":"Same sampled dates across candidates preserve cross-correlation. Approximate stationarity/mixing assumptions; not DSR/SPA or a complete historical trial correction. Mean CI is daily arithmetic, not CAGR."}))
}
pub fn block_bootstrap(curve:&[Value],capital:f64,seed:u64)->AnalyticsResult<Value>{
    let(_,returns)=equity_returns(curve,capital)?;let n=returns.len();if n==0{return Ok(json!({"status":"unavailable"}));}
    let mut random=Random::new(seed);let mut estimates=vec![];
    for _ in 0..1000{let mut total=0.0;let mut length=0;while length<n{let start=random.range(n);let count=20.min(n-length);for j in 0..count{total+=returns[(start+j)%n];}length+=count;}estimates.push(total/n as f64*252.0);}
    estimates.sort_by(f64::total_cmp);Ok(json!({"status":"exploratory","seed":seed,"replications":1000,"block_sessions":20,
        "annualized_arithmetic_mean_ci95":[estimates[24],estimates[974]],"caveat":"Resamples the realized selected-strategy return path; selection bias and regime changes are not corrected."}))
}
pub fn choose_development(summaries:&BTreeMap<String,Value>,minimum_trades:usize,minimum_symbols:usize)->AnalyticsResult<String>{
    let mut eligible=vec![];for(name,s)in summaries{let trades=s["trades"].as_u64().ok_or("development trade count missing")?;
        let symbols=s["traded_symbols"].as_u64().ok_or("development symbol count missing")?;let total=number(&s["total_return"]).ok_or("development total return missing")?;
        let cost=number(&s["cost_2x_return"]).ok_or("development cost return missing")?;
        if trades>=minimum_trades as u64&&symbols>=minimum_symbols as u64&&total>0.0&&cost>0.0{eligible.push((name,total));}}
    eligible.sort_by(|(a,av),(b,bv)|bv.total_cmp(av).then(a.cmp(b)));Ok(eligible.first().map(|(s,_)|(*s).clone()).unwrap_or_else(||"CASH".into()))
}
pub fn evidence_gate(metrics:&Value,statistic:&Value,traded_symbols:usize,cost_return:f64,requirements:&Value,
    independent_holdout:bool,pit_universe:bool,actual_theory_path:bool,complete_trial_history:bool)->AnalyticsResult<Value>{
    let requirement=|key:&str|->AnalyticsResult<f64>{number(&requirements[key]).ok_or_else(||format!("evidence requirement missing: {key}"))};
    let p=number(&statistic["family_adjusted_p"]);let ci=statistic["bonferroni_mean_ci"].as_array().and_then(|a|a.first()).and_then(number);
    let research=json!({"trades":number(&metrics["trades"]).ok_or("trade metric missing")?>=requirement("minimum_closed_trades")?,
        "symbols":traded_symbols as f64>=requirement("minimum_symbols_with_closed_trades")?,
        "active_sessions":number(&statistic["active_return_sessions"]).unwrap_or(0.0)>=requirement("minimum_active_sessions")?,
        "positive_net":number(&metrics["total_return"]).ok_or("return metric missing")?>0.0,"cost_stress":cost_return>0.0,
        "family_adjusted_significance":p.is_some_and(|p|0.0<=p&&p<requirement("alpha").unwrap_or(0.0)),"positive_confidence_lower_bound":ci.is_some_and(|v|v>0.0)});
    let external=json!({"independent_holdout":independent_holdout,"point_in_time_universe":pit_universe,"actual_theory_path":actual_theory_path,"complete_historical_trial_record":complete_trial_history});
    let passed=research.as_object().is_some_and(|m|m.values().all(|v|v==true));let verified=external.as_object().is_some_and(|m|m.values().all(|v|v==true));
    Ok(json!({"research_checks":research,"external_checks":external,"research_passed":passed,"validated":passed&&verified,
        "reason":"Conditional evidence for the declared scope only; external evidence is supplied explicitly and never inferred from returns."}))
}
