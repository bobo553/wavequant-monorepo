//! 封存历史诊断入口。实现原功能，但只在调用者显式启动任务时执行。
use std::{collections::{BTreeMap, BTreeSet}, fs, path::Path};
use rust_decimal::Decimal;
use serde::{Deserialize, Serialize};
use crate::indexed_json::{Value, json};
use crate::analysis::{analyzer::AnalyzerConfig, system::run_system_signals, trading::config::TradingDayConfig};
use super::{AnalyticsResult, evidence_statistics, float, number, research, runner};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ValidationFold {
    pub fold: usize,
    pub train: [String; 2],
    pub validation: [String; 2],
    pub test: [String; 2],
    pub gap_sessions: usize,
    pub test_sessions: usize,
}

pub fn walk_forward_folds(sessions: &[String], train: usize, validation: usize, test: usize, gap: usize) -> AnalyticsResult<Vec<ValidationFold>> {
    if train == 0 || validation == 0 || test == 0 { return Err("train, validation and test lengths must be positive".into()); }
    let days = sessions;
    if days.windows(2).any(|pair| pair[0] >= pair[1]) { return Err("unique ordered sessions required".into()); }
    for day in days { crate::data::tdx::parse_date(day).map_err(|e| e.to_string())?; }
    let mut start = train.checked_add(gap).and_then(|n| n.checked_add(validation)).and_then(|n| n.checked_add(gap)).ok_or("fold lengths overflow")?;
    let mut folds = Vec::new();
    while start.checked_add(test).is_some_and(|end| end <= days.len()) {
        let validation_end = start - gap;
        let validation_start = validation_end - validation;
        let train_end = validation_start - gap;
        folds.push(ValidationFold { fold: folds.len(), train: [days[0].clone(), days[train_end - 1].clone()], validation: [days[validation_start].clone(), days[validation_end - 1].clone()], test: [days[start].clone(), days[start + test - 1].clone()], gap_sessions: gap, test_sessions: test });
        start += test;
    }
    Ok(folds)
}

fn annotation_keys(rows: &[Value]) -> AnalyticsResult<BTreeSet<[String; 3]>> {
    let mut keys = BTreeSet::new();
    for row in rows {
        let read = |field: &str| row[field].as_str().filter(|v| !v.is_empty()).map(str::to_owned).ok_or_else(|| format!("annotation {field} must be a nonempty string"));
        let key = [read("symbol")?, read("timestamp")?, read("event")?];
        if !keys.insert(key) { return Err("duplicate annotation identity".into()); }
    }
    Ok(keys)
}

pub fn audit_annotations(expected: &[Value], observed: &[Value]) -> AnalyticsResult<Value> {
    let gold = annotation_keys(expected)?;
    let predicted = annotation_keys(observed)?;
    if gold.is_empty() { return Ok(json!({"status":"unavailable_no_independent_annotations","precision":null,"recall":null})); }
    let tp = gold.intersection(&predicted).count();
    Ok(json!({"status":"measured","true_positive":tp,"false_positive":predicted.len()-tp,"false_negative":gold.len()-tp,
        "precision":if predicted.is_empty(){None}else{Some(tp as f64/predicted.len() as f64)},"recall":tp as f64/gold.len() as f64,
        "missed":gold.difference(&predicted).collect::<Vec<_>>(),"extra":predicted.difference(&gold).collect::<Vec<_>>()}))
}

fn read_protocol(path: &Path) -> AnalyticsResult<Value> {
    if fs::metadata(path).map_err(|e| e.to_string())?.len() > 16 * 1024 * 1024 { return Err("validation protocol exceeds size bound".into()); }
    let bytes = fs::read(path).map_err(|e| e.to_string())?;
    let value: Value = crate::indexed_json::from_slice(bytes.strip_prefix(&[239, 187, 191]).unwrap_or(&bytes)).map_err(|e| e.to_string())?;
    if !value.is_object() { return Err("validation protocol must be an object".into()); }
    Ok(value)
}

fn attribution(result: &Value, symbols: impl Iterator<Item=String>) -> AnalyticsResult<Value> {
    let mut totals: BTreeMap<String, (usize, Decimal)> = symbols.map(|symbol| (symbol, (0, Decimal::ZERO))).collect();
    for trade in result["trades"].as_array().ok_or("trade ledger missing")? {
        let symbol = trade["symbol"].as_str().ok_or("closed trade symbol missing")?;
        let pnl = super::dec(&trade["pnl"]).ok_or("closed trade pnl missing")?;
        let entry = totals.entry(symbol.to_owned()).or_default(); entry.0 += 1; entry.1 += pnl;
    }
    totals.into_iter().map(|(symbol, (trades, pnl))| Ok((symbol, json!({"trades":trades,"pnl":float(pnl)?})))).collect::<AnalyticsResult<BTreeMap<_,_>>>().map(|v| json!(v))
}

