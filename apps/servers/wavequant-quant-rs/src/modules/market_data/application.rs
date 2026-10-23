use super::Bar;
use std::{error::Error, io};

pub type SourceError = Box<dyn Error + Send + Sync>;

/// 端口由用例定义，文件与未来数据库适配器不得把协议对象传入领域层。
pub trait BarSource {
    fn load(&self) -> Result<Vec<Bar>, SourceError>;
}

#[derive(Debug, PartialEq, Eq)]
pub struct Inspection {
    pub bars: usize,
    pub first_timestamp_ms: u64,
    pub last_timestamp_ms: u64,
}

pub fn inspect(source: &impl BarSource) -> Result<Inspection, SourceError> {
    let bars = source.load()?;
    let first = bars
        .first()
        .ok_or_else(|| invalid("market data must not be empty"))?;
    let mut previous_timestamp = None;
    for bar in &bars {
        bar.validate()?;
        if previous_timestamp.is_some_and(|previous| bar.timestamp_ms <= previous) {
            return Err(invalid("bar timestamps must be strictly increasing").into());
        }
        previous_timestamp = Some(bar.timestamp_ms);
    }
    Ok(Inspection {
        bars: bars.len(),
        first_timestamp_ms: first.timestamp_ms,
        last_timestamp_ms: previous_timestamp.ok_or_else(|| invalid("missing final timestamp"))?,
    })
}

fn invalid(message: &'static str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message)
}
