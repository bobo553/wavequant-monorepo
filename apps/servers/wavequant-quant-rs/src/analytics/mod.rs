//! 已发生账户结果的纯分析与原生导出；不触发策略、行情请求或回测。
pub mod metrics;
pub mod evidence_statistics;
pub mod execution_diagnostics;
pub mod screening_funnel;
pub mod quality;
pub mod holding_drawdown;
pub mod trade_evidence;
pub mod research;
pub mod notion;
pub mod foundation_audit;
pub mod system_research;
pub mod runner;
pub mod research_panel;
pub mod strategy_evidence;
pub mod validation_suite;
use rust_decimal::{Decimal,prelude::ToPrimitive};
use crate::indexed_json::Value;
pub type AnalyticsResult<T> = Result<T,String>;
pub(crate) fn dec(value:&Value)->Option<Decimal>{match (value).kind() {Some(crate::indexed_json::Kind::String(s))=>s.parse().ok(),Some(crate::indexed_json::Kind::Number(n))=>n.to_string().parse().ok(),_=>None}}
pub(crate) fn number(value:&Value)->Option<f64>{value.as_f64().or_else(||value.as_str()?.parse().ok()).filter(|v|v.is_finite())}
pub(crate) fn float(value:Decimal)->AnalyticsResult<f64>{value.to_f64().filter(|v|v.is_finite()).ok_or_else(||"analytics decimal outside finite range".into())}
pub(crate) fn merge(target:&mut Value,other:&Value){if let(Some(a),Some(b))=(target.as_object_mut(),other.as_object()){for(k,v)in b{a.insert(k.clone(),v.clone());}}}
pub(crate) fn date(value:&str)->&str{value.get(..10).unwrap_or(value)}
