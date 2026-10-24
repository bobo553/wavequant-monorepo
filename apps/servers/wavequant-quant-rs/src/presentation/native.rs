//! CLI 与离线运行服务的原生装配；每个结果来自实际数据、策略或账本服务。
use crate::{data,execution,governance::{OperationsPipeline,OperationsConfig,FrozenInputs,DatasetAudit},persistence::*};
use chrono::{DateTime,Utc};
use rust_decimal::Decimal;
use crate::indexed_json::{Value,json};
use std::{collections::BTreeMap,path::{Path,PathBuf},process::{Command,Stdio},fs::File,io::Read};

pub fn failure(error:impl std::fmt::Display)->StoreError{StoreError(error.to_string())}
pub fn read_json(path:&Path)->StoreResult<Value>{let file=File::open(path)?;require(file.metadata()?.len()<=16*1024*1024,"JSON exceeds 16 MiB")?;
    let mut bytes=Vec::new();file.take(16*1024*1024+1).read_to_end(&mut bytes)?;require(bytes.len()<=16*1024*1024,"JSON exceeds 16 MiB")?;Ok(crate::indexed_json::from_slice(&bytes)?)}
fn at(value:&str)->StoreResult<DateTime<Utc>>{DateTime::parse_from_rfc3339(value).map(|v|v.with_timezone(&Utc)).map_err(failure)}
pub fn new_output(path:&Path)->StoreResult<()>{if path.exists(){require(path.is_dir()&&std::fs::read_dir(path)?.next().is_none(),"output directory must be empty")?;}else{std::fs::create_dir_all(path)?;}Ok(())}
pub fn journal_report(path:&Path)->StoreResult<Value>{
    require(path.is_file(),"existing journal required")?;let db=rusqlite::Connection::open_with_flags(path,rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY)?;
    let native_execution:bool=db.query_row("SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE type='table' AND name='rust_execution_events_v1')",[],|r|r.get(0))?;
    if native_execution{let mut value=crate::indexed_json::to_value(execution::inspect_execution_journal(path).map_err(failure)?)?;value["integrity"]=json!("passed");return Ok(value);}
    let mut store=EventStore::open(path,true)?;store.snapshot(|s|Ok(json!({"events":s.events()?.len(),"head":s.verify()?,"schema":"rust_events_v1","integrity":"passed"})))
}
pub fn account_report(path:&Path,asof:Option<DateTime<Utc>>)->StoreResult<Value>{
    let last=execution::inspect_execution_journal(path).map_err(failure)?.last_event_at.ok_or_else(||failure("empty account journal"))?;
    crate::indexed_json::to_value(execution::Account::open_existing_readonly(path).map_err(failure)?.report(asof.unwrap_or(last)).map_err(failure)?).map_err(Into::into)
}
pub fn import_tdx(root:&Path,output:&Path,start:&str,end:&str,symbols:&[String])->StoreResult<Value>{
    require(!symbols.is_empty(),"explicit TDX symbols required")?;require(!output.exists()&&!output.with_extension("metadata.json").exists(),"TDX output already exists")?;
    data::tdx::parse_date(start).map_err(failure)?;data::tdx::parse_date(end).map_err(failure)?;
    require(start<=end,"end precedes start")?;let reader=data::tdx::TdxReader::new(root).map_err(failure)?;
    let(actions,actions_version)=data::gbbq::read_actions(&reader.root().join("T0002/hq_cache/gbbq")).map_err(failure)?;
    let mut rows=Vec::new();let mut sources=Vec::new();let mut unique=std::collections::BTreeSet::new();
    for symbol in symbols{require(unique.insert(symbol),"duplicate symbol")?;let source=reader.bars(symbol,end).map_err(failure)?;
        require(source.bars.first().is_some_and(|b|b.date.as_str()<start),"choose a start after listing; IPO execution is not modeled")?;
        let converted=data::adjustments::adjust_rows(&source.bars,&actions,start,end,symbol).map_err(failure)?;
        require(!converted.is_empty(),"no TDX data in requested range")?;
        sources.push(json!({"symbol":symbol,"file":reader.day_path(symbol).map_err(failure)?,"sha256":source.version,"first":converted.first().map(|b|&b.timestamp),"last":converted.last().map(|b|&b.timestamp),"rows":converted.len()}));
        for b in converted{rows.push(data::exchange::ImportedBar{timestamp:b.timestamp,symbol:b.symbol,open:b.open,high:b.high,low:b.low,close:b.close,volume:b.volume as f64,
            adjustment_factor:b.adjustment_factor,buyable:b.buyable,sellable:b.sellable,close_buyable:b.close_buyable,nonflat_close_buyable:b.nonflat_close_buyable,close_sellable:b.close_sellable,nonflat_close_sellable:b.nonflat_close_sellable,raw_is_st:b.raw_is_st,raw_trading_active:b.raw_trading_active});}
    }
    data::exchange::write_csv(output,&rows).map_err(failure)?;
    let metadata=json!({"kind":"real_market","source":"Tongdaxin local .day + gbbq","bar_permission_schema":"source_close_sell_permissions_v2","frequency":"daily","requested_start":start,"requested_end":end,
        "start":rows.iter().map(|r|&r.timestamp).min(),"end":rows.iter().map(|r|&r.timestamp).max(),"sources":sources,"gbbq_sha256":actions_version,"sha256":fingerprint(output)?,"rows":rows.len(),
        "price_basis":"causal multiplicative adjusted equivalent units","limitations":["Fixed selected universe does not establish all-market completeness.","No historical ST data in .day; permissions assume established non-ST shares.","Adjusted equivalent units do not model dividend-tax or rights cash ledgers.","No auction queue or minute execution claims."]});
    write_json(&output.with_extension("metadata.json"),&metadata)?;Ok(metadata)
}

