//! strategy_evidence.py：冻结候选、实际信号、同日期块统计及只用训练段选择。
use super::{AnalyticsResult,runner::{self,RunnerSources},research,research_panel,evidence_statistics,validation_suite,number,float};
use crate::{analysis::{analyzer::AnalyzerConfig,trading::{RuleCandle,config::TradingDayConfig},baseline::Signal},
    execution::{StreamBar,TrendStreamStrategy,StreamSignalStrategy,SignalSide},persistence::*};
use rust_decimal::Decimal;
use crate::indexed_json::{Value,json};
use std::{collections::{BTreeMap,BTreeSet},path::Path,fs};
fn err(e:impl std::fmt::Display)->String{e.to_string()}
fn fail(ok:bool,reason:&str)->AnalyticsResult<()>{if ok{Ok(())}else{Err(reason.into())}}
fn integer(value:&Value,name:&str)->AnalyticsResult<usize>{value[name].as_u64().and_then(|n|usize::try_from(n).ok()).ok_or_else(||format!("missing integer {name}"))}
fn decimal(value:&Value)->Option<Decimal>{super::dec(value)}
pub fn strategy_configs(protocol:&Value)->AnalyticsResult<BTreeMap<String,TradingDayConfig>>{
    let candidates=protocol["candidates"].as_object().filter(|v|!v.is_empty()).ok_or("nonempty candidates required")?;
    let mut configs=BTreeMap::new();for(name,changes)in candidates.iter().map(|(n,v)|(n.as_str(),&v["changes"])).chain(std::iter::once(("strict_reference",&protocol["strict_reference"]))){
        identifier(name).map_err(err)?;let mut config=protocol["strategy"].clone();fail(config.is_object()&&changes.is_object(),"strategy and changes must be objects")?;
        super::merge(&mut config,changes);let config:TradingDayConfig=crate::indexed_json::from_value(config).map_err(err)?;config.validate()?;
        fail(config.entry_policy=="transitioned_squeeze"&&config.regime_filter,"study must retain transitioned squeeze entry policy")?;configs.insert(name.into(),config);}
    Ok(configs)
}
fn generate(symbol:&str,bars:&[RuleCandle],config:&TradingDayConfig,source_version:&str)->AnalyticsResult<(Vec<Signal>,Vec<Value>)>{
    let mut strategy=TrendStreamStrategy::new(AnalyzerConfig{trading:config.clone(),n_target_trend_confirmation_enabled:config.n_target_trend_confirmation_enabled,..AnalyzerConfig::default()},
        source_version.into()).map_err(err)?;let mut prefix=Vec::with_capacity(bars.len());let mut signals=vec![];
    for(i,b)in bars.iter().enumerate(){let timestamp=crate::data::dataset::market_close(&b.timestamp).map_err(err)?;
        prefix.push(StreamBar{timestamp,symbol:symbol.into(),open:b.open,high:b.high,low:b.low,close:b.close,volume:b.volume,adjustment_factor:b.adjustment_factor,raw_is_st:b.raw_is_st,raw_trading_active:b.raw_trading_active});
        for s in strategy.generate(&prefix).map_err(err)?{let trigger=s.trigger_timestamp.to_rfc3339();
            let(retracement,regime)=if s.side==SignalSide::Long{(s.evidence.get("retracement").or_else(||s.evidence.get("counter_ratio")).and_then(decimal).ok_or("LONG retracement evidence missing")?,
                s.evidence.get("regime").and_then(Value::as_str).ok_or("LONG regime evidence missing")?.to_string())}else{(Decimal::ZERO,"risk_exit".into())};
            signals.push(Signal{timestamp:b.timestamp.clone(),symbol:symbol.into(),bar_index:i,side:if s.side==SignalSide::Long{"LONG"}else{"EXIT"}.into(),reference_price:s.reference_price,
                invalidation_price:s.invalidation_price,reason:s.reason,trigger_timestamp:trigger,retracement,
                rvol:s.evidence.get("rvol").and_then(decimal),regime,target_price:s.target_price,minimum_reward_risk:s.minimum_reward_risk});}
    }Ok((signals,strategy.audit(symbol)))
}
struct SymbolResult{symbol:String,signals:Vec<Signal>,events:BTreeMap<String,usize>,reasons:BTreeMap<String,usize>,checks:Vec<Value>,counts:Value}
fn evaluate_symbol(name:&str,symbol:&str,bars:&[RuleCandle],config:&TradingDayConfig,check_prefix:bool,source_version:&str)->AnalyticsResult<SymbolResult>{
    let(signals,audit)=generate(symbol,bars,config,source_version)?;let mut checks=vec![];
    if check_prefix{for stop in [bars.len()/2,bars.len()*3/4]{if stop==0{continue;}let(prefix,_)=generate(symbol,&bars[..stop],config,source_version)?;
        let passed=prefix==signals.iter().filter(|s|s.bar_index<stop).cloned().collect::<Vec<_>>();checks.push(json!({"candidate":name,"symbol":symbol,"bars":stop,"passed":passed}));fail(passed,"future suffix altered a dated signal")?;}}
    let mut events=BTreeMap::<String,usize>::new();let mut reasons=BTreeMap::<String,usize>::new();for event in &audit{if let Some(kind)=event["event"].as_str(){*events.entry(kind.into()).or_default()+=1;
        if matches!(kind,"entry_rejected"|"entry_preflight_rejected"){if let Some(reason)=event["reason"].as_str(){*reasons.entry(reason.into()).or_default()+=1;}}}}
    let counts=json!({"long_signals":signals.iter().filter(|s|s.side=="LONG").count(),"exit_signals":signals.iter().filter(|s|s.side=="EXIT").count(),"audit_observations":audit.len()});
    Ok(SymbolResult{symbol:symbol.into(),signals,events,reasons,checks,counts})
}
fn traded(result:&Value)->usize{result["trades"].as_array().into_iter().flatten().filter_map(|t|t["symbol"].as_str()).collect::<BTreeSet<_>>().len()}
fn scores(result:&Value,stressed:&Value)->Value{json!({"trades":result["metrics"]["trades"],"traded_symbols":traded(result),"total_return":result["metrics"]["total_return"],"cost_2x_return":stressed["metrics"]["total_return"]})}
fn actual_period(input:&runner::Inputs,signals:&[Signal],parameters:&crate::analysis::profiles::ExecutionParameters,begin:&str,end:&str,n_target:bool)->AnalyticsResult<Value>{
    runner::portfolio_with_confirmation(input,signals,parameters,begin,end,n_target)
}
pub fn run_strategy_evidence(root:&Path,protocol_path:&Path,output:&Path,tests:&Value,workers:usize)->AnalyticsResult<Value>{
    fail(tests["status"]=="passed","engineering test gate must pass")?;fail((1..=8).contains(&workers),"workers must be between 1 and 8")?;
    fs::create_dir_all(output).map_err(err)?;for p in fs::read_dir(output).map_err(err)?{fail(p.map_err(err)?.file_name()=="test_log.txt","new experiment directory required")?;}
    let protocol_path=protocol_path.canonicalize().map_err(err)?;let protocol:Value=crate::indexed_json::from_slice(&fs::read(&protocol_path).map_err(err)?).map_err(err)?;
    let configs=strategy_configs(&protocol)?;let mut periods=BTreeMap::<String,[String;2]>::new();let mut previous=None::<String>;
    let first=protocol["data_start"].as_str().ok_or("data_start missing")?;let last=protocol["data_end"].as_str().ok_or("data_end missing")?;
    for label in ["development","validation","diagnostic"]{let span=protocol["periods"][label].as_array().filter(|v|v.len()==2).ok_or("period missing")?;
        let begin=span[0].as_str().ok_or("period begin missing")?;let end=span[1].as_str().ok_or("period end missing")?;
        crate::data::tdx::parse_date(begin).and_then(|_|crate::data::tdx::parse_date(end)).map_err(err)?;
        fail(begin<=end&&begin>=first&&end<=last&&previous.as_deref().is_none_or(|p|begin>p),"ordered covered nonoverlapping study periods required")?;
        periods.insert(label.into(),[begin.into(),end.into()]);previous=Some(end.into());}
    write_json(&output.join("protocol_frozen.json"),&protocol).map_err(err)?;
    let(path,metadata,universe)=research_panel::build_research_panel(root,&output.join("panel"),&protocol)?;fail(metadata["end"]==protocol["data_end"],"panel does not reach fixed endpoint")?;
    let input=runner::inputs(&path,true,&RunnerSources::default())?;let execution=runner::parameters(&protocol["execution"])?;let stress=runner::stress(&execution);stress.validate()?;
    let source_root=Path::new(env!("CARGO_MANIFEST_DIR"));let source_files:Vec<_>=directory_files(&source_root.join("src")).map_err(err)?.into_iter().map(|p|p.strip_prefix(source_root).map(Path::to_owned).map_err(err)).collect::<AnalyticsResult<_>>()?;
    let environment=environment_snapshot(source_root,&source_files).map_err(err)?;let mut store=EventStore::open(&output.join("experiments.sqlite"),false).map_err(err)?;
    let run_id="squeeze-evidence-v5";let mut hashes=BTreeMap::new();for source in [&protocol_path,&path,&path.with_extension("metadata.json"),&output.join("panel/universe_frozen.json")]{hashes.insert(source.canonicalize().map_err(err)?.to_string_lossy().into_owned(),fingerprint(source).map_err(err)?);}
    ExperimentRegistry{store:&mut store}.start(run_id,chrono::Utc::now(),protocol.clone(),&hashes,environment.clone()).map_err(err)?;write_json(&output.join("environment.json"),&environment).map_err(err)?;
    let computed=(||->AnalyticsResult<Value>{
        let mut candidates=BTreeMap::<String,Value>::new();let mut all_signals=BTreeMap::<String,Vec<Signal>>::new();let mut development=BTreeMap::new();let mut paths=BTreeMap::<String,Vec<f64>>::new();
        let mut dates=None::<Vec<String>>;let mut prefix_checks=vec![];let jobs:Vec<_>=input.grouped.iter().collect();let source_version=input.metadata["sha256"].as_str().ok_or("frozen data identity missing")?;
        for(name,config)in &configs{let mut signals=vec![];let mut events=BTreeMap::<String,usize>::new();let mut reasons=BTreeMap::<String,usize>::new();let mut counts=BTreeMap::new();
            // 有界独立实例并行；按固定股票顺序收集，完成时间不参与候选排序。
            for(batch_index,batch)in jobs.chunks(workers).enumerate(){let batch_results=std::thread::scope(|scope|{
                let handles:Vec<_>=batch.iter().enumerate().map(|(index,(symbol,bars))|scope.spawn(move||evaluate_symbol(name,symbol,bars,config,batch_index==0&&index==0,source_version))).collect();
                handles.into_iter().map(|h|h.join().map_err(|_|"strategy evidence worker panicked".to_string())?).collect::<AnalyticsResult<Vec<_>>>()
            })?;
                for r in batch_results{signals.extend(r.signals);counts.insert(r.symbol,r.counts);for(k,v)in r.events{*events.entry(k).or_default()+=v;}for(k,v)in r.reasons{*reasons.entry(k).or_default()+=v;}prefix_checks.extend(r.checks);}
            }
            signals.sort_by(|a,b|(&a.timestamp,&a.symbol,&a.side).cmp(&(&b.timestamp,&b.symbol,&b.side)));
            let values=signals.iter().map(crate::indexed_json::to_value).collect::<Result<Vec<_>,_>>().map_err(err)?;runner::write_signals(&output.join(name),&values)?;
            write_json(&output.join(name).join("stock_counts.json"),&counts).map_err(err)?;
            let mut row=json!({"config":config,"event_counts":events,"rejection_observations":reasons,"count_unit":"dated_observations_not_independent_trades","periods":{},"stress":{}});
            for(label,span)in &periods{let r=actual_period(&input,&signals,&execution,&span[0],&span[1],config.n_target_trend_confirmation_enabled)?;let s=actual_period(&input,&signals,&stress,&span[0],&span[1],config.n_target_trend_confirmation_enabled)?;
                research::save_result(&output.join(name).join(label),&r)?;research::save_result(&output.join(name).join(format!("{label}_cost2x")),&s)?;
                let pnl:BTreeMap<_,_>=input.grouped.keys().map(|symbol|{let value=r["trades"].as_array().into_iter().flatten().filter(|t|t["symbol"].as_str()==Some(symbol.as_str())).filter_map(|t|number(&t["pnl"])).sum::<f64>();(symbol.clone(),value)}).collect();
                row["periods"][label]=json!({"metrics":r["metrics"],"diagnostics":r["backtest"]["diagnostics"],"traded_symbols":traded(&r),"closed_trade_pnl_by_symbol":pnl});row["stress"][label]=s["metrics"].clone();
                if name!="strict_reference"&&label=="development"{development.insert(name.clone(),scores(&r,&s));}
                if name!="strict_reference"&&label=="diagnostic"{let(d,values)=evidence_statistics::equity_returns(r["equity"].as_array().ok_or("equity missing")?,float(execution.initial_capital)?)?;
                    fail(dates.as_ref().is_none_or(|old|*old==d),"unaligned candidate return dates")?;dates=Some(d);paths.insert(name.clone(),values);}
            }
            all_signals.insert(name.clone(),signals);write_json(&output.join(name).join("candidate_checkpoint.json"),&row).map_err(err)?;candidates.insert(name.clone(),row);
            write_json(&output.join("prefix_checks.json"),&prefix_checks).map_err(err)?;
        }
        let statistics_config=&protocol["statistics"];let block=integer(statistics_config,"block_sessions")?;let replications=integer(statistics_config,"replications")?;
        let seed=statistics_config["seed"].as_u64().ok_or("statistics seed missing")?;let alpha=number(&statistics_config["alpha"]).ok_or("statistics alpha missing")?;
        let statistics=evidence_statistics::joint_block_test(&paths,block,replications,seed,alpha)?;let control=paths.get("proxy_control").ok_or("proxy_control missing")?;
        let deltas: BTreeMap<_,_>=paths.iter().filter(|(n,_)|n.as_str()!="proxy_control").map(|(name,values)|(name.clone(),values.iter().zip(control).map(|(a,b)|a-b).collect())).collect();
        let incremental=evidence_statistics::joint_block_test(&deltas,block,replications,seed,alpha)?;let minimum_trades=integer(statistics_config,"minimum_closed_trades")?;let minimum_symbols=integer(statistics_config,"minimum_symbols_with_closed_trades")?;
        let selected=evidence_statistics::choose_development(&development,minimum_trades,minimum_symbols)?;let mut gates=BTreeMap::new();
        for name in protocol["candidates"].as_object().ok_or("candidates missing")?.keys(){let row=&candidates[name];let d=&row["periods"]["diagnostic"];
            gates.insert(name.clone(),evidence_statistics::evidence_gate(&d["metrics"],&statistics["candidates"][name],integer(d,"traded_symbols")?,number(&row["stress"]["diagnostic"]["total_return"]).ok_or("stress return missing")?,statistics_config,false,false,false,false)?);}
        let sessions:Vec<_>=input.grouped.values().flatten().map(|b|b.timestamp.clone()).collect::<BTreeSet<_>>().into_iter().collect();
        let folds=validation_suite::walk_forward_folds(&sessions,756,252,126,execution.max_hold_bars)?;let mut rolling=vec![];
        for fold in folds{let mut train=BTreeMap::new();for name in protocol["candidates"].as_object().ok_or("candidates missing")?.keys(){let signals=&all_signals[name];
            let n_target=configs[name].n_target_trend_confirmation_enabled;let r=actual_period(&input,signals,&execution,&fold.train[0],&fold.train[1],n_target)?;let s=actual_period(&input,signals,&stress,&fold.train[0],&fold.train[1],n_target)?;train.insert(name.clone(),scores(&r,&s));}
            let choice=evidence_statistics::choose_development(&train,minimum_trades,minimum_symbols)?;let chosen=all_signals.get(&choice).map(Vec::as_slice).unwrap_or(&[]);
            fail(choice=="CASH"||all_signals.contains_key(&choice),"unknown selected candidate")?;
            let n_target=configs.get(&choice).is_some_and(|config|config.n_target_trend_confirmation_enabled);
            let validation=actual_period(&input,chosen,&execution,&fold.validation[0],&fold.validation[1],n_target)?;let test=actual_period(&input,chosen,&execution,&fold.test[0],&fold.test[1],n_target)?;
            let destination=output.join("rolling").join(fold.fold.to_string());research::save_result(&destination.join("validation"),&validation)?;research::save_result(&destination.join("test"),&test)?;
            let mut row=crate::indexed_json::to_value(fold).map_err(err)?;super::merge(&mut row,&json!({"selected_from_train_only":choice,"train_scores":train,"validation_metrics":validation["metrics"],"test_metrics":test["metrics"],"diagnostic":test["backtest"]["diagnostics"]}));rolling.push(row);
        }
        for(label,span)in &periods{research::save_result(&output.join("equal_weight_gross").join(label),&runner::benchmark(&input.grouped,&span[0],&span[1],execution.initial_capital)?)?;}
        let report=json!({"protocol":protocol,"data":metadata,"universe_summary":{"selected":universe["selected"].as_array().map_or(0,Vec::len),"imported":input.grouped.len(),"eligible":universe["eligible_symbols"].as_array().map_or(0,Vec::len),"quarantine":metadata["quarantine"]},
            "engineering_tests":tests,"workers":workers,"worker_backend":"bounded_native_threads_independent_analyzers","prefix_checks":prefix_checks,"candidates":candidates,"development_scores":development,"selected_for_forward_observation":selected,
            "family_statistics":statistics,"incremental_vs_control":incremental,"gates":gates,"rolling":rolling,"benchmark":"equal_weight_gross fixed local basket; not fee/risk matched, not market alpha",
            "validated":false,"production_ready":false,"default_strategy_changed":false,"required_next_evidence":["historical_ST_suspension_delist_PIT_universe","actual_minor_path_or_independent_proxy_annotations","complete_trial_history","new_unseen_forward_data_after_freeze"]});
        research::report_files(output,&report)?;
        let artifacts:Vec<_>=directory_files(output).map_err(err)?.into_iter().filter(|p|!p.file_name().map(|n|n.to_string_lossy().contains(".sqlite")).unwrap_or(false)).collect();
        ExperimentRegistry{store:&mut store}.finish(run_id,chrono::Utc::now(),&artifacts,"COMPLETED",None).map_err(err)?;Ok(report)
    })();
    if computed.is_err()&&store.get(&format!("experiment:end:{run_id}")).map_err(err)?.is_none(){ExperimentRegistry{store:&mut store}.finish(run_id,chrono::Utc::now(),&[],"FAILED",Some("strategy_evidence_failed")).map_err(err)?;}
    computed
}
