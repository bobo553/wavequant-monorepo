//! Python test_trade_rule_dispatch / test_integrated_strategy 的调度及成交边界。
use rust_decimal::Decimal;
use wavequant_quant_rs::{
    analysis::{
        analyzer::{AnalysisMode, AnalyzerConfig},
        system::{SystemConfig, run_system_backtest, run_system_backtest_with_signals},
        trading::{
            dispatch::{DecisionScope, SignalHistory},
            qualification::{TradingEligibility, TradingInterval},
        },
    },
    data::adjustments::ExecutionBar,
    execution::{BacktestSession, ExecutionMode, Side},
    indexed_json::{Value, json},
};

fn qualification() -> TradingEligibility {
    let mut eligibility = TradingEligibility::new(2).unwrap();
    eligibility.intervals.push(TradingInterval {
        trend_level: 2, source_path: "lecture-0".into(), start_index: 2,
        start_flip_index: 1, end_index: Some(4), end_flip_index: None,
        end_reason: Some("source_bear_trend_confirmed".into()),
        start_low_index: 1, start_low_ordinal: 0, start_low_price: Some(Decimal::from(8)),
        end_key_index: None, end_key_price: None, confirmation_gap: None, gap_replaced_index: None,
    });
    eligibility
}

#[test]
fn qualification_end_stops_strategy_scans_and_cannot_consume_a_buy() {
    let eligibility = qualification();
    let history = SignalHistory::default();
    let end = DecisionScope::at(&eligibility, 4, false);
    assert!(!history.observes_exits(end));
    assert!(!history.permits_entry(end, false));
    let active = DecisionScope::at(&eligibility, 3, false);
    assert!(history.permits_entry(active, false));
    assert!(!history.permits_entry(active, true));
}

#[test]
fn prior_buy_does_not_resume_strategy_scans_after_qualification_closes() {
    let eligibility = qualification();
    let outside = DecisionScope::at(&eligibility, 5, false);
    let mut history = SignalHistory::default();
    assert!(!history.observes_exits(outside));
    history.record_buy_signal();
    assert!(!history.observes_exits(outside));
    assert!(!history.permits_entry(outside, false));
    let restored: SignalHistory = serde_json::from_str(&serde_json::to_string(&history).unwrap()).unwrap();
    assert!(!restored.observes_exits(DecisionScope::at(&eligibility, 6, false)));
}

fn sessions() -> Vec<BacktestSession> {
    (0..4).map(|index| BacktestSession {
        daily: ExecutionBar {
            symbol: "sh.600000".into(), timestamp: format!("2026-01-{:02}", 5 + index),
            open: 10.0, high: 10.2, low: 9.8, close: 10.0, volume: 1_000_000.0,
            adjustment_factor: 1.0, buyable: true, sellable: true,
            close_buyable: Some(true), close_sellable: Some(true),
            nonflat_close_buyable: Some(true), nonflat_close_sellable: Some(true),
            raw_trading_active: Some(true), raw_is_st: Some(false),
        },
        source: "migration_fixture".into(), data_version: "frozen-v1".into(),
        minutes: None, minute_source: None, minute_data_version: None,
    }).collect()
}

fn config() -> SystemConfig {
    let mut config = SystemConfig::default();
    config.analyzer.trading.buy_interval_level = 0;
    config.parameters.entry_at_close = false;
    config.parameters.staged_exit_intraday = false;
    config.parameters.consolidation_entry_intraday = false;
    config.execution.net_reward_risk_filter = false;
    config.execution.exit_on_target = false;
    config
}

#[test]
fn frozen_signals_cannot_bypass_an_inactive_qualification_window() {
    let mut qualified = config();
    qualified.analyzer.trading.buy_interval_level = 2;
    let result = run_system_backtest_with_signals(
        &sessions(), qualified, &[signal(0,"LONG"), signal(2,"EXIT")], None, |_| {},
    ).unwrap();
    assert!(result.account.orders.is_empty());
    assert!(result.account.positions.is_empty());
    assert!(result.signals.is_empty());
    assert_eq!(result.account.equity.len(), sessions().len());
}

#[test]
fn signals_before_the_requested_start_are_neither_recorded_nor_matched() {
    let mut scoped = config();
    scoped.trading_start = Some("2026-01-07".into());
    let result = run_system_backtest_with_signals(
        &sessions(), scoped, &[signal(0,"LONG"), signal(2,"LONG")], None, |_| {},
    ).unwrap();
    assert!(result.signals.iter().all(|row| row["timestamp"].as_str().is_some_and(|at| at >= "2026-01-07")));
    let fills:Vec<_> = result.account.orders.iter().filter(|order| order.side == Side::Buy && order.status == "filled").collect();
    assert_eq!(fills.len(), 1);
    assert_eq!(fills[0].timestamp.date_naive().to_string(), "2026-01-08");
}

fn signal(index: usize, side: &str) -> Value {
    json!({"symbol":"sh.600000","timestamp":format!("2026-01-{:02}",5+index),
        "bar_index":index,"side":side,"reason":"python_frozen_fixture",
        "reference_price":"10","invalidation_price":"8","target_price":"15",
        "minimum_reward_risk":"0","execution_model":"next_open","evidence":{}})
}

#[test]
fn frozen_daily_signals_reach_the_rust_account_on_the_next_open() {
    let result = run_system_backtest_with_signals(
        &sessions(), config(), &[signal(0,"LONG"), signal(2,"EXIT")], None, |_| {},
    ).unwrap();
    let buy = result.account.orders.iter().find(|o|o.side==Side::Buy && o.status=="filled").unwrap();
    let sell = result.account.orders.iter().find(|o|o.side==Side::Sell && o.status=="filled").unwrap();
    assert_eq!(buy.mode, ExecutionMode::NextOpen);
    assert_eq!(buy.timestamp.date_naive().to_string(), "2026-01-06");
    assert_eq!(sell.timestamp.date_naive().to_string(), "2026-01-08");
    assert!(result.account.positions.is_empty());
    assert!(result.account.total_fees > Decimal::ZERO);
    assert!(result.analysis["sh.600000"].daily_trading.as_ref().unwrap().trading_applicable);
}

#[test]
fn exit_preempts_a_frozen_buy_even_without_a_position() {
    let result = run_system_backtest_with_signals(
        &sessions(), config(), &[signal(0,"LONG"), signal(0,"EXIT")], None, |_| {},
    ).unwrap();
    assert!(!result.account.orders.iter().any(|o|o.side==Side::Buy && o.status=="filled"));
    assert!(result.account.positions.is_empty());
}

#[test]
fn structure_requests_keep_their_account_empty() {
    let mut config = config();
    config.analyzer = AnalyzerConfig::default();
    assert_eq!(config.analyzer.mode, AnalysisMode::Structure);
    let result = run_system_backtest(&sessions(), config, None, |_| {}).unwrap();
    assert!(result.account.orders.is_empty());
    assert!(result.account.positions.is_empty());
    assert!(result.signals.is_empty());
    assert!(result.indexed_analysis.contains_key("sh.600000"));
}
