//! PostgreSQL / MySQL 原生适配：绑定参数、有界连接池、事务发布与 TLS 身份校验。
use std::{ops::{Deref,DerefMut},sync::{Mutex,Condvar},time::{Duration,Instant}};
use mysql::prelude::Queryable;
use postgres::types::{ToSql,Type};
use rusqlite::types::Value as SqlValue;
use wavequant_quant_rs::indexed_json::{Value,json};
use crate::presentation::error::ApiError;
type Result<T>=std::result::Result<T,ApiError>;
fn failure()->ApiError {ApiError::unavailable("SQL 存储操作失败，请检查服务与数据库配置")}

pub enum RemoteStore {Postgres(PgPool),Mysql{pool:mysql::Pool,timeout:Duration}}
struct PgState {idle:Vec<postgres::Client>,total:usize}
pub struct PgPool {config:postgres::Config,tls:native_tls::TlsConnector,maximum:usize,timeout:Duration,state:Mutex<PgState>,ready:Condvar}
struct PgLease<'a> {pool:&'a PgPool,client:Option<postgres::Client>}
impl Deref for PgLease<'_> {type Target=postgres::Client;fn deref(&self)->&Self::Target{self.client.as_ref().expect("lease owns a connection")}}
impl DerefMut for PgLease<'_> {fn deref_mut(&mut self)->&mut Self::Target{self.client.as_mut().expect("lease owns a connection")}}
impl Drop for PgLease<'_> {fn drop(&mut self){if let Ok(mut state)=self.pool.state.lock(){if let Some(client)=self.client.take(){if client.is_closed(){state.total=state.total.saturating_sub(1);}else{state.idle.push(client);}}self.pool.ready.notify_one();}}}
impl PgPool {
    fn lease(&self)->Result<PgLease<'_>> {
        let deadline=Instant::now()+self.timeout;
        loop {
            let mut state=self.state.lock().map_err(|_|failure())?;
            while let Some(client)=state.idle.pop(){if !client.is_closed(){return Ok(PgLease{pool:self,client:Some(client)});}state.total=state.total.saturating_sub(1);}
            if state.total<self.maximum {
                state.total+=1;drop(state);
                let connection=self.config.connect(postgres_native_tls::MakeTlsConnector::new(self.tls.clone())).and_then(|mut client|{client.batch_execute(&format!("SET statement_timeout = {}; SET lock_timeout = {}; SET TIME ZONE 'UTC'",self.timeout.as_millis(),self.timeout.as_millis()))?;Ok(client)});
                return match connection {Ok(client)=>Ok(PgLease{pool:self,client:Some(client)}),Err(_)=>{if let Ok(mut state)=self.state.lock(){state.total=state.total.saturating_sub(1);self.ready.notify_one();}Err(failure())}};
            }
            let remaining=deadline.saturating_duration_since(Instant::now());if remaining.is_zero(){return Err(ApiError::unavailable("SQL 连接池繁忙，请稍后重试"));}
            let (guard,wait)=self.ready.wait_timeout(state,remaining).map_err(|_|failure())?;drop(guard);
            if wait.timed_out(){return Err(ApiError::unavailable("SQL 连接池繁忙，请稍后重试"));}
        }
    }
}
impl RemoteStore {
    pub fn postgres(url:&str,maximum:usize,timeout:Duration)->Result<Self> {
        let raw=url.replacen("postgresql+psycopg://","postgresql://",1);
        let mut parsed=reqwest::Url::parse(&raw).map_err(|_|ApiError::invalid("invalid PostgreSQL URL"))?;
        if parsed.fragment().is_some(){return Err(ApiError::invalid("database URL fragments are not supported"));}
        let mut query=Vec::new();let mut tls=native_tls::TlsConnector::builder();
        for (key,value) in parsed.query_pairs(){match key.as_ref(){
            "sslmode"=>{if !matches!(value.as_ref(),"require"|"verify-ca"|"verify-full"){return Err(ApiError::invalid("PostgreSQL requires certificate-verified TLS"));}},
            "sslrootcert"=>{let bytes=std::fs::read(value.as_ref()).map_err(|_|ApiError::invalid("cannot read PostgreSQL CA certificate"))?;let certificate=native_tls::Certificate::from_pem(&bytes).or_else(|_|native_tls::Certificate::from_der(&bytes)).map_err(|_|ApiError::invalid("invalid PostgreSQL CA certificate"))?;tls.add_root_certificate(certificate);},
            "connect_timeout"=>{},
            _=>query.push((key.into_owned(),value.into_owned())),
        }}
        parsed.set_query(None);if !query.is_empty(){parsed.query_pairs_mut().extend_pairs(query);}
        let mut config:postgres::Config=parsed.as_str().parse().map_err(|_|ApiError::invalid("invalid PostgreSQL connection options"))?;
        config.ssl_mode(postgres::config::SslMode::Require).connect_timeout(timeout).tcp_user_timeout(timeout);
        let tls=tls.build().map_err(|_|failure())?;
        Ok(Self::Postgres(PgPool{config,tls,maximum,timeout,state:Mutex::new(PgState{idle:Vec::new(),total:0}),ready:Condvar::new()}))
    }
    pub fn mysql(url:&str,maximum:usize,timeout:Duration)->Result<Self> {
        let raw=url.replacen("mysql+pymysql://","mysql://",1);
        let mut parsed=reqwest::Url::parse(&raw).map_err(|_|ApiError::invalid("invalid MySQL URL"))?;
        if parsed.fragment().is_some(){return Err(ApiError::invalid("database URL fragments are not supported"));}
        let mut query=Vec::new();let mut ssl=mysql::SslOpts::default();
        for (key,value) in parsed.query_pairs(){match key.as_ref(){
            "ssl_ca"=>ssl=ssl.with_root_cert_path(Some(std::path::PathBuf::from(value.as_ref()))),
            "charset"=>{if !matches!(value.as_ref(),"utf8mb4"|"utf8"){return Err(ApiError::invalid("MySQL charset must be UTF-8"));}},
            "connect_timeout"=>{},
            "ssl_disabled"|"ssl_verify_cert"|"ssl_verify_identity"=>return Err(ApiError::invalid("MySQL TLS verification cannot be disabled")),
            _=>query.push((key.into_owned(),value.into_owned())),
        }}
        parsed.set_query(None);if !query.is_empty(){parsed.query_pairs_mut().extend_pairs(query);}
        let opts=mysql::Opts::from_url(parsed.as_str()).map_err(|_|ApiError::invalid("invalid MySQL connection options"))?;
        let constraints=mysql::PoolConstraints::new(0,maximum).ok_or_else(||ApiError::invalid("invalid MySQL pool size"))?;
        let builder=mysql::OptsBuilder::from_opts(opts).pool_opts(mysql::PoolOpts::default().with_constraints(constraints)).prefer_socket(false)
            .ssl_opts(Some(ssl)).tcp_connect_timeout(Some(timeout)).read_timeout(Some(timeout)).write_timeout(Some(timeout))
            .init(vec!["SET time_zone = '+00:00'".to_owned(),"SET NAMES utf8mb4".to_owned()]);
        let pool=mysql::Pool::new(builder).map_err(|_|failure())?;
        Ok(Self::Mysql{pool,timeout})
    }
    pub fn ping(&self)->Result<()> {match self{Self::Postgres(pool)=>{pool.lease()?.simple_query("SELECT 1").map_err(|_|failure())?;},Self::Mysql{pool,timeout}=>{let value:Option<u8>=pool.try_get_conn(*timeout).map_err(|_|failure())?.query_first("SELECT 1").map_err(|_|failure())?;if value!=Some(1){return Err(failure());}}}Ok(())}
    pub fn initialize(&self)->Result<()> {match self {
        Self::Postgres(pool)=>{let mut client=pool.lease()?;let mut tx=client.transaction().map_err(|_|failure())?;for statement in schema(false){tx.batch_execute(&statement).map_err(|_|failure())?;}tx.commit().map_err(|_|failure())?;},
        Self::Mysql{pool,timeout}=>{let mut client=pool.try_get_conn(*timeout).map_err(|_|failure())?;for statement in schema(true){client.query_drop(statement).map_err(|_|failure())?;}},
    }Ok(())}
    pub fn query(&self,sql:&str,params:Vec<SqlValue>)->Result<Vec<Value>> {match self {
        Self::Postgres(pool)=>pg_query(&mut *pool.lease()?,sql,&params),
        Self::Mysql{pool,timeout}=>mysql_query(&mut pool.try_get_conn(*timeout).map_err(|_|failure())?,sql,&params),
    }}
    pub fn publish(&self,table:&str,key:&str,columns:&[&str],params:Vec<SqlValue>,updates:&[&str],identity:&str)->Result<Value> {
        let prefix=format!("INSERT INTO {table} ({}) VALUES ({})",columns.join(","),vec!["?";columns.len()].join(","));
        let read=format!("SELECT * FROM {table} WHERE {key}=?");
        match self {
            Self::Postgres(pool)=>{
                let sql=postgres_sql(&format!("{prefix} ON CONFLICT({key}) DO UPDATE SET {}",updates.iter().map(|field|format!("{field}=excluded.{field}")).collect::<Vec<_>>().join(",")));
                let mut client=pool.lease()?;let mut tx=client.transaction().map_err(|_|failure())?;
                let statement=tx.prepare(&sql).map_err(|_|failure())?;let values=pg_params(statement.params(),&params)?;let refs=values.iter().map(|v|v.as_ref() as &(dyn ToSql+Sync)).collect::<Vec<_>>();tx.execute(&statement,&refs).map_err(|_|failure())?;
                let result=pg_query(&mut tx,&read,&[SqlValue::Text(identity.into())])?.pop().ok_or_else(failure)?;
                tx.commit().map_err(|_|failure())?;Ok(result)
            }
            Self::Mysql{pool,timeout}=>{
                let sql=format!("{prefix} ON DUPLICATE KEY UPDATE {}",updates.iter().map(|field|format!("{field}=VALUES({field})")).collect::<Vec<_>>().join(","));
                let mut client=pool.try_get_conn(*timeout).map_err(|_|failure())?;let mut tx=client.start_transaction(mysql::TxOpts::default()).map_err(|_|failure())?;
                tx.exec_drop(sql,mysql_params(&params)).map_err(|_|failure())?;
                let result=mysql_query(&mut tx,&read,&[SqlValue::Text(identity.into())])?.pop().ok_or_else(failure)?;tx.commit().map_err(|_|failure())?;Ok(result)
            }
        }
    }
}
fn postgres_sql(sql:&str)->String {
    let mut sql=sql.replace("scope_symbol IS ?","scope_symbol IS NOT DISTINCT FROM ?").replace("? IS NULL","CAST(? AS TEXT) IS NULL");
    for name in ["market_total","stale"] {let expression=if name=="stale" {"CASE WHEN payload->>'stale' IN ('true','1') THEN 1 ELSE 0 END".to_owned()}else{format!("CAST(payload->>'{name}' AS BIGINT)")};sql=sql.replace(&format!("json_extract(payload,'$.{name}')"),&expression);}
    for name in ["n_target_trend_confirmation_enabled","ordinary_trend_completion_enabled"]{sql=sql.replace(&format!("json_extract(payload,'$.{name}')"),&format!("CASE WHEN payload->>'{name}' IN ('true','1') THEN 1 ELSE 0 END"));}
    let mut index=0;sql.chars().map(|c|if c=='?' {index+=1;format!("${index}")}else{c.to_string()}).collect()
}
fn mysql_sql(sql:&str)->String {
    let mut sql=sql.replace("scope_symbol IS ?","scope_symbol <=> ?");
    for name in ["market_total","stale","n_target_trend_confirmation_enabled","ordinary_trend_completion_enabled"]{
        let expression=if name=="market_total" {format!("CAST(JSON_UNQUOTE(JSON_EXTRACT(payload,'$.{name}')) AS UNSIGNED)")}else{format!("CASE WHEN JSON_UNQUOTE(JSON_EXTRACT(payload,'$.{name}')) IN ('true','1') THEN 1 ELSE 0 END")};
        sql=sql.replace(&format!("json_extract(payload,'$.{name}')"),&expression);
    }sql
}
fn pg_query(client:&mut impl postgres::GenericClient,sql:&str,parameters:&[SqlValue])->Result<Vec<Value>> {
    let statement=client.prepare(&format!("SELECT row_to_json(native_row)::json FROM ({}) AS native_row",postgres_sql(sql))).map_err(|_|failure())?;
    let values=pg_params(statement.params(),parameters)?;let refs=values.iter().map(|v|v.as_ref() as &(dyn ToSql+Sync)).collect::<Vec<_>>();
    client.query(&statement,&refs).map_err(|_|failure())?.into_iter().map(|row|row.try_get::<_,tokio_postgres::types::Json<Value>>(0).map(|value|value.0).map_err(|_|failure())).collect()
}
fn pg_params(types:&[Type],values:&[SqlValue])->Result<Vec<Box<dyn ToSql+Sync>>> {
    if types.len()!=values.len(){return Err(ApiError::internal());}
    types.iter().zip(values).map(|(kind,value)|{
        let text=match value{SqlValue::Text(value)=>Some(value.as_str()),_=>None};
        let integer=match value{SqlValue::Integer(value)=>Some(*value),_=>None};
        let boxed:Box<dyn ToSql+Sync>=match *kind {
            Type::JSON|Type::JSONB=>Box::new(text.map(wavequant_quant_rs::indexed_json::from_str::<Value>).transpose().map_err(|_|failure())?.map(tokio_postgres::types::Json)),
            Type::INT8=>Box::new(integer),Type::INT4=>Box::new(integer.map(i32::try_from).transpose().map_err(|_|ApiError::invalid("SQL integer overflow"))?),
            Type::TIMESTAMPTZ=>Box::new(text.map(timestamp).transpose()?),Type::TIMESTAMP=>Box::new(text.map(|v|timestamp(v).map(|v|v.naive_utc())).transpose()?),
            Type::TEXT|Type::VARCHAR|Type::BPCHAR=>Box::new(text.map(str::to_owned)),
            _=>return Err(ApiError::invalid("unsupported SQL parameter type")),
        };Ok(boxed)
    }).collect()
}
fn timestamp(value:&str)->Result<chrono::DateTime<chrono::Utc>> {chrono::DateTime::parse_from_rfc3339(value).map(|v|v.with_timezone(&chrono::Utc)).or_else(|_|chrono::NaiveDateTime::parse_from_str(value,"%Y-%m-%d %H:%M:%S%.f").map(|v|v.and_utc())).map_err(|_|ApiError::invalid("invalid SQL timestamp"))}
fn mysql_params(params:&[SqlValue])->mysql::Params {mysql::Params::Positional(params.iter().map(|value|match value {SqlValue::Null=>mysql::Value::NULL,SqlValue::Integer(value)=>mysql::Value::Int(*value),SqlValue::Real(value)=>mysql::Value::Double(*value),SqlValue::Text(value)=>mysql::Value::Bytes(value.as_bytes().to_vec()),SqlValue::Blob(value)=>mysql::Value::Bytes(value.clone())}).collect())}
fn mysql_query(client:&mut impl Queryable,sql:&str,params:&[SqlValue])->Result<Vec<Value>> {
    let rows:Vec<mysql::Row>=client.exec(mysql_sql(sql),mysql_params(params)).map_err(|_|failure())?;
    rows.into_iter().map(|row|{
        let mut object=wavequant_quant_rs::indexed_json::Map::new();
        for (index,column) in row.columns_ref().iter().enumerate(){let name=column.name_str();let value=match row.as_ref(index).ok_or_else(failure)? {
            mysql::Value::NULL=>Value::Null,mysql::Value::Int(v)=>json!(v),mysql::Value::UInt(v)=>json!(v),mysql::Value::Float(v)=>json!(v),mysql::Value::Double(v)=>json!(v),
            mysql::Value::Bytes(bytes)=>{let text=std::str::from_utf8(bytes).map_err(|_|failure())?;if name=="payload"{wavequant_quant_rs::indexed_json::from_str(text)?}else if matches!(name.as_ref(),"market_total"|"stale_stocks"|"published_stocks"){json!(text.parse::<i64>().map_err(|_|failure())?)}else{json!(text)}},
            mysql::Value::Date(year,month,day,hour,minute,second,micro)=>json!(format!("{year:04}-{month:02}-{day:02} {hour:02}:{minute:02}:{second:02}.{micro:06}")),
            mysql::Value::Time(..)=>return Err(failure()),
        };object.insert(name.into_owned(),value);}Ok(Value::Object(object))
    }).collect()
}
fn schema(mysql:bool)->Vec<String> {
    let timestamp=if mysql{"DATETIME(6)"}else{"TIMESTAMP WITH TIME ZONE"};let payload=if mysql{"JSON"}else{"JSONB"};
    let common=format!("payload {payload} NOT NULL,created_at {timestamp} NOT NULL,updated_at {timestamp} NOT NULL");
    let signal=format!("snapshot_id VARCHAR(64) PRIMARY KEY,run_id VARCHAR(128) NOT NULL,variant VARCHAR(128) NOT NULL,source VARCHAR(32) NOT NULL,scope_symbol VARCHAR(32),asof VARCHAR(10) NOT NULL,algorithm_version VARCHAR(64) NOT NULL,data_version VARCHAR(64) NOT NULL,total INTEGER NOT NULL,skipped INTEGER NOT NULL,failed INTEGER NOT NULL,{common}");
    let tables=[
        ("wavequant_research_runs",format!("run_id VARCHAR(128) PRIMARY KEY,status VARCHAR(32) NOT NULL,strategy VARCHAR(128),{common}"),vec![("ix_wavequant_research_runs_status_updated","status,updated_at")]),
        ("wavequant_structure_signal_snapshots",signal.clone(),vec![("ix_wavequant_structure_signal_lookup","run_id,variant,source,scope_symbol,asof,algorithm_version,updated_at"),("ix_wavequant_structure_generation_lookup","run_id,source,asof,algorithm_version,scope_symbol,updated_at")]),
        ("wavequant_buy_signal_snapshots",format!("{signal},scenario VARCHAR(128) NOT NULL,start VARCHAR(10) NOT NULL"),vec![("ix_wavequant_buy_signal_lookup","run_id,variant,scenario,source,scope_symbol,asof,start,algorithm_version,updated_at")]),
        ("wavequant_market_timeframe_snapshots",format!("snapshot_id VARCHAR(64) PRIMARY KEY,source VARCHAR(32) NOT NULL,symbol VARCHAR(32) NOT NULL,timeframe VARCHAR(16) NOT NULL,requested_asof VARCHAR(10) NOT NULL,resolved_asof VARCHAR(10) NOT NULL,algorithm_version VARCHAR(64) NOT NULL,data_version VARCHAR(64) NOT NULL,{common}"),vec![("ix_wavequant_market_timeframe_lookup","source,symbol,timeframe,requested_asof,algorithm_version,updated_at")]),
    ];
    let mut statements=Vec::new();for (table,columns,indexes) in tables {if mysql {let indexes=indexes.iter().map(|(name,columns)|format!(",INDEX {name} ({columns})")).collect::<String>();statements.push(format!("CREATE TABLE IF NOT EXISTS {table} ({columns}{indexes}) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin"));}else{statements.push(format!("CREATE TABLE IF NOT EXISTS {table} ({columns})"));for (name,columns) in indexes{statements.push(format!("CREATE INDEX IF NOT EXISTS {name} ON {table} ({columns})"));}}}statements
}
