use crate::modules::market_data::{Bar, BarSource};
use serde::Deserialize;
use std::{
    fs::File,
    io::{self, Read},
    path::PathBuf,
};

const MAX_INPUT_BYTES: u64 = 16 * 1024 * 1024;

pub struct JsonBarSource {
    pub path: PathBuf,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct BarRecord {
    timestamp_ms: u64,
    open_ticks: u64,
    high_ticks: u64,
    low_ticks: u64,
    close_ticks: u64,
    volume: u64,
}

impl BarSource for JsonBarSource {
    fn load(&self) -> Result<Vec<Bar>, Box<dyn std::error::Error + Send + Sync>> {
        let mut bytes = Vec::new();
        File::open(&self.path)?
            .take(MAX_INPUT_BYTES + 1)
            .read_to_end(&mut bytes)?;
        if bytes.len() as u64 > MAX_INPUT_BYTES {
            return Err(io::Error::new(io::ErrorKind::InvalidData, "input exceeds 16 MiB").into());
        }
        let records: Vec<BarRecord> = crate::indexed_json::from_slice(&bytes)?;
        Ok(records
            .into_iter()
            .map(|record| Bar {
                timestamp_ms: record.timestamp_ms,
                open_ticks: record.open_ticks,
                high_ticks: record.high_ticks,
                low_ticks: record.low_ticks,
                close_ticks: record.close_ticks,
                volume: record.volume,
            })
            .collect())
    }
}
