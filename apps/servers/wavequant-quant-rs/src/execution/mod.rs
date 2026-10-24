//! 原生纸面订单与逐开盘执行；策略判断由调用方提供，账本不生成信号。
mod account;
mod engine;
mod journal;
mod market;
mod venue;
mod strategy_backtest;
pub(crate) use strategy_backtest::source_candle;
mod stream;
mod intent;
pub use account::*;
pub use engine::*;
pub use journal::{ExecutionError, ExecutionResult,JournalInspection,inspect_execution_journal};
pub use market::*;
pub use venue::*;
pub use strategy_backtest::*;
pub use stream::*;
pub use intent::*;
