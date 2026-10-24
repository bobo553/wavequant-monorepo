//! 因果复权与原始价格交易权限，禁止让将来除权事件重写历史。
use std::str::FromStr;
use rust_decimal::{Decimal, RoundingStrategy, prelude::ToPrimitive};
use serde::{Serialize,Deserialize};
use super::tdx::{DailyBar,DataError,is_stock,parse_date};

#[derive(Clone,Debug,Serialize,Deserialize)]
pub struct CorporateAction {
    pub symbol: String, pub date: String, pub category: u32,
    pub cash: f64, pub rights_price: f64, pub bonus: f64, pub rights: f64,
}
#[derive(Clone,Debug,Serialize,Deserialize)]
pub struct ExecutionBar {
    pub timestamp: String, pub symbol: String,
    pub open: f64, pub high: f64, pub low: f64, pub close: f64, pub volume: f64,
    pub adjustment_factor: f64, pub buyable: bool, pub sellable: bool,
    #[serde(default)] pub close_sellable: Option<bool>, #[serde(default)] pub nonflat_close_sellable: Option<bool>,
    #[serde(default)] pub close_buyable: Option<bool>, #[serde(default)] pub nonflat_close_buyable: Option<bool>,
    #[serde(default)] pub raw_is_st: Option<bool>, #[serde(default)] pub raw_trading_active: Option<bool>,
}
#[derive(Clone,Debug,Serialize)]
pub struct SecuritySpec {
    pub market: String, pub board: String, pub board_label: String,
    pub price_limit_rate: f64, pub minimum_buy_shares: u32, pub buy_share_step: u32,
}
fn invalid(message: &str) -> DataError { DataError::Invalid(message.into()) }
pub fn security_spec(symbol: &str,date: &str) -> Result<SecuritySpec,DataError> {
    parse_date(date)?;
    if !is_stock(symbol) { return Err(invalid("not a supported SH/SZ/BJ A-share symbol")); }
    let (market,board,label,rate,minimum,step) = if symbol.starts_with("sh.60") { ("sh","sh_main","沪市主板",0.10,100,100) }
        else if symbol.starts_with("sz.00") { ("sz","sz_main","深市主板",0.10,100,100) }
        else if symbol.starts_with("sz.30") { ("sz","chinext","创业板",if date >= "2020-08-24" {0.20} else {0.10},100,100) }
        else if symbol.starts_with("sh.68") { ("sh","star","科创板",0.20,200,1) }
        else { ("bj","bse","北交所",0.30,100,1) };
    Ok(SecuritySpec{market:market.into(),board:board.into(),board_label:label.into(),price_limit_rate:rate,minimum_buy_shares:minimum,buy_share_step:step})
}
fn decimal(value: f64) -> Result<Decimal,DataError> {
    if !value.is_finite() { return Err(invalid("non-finite price")); }
    Decimal::from_str(&value.to_string()).map_err(|_|invalid("price exceeds decimal range"))
}
pub fn price_limit_bounds(symbol: &str,date: &str,previous: f64,is_st: bool) -> Result<(f64,f64),DataError> {
    let spec = security_spec(symbol,date)?;
    if previous <= 0.0 { return Err(invalid("invalid previous close")); }
    let rate = if is_st && matches!(spec.board.as_str(),"sh_main" | "sz_main") {
        if date < "2026-07-06" { 0.05 } else { 0.10 }
    } else if is_st && spec.board == "chinext" && date < "2020-08-24" { 0.05 } else { spec.price_limit_rate };
    let previous = decimal(previous)?;
    let rate = decimal(rate)?;
    let lower = previous.checked_mul(Decimal::ONE - rate).ok_or_else(||invalid("price overflow"))?.round_dp_with_strategy(2,RoundingStrategy::MidpointAwayFromZero);
    let upper = previous.checked_mul(Decimal::ONE + rate).ok_or_else(||invalid("price overflow"))?.round_dp_with_strategy(2,RoundingStrategy::MidpointAwayFromZero);
    Ok((lower.to_f64().ok_or_else(||invalid("price overflow"))?,upper.to_f64().ok_or_else(||invalid("price overflow"))?))
}
pub fn opening_permissions(symbol: &str,date: &str,previous: f64,opening: f64,is_st: bool,active: bool) -> Result<(bool,bool),DataError> {
    if !active { return Ok((false,false)); }
    if !opening.is_finite() || opening <= 0.0 { return Err(invalid("invalid opening price")); }
    let (lower,upper) = price_limit_bounds(symbol,date,previous,is_st)?;
    Ok((!is_st && opening < upper,opening > lower))
}
pub fn action_symbol(market: u32,code: &str) -> Result<String,DataError> {
    let prefix = match market { 0 => "sz", 1 => "sh", 2 => "bj", _ => return Err(invalid("unsupported TDX corporate-action market")) };
    if code.len() != 6 || !code.bytes().all(|c|c.is_ascii_digit()) { return Err(invalid("invalid TDX action code")); }
    Ok(format!("{prefix}.{code}"))
}
pub fn ex_reference(previous: f64,event: &CorporateAction) -> Result<f64,DataError> {
    if [previous,event.cash,event.rights_price,event.bonus,event.rights].iter().any(|v|!v.is_finite()) { return Err(invalid("invalid corporate-action input")); }
    let value = (previous * 10.0 - event.cash + event.rights_price * event.rights) / (10.0 + event.bonus + event.rights);
    if !value.is_finite() || value <= 0.0 { return Err(invalid("invalid corporate-action reference price")); }
    Ok(value)
}
pub fn adjust_rows(raw: &[DailyBar],events: &[CorporateAction],start: &str,end: &str,symbol: &str) -> Result<Vec<ExecutionBar>,DataError> {
    if parse_date(start)? > parse_date(end)? || !is_stock(symbol) { return Err(invalid("invalid dates or symbol")); }
    let mut relevant = Vec::new();
    for event in events {
        parse_date(&event.date)?;
        if event.symbol == symbol && event.date.as_str() >= start && event.date.as_str() <= end {
            if event.category != 1 { return Err(invalid("unsupported split/consolidation")); }
            relevant.push(event);
        }
    }
    relevant.sort_by(|a,b|a.date.cmp(&b.date));
    let mut result = Vec::new();
    let mut factor = 1.0;
    let mut cursor = 0;
    let mut previous = None;
    let mut prior_date: Option<&str> = None;
    for row in raw {
        parse_date(&row.date)?;
        if prior_date.is_some_and(|d|d >= row.date.as_str()) { return Err(invalid("non-increasing raw dates")); }
        prior_date = Some(&row.date);
        if row.date.as_str() > end { break; }
        if row.date.as_str() < start { previous = Some(row.close); continue; }
        if [row.open,row.high,row.low,row.close].iter().any(|v|!v.is_finite() || *v <= 0.0) || row.low > row.open.min(row.close) || row.high < row.open.max(row.close) { return Err(invalid("invalid raw OHLC")); }
        let mut reference = previous.unwrap_or(row.open);
        while cursor < relevant.len() && relevant[cursor].date <= row.date {
            let next = ex_reference(reference,relevant[cursor])?;
            factor *= reference / next;
            reference = next;
            cursor += 1;
        }
        if !factor.is_finite() || factor <= 0.0 { return Err(invalid("invalid cumulative adjustment factor")); }
        let (buyable,sellable) = opening_permissions(symbol,&row.date,reference,row.open,false,true)?;
        let active = row.volume > 0;
        let (close_buyable,close_sellable) = opening_permissions(symbol,&row.date,reference,row.close,false,active)?;
        let nonflat = active && row.high > row.low;
        let nonflat_close_sellable = nonflat && opening_permissions(symbol,&row.date,reference,row.high,false,active)?.1;
        let nonflat_close_buyable = nonflat && opening_permissions(symbol,&row.date,reference,row.low,false,active)?.0;
        result.push(ExecutionBar{timestamp:row.date.clone(),symbol:symbol.into(),open:row.open*factor,high:row.high*factor,low:row.low*factor,close:row.close*factor,volume:row.volume as f64,adjustment_factor:factor,buyable,sellable,close_sellable:Some(close_sellable),nonflat_close_sellable:Some(nonflat_close_sellable),close_buyable:Some(close_buyable),nonflat_close_buyable:Some(nonflat_close_buyable),raw_is_st:Some(false),raw_trading_active:Some(active)});
        previous = Some(row.close);
    }
    Ok(result)
}
