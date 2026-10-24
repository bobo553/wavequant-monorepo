//! 与趋势结构独立的量价观察；沿用 Python 的显式政策与可知时钟。
pub mod price_action;
pub mod regime;
pub mod control;
pub mod washout;
pub mod strength;
pub mod turn;
pub use super::trading::entries::{medium_bullish_body,medium_bearish_body,strong_bullish_candle,
    bullish_breakout_rejection,volume_high_open_rising,volume_gap_follow_through,double_volume_bullish_breakout,
    is_volume_bearish,is_bearish_inside_child,bearish_body_engulfs,volume_bearish_buy_rejection,
    volume_bearish_entry_rejection,is_long_upper_shadow,volume_upper_shadow_rejection};
