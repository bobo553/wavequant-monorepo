//! 指数身份独立于A股代码；不能把沪市000001误当深市000001个股。
use super::{tdx::{is_stock,MarketData},adjustments::ExecutionBar};
pub fn is_index(symbol:&str)->bool {
    symbol.len()==9&&symbol.as_bytes()[3..].iter().all(u8::is_ascii_digit)
        &&(symbol.starts_with("sh.000")||symbol.starts_with("sz.399")||symbol.starts_with("sz.980")||symbol.starts_with("bj.899"))
}
pub fn supported(source:&str,symbol:&str)->bool{is_stock(symbol)||(source=="akshare"&&is_index(symbol))}
/// 固定完整历史来源；不在同一指数快照中拼接或自动切换供应商。
pub fn upstream(symbol:&str)->&'static str{if symbol.starts_with("bj."){"eastmoney"}else{"sina"}}
pub fn endpoint(symbol:&str)->&'static str{if symbol.starts_with("bj."){"stock_zh_index_daily_em"}else{"stock_zh_index_daily"}}
pub fn cache_policy(symbol:&str)->&'static str{if symbol.starts_with("bj."){"akshare-eastmoney-index-kline-v1"}else{"akshare-sina-index-kline-v2"}}
pub const COMMON:[(&str,&str);10]=[
    ("sh.000001","上证指数"),("sz.399001","深证成指"),("sz.399006","创业板指"),("sh.000300","沪深300"),
    ("sh.000016","上证50"),("sh.000905","中证500"),("sh.000852","中证1000"),("sh.000688","科创50"),
    ("sz.399005","中小100"),("bj.899050","北证50"),
];
/// 指数无个股公司行动复权，不具备直接成交权限；仅供行情和结构消费者。
pub fn execution_bars(market:&MarketData,start:&str)->Vec<ExecutionBar>{
    market.bars.iter().filter(|bar|bar.date.as_str()>=start).map(|bar|ExecutionBar{
        timestamp:bar.date.clone(),symbol:market.symbol.clone(),open:bar.open,high:bar.high,low:bar.low,close:bar.close,
        volume:bar.volume as f64,adjustment_factor:1.0,buyable:false,sellable:false,close_sellable:Some(false),
        nonflat_close_sellable:Some(false),close_buyable:Some(false),nonflat_close_buyable:Some(false),raw_is_st:None,raw_trading_active:None,
    }).collect()
}
#[cfg(test)]
mod tests{
    use super::*;
    #[test]fn exchanges_distinguish_index_and_equity(){assert!(is_index("sh.000001"));assert!(!is_stock("sh.000001"));assert!(!is_index("sz.000001"));assert!(is_stock("sz.000001"));assert!(supported("akshare","bj.899050"));assert!(!supported("tdx","sh.000001"));assert!(!is_index("sh.00000x"));}
    #[test]fn index_has_no_corporate_adjustment_or_trade_permission(){let m=MarketData{symbol:"sh.000001".into(),asof:"2026-01-02".into(),version:"x".into(),bars:vec![super::super::tdx::DailyBar{date:"2026-01-02".into(),open:3000.0,high:3100.0,low:2900.0,close:3050.0,volume:10000,amount:None}]};let bars=execution_bars(&m,"2026-01-01");assert_eq!(bars[0].close,3050.0);assert_eq!(bars[0].adjustment_factor,1.0);assert!(!bars[0].buyable&&!bars[0].sellable);}
}