pub struct NativePipeline{pub project_root:PathBuf,pub engineering_evidence:Option<Value>}
impl NativePipeline{
    pub fn discover()->StoreResult<Self>{let start=std::env::current_dir()?;
        let root=start.ancestors().find(|p|p.join("apps/servers/wavequant-quant-rs/Cargo.toml").is_file()).map(Path::to_owned)
            .or_else(||Path::new(env!("CARGO_MANIFEST_DIR")).ancestors().find(|p|p.join("apps/servers/wavequant-quant-rs/Cargo.toml").is_file()).map(Path::to_owned))
            .ok_or_else(||failure("Rust project sources required for an auditable system run"))?;Ok(Self{project_root:root.canonicalize()?,engineering_evidence:None})}
}
impl OperationsPipeline for NativePipeline{
    fn environment(&mut self)->StoreResult<(PathBuf,Value)>{let workspace=Path::new("apps/servers/wavequant-quant-rs");let mut files=vec![PathBuf::from("Cargo.toml"),workspace.join("Cargo.toml")];
        if self.project_root.join("Cargo.lock").is_file(){files.push(PathBuf::from("Cargo.lock"));}
        for relative in [workspace.join("src"),workspace.join("tests")]{let path=self.project_root.join(relative);if path.is_dir(){for source in directory_files(&path)?{
            files.push(source.strip_prefix(&self.project_root).map_err(failure)?.to_owned());
        }}}
        files.sort();files.dedup();Ok((self.project_root.clone(),environment_snapshot(&self.project_root,&files)?))
    }
    fn engineering_tests(&mut self,output:&Path)->StoreResult<Value>{
        // 仅用户调用 system-run/platform-audit 才会进入此固定门禁；编写代码不会启动检查。
        std::fs::create_dir_all(output)?;let log=output.join("test_log.txt");require(!log.exists(),"test log already exists")?;
        let stdout=std::fs::OpenOptions::new().write(true).create_new(true).open(&log)?;let stderr=stdout.try_clone()?;
        let status=Command::new("cargo").current_dir(&self.project_root).args(["test","-p","wavequant-quant-rs","--lib","--tests"])
            .stdin(Stdio::null()).stdout(Stdio::from(stdout)).stderr(Stdio::from(stderr)).status()?;
        let text=std::fs::read_to_string(&log)?;let passed:usize=text.lines().filter_map(|line|line.trim().strip_prefix("test result: ok. "))
            .filter_map(|tail|tail.split_whitespace().next()).filter_map(|n|n.parse::<usize>().ok()).sum();
        require(status.success()&&passed>0,"engineering tests failed or no tests executed; inspect test_log.txt")?;
        let evidence=json!({"status":"passed","command":["cargo","test","-p","wavequant-quant-rs","--lib","--tests"],"passed":passed,"log":"test_log.txt","log_sha256":fingerprint(&log)?});
        self.engineering_evidence=Some(evidence.clone());Ok(evidence)
    }
    fn import_tdx(&mut self,root:&Path,csv:&Path,config:&OperationsConfig)->StoreResult<()>{let today=chrono::Local::now().date_naive().to_string();import_tdx(root,csv,&config.start,config.end.as_deref().unwrap_or(&today),&config.symbols)?;Ok(())}
    fn dataset_audit(&mut self,inputs:&FrozenInputs,when:DateTime<Utc>,store:&mut EventStore)->StoreResult<DatasetAudit>{
        let master=inputs.security_master.as_ref().map(data::security_master::SecurityMaster::load).transpose().map_err(failure)?.unwrap_or_default();
        let calendar:Option<Vec<String>>=inputs.calendar.as_ref().map(|p|crate::indexed_json::from_value(read_json(p)?).map_err(StoreError::from)).transpose()?;
        let manifest=data::dataset::build_manifest(&inputs.csv,&when.to_rfc3339(),&master,calendar.as_deref()).map_err(failure)?;
        let id=manifest["manifest_id"].as_str().ok_or_else(||failure("dataset manifest identity missing"))?;
        if store.get(&format!("dataset:{id}"))?.is_none(){store.transaction(|s|{s.append(&format!("dataset:{id}"),"DATASET_REGISTERED",when,manifest.clone())?;Ok(())})?;}
        let bars=data::exchange::load_bars(&inputs.csv).map_err(failure)?;
        let latest_by_symbol=bars.bars.into_iter().filter_map(|(symbol,bars)|bars.last().map(|b|(symbol,b.timestamp[..10].into()))).collect();
        Ok(DatasetAudit{manifest,latest_by_symbol})
    }
    fn research(&mut self,inputs:&FrozenInputs,output:&Path)->StoreResult<Value>{
        require(self.engineering_evidence.as_ref().is_some_and(|e|e["status"]=="passed"),"actual engineering gate evidence required before validation suite")?;
        crate::analytics::validation_suite::run_validation_suite(&inputs.csv,&inputs.protocol,output).map_err(failure)
    }
    fn paper_acceptance(&mut self,output:&Path)->StoreResult<Value>{paper_acceptance(output)}
    fn account_report(&mut self,path:&Path)->StoreResult<Value>{account_report(path,None)}
    fn journal_head(&mut self,path:&Path)->StoreResult<String>{journal_report(path)?["head"].as_str().map(str::to_owned).ok_or_else(||failure("journal head missing"))}
}

