use crate::{infrastructure::JsonBarSource, modules::market_data::inspect};
use std::{error::Error, ffi::OsString, io};

pub fn run(args: impl IntoIterator<Item = OsString>) -> Result<(), Box<dyn Error + Send + Sync>> {
    let mut args = args.into_iter();
    match args.next().as_deref().and_then(|value| value.to_str()) {
        None | Some("--help") if args.next().is_none() => {
            println!("Usage: wavequant-quant-rs inspect <bars.json>");
            println!(
                "Inspect a single chronological OHLCV series; no strategy or orders are executed."
            );
            Ok(())
        }
        Some("inspect") => {
            let path = args
                .next()
                .ok_or_else(|| invalid("inspect requires a JSON file"))?;
            if args.next().is_some() {
                return Err(invalid("unexpected arguments").into());
            }
            let report = inspect(&JsonBarSource { path: path.into() })?;
            println!(
                "bars={} first_timestamp_ms={} last_timestamp_ms={}",
                report.bars, report.first_timestamp_ms, report.last_timestamp_ms
            );
            Ok(())
        }
        _ => Err(invalid("usage: wavequant-quant-rs inspect <bars.json>").into()),
    }
}

fn invalid(message: &'static str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidInput, message)
}
