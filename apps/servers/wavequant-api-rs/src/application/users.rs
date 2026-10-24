use std::{sync::Arc,time::Duration};
use chrono::{NaiveDateTime,Utc};
use serde::Deserialize;
use wavequant_quant_rs::indexed_json::{Value,json};
use tokio_postgres::{Client,config::SslMode,error::SqlState};
use postgres_native_tls::MakeTlsConnector;
use crate::presentation::error::ApiError;

#[derive(Deserialize)]
pub struct CreateUser {pub name:String,pub email:String}

pub async fn connect()->Result<Option<Arc<Client>>,ApiError> {
    let Some(host)=std::env::var("DB_HOST").ok() else {return Ok(None);};
    let variable=|name|std::env::var(name).map_err(|_|ApiError::invalid("DB_HOST、DB_PORT、DB_DATABASE、DB_USERNAME、DB_PASSWORD 必须完整配置"));
    let mut config=tokio_postgres::Config::new();
    config.host(&host).port(variable("DB_PORT")?.parse().map_err(|_|ApiError::invalid("DB_PORT 无效"))?).dbname(&variable("DB_DATABASE")?).user(&variable("DB_USERNAME")?).password(variable("DB_PASSWORD")?).connect_timeout(Duration::from_secs(3)).ssl_mode(SslMode::Require);
    let tls=native_tls::TlsConnector::builder().build().map_err(|_|ApiError::unavailable("TLS 初始化失败"))?;
    // 使用系统信任根验证证书与主机名；失败时不回退明文连接。
    let (client,connection)=config.connect(MakeTlsConnector::new(tls)).await.map_err(|_|ApiError::unavailable("用户数据库 TLS 连接失败，请检查服务器证书与配置"))?;
    tokio::spawn(async move {if connection.await.is_err(){tracing::error!("user database connection stopped");}});
    Ok(Some(Arc::new(client)))
}

pub async fn create(client:&Client,input:CreateUser)->Result<Value,ApiError> {
    // 对齐 Zod 默认 email 模式，前瞻条件拆成前置判断。
    let expression=regex::Regex::new(r"^[A-Za-z0-9_'+.\-]*[A-Za-z0-9_+\-]@([A-Za-z0-9][A-Za-z0-9\-]*\.)+[A-Za-z]{2,}$").map_err(|_|ApiError::internal())?;
    if input.name.is_empty()||input.email.starts_with('.')||input.email.contains("..")||!expression.is_match(&input.email) {return Err(ApiError::invalid("Name is required and email must be valid"));}
    let parameters: [&(dyn tokio_postgres::types::ToSql + Sync); 2]=[&input.name,&input.email];
    let query=client.query_one("INSERT INTO users (name,email) VALUES ($1,$2) RETURNING id::text,name,email,\"createdAt\",\"updatedAt\"",&parameters);
    let row=tokio::time::timeout(Duration::from_secs(5),query).await.map_err(|_|ApiError::unavailable("用户数据库请求超时"))?.map_err(|error|{if error.code()==Some(&SqlState::UNIQUE_VIOLATION){ApiError(axum::http::StatusCode::CONFLICT,"EMAIL_ALREADY_REGISTERED","Email already registered".into())}else{ApiError::unavailable("用户数据库暂不可用")}})?;
    let created:NaiveDateTime=row.try_get("createdAt").map_err(|_|ApiError::internal())?;
    let updated:NaiveDateTime=row.try_get("updatedAt").map_err(|_|ApiError::internal())?;
    Ok(json!({"id":row.try_get::<_,String>("id").map_err(|_|ApiError::internal())?,"name":row.try_get::<_,String>("name").map_err(|_|ApiError::internal())?,"email":row.try_get::<_,String>("email").map_err(|_|ApiError::internal())?,"createdAt":created.and_local_timezone(Utc).single().ok_or_else(ApiError::internal)?.to_rfc3339(),"updatedAt":updated.and_local_timezone(Utc).single().ok_or_else(ApiError::internal)?.to_rfc3339()}))
}