/// 保留原工程夹具，以真实策略输出驱动 OMS；清仓是显式工程操作而非伪造策略 EXIT。
pub fn paper_acceptance(output:&Path)->StoreResult<Value>{
    use crate::analysis::{analyzer::AnalyzerConfig,trading::day::TradingDayConfig};
    use execution::{Account,PortfolioLimits,PaperVenue,VenueSettings,SecurityFact,OrderRequest,Side,OpeningTick,StreamBar,TrendStreamStrategy,StrategyRuntime,SignalSide,IntentExecutionOptions};
    new_output(output)?;
    let rows=[(10.,11.,9.,10.),(11.,12.,10.,11.),(10.,11.,8.,9.),(9.,10.,7.,8.),(12.,13.,11.5,12.5),
        (11.,12.,10.,11.),(12.,13.,11.,12.8),(13.,14.,12.,14.),(14.,15.,13.,15.),(15.,16.,14.,16.)];
    let bars:Vec<_>=rows.into_iter().enumerate().map(|(index,(open,high,low,close))|{let decimal=|v:f64|v.to_string().parse::<Decimal>().map_err(failure);
        Ok(StreamBar{timestamp:at(&format!("2026-01-{:02}T15:00:00+08:00",index+1))?,symbol:"FIXTURE".into(),open:decimal(open)?,high:decimal(high)?,low:decimal(low)?,close:decimal(close)?,
        volume:Decimal::from(if index==7{2_000_000}else{1_000_000}),adjustment_factor:Decimal::ONE,raw_trading_active:Some(true),raw_is_st:Some(false)})}).collect::<StoreResult<_>>()?;
    let trading=TradingDayConfig{volume_lookback:3,minimum_reward_risk:Decimal::ZERO,entry_policy:"legacy_n_continuation".into(),preflight_reward_risk:false,squeeze_pullback_entries:false,..TradingDayConfig::default()};
    let config=AnalyzerConfig{trading,..AnalyzerConfig::default()};write_json(&output.join("strategy.json"),&config)?;write_json(&output.join("bars.json"),&bars)?;
    let strategy=TrendStreamStrategy::new(config,sha256(canonical(&crate::indexed_json::to_value(&bars)?)?)).map_err(failure)?;
    let mut runtime=StrategyRuntime::new(strategy).map_err(failure)?;
    let opened=at("2025-12-31T15:00:00+08:00")?;let when=at("2026-01-11T09:30:00+08:00")?;
    let capital=Decimal::from(1_000_000);let limits=PortfolioLimits::default();
    let master=BTreeMap::from([("FIXTURE".into(),SecurityFact{symbol:"FIXTURE".into(),sector:"synthetic".into(),active:true,suspended:false,st:false,buy_lot:100,settlement_days:1,tick_size:Decimal::new(1,2),source:"engineering_fixture_not_market_metadata".into(),known_at:at("2019-12-31T15:00:00+08:00")?,effective_from:chrono::NaiveDate::from_ymd_opt(2020,1,1).ok_or_else(||failure("fixture date"))?,effective_to:None})]);
    let account_path=output.join("account.sqlite");let venue_path=output.join("venue.sqlite");
    let mut account=Account::open(&account_path,capital,limits.clone(),opened).map_err(failure)?;
    for bar in bars{let available=bar.timestamp;runtime.on_bar(&mut account,bar,available).map_err(failure)?;}
    let longs:Vec<_>=runtime.pending_intents(&account).map_err(failure)?.into_iter().filter(|i|i.signal.side==SignalSide::Long).collect();
    require(longs.len()==1,"fixture must emit exactly one canonical N/regime LONG")?;let intent=&longs[0];write_json(&output.join("signal.json"),intent)?;
    account.quote("quote:entry","FIXTURE",Decimal::from(16),when).map_err(failure)?;
    let fee=Decimal::from(40);let accepted=runtime.execute_intent(&mut account,&intent.id,&IntentExecutionOptions{when,raw_open:Decimal::from(16),adjustment_factor:Decimal::ONE,fee_budget:fee,
        slippage_bps:Decimal::from(5),max_entry_gap:Decimal::new(5,2),expires_on:execution::session(when)},&master).map_err(failure)?;
    require(accepted.disposition=="ORDERED","fixture intent was not ordered")?;
    let order_id=accepted.acknowledgement.and_then(|a|a.order_id).ok_or_else(||failure("ordered fixture intent lacks order identity"))?;
    let quantity=account.state().map_err(failure)?.orders.get(&order_id).ok_or_else(||failure("fixture order missing"))?.request.quantity;
    let mut venue=PaperVenue::open(&venue_path,VenueSettings::default(),opened).map_err(failure)?;
    venue.dispatch(&account).map_err(failure)?;venue.dispatch(&account).map_err(failure)?;
    let opening=OpeningTick{tick_id:"entry-half".into(),symbol:"FIXTURE".into(),at:when,price:Decimal::from(16),available_volume:quantity/2,buyable:true,sellable:true};
    venue.match_open(&opening).map_err(failure)?;venue.match_open(&opening).map_err(failure)?;
    require(account.snapshot().map_err(failure)?.positions.is_empty(),"local account changed before fill delivery")?;drop(account);
    let mut account=Account::open(&account_path,capital,limits,opened).map_err(failure)?;
    venue.receive(&mut account).map_err(failure)?;let once=account.snapshot().map_err(failure)?;venue.receive(&mut account).map_err(failure)?;
    let duplicate_delivery=account.snapshot().map_err(failure)?==once;let partial=once.positions.get("FIXTURE")==Some(&(quantity/2));
    let first=account.reconcile("reconcile:partial",when,venue.snapshot().map_err(failure)?).map_err(failure)?;
    let when2=at("2026-01-12T09:30:00+08:00")?;account.quote("quote:remainder","FIXTURE",Decimal::from(16),when2).map_err(failure)?;
    venue.match_open(&OpeningTick{tick_id:"entry-rest".into(),at:when2,available_volume:quantity,..opening.clone()}).map_err(failure)?;venue.receive(&mut account).map_err(failure)?;
    let second=account.reconcile("reconcile:full",when2,venue.snapshot().map_err(failure)?).map_err(failure)?;
    let when3=at("2026-01-13T09:30:00+08:00")?;account.quote("quote:exit","FIXTURE",Decimal::from(17),when3).map_err(failure)?;
    let t_plus_one=account.state().map_err(failure)?.sellable("FIXTURE",when3)==quantity;
    account.submit(OrderRequest{order_id:"fixture-liquidation".into(),symbol:"FIXTURE".into(),side:Side::Sell,quantity,limit_price:Decimal::new(1698,2),stop_price:Decimal::from(12),signal_at:at("2026-01-12T15:00:00+08:00")?,fee_budget:fee},when3,&master).map_err(failure)?;
    venue.dispatch(&account).map_err(failure)?;venue.match_open(&OpeningTick{tick_id:"exit".into(),at:when3,price:Decimal::from(17),available_volume:quantity,..opening}).map_err(failure)?;venue.receive(&mut account).map_err(failure)?;
    let final_match=account.reconcile("reconcile:flat",when3,venue.snapshot().map_err(failure)?).map_err(failure)?;
    let health=account.monitor("health:final",when3).map_err(failure)?;let snapshot=account.snapshot().map_err(failure)?;let venue_snapshot=venue.snapshot().map_err(failure)?;
    let account_head=execution::inspect_execution_journal(&account_path).map_err(failure)?.head;
    snapshot_sqlite(&account_path,&output.join("account.backup.sqlite"))?;
    let backup_match=execution::inspect_execution_journal(&output.join("account.backup.sqlite")).map_err(failure)?.head==account_head;
    let checks=json!({"canonical_signal_to_order":true,"partial_fills":partial,"restart_recovery":partial,"duplicate_delivery":duplicate_delivery,
        "independent_reconciliation":first&&second&&final_match&&snapshot.positions.is_empty(),"T_plus_1_exit":t_plus_one,"backup_restore":backup_match});
    require(checks.as_object().is_some_and(|c|c.values().all(|v|v==true)),"paper acceptance failed")?;
    let report=json!({"kind":"synthetic_engineering_acceptance_not_strategy_returns","checks":checks,"quantity":quantity,"snapshot":snapshot,"venue_snapshot":venue_snapshot,"health":health,
        "account_head":account_head,"venue_head":execution::inspect_execution_journal(&venue_path).map_err(failure)?.head,
        "limitations":["Minimal legacy N/regime fixture; production policy is unchanged.","Synthetic dates, quotes and security metadata do not establish trading edge.","Venue rates are explicit scenario assumptions."]});
    write_json(&output.join("report.json"),&report)?;Ok(report)
}
