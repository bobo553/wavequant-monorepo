use super::journal::{ExecutionResult, require};
use chrono::{DateTime, NaiveDate, Utc};
use rust_decimal::{Decimal, RoundingStrategy};
use serde::{Deserialize, Serialize};

pub fn money(value: Decimal) -> Decimal { value.round_dp_with_strategy(2, RoundingStrategy::MidpointAwayFromZero) }
pub fn session(at: DateTime<Utc>) -> NaiveDate { (at + chrono::Duration::hours(8)).date_naive() }
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum Side { Buy, Sell }
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SecurityFact {
    pub symbol: String, pub sector: String, pub active: bool, pub suspended: bool, pub st: bool,
    pub buy_lot: u64, pub settlement_days: u8, pub tick_size: Decimal, pub source: String,
    pub known_at: DateTime<Utc>, pub effective_from: NaiveDate, pub effective_to: Option<NaiveDate>,
}
impl SecurityFact {
    pub fn visible(&self, at: DateTime<Utc>) -> bool { self.known_at <= at && self.effective_from <= session(at) && self.effective_to.is_none_or(|end| session(at) <= end) }
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AShareSpec { pub board: String, pub price_limit_rate: Decimal, pub minimum_buy_shares: u64, pub buy_share_step: u64 }
/// 来源 a_share_security.py；历史 ST 必须由调用方明确提供。
pub fn a_share_spec(symbol: &str, day: NaiveDate) -> ExecutionResult<AShareSpec> {
    let (market, code) = symbol.split_once('.').unwrap_or(("", ""));
    require(code.len() == 6 && code.bytes().all(|c| c.is_ascii_digit()), "unsupported_a_share_symbol")?;
    let (board, rate, minimum, step) = match (market, &code[..2]) {
        ("sh", "60") => ("sh_main", 10, 100, 100), ("sz", "00") => ("sz_main", 10, 100, 100),
        ("sz", "30") => ("chinext", if day.to_string().as_str() >= "2020-08-24" {20} else {10}, 100, 100),
        ("sh", "68") => ("star", 20, 200, 1), ("bj", "43" | "82" | "83" | "87" | "88" | "92") => ("bse", 30, 100, 1),
        _ => return Err(super::ExecutionError("unsupported_a_share_symbol".into())),
    };
    Ok(AShareSpec { board: board.into(), price_limit_rate: Decimal::new(rate, 2), minimum_buy_shares: minimum, buy_share_step: step })
}
pub fn price_limit_bounds(symbol: &str, day: NaiveDate, previous_close: Decimal, st: bool) -> ExecutionResult<(Decimal, Decimal)> {
    require(previous_close > Decimal::ZERO, "positive_preclose_required")?;
    let spec = a_share_spec(symbol, day)?;
    let date = day.to_string();
    let rate = if st && matches!(spec.board.as_str(), "sh_main" | "sz_main") { Decimal::new(if date.as_str() < "2026-07-06" {5} else {10}, 2) }
        else if st && spec.board == "chinext" && date.as_str() < "2020-08-24" { Decimal::new(5, 2) } else { spec.price_limit_rate };
    Ok((money(previous_close * (Decimal::ONE-rate)), money(previous_close * (Decimal::ONE+rate))))
}
pub fn opening_permissions(symbol: &str, day: NaiveDate, open: Decimal, previous_close: Decimal, active: bool, st: bool) -> ExecutionResult<(bool, bool)> {
    require(open > Decimal::ZERO, "positive_open_required")?;
    if !active { return Ok((false, false)); }
    let (lower, upper) = price_limit_bounds(symbol, day, previous_close, st)?;
    Ok((!st && open < upper, open > lower))
}
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct FeeConfig { pub commission_bps: Decimal, pub minimum_commission: Decimal, pub a_share_taxes: bool }
impl Default for FeeConfig {
    fn default() -> Self { Self { commission_bps: Decimal::from(3), minimum_commission: Decimal::from(5), a_share_taxes: true } }
}
/// analytics/backtest.transaction_fee 的日期边界；原函数不在这里额外按分取整。
pub fn transaction_fee(notional: Decimal, day: NaiveDate, side: Side, config: &FeeConfig) -> ExecutionResult<Decimal> {
    require(notional >= Decimal::ZERO && config.commission_bps >= Decimal::ZERO && config.commission_bps < Decimal::from(10_000) && config.minimum_commission >= Decimal::ZERO, "invalid_fee_parameters")?;
    let mut fee = config.minimum_commission.max(notional * config.commission_bps / Decimal::from(10_000));
    if config.a_share_taxes {
        let date = day.to_string();
        fee += notional * Decimal::new(if date.as_str() < "2022-04-29" {2} else {1}, 5);
        if side == Side::Sell { fee += notional * if date.as_str() < "2023-08-28" { Decimal::new(1,3) } else { Decimal::new(5,4) }; }
    }
    Ok(fee)
}
