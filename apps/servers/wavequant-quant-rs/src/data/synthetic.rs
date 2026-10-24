//! 原离线演示数据。模拟交易日和证券身份始终显式标记，不进入真实行情适配器。
use std::path::Path;
use chrono::{Datelike, Duration, NaiveDate, Weekday};
use crate::indexed_json::{Value, json};
use super::{exchange::{self, ImportedBar}, tdx::{DataError, fingerprint}};
use crate::infrastructure::seeded_random::Random;

pub fn synthetic_dataset(path: &Path, seed: u64, sessions: usize, symbols: usize) -> Result<Value, DataError> {
    if sessions < 50 || symbols == 0 || sessions.checked_mul(symbols).is_none_or(|n| n > 2_000_000) {
        return Err(DataError::Invalid("synthetic dataset requires >=50 sessions, >=1 symbol and <=2000000 rows".into()));
    }
    let mut random = Random::new(seed);
    let mut days = Vec::with_capacity(sessions);
    let mut current = NaiveDate::from_ymd_opt(2018, 1, 2).ok_or_else(|| DataError::Invalid("invalid synthetic epoch".into()))?;
    while days.len() < sessions {
        if !matches!(current.weekday(), Weekday::Sat | Weekday::Sun) { days.push(current.to_string()); }
        current = current.checked_add_signed(Duration::days(1)).ok_or_else(|| DataError::Invalid("synthetic calendar overflow".into()))?;
    }
    let market: Vec<_> = days.iter().map(|_| random.gauss(0.0, 0.007)).collect();
    let mut bars = Vec::with_capacity(sessions*symbols);
    for symbol in 0..symbols {
        let mut price = 12.0 + symbol as f64 * 4.0;
        for (i, day) in days.iter().enumerate() {
            let drift = [0.002, -0.0015, 0.0, 0.0008][((i+symbol*17)/90)%4];
            let open = (price*(1.0+random.gauss(0.0, 0.004))).max(1.0);
            let movement = drift + 0.5*market[i] + random.gauss(0.0, 0.013);
            let close = (open*(1.0+movement)).max(1.0);
            let spread = random.gauss(0.008, 0.003).abs();
            let volume = (2_000_000.0*random.log_normal(0.0, 0.3)*(1.0+movement.abs()*30.0)).trunc();
            bars.push(ImportedBar { timestamp: day.clone(), symbol: format!("SYN{symbol:03}"), open, high: open.max(close)*(1.0+spread), low: open.min(close)*(1.0-spread), close, volume,
                buyable:true,sellable:true,adjustment_factor:1.0,close_buyable:None,nonflat_close_buyable:None,close_sellable:None,nonflat_close_sellable:None,raw_is_st:None,raw_trading_active:None });
            price = close;
        }
    }
    exchange::write_csv(path, &bars)?;
    let metadata = json!({"kind":"synthetic","source":"seeded simulation","seed":seed,"frequency":"daily","start":days[0],"end":days[sessions-1],
        "rows":bars.len(),"sha256":fingerprint(path)?,"symbols":(0..symbols).map(|n|format!("SYN{n:03}")).collect::<Vec<_>>(),"csv":path.file_name().map(|n|n.to_string_lossy()),
        "limitations":["Engineering validation only; simulated weekday calendar."],"random_generator":"CPython_integer_seed_MT19937_gauss_normalvariate"});
    exchange::write_json(path.with_extension("metadata.json"), &metadata)?;
    Ok(metadata)
}