pub fn run_validation_suite(csv_path: &Path, protocol_path: &Path, output: &Path) -> AnalyticsResult<Value> {
    let protocol = read_protocol(protocol_path)?;
    let metadata = read_protocol(&csv_path.with_extension("metadata.json"))?;
    let actual_hash = crate::data::tdx::fingerprint(csv_path).map_err(|e| e.to_string())?;
    if metadata["sha256"].as_str() != Some(actual_hash.as_str()) { return Err("data provenance hash mismatch".into()); }
    let input = runner::inputs(csv_path, true, &runner::RunnerSources::default())?;
    let sessions: Vec<_> = input.grouped.values().flatten().map(|b| b.timestamp.clone()).collect::<BTreeSet<_>>().into_iter().collect();
    let last = sessions.last().ok_or("validation input is empty")?;
    if protocol["data_end"].as_str() != Some(last.as_str()) { return Err("data_end must equal the frozen dataset's final session".into()); }
    let execution = runner::parameters(&protocol["execution"])?;
    let folds = walk_forward_folds(&sessions, 756, 252, 126, execution.max_hold_bars)?;
    let mut settings = BTreeMap::new();
    for name in ["strict_full", "proxy_full"] {
        let mut settings_value = json!({"entry_policy":"legacy_n_continuation","preflight_reward_risk":false,"squeeze_pullback_entries":false});
        if !protocol["strategy"].is_object() || !protocol["variants"][name].is_object() { return Err(format!("strategy and {name} configuration are required")); }
        super::merge(&mut settings_value, &protocol["strategy"]); super::merge(&mut settings_value, &protocol["variants"][name]);
        let setting: TradingDayConfig = crate::indexed_json::from_value(settings_value).map_err(|e| e.to_string())?;
        setting.validate()?; settings.insert(name, setting);
    }
    if output.exists() && fs::read_dir(output).map_err(|e| e.to_string())?.next().is_some() { return Err("validation output must be empty".into()); }
    fs::create_dir_all(output).map_err(|e| e.to_string())?;
    research::write_json(&output.join("protocol.json"), &protocol)?;
    let mut report = json!({"kind":"previously_seen_history_fixed_rule_diagnostics","selection":"none","folds":folds,"variants":{},"protocol":protocol,
        "data_sha256":input.metadata["sha256"],"calendar_kind":"observed_union_not_official_exchange_calendar","independent_holdout":false,
        "production_ready":false,"statistical_evidence":"not_established","annotation_audit":audit_annotations(&[], &[])?,
        "warning":"Each test interval starts in cash; end positions are marked, not forcibly sold. Independent interval curves must not be concatenated."});
    for (name, setting) in settings {
        let mut signals = Vec::new();
        for bars in input.grouped.values() {
            let config = AnalyzerConfig { n_target_trend_confirmation_enabled: setting.n_target_trend_confirmation_enabled, trading: setting.clone(), ..AnalyzerConfig::default() };
            signals.extend(run_system_signals(bars, config)?.signals);
        }
        signals.sort_by(|a,b| (a["timestamp"].as_str(),a["symbol"].as_str(),a["side"].as_str()).cmp(&(b["timestamp"].as_str(),b["symbol"].as_str(),b["side"].as_str())));
        runner::write_signals(&output.join(name), &signals)?;
        let mut fold_results = Vec::new();
        for fold in &folds {
            let result = runner::portfolio_values(&input, &signals, &execution, &fold.test[0], &fold.test[1], &setting)?;
            research::save_result(&output.join(name).join("folds").join(fold.fold.to_string()), &result)?;
            fold_results.push(json!({"fold":fold.fold,"metrics":result["metrics"],"diagnostics":result["backtest"]["diagnostics"]}));
        }
        let mut double = execution.clone(); double.commission_bps_per_side *= Decimal::TWO; double.minimum_commission *= Decimal::TWO; double.slippage_bps_per_side *= Decimal::TWO;
        let mut triple = execution.clone(); triple.commission_bps_per_side *= Decimal::from(3); triple.minimum_commission *= Decimal::from(3); triple.slippage_bps_per_side *= Decimal::from(3);
        let mut half = execution.clone(); half.max_participation /= Decimal::TWO;
        let mut scenarios = json!({});
        let mut row = json!({"strategy":setting,"folds":fold_results,"scenarios":{}});
        for (scenario, parameters) in [("base", execution.clone()), ("cost_2x", double), ("cost_3x", triple), ("capacity_half", half)] {
            parameters.validate()?;
            let result = runner::portfolio_values(&input, &signals, &parameters, &input.first, last, &setting)?;
            research::save_result(&output.join(name).join(scenario), &result)?;
            scenarios[scenario] = json!({"execution":parameters,"metrics":result["metrics"],"diagnostics":result["backtest"]["diagnostics"]});
            if scenario == "base" {
                row["bootstrap"] = evidence_statistics::block_bootstrap(result["equity"].as_array().ok_or("equity missing")?, float(execution.initial_capital)?, 20260908)?;
                row["bootstrap"]["warning"] = json!("Exploratory return-resampling only; not selection-corrected evidence. Sparse trades cannot establish an edge.");
                row["closed_trade_attribution"] = attribution(&result, input.grouped.keys().cloned())?;
                let minimum = protocol["gate"]["minimum_train_trades"].as_u64().ok_or("minimum_train_trades gate missing")?;
                row["minimum_trade_gate_passed"] = json!(number(&result["metrics"]["trades"]).ok_or("trade count missing")? >= minimum as f64);
            }
        }
        row["scenarios"] = scenarios;
        report["variants"][name] = row;
    }
    research::write_json(&output.join("report.json"), &report)?;
    Ok(report)
}
