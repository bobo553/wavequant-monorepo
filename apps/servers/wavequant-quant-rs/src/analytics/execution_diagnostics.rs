use crate::execution::{StrategyBacktest,Side};
use crate::indexed_json::{Value,json};
use std::collections::BTreeMap;
pub fn execution_diagnostics(result:&StrategyBacktest)->Value{
    let buys:Vec<_>=result.orders.iter().filter(|o|o.side==Side::Buy).collect();
    let fills=buys.iter().filter(|o|o.status=="filled").count();let mut rejected=BTreeMap::<String,usize>::new();
    for order in &buys{if order.status=="cancelled"{*rejected.entry(order.reason.clone()).or_default()+=1;}}
    json!({"status":if buys.is_empty(){"no_entry_signals_reached_execution"}else if fills==0{"all_entry_attempts_rejected"}
        else if result.trades.is_empty(){"open_positions_without_closed_trades"}else{"trades_observed"},
        "entry_attempts":buys.len(),"entry_fills":fills,"closed_trades":result.trades.len(),"open_positions":result.positions.len(),
        "rejection_reasons":rejected,"warning":"No fills or too few trades cannot establish profitability; inspect constraints, do not force orders."})
}
