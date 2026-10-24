use crate::{governance::{self,OperationsPipeline,FrozenInputs},persistence::*};
use super::native;
use std::{collections::{BTreeMap,BTreeSet},error::Error,ffi::OsString,io::{self,Write},path::{Path,PathBuf}};
use crate::indexed_json::json;
type Result<T>=std::result::Result<T,Box<dyn Error+Send+Sync>>;
fn invalid(message:impl Into<String>)->io::Error{io::Error::new(io::ErrorKind::InvalidInput,message.into())}
fn output(value:&impl serde::Serialize)->Result<()>{let mut out=io::stdout().lock();crate::indexed_json::to_writer_pretty(&mut out,value)?;writeln!(out)?;Ok(())}
struct Arguments{positionals:Vec<OsString>,options:BTreeMap<String,OsString>,flags:BTreeSet<String>}
impl Arguments{
    fn parse(args:impl Iterator<Item=OsString>,allowed:&[&str],flags:&[&str])->Result<Self>{let mut args=args.peekable();let mut parsed=Self{positionals:vec![],options:BTreeMap::new(),flags:BTreeSet::new()};
        while let Some(value)=args.next(){if let Some(name)=value.to_str().and_then(|s|s.strip_prefix("--")){
            if flags.contains(&name){if !parsed.flags.insert(name.into()){return Err(invalid(format!("duplicate --{name}")).into());}continue;}
            if !allowed.contains(&name){return Err(invalid(format!("unknown --{name}")).into());}
            let mut next=args.next().ok_or_else(||invalid(format!("--{name} requires a value")))?;
            if next.to_str().is_some_and(|s|s.starts_with("--")){return Err(invalid(format!("--{name} requires a value")).into());}
            if name=="symbols"{while args.peek().is_some_and(|v|!v.to_string_lossy().starts_with("--")){next.push(",");next.push(args.next().ok_or_else(||invalid("symbol disappeared"))?);}}
            if parsed.options.insert(name.into(),next).is_some(){return Err(invalid(format!("duplicate --{name}")).into());}
        }else{parsed.positionals.push(value);}}
        Ok(parsed)
    }
    fn required(&self,name:&str)->Result<&OsString>{self.options.get(name).ok_or_else(||invalid(format!("--{name} is required")).into())}
    fn text(&self,name:&str)->Result<Option<&str>>{self.options.get(name).map(|v|v.to_str().ok_or_else(||invalid(format!("--{name} must be UTF-8")).into())).transpose()}
    fn required_text(&self,name:&str)->Result<&str>{self.text(name)?.ok_or_else(||invalid(format!("--{name} is required")).into())}
    fn path(&self,name:&str)->Option<PathBuf>{self.options.get(name).map(PathBuf::from)}
    fn positional(&self,count:usize)->Result<()>{if self.positionals.len()!=count{return Err(invalid(format!("expected {count} positional argument(s)")).into());}Ok(())}
}
fn frozen(args:&Arguments)->Result<FrozenInputs>{Ok(FrozenInputs{csv:args.path("csv").unwrap_or_else(||PathBuf::from("data/tdx_system_20260908.csv")),protocol:args.path("protocol").unwrap_or_else(||PathBuf::from("configs/system_squeeze_v4.json")),security_master:args.path("security-master"),calendar:args.path("calendar")})}
fn configuration(args:&Arguments)->Result<crate::indexed_json::Value>{args.path("config").map(|p|native::read_json(&p).map_err(Into::into)).unwrap_or_else(||Ok(json!({})))}
pub fn help(){println!("Additional native commands:\n\
  validate <bars.csv|bars.json>\n\
  backtest CSV [--config PATH] [--output-dir PATH]\n\
  research CSV [--config PATH] [--protocol PATH] [--output-dir PATH]\n\
  import-tdx [--tdx-root PATH] [--csv PATH] [--start YYYY-MM-DD] [--end YYYY-MM-DD] [--symbols sh.600000 sz.000001]\n\
  run-tdx [import options] [--config PATH] [--protocol PATH] [--output-dir PATH]\n\
  demo [--output-dir PATH]\n\
  notion-research [--csv PATH] [--notes-dir PATH] [--protocol PATH] [--output-dir PATH] [--config PATH]\n\
  foundation-audit CSV [--output-dir PATH]\n\
  strategy-evidence [--tdx-root PATH] [--protocol PATH] [--output-dir PATH] [--workers 1..8]\n\
  system-run --config PATH --run-id ID\n\
  system-status [--root PATH]\n\
  system-backup [--root PATH] --destination PATH\n\
  system-restore --bundle PATH --destination PATH\n\
  system-alert-ack [--root PATH] --id ID --reason TEXT\n\
  journal-check DATABASE [--backup PATH]\n\
  account-report DATABASE [--asof RFC3339] [--output PATH]\n\
  paper-demo --output-dir PATH\n\
  platform-audit --csv PATH --protocol PATH --output-dir PATH [--security-master PATH] [--calendar PATH] [--engineering-only]\n\
  system-research --csv PATH --protocol PATH --output-dir PATH [--security-master PATH] [--calendar PATH]\n\
All orders are local paper orders. system-run and platform-audit run engineering gates when explicitly invoked.");}
pub fn run(command:&str,args:impl Iterator<Item=OsString>)->Result<()>{
    match command{
        "internal-worker"=>{let p=Arguments::parse(args,&[],&[])?;p.positional(0)?;crate::infrastructure::computation::worker_main().map_err(Into::into)},
        "validate"=>{let p=Arguments::parse(args,&[],&[])?;p.positional(1)?;let bars=crate::data::exchange::load_bars(Path::new(&p.positionals[0]))?;
            let coverage:BTreeMap<_,_>=bars.bars.iter().map(|(symbol,rows)|(symbol,json!({"first":rows.first().map(|b|&b.timestamp),"last":rows.last().map(|b|&b.timestamp),"rows":rows.len()}))).collect();
            output(&json!({"symbols":bars.bars.len(),"rows":bars.bars.values().map(Vec::len).sum::<usize>(),"coverage":coverage,"source_version":bars.source_version}))},
        "backtest"|"research"=>{let p=Arguments::parse(args,if command=="research"{&["config","output-dir","protocol"]}else{&["config","output-dir"]},&[])?;p.positional(1)?;
            let destination=p.path("output-dir").unwrap_or_else(||PathBuf::from("results").join(command));let config=configuration(&p)?;let csv=Path::new(&p.positionals[0]);
            let report=if command=="research"{crate::analytics::runner::run_research(csv,&destination,&config,&p.path("protocol").unwrap_or_else(||PathBuf::from("configs/research_protocol.json")),None)?}
                else{crate::analytics::runner::run_backtest(csv,&destination,&config)?};output(&report)},
        "demo"=>{let p=Arguments::parse(args,&["output-dir"],&[])?;p.positional(0)?;let destination=p.path("output-dir").unwrap_or_else(||PathBuf::from("results/demo"));native::new_output(&destination)?;
            let csv=destination.join("synthetic.csv");crate::data::synthetic::synthetic_dataset(&csv,20260907,500,5)?;
            let report=crate::analytics::runner::run_backtest(&csv,&destination,&json!({}))?;output(&json!({"kind":"synthetic_engineering_only","metrics":report["metrics"],"report":destination.join("report.json")}))},
        "notion-research"=>{let p=Arguments::parse(args,&["csv","notes-dir","protocol","output-dir","config"],&[])?;p.positional(0)?;
            let destination=p.path("output-dir").unwrap_or_else(||PathBuf::from("results/notion_v2"));native::new_output(&destination)?;
            let mut pipeline=native::NativePipeline::discover()?;let tests=pipeline.engineering_tests(&destination)?;
            output(&crate::analytics::runner::run_notion(&p.path("csv").unwrap_or_else(||PathBuf::from("data/tdx_daily.csv")),&p.path("notes-dir").unwrap_or_else(||PathBuf::from("notes/notion_20260908")),&destination,
                &p.path("protocol").unwrap_or_else(||PathBuf::from("configs/notion_protocol.json")),&configuration(&p)?,Some(&tests))?)},
        "foundation-audit"=>{let p=Arguments::parse(args,&["output-dir"],&[])?;p.positional(1)?;let destination=p.path("output-dir").unwrap_or_else(||PathBuf::from("results/foundations"));native::new_output(&destination)?;
            let mut pipeline=native::NativePipeline::discover()?;let tests=pipeline.engineering_tests(&destination)?;output(&crate::analytics::foundation_audit::audit_foundations(Path::new(&p.positionals[0]),&destination,Some(&tests))?)},
        "strategy-evidence"=>{let p=Arguments::parse(args,&["tdx-root","protocol","output-dir","workers"],&[])?;p.positional(0)?;
            let workers=p.text("workers")?.unwrap_or("4").parse::<usize>()?;if !(1..=8).contains(&workers){return Err(invalid("workers must be between 1 and 8").into());}
            let destination=p.path("output-dir").unwrap_or_else(||PathBuf::from("results/squeeze_evidence_v5_20260908"));native::new_output(&destination)?;
            let mut pipeline=native::NativePipeline::discover()?;let tests=pipeline.engineering_tests(&destination)?;
            output(&crate::analytics::strategy_evidence::run_strategy_evidence(&p.path("tdx-root").unwrap_or_else(||PathBuf::from("D:/TDX")),&p.path("protocol").unwrap_or_else(||PathBuf::from("configs/squeeze_evidence_v5.json")),&destination,&tests,workers)?)},
        "system-run"=>{let p=Arguments::parse(args,&["config","run-id"],&[])?;p.positional(0)?;
            let config=governance::OperationsConfig::load(&p.path("config").unwrap_or_else(||PathBuf::from("configs/operations.json")))?;
            let mut pipeline=native::NativePipeline::discover()?;output(&governance::run_operations(&config,p.required_text("run-id")?,&mut pipeline)?)},
        "system-status"=>{let p=Arguments::parse(args,&["root"],&[])?;p.positional(0)?;output(&governance::operations_status(&p.path("root").unwrap_or_else(||PathBuf::from("results/operations_v1")))?)},
        "system-backup"=>{let p=Arguments::parse(args,&["root","destination"],&[])?;p.positional(0)?;let root=p.path("root").unwrap_or_else(||PathBuf::from("results/operations_v1"));
            if !root.join("operations.sqlite").is_file(){return Err(invalid("existing operations root required").into());}let _lock=WorkspaceLock::acquire(&root)?;
            output(&create_bundle(&root,Path::new(p.required("destination")?))?)},
        "system-restore"=>{let p=Arguments::parse(args,&["bundle","destination"],&[])?;p.positional(0)?;
            output(&restore_bundle(Path::new(p.required("bundle")?),Path::new(p.required("destination")?))?)},
        "system-alert-ack"=>{let p=Arguments::parse(args,&["root","id","reason"],&[])?;p.positional(0)?;let root=p.path("root").unwrap_or_else(||PathBuf::from("results/operations_v1"));
            if !root.join("operations.sqlite").is_file(){return Err(invalid("existing operations journal required").into());}
            let _lock=WorkspaceLock::acquire(&root)?;let mut store=EventStore::open(&root.join("operations.sqlite"),false)?;
            output(&acknowledge_alert(&mut store,p.required_text("id")?,p.required_text("reason")?,chrono::Utc::now())?)},
        "journal-check"=>{let p=Arguments::parse(args,&["backup"],&[])?;p.positional(1)?;let path=Path::new(&p.positionals[0]);let mut report=native::journal_report(path)?;
            if let Some(destination)=p.path("backup"){snapshot_sqlite(path,&destination)?;let restored=native::journal_report(&destination)?;
                if restored["head"]!=report["head"]{return Err(invalid("journal changed during backup; retry with writer stopped").into());}report["backup"]=json!(destination);report["backup_verified"]=json!(true);}
            output(&report)},
        "account-report"=>{let p=Arguments::parse(args,&["asof","output"],&[])?;p.positional(1)?;
            let asof=p.text("asof")?.map(chrono::DateTime::parse_from_rfc3339).transpose()?.map(|v|v.with_timezone(&chrono::Utc));
            let report=native::account_report(Path::new(&p.positionals[0]),asof)?;
            if let Some(path)=p.path("output"){if path.exists(){return Err(invalid("account report output already exists").into());}write_json(&path,&report)?;}output(&report)},
        "paper-demo"=>{let p=Arguments::parse(args,&["output-dir"],&[])?;p.positional(0)?;output(&native::paper_acceptance(&p.path("output-dir").unwrap_or_else(||PathBuf::from("results/paper_demo")))?)},
        "platform-audit"|"system-research"=>{let flags:&[&str]=if command=="platform-audit"{&["engineering-only"]}else{&[]};
            let p=Arguments::parse(args,&["csv","protocol","output-dir","security-master","calendar"],flags)?;p.positional(0)?;
            let inputs=frozen(&p)?;let destination=p.path("output-dir").unwrap_or_else(||PathBuf::from(if command=="platform-audit"{"results/platform_v1_20260908"}else{"results/system_squeeze_v4_20260908"}));native::new_output(&destination)?;
            let mut pipeline=native::NativePipeline::discover()?;let tests=pipeline.engineering_tests(&destination)?;
            if command=="platform-audit"{output(&governance::run_platform_audit(&inputs,&destination,&tests,!p.flags.contains("engineering-only"),&mut pipeline)?)}
            else{output(&crate::analytics::runner::run_system_research_with_sources(&inputs.csv,&destination,&inputs.protocol,&tests,&crate::analytics::runner::RunnerSources{security_master:inputs.security_master,calendar:inputs.calendar})?)}}
        "import-tdx"|"run-tdx"=>{let allowed:&[&str]=if command=="run-tdx"{&["tdx-root","csv","start","end","symbols","config","protocol","output-dir"]}else{&["tdx-root","csv","start","end","symbols"]};
            let p=Arguments::parse(args,allowed,&[])?;p.positional(0)?;let symbols:Vec<String>=p.text("symbols")?.map(|v|v.split(',').map(str::trim).map(str::to_owned).collect())
                .unwrap_or_else(||crate::analytics::research_panel::DEFAULT_SYMBOLS.iter().map(|s|(*s).to_owned()).collect());
            let destination=p.path("output-dir").unwrap_or_else(||PathBuf::from("results/tdx"));let tests=if command=="run-tdx"{native::new_output(&destination)?;Some(native::NativePipeline::discover()?.engineering_tests(&destination)?)}else{None};
            let csv=p.path("csv").unwrap_or_else(||PathBuf::from("data/tdx_daily.csv"));let today=chrono::Local::now().date_naive().to_string();
            let metadata=native::import_tdx(&p.path("tdx-root").unwrap_or_else(||PathBuf::from("D:/TDX")),&csv,p.text("start")?.unwrap_or("2018-01-01"),p.text("end")?.unwrap_or(&today),&symbols)?;
            if command=="import-tdx"{output(&metadata)}else{output(&crate::analytics::runner::run_research(&csv,&destination,&configuration(&p)?,&p.path("protocol").unwrap_or_else(||PathBuf::from("configs/research_protocol.json")),tests.as_ref())?)}},
        _=>Err(invalid(format!("unknown command {command}; use --help")).into()),
    }
}
