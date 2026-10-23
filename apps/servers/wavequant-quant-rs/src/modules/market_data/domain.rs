use std::{error::Error, fmt};

/// 单一标的、周期、复权口径的数据批次；价格采用同一 tick 单位，避免浮点金额。
#[derive(Debug, Clone, Copy)]
pub struct Bar {
    pub timestamp_ms: u64,
    pub open_ticks: u64,
    pub high_ticks: u64,
    pub low_ticks: u64,
    pub close_ticks: u64,
    pub volume: u64,
}

#[derive(Debug, Clone, Copy)]
pub struct InvalidBar;

impl fmt::Display for InvalidBar {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .write_str("bar requires a positive timestamp, positive prices and valid OHLC bounds")
    }
}

impl Error for InvalidBar {}

impl Bar {
    pub fn validate(&self) -> Result<(), InvalidBar> {
        if self.timestamp_ms == 0
            || self.low_ticks == 0
            || self.low_ticks > self.high_ticks
            || !(self.low_ticks..=self.high_ticks).contains(&self.open_ticks)
            || !(self.low_ticks..=self.high_ticks).contains(&self.close_ticks)
        {
            return Err(InvalidBar);
        }
        Ok(())
    }
}
