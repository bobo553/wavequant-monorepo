use crate::{infrastructure::JsonBarSource, modules::market_data::inspect};
use std::{error::Error, ffi::OsString, fs::File, io::{self, Read, Write}};

pub fn run(args: impl IntoIterator<Item = OsString>) -> Result<(), Box<dyn Error + Send + Sync>> {
    let values:Vec<OsString>=args.into_iter().collect();
    if let Some(command)=values.first().and_then(|v|v.to_str()){
        if !matches!(command,"inspect"|"analyze"|"--help"|"help"){
            return super::commands::run(command,values.iter().skip(1).cloned());
        }
    }
    let mut args = values.into_iter();
    match args.next().as_deref().and_then(|value| value.to_str()) {
        None | Some("--help") | Some("help") if args.next().is_none() => {
            println!("Usage: wavequant-quant-rs inspect <bars.json> | analyze <request.json>");
            println!(
                "Inspect a single chronological OHLCV series; no strategy or orders are executed."
            );
            super::commands::help();
            Ok(())
        }
        Some("analyze") => {
            let path=args.next().ok_or_else(||invalid("analyze requires a request JSON file"))?;
            if args.next().is_some() {return Err(invalid("unexpected arguments").into());}
            let mut bytes=Vec::new();
            File::open(path)?.take(16*1024*1024+1).read_to_end(&mut bytes)?;
            if bytes.len()>16*1024*1024 {return Err(invalid("analysis request exceeds 16 MiB").into());}
            let request=crate::indexed_json::from_slice(&bytes)?;
            let result=crate::analysis::analyzer::analyze(request).map_err(|message|io::Error::new(io::ErrorKind::InvalidInput,message))?;
            let mut output=io::stdout().lock();
            crate::indexed_json::to_writer(&mut output,&result)?;
            writeln!(output)?;
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
